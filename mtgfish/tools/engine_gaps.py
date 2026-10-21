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
    Finding("Specialize", WRONG, "digital",
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
            "removes a counter each upkeep and sacrifices as the last one comes off,"
            " an upkeep early",
            "IR: upkeep trigger sacrifices when the count reaches 0"),
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
    Finding("Modular", WRONG, "702.43a",
            "when it dies, move its +1/+1 counters (as it last existed) to target artifact creature",
            "moves none: the count is read from the card in the graveyard",
            "auditor board: Arcbound Worker (1 counter) Shocked; Ornithopter got no counter"),
    Finding("Replicate", MISSING, "702.56a",
            "a copy for each time the replicate cost was paid; each copy may choose new targets",
            "copies keep the original's targets: refused on targeted spells, read on the rest",
            "auditor: copies are never offered new targets"),
    Finding("Crew", WRONG, "702.122a",
            "tap other untapped creatures with total power N or greater",
            "taps N creatures, whatever their power",
            "auditor board: Cultivator's Caravan (Crew 3) can't be crewed by one Hill Giant"),
    Finding("Jump-start", WRONG, "702.133a",
            "cast from the graveyard for its mana cost plus discarding a card; then exiled",
            "cast from the graveyard for nothing",
            "auditor board: Radical Idea cast from the graveyard with no mana, no discard"),
    Finding("Aftermath", WRONG, "702.127a",
            "this half is cast only from a graveyard, then exiled",
            "castable from hand and not from the graveyard",
            "auditor board: Dawn (Dusk // Dawn) castable from hand, not from graveyard"),
    Finding("Encore", WRONG, "702.141a",
            "exile it from the graveyard: a token copy attacking each opponent",
            "an alternative cost to cast it",
            "code: registered as a graveyard alternative cost"),
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
            "Assist", "Bargain", "Bestow", "Blitz", "Buyback", "Cleave",
            "Conspire", "Embalm", "Emerge", "Entwine", "Escalate",
            "Eternalize", "Freerunning", "Gift", "Impending", "Miracle",
            "More Than Meets the Eye", "Multikicker", "Offspring", "Prowl",
            "Retrace", "Spectacle", "Splice", "Spree", "Squad", "Warp",
        )
    ),
)


ENGINE, PARSER = "engine", "parser"

