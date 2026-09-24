"""Running many games, reproducibly.

Determinism is not a nice property here, it is the feature. Replays are not
stored - a 10,000-game run would be gigabytes of logs - they are *regenerated*
by replaying a seed. That only works if a seed determines a game completely, so
everything that could introduce variation is pinned:

* each game's seed is derived from the run seed and the game index, so game
  4,712 is the same game whether the run was 5,000 games or 50,000, and whether
  it was run on one core or twenty;
* workers share no state, and results are reassembled in index order rather
  than completion order;
* the ability provider is rebuilt per process rather than shared, because a
  cache that fills in a different order across runs is exactly the kind of
  thing that makes two identical seeds diverge.

The digest on every record is the check. ``verify`` replays a sample and
compares; if a digest differs, the run's replays cannot be trusted and the
report says so rather than quietly showing the wrong game.
"""

from __future__ import annotations

import multiprocessing
import os
import threading
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

from ..data.db import CardDatabase
from ..rules.setup import new_game
from ..rules.turn import TurnOptions, run_game
from .observer import Observer, finish, new_record
from .records import GameRecord


@dataclass(slots=True)
class RunConfig:
    """Everything that decides what a run *is*.

    Recorded alongside the results, because a report is only comparable with
    another report if these match - and because a replay needs all of it to
    rebuild the game.
    """

    decklists: tuple[str, ...]
    games: int = 10_000
    #: CR has no turn cap; this one is the simulator's, and 25 rounds of a
    #: four-player table is the point past which a goldfish has stopped
    #: telling you anything about the deck.
    max_rounds: int = 25
    run_seed: int = 0
    #: Which deck the report is about.
    hero: int = 0
    workers: int = 0
    #: The card pool's content hash, so a report cannot be silently compared
    #: against one built from a different Scryfall snapshot.
    card_db_hash: str = ""

    def seed_for(self, index: int) -> int:
        """The seed of one game.

        Derived arithmetically from the run seed and the index rather than
        drawn from a stream, so game N is reproducible without replaying the
        N-1 games before it - which is what makes a single datapoint on a
        chart clickable.
        """
        mixed = (self.run_seed * 0x9E3779B1 + index * 0x85EBCA77) & 0xFFFFFFFF
        # One xorshift round, to decorrelate neighbouring indices. Without it
        # consecutive games get consecutive-looking seeds and the shuffles of
        # game N and N+1 are visibly similar.
        mixed ^= mixed >> 15
        mixed = (mixed * 0x2545F491) & 0xFFFFFFFF
        return mixed ^ (mixed >> 13)


class RunCancelled(Exception):
    """The run was stopped before it finished, on purpose.

    Raised rather than returning a partial result: a report over whichever
    games happened to finish first is biased towards the short ones, and
    presenting it as the deck's numbers would be wrong.
    """


@dataclass(slots=True)
class RunResult:
    config: RunConfig
    records: list[GameRecord] = field(default_factory=list)
    #: Cards in the hero's deck that no ability could be read for, so the
    #: report can say which numbers are standing on inert cards.
    unparsed_cards: list[str] = field(default_factory=list)

    @property
    def completed(self) -> int:
        return len(self.records)


#: Rebuilt once per worker rather than passed in, because a card database and
#: a parse cache are not picklable and should not be.
#:
#: Keyed by thread as well as living per process. A run in the GUI happens on
#: a worker thread, and sqlite connections belong to the thread that opened
#: them - so a second run on a *different* thread reusing the first one's
#: database would fail with a ProgrammingError partway through, which is a
#: much worse way to find out than at the first query.
_WORKER: dict[int, dict] = {}


def _worker_state(decklists: tuple[str, ...]):
    """The per-thread card database, decks and ability provider."""
    import threading

    from ..data.decks import parse_decklist
    from ..parser.compile import OracleAbilities

    state = _WORKER.setdefault(threading.get_ident(), {})
    if state.get("decklists") != decklists:
        db = CardDatabase()
        db.registry()
        state["db"] = db
        state["decks"] = [
            parse_decklist(text, db, name=f"P{index}")
            for index, text in enumerate(decklists)
        ]
        state["provider"] = OracleAbilities()
        state["decklists"] = decklists
    return state["decks"], state["provider"]


