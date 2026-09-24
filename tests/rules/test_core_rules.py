"""The core-rules gap closure: 101.2, 111, 114, 603.7, 603.8, 606, 607, 612.

These are the rules that were catalogued but not built. Each one is load-bearing
for cards that any real Commander deck contains.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, keyword, make_board

from mtgfish.rules.cr100_game_concepts.actions import destroy, exiled_with, untap
from mtgfish.rules.cr100_game_concepts.cr111_tokens import create_emblem, create_tokens, token_name
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind
from mtgfish.rules.cr500_turn_structure.restrictions import (
    Act,
    Restriction,
    prohibited,
    register_standing,
)
from mtgfish.rules.cr600_spells_and_abilities.abilities import (
    Ability,
    AbilityKind,
    DelayedTrigger,
    TriggerCondition,
)
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import activate_ability
from mtgfish.rules.cr600_spells_and_abilities.cr603_triggers import check_state_triggers
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind, TokenSpec
from mtgfish.rules.kernel.enums import CardType, Color, Zone
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import (
    Comparison,
    Condition,
    ConditionKind,
    NumericConstraint,
    ObjectFilter,
    PlayerFilter,
    PlayerScope,
    Value,
)

CREATURES = ObjectFilter(types_all=CardType.CREATURE)


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


# ---------------------------------------------------------------------------
# CR 101.2 - "can't" beats "can"
# ---------------------------------------------------------------------------


def test_a_prohibition_stops_untapping(board):
    """Winter Orb's shape: an instruction to untap loses to "can't untap"."""
    bears = board.play("Grizzly Bears", tapped=True)
    register_standing(
        board.game,
        Restriction(act=Act.UNTAP, subject=CREATURES, text="creatures can't untap"),
    )
    assert not untap(board.game, bears)
    assert bears.tapped


def test_without_the_prohibition_untapping_works(board):
    bears = board.play("Grizzly Bears", tapped=True)
    assert untap(board.game, bears)
    assert not bears.tapped


def test_a_prohibition_stops_destruction(board):
    bears = board.play("Grizzly Bears")
    register_standing(
        board.game, Restriction(act=Act.BE_DESTROYED, subject=CREATURES)
    )
    assert not destroy(board.game, bears)
    assert bears.id in board.game.battlefield


def test_a_prohibition_stops_attacking(board):
    from mtgfish.rules.cr500_turn_structure.cr506_combat import can_attack

    bears = board.play("Grizzly Bears")
    assert can_attack(board.game, bears)

    register_standing(board.game, Restriction(act=Act.ATTACK, subject=CREATURES))
    assert not can_attack(board.game, bears)


def test_a_prohibition_stops_blocking(board):
    from mtgfish.rules.cr500_turn_structure.cr506_combat import can_block

    attacker = board.play("Grizzly Bears", controller=0)
    blocker = board.play("Grizzly Bears", controller=1)
    assert can_block(board.game, blocker, attacker)

    register_standing(board.game, Restriction(act=Act.BLOCK, subject=CREATURES))
    assert not can_block(board.game, blocker, attacker)


def test_a_prohibition_can_be_conditional(board):
    """"Creatures can't attack unless..." - the condition gates the "can't"."""
    from mtgfish.rules.cr500_turn_structure.cr506_combat import can_attack

    bears = board.play("Grizzly Bears")
    never = Condition(ConditionKind.NEVER)
    register_standing(
        board.game, Restriction(act=Act.ATTACK, subject=CREATURES, condition=never)
    )
    assert can_attack(board.game, bears), "a false condition means no prohibition"


def test_a_prohibition_names_itself(board):
    """A surprising simulated game needs to be explainable."""
    bears = board.play("Grizzly Bears")
    register_standing(
        board.game,
        Restriction(act=Act.BE_DESTROYED, subject=CREATURES, text="can't be destroyed"),
    )
    found = prohibited(board.game, Act.BE_DESTROYED, obj=bears)
    assert found is not None and "can't be destroyed" in str(found)


# ---------------------------------------------------------------------------
# CR 111 - tokens
# ---------------------------------------------------------------------------


