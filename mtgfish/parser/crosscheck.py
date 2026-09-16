"""Checking the parser against somebody else's reading of the same cards.

Full consumption guarantees a card is either read completely or reported as
unread. It cannot tell you whether a card that *did* parse parsed **correctly**
- and that is the failure nobody catches by eye, because the card looks fine
and the simulation quietly does the wrong thing.

So the check has to come from outside. Scryfall's oracle tags are a human-
curated reading of the same 37,000 cards: this one is removal, that one draws
cards, this one has an activated ability. If the taggers say a card is removal
and the parser produced no removal effect, one of the two is wrong, and it is
worth a look either way.

The tags are never used to *generate* behaviour - only to disagree. A parser
that learned from the tags would agree with them by construction and the check
would be worth nothing.

Disagreements are grouped by tag rather than listed by card, because 412 cards
tagged ``removal-destroy`` that produced no destroy effect is one missing
grammar rule, not 412 problems.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

from ..rules.abilities import AbilityKind
from ..rules.effects import EffectKind
from .compile import parse_card


@dataclass(frozen=True, slots=True)
class Expectation:
    """What a tag implies the parse should contain.

    ``any_of`` is satisfied by one match, because most tags describe an
    outcome that several opcodes can produce - "spot removal" is destroy,
    exile, bounce or damage depending on the card.
    """

    tag: str
    any_of: frozenset[EffectKind] = frozenset()
    ability_kind: AbilityKind | None = None
    #: Some tags describe an absence. A "vanilla" creature that parsed into
    #: three abilities is as wrong as removal that produced none.
    expect_no_abilities: bool = False


#: The tags worth checking, and what each implies.
#:
#: Deliberately a small, high-confidence set. A tag whose meaning is fuzzy
#: ("drawback", "alliteration") produces noise, and a check that cries wolf
#: gets ignored - which is worse than not having it.
EXPECTATIONS: tuple[Expectation, ...] = (
    Expectation("removal-destroy", frozenset({EffectKind.DESTROY})),
    Expectation("removal-exile", frozenset({EffectKind.EXILE})),
    Expectation("pure draw", frozenset({EffectKind.DRAW})),
    Expectation("repeatable pure draw", frozenset({EffectKind.DRAW})),
    Expectation("burn any", frozenset({EffectKind.DAMAGE})),
    Expectation("burn creature", frozenset({EffectKind.DAMAGE})),
    Expectation("opponent loses life", frozenset({EffectKind.LOSE_LIFE})),
    Expectation(
        "repeatable creature tokens", frozenset({EffectKind.CREATE_TOKEN})
    ),
    Expectation("gives pp counters", frozenset({EffectKind.ADD_COUNTERS})),
    Expectation("gains pp counters", frozenset({EffectKind.ADD_COUNTERS})),
    Expectation("power boost to all", frozenset({EffectKind.MODIFY_PT})),
    Expectation("triggered ability", ability_kind=AbilityKind.TRIGGERED),
)

#: Tags deliberately *not* checked, and why. Kept in the source rather than
#: deleted, because the next person to look at this list will otherwise add
#: them back and get the same noise.
#:
#: "spot removal"        - a tapper or a -1/-1 counter is removal to a tagger
#:                         and something else entirely to the engine.
#: "repeatable lifegain" - lifelink is lifegain and produces no GAIN_LIFE.
#: "activated ability"   - a land's mana ability comes from its land types
#:                         (CR 305.6), not from any text this parser reads.
#: "mana ability"        - same.
#: "draw engine"         - too broad; a card that loots is a draw engine.
#: "virtual french vanilla" - means "vanilla once it has resolved", so an
#:                         enters-the-battlefield trigger is expected rather
#:                         than a contradiction.
NOT_CHECKED = frozenset(
    {
        "spot removal",
        "repeatable lifegain",
        "activated ability",
        "mana ability",
        "draw engine",
        "virtual french vanilla",
    }
)

#: Tags meaning "every ability on this card is a keyword ability". An ability
#: that came from a keyword counts even when it expands into effects -
#: Bloodthirst is a keyword and it places counters.
KEYWORDS_ONLY = frozenset({"french vanilla"})

#: Cards with no abilities at all.
VANILLA = frozenset({"vanilla"})


@dataclass
class Disagreement:
    """One tag's worth of cards where the parse and the taggers differ."""

    tag: str
    expected: str
    cards: list[str] = field(default_factory=list)
    checked: int = 0

    @property
    def rate(self) -> float:
        return len(self.cards) / self.checked if self.checked else 0.0


