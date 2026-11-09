"""Browser sessions: one bridge each, on a thread of its own.

Each browser gets its own ``Bridge`` - its own sandbox, last run and Archidekt
sign-in - keyed by a cookie. Every call to a bridge happens on that session's
one dedicated thread, which is what the desktop app's UI thread was: the
bridge owns a sqlite connection that must stay on the thread that opened it.

Simulations are not run here. A ``start_*`` call becomes a job (``jobs``),
which starts the bridge's run when the queue has room; the bridge plays it on
worker processes and reports through its ``*_finished`` signal, which ends
the job.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import queue
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from .jobs import JobStatus, LocalJobQueue, finish_signal
from .settings import Settings

log = logging.getLogger("mtgfish.web")

PACKAGE = Path(__file__).resolve().parent.parent
WEB = PACKAGE / "ui" / "web"

#: Signals that carry only progress. Frequent, disposable, and never replayed
#: to a reconnecting page - the next one is a second away.
TRANSIENT_SIGNALS = {"progressed", "cutlab_status"}


class ServerFull(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# What the bridge offers
# ---------------------------------------------------------------------------


class Api:
    """The bridge's slots and signals, discovered rather than listed.

    Discovered once per process. Code changes arrive by restarting the process
    (``--reload`` does it), so there is nothing to re-discover while running.
    """

    def __init__(self, settings: Settings) -> None:
        from ..ui.bridge import Bridge
        from ..ui.qtcompat import is_slot, signal_names

        self.bridge_class = Bridge
        self.signals = signal_names(Bridge) + ["job_status", "history_changed"]
        self.methods = sorted(
            name
            for name in dir(Bridge)
            if not name.startswith("_")
            and name not in settings.deny
            and name not in self.signals
            and callable(getattr(Bridge, name, None))
            and is_slot(Bridge, name)
        )


class _LoopSubscriber:
    """An event subscriber that hands events to an asyncio loop.

    Events are published from whatever thread the bridge's worker is on; an
    asyncio queue may only be touched from its own loop, so each event is
    handed across with ``call_soon_threadsafe``.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, maxsize: int = 2000) -> None:
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)

    def put_nowait(self, item) -> None:
        def put() -> None:
            try:
                self.queue.put_nowait(item)
            except asyncio.QueueFull:
                pass  # A stalled page misses progress ticks, not results.

        try:
            self.loop.call_soon_threadsafe(put)
        except RuntimeError:
            pass  # the loop is gone: the page has left


