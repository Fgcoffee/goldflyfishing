"""Keywords that change how a spell is paid for or activated (CR 702).

Four keywords that the registry used to call PARTIAL - an ability of the right
shape wrapped around an effect the engine could not run:

* sneak (CR 702.190) and web-slinging (CR 702.188), alternative costs that
  also return one of your creatures to your hand;
* power-up (CR 702.193), an activated ability with an activation limit;
* paradigm (CR 702.192), which is still partial and is tested here so the
  reason stays visible.

The shape tests check what the builder produces; the engine tests cast and
activate the result, because an alternative cost that the legality layer never
offers is an alternative cost that does not exist.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaCost
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind
from mtgfish.rules.cr600_spells_and_abilities.abilities import AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import (
    KeywordInstance,
    build,
)
from mtgfish.rules.cr700_additional_rules.keywords import Status, lookup
from mtgfish.rules.kernel.enums import CardType, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.rules.kernel.query import ConditionKind


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def kw(name: str, cost: str | None = None, **kwargs) -> tuple:
    """Build a keyword the way the parser would, with its printed cost."""
    if cost is not None:
        kwargs["cost"] = Cost(
            (CostComponent(CostKind.MANA, mana=ManaCost.parse(cost)),)
        )
    return build(KeywordInstance(name, **kwargs))


def give_mana(board, player: int, amount: int, color: str = "G") -> None:
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.kernel.enums import LETTER_TO_COLOR

    board.game.player(PlayerId(player)).mana_pool.add(
        ManaKind(LETTER_TO_COLOR[color]), amount
    )


def attack_with(board, creature, defender: int = 1, blocked: bool = False) -> None:
    """Put a creature into combat as an attacker, past blocker declaration."""
    from mtgfish.rules.cr500_turn_structure.cr506_combat import _combat

    combat = _combat(board.game)
    combat.attacking[creature.id] = PlayerId(defender)
    combat.blockers_declared = True
    if blocked:
        combat.was_blocked.add(creature.id)
    board.game.step = Step.DECLARE_BLOCKERS


def alternative_casts(board, player: int = 0) -> list[Action]:
    return [
        action
        for action in legal_actions(board.game, PlayerId(player))
        if action.kind is ActionKind.CAST_SPELL and action.alternative_cost >= 0
    ]


def hand_names(board, player: int = 0) -> list[str]:
    game = board.game
    return [
        game.printed_characteristics(game.objects[oid]).name
        for oid in game.player(PlayerId(player)).hand
    ]


def main_phase(board, player: int = 0) -> None:
    """Your own main phase with an empty stack: sorcery timing (CR 307.1)."""
    from mtgfish.rules.kernel.enums import Phase

    board.game.active_player = PlayerId(player)
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN


def in_combat(board, player: int = 0) -> None:
    """Your declare blockers step: instant timing only."""
    from mtgfish.rules.kernel.enums import Phase

    board.game.active_player = PlayerId(player)
    board.game.phase = Phase.COMBAT
    board.game.step = Step.DECLARE_BLOCKERS


def return_components(cost: Cost) -> list[CostComponent]:
    return [c for c in cost.components if c.kind is CostKind.RETURN_TO_HAND]


# ---------------------------------------------------------------------------
# Sneak (CR 702.190a)
# ---------------------------------------------------------------------------


def test_sneak_is_an_alternative_cost_that_also_returns_an_unblocked_attacker():
    """CR 702.190a: the keyword's cost *and* the returned creature, together."""
    (ability,) = kw("Sneak", "{2}{G}")

    assert ability.kind is AbilityKind.STATIC
    assert ability.alternative_cost is not None
    cost = ability.alternative_cost.cost
    assert cost.mana_component == ManaCost.parse("{2}{G}")

    (returned,) = return_components(cost)
    assert returned.filter.types_all & CardType.CREATURE
    assert returned.filter.attacking is True
    assert returned.filter.blocked is False


def test_sneak_records_its_declare_blockers_window():
    """CR 702.190a limits the cast to your declare blockers step."""
    (ability,) = kw("Sneak", "{G}")

    condition = ability.alternative_cost.condition
    assert condition.kind is ConditionKind.IS_STEP
    assert condition.constraint.value.constant == int(Step.DECLARE_BLOCKERS)


