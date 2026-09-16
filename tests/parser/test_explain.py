"""The round-trip: what the parser says it understood.

These tests guard one property above all others - that the explanation is
built from the opcodes and not from the oracle text the parser was handed. An
explainer that echoes its input always agrees with the card, which would make
the whole review workflow a rubber stamp.
"""

from __future__ import annotations

import pytest

from mtgfish.parser import parse_card
from mtgfish.parser.explain import explain_ability, explain_card
from mtgfish.rules.abilities import Ability, AbilityKind
from mtgfish.rules.effects import Effect, EffectKind
from mtgfish.rules.enums import CardType, Duration
from mtgfish.rules.query import ControllerRelation, ObjectFilter, Value


def _abilities(db, name):
    card = db.lookup(name)
    assert card is not None, name
    return [
        ability
        for face in parse_card(card).faces
        for ability in face.abilities
    ]


# ---------------------------------------------------------------------------
# The central property
# ---------------------------------------------------------------------------


def test_the_explanation_ignores_the_text_it_was_parsed_from():
    """The one thing that makes this worth having.

    An effect carries the oracle snippet it came from. If the explainer used
    it, a parse that quietly dropped a constraint would still read back
    perfectly and the reviewer would approve a card the engine gets wrong.
    """
    effect = Effect(
        EffectKind.DRAW,
        amount=Value.of(3),
        text="destroy target creature an opponent controls",
    )
    ability = Ability(AbilityKind.SPELL, effects=(effect,))

    said = explain_ability(ability)
    assert "draw" in said.lower()
    assert "destroy" not in said.lower()


def test_a_dropped_constraint_changes_the_explanation():
    """The failure this is meant to catch, stated directly."""
    full = ObjectFilter(
        types_all=CardType.CREATURE, controller=ControllerRelation.OPPONENT
    )
    lost = ObjectFilter(types_all=CardType.CREATURE)

    def destroy(spec):
        return explain_ability(
            Ability(
                AbilityKind.SPELL,
                effects=(
                    Effect(EffectKind.DESTROY, targets=spec, is_targeted=True),
                ),
            )
        )

    assert "an opponent controls" in destroy(full)
    assert "an opponent controls" not in destroy(lost)


def test_a_dropped_duration_changes_the_explanation():
    def pump(duration):
        return explain_ability(
            Ability(
                AbilityKind.SPELL,
                effects=(
                    Effect(
                        EffectKind.MODIFY_PT,
                        targets=ObjectFilter(types_all=CardType.CREATURE),
                        is_targeted=True,
                        amount=Value.of(3),
                        amount2=Value.of(3),
                        duration=duration,
                    ),
                ),
            )
        )

    assert "until end of turn" in pump(Duration.END_OF_TURN)
    assert "until end of turn" not in pump(Duration.PERMANENT)


# ---------------------------------------------------------------------------
# Real cards
# ---------------------------------------------------------------------------


def test_a_burn_spell_reads_back_as_damage(card_db):
    (bolt,) = _abilities(card_db, "Lightning Bolt")
    said = explain_ability(bolt)
    assert "3 damage" in said
    assert "player" in said, "any target includes players (CR 115.4)"


def test_a_pump_spell_keeps_its_duration(card_db):
    (growth,) = _abilities(card_db, "Giant Growth")
    said = explain_ability(growth)
    assert "+3/+3" in said
    assert "until end of turn" in said


def test_a_mana_ability_names_the_symbols_it_produces(card_db):
    """Not "two mana": a dual land makes one of each, and the difference is
    the difference between a correct mana base and a doubled one."""
    abilities = _abilities(card_db, "Simic Growth Chamber")
    said = " ".join(explain_ability(a) for a in abilities)
    assert "{G}{U}" in said


def test_an_unreadable_ability_says_it_is_inert(card_db):
    from mtgfish.rules.abilities import Ability as A

    assert "inert" in explain_ability(A.unreadable("blah blah")).lower()


def test_explain_card_pairs_each_ability_with_its_oracle_text(card_db):
    rows = explain_card(parse_card(card_db.lookup("Lightning Bolt")))
    assert rows
    row = rows[0]
    assert row["oracle"], "the reviewer needs the card's own words"
    assert row["understood"], "and what the engine made of them"
    assert row["oracle"] != row["understood"]


@pytest.mark.parametrize(
    "name",
    ["Sol Ring", "Llanowar Elves", "Wrath of God", "Counterspell", "Serra Angel"],
)
def test_common_cards_explain_without_falling_over(card_db, name):
    """A renderer that raises on a normal card is worse than a rough one."""
    for ability in _abilities(card_db, name):
        assert explain_ability(ability)


def test_every_effect_opcode_the_parser_emits_has_a_phrasing(card_db):
    """A missing phrasing renders as "[opcode_name]", which is deliberate -
    it is visible. This test says how much of that is left, so it cannot
    quietly grow."""
    from mtgfish.parser.explain import _effect

    seen: set[str] = set()
    for index, card in enumerate(card_db.iter_cards(commander_legal_only=True)):
        if index > 4000:
            break
        for face in parse_card(card).faces:
            for ability in face.abilities:
                for effect in ability.effects:
                    for node in effect.walk():
                        if _effect(node).startswith("["):
                            seen.add(node.kind.name)

    assert len(seen) <= 12, f"unphrased opcodes have grown: {sorted(seen)}"
