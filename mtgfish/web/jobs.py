"""Simulation jobs: queued, running, done, cancelled or failed.

A game takes seconds and a run of ten thousand takes hours, so no request ever
waits for one. Starting a simulation creates a *job*; the request returns at
once, saying whether the job started or is waiting its turn, and the result
arrives later as an event on the page's stream (``run_finished`` and friends),
exactly as it always has.

The queue enforces two limits: how many runs the machine plays at once
(``MTGFISH_MAX_RUNS``) and how many one person may have running and waiting
(``MTGFISH_MAX_RUNS_PER_USER``, ``MTGFISH_MAX_QUEUED_PER_USER``). Past the
first, runs wait in order; past the second, a start is refused.

**Moving to worker machines.** ``JobQueue`` is the seam. ``LocalJobQueue``
plays the job on this machine, through the browser's own bridge (which runs
games in a process pool). A distributed backend - RQ, Celery or Dramatiq -
implements the same four methods: ``submit`` enqueues a message carrying the
job's ``RunConfig`` (everything a run is; see ``sim.runner``), a worker
process plays it, and ``complete`` is called when its result lands, which
publishes the finish event to the page. Nothing above this module needs to
change, because nothing above it knows where a job runs.
"""

from __future__ import annotations

import enum
import itertools
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

log = logging.getLogger("mtgfish.web.jobs")


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def finished(self) -> bool:
        return self in (JobStatus.DONE, JobStatus.CANCELLED, JobStatus.FAILED)


#: Which event ends which kind of job. A start slot not listed here ends with
#: ``run_finished``, the ordinary run's.
FINISH_SIGNALS = {
    "start_run": "run_finished",
    "start_lab": "lab_finished",
    "start_cutlab": "cutlab_finished",
    "start_queue_test": "cutlab_finished",
}


def finish_signal(kind: str) -> str:
    return FINISH_SIGNALS.get(kind, "run_finished")


class QueueFull(RuntimeError):
    """This owner already has as many runs waiting as they may."""


#: Starts a job's work. Returns whether it started, and the reply to give the
#: page (the bridge slot's own JSON).
Starter = Callable[[], tuple[bool, str]]
#: Asks a running job to stop. Its finish event follows when it has.
Canceller = Callable[[], None]
#: Tells the job's owner something happened to it without a finish event of
#: its own (it was cancelled while waiting, or failed to start). Called with
#: the finish signal's name and its JSON payload.
Notifier = Callable[[str, str], None]


@dataclass
class Job:
    id: str
    owner: str
    kind: str
    #: Which browser session started it: its finish event comes from there,
    #: even if the person signs in (changing the owner) while it runs.
    tag: str = ""
    status: JobStatus = JobStatus.QUEUED
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    error: str = ""
    #: The bridge's reply when the job started, for the page.
    reply: str = ""
    _start: Starter | None = field(default=None, repr=False)
    _cancel: Canceller | None = field(default=None, repr=False)
    _notify: Notifier | None = field(default=None, repr=False)

    def as_dict(self, position: int | None = None) -> dict:
        out = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status.value,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "error": self.error,
        }
        if position is not None:
            out["position"] = position
        return out


class JobQueue(Protocol):
    """What the web layer needs from wherever jobs run."""

    def submit(
        self, owner: str, kind: str, start: Starter, cancel: Canceller, notify: Notifier,
        tag: str = "",
    ) -> Job: ...

    def cancel(self, job_id: str) -> bool: ...

    def complete(self, tag: str, kind_signal: str, status: JobStatus, error: str = "") -> None: ...

    def for_owner(self, owner: str) -> list[Job]: ...

    def for_tag(self, tag: str) -> list[Job]: ...


