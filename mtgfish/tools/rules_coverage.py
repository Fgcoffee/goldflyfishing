"""Report Comprehensive Rules coverage.

    python -m mtgfish.tools.rules_coverage
    python -m mtgfish.tools.rules_coverage --gaps 600

Downloads the current Comprehensive Rules if they are not cached, then reports
what fraction of each section the engine implements. Sections 100-600 are the
load-bearing core; the 700s are mostly keyword actions and are tracked by the
keyword registry instead.
"""

from __future__ import annotations

import argparse
import sys

from ..data.comprehensive_rules import default_path, download, parse
from ..rules.coverage import Status, entry_for, report

BAR_WIDTH = 28


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="re-download the rules")
    parser.add_argument(
        "--gaps", type=int, default=None, metavar="SECTION",
        help="list unimplemented rule groups in a section, e.g. 600",
    )
    args = parser.parse_args(argv)

    path = default_path()
    if args.refresh or not path.exists():
        print("Downloading the Comprehensive Rules...")
        path = download()

    rules = parse(path)
    data = report(rules)

    print(f"\nComprehensive Rules: {len(rules)} numbered rules\n")
    print(f"{'Section':<38}{'done':>6}{'part':>6}{'todo':>6}{'n/a':>5}  coverage")
    print("-" * 92)

    core_done = core_total = 0
    for section, stats in data["sections"].items():
        counts = stats["counts"]
        name = f"{section}00s {data['names'][section]}"
        filled = int(BAR_WIDTH * stats["percent"] / 100)
        bar = "#" * filled + "." * (BAR_WIDTH - filled)
        print(
            f"{name:<38}{counts[Status.IMPLEMENTED]:>6}{counts[Status.PARTIAL]:>6}"
            f"{counts[Status.NOT_IMPLEMENTED]:>6}{counts[Status.NOT_APPLICABLE]:>5}"
            f"  {bar} {stats['percent']:5.1f}%"
        )
        if 1 <= section <= 6:
            core_done += stats["done"]
            core_total += stats["relevant"]

    print("-" * 92)
    core = 100.0 * core_done / core_total if core_total else 0.0
    print(f"{'CORE (100s-600s)':<38}{core_done:>6}{'':>6}{core_total - core_done:>6}{'':>5}"
          f"  {'#' * int(BAR_WIDTH * core / 100):.<{BAR_WIDTH}} {core:5.1f}%")

    if args.gaps is not None:
        # Accept either a section digit ("6") or the block ("600").
        section = int(str(args.gaps)[0])
        print(f"\nGaps in the {section}00s:\n")
        for group, numbers in sorted(data["gaps"].items()):
            if int(group[0]) != section:
                continue
            entry = entry_for(group)
            label = entry.status.name.lower()
            note = f" - {entry.note}" if entry.note else ""
            print(f"  {group:<6} {len(numbers):>4} rules  [{label}]{note}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
