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
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import CardType, Duration
from mtgfish.rules.kernel.query import ControllerRelation, ObjectFilter, Value


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
    from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability as A

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

    assert len(seen) <= 10, f"unphrased opcodes have grown: {sorted(seen)}"


# ---------------------------------------------------------------------------
# Fields that change behaviour must change the rendering
# ---------------------------------------------------------------------------


def _say(*effects):
    return explain_ability(Ability(AbilityKind.SPELL, effects=effects))


def test_whose_option_it_is_is_rendered():
    from mtgfish.rules.kernel.query import PlayerFilter, PlayerScope

    draw = Effect(EffectKind.DRAW, amount=Value.of(1))
    mine = _say(Effect(EffectKind.OPTIONAL, children=(draw,)))
    theirs = _say(
        Effect(
            EffectKind.OPTIONAL,
            players=PlayerFilter(PlayerScope.EACH_OPPONENT),
            children=(draw,),
        )
    )
    assert mine != theirs
    assert "each opponent may" in theirs.lower()


def test_an_untargeted_target_scope_is_not_rendered_as_a_target():
    """The resolver answers an untargeted "target player" from the scope
    alone, which is nobody - it must not read like a real target."""
    from mtgfish.rules.kernel.query import PlayerFilter, PlayerScope

    scope = PlayerFilter(PlayerScope.TARGET_PLAYER)
    targeted = _say(Effect(EffectKind.DRAW, players=scope, is_targeted=True))
    loose = _say(Effect(EffectKind.DRAW, players=scope))
    assert "not targeted" in loose
    assert "not targeted" not in targeted


def test_declining_shows_its_consequence():
    said = _say(
        Effect(
            EffectKind.IF_YOU_DONT,
            children=(Effect(EffectKind.LOSE_LIFE, amount=Value.of(3)),),
        )
    )
    assert "loses 3 life" in said
    assert "[if_you_dont]" not in said


def test_a_delayed_trigger_says_whether_it_repeats():
    from mtgfish.rules.cr600_spells_and_abilities.abilities import TriggerCondition
    from mtgfish.rules.kernel.events import EventKind

    trigger = TriggerCondition(event_kinds=frozenset({EventKind.UPKEEP}))
    draw = (Effect(EffectKind.DRAW, amount=Value.of(1)),)
    once = _say(Effect(EffectKind.DELAYED_TRIGGER, trigger=trigger, children=draw))
    each = _say(
        Effect(EffectKind.DELAYED_TRIGGER, trigger=trigger, children=draw, repeats=True)
    )
    assert once != each


def test_top_and_bottom_of_the_library_differ():
    spec = ObjectFilter(remembered=True)
    top = _say(Effect(EffectKind.PUT_ON_LIBRARY, targets=spec))
    bottom = _say(Effect(EffectKind.PUT_ON_LIBRARY, targets=spec, amount=Value.of(-1)))
    assert "top" in top and "bottom" in bottom


def test_divided_damage_reads_differently_from_damage_to_each():
    spec = ObjectFilter(types_all=CardType.CREATURE)
    each = _say(Effect(EffectKind.DAMAGE, targets=spec, amount=Value.of(4), is_targeted=True))
    split = _say(
        Effect(
            EffectKind.DAMAGE, targets=spec, amount=Value.of(4), is_targeted=True, divided=True
        )
    )
    assert "divided" in split and "divided" not in each


def test_a_steal_shows_how_long_it_lasts():
    spec = ObjectFilter(types_all=CardType.CREATURE)
    said = _say(
        Effect(
            EffectKind.GAIN_CONTROL,
            targets=spec,
            is_targeted=True,
            duration=Duration.END_OF_TURN,
        )
    )
    assert "until end of turn" in said


def test_exiling_the_top_of_a_library_is_not_exiling_the_source():
    from mtgfish.rules.kernel.enums import Zone
    from mtgfish.rules.kernel.query import PlayerFilter, PlayerScope

    said = _say(
        Effect(
            EffectKind.EXILE,
            players=PlayerFilter(PlayerScope.EACH_PLAYER),
            from_zone=Zone.LIBRARY,
            amount=Value.of(1),
        )
    )
    assert "each player's library" in said
    assert "this permanent" not in said


def test_a_mode_count_is_rendered():
    modes = tuple(
        Effect(EffectKind.SEQUENCE, children=(Effect(EffectKind.DRAW, amount=Value.of(n)),))
        for n in (1, 2, 3)
    )
    one = _say(Effect(EffectKind.CHOOSE_MODE, children=modes))
    two = _say(Effect(EffectKind.CHOOSE_MODE, children=modes, amount=Value.of(2)))
    assert "choose one" in one.lower()
    assert "choose 2" in two.lower()