SOLDIER = TokenSpec(
    types=CardType.CREATURE,
    subtypes=("Soldier",),
    colors=Color.WHITE,
    power=Value.of(1),
    toughness=Value.of(1),
)


def test_token_name_defaults_to_subtypes_plus_token(board):
    """CR 111.4."""
    assert token_name(SOLDIER) == "Soldier Token"
    assert token_name(TokenSpec(name="Clue")) == "Clue"


def test_creating_a_token(board):
    created = create_tokens(board.game, SOLDIER, PlayerId(0))
    assert len(created) == 1
    token = created[0]
    chars = board.game.characteristics(token)
    assert chars.name == "Soldier Token"
    assert chars.has_type(CardType.CREATURE)
    assert chars.has_subtype("Soldier")
    assert chars.colors == Color.WHITE
    assert board.pt(token) == (1, 1)
    assert token.is_token


def test_tokens_enter_with_summoning_sickness(board):
    token = create_tokens(board.game, SOLDIER, PlayerId(0))[0]
    assert token.summoning_sick


def test_creating_several_tokens(board):
    assert len(create_tokens(board.game, SOLDIER, PlayerId(0), 5)) == 5


def test_a_token_with_keywords(board):
    spec = TokenSpec(
        types=CardType.CREATURE,
        subtypes=("Angel",),
        power=Value.of(4),
        toughness=Value.of(4),
        keywords=("Flying",),
    )
    token = create_tokens(board.game, spec, PlayerId(0))[0]
    assert "Flying" in board.keywords(token)


def test_a_token_that_cannot_enter_is_never_created(board):
    """CR 111.5: not created and removed - simply not created."""
    register_standing(
        board.game, Restriction(act=Act.ENTER_BATTLEFIELD, subject=CREATURES)
    )
    assert create_tokens(board.game, SOLDIER, PlayerId(0)) == []
    assert not board.game.battlefield


def test_a_token_leaving_still_ceases_to_exist(board):
    token = create_tokens(board.game, SOLDIER, PlayerId(0))[0]
    board.game.move_object(token, Zone.GRAVEYARD)
    board.sba()
    assert token.id not in board.game.objects


# ---------------------------------------------------------------------------
# CR 114 - emblems
# ---------------------------------------------------------------------------


def test_an_emblem_goes_to_the_command_zone(board):
    emblem = create_emblem(board.game, PlayerId(0), (keyword("Flying"),))
    assert emblem.zone is Zone.COMMAND
    assert emblem.id in board.game.command


def test_an_emblem_has_no_characteristics_but_its_abilities(board):
    """CR 114.3: no types beyond Emblem, no mana cost, no color, no name."""
    emblem = create_emblem(board.game, PlayerId(0), (keyword("Flying"),))
    chars = board.game.characteristics(emblem)
    assert chars.name == ""
    assert chars.colors == Color.NONE
    assert not chars.has_mana_cost
    assert chars.abilities


def test_emblem_abilities_function_in_the_command_zone(board):
    """CR 114.4 - otherwise they would do nothing at all."""
    emblem = create_emblem(board.game, PlayerId(0), (keyword("Flying"),))
    for ability in board.game.characteristics(emblem).abilities:
        assert Zone.COMMAND in ability.functions_in


# ---------------------------------------------------------------------------
# CR 603.7 - delayed triggered abilities
# ---------------------------------------------------------------------------


def end_step_trigger() -> TriggerCondition:
    return TriggerCondition(
        event_kinds=frozenset({EventKind.END_STEP}),
        functions_in=frozenset({Zone.BATTLEFIELD, Zone.COMMAND}),
        text="at the beginning of the next end step",
    )


def test_a_delayed_trigger_fires_on_its_event(board):
    game = board.game
    source = board.play("Grizzly Bears")
    game.delayed_triggers.append(
        DelayedTrigger(
            trigger=end_step_trigger(),
            effects=(Effect(EffectKind.DRAW, players=PlayerFilter(PlayerScope.YOU),
                            amount=Value.of(1)),),
            controller=PlayerId(0),
            source=source.id,
        )
    )

    from mtgfish.rules.kernel.events import Event

    game.emit(Event(EventKind.END_STEP, player=PlayerId(0)))
    assert game.pending_triggers