class LocalJobQueue:
    """Jobs played on this machine, in order, within the limits."""

    def __init__(
        self,
        *,
        max_running: int = 1,
        max_running_per_owner: int = 1,
        max_queued_per_owner: int = 2,
        keep_finished: int = 200,
    ) -> None:
        self.max_running = max(1, max_running)
        self.max_running_per_owner = max(1, max_running_per_owner)
        self.max_queued_per_owner = max(0, max_queued_per_owner)
        self.keep_finished = keep_finished
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []  # every job, oldest first
        self._lock = threading.RLock()
        self._ids = itertools.count(1)

    # -- reading ------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def for_owner(self, owner: str) -> list[Job]:
        with self._lock:
            return [self._jobs[i] for i in self._order if self._jobs[i].owner == owner]

    def for_tag(self, tag: str) -> list[Job]:
        with self._lock:
            return [self._jobs[i] for i in self._order if self._jobs[i].tag == tag]

    def position(self, job: Job) -> int | None:
        """1 for the next job to start; None once it is not waiting."""
        with self._lock:
            if job.status is not JobStatus.QUEUED:
                return None
            waiting = [i for i in self._order if self._jobs[i].status is JobStatus.QUEUED]
            return waiting.index(job.id) + 1

    @property
    def running(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status is JobStatus.RUNNING)

    @property
    def queued(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status is JobStatus.QUEUED)

    def running_for(self, owner: str) -> int:
        with self._lock:
            return sum(
                1 for j in self._jobs.values()
                if j.owner == owner and j.status is JobStatus.RUNNING
            )

    # -- changing -----------------------------------------------------------

    def submit(
        self, owner: str, kind: str, start: Starter, cancel: Canceller, notify: Notifier,
        tag: str = "",
    ) -> Job:
        """Queue a job, and start it now if there is room.

        When there is room the job is started on the caller's thread, so the
        page gets the bridge's own reply (its errors included) in answer to
        the call that asked. Otherwise it waits and is started on a thread of
        its own when its turn comes.
        """
        with self._lock:
            waiting = sum(
                1 for j in self._jobs.values()
                if j.owner == owner and j.status is JobStatus.QUEUED
            )
            if self._has_room(owner):
                job = self._new(owner, kind, start, cancel, notify, tag)
                self._begin(job)
                runnable = False
            else:
                if waiting >= self.max_queued_per_owner:
                    raise QueueFull(
                        f"you already have {waiting} simulation(s) waiting - "
                        "let one finish or stop it first"
                    )
                job = self._new(owner, kind, start, cancel, notify, tag)
                runnable = True
        if not runnable:
            self._run_start(job)
        return job

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status.finished:
                return False
            if job.status is JobStatus.QUEUED:
                self._finish(job, JobStatus.CANCELLED)
                notify = job._notify
            else:
                notify = None
                stop = job._cancel
        if notify is not None:
            self._safe_notify(job, '{"cancelled": true, "message": "removed from the queue"}')
            self._pump()
            return True
        if stop is not None:
            try:
                stop()
            except Exception:  # noqa: BLE001 - stopping is best effort
                log.warning("job %s: could not stop it", job_id, exc_info=True)
        return True

    def cancel_tag(self, tag: str) -> int:
        """Cancel everything a browser session has waiting or running."""
        jobs = [j for j in self.for_tag(tag) if not j.status.finished]
        # Waiting ones first, so stopping the running one cannot start them.
        jobs.sort(key=lambda j: j.status is JobStatus.RUNNING)
        for job in jobs:
            self.cancel(job.id)
        return len(jobs)

    def complete(self, tag: str, kind_signal: str, status: JobStatus, error: str = "") -> None:
        """The running job of session ``tag`` that ends with ``kind_signal`` is over."""
        with self._lock:
            for job_id in self._order:
                job = self._jobs[job_id]
                if (
                    job.tag == tag
                    and job.status is JobStatus.RUNNING
                    and finish_signal(job.kind) == kind_signal
                ):
                    self._finish(job, status, error)
                    break
        self._pump()

    # -- internals ----------------------------------------------------------

    def _new(self, owner, kind, start, cancel, notify, tag="") -> Job:
        job = Job(
            id=f"j{next(self._ids)}-{int(time.time()) % 100000}",
            owner=owner,
            kind=kind,
            tag=tag or owner,
            _start=start,
            _cancel=cancel,
            _notify=notify,
        )
        self._jobs[job.id] = job
        self._order.append(job.id)
        self._trim()
        return job

    def _has_room(self, owner: str) -> bool:
        return self.running < self.max_running and self.running_for(owner) < self.max_running_per_owner

    def _begin(self, job: Job) -> None:
        job.status = JobStatus.RUNNING
        job.started = time.time()

    def _finish(self, job: Job, status: JobStatus, error: str = "") -> None:
        job.status = status
        job.finished = time.time()
        job.error = error
        job._start = job._cancel = None

    def _run_start(self, job: Job) -> None:
        try:
            started, reply = job._start() if job._start else (False, "")
        except Exception as exc:  # noqa: BLE001 - reported to the page
            started, reply = False, ""
            job.error = f"{type(exc).__name__}: {exc}"
        job.reply = reply
        if not started:
            with self._lock:
                self._finish(job, JobStatus.FAILED, job.error or "did not start")
            self._pump()

    def _pump(self) -> None:
        """Start whatever is waiting and now has room, oldest first."""
        while True:
            with self._lock:
                job = next(
                    (
                        self._jobs[i] for i in self._order
                        if self._jobs[i].status is JobStatus.QUEUED
                        and self._has_room(self._jobs[i].owner)
                    ),
                    None,
                )
                if job is None:
                    return
                self._begin(job)
            # Not on this thread: _pump is called from the thread that
            # reported the last job finished - which can be the very session
            # thread the next start has to wait on.
            threading.Thread(
                target=self._start_waiting, args=(job,), daemon=True, name=f"job-{job.id}"
            ).start()

    def _start_waiting(self, job: Job) -> None:
        self._run_start(job)
        if job.status is JobStatus.FAILED:
            # The page was told "queued" long ago; it hears the failure the
            # way it hears every result, through the finish event.
            import json

            try:
                error = json.loads(job.reply).get("error") if job.reply else None
            except ValueError:
                error = None
            self._safe_notify(job, json.dumps({"error": error or job.error or "the run did not start"}))

    def _safe_notify(self, job: Job, payload: str) -> None:
        if job._notify is None:
            return
        try:
            job._notify(finish_signal(job.kind), payload)
        except Exception:  # noqa: BLE001
            log.warning("job %s: could not notify its page", job.id, exc_info=True)

    def _trim(self) -> None:
        finished = [i for i in self._order if self._jobs[i].status.finished]
        for job_id in finished[: max(0, len(finished) - self.keep_finished)]:
            self._order.remove(job_id)
            del self._jobs[job_id]