#: Where the fault most likely lies, and why. *engine*: the rules engine is
#: missing a piece the card needs (a state, a value, a rule it does not
#: apply). *parser*: the engine has every piece, and the text or keyword was
#: allocated to the wrong ones - a dropped count, a dropped condition, the
#: wrong opcode. Checked by looking for the primitive in mtgfish/rules.
CAUSES: dict[str, tuple[str, str]] = {
    # engine
    "Flashback": (ENGINE, "casting from the graveyard works; the rule that exiles the"
                  " spell afterwards (CR 702.34a) is not applied"),
    "Soulbond": (ENGINE, "no paired state between two creatures"),
    "Saddle": (ENGINE, "no saddled state"),
    "Haunt": (ENGINE, "no haunting relation between a card in exile and a creature"),
    "Sunburst": (ENGINE, "no record of the colors of mana spent to cast a spell"),
    "Amplify": (ENGINE, "no 'reveal any number of cards from hand' step"),
    "Compleated": (ENGINE, "no record of Phyrexian mana paid with life"),
    "Craft": (ENGINE, "no craft cost (exiling materials) or return-transformed"),
    "Specialize": (ENGINE, "no specialize faces (digital)"),
    "Demonstrate": (ENGINE, "no way to copy a spell for another player"),
    "Dethrone": (ENGINE, "no 'player with the most life' comparison"),
    "Harmonize": (ENGINE, "no harmonize cost (tap a creature to reduce it)"),
    "Plot": (ENGINE, "no plotted cards"),
    "Myriad": (ENGINE, "no token copies attacking each other opponent"),
    "Double team": (ENGINE, "no conjure-on-attack (digital)"),
    "Teamwork": (ENGINE, "no tap-creatures-by-total-power additional cost"),
    "Intensity": (ENGINE, "no intensity value (digital)"),
    # parser allocation
    "Annihilator": (PARSER, "per-player sacrifices of N exist (edicts use them); the"
                    " keyword dropped the count and named every opponent"),
    "Exploit": (PARSER, "optional single sacrifices exist; the keyword dropped the count"),
    "Champion": (PARSER, "exile-until-it-leaves exists; the keyword dropped 'another'"
                 " and the count"),
    "Living weapon": (PARSER, "create token and attach exist; the token count was 0"),
    "For Mirrodin!": (PARSER, "create token and attach exist; the token count was 0"),
    "Job select": (PARSER, "create token and attach exist; built as a counter instead"),
    "Fabricate": (PARSER, "choose-one modes exist; the token mode was dropped"),
    "Devour": (PARSER, "any-number sacrifices exist; built as a flat counter count"),
    "Backup": (PARSER, "targeted counters and granted abilities exist; aimed at itself"),
    "Boast": (PARSER, "attacked-this-turn exists; the printed ability was replaced by a"
              " counter"),
    "Fading": (PARSER, "upkeep triggers, counter removal and sacrifice exist; the"
               " sacrifice condition is off by one upkeep"),
    "Recover": (PARSER, "graveyard triggers and pay-or-else exist; wrong trigger, no cost"),
    "Storm": (PARSER, "the spells-cast-this-turn count exists; one copy was built"),
    "Ripple": (PARSER, "reveal and cast-free exist; the same-name filter was dropped"),
    "Evolve": (PARSER, "power/toughness comparisons exist; the condition was dropped"),
    "Training": (PARSER, "attack triggers with conditions exist; the condition was dropped"),
    "Modular": (PARSER, "last-known information exists for sacrificed objects; the dies"
                " trigger reads the counters off the card in the graveyard"),
    "Replicate": (ENGINE, "copying a spell does not let the copy choose new targets"),
    "Crew": (ENGINE, "no 'creatures with total power N' cost; the builder tapped N"),
    "Jump-start": (PARSER, "graveyard casting and discard costs exist; built as a free"
                   " alternative cost"),
    "Aftermath": (PARSER, "graveyard casting exists; the zone was set the wrong way round"),
    "Encore": (PARSER, "graveyard activated abilities and token copies exist; built as a"
               " cast"),
    "Graft": (PARSER, "triggers exist; moving counters may also need engine work"),
    "Hideaway": (PARSER, "look-at-top and exile exist; playing the hidden card later may"
                 " also need engine work"),
    "Gravestorm": (PARSER, "one copy was built; a permanents-to-graveyard count may also"
                   " need engine work"),
}
#: The suspect cost keywords: the cost is paid, nothing reads it afterwards.
SUSPECT_CAUSE = (ENGINE, "the cost can be paid; the casting code never applies what"
                 " paying it means (Buyback's return to hand, Dash's return, ...)")

#: Refusals the parser makes after reading every word.
REFUSAL_CAUSES: dict[str, tuple[str, str]] = {
    "pile reference without a pile": (ENGINE, "the engine has no piles"),
    "player reference on REVEAL": (ENGINE, "the reveal opcode cannot name who reveals"),
    "player reference on EXILE": (ENGINE, "the exile opcode cannot name a player"),
    "a combat cost other than mana": (ENGINE, "attack costs can only be mana"),
    "a combat cost scaled by something the engine cannot count": (
        ENGINE, "the attack cost needs a count the engine has no value for"),
    "'its controller' with no single antecedent": (
        PARSER, "the parser cannot decide whose controller is meant"),
    "'that player' with no single antecedent": (
        PARSER, "the parser cannot decide which player is meant"),
    "characteristic of an object it cannot name": (
        PARSER, "the parser cannot bind 'its power' (etc.) to an object"),
    "'other target' after another target": (PARSER, "two targets the parser cannot keep apart"),
    "unbound player reference": (PARSER, "a player reference bound to nothing"),
    "the one who acts was named under a condition": (
        PARSER, "the actor is named inside a condition"),
    "refers back to another chapter": (PARSER, "a Saga chapter referring to another chapter"),
    "a standing prohibition on whatever was just referred to": (
        PARSER, "a lasting 'can't' on an object the parser cannot keep hold of"),
}


def _cause(mechanic: str, verdict: str) -> tuple[str, str]:
    if mechanic.startswith("refused: "):
        return REFUSAL_CAUSES.get(mechanic[len("refused: "):], (PARSER, "unclassified refusal"))
    if verdict == SUSPECT:
        return SUSPECT_CAUSE
    return CAUSES.get(mechanic, (ENGINE, "unclassified"))


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
    from ..rules.cr700_additional_rules.cr702_keyword_impl import MISBUILT_KEYWORDS

    for finding in FINDINGS:
        pattern = _keyword_line(finding.keyword)
        # A wrong builder the engine now refuses (built inert) is no longer
        # played wrong; the card honestly reports as not understood.
        verdict = finding.verdict
        if verdict == WRONG and finding.keyword.lower() in MISBUILT_KEYWORDS:
            verdict = MISSING
        for record in cards:
            if any(pattern(text) for text in _card_texts(record)):
                rows.append(_row(record, finding.keyword, verdict, finding.engine_does))
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
    cause, why = _cause(mechanic, verdict)
    return {
        "card": record["name"],
        "rank": record.get("rank") or "",
        "counted_as_read": record["status"] == "ok",
        "cause": cause,
        "mechanic": mechanic,
        "verdict": verdict,
        "why": why,
        "note": note,
    }