def play_one(
    config: RunConfig,
    index: int,
    *,
    log: bool = False,
    cancel: threading.Event | None = None,
    watch=None,
):
    """Play one game and return its record.

    ``log`` turns on the full rules log, which is how a replay is produced -
    the same call, the same seed, one flag different.

    ``cancel`` is checked on every game event, so stopping does not have to
    wait for a game to end. One pathological game took over ten minutes.

    ``watch`` is a second observer, called with every event after the run's
    own. It is how a replay records the board over time (see ``sim.board``)
    without a run paying anything for it. Like the observer it must only read:
    a watched game has to be the same game as an unwatched one, or the film
    shows a board the statistics did not come from.
    """
    decks, provider = _worker_state(config.decklists)
    seed = config.seed_for(index)

    game = new_game(decks, seed=seed, log_enabled=log, ability_provider=provider)
    record = new_record(index, seed, len(game.players))
    observer = Observer(record, config.hero)
    game.observer = observer
    if cancel is not None or watch is not None:
        # The observer sees every event and records the same ones either way,
        # so the game - and its digest - is unchanged by being watched.
        def watched(game_, event, _observe=observer, _also=watch):
            if cancel is not None and cancel.is_set():
                raise RunCancelled(f"stopped during game {index}")
            _observe(game_, event)
            if _also is not None:
                _also(game_, event)

        game.observer = watched

    _seat_agents(game)
    _record_opening_hand(game, record, config.hero)

    finished = run_game(game, TurnOptions(max_rounds=config.max_rounds))
    finish(finished, record, observer)
    return (record, finished) if log else record


def _seat_agents(game) -> None:
    from ..ai.simple import SimpleAgent

    for player in game.players:
        game.agents[player.id] = SimpleAgent()


def _record_opening_hand(game, record: GameRecord, hero: int) -> None:
    player = game.player(hero)
    names = []
    for object_id in player.hand:
        obj = game.objects.get(object_id)
        if obj is None:
            continue
        card = getattr(obj, "card", None)
        names.append(getattr(card, "name", "") or "unknown")
    record.opening_hand = names
    record.drawn.update(names)


class GameCrashed(RuntimeError):
    """One game raised. Carries which game, so it can be replayed and fixed."""


def _play_chunk(args) -> list[GameRecord]:
    config, indices = args
    records = []
    for index in indices:
        try:
            records.append(play_one(config, index))
        except Exception as exc:  # noqa: BLE001 - re-raised with the game named
            import traceback

            # A bare "'NoneType' object has no attribute 'zones'" from somewhere
            # in fifty games says nothing about which game, or where. The seed
            # makes the game replayable; the traceback says where to look. Kept
            # as one string so it survives the trip back from a worker process.
            detail = "".join(traceback.format_exception(exc, limit=-6))
            raise GameCrashed(
                f"game {index} (seed {config.seed_for(index)}) crashed: "
                f"{type(exc).__name__}: {exc}\n{detail}"
            ) from None
    return records


