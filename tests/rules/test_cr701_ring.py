"""The Ring tempts you (CR 701.54)."""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.rules.cr500_turn_structure.cr506_combat import can_block
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.cr700_additional_rules.cr701_ring import ring_bearer, tempt
from mtgfish.rules.kernel.enums import Supertype, Zone
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.gameobject import ObjectKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import YOU

P0 = PlayerId(0)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    for player in board.game.players:
        board.game.agents[player.id] = FixedAgent()
    return board


def emblems(board, player=P0) -> list:
    game = board.game
    return [
        game.objects[oid]
        for oid in game.command
        if game.objects[oid].kind is ObjectKind.EMBLEM
        and game.objects[oid].owner == player
    ]


def test_the_ring_tempts_you_through_the_effect(board):
    """RING_TEMPTS runs the whole of CR 701.54, not just a counter."""
    bears = board.play("Grizzly Bears")
    execute(
        Resolution(game=board.game, source=bears.id, controller=P0),
        (Effect(EffectKind.RING_TEMPTS, players=YOU),),
    )
    assert board.game.player(P0).ring_tempted_count == 1
    assert ring_bearer(board.game, P0) is bears
    assert len(emblems(board)) == 1


def test_one_emblem_however_often_you_are_tempted(board):
    """CR 701.54c: only if you don't already have The Ring."""
    board.play("Grizzly Bears")
    for _ in range(3):
        tempt(board.game, P0)
    assert len(emblems(board)) == 1
    assert board.game.player(P0).ring_tempted_count == 3


def test_tempted_with_no_creature_still_counts(board):
    """CR 701.54d: the Ring tempts you even if nothing could be chosen."""
    seen = []
    board.game.observer = lambda _g, e: seen.append(e.kind)
    tempt(board.game, P0)
    assert ring_bearer(board.game, P0) is None
    assert EventKind.RING_TEMPTED in seen


def test_your_ring_bearer_is_legendary(board):
    bears = board.play("Grizzly Bears")
    assert not board.chars(bears).is_legendary
    tempt(board.game, P0)
    board.refresh()
    assert board.chars(bears).type_line.supertypes & Supertype.LEGENDARY


def test_only_the_ring_bearer_is_legendary(board):
    board.play("Grizzly Bears")
    other = board.play("Runeclaw Bear")
    board.game.player(P0)  # the greater power is chosen: both 2, lower id
    tempt(board.game, P0)
    board.refresh()
    bearer = ring_bearer(board.game, P0)
    assert other is not bearer
    assert not board.chars(other).is_legendary


def test_a_ring_bearer_cannot_be_blocked_by_greater_power(board):
    bears = board.play("Grizzly Bears")
    tempt(board.game, P0)
    small = board.play("Llanowar Elves", controller=1)
    big = board.play("Hill Giant", controller=1)
    board.refresh()

    assert can_block(board.game, small, bears)
    assert not can_block(board.game, big, bears)


def test_another_creature_is_blockable_as_usual(board):
    """Control: the restriction belongs to the Ring-bearer alone."""
    board.play("Grizzly Bears")
    tempt(board.game, P0)
    other = board.play("Runeclaw Bear")
    big = board.play("Hill Giant", controller=1)
    board.refresh()
    assert can_block(board.game, big, other)


def test_losing_control_ends_the_designation(board):
    """CR 701.54a: until another player gains control of it."""
    bears = board.play("Grizzly Bears")
    tempt(board.game, P0)
    board.game.emit(Event(EventKind.CONTROL_CHANGED, object_id=bears.id, player=PlayerId(1)))
    assert ring_bearer(board.game, P0) is None


def _fire(board, event) -> list[str]:
    """Emit an event and resolve whatever it triggered; return what resolved."""
    game = board.game
    resolved = []

    def watch(_g, e):
        if e.kind is EventKind.ABILITY_RESOLVED:
            obj = _g.objects.get(e.object_id)
            if obj is not None and obj.ability is not None:
                resolved.append(obj.ability.text)

    game.observer = watch
    game.emit(event)
    board.settle()
    board.resolve_stack()
    return resolved


def test_the_loot_needs_two_temptations(board):
    bears = board.play("Grizzly Bears")
    tempt(board.game, P0)
    attacked = Event(EventKind.ATTACKS, object_id=bears.id, player=P0)
    assert not any("draw a card" in t for t in _fire(board, attacked))

    tempt(board.game, P0)
    library = len(board.game.player(P0).library)
    assert any("draw a card" in t for t in _fire(board, attacked))
    assert len(board.game.player(P0).library) == library - 1


def test_the_drain_needs_four_temptations_and_damage_to_a_player(board):
    bears = board.play("Grizzly Bears")
    for _ in range(4):
        tempt(board.game, P0)
    blocker = board.play("Runeclaw Bear", controller=1)
    life = board.game.player(PlayerId(1)).life

    to_creature = Event(
        EventKind.COMBAT_DAMAGE_DEALT, object_id=blocker.id, player=PlayerId(1),
        source=bears.id, source_controller=P0, amount=2,
    )
    _fire(board, to_creature)
    assert board.game.player(PlayerId(1)).life == life

    to_player = Event(
        EventKind.COMBAT_DAMAGE_DEALT, player=PlayerId(1),
        source=bears.id, source_controller=P0, amount=2,
    )
    _fire(board, to_player)
    assert board.game.player(PlayerId(1)).life == life - 3


def test_blockers_of_the_ring_bearer_are_sacrificed_at_end_of_combat(board):
    from mtgfish.rules.cr500_turn_structure.cr506_combat import _combat
    from mtgfish.rules.kernel.enums import Step

    game = board.game
    bears = board.play("Grizzly Bears")
    for _ in range(3):
        tempt(game, P0)
    blocker = board.play("Llanowar Elves", controller=1)
    combat = _combat(game)
    combat.attacking[bears.id] = PlayerId(1)
    combat.blockers[bears.id] = [blocker.id]
    combat.blocking[blocker.id] = [bears.id]
    combat.blockers_declared = True

    _fire(board, Event(EventKind.BECOMES_BLOCKED, object_id=bears.id, amount=1))
    assert blocker.zone is Zone.BATTLEFIELD and blocker.is_live  # not yet

    _fire(board, Event(EventKind.STEP_BEGAN, player=P0, amount=int(Step.END_OF_COMBAT)))
    assert not blocker.is_live


def test_an_emblems_static_ability_applies(board):
    """CR 114.4, which the Ring depends on: an emblem's abilities work from
    the command zone. Checked with an anthem emblem, the common case."""
    from mtgfish.rules.cr100_game_concepts.cr111_tokens import create_emblem
    from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
    from mtgfish.rules.kernel.enums import CardType
    from mtgfish.rules.kernel.query import ControllerRelation, ObjectFilter, Value

    bears = board.play("Grizzly Bears")
    create_emblem(
        board.game,
        P0,
        (
            Ability.static(
                Effect(
                    EffectKind.MODIFY_PT,
                    targets=ObjectFilter(
                        types_all=CardType.CREATURE, controller=ControllerRelation.YOU
                    ),
                    amount=Value.of(1),
                    amount2=Value.of(1),
                    text="creatures you control get +1/+1",
                )
            ),
        ),
    )
    board.refresh()
    assert board.pt(bears) == (3, 3)
