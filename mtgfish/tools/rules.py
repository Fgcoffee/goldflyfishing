"""Look up Comprehensive Rules by number, and check the engine's citations.

    python -m mtgfish.tools.rules show 601.2b
    python -m mtgfish.tools.rules show 702.179 --subrules --where
    python -m mtgfish.tools.rules check
    python -m mtgfish.tools.rules baseline --write

``show`` prints WotC's exact wording for a rule, optionally with its subrules
and every place the engine cites it. That is the "what does this rule actually
say, and who relies on it" question, answered without leaving the terminal.

``check`` is the one that earns its keep after a rules release. It reports
citations pointing at rule numbers that no longer exist, and cited rules whose
wording has changed since the baseline was taken - old text and new, side by
side, so the diff to re-read is a short list rather than the whole rulebook.
"""

from __future__ import annotations

import argparse
import json
import sys

from ..rules import citations


def _print_rule(number: str, *, indent: str = "") -> bool:
    found = citations.rule(number)
    if found is None:
        return False
    print(f"{indent}CR {found.number}  {found.text}")
    return True


def _subrules(number: str) -> list[str]:
    """Every rule nested under this one, in rule order.

    A three-digit group matches its numbered subrules ("502" -> "502.3"); a
    numbered subrule matches its lettered ones ("502.2" -> "502.2a").
    """
    wanted = f"{number}." if "." not in number else number
    return [
        candidate
        for candidate in sorted(citations._rules(), key=_sort_key)
        if candidate != number and candidate.startswith(wanted)
    ]


def _sort_key(number: str) -> tuple:
    parts = number.split(".")
    major = int(parts[0])
    if len(parts) == 1:
        return (major, 0, "")
    tail = parts[1]
    digits = "".join(c for c in tail if c.isdigit())
    letter = "".join(c for c in tail if c.isalpha())
    return (major, int(digits or 0), letter)


def _cmd_show(args) -> int:
    number = args.number.removeprefix("CR").strip()
    if not _print_rule(number):
        nearest = citations.resolve(number)
        print(f"CR {number} does not exist in {citations.release_line() or 'the rules'}.")
        if nearest is not None:
            print("\nNearest existing rule:")
            _print_rule(nearest.number, indent="  ")
        return 1

    if args.subrules:
        for sub in _subrules(number):
            _print_rule(sub, indent="  ")

    if args.where:
        cited = [c for c in citations.cited_in_source() if c.number == number]
        print(f"\nCited in {len(cited)} place(s):")
        for citation in cited:
            print(f"  {citation.path}:{citation.line}")
    return 0


def _cmd_check(args) -> int:
    data = citations.check()
    release = data["release"] or "unknown release"
    baseline = data["baseline_release"]

    print(f"\nRules text : {release}")
    print(f"Baseline   : {baseline or 'none recorded'}")
    print(f"Citations  : {len(data['citations'])} across {len(data['cited_rules'])} rules\n")

    dangling = data["dangling"]
    if dangling:
        total = sum(len(d.citations) for d in dangling)
        print(f"DANGLING - {total} citation(s) to {len(dangling)} rule(s) that do not exist:")
        for item in dangling:
            nearest = f" (nearest: CR {item.nearest})" if item.nearest else ""
            print(f"\n  CR {item.number}{nearest}")
            for citation in item.citations[: args.limit]:
                print(f"      {citation.path}:{citation.line}")
            if len(item.citations) > args.limit:
                print(f"      ... and {len(item.citations) - args.limit} more")
        print()
    else:
        print("DANGLING - none: every citation names a rule that exists.\n")

    drifted = data["drifted"]
    if drifted:
        print(f"CHANGED - {len(drifted)} cited rule(s) reworded since the baseline:")
        for item in drifted:
            print(f"\n  CR {item.number}")
            print(f"    was: {item.was}")
            print(f"    now: {item.now}")
            for citation in item.citations[: args.limit]:
                print(f"      {citation.path}:{citation.line}")
        print()
    elif baseline:
        print("CHANGED - none: every cited rule still reads as it did.\n")

    return 1 if (dangling or drifted) else 0


