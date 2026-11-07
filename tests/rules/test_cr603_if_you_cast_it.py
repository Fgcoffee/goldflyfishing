"""CR 603.4: "When this enters, if you cast it, ..." is checked as the
permanent enters, so a cast creature triggers and a reanimated or copied one
does not (CR 707.10: a copy of a spell was not cast)."""

from __future__ import annotations

from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, TriggerCondition
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.cr600_spells_and_abilities.cr608_stack import resolve_top
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr700_additional_rules.cr707_faces import copy_spell
from mtgfish.rules.kernel.enums import Color, Phase, Step, Zone
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import YOU, Condition, ConditionKind, ObjectFilter, Value

P0 = PlayerId(0)

IF_YOU_CAST_IT = Ability.triggered(
    TriggerCondition(
        event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
        subject=ObjectFilter(source_only=True),
        intervening_if=Condition(ConditionKind.WAS_CAST),
        text="when this creature enters, if you cast it",
    ),
    Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1)),
    text="When this creature enters, if you cast it, draw a card.",
)


def board_with_the_trigger(card_db):
    board = make_board(card_db, ScriptedAbilities({"Grizzly Bears": (IF_YOU_CAST_IT,)}))
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    game.pending_triggers.clear()
    return board


def mine(game):
    return [t for t in game.pending_triggers if game.objects[t.source].card.name == "Grizzly Bears"]


def test_a_cast_creature_triggers(card_db):
    board = board_with_the_trigger(card_db)
    game = board.game
    game.player(P0).mana_pool.add(ManaKind(Color.GREEN), 2)
    cast_spell(game, P0, Action(ActionKind.CAST_SPELL, source=board.hand("Grizzly Bears").id))
    resolve_top(game)
    assert len(mine(game)) == 1


def test_a_creature_put_onto_the_battlefield_does_not(card_db):
    board = board_with_the_trigger(card_db)
    game = board.game
    game.move_object(board.graveyard("Grizzly Bears"), Zone.BATTLEFIELD)
    assert not mine(game)


def test_a_copy_of_the_spell_was_not_cast(card_db):
    board = board_with_the_trigger(card_db)
    game = board.game
    game.player(P0).mana_pool.add(ManaKind(Color.GREEN), 2)
    spell = cast_spell(
        game, P0, Action(ActionKind.CAST_SPELL, source=board.hand("Grizzly Bears").id)
    )
    copy_spell(game, spell, controller=P0)
    resolve_top(game)  # the copy
    assert not mine(game)
    resolve_top(game)  # the original
    assert len(mine(game)) == 1