def test_sneak_is_offered_once_an_attacker_is_unblocked(board):
    """The engine has to *offer* the cast, not merely hold the data."""
    board.scripts.add("Runeclaw Bear", *kw("Sneak", "{G}"))
    board.hand("Runeclaw Bear", controller=0)
    ninja = board.play("Grizzly Bears", controller=0)
    give_mana(board, 0, 1)
    attack_with(board, ninja)

    assert alternative_casts(board)


def test_sneak_is_not_offered_with_nothing_unblocked_to_return(board):
    """CR 601.2h: a cost that cannot be paid is not an option at all."""
    board.scripts.add("Runeclaw Bear", *kw("Sneak", "{G}"))
    board.hand("Runeclaw Bear", controller=0)
    board.play("Grizzly Bears", controller=0)  # at home, not attacking
    give_mana(board, 0, 1)

    assert not alternative_casts(board)


def test_sneak_is_not_offered_when_the_attacker_was_blocked(board):
    """CR 509.1h: a blocked attacker is not an unblocked one."""
    board.scripts.add("Runeclaw Bear", *kw("Sneak", "{G}"))
    board.hand("Runeclaw Bear", controller=0)
    blocked = board.play("Grizzly Bears", controller=0)
    give_mana(board, 0, 1)
    attack_with(board, blocked, blocked=True)

    assert not alternative_casts(board)


def test_paying_a_sneak_cost_bounces_the_attacker(board):
    """The cost is charged, not merely checked."""
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell

    board.scripts.add("Runeclaw Bear", *kw("Sneak", "{G}"))
    card = board.hand("Runeclaw Bear", controller=0)
    attacker = board.play("Grizzly Bears", controller=0)
    give_mana(board, 0, 1)
    attack_with(board, attacker)

    (action,) = alternative_casts(board)
    spell = cast_spell(board.game, PlayerId(0), action)

    # CR 400.7: the bounced attacker is a new object in hand, so the board is
    # what the assertion has to ask.
    assert "Grizzly Bears" in hand_names(board)
    assert board.alive(0) == []
    assert spell.zone is Zone.STACK
    assert attacker.id in spell.cost_paid_objects
    assert card.id != spell.id  # the card left hand to become the spell


# ---------------------------------------------------------------------------
# Web-slinging (CR 702.188a)
# ---------------------------------------------------------------------------


def test_web_slinging_returns_a_tapped_creature():
    """CR 702.188a: tapped, and with no timing clause of its own."""
    (ability,) = kw("Web-slinging", "{R}{G}")

    cost = ability.alternative_cost.cost
    assert cost.mana_component == ManaCost.parse("{R}{G}")
    (returned,) = return_components(cost)
    assert returned.filter.tapped is True
    assert returned.filter.attacking is None
    assert ability.alternative_cost.condition is None


def test_web_slinging_is_offered_only_with_a_tapped_creature(board):
    main_phase(board)
    board.scripts.add("Runeclaw Bear", *kw("Web-slinging", "{G}"))
    board.hand("Runeclaw Bear", controller=0)
    creature = board.play("Grizzly Bears", controller=0)
    give_mana(board, 0, 1)

    assert not alternative_casts(board)

    creature.tapped = True
    board.refresh()
    assert alternative_casts(board)


def test_web_slinging_needs_no_combat(board):
    """Unlike sneak: a tapped creature is enough, in a main phase."""
    main_phase(board)
    board.scripts.add("Runeclaw Bear", *kw("Web-slinging", "{G}"))
    board.hand("Runeclaw Bear", controller=0)
    board.play("Grizzly Bears", controller=0, tapped=True)
    give_mana(board, 0, 1)

    assert alternative_casts(board)


def test_web_slinging_keeps_the_spells_own_timing(board):
    """CR 702.188a changes the cost, not the timing: a creature spell cast by
    web-slinging is still sorcery-speed (CR 307.1)."""
    board.scripts.add("Runeclaw Bear", *kw("Web-slinging", "{G}"))
    board.hand("Runeclaw Bear", controller=0)
    board.play("Grizzly Bears", controller=0, tapped=True)
    give_mana(board, 0, 1)
    in_combat(board)

    assert not alternative_casts(board)