def run(
    config: RunConfig,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> RunResult:
    """Play a whole run, in parallel when it is worth it.

    Setting ``cancel`` stops the run promptly - worker processes are killed,
    not left to finish the game they are on - and raises ``RunCancelled``.
    """
    result = RunResult(config=config)
    workers = config.workers or _default_workers()

    def check() -> None:
        if cancel is not None and cancel.is_set():
            raise RunCancelled(f"stopped after {len(result.records)} of {config.games} games")

    if workers <= 1 or config.games < 32:
        for index in range(config.games):
            check()
            result.records.append(play_one(config, index, cancel=cancel))
            if progress:
                progress(len(result.records), config.games)
        return result

    # Many small chunks rather than one per worker. With one chunk each, a
    # 10,000 game run reports progress twenty times - in twenty jumps of five
    # hundred games - and a progress bar that sits still for a minute is
    # indistinguishable from a hung program, which is exactly what it looked
    # like.
    chunk_size = max(1, min(64, config.games // (workers * 8) or 1))
    chunks = list(_chunk_of_size(range(config.games), chunk_size))

    # Not a ``with`` block. Leaving one waits for every game in flight to
    # finish, so Ctrl+C, a closed window or a restarting server all sat there
    # until the slowest game ended - which, for one pathological game, was
    # more than ten minutes. Only a run that completes is allowed to wait.
    pool = ProcessPoolExecutor(max_workers=workers, initializer=_exit_with_parent)
    _quiet_shutdown_noise(pool)
    completed = False
    try:
        pending = {pool.submit(_play_chunk, (config, chunk)) for chunk in chunks}
        while pending:
            # Collected as they finish so progress is smooth. Order does not
            # matter here because the records carry their own game index and
            # are sorted below - a report whose replays depend on completion
            # order would point at the wrong games.
            done, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for future in done:
                result.records.extend(future.result())
                if progress:
                    progress(len(result.records), config.games)
            check()
        completed = True
    finally:
        if completed:
            pool.shutdown(wait=True)
        else:
            _stop_pool(pool)

    result.records.sort(key=lambda record: record.index)
    return result


def _quiet_shutdown_noise(pool: ProcessPoolExecutor) -> None:
    """Stop a stopped pool printing a traceback per worker.

    Shutting a pool down queues one "exit" sentinel per worker. Once the
    workers have been killed those can no longer be delivered, and the queue's
    feeder thread prints a full traceback for each - two dozen of them on every
    press of Stop, none of which is an error. Real tasks that fail to send are
    still reported: those carry a work id and go to the pool's own handler.

    Installed before the first task is submitted, because the feeder thread
    captures its error handler when it starts.
    """
    queue = getattr(pool, "_call_queue", None)
    original = getattr(queue, "_on_queue_feeder_error", None)
    if original is None:
        return

    def on_error(exc, obj):
        if obj is None or (isinstance(exc, OSError) and not hasattr(obj, "work_id")):
            return
        original(exc, obj)

    queue._on_queue_feeder_error = on_error


def _stop_pool(pool: ProcessPoolExecutor) -> None:
    """Stop a pool now, rather than when its current games end."""
    import time

    processes = list((getattr(pool, "_processes", None) or {}).values())

    # Cancel the queued work *before* killing anything, and give the queue's
    # feeder thread a moment to finish writing what it already holds. Killed
    # first, the feeder keeps writing into dead workers' pipes and prints a
    # traceback per worker - two dozen of them for one press of Stop.
    pool.shutdown(wait=False, cancel_futures=True)
    call_queue = getattr(pool, "_call_queue", None)
    buffer = getattr(call_queue, "_buffer", None)
    deadline = time.monotonic() + 1.0
    while buffer and time.monotonic() < deadline:
        time.sleep(0.02)

    kill = getattr(pool, "kill_workers", None)  # Python 3.14 and later
    if kill is not None:
        try:
            kill()
        except Exception:  # noqa: BLE001 - fall through to killing them directly
            pass
    for process in processes:
        try:
            if process.is_alive():
                process.kill()
        except Exception:  # noqa: BLE001 - already gone
            pass
    for process in processes:
        try:
            process.join(timeout=2)
        except Exception:  # noqa: BLE001
            pass


def _exit_with_parent() -> None:
    """Make a worker die with the process that started it.

    A worker only notices its parent has gone when it next asks for work, and
    it asks between games - so a server that was killed, or a window that
    crashed, left every worker playing out its current game with nobody to
    report to. A thread waiting on the parent closes that gap.
    """
    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def watch() -> None:
        parent.join()
        os._exit(0)

    threading.Thread(target=watch, daemon=True, name="exit-with-parent").start()


def _chunk_of_size(items, size: int):
    """Split into chunks of a fixed size, for steady progress reporting."""
    batch: list = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def replay(config: RunConfig, index: int, *, watch=None):
    """Rebuild one game from its seed, with the full log.

    Not a lookup - the game is played again. That is what makes storage cost
    nothing and what makes every datapoint clickable.

    ``watch`` is passed through to ``play_one``: it is how the board film is
    recorded, for a replay only, without a run paying anything for it.
    """
    return play_one(config, index, log=True, watch=watch)


def verify(config: RunConfig, result: RunResult, *, sample: int = 8) -> list[int]:
    """Replay a sample and return the indices whose digest changed.

    An empty list means the run's replays are trustworthy. A non-empty one
    means something in the engine read unordered state, and every replay in
    the run is suspect - which the report must say rather than showing a game
    that is not the one the numbers came from.
    """
    if not result.records:
        return []

    step = max(1, len(result.records) // max(1, sample))
    mismatched = []
    for record in result.records[::step][:sample]:
        again = play_one(config, record.index)
        if again.digest != record.digest:
            mismatched.append(record.index)
    return mismatched


def _default_workers() -> int:
    # One short of the core count: a run that saturates every core makes the
    # machine unusable, and this is a desktop tool.
    return max(1, (os.cpu_count() or 2) - 1)


def _chunk(indices: Iterable[int], parts: int) -> Iterator[list[int]]:
    """Split work into contiguous blocks, one per worker.

    Contiguous rather than round-robin so each worker's parse cache warms on a
    similar slice of the deck; the games are identical either way.
    """
    items = list(indices)
    if not items:
        return
    size = max(1, (len(items) + parts - 1) // parts)
    for start in range(0, len(items), size):
        yield items[start : start + size]
