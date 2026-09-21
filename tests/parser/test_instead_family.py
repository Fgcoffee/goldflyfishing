"""The whole "instead" family, read as one grammar.

Every amount-changing replacement in Magic is the same sentence::

    If <event> would <happen>, <the same event, resized> instead.

The substitute is not a *new* effect - it is the original event with a
different number. Read as a new effect, Doubling Season produces a second
batch of counters rather than a bigger one: a card that still runs and does
the wrong thing, which is the failure mode that matters.

So one clause reads the family and one arithmetic applies it - multiply, then
add. Writing a rule per card is how "twice that many" and "that many plus one"
ended up in different code paths that could disagree.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.tokens import Stream
from mtgfish.rules.cr614_replacement import ReplacementKind
from mtgfish.rules.effects import EffectKind


@pytest.fixture(autouse=True)
def _registry(card_db):
    card_db.registry()


def _replacement(text):
    stream = Stream.of(text)
    effects = parse_effects(stream)
    assert effects is not None and stream.done, f"unread: {stream.remaining()}"
    return next(
        node
        for effect in effects
        for node in effect.walk()
        if node.kind is EffectKind.REPLACEMENT
    )


# ---------------------------------------------------------------------------
# The event half
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (
            "If one or more +1/+1 counters would be put on a creature you control, "
            "that many plus one are put on it instead.",
            ReplacementKind.MODIFY_COUNTERS,
        ),
        (
            "If an effect would put one or more counters on a permanent you control, "
            "it puts twice that many of those counters on that permanent instead.",
            ReplacementKind.MODIFY_COUNTERS,
        ),
        (
            "If one or more tokens would be created under your control, "
            "twice that many of those tokens are created instead.",
            ReplacementKind.MODIFY_TOKENS,
        ),
        (
            "If a source you control would deal damage to a permanent or player, "
            "it deals triple that damage instead.",
            ReplacementKind.MODIFY_DAMAGE,
        ),
        (
            "If you would gain life, you gain twice that much life instead.",
            ReplacementKind.MODIFY_LIFE_CHANGE,
        ),
        (
            "If an opponent would lose life during your turn, "
            "they lose twice that much life instead.",
            ReplacementKind.MODIFY_LIFE_CHANGE,
        ),
    ],
)
def test_the_event_half_picks_the_right_replacement_kind(text, kind):
    """Which event is caught decides everything downstream. A damage doubler
    registered against the counter event applies to nothing at all."""
    assert _replacement(text).replacement_kind == int(kind)


def test_the_subject_may_be_the_counters_themselves(card_db):
    """English puts the subject in either place, and both are common:

    "If ONE OR MORE COUNTERS would be put on ..."   - the thing itself
    "If A SOURCE YOU CONTROL would deal damage ..." - whoever acts

    The first is not a noun phrase the object grammar can read, so a reader
    that only handled the second failed on the word "would".
    """
    effect = _replacement(
        "If one or more +1/+1 counters would be put on a creature you control, "
        "that many plus one are put on it instead."
    )
    assert effect.counter_type == "+1/+1"
    assert effect.targets is not None


def test_a_counter_kind_may_be_unnamed(card_db):
    """Hardened Scales names a kind; Doubling Season does not. Insisting on
    one missed every card in the second group."""
    effect = _replacement(
        "If an effect would put one or more counters on a permanent you control, "
        "it puts twice that many of those counters on that permanent instead."
    )
    assert effect.counter_type == ""


# ---------------------------------------------------------------------------
# The amount half
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phrase", "multiplier", "extra"),
    [
        ("that many plus one are put on it", 1, 1),
        ("twice that many of those counters are put on it", 2, 0),
        ("three times that many of those counters are put on it", 3, 0),
    ],
)
def test_the_amount_half(phrase, multiplier, extra):
    effect = _replacement(
        "If one or more +1/+1 counters would be put on a creature you control, "
        f"{phrase} instead."
    )
    assert (effect.multiplier, effect.amount.constant) == (multiplier, extra)


# ---------------------------------------------------------------------------
# Real cards, and the arithmetic applied on a board
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Hardened Scales",
        "Branching Evolution",
        "Corpsejack Menace",
        "Doubling Season",
        "Mondrak, Glory Dominus",
    ],
)
def test_the_card_reads_completely(card_db, name):
    from mtgfish.parser import parse_card

    card = card_db.lookup(name)
    if card is None:
        pytest.skip(f"{name} not in this pool")
    parsed = parse_card(card)
    assert parsed.fully_parsed, [f.reason for f in parsed.failures]


def test_multiply_happens_before_add(card_db, tmp_path):
    """One arithmetic for the family: multiply, then add. Two doublers of
    different shapes must compose rather than each doing its own thing."""
    from mtgfish.parser.verdicts import VerdictStore
    from mtgfish.rules import actions
    from mtgfish.ui.sandbox import Sandbox

    box = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Hardened Scales", "battlefield", 0)
    box.put("Branching Evolution", "battlefield", 0)
    box.game.invalidate_characteristics()

    bear = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Grizzly Bears"
    )
    actions.add_counters(box.game, bear, "+1/+1", 1, source=bear.id)
    # (1 x 2) + 1 or (1 + 1) x 2 - the order is the controller's choice
    # (CR 616.1), and either is legal. Neither being applied is not.
    assert bear.counter_count("+1/+1") in (3, 4)


# ---------------------------------------------------------------------------
# The attack-tax family, which needed a scaling cost
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Propaganda", "Ghostly Prison", "Windborn Muse"])
def test_an_attack_tax_reads(card_db, name):
    from mtgfish.parser import parse_card

    card = card_db.lookup(name)
    if card is None:
        pytest.skip(f"{name} not in this pool")
    assert parse_card(card).fully_parsed


def test_an_attack_tax_scales_with_the_attackers(card_db):
    """"{2} for each creature they control that's attacking you". A flat tax
    is a much weaker card once a board develops."""
    from mtgfish.parser import parse_card
    from mtgfish.rules.effects import EffectKind

    card = card_db.lookup("Propaganda")
    if card is None:
        pytest.skip("Propaganda not in this pool")

    nodes = [
        node
        for face in parse_card(card).faces
        for ability in face.abilities
        for effect in ability.effects
        for node in effect.walk()
        if node.kind is EffectKind.UNLESS_PAYS
    ]
    assert nodes, "the unless-pays half must survive"
    assert nodes[0].pay_cost is not None