def test_sneak_is_not_offered_outside_the_declare_blockers_step(board):
    """CR 702.190a names the step; an unblocked attacker in the combat damage
    step is still unblocked, but the window has closed."""
    board.scripts.add("Runeclaw Bear", *kw("Sneak", "{G}"))
    board.hand("Runeclaw Bear", controller=0)
    ninja = board.play("Grizzly Bears", controller=0)
    give_mana(board, 0, 1)
    attack_with(board, ninja)
    board.game.step = Step.COMBAT_DAMAGE

    assert not alternative_casts(board)


def test_sneak_is_not_offered_on_an_opponents_turn(board):
    """CR 702.190a: *your* declare blockers step."""
    board.scripts.add("Runeclaw Bear", *kw("Sneak", "{G}"))
    board.hand("Runeclaw Bear", controller=0)
    ninja = board.play("Grizzly Bears", controller=0)
    give_mana(board, 0, 1)
    attack_with(board, ninja)
    board.game.active_player = PlayerId(1)

    assert not alternative_casts(board)


def test_flashback_on_a_sorcery_keeps_sorcery_timing(board):
    """CR 702.34a with CR 307.1: flashback is a cost, not a timing grant."""
    board.scripts.add("Divination", *kw("Flashback", "{G}"))
    board.graveyard("Divination", controller=0)
    give_mana(board, 0, 1)

    main_phase(board)
    assert alternative_casts(board)
    in_combat(board)
    assert not alternative_casts(board)


# ---------------------------------------------------------------------------
# Power-up (CR 702.193a)
# ---------------------------------------------------------------------------


def _draw_a_card():
    from mtgfish.rules.cr600_spells_and_abilities.effects import Effect
    from mtgfish.rules.kernel.query import YOU, Value

    return Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1), text="draw a card")


def test_power_up_is_an_activated_ability_around_the_printed_body():
    """CR 702.193a: "[Cost]: [Effect]", with the body coming from the card."""
    body = _draw_a_card()
    (ability,) = kw("Power-up", "{5}{G}", effects=(body,))

    assert ability.kind is AbilityKind.ACTIVATED
    assert ability.cost.mana_component == ManaCost.parse("{5}{G}")
    assert ability.effects == (body,)
    assert not any(effect.is_unparsed for effect in ability.effects)


def test_power_up_is_limited_to_one_activation():
    """CR 702.193a: "Activate this ability only once" - not once each turn."""
    (ability,) = kw("Power-up", "{2}", effects=(_draw_a_card(),))
    assert ability.only_once
    assert not ability.once_each_turn
    assert ability.reduced_by_own_mana_cost_on_entry


def _power_up_activations(board, bear) -> list:
    return [
        action
        for action in legal_actions(board.game, PlayerId(0))
        if action.kind is ActionKind.ACTIVATE_ABILITY and action.source == bear.id
    ]


def test_a_power_up_ability_can_be_activated_once_and_then_not_again(board):
    """The limit outlives the turn: a new turn does not give it back."""
    from mtgfish.rules.cr500_turn_structure.cr500_turn import clear_turn_activations
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import activate_ability

    board.scripts.add("Grizzly Bears", *kw("Power-up", "{G}", effects=(_draw_a_card(),)))
    bear = board.play("Grizzly Bears", controller=0)
    give_mana(board, 0, 4)

    (action,) = _power_up_activations(board, bear)
    activate_ability(board.game, PlayerId(0), action)
    assert not _power_up_activations(board, bear)

    board.game.turn += 1
    clear_turn_activations(board.game)
    assert not _power_up_activations(board, bear)


def test_power_up_costs_less_by_the_permanents_mana_cost_the_turn_it_entered(board):
    """CR 702.193a/b: {3}{G}{G} less Grizzly Bears' {1}{G} is {2}{G}."""
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
        activation_mana_cost,
    )

    board.scripts.add(
        "Grizzly Bears", *kw("Power-up", "{3}{G}{G}", effects=(_draw_a_card(),))
    )
    bear = board.play("Grizzly Bears", controller=0)
    board.refresh()
    ability = board.game.characteristics(bear).abilities[0]

    assert activation_mana_cost(board.game, bear, ability) == ManaCost.parse("{2}{G}")


