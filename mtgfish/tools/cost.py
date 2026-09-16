"""What a simulation costs: time, CPU and memory per game, engine versus bot.

A run's bill is set by three numbers per game - wall-clock, CPU and memory -
and by how they are spread, not their average: provisioning is for the slow
games. This plays real games exactly as ``runner.play_one`` does, with the
bots wrapped so every decision they make is timed, and writes one line per
game so the whole distribution survives.

Three modes, because no single measurement can do all three honestly:

``timing`` (default)
    Wall-clock and CPU per game, split three ways: the bot deciding, the
    engine enumerating the legal choices it decides between, and everything
    else the rules engine does. Resident memory is sampled alongside.

``memory``
    The same games again under ``tracemalloc``: each game's Python heap peak,
    and the largest transient the bot allocated while deciding. Kept out of
    the timing pass because tracing slows a game two- or three-fold.

``pool``
    One production run through ``runner.run`` - worker pool, startup and all -
    measuring total wall, total CPU across every worker process and the peak
    memory of the whole process tree. The per-game figures explain the bill;
    this is the bill.

Examples::

    python -m mtgfish.tools.cost bench/decks/aggro.txt --mirror --games 250 \\
        --label aggro --out bench/results/aggro.timing.jsonl
    python -m mtgfish.tools.cost bench/decks/*.txt --games 250 --memory \\
        --label mixed --shard 0/2 --out bench/results/mixed.memory.0.jsonl
    python -m mtgfish.tools.cost bench/decks/*.txt --pool 1000 \\
        --label mixed --out bench/results/mixed.pool.json
    python -m mtgfish.tools.cost --report bench/results/*.jsonl

Engine time is what remains of a game's wall-clock once the bot and the legal
move enumeration are taken out, so it includes setup, mulligans, state-based
actions, triggers, combat, layers and the observer that builds the record -
everything a production game does that is not a decision.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import sys
import threading
import time
import tracemalloc
from dataclasses import asdict, dataclass

try:
    import psutil
except ImportError:  # pragma: no cover - only the pool mode strictly needs it
    psutil = None

MB = 1024 * 1024


# ---------------------------------------------------------------------------
# Instrumentation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Meter:
    """Accumulated time for one game, by who was running."""

    bot_wall: float = 0.0
    bot_cpu: float = 0.0
    bot_calls: int = 0
    legal_wall: float = 0.0
    legal_cpu: float = 0.0
    legal_calls: int = 0
    #: Largest heap growth during any single bot decision (memory mode only).
    bot_heap_peak: int = 0
    game_heap_peak: int = 0
    tracing: bool = False
    #: Non-zero while inside a timed call. Nested calls are not timed again:
    #: a bot that asks the engine a question is still the bot deciding.
    depth: int = 0

    def enter(self):
        self.depth += 1
        if self.depth > 1:
            return None
        base = None
        if self.tracing:
            current, peak = tracemalloc.get_traced_memory()
            self.game_heap_peak = max(self.game_heap_peak, peak)
            tracemalloc.reset_peak()
            base = current
        return (time.perf_counter(), time.thread_time(), base)

    def leave(self, token, kind: str) -> None:
        self.depth -= 1
        if token is None:
            return
        wall, cpu, base = token
        wall = time.perf_counter() - wall
        cpu = time.thread_time() - cpu
        if kind == "bot":
            self.bot_wall += wall
            self.bot_cpu += cpu
            self.bot_calls += 1
        else:
            self.legal_wall += wall
            self.legal_cpu += cpu
            self.legal_calls += 1
        if base is not None:
            _current, peak = tracemalloc.get_traced_memory()
            self.game_heap_peak = max(self.game_heap_peak, peak)
            if kind == "bot":
                self.bot_heap_peak = max(self.bot_heap_peak, peak - base)
            tracemalloc.reset_peak()


class TimedAgent:
    """A bot, with every method call timed. Behaviour is untouched.

    Wraps whatever the agent has rather than a fixed list, so a hook added to
    the agent protocol later is measured without anyone remembering to add it
    here - and an optional hook the bot lacks is still absent, which is how
    the engine tells it is optional.
    """

    __slots__ = ("_agent", "_meter", "_cache")

    def __init__(self, agent, meter: Meter) -> None:
        self._agent = agent
        self._meter = meter
        self._cache: dict = {}

    def __getattr__(self, name: str):
        attr = getattr(self._agent, name)
        if name.startswith("__") or not callable(attr):
            return attr
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        meter = self._meter

        def timed(*args, **kwargs):
            token = meter.enter()
            try:
                return attr(*args, **kwargs)
            finally:
                meter.leave(token, "bot")

        self._cache[name] = timed
        return timed


_METER: Meter | None = None


def _install_legal_actions_timer() -> None:
    """Time legal-move enumeration where the engine offers choices to a bot.

    ``priority._decide`` imports ``legal_actions`` at call time, so replacing
    the module attribute is enough. Calls the bot makes itself happen inside a
    timed bot call and stay counted as the bot's.
    """
    from ..rules import legality

    original = legality.legal_actions
    if getattr(original, "_timed", False):
        return

    def timed(*args, **kwargs):
        meter = _METER
        if meter is None:
            return original(*args, **kwargs)
        token = meter.enter()
        try:
            return original(*args, **kwargs)
        finally:
            meter.leave(token, "legal")

    timed._timed = True
    legality.legal_actions = timed


class RssSampler:
    """Resident memory, sampled on a thread, reset per game."""

    def __init__(self, interval: float = 0.02) -> None:
        self.interval = interval
        self.peak = 0
        self._stop = threading.Event()
        self._process = psutil.Process() if psutil else None
        self._thread = threading.Thread(target=self._run, daemon=True, name="rss-sampler")

    def rss(self) -> int:
        return self._process.memory_info().rss if self._process else 0

    def start(self) -> None:
        if self._process:
            self._thread.start()

    def reset(self) -> None:
        self.peak = self.rss()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            rss = self.rss()
            if rss > self.peak:
                self.peak = rss

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# Playing
# ---------------------------------------------------------------------------


def _machine() -> dict:
    info = {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "python": sys.version.split()[0],
        "logical_cpus": os.cpu_count(),
    }
    if psutil:
        info["physical_cpus"] = psutil.cpu_count(logical=False)
        info["ram_gb"] = round(psutil.virtual_memory().total / 2**30, 1)
    info["thread_time_resolution_s"] = time.get_clock_info("thread_time").resolution
    return info


def _read_decks(paths: list[str], mirror: bool) -> tuple[tuple[str, ...], list[str]]:
    texts = [open(path, encoding="utf8").read() for path in paths]
    names = [os.path.splitext(os.path.basename(path))[0] for path in paths]
    if mirror:
        texts, names = [texts[0]] * 4, [names[0]] * 4
    if len(texts) != 4:
        raise SystemExit("a pod is four decks: pass four, or one with --mirror")
    return tuple(texts), names


def measure_games(config, indices, *, memory: bool, out) -> dict:
    """Play each game instrumented, writing one JSON line per game."""
    global _METER

    from ..rules.turn import TurnOptions, run_game
    from ..sim.observer import Observer, finish, new_record
    from ..sim.runner import _record_opening_hand, _seat_agents, _worker_state, new_game

    sampler = RssSampler()
    rss_before = sampler.rss()
    wall, cpu = time.perf_counter(), time.thread_time()
    decks, provider = _worker_state(config.decklists)
    startup = {
        "wall_s": time.perf_counter() - wall,
        "cpu_s": time.thread_time() - cpu,
        "rss_before_mb": rss_before / MB,
        "rss_after_mb": sampler.rss() / MB,
    }
    _install_legal_actions_timer()
    sampler.start()
    if memory:
        tracemalloc.start()

    for index in indices:
        meter = Meter(tracing=memory)
        _METER = meter
        seed = config.seed_for(index)
        sampler.reset()
        rss_start = sampler.rss()
        heap_start = tracemalloc.get_traced_memory()[0] if memory else 0
        if memory:
            tracemalloc.reset_peak()

        wall, cpu = time.perf_counter(), time.thread_time()
        game = new_game(decks, seed=seed, ability_provider=provider)
        record = new_record(index, seed, len(game.players))
        game.observer = Observer(record, config.hero)
        _seat_agents(game)
        for player_id, agent in list(game.agents.items()):
            game.agents[player_id] = TimedAgent(agent, meter)
        _record_opening_hand(game, record, config.hero)
        finished = run_game(game, TurnOptions(max_rounds=config.max_rounds))
        finish(finished, record)
        wall = time.perf_counter() - wall
        cpu = time.thread_time() - cpu

        row = {
            "index": index,
            "seed": seed,
            "wall_s": wall,
            "cpu_s": cpu,
            "bot_wall_s": meter.bot_wall,
            "bot_cpu_s": meter.bot_cpu,
            "legal_wall_s": meter.legal_wall,
            "legal_cpu_s": meter.legal_cpu,
            "engine_wall_s": max(0.0, wall - meter.bot_wall - meter.legal_wall),
            "bot_calls": meter.bot_calls,
            "legal_calls": meter.legal_calls,
            "turns": record.turns,
            "actions": finished.actions_taken,
            "objects": len(finished.objects),
            "winner": record.winner,
            "win_reason": record.win_reason.name if record.win_reason is not None else None,
            "stalled": bool(record.stalled),
            "runaway": record.runaway,
            "loops": len(record.loops),
            "rss_start_mb": rss_start / MB,
            "rss_peak_mb": max(sampler.peak, sampler.rss()) / MB,
        }
        if memory:
            current, peak = tracemalloc.get_traced_memory()
            game_peak = max(meter.game_heap_peak, peak)
            row["heap_peak_mb"] = (game_peak - heap_start) / MB
            row["bot_heap_peak_mb"] = meter.bot_heap_peak / MB
            row["heap_retained_mb"] = (current - heap_start) / MB
        out.write(json.dumps(row) + "\n")
        out.flush()
        del game, finished, record

    _METER = None
    sampler.stop()
    if memory:
        tracemalloc.stop()
    return startup


def measure_pool(config) -> dict:
    """A production run, observed from outside: the bill itself."""
    if psutil is None:
        raise SystemExit("pool mode needs psutil")
    from ..sim.runner import _default_workers, run

    me = psutil.Process()
    cpu_by_pid: dict[int, float] = {}
    rss_peak = 0
    stop = threading.Event()

    def sample() -> None:
        nonlocal rss_peak
        while not stop.wait(0.1):
            total = 0
            for process in [me, *me.children(recursive=True)]:
                try:
                    times = process.cpu_times()
                    total += process.memory_info().rss
                    cpu_by_pid[process.pid] = times.user + times.system
                except psutil.Error:
                    continue
            rss_peak = max(rss_peak, total)

    own_before = sum(me.cpu_times()[:2])
    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    wall = time.perf_counter()
    result = run(config)
    wall = time.perf_counter() - wall
    stop.set()
    thread.join()
    cpu_by_pid[me.pid] = sum(me.cpu_times()[:2]) - own_before

    stalls = sum(1 for record in result.records if record.stalled)
    return {
        "games": len(result.records),
        "workers": config.workers or _default_workers(),
        "wall_s": wall,
        "cpu_s": sum(cpu_by_pid.values()),
        "processes_seen": len(cpu_by_pid),
        "rss_peak_total_mb": rss_peak / MB,
        "stalls": stalls,
        "runaways": sum(1 for record in result.records if record.runaway),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def percentile(values: list[float], q: float) -> float:
    """Linear interpolation between closest ranks, like numpy's default."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


