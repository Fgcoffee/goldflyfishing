"""Operators, not templates.

Clustering failures by their first four tokens said there were 328 distinct
problems in the top thousand cards. Clustering by the single word the parse
stopped on said 96 words, twenty of which covered two thirds of everything.
The templates were never the structure — the operators were, and three of them
accounted for a quarter of all failures on their own:

``for each X``   a multiplier. Read only when it *was* the whole amount, never
                 when it modified one.
``if <cond>``    a condition. ``unless`` and ``as long as`` already worked as
                 trailing modifiers; ``if`` never did, though it is commonest.
``or``           disjunction, implemented for nouns and card types only.

The lesson is in the attachment point. "Deals 1 damage **to any target** for
each creature you control" puts the target between the number and its
multiplier, so attaching ``for each`` to the amount reader could never work —
it belongs to the whole effect, which is where the other trailing operators
already lived.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.tokens import Stream
from mtgfish.rules.effects import EffectKind
from mtgfish.rules.query import ValueKind


@pytest.fixture(autouse=True)
def _registry(card_db):
    card_db.registry()


def _effects(text):
    stream = Stream.of(text)
    effects = parse_effects(stream)
    assert effects is not None and stream.done, f"unread: {stream.remaining()}"
    return effects


def _only(effects, kind):
    return next(
        node for effect in effects for node in effect.walk() if node.kind is kind
    )


# ---------------------------------------------------------------------------
# "for each X"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "this creature deals 1 damage to any target for each creature you control.",
        "You gain 1 life for each artifact you control.",
        "You gain 2 life for each charge counter on this artifact.",
        "Add {B} for each Swamp you control.",
    ],
)
def test_for_each_reads_wherever_it_sits(text):
    _effects(text)


def test_the_multiplier_reaches_the_amount():
    """Reading the words is not enough - the count has to end up in the
    quantity, or the card parses and does one damage."""
    effects = _effects(
        "this creature deals 1 damage to any target for each creature you control."
    )
    damage = _only(effects, EffectKind.DAMAGE)
    assert damage.amount.kind is ValueKind.COUNT


def test_a_leading_number_multiplies_rather_than_replaces():
    effects = _effects("You gain 2 life for each charge counter on this artifact.")
    gain = _only(effects, EffectKind.GAIN_LIFE)
    assert gain.amount.kind is ValueKind.PRODUCT


def test_mana_production_scales(card_db, tmp_path):
    """Mana comes from a symbol list, so a scaled *amount* is ignored - "Add
    {B} for each Swamp" would make one black mana however many Swamps were
    out. Cabal Coffers is the card that makes this obvious."""
    from mtgfish.parser.verdicts import VerdictStore
    from mtgfish.ui.sandbox import Sandbox

    if card_db.lookup("Cabal Coffers") is None:
        pytest.skip("Cabal Coffers not in this pool")

    added = {}
    for swamps in (2, 4):
        box = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / f"v{swamps}.json"))
        box.put("Cabal Coffers", "battlefield", 0)
        for _ in range(swamps):
            box.put("Swamp", "battlefield", 0)
        box.game.invalidate_characteristics()
        box.give_mana(5)

        before = box.game.player(0).mana_pool.total
        action = next(a for a in box.legal() if "Cabal Coffers" in a["description"])
        box.perform(action["index"])
        # Net of the {2} activation cost.
        added[swamps] = box.game.player(0).mana_pool.total - before + 2

    assert added == {2: 2, 4: 4}


def test_an_effect_without_a_multiplier_is_untouched():
    effects = _effects("You gain 2 life.")
    gain = _only(effects, EffectKind.GAIN_LIFE)
    assert gain.amount.kind is ValueKind.CONSTANT
    assert gain.amount.constant == 2


# ---------------------------------------------------------------------------
# "if" as a trailing condition
# ---------------------------------------------------------------------------


def test_if_reads_on_either_side():
    """"Draw a card if you control a Forest" and "If you control a Forest,
    draw a card" are the same card. Only the second parsed."""
    for text in (
        "Draw a card if you control a Forest.",
        "If you control a Forest, draw a card.",
    ):
        effects = _effects(text)
        kinds = {node.kind for effect in effects for node in effect.walk()}
        assert EffectKind.CONDITIONAL in kinds


def test_an_unconditional_effect_stays_unconditional():
    effects = _effects("Draw a card.")
    kinds = {node.kind for effect in effects for node in effect.walk()}
    assert EffectKind.CONDITIONAL not in kinds


def test_a_trailing_condition_keeps_the_effect_it_guards():
    effects = _effects("Draw a card if you control a Forest.")
    conditional = _only(effects, EffectKind.CONDITIONAL)
    assert conditional.children
    inner = {node.kind for child in conditional.children for node in child.walk()}
    assert EffectKind.DRAW in inner


# ---------------------------------------------------------------------------
# Comparisons, which are disjunction in numeric clothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Destroy target creature with mana value 3 or less.",
        "Destroy target creature with mana value less than or equal to 3.",
        "Destroy target creature with power 4 or greater.",
    ],
)
def test_comparison_forms(text):
    """"less than or equal to" has to be tried before "less than", or the
    short form matches and strands "or equal to"."""
    _effects(text)
