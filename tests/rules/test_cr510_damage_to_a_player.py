"""'Deals combat damage to a player' triggers (CR 510.2, 603.2).

The trigger names its recipient. Damage dealt to a blocking creature is combat
damage too, and a trigger that watched combat damage without asking who took
it fired for every blocked Ninja and saboteur.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.parser.tokens import Stream
from mtgfish.parser.triggers import parse_trigger
from mtgfish.rules.cr600_spells_and_abilities.cr603_triggers import condition_met
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.ids import NO_OBJECT, NO_PLAYER, PlayerId


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


TEXTS = (
    "Whenever this creature deals combat damage to a player, draw a card.",
    "Whenever a creature you control deals combat damage to a player, draw a card.",
)


def damage(source, *, to_player=None, to_object=None) -> Event:
    return Event(
        EventKind.COMBAT_DAMAGE_DEALT,
        object_id=to_object.id if to_object is not None else NO_OBJECT,
        player=to_player if to_player is not None else (
            to_object.controller if to_object is not None else NO_PLAYER
        ),
        source=source.id,
        source_controller=source.controller,
        amount=2,
    )


@pytest.mark.parametrize("text", TEXTS)
def test_fires_on_combat_damage_to_a_player(board, text):
    trigger = parse_trigger(Stream.of(text))
    attacker = board.play("Grizzly Bears", controller=0)
    assert condition_met(board.game, attacker, trigger, damage(attacker, to_player=PlayerId(1)))


@pytest.mark.parametrize("text", TEXTS)
def test_does_not_fire_on_combat_damage_to_a_creature(board, text):
    trigger = parse_trigger(Stream.of(text))
    attacker = board.play("Grizzly Bears", controller=0)
    blocker = board.play("Runeclaw Bear", controller=1)
    assert not condition_met(board.game, attacker, trigger, damage(attacker, to_object=blocker))


def noncombat(event: Event) -> Event:
    return event.replaced(kind=EventKind.DAMAGE_DEALT)


def test_deals_damage_includes_combat_damage(board):
    """CR 120.1: combat damage is damage. A trigger that watched only the
    noncombat event never fired in combat."""
    trigger = parse_trigger(Stream.of("Whenever this creature deals damage, you gain 1 life."))
    attacker = board.play("Grizzly Bears", controller=0)
    event = damage(attacker, to_player=PlayerId(1))

    assert condition_met(board.game, attacker, trigger, event)
    assert condition_met(board.game, attacker, trigger, noncombat(event))


def test_deals_damage_to_an_opponent_names_the_recipient(board):
    trigger = parse_trigger(
        Stream.of("Whenever this creature deals damage to an opponent, draw a card.")
    )
    attacker = board.play("Grizzly Bears", controller=0)
    blocker = board.play("Runeclaw Bear", controller=1)

    assert condition_met(board.game, attacker, trigger, damage(attacker, to_player=PlayerId(1)))
    assert not condition_met(board.game, attacker, trigger, damage(attacker, to_object=blocker))
    assert not condition_met(board.game, attacker, trigger, damage(attacker, to_player=PlayerId(0)))


def test_deals_damage_to_a_creature_names_the_recipient(board):
    trigger = parse_trigger(
        Stream.of("Whenever this creature deals damage to a creature, draw a card.")
    )
    attacker = board.play("Grizzly Bears", controller=0)
    blocker = board.play("Runeclaw Bear", controller=1)

    assert condition_met(board.game, attacker, trigger, damage(attacker, to_object=blocker))
    assert not condition_met(board.game, attacker, trigger, damage(attacker, to_player=PlayerId(1)))


def test_is_dealt_damage_includes_combat_damage(board):
    trigger = parse_trigger(Stream.of("Whenever this creature is dealt damage, draw a card."))
    attacker = board.play("Grizzly Bears", controller=0)
    blocker = board.play("Runeclaw Bear", controller=1)

    assert condition_met(board.game, blocker, trigger, damage(attacker, to_object=blocker))
