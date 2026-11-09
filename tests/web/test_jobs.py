"""The job queue: limits, order, cancelling - no bridge, no games."""

from __future__ import annotations

import json
import threading
import time

import pytest

from mtgfish.web.jobs import JobStatus, LocalJobQueue, QueueFull


class Fake:
    """A job body: starts at once, finishes when told."""

    def __init__(self, started=True, error=None):
        self.started = started
        self.error = error
        self.calls = 0
        self.stopped = 0
        self.notes: list[tuple[str, str]] = []
        self.went = threading.Event()

    def start(self):
        self.calls += 1
        self.went.set()
        reply = {"started": self.started}
        if self.error:
            reply["error"] = self.error
        return self.started, json.dumps(reply)

    def cancel(self):
        self.stopped += 1

    def notify(self, signal, payload):
        self.notes.append((signal, payload))


def submit(queue, owner, fake, kind="start_run"):
    return queue.submit(owner, kind, fake.start, fake.cancel, fake.notify)


def test_starts_at_once_when_there_is_room():
    queue = LocalJobQueue(max_running=1)
    fake = Fake()
    job = submit(queue, "a", fake)
    assert job.status is JobStatus.RUNNING and fake.calls == 1
    assert json.loads(job.reply)["started"]


def test_waits_its_turn_and_starts_when_the_first_finishes():
    queue = LocalJobQueue(max_running=1, max_running_per_owner=1, max_queued_per_owner=2)
    first, second = Fake(), Fake()
    submit(queue, "a", first)
    waiting = submit(queue, "b", second)
    assert waiting.status is JobStatus.QUEUED and queue.position(waiting) == 1
    assert second.calls == 0

    queue.complete("a", "run_finished", JobStatus.DONE)
    assert second.went.wait(5)
    assert waiting.status is JobStatus.RUNNING


def test_one_person_cannot_fill_the_queue():
    queue = LocalJobQueue(max_running=1, max_running_per_owner=1, max_queued_per_owner=1)
    submit(queue, "a", Fake())
    submit(queue, "a", Fake())  # waits
    with pytest.raises(QueueFull):
        submit(queue, "a", Fake())
    # Someone else still gets a place.
    assert submit(queue, "b", Fake()).status is JobStatus.QUEUED


def test_per_person_limit_lets_others_overtake():
    queue = LocalJobQueue(max_running=2, max_running_per_owner=1, max_queued_per_owner=2)
    submit(queue, "a", Fake())
    mine = submit(queue, "a", Fake())
    theirs = submit(queue, "b", Fake())
    assert mine.status is JobStatus.QUEUED
    assert theirs.status is JobStatus.RUNNING


def test_cancelling_a_waiting_job_tells_its_page():
    queue = LocalJobQueue(max_running=1)
    submit(queue, "a", Fake())
    fake = Fake()
    job = submit(queue, "b", fake, kind="start_lab")
    assert queue.cancel(job.id)
    assert job.status is JobStatus.CANCELLED and fake.calls == 0
    signal, payload = fake.notes[0]
    assert signal == "lab_finished" and json.loads(payload)["cancelled"]


def test_cancelling_a_running_job_asks_it_to_stop():
    queue = LocalJobQueue()
    fake = Fake()
    job = submit(queue, "a", fake)
    queue.cancel(job.id)
    assert fake.stopped == 1
    # Still running until its finish event says otherwise.
    assert job.status is JobStatus.RUNNING
    queue.complete("a", "run_finished", JobStatus.CANCELLED)
    assert job.status is JobStatus.CANCELLED


def test_a_start_that_fails_frees_the_slot():
    queue = LocalJobQueue(max_running=1)
    bad = Fake(started=False, error="paste a decklist first")
    job = submit(queue, "a", bad)
    assert job.status is JobStatus.FAILED
    assert submit(queue, "b", Fake()).status is JobStatus.RUNNING


def test_a_queued_start_that_fails_is_reported_as_a_finish_event():
    queue = LocalJobQueue(max_running=1)
    submit(queue, "a", Fake())
    bad = Fake(started=False, error="no deck")
    job = submit(queue, "b", bad)
    queue.complete("a", "run_finished", JobStatus.DONE)
    deadline = time.time() + 5
    while job.status is not JobStatus.FAILED and time.time() < deadline:
        time.sleep(0.02)
    assert job.status is JobStatus.FAILED
    while not bad.notes and time.time() < deadline:
        time.sleep(0.02)
    assert json.loads(bad.notes[0][1])["error"] == "no deck"


def test_finish_events_match_their_job_kind():
    queue = LocalJobQueue(max_running=2, max_running_per_owner=2)
    run = submit(queue, "a", Fake(), kind="start_run")
    lab = submit(queue, "a", Fake(), kind="start_cutlab")
    queue.complete("a", "cutlab_finished", JobStatus.DONE)
    assert lab.status is JobStatus.DONE and run.status is JobStatus.RUNNING