def test_power_up_pays_full_price_on_a_later_turn(board):
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
        activation_mana_cost,
    )

    board.scripts.add(
        "Grizzly Bears", *kw("Power-up", "{3}{G}{G}", effects=(_draw_a_card(),))
    )
    bear = board.play("Grizzly Bears", controller=0)
    board.game.turn += 1
    board.refresh()
    ability = board.game.characteristics(bear).abilities[0]

    assert activation_mana_cost(board.game, bear, ability) == ManaCost.parse("{3}{G}{G}")


def test_power_up_reduction_is_offered_with_only_the_reduced_mana(board):
    """Three mana is not enough for {3}{G}{G}, but it is for the reduced cost."""
    board.scripts.add(
        "Grizzly Bears", *kw("Power-up", "{3}{G}{G}", effects=(_draw_a_card(),))
    )
    bear = board.play("Grizzly Bears", controller=0)
    give_mana(board, 0, 3)
    assert _power_up_activations(board, bear)


def test_colored_reduction_with_nothing_to_reduce_comes_off_generic(board):
    """CR 702.193b: excess colored mana in the mana cost reduces generic."""
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
        activation_mana_cost,
    )

    board.scripts.add("Grizzly Bears", *kw("Power-up", "{4}{R}", effects=(_draw_a_card(),)))
    bear = board.play("Grizzly Bears", controller=0)
    board.refresh()
    ability = board.game.characteristics(bear).abilities[0]

    assert activation_mana_cost(board.game, bear, ability) == ManaCost.parse("{2}{R}")


# ---------------------------------------------------------------------------
# Paradigm (CR 702.192a) - still partial, deliberately
# ---------------------------------------------------------------------------


def test_paradigm_exiles_the_spell():
    """CR 702.192a's second spell ability is buildable today."""
    (ability,) = kw("Paradigm")

    assert ability.kind is AbilityKind.SPELL
    assert any(effect.kind is EffectKind.EXILE for effect in ability.effects)


def test_paradigm_still_reports_the_copy_it_cannot_make():
    """A copy created *in exile* has no opcode, so the gap stays visible."""
    (ability,) = kw("Paradigm")

    assert any(
        node.is_unparsed for effect in ability.effects for node in effect.walk()
    )
    assert lookup("Paradigm").status is Status.PARTIAL


# ---------------------------------------------------------------------------
# Controls: shapes these changes must not have disturbed
# ---------------------------------------------------------------------------


def test_flashback_is_still_a_graveyard_alternative_cost():
    (ability,) = kw("Flashback", "{2}{R}")

    assert ability.alternative_cost.from_zone is Zone.GRAVEYARD
    assert not return_components(ability.alternative_cost.cost)


def test_ninjutsu_still_returns_an_unblocked_attacker_as_an_activation_cost():
    """The same cost component, on an activated ability rather than a cast."""
    (ability,) = kw("Ninjutsu", "{1}{U}")

    assert ability.kind is AbilityKind.ACTIVATED
    (returned,) = return_components(ability.cost)
    assert returned.filter.blocked is False


def test_an_unknown_keyword_is_still_unparsed():
    (ability,) = kw("Zibbleflorp")
    assert ability.unparsed


def test_a_return_cost_the_parser_already_read_is_not_added_twice():
    """The keyword's text may spell the return out; one is still one."""
    from mtgfish.rules.kernel.query import ControllerRelation, ObjectFilter, Value

    printed = Cost(
        (
            CostComponent(CostKind.MANA, mana=ManaCost.parse("{G}")),
            CostComponent(
                CostKind.RETURN_TO_HAND,
                filter=ObjectFilter(
                    types_all=CardType.CREATURE, controller=ControllerRelation.YOU
                ),
                amount=Value.of(1),
                text="return a creature you control",
            ),
        )
    )
    for name in ("Sneak", "Web-slinging"):
        (ability,) = build(KeywordInstance(name, cost=printed))
        assert len(return_components(ability.alternative_cost.cost)) == 1, name


