"""Turn the cost benchmark's raw per-game lines into distributions and a bill.

    python bench/analyze_cost.py [bench/results]

Reads ``<pod>.timing.*.jsonl``, ``<pod>.memory.*.jsonl`` and ``<pod>.pool.json``
as written by ``bench/run_cost_bench.sh``, prints markdown tables, and writes
``summary.json`` beside them.

Two different "p95"s appear, and they answer different questions:

* the p95 *game* - how slow the slow games are. It sets tail latency, how long
  a worker can be pinned by one game, and per-worker memory headroom;
* the p95 *1000-game run* - what a whole run costs when its games happen to
  be unlucky. Estimated by resampling the measured games 1000 at a time. A
  sum of a thousand games is far steadier than one game, so this sits close to
  the mean; what moves a run's bill is which decks are in the pod.
"""

from __future__ import annotations

import glob
import json
import math
import os
import random
import sys

RESULTS = sys.argv[1] if len(sys.argv) > 1 else "bench/results"
PODS = ["aggro", "combo", "control", "stax", "mixed"]
QS = [("p50", 0.5), ("p90", 0.9), ("p95", 0.95), ("p99", 0.99), ("max", 1.0)]
RUN_GAMES = 1000

#: Prices as found in September 2026 - see the report for sources. Instances
#: are billed per hour whether busy or not, so their per-vCPU-hour figure is a
#: floor that assumes the box is kept busy.
#: ``slowdown`` brackets how many more CPU-seconds the same games take on that
#: provider's vCPU than on this machine's Ryzen 9 7900X3D. Not measured on the
#: providers; derived from Geekbench 6 single-core figures (Zen 4 desktop
#: ~2250-2800, EPYC 9R14 ~1950-2000, EPYC Milan ~1650-2150) and from whether a
#: vCPU is a whole core (c7a) or a hardware thread that shares one when every
#: thread is busy (Fargate, Hetzner CCX).
PRICES = [
    {
        "name": "AWS Fargate, on-demand x86 (us-east-1)",
        "currency": "$",
        "vcpu_h": 0.04048,
        "gb_h": 0.004445,
        "slowdown": (1.35, 2.0),
    },
    {
        "name": "AWS EC2 c7a.2xlarge, on-demand (8 vCPU / 16 GB)",
        "currency": "$",
        "vcpu_h": 0.41056 / 8,
        "gb_h": 0.0,
        "slowdown": (1.3, 1.4),
    },
    {
        "name": "Hetzner CCX33 (8 dedicated vCPU / 32 GB)",
        "currency": "EUR ",
        "vcpu_h": 0.2219 / 8,
        "gb_h": 0.0,
        "slowdown": (1.5, 2.0),
    },
]


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def dist(values: list[float]) -> dict:
    out = {name: pct(values, q) for name, q in QS}
    out["mean"] = sum(values) / len(values) if values else float("nan")
    out["n"] = len(values)
    return out


