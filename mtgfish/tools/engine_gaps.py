"""Cards that do not work because the rules engine lacks (or mis-builds) a mechanic.

    python -m mtgfish.tools.census --out /tmp/census.jsonl.gz
    python -m mtgfish.tools.engine_gaps --census /tmp/census.jsonl.gz

writes ``docs/engine-gaps.md`` and ``docs/engine-gaps.csv``: every card in the
format-legal pool that fails for an engine reason, grouped by mechanic, most
played first.

Three kinds of entry, kept apart because they mean different things:

* **wrong** - the card is counted as read, and the engine does something the
  card does not say. The worst outcome there is (docs/parser-working-rules.md
  rule 1). Each was checked on a real board or from the built IR, and the
  evidence is written beside it.
* **missing** - the engine has no implementation, and the card honestly
  reports as not understood: a keyword whose builder is an inert placeholder,
  or a refusal the parser makes after reading every word (no single
  antecedent, a pile with no pile, ...), which the census records with
  ``engine = True``.
* **suspect** - the engine has no visible handling for the mechanic and the
  card counts as read, but nobody has proved it wrong on a board yet.

The findings table is maintained by hand: an entry is a claim about the
engine, and goes in only with its evidence. Which cards carry a keyword is
read from the keyword lines of their oracle text.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
from dataclasses import dataclass
from pathlib import Path

WRONG, MISSING, SUSPECT = "wrong", "missing", "suspect"


@dataclass(frozen=True)
class Finding:
    keyword: str
    verdict: str
    cr: str
    card_says: str
    engine_does: str
    evidence: str


#: Keyword mechanics. Evidence "board" means the card was played on a real
#: board with its parsed oracle text and the result observed; "IR" means the
#: ability the keyword builds was read and is wrong on its face.
FINDINGS: tuple[Finding, ...] = (
    # -- wrong: counted as read, does something else -------------------------
    Finding("Annihilator", WRONG, "702.86a",
            "defending player sacrifices N permanents of their choice",
            "every permanent an opponent controls is sacrificed",
            "board: Kozilek, Butcher of Truth attacking; all 6 opposing lands sacrificed"),
    Finding("Flashback", WRONG, "702.34a",
            "cast from graveyard; exiled instead of going anywhere else",
            "cast from graveyard and returns to the graveyard, castable again forever",
            "board: Think Twice flashed back, drew, and was back in the graveyard"),
    Finding("Exploit", WRONG, "702.110a",
            "you may sacrifice a creature when it enters",
            "you may sacrifice every creature you control, itself included",
            "board: Sidisi, Undead Vizier with 3 Grizzly Bears; all 4 sacrificed"),
    Finding("Champion", WRONG, "702.72a",
            "exile another creature of the type until this leaves; else sacrifice this",
            "exiles every creature you control, itself included",
            "board: Mistbind Clique with 3 Faeries; all 4 exiled"),
    Finding("Living weapon", WRONG, "702.92a",
            "create a 0/0 Germ token and attach this to it",
            "creates no Germ (token count 0) and attaches to nothing",
            "board: Nettlecyst resolved with no Germ"),
    Finding("For Mirrodin!", WRONG, "702.163a",
            "create a 2/2 Rebel token and attach this to it",
            "creates no Rebel (token count 0)",
            "IR: CREATE_TOKEN amount 0, same shape as living weapon"),
    Finding("Saddle", WRONG, "702.171a",
            "tap creatures with total power N: this becomes saddled",
            "pay nothing: put N +1/+1 counters on this",
            "IR: ADD_COUNTERS +1/+1, empty cost"),
    Finding("Craft", WRONG, "702.167a",
            "exile this and the named materials: return it transformed",
            "pay nothing: put a +1/+1 counter on this",
            "IR: ADD_COUNTERS +1/+1, empty cost"),
    Finding("Harmonize", WRONG, "702.180a",
            "cast this from your graveyard for its harmonize cost, tapping a creature to help",
            "pay the cost: put a +1/+1 counter on this",
            "IR: ADD_COUNTERS +1/+1"),
    Finding("Job select", WRONG, "702.182a",
            "create a 1/1 Hero token and attach this Equipment to it",
            "put a +1/+1 counter on this",
            "IR: ADD_COUNTERS +1/+1, empty cost"),
    Finding("Specialize", WRONG, "702.172a",
            "discard a card of a color: perpetually specialize into another face",
            "pay the cost: put a +1/+1 counter on this",
            "IR: ADD_COUNTERS +1/+1"),
    Finding("Boast", WRONG, "702.142a",
            "the printed ability, activatable only if this attacked this turn",
            "pay nothing: put a +1/+1 counter on this",
            "IR: ADD_COUNTERS +1/+1, empty cost"),
    Finding("Amplify", WRONG, "702.38a",
            "reveal creature cards sharing a type; N counters for each revealed",
            "always enters with N +1/+1 counters",
            "IR: flat ADD_COUNTERS N"),
    Finding("Devour", WRONG, "702.82a",
            "sacrifice any number of creatures; N counters for each sacrificed",
            "always enters with N +1/+1 counters, sacrificing nothing",
            "IR: flat ADD_COUNTERS N"),
    Finding("Backup", WRONG, "702.165a",
            "put N counters on target creature; it gains this card's abilities",
            "puts N counters on this creature",
            "IR: ADD_COUNTERS on this permanent"),
    Finding("Fabricate", WRONG, "702.123a",
            "choose: N +1/+1 counters, or N 1/1 Servo tokens",
            "always N +1/+1 counters",
            "IR: flat ADD_COUNTERS N"),
    Finding("Graft", WRONG, "702.58a",
            "enters with N counters, and may move one onto each creature that enters",
            "enters with N counters only",
            "IR: flat ADD_COUNTERS N, no trigger"),
    Finding("Modular", WRONG, "702.43a",
            "enters with N counters; when it dies, move them to target artifact creature",
            "enters with N counters only",
            "IR: flat ADD_COUNTERS N, no dies trigger"),
    Finding("Sunburst", WRONG, "702.44a",
            "a counter for each color of mana spent to cast it",
            "always exactly one +1/+1 counter",
            "IR: flat ADD_COUNTERS 1"),
    Finding("Soulbond", WRONG, "702.95a",
            "pair this with one other unpaired creature",
            "attaches to every other creature you control",
            "IR: ATTACH to 'all another creature you control'"),
    Finding("Fading", WRONG, "702.32a",
            "enters with N fade counters; remove one each upkeep, sacrifice when you can't",
            "enters with N fade counters and nothing else happens",
            "IR: ADD_COUNTERS only, no upkeep trigger"),
    Finding("Vanishing", WRONG, "702.63a",
            "enters with N time counters; remove one each upkeep, sacrifice at the last",
            "enters with N time counters and nothing else happens",
            "IR: ADD_COUNTERS only, no upkeep trigger"),
    Finding("Compleated", WRONG, "702.150a",
            "enters with two fewer loyalty for each Phyrexian mana paid with life",
            "continuously removes X loyalty counters",
            "IR: static REMOVE_COUNTERS X"),
    Finding("Haunt", WRONG, "702.55a",
            "when it dies, exile it haunting target creature; triggers again when that dies",
            "when it dies, exile it; nothing haunted",
            "IR: EXILE self only"),
    Finding("Recover", WRONG, "702.59a",
            "when a creature card is put into your graveyard, pay the cost or exile this",
            "when a creature you control dies, you may return this to hand for free",
            "IR: trigger on your creatures dying, no cost, no exile"),
    Finding("Hideaway", WRONG, "702.75a",
            "look at the top N, exile one face down, the rest on the bottom",
            "reveals the top N, exiles the top card, puts nothing correctly",
            "IR: REVEAL N, EXILE top 1, PUT_ON_LIBRARY of 'you'"),
    Finding("Demonstrate", WRONG, "702.144a",
            "you may copy it; if you do, an opponent also copies it",
            "you may copy it, then you copy it again",
            "IR: two COPY_SPELL for you"),
    Finding("Storm", WRONG, "702.40a",
            "copy it for each spell cast before it this turn",
            "copies it once",
            "IR: one COPY_SPELL"),
    Finding("Gravestorm", WRONG, "702.69a",
            "copy it for each permanent put into a graveyard this turn",
            "copies it once",
            "IR: one COPY_SPELL"),
    Finding("Ripple", WRONG, "702.60a",
            "reveal the top N; cast any with the same name free; rest on the bottom",
            "casts every card you own in your library for free",
            "IR: CAST_WITHOUT_PAYING 'all object you own in library'"),
    Finding("Dethrone", WRONG, "702.105a",
            "+1/+1 counter when it attacks the player with the most life",
            "+1/+1 counter whenever it attacks anyone",
            "IR: no condition on the defending player"),
    Finding("Evolve", WRONG, "702.100a",
            "counter when a creature with greater power or toughness enters",
            "counter whenever any creature you control enters",
            "IR: no power/toughness comparison"),
    Finding("Training", WRONG, "702.149a",
            "counter when it attacks with a creature of greater power",
            "counter whenever it attacks",
            "IR: no condition"),
    Finding("Unleash", WRONG, "702.98a",
            "may enter with a +1/+1 counter; can't block while it has one",
            "may enter with a +1/+1 counter; blocks freely",
            "IR: no blocking restriction"),
    # -- missing: inert placeholder, honestly not understood -----------------
    Finding("Plot", MISSING, "702.170a", "exile from hand, cast later for free",
            "nothing (inert)", "IR: UNPARSED placeholder"),
    Finding("Myriad", MISSING, "702.116a", "token copies attacking each other opponent",
            "nothing (inert)", "IR: UNPARSED placeholder"),
    Finding("Double team", MISSING, "digital", "conjure a copy into hand when it attacks",
            "nothing (inert)", "IR: UNPARSED placeholder"),
    Finding("Teamwork", MISSING, "702.194a", "tap creatures as an additional cost for a bonus",
            "nothing (inert)", "IR: UNPARSED placeholder"),
    Finding("Intensity", MISSING, "digital", "starting intensity, raised by effects",
            "nothing (inert)", "IR: UNPARSED placeholder"),
    # -- suspect: no visible engine handling, not yet proved -----------------
    *(
        Finding(name, SUSPECT, "", "an alternative or additional cost with its own consequences",
                "the cost can be paid; nothing in the engine reads the keyword afterwards",
                "code: no use of the keyword outside the keyword table")
        for name in (
            "Aftermath", "Assist", "Bargain", "Bestow", "Blitz", "Buyback", "Cleave",
            "Conspire", "Embalm", "Emerge", "Encore", "Entwine", "Escalate",
            "Eternalize", "Freerunning", "Gift", "Impending", "Jump-start", "Miracle",
            "More Than Meets the Eye", "Multikicker", "Offspring", "Prowl",
            "Replicate", "Retrace", "Spectacle", "Splice", "Spree", "Squad", "Warp",
        )
    ),
)


def _keyword_line(keyword: str):
    """Whether an ability is a keyword line carrying this keyword: the keyword
    first, or an item of a keyword list ("Flying, annihilator 2")."""
    key = keyword.lower()

    def matches(text: str) -> bool:
        first = text.split(".", 1)[0].lower()
        return any(
            item.strip() == key or item.strip().startswith(key + " ")
            for item in first.split(",")
        )

    return matches


def _card_texts(record: dict) -> list[str]:
    return [a["text"] for a in record["abilities"]]


def build(records: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """One row per (card, mechanic): the cards each finding applies to."""
    rows: list[dict] = []
    cards = [r for r in records if r["status"] in ("ok", "failed")]
    for finding in FINDINGS:
        pattern = _keyword_line(finding.keyword)
        for record in cards:
            if any(pattern(text) for text in _card_texts(record)):
                rows.append(_row(record, finding.keyword, finding.verdict, finding.engine_does))
    # Refusals made after every word was read: the census marks them.
    refusals: dict[str, str] = {}
    for record in cards:
        for ability in record["abilities"]:
            if not ability["ok"] and ability.get("engine") and ability.get("rule") != "inert":
                reason = ability.get("reason", "")
                refusals.setdefault(reason, reason)
                rows.append(_row(record, f"refused: {reason}", MISSING, ability["text"][:120]))
    return rows, refusals


def _row(record: dict, mechanic: str, verdict: str, note: str) -> dict:
    return {
        "card": record["name"],
        "rank": record.get("rank") or "",
        "counted_as_read": record["status"] == "ok",
        "mechanic": mechanic,
        "verdict": verdict,
        "note": note,
    }


def render(rows: list[dict]) -> str:
    by_mechanic: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_mechanic[row["mechanic"]].append(row)
    findings = {f.keyword: f for f in FINDINGS}

    def rank(row):
        return row["rank"] if row["rank"] != "" else 10**9

    def section(verdict: str) -> list[str]:
        names = [m for m in by_mechanic if by_mechanic[m][0]["verdict"] == verdict]
        names.sort(key=lambda m: -len(by_mechanic[m]))
        return names

    wrong_cards = {r["card"] for r in rows if r["verdict"] == WRONG and r["counted_as_read"]}
    missing_cards = {r["card"] for r in rows if r["verdict"] == MISSING}
    suspect_cards = {r["card"] for r in rows if r["verdict"] == SUSPECT and r["counted_as_read"]}
    out = [
        "# Cards that don't work because of the rules engine",
        "",
        "Generated by `python -m mtgfish.tools.engine_gaps` from a census of every card",
        "legal in at least one format. Regenerated as the work changes it; do not edit by hand.",
        "",
        "| | Cards |",
        "|---|---|",
        f"| **Engine does it wrong**, card currently counted as read | **{len(wrong_cards)}** |",
        f"| **Engine lacks it**, card honestly reported as not understood | **{len(missing_cards)}** |",
        f"| **Suspected, not yet proved**, card counted as read | {len(suspect_cards)} |",
        "",
        "A card can appear under more than one mechanic. Within each mechanic, cards are",
        "listed most played first; *read* marks a card the parser currently counts as",
        "fully read.",
        "",
    ]
    titles = {
        WRONG: "## 1. The engine does it wrong",
        MISSING: "## 2. The engine lacks it",
        SUSPECT: "## 3. Suspected, not yet proved",
    }
    blurbs = {
        WRONG: "Each was checked on a real board or from the ability the keyword builds. "
        "These cards look understood and play differently from the card.",
        MISSING: "Honest failures: the words were read, and there is nothing in the engine "
        "to tie them to.",
        SUSPECT: "The cost can be paid, but nothing in the engine reads the keyword "
        "afterwards (Buyback's return to hand, Dash's return at end of turn, ...). "
        "Each needs a board test before it moves to section 1 or is cleared.",
    }
    for verdict in (WRONG, MISSING, SUSPECT):
        out += [titles[verdict], "", blurbs[verdict], ""]
        for mechanic in section(verdict):
            group = sorted(by_mechanic[mechanic], key=rank)
            finding = findings.get(mechanic)
            read = sum(1 for r in group if r["counted_as_read"])
            out.append(f"### {mechanic} ({len(group)} cards, {read} counted as read)")
            out.append("")
            if finding is not None:
                cr = f"CR {finding.cr}: " if finding.cr[:1].isdigit() else ""
                out.append(f"- **Card says:** {cr}{finding.card_says}")
                out.append(f"- **Engine does:** {finding.engine_does}")
                out.append(f"- **Evidence:** {finding.evidence}")
                out.append("")
            names = [
                f"{r['card']}{' *(read)*' if r['counted_as_read'] else ''}" for r in group
            ]
            out.append(", ".join(names))
            out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--census", type=Path, required=True, help="a saved census")
    parser.add_argument("--out", type=Path, default=Path("docs/engine-gaps.md"))
    args = parser.parse_args(argv)
    from .census import load

    rows, _ = build(load(args.census))
    args.out.write_text(render(rows) + "\n", encoding="utf8")
    with args.out.with_suffix(".csv").open("w", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["card"])
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["verdict"], r["mechanic"], r["card"])))
    counts = collections.Counter(r["verdict"] for r in rows)
    print(f"wrote {args.out} ({json.dumps(dict(counts))} card-mechanic rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