@dataclass
class CrossCheckReport:
    checked: int = 0
    tagged: int = 0
    disagreements: list[Disagreement] = field(default_factory=list)
    #: Cards with no tags at all, which cannot be checked either way.
    untagged: int = 0

    def render(self, limit: int = 20) -> str:
        lines = [
            f"Cross-checked {self.checked} fully-parsed cards against Scryfall's "
            "oracle tags.",
            f"{self.tagged} carried a tag this check understands; "
            f"{self.untagged} did not.",
            "",
            "Where the parse and the taggers disagree:",
        ]
        if not self.disagreements:
            lines.append("  (nothing - every checkable card agreed)")
            return "\n".join(lines)

        ranked = sorted(
            self.disagreements, key=lambda d: len(d.cards), reverse=True
        )
        for item in ranked[:limit]:
            lines.append(
                f"  {item.tag:28} {len(item.cards):5} of {item.checked:5}"
                f"  ({item.rate:.0%})  expected {item.expected}"
            )
            for name in item.cards[:3]:
                lines.append(f"      e.g. {name}")
        return "\n".join(lines)


def cross_check(cards, db) -> CrossCheckReport:
    """Compare every fully-parsed card against its tags.

    Only fully-parsed cards are checked. An unparsed card is already known to
    be wrong and reported elsewhere; the point here is the ones that look
    fine.
    """
    report = CrossCheckReport()
    by_tag: dict[str, Disagreement] = {}

    for card in cards:
        parsed = parse_card(card)
        if parsed.failures or not any(face.abilities for face in parsed.faces):
            continue
        report.checked += 1

        tags = set(db.oracle_tags(card.oracle_id))
        if not tags:
            report.untagged += 1
            continue
        report.tagged += 1

        produced, kinds = _produced(parsed)
        for expectation in EXPECTATIONS:
            if expectation.tag not in tags:
                continue
            entry = by_tag.setdefault(
                expectation.tag,
                Disagreement(
                    tag=expectation.tag, expected=_describe(expectation)
                ),
            )
            entry.checked += 1
            if not _satisfied(expectation, produced, kinds):
                entry.cards.append(card.name)

        for tag in tags & KEYWORDS_ONLY:
            entry = by_tag.setdefault(
                tag,
                Disagreement(tag=tag, expected="every ability from a keyword"),
            )
            entry.checked += 1
            if _has_non_keyword_ability(parsed):
                entry.cards.append(card.name)

        for tag in tags & VANILLA:
            entry = by_tag.setdefault(
                tag, Disagreement(tag=tag, expected="no abilities at all")
            )
            entry.checked += 1
            if any(face.abilities for face in parsed.faces):
                entry.cards.append(card.name)

    report.disagreements = list(by_tag.values())
    return report


def _has_non_keyword_ability(parsed) -> bool:
    """Whether anything on the card came from something other than a keyword.

    ``ability.keyword`` is set by the keyword registry and by nothing else, so
    it is an exact answer to "did this come from a keyword" - including for
    keywords like Bloodthirst whose expansion has real effects.
    """
    return any(
        not ability.keyword
        for face in parsed.faces
        for ability in face.abilities
    )


def _produced(parsed) -> tuple[set[EffectKind], set[AbilityKind]]:
    effects: set[EffectKind] = set()
    kinds: set[AbilityKind] = set()
    for face in parsed.faces:
        for ability in face.abilities:
            kinds.add(ability.kind)
            for effect in ability.effects:
                for node in effect.walk():
                    effects.add(node.kind)
    return effects, kinds


def _satisfied(
    expectation: Expectation,
    produced: set[EffectKind],
    kinds: set[AbilityKind],
) -> bool:
    if expectation.ability_kind is not None:
        return expectation.ability_kind in kinds
    if expectation.any_of:
        return bool(produced & expectation.any_of)
    return True


def _describe(expectation: Expectation) -> str:
    if expectation.ability_kind is not None:
        return f"an {expectation.ability_kind.name.lower()} ability"
    names = sorted(kind.name for kind in expectation.any_of)
    if len(names) > 3:
        return f"one of {len(names)} removal-ish effects"
    return " or ".join(names)


def worst_tags(report: CrossCheckReport, limit: int = 10) -> list[tuple[str, int]]:
    """The tags with the most disagreements - the work queue."""
    counter = collections.Counter(
        {item.tag: len(item.cards) for item in report.disagreements}
    )
    return counter.most_common(limit)