# ---------------------------------------------------------------------------
# CR 702.190b - a sneaked permanent enters tapped and attacking
# ---------------------------------------------------------------------------


@pytest.fixture
def table(card_db):
    """Three players, so "the same player" is distinguishable from "the first
    opponent"."""
    return make_board(card_db, ScriptedAbilities(), players=3)


def _sneak_in(board, defender: int):
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell

    board.scripts.add("Runeclaw Bear", *kw("Sneak", "{G}"))
    board.hand("Runeclaw Bear", controller=0)
    attacker = board.play("Grizzly Bears", controller=0)
    give_mana(board, 0, 1)
    attack_with(board, attacker, defender=defender)
    board.game.phase = Phase.COMBAT
    (action,) = alternative_casts(board)
    spell = cast_spell(board.game, PlayerId(0), action)
    board.resolve_stack()
    return spell


def _on_battlefield(board, name: str):
    game = board.game
    return next(
        game.objects[oid]
        for oid in game.battlefield
        if game.printed_characteristics(game.objects[oid]).name == name
    )


def test_a_sneaked_creature_enters_tapped_and_attacking_the_same_player(table):
    from mtgfish.rules.cr500_turn_structure.cr506_combat import _combat

    _sneak_in(table, defender=2)
    bear = _on_battlefield(table, "Runeclaw Bear")

    assert bear.tapped
    assert _combat(table.game).attacking.get(bear.id) == PlayerId(2)


def test_the_spell_remembers_its_sneak_cost_was_paid(table):
    from mtgfish.rules.kernel.conditions import holds
    from mtgfish.rules.kernel.query import Condition

    _sneak_in(table, defender=1)
    bear = _on_battlefield(table, "Runeclaw Bear")

    sneak = Condition(kind=ConditionKind.ALTERNATIVE_COST_PAID, keyword="Sneak")
    surge = Condition(kind=ConditionKind.ALTERNATIVE_COST_PAID, keyword="Surge")
    assert holds(table.game, sneak, source=bear.id, controller=PlayerId(0))
    assert not holds(table.game, surge, source=bear.id, controller=PlayerId(0))


def test_the_same_creature_cast_normally_does_not_attack(board):
    """Control: the arrival belongs to the sneak cost, not the card."""
    from mtgfish.rules.cr500_turn_structure.cr506_combat import _combat
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
    from mtgfish.rules.kernel.conditions import holds
    from mtgfish.rules.kernel.query import Condition

    main_phase(board)
    board.scripts.add("Runeclaw Bear", *kw("Sneak", "{G}"))
    card = board.hand("Runeclaw Bear", controller=0)
    give_mana(board, 0, 2)
    cast_spell(
        board.game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id)
    )
    board.resolve_stack()
    bear = _on_battlefield(board, "Runeclaw Bear")

    assert not bear.tapped
    assert bear.id not in _combat(board.game).attacking
    any_alternative = Condition(kind=ConditionKind.ALTERNATIVE_COST_PAID)
    assert not holds(board.game, any_alternative, source=bear.id, controller=PlayerId(0))


def test_a_kicked_permanent_remembers_it_was_kicked(board):
    """CR 702.33e: "if it was kicked" on a permanent asks about the spell it
    was, so the record has to survive resolution (CR 608.3)."""
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
    from mtgfish.rules.kernel.conditions import holds
    from mtgfish.rules.kernel.query import Condition

    main_phase(board)
    board.scripts.add("Runeclaw Bear", *kw("Kicker", "{1}"))
    card = board.hand("Runeclaw Bear", controller=0)
    give_mana(board, 0, 3)
    cast_spell(
        board.game,
        PlayerId(0),
        Action(ActionKind.CAST_SPELL, source=card.id, additional_costs=(0,)),
    )
    board.resolve_stack()
    bear = _on_battlefield(board, "Runeclaw Bear")

    kicked = Condition(kind=ConditionKind.WAS_KICKED)
    assert holds(board.game, kicked, source=bear.id, controller=PlayerId(0))
    assert bear.mana_spent == 3