def test_a_delayed_trigger_fires_only_once(board):
    """CR 603.7b: the *next* time its event occurs, then it is gone."""
    game = board.game
    source = board.play("Grizzly Bears")
    game.delayed_triggers.append(
        DelayedTrigger(
            trigger=end_step_trigger(),
            effects=(Effect(EffectKind.NOTHING),),
            controller=PlayerId(0),
            source=source.id,
        )
    )

    from mtgfish.rules.kernel.events import Event

    game.emit(Event(EventKind.END_STEP, player=PlayerId(0)))
    first = len(game.pending_triggers)
    game.pending_triggers.clear()

    game.emit(Event(EventKind.END_STEP, player=PlayerId(0)))
    assert first == 1
    assert not game.pending_triggers
    assert not game.delayed_triggers


def test_a_delayed_trigger_survives_its_source_leaving(board):
    """It belongs to the effect that created it, not to any object."""
    game = board.game
    source = board.play("Grizzly Bears")
    game.delayed_triggers.append(
        DelayedTrigger(
            trigger=end_step_trigger(),
            effects=(Effect(EffectKind.NOTHING),),
            controller=PlayerId(0),
            source=source.id,
        )
    )
    destroy(game, source)
    board.sba()
    game.pending_triggers.clear()

    from mtgfish.rules.kernel.events import Event

    game.emit(Event(EventKind.END_STEP, player=PlayerId(0)))
    assert game.pending_triggers


# ---------------------------------------------------------------------------
# CR 603.8 - state triggers
# ---------------------------------------------------------------------------


def low_life_state_trigger() -> Ability:
    """"When you have 5 or less life, ..." - a condition, not an event."""
    return Ability(
        AbilityKind.TRIGGERED,
        effects=(Effect(EffectKind.NOTHING, text="state trigger fired"),),
        trigger=TriggerCondition(
            event_kinds=frozenset(),
            is_state_trigger=True,
            intervening_if=Condition(
                ConditionKind.LIFE,
                players=PlayerFilter(PlayerScope.YOU),
                constraint=NumericConstraint(Comparison.LE, Value.of(5)),
            ),
            text="when you have 5 or less life",
        ),
        text="state trigger",
    )


def test_a_state_trigger_fires_when_the_condition_becomes_true(board):
    game = board.game
    board.scripts.add("Platinum Angel", low_life_state_trigger())
    board.play("Platinum Angel", controller=0)

    check_state_triggers(game)
    assert not game.pending_triggers, "40 life, so not yet"

    game.player(PlayerId(0)).life = 3
    check_state_triggers(game)
    assert game.pending_triggers


def test_a_state_trigger_does_not_re_fire_while_it_stays_true(board):
    """CR 603.8b, and without it the stack would fill up for ever."""
    game = board.game
    board.scripts.add("Platinum Angel", low_life_state_trigger())
    board.play("Platinum Angel", controller=0)
    game.player(PlayerId(0)).life = 3

    check_state_triggers(game)
    assert len(game.pending_triggers) == 1
    game.pending_triggers.clear()

    check_state_triggers(game)
    assert not game.pending_triggers


def test_a_state_trigger_re_arms_once_the_condition_goes_false(board):
    game = board.game
    board.scripts.add("Platinum Angel", low_life_state_trigger())
    board.play("Platinum Angel", controller=0)

    game.player(PlayerId(0)).life = 3
    check_state_triggers(game)
    game.pending_triggers.clear()

    game.player(PlayerId(0)).life = 20
    check_state_triggers(game)
    assert not game.pending_triggers

    game.player(PlayerId(0)).life = 2
    check_state_triggers(game)
    assert game.pending_triggers


# ---------------------------------------------------------------------------
# CR 606 - loyalty abilities
# ---------------------------------------------------------------------------


def loyalty_ability(change: int) -> Ability:
    return Ability(
        AbilityKind.ACTIVATED,
        effects=(Effect(EffectKind.DRAW, players=PlayerFilter(PlayerScope.YOU),
                        amount=Value.of(1)),),
        cost=Cost((CostComponent(CostKind.LOYALTY, amount=Value.of(change)),)),
        is_loyalty_ability=True,
        text=f"{change:+d}: Draw a card.",
    )