QUANTILES = (("min", 0.0), ("p50", 0.5), ("p90", 0.9), ("p95", 0.95), ("p99", 0.99), ("max", 1.0))


def distribution(values: list[float]) -> dict:
    out = {name: percentile(values, q) for name, q in QUANTILES}
    out["mean"] = sum(values) / len(values) if values else float("nan")
    out["n"] = len(values)
    return out


def report(paths: list[str]) -> None:
    groups: dict[str, list[dict]] = {}
    for pattern in paths:
        for path in sorted(glob.glob(pattern)):
            label = os.path.basename(path).split(".")[0]
            # A memory-pass game ran under tracemalloc, so its timings are
            # inflated; only its heap figures are kept.
            heap_only = ".memory." in os.path.basename(path)
            with open(path, encoding="utf8") as handle:
                for line in handle:
                    line = line.strip()
                    if not (line.startswith("{") and '"index"' in line):
                        continue
                    row = json.loads(line)
                    if heap_only:
                        row = {
                            key: value
                            for key, value in row.items()
                            if key == "index" or "heap" in key
                        }
                    groups.setdefault(label, []).append(row)

    metrics = (
        "wall_s", "cpu_s", "engine_wall_s", "bot_wall_s", "legal_wall_s",
        "rss_peak_mb", "heap_peak_mb", "bot_heap_peak_mb", "turns", "actions",
    )
    for label, rows in groups.items():
        # A game measured in both passes appears twice; keep one row per game
        # per field, merging the memory fields into the timing row.
        merged: dict[int, dict] = {}
        for row in rows:
            merged.setdefault(row["index"], {}).update(
                {key: value for key, value in row.items() if value is not None}
            )
        games = list(merged.values())
        print(f"\n== {label}: {len(games)} games")
        print(f"   {'metric':16}" + "".join(f"{name:>10}" for name, _ in QUANTILES) + f"{'mean':>10}")
        for metric in metrics:
            values = [game[metric] for game in games if metric in game]
            if not values:
                continue
            stats = distribution(values)
            print(
                f"   {metric:16}"
                + "".join(f"{stats[name]:10.3f}" for name, _ in QUANTILES)
                + f"{stats['mean']:10.3f}"
            )


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("decks", nargs="*")
    parser.add_argument("--mirror", action="store_true", help="four copies of the first deck")
    parser.add_argument("--games", type=int, default=250)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--label", default="run")
    parser.add_argument("--out", default="")
    parser.add_argument("--memory", action="store_true", help="tracemalloc pass instead of timing")
    parser.add_argument("--pool", type=int, default=0, help="production run of this many games")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--shard", default="0/1", help="k/n: play only games with index %% n == k")
    parser.add_argument("--report", nargs="+", metavar="JSONL")
    args = parser.parse_args(argv)

    if args.report:
        report(args.report)
        return

    from ..data.db import CardDatabase
    from ..sim.runner import RunConfig

    decklists, names = _read_decks(args.decks, args.mirror)
    db = CardDatabase()
    config = RunConfig(
        decklists=decklists,
        games=args.pool or args.games,
        run_seed=args.seed,
        workers=args.workers,
        card_db_hash=db.content_hash,
    )
    header = {
        "label": args.label,
        "decks": names,
        "mode": "pool" if args.pool else ("memory" if args.memory else "timing"),
        "run_seed": args.seed,
        "machine": _machine(),
    }

    if args.pool:
        header["pool"] = measure_pool(config)
        text = json.dumps(header, indent=2)
        if args.out:
            with open(args.out, "w", encoding="utf8") as handle:
                handle.write(text + "\n")
        print(text)
        return

    k, n = (int(part) for part in args.shard.split("/"))
    indices = [index for index in range(args.games) if index % n == k]
    out = open(args.out, "w", encoding="utf8") if args.out else sys.stdout
    try:
        out.write(json.dumps(header) + "\n")
        startup = measure_games(config, indices, memory=args.memory, out=out)
        out.write(json.dumps({"startup": startup, "config": asdict(config) | {"decklists": names}}) + "\n")
    finally:
        if out is not sys.stdout:
            out.close()


if __name__ == "__main__":
    main()