def load(pod: str, mode: str) -> tuple[list[dict], list[dict]]:
    rows: dict[int, dict] = {}
    startups: list[dict] = []
    for path in sorted(glob.glob(os.path.join(RESULTS, f"{pod}.{mode}.*.jsonl"))):
        with open(path, encoding="utf8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue  # A shard still writing its last line.
                if "index" in obj:
                    rows[obj["index"]] = obj
                elif "startup" in obj:
                    startups.append(obj["startup"])
    return [rows[key] for key in sorted(rows)], startups


def load_pool(pod: str) -> dict | None:
    path = os.path.join(RESULTS, f"{pod}.pool.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf8") as handle:
        return json.load(handle).get("pool")


def bootstrap_total(values: list[float], *, n: int = RUN_GAMES, reps: int = 4000) -> dict:
    rng = random.Random(20260915)
    totals = [sum(rng.choices(values, k=n)) for _ in range(reps)]
    return dist(totals)


def fmt(value: float, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{value:,.{digits}f}"


def table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def main() -> None:
    summary: dict = {"pods": {}}
    per_game_metrics = [
        ("wall_s", "wall s", 3, 1.0),
        ("cpu_s", "CPU s", 3, 1.0),
        ("engine_wall_s", "engine s", 3, 1.0),
        ("legal_wall_s", "legal-move gen s", 3, 1.0),
        ("bot_wall_s", "bot decisions ms", 1, 1000.0),
        ("turns", "player-turns", 0, 1.0),
        ("actions", "actions", 0, 1.0),
        ("rss_peak_mb", "worker RSS peak MB", 1, 1.0),
    ]
    memory_metrics = [
        ("heap_peak_mb", "game heap peak MB", 2, 1.0),
        ("bot_heap_peak_mb", "bot decision heap peak KB", 1, 1024.0),
        ("heap_retained_mb", "heap retained after game MB", 2, 1.0),
    ]

    for pod in PODS:
        timing, startups = load(pod, "timing")
        memory, _ = load(pod, "memory")
        pool = load_pool(pod)
        if not timing:
            continue
        info: dict = {"games_timed": len(timing), "games_memory": len(memory)}

        print(f"\n## {pod} ({len(timing)} games timed, {len(memory)} under tracemalloc)\n")
        rows = []
        for key, label, digits, scale in per_game_metrics:
            values = [row[key] * scale for row in timing if row.get(key) is not None]
            d = dist(values)
            info[key] = d
            rows.append([label] + [fmt(d[name], digits) for name, _ in QS] + [fmt(d["mean"], digits)])
        for key, label, digits, scale in memory_metrics:
            values = [row[key] * scale for row in memory if row.get(key) is not None]
            if not values:
                continue
            d = dist(values)
            info[key] = d
            rows.append([label] + [fmt(d[name], digits) for name, _ in QS] + [fmt(d["mean"], digits)])
        print(table(["per game"] + [name for name, _ in QS] + ["mean"], rows))

        wall = sum(row["wall_s"] for row in timing)
        engine = sum(row["engine_wall_s"] for row in timing)
        legal = sum(row["legal_wall_s"] for row in timing)
        bot = sum(row["bot_wall_s"] for row in timing)
        shares = {"engine": engine / wall, "legal_moves": legal / wall, "bot": bot / wall}
        bot_share = [row["bot_wall_s"] / row["wall_s"] for row in timing if row["wall_s"]]
        info["shares"] = shares
        info["bot_share_per_game"] = dist(bot_share)
        stalls = sum(1 for row in timing if row.get("stalled"))
        runaways = sum(1 for row in timing if row.get("runaway"))
        info["stall_rate"] = stalls / len(timing)
        print(
            f"\nWhere the time goes: engine {shares['engine']:.1%}, legal-move "
            f"generation {shares['legal_moves']:.1%}, bot decisions {shares['bot']:.2%}"
            f"  (bot share per game p95 {pct(bot_share, 0.95):.2%}). "
            f"Stall-outs {stalls}/{len(timing)}, runaways {runaways}."
        )

        cpu_values = [row["cpu_s"] for row in timing]
        boot = bootstrap_total(cpu_values)
        info["run_cpu_bootstrap_s"] = boot
        startup_cpu = sum(s["cpu_s"] for s in startups) / len(startups) if startups else 0.0
        info["startup_cpu_s"] = startup_cpu
        print(
            f"\nCPU for the games of a {RUN_GAMES}-game run, by resampling: "
            f"p50 {fmt(boot['p50'], 0)} s, p95 {fmt(boot['p95'], 0)} s, "
            f"p99 {fmt(boot['p99'], 0)} s (p95/p50 = {boot['p95'] / boot['p50']:.3f}). "
            f"Worker startup {startup_cpu:.2f} CPU-s each."
        )

        if pool:
            info["pool"] = pool
            per_game = pool["cpu_s"] / pool["games"]
            overhead = per_game / (sum(cpu_values) / len(cpu_values)) - 1
            info["pool_cpu_per_game_s"] = per_game
            print(
                f"\nMeasured production run: {pool['games']} games on {pool['workers']} "
                f"workers in {fmt(pool['wall_s'], 1)} s wall, {fmt(pool['cpu_s'], 0)} CPU-s "
                f"({fmt(pool['cpu_s'] / 3600, 3)} vCPU-h), peak process-tree RSS "
                f"{fmt(pool['rss_peak_total_mb'], 0)} MB. {per_game:.3f} CPU-s/game, "
                f"{overhead:+.1%} versus the instrumented serial mean "
                "(pool scheduling, startup, contention and SMT)."
            )
        summary["pods"][pod] = info

    # -- the bill ----------------------------------------------------------
    print("\n## Cost of one 1000-game run\n")
    headers = ["pod", "vCPU-h (measured)"] + [p["name"] for p in PRICES]
    rows = []
    bill: dict = {}
    for pod, info in summary["pods"].items():
        pool = info.get("pool")
        if not pool:
            continue
        vcpu_h = pool["cpu_s"] / 3600
        gb_h = (pool["rss_peak_total_mb"] / 1024) * (pool["wall_s"] / 3600)
        row = [pod, fmt(vcpu_h, 3)]
        bill[pod] = {"vcpu_h": vcpu_h, "gb_h": gb_h, "prices": {}}
        for price in PRICES:
            # A slower vCPU takes longer, so the memory is held longer too.
            low_factor, high_factor = price["slowdown"]
            low = (vcpu_h * price["vcpu_h"] + gb_h * price["gb_h"]) * low_factor
            high = (vcpu_h * price["vcpu_h"] + gb_h * price["gb_h"]) * high_factor
            bill[pod]["prices"][price["name"]] = [low, high]
            row.append(f"{price['currency']}{low:.4f} - {price['currency']}{high:.4f}")
        rows.append(row)
    print(table(headers, rows))
    print(
        "\nEach range scales the CPU measured on this machine by that provider's "
        "slowdown bracket (see PRICES). Instance prices assume the box is kept "
        "busy; idle hours are paid for too."
    )
    summary["bill"] = bill

    with open(os.path.join(RESULTS, "summary.json"), "w", encoding="utf8") as handle:
        json.dump(summary, handle, indent=2)


if __name__ == "__main__":
    main()