def test_a_plus_loyalty_ability_adds_counters(board):
    game = board.game
    board.scripts.add("Ajani, Caller of the Pride", loyalty_ability(1))
    walker = board.play("Ajani, Caller of the Pride")
    walker.add_counters("loyalty", 4)
    board.refresh()

    activate_ability(
        game, PlayerId(0),
        Action(ActionKind.ACTIVATE_ABILITY, source=walker.id, ability_index=0),
    )
    assert walker.counter_count("loyalty") == 5


def test_a_minus_loyalty_ability_removes_counters(board):
    game = board.game
    board.scripts.add("Ajani, Caller of the Pride", loyalty_ability(-3))
    walker = board.play("Ajani, Caller of the Pride")
    walker.add_counters("loyalty", 4)
    board.refresh()

    activate_ability(
        game, PlayerId(0),
        Action(ActionKind.ACTIVATE_ABILITY, source=walker.id, ability_index=0),
    )
    assert walker.counter_count("loyalty") == 1


def test_a_minus_ability_cannot_be_paid_without_the_counters(board):
    """CR 606.4: the counters *are* the cost, so this is not activatable."""
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import _can_pay_activation

    game = board.game
    board.scripts.add("Ajani, Caller of the Pride", loyalty_ability(-5))
    walker = board.play("Ajani, Caller of the Pride")
    walker.add_counters("loyalty", 2)
    board.refresh()

    ability = game.characteristics(walker).abilities[0]
    assert not _can_pay_activation(game, walker, ability)


def test_loyalty_abilities_are_once_per_turn(board):
    """CR 606.3."""
    from mtgfish.rules.kernel.enums import Phase, Step
    from mtgfish.rules.kernel.legality import legal_actions

    game = board.game
    game.active_player = PlayerId(0)
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    board.scripts.add("Ajani, Caller of the Pride", loyalty_ability(1))
    walker = board.play("Ajani, Caller of the Pride")
    walker.add_counters("loyalty", 4)
    board.refresh()

    assert any(a.kind is ActionKind.ACTIVATE_ABILITY for a in legal_actions(game, PlayerId(0)))
    walker.activations_this_turn[0] = 1
    assert not any(
        a.kind is ActionKind.ACTIVATE_ABILITY for a in legal_actions(game, PlayerId(0))
    )


def _walker_activations(game, walker) -> list:
    from mtgfish.rules.kernel.legality import legal_actions

    return [
        a
        for a in legal_actions(game, PlayerId(0))
        if a.kind is ActionKind.ACTIVATE_ABILITY and a.source == walker.id
    ]


def _main_phase(game) -> None:
    from mtgfish.rules.kernel.enums import Phase, Step

    game.active_player = PlayerId(0)
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN


def test_only_one_loyalty_ability_per_permanent_each_turn(board):
    """CR 606.3: the limit is per permanent, not per ability - a +1 and then a
    -2 from the same planeswalker in one turn is two activations too many."""
    game = board.game
    _main_phase(game)
    board.scripts.add(
        "Ajani, Caller of the Pride", loyalty_ability(1), loyalty_ability(-2)
    )
    walker = board.play("Ajani, Caller of the Pride")
    walker.add_counters("loyalty", 4)
    board.refresh()

    assert len(_walker_activations(game, walker)) == 2
    activate_ability(
        game, PlayerId(0),
        Action(ActionKind.ACTIVATE_ABILITY, source=walker.id, ability_index=0),
    )
    board.resolve_stack()
    assert not _walker_activations(game, walker)
    with pytest.raises(Exception):
        activate_ability(
            game, PlayerId(0),
            Action(ActionKind.ACTIVATE_ABILITY, source=walker.id, ability_index=1),
        )