class Session:
    """One browser's bridge, and the thread every call to it runs on."""

    def __init__(self, sid: str, api: Api, manager: SessionManager) -> None:
        self.sid = sid
        self.api = api
        self.manager = manager
        self.created = self.last_seen = time.time()
        self._subscribers: list = []
        self._backlog: deque[tuple[int, str]] = deque(maxlen=50)
        self._event_id = 0
        self._lock = threading.Lock()
        self._thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"bridge-{sid[:6]}")
        self.bridge = self._thread.submit(self._build).result()

    def _build(self):
        from ..ui.qtcompat import HAS_QT

        bridge = self.api.bridge_class()
        for name in self.api.signals:
            signal = getattr(bridge, name, None)
            if signal is None:
                continue  # job_status and history_changed are the server's own
            listener = self._listener(name)
            if HAS_QT:  # pragma: no cover - the server normally runs without Qt
                from PySide6.QtCore import Qt

                signal.connect(listener, Qt.ConnectionType.DirectConnection)
            else:
                signal.connect(listener)
        # Never the desktop app's shared verdict file: on a server that file
        # would be everyone's. Anonymous browsers judge cards privately; a
        # signed-in one is switched to the account's own store (app.py).
        from .auth.verdicts import MemoryVerdictStore, use_verdicts

        use_verdicts(bridge, MemoryVerdictStore())
        return bridge

    def _listener(self, name: str):
        def forward(*args) -> None:
            self.publish(name, list(args))

        return forward

    #: The signed-in account, if any (set by the app on each request).
    user_id: str | None = None

    @property
    def owner(self) -> str:
        """Who the session's jobs belong to, for per-person limits: the
        signed-in account, or this browser when nobody is signed in."""
        return f"user:{self.user_id}" if self.user_id else f"anon:{self.sid}"

    @property
    def running(self) -> bool:
        return any(not job.status.finished for job in self.manager.jobs.for_tag(self.sid))

    # -- calls --------------------------------------------------------------

    def submit(self, method: str, args: list) -> Future:
        """Run one bridge slot on the session's thread."""
        self.last_seen = time.time()
        return self._thread.submit(getattr(self.bridge, method), *args)

    def call(self, method: str, args: list, timeout: float) -> str:
        return self.submit(method, args).result(timeout=timeout)

    def start_job(self, method: str, args: list, timeout: float) -> dict:
        """A ``start_*`` slot, through the queue. Returns the reply for the page."""
        jobs = self.manager.jobs

        def start() -> tuple[bool, str]:
            # The bridge emits its finish signal from inside its worker
            # thread, so a job queued behind another can be told to start
            # while that thread is still on its way out - and the bridge
            # would refuse with "a run is already in progress".
            worker = getattr(self.bridge, "_worker", None)
            if worker is not None and worker.is_alive():
                worker.join(timeout=60)
            reply = self.call(method, args, timeout)
            try:
                started = bool(json.loads(reply).get("started"))
            except (ValueError, AttributeError):
                started = False
            return started, reply

        def cancel() -> None:
            stop = getattr(self.bridge, "cancel_run", None)
            if stop is not None:
                self._thread.submit(stop).result(timeout=10)

        job = jobs.submit(self.owner, method, start, cancel, self.publish_finish, tag=self.sid)
        if job.status is JobStatus.QUEUED:
            position = jobs.position(job)
            self.publish("job_status", [json.dumps(job.as_dict(position))])
            return {"started": True, "queued": True, "position": position, "job": job.id}
        try:
            reply = json.loads(job.reply) if job.reply else {}
        except ValueError:
            reply = {"error": job.reply}
        if not isinstance(reply, dict):
            reply = {"reply": reply}
        if job.status is JobStatus.FAILED and not reply.get("error"):
            reply["error"] = job.error or "the run did not start"
        reply["job"] = job.id
        return reply

    def cancel_jobs(self) -> dict:
        """``cancel_run`` from the page: everything this browser has going."""
        count = self.manager.jobs.cancel_tag(self.sid)
        return {"cancelled": bool(count)} if count else {
            "cancelled": False, "reason": "nothing is running"
        }

    def publish_finish(self, signal: str, payload: str) -> None:
        """A finish event the queue made up (cancelled while waiting, failed to start)."""
        self.publish(signal, [payload], from_queue=True)

    # -- events -------------------------------------------------------------

    def publish(self, signal: str, args: list, *, from_queue: bool = False) -> None:
        if signal.endswith("_finished") and not from_queue:
            self._job_finished(signal, args)
        with self._lock:
            self._event_id += 1
            event_id = self._event_id
            payload = json.dumps({"signal": signal, "args": args}, default=str)
            if signal not in TRANSIENT_SIGNALS:
                self._backlog.append((event_id, payload))
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait((event_id, payload))
            except queue.Full:
                pass  # A stalled page misses progress ticks, not results.

    def _job_finished(self, signal: str, args: list) -> None:
        status, error = JobStatus.DONE, ""
        try:
            payload = json.loads(args[0]) if args else {}
        except (ValueError, TypeError):
            payload = {}
        if isinstance(payload, dict):
            if payload.get("cancelled"):
                status = JobStatus.CANCELLED
            elif payload.get("error"):
                status, error = JobStatus.FAILED, str(payload["error"])
        self.manager.jobs.complete(self.sid, signal, status, error)
        hook = self.manager.on_finished
        if hook is not None and isinstance(payload, dict):
            try:
                hook(self, signal, status, payload)
            except Exception:  # noqa: BLE001 - history is not worth losing a result over
                log.warning("session %s: finish hook failed", self.sid[:6], exc_info=True)

    def subscribe(self, last_event_id: int | None, loop=None):
        """A queue of events, primed with any this page missed while away.

        A run finishing while the page's connection was dropping would
        otherwise be a result nobody ever sees. With ``loop``, the subscriber
        feeds an asyncio queue on that loop (the SSE endpoint); without, a
        plain thread-safe queue.
        """
        subscriber = _LoopSubscriber(loop) if loop is not None else queue.Queue(maxsize=2000)
        with self._lock:
            if last_event_id is not None:
                for event_id, payload in self._backlog:
                    if event_id > last_event_id:
                        subscriber.put_nowait((event_id, payload))
            self._subscribers.append(subscriber)
        self.last_seen = time.time()
        return subscriber

    def unsubscribe(self, subscriber) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)
            abandoned = not self._subscribers and self.running
        self.last_seen = time.time()
        if abandoned:
            self._schedule_abandon()

    @property
    def watched(self) -> bool:
        return bool(self._subscribers)

    # -- stopping -----------------------------------------------------------

    def _schedule_abandon(self) -> None:
        """Stop the run if its page does not come back.

        The event stream is the only sign a page is still open; it drops when
        the tab closes and also, briefly, when the page reloads. So the run
        gets a grace period rather than being stopped the moment it drops.
        """
        grace = self.manager.settings.abandon_grace
        if grace < 0:
            return
        timer = threading.Timer(grace, self._abandon_if_still_gone)
        timer.daemon = True
        timer.start()

    def _abandon_if_still_gone(self) -> None:
        if self.watched or not self.running:
            return
        log.info("session %s: page closed during a run; stopping the run", self.sid[:6])
        self.cancel()

    def cancel(self) -> None:
        """Stop everything this session has waiting or running."""
        try:
            self.manager.jobs.cancel_tag(self.sid)
        except Exception:  # noqa: BLE001 - stopping is best effort
            log.warning("session %s: could not stop its run", self.sid[:6], exc_info=True)

    def close(self) -> None:
        # Stop the run first and wait for its processes: closing the database
        # under a running simulation, or exiting with its workers still
        # playing, is the ungraceful shutdown this is here to avoid.
        for job in self.manager.jobs.for_tag(self.sid):
            if job.status is JobStatus.QUEUED:
                self.manager.jobs.cancel(job.id)
        shutdown = getattr(self.bridge, "shutdown", None)
        if shutdown is not None:
            try:
                shutdown()
            except Exception:  # noqa: BLE001
                pass

        def shut() -> None:
            try:
                self.bridge.db.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass

        try:
            self._thread.submit(shut).result(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        self._thread.shutdown(wait=False)


class SessionManager:
    def __init__(self, api: Api, settings: Settings, jobs: LocalJobQueue | None = None) -> None:
        self.api = api
        self.settings = settings
        self.jobs = jobs or LocalJobQueue(
            max_running=settings.max_runs,
            max_running_per_owner=settings.max_runs_per_user,
            max_queued_per_owner=settings.max_queued_per_user,
        )
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        #: Called as (session, signal, status, payload) when a job finishes.
        self.on_finished = None
        self._reaper = threading.Thread(target=self._reap_forever, daemon=True, name="reaper")
        self._reaper.start()

    def get(self, sid: str) -> Session:
        with self._lock:
            session = self._sessions.get(sid)
            if session is not None:
                session.last_seen = time.time()
                return session
            if len(self._sessions) >= self.settings.max_sessions:
                self._evict_one()
            session = Session(sid, self.api, self)
            self._sessions[sid] = session
            log.info("session %s opened (%d open)", sid[:6], len(self._sessions))
            return session

    def _evict_one(self) -> None:
        idle = [s for s in self._sessions.values() if not s.running and not s.watched]
        if not idle:
            raise ServerFull("every session is in use")
        oldest = min(idle, key=lambda s: s.last_seen)
        del self._sessions[oldest.sid]
        oldest.close()

    def _reap_forever(self) -> None:
        while True:
            time.sleep(60)
            cutoff = time.time() - self.settings.session_ttl
            with self._lock:
                stale = [
                    s for s in self._sessions.values()
                    if s.last_seen < cutoff and not s.running and not s.watched
                ]
                for session in stale:
                    del self._sessions[session.sid]
            for session in stale:
                session.close()

    def close_all(self) -> None:
        """Stop every run and close every session, for a server shutting down."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        # Every run is told to stop before any is waited on, so a server with
        # several running does not stop them one at a time.
        for session in sessions:
            if session.running:
                session.cancel()
        for session in sessions:
            session.close()

    def all(self) -> list[Session]:
        with self._lock:
            return list(self._sessions.values())

    @property
    def count(self) -> int:
        return len(self._sessions)

    @property
    def runs(self) -> int:
        return self.jobs.running


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


def _fingerprint(paths) -> str:
    digest = hashlib.sha1()
    for path in sorted(paths):
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(f"{path.relative_to(PACKAGE)}|{stat.st_mtime_ns}|{stat.st_size}\n".encode())
    return digest.hexdigest()[:12]


def code_files() -> list[Path]:
    return [p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts]


def web_files() -> list[Path]:
    return [p for p in WEB.rglob("*") if p.is_file()]


class Versions:
    """What a page needs to know to tell that it is out of date.

    The code part is read at start-up and never again: Python that changed on
    disk is not running until the process restarts, so reporting it early
    would tell the page to reload into the same old code. The web files are
    served from disk on every request, so their part is live.
    """

    def __init__(self) -> None:
        self.code = _fingerprint(code_files())
        self._web = ""
        self._checked = 0.0
        self._lock = threading.Lock()

    @property
    def current(self) -> str:
        with self._lock:
            if time.time() - self._checked > 2:
                self._web = _fingerprint(web_files())
                self._checked = time.time()
            return f"{self.code}-{self._web}"


__all__ = [
    "Api", "PACKAGE", "ServerFull", "Session", "SessionManager", "TRANSIENT_SIGNALS",
    "Versions", "WEB", "code_files", "finish_signal", "web_files",
]