def _cmd_baseline(args) -> int:
    document = citations.build_baseline()
    if not args.write:
        print(json.dumps(document, indent=2)[:2000])
        print(f"\n({len(document['rules'])} rules; pass --write to save)")
        return 0
    citations.BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    citations.BASELINE_PATH.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf8"
    )
    print(f"Wrote {len(document['rules'])} rules to {citations.BASELINE_PATH}")
    print(f"Release: {document['release']}")
    return 0



def _cmd_find(args) -> int:
    """Rules whose text contains a phrase - how you re-find a renumbered rule."""
    needle = " ".join(args.phrase).lower()
    rules = citations._rules()
    hits = [
        rules[number]
        for number in sorted(rules, key=_sort_key)
        if needle in rules[number].text.lower()
    ]
    if args.section:
        hits = [r for r in hits if r.number.startswith(args.section)]
    if not hits:
        print(f"No rule contains {needle!r}.")
        return 1
    for found in hits[: args.limit]:
        print(f"CR {found.number}  {found.text}")
        print()
    if len(hits) > args.limit:
        print(f"... and {len(hits) - args.limit} more")
    return 0



def _cmd_audit(args) -> int:
    """Rules the coverage table calls done, that no code anywhere cites.

    A citation is evidence of awareness, not of implementation - but the
    converse is strong. ``COVERAGE`` scores whole rule *groups*, so marking
    "601" implemented silently scores all forty of its subrules as done. A
    subrule in a group claimed implemented, which not one line of the engine
    mentions, is where that claim is most likely to be hollow.

    This is how the coverage report came to contradict reality: token doublers,
    effect durations and "doesn't untap" were all inside groups scored as
    implemented while the behaviour was missing entirely.
    """
    from ..rules.coverage import Status, entry_for

    cited = {c.number for c in citations.cited_in_source()}
    rules = citations._rules()

    suspect = []
    for number in sorted(rules, key=_sort_key):
        if args.section and not number.startswith(args.section):
            continue
        if "." not in number:
            continue  # Group headers carry no requirement of their own.
        entry = entry_for(number)
        if entry.status is not Status.IMPLEMENTED:
            continue
        group = number.split(".")[0]
        if number in cited or group in cited:
            continue
        parent_number = citations.parent(number)
        if parent_number and parent_number in cited:
            continue
        suspect.append(number)

    scope = f"section {args.section}" if args.section else "all sections"
    print(f"\nRules scored IMPLEMENTED with no citation anywhere in the engine ({scope}):")
    print(f"{len(suspect)} of them.\n")
    for number in suspect[: args.limit]:
        print(f"  CR {number}  {rules[number].text[:110]}")
    if len(suspect) > args.limit:
        print(f"\n  ... and {len(suspect) - args.limit} more")
    print(
        "\nNot all of these are gaps - a rule can be honoured by code that "
        "never names it.\nThey are the claims worth checking first."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="print a rule's exact wording")
    show.add_argument("number", help='rule number, e.g. "601.2b"')
    show.add_argument("--subrules", action="store_true", help="include subrules")
    show.add_argument("--where", action="store_true", help="where the engine cites it")
    show.set_defaults(func=_cmd_show)

    check = sub.add_parser("check", help="dangling citations and reworded rules")
    check.add_argument("--limit", type=int, default=6, help="locations shown per rule")
    check.set_defaults(func=_cmd_check)

    find = sub.add_parser("find", help="rules containing a phrase")
    find.add_argument("phrase", nargs="+", help="text to search for")
    find.add_argument("--section", default="", help='limit to a prefix, e.g. "120"')
    find.add_argument("--limit", type=int, default=8)
    find.set_defaults(func=_cmd_find)

    audit = sub.add_parser("audit", help="implemented-but-uncited rules")
    audit.add_argument("--section", default="", help='limit to a prefix, e.g. "6"')
    audit.add_argument("--limit", type=int, default=25)
    audit.set_defaults(func=_cmd_audit)

    base = sub.add_parser("baseline", help="record the wording the engine was written against")
    base.add_argument("--write", action="store_true", help="save it")
    base.set_defaults(func=_cmd_baseline)

    args = parser.parse_args(argv)
    if not citations.available():
        print("No Comprehensive Rules text found. Run mtgfish.tools.rules_coverage --refresh")
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