def test_loyalty_abilities_come_back_the_next_turn(board):
    """CR 606.3 says each turn: the count must reset as a new turn begins."""
    from mtgfish.rules.cr500_turn_structure.cr500_turn import clear_turn_activations

    game = board.game
    _main_phase(game)
    board.scripts.add("Ajani, Caller of the Pride", loyalty_ability(1))
    walker = board.play("Ajani, Caller of the Pride")
    walker.add_counters("loyalty", 4)
    board.refresh()

    activate_ability(
        game, PlayerId(0),
        Action(ActionKind.ACTIVATE_ABILITY, source=walker.id, ability_index=0),
    )
    board.resolve_stack()
    assert not _walker_activations(game, walker)

    game.turn += 1
    clear_turn_activations(game)
    assert _walker_activations(game, walker)


def test_a_new_turn_resets_activation_counts_through_take_turn(board):
    """The reset lives in the turn itself, not only in the helper."""
    import inspect

    from mtgfish.rules.cr500_turn_structure import cr500_turn

    assert "clear_turn_activations(game)" in inspect.getsource(cr500_turn.take_turn)


# ---------------------------------------------------------------------------
# CR 607 - linked abilities
# ---------------------------------------------------------------------------


def test_exiling_records_what_did_the_exiling(board):
    """CR 607.2: "the exiled cards" has to be findable later."""
    from mtgfish.rules.cr100_game_concepts.actions import exile

    game = board.game
    source = board.play("Grizzly Bears", controller=0)
    victim = board.play("Grizzly Bears", controller=1)

    exile(game, victim, source=source.id)
    linked = exiled_with(game, source.id)
    assert len(linked) == 1
    assert linked[0].zone is Zone.EXILE


def test_unrelated_exiles_are_not_linked(board):
    from mtgfish.rules.cr100_game_concepts.actions import exile

    game = board.game
    source = board.play("Grizzly Bears", controller=0)
    other = board.play("Grizzly Bears", controller=1)
    exile(game, other)  # No source.
    assert exiled_with(game, source.id) == []


def test_a_card_that_leaves_exile_is_no_longer_linked(board):
    """CR 607.2's reference is to cards still in exile."""
    from mtgfish.rules.cr100_game_concepts.actions import exile

    game = board.game
    source = board.play("Grizzly Bears", controller=0)
    victim = board.play("Grizzly Bears", controller=1)
    exiled = exile(game, victim, source=source.id)

    game.move_object(exiled, Zone.HAND, to_player=PlayerId(1))
    assert exiled_with(game, source.id) == []


# ---------------------------------------------------------------------------
# CR 612 - text-changing effects
# ---------------------------------------------------------------------------


def text_change(from_word: str, to_word: str) -> Ability:
    return Ability.static(
        Effect(
            EffectKind.TEXT_CHANGE,
            targets=ObjectFilter(types_all=CardType.LAND),
            keywords=(from_word, to_word),
        ),
        text=f"{from_word} becomes {to_word}",
    )


def test_a_text_change_swaps_a_land_type(board):
    """CR 612, layer 3 - and the new type brings its mana ability with it."""
    forest = board.play("Forest")
    assert board.taps_for(forest) == {"G"}

    board.scripts.add("Sea's Claim", text_change("Forest", "Island"))
    board.play("Sea's Claim")

    chars = board.game.characteristics(forest)
    assert "Island" in chars.subtypes
    assert "Forest" not in chars.subtypes
    assert board.taps_for(forest) == {"U"}


def test_a_text_change_leaves_other_types_alone(board):
    """CR 612.2: only words used in the right way change."""
    bayou = board.play("Bayou")  # Forest Swamp
    board.scripts.add("Sea's Claim", text_change("Forest", "Island"))
    board.play("Sea's Claim")

    chars = board.game.characteristics(bayou)
    assert "Island" in chars.subtypes
    assert "Swamp" in chars.subtypes
    assert board.taps_for(bayou) == {"U", "B"}


def test_text_change_applies_before_type_setting(board):
    """Layer 3 runs before layer 4, so Blood Moon still wins."""
    from test_layers import blood_moon

    bayou = board.play("Bayou")
    board.scripts.add("Sea's Claim", text_change("Forest", "Island"))
    board.scripts.add("Blood Moon", blood_moon())
    board.play("Sea's Claim")
    board.play("Blood Moon")

    assert board.game.characteristics(bayou).subtypes == ("Mountain",)