def render(rows: list[dict]) -> str:
    by_mechanic: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_mechanic[row["mechanic"]].append(row)
    findings = {f.keyword: f for f in FINDINGS}

    def rank(row):
        return row["rank"] if row["rank"] != "" else 10**9

    def cards(cause: str, verdict: str) -> set[str]:
        return {
            r["card"] for r in rows
            if r["cause"] == cause and r["verdict"] == verdict
            and (verdict == MISSING or r["counted_as_read"])
        }

    out = [
        "# Cards that don't work because of the rules engine",
        "",
        "Generated by `python -m mtgfish.tools.engine_gaps` from a census of every card",
        "legal in at least one format. Regenerated as the work changes it; do not edit by hand.",
        "",
        "Split by where the fault most likely lies:",
        "",
        "- **Rules engine bugs** - the engine is missing a piece the card needs (a state,",
        "  a value, a rule it never applies). Fixing them means engine work.",
        "- **Parser allocation bugs** - the engine has every piece the card needs, and the",
        "  text or keyword was wired to the wrong ones (a dropped count, a dropped",
        "  condition, the wrong operation). Fixing them means changing what the text",
        "  is compiled to.",
        "",
        "Within each, three severities: **wrong** (counted as read, plays differently from",
        "the card - the worst case), **missing** (honestly reported as not understood) and",
        "**suspected** (counted as read, not yet proved wrong on a board).",
        "",
        "| | Rules engine | Parser allocation |",
        "|---|---|---|",
    ]
    for verdict, label in ((WRONG, "Wrong, counted as read"), (MISSING, "Missing, not understood"),
                           (SUSPECT, "Suspected, counted as read")):
        out.append(
            f"| {label} | {len(cards(ENGINE, verdict))} | {len(cards(PARSER, verdict))} |"
        )
    out += [
        "",
        "A card can appear under more than one mechanic. Cards are listed most played",
        "first; *(read)* marks a card the parser currently counts as fully read.",
        "",
    ]
    titles = {WRONG: "Wrong", MISSING: "Missing", SUSPECT: "Suspected, not yet proved"}
    for number, (cause, heading) in enumerate(
        ((ENGINE, "Could be rules engine bugs"), (PARSER, "Could be parser allocation bugs")),
        start=1,
    ):
        out += [f"## {number}. {heading}", ""]
        for verdict in (WRONG, MISSING, SUSPECT):
            names = [
                m for m, group in by_mechanic.items()
                if group[0]["cause"] == cause and group[0]["verdict"] == verdict
            ]
            if not names:
                continue
            names.sort(key=lambda m: -len(by_mechanic[m]))
            out += [f"### {number}.{'abc'[(WRONG, MISSING, SUSPECT).index(verdict)]} {titles[verdict]}", ""]
            for mechanic in names:
                group = sorted(by_mechanic[mechanic], key=rank)
                finding = findings.get(mechanic)
                read = sum(1 for r in group if r["counted_as_read"])
                out.append(f"#### {mechanic} ({len(group)} cards, {read} counted as read)")
                out.append("")
                out.append(f"- **Likely cause:** {group[0]['why']}")
                if finding is not None:
                    cr = f"CR {finding.cr}: " if finding.cr[:1].isdigit() else ""
                    out.append(f"- **Card says:** {cr}{finding.card_says}")
                    out.append(f"- **Engine does:** {finding.engine_does}")
                    out.append(f"- **Evidence:** {finding.evidence}")
                out.append("")
                out.append(", ".join(
                    f"{r['card']}{' *(read)*' if r['counted_as_read'] else ''}" for r in group
                ))
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
        writer = csv.DictWriter(
            handle, fieldnames=list(rows[0].keys()) if rows else ["card"], lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["cause"], r["verdict"], r["mechanic"], r["card"])))
    counts = collections.Counter(r["verdict"] for r in rows)
    print(f"wrote {args.out} ({json.dumps(dict(counts))} card-mechanic rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
