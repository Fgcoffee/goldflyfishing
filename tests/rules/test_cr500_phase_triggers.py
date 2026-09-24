"""Phase triggers (CR 500.6, 505.1a, 507.1).

"At the beginning of combat" and "at the beginning of your precombat main
phase" were parsed into a PHASE_BEGAN trigger that nothing emitted, so every
such trigger - 369 printed lines among Commander-legal cards - waited for an
event that never came. They also shared one event kind with nothing to say
*which* phase, so emitting it alone would have fired a combat trigger in
every main phase.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.parser.tokens import Stream
from mtgfish.parser.triggers import parse_trigger
from mtgfish.rules.cr500_turn_structure.cr500_turn import take_turn
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import Phase
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.ids import PlayerId


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def probe(text: str) -> Ability:
    """A triggered ability whose trigger is read from oracle text."""
    trigger = parse_trigger(Stream.of(text))
    assert trigger is not None, text
    return Ability.triggered(
        trigger, Effect(EffectKind.NOTHING, text=text), text=text
    )


def resolutions_in_a_turn(board, *texts: str) -> dict[str, int]:
    game = board.game
    board.scripts.add("Grizzly Bears", *(probe(text) for text in texts))
    board.play("Grizzly Bears", controller=0)

    counts = dict.fromkeys(texts, 0)

    def watch(_game, event):
        if event.kind is EventKind.ABILITY_RESOLVED:
            obj = _game.objects.get(event.object_id)
            if obj is not None and obj.ability is not None and obj.ability.text in counts:
                counts[obj.ability.text] += 1

    game.observer = watch
    for player in game.players:
        game.agents[player.id] = FixedAgent()
    game.active_player = PlayerId(0)
    take_turn(game)
    return counts


COMBAT = "At the beginning of combat on your turn, draw a card."
FIRST_MAIN = "At the beginning of your precombat main phase, draw a card."
SECOND_MAIN = "At the beginning of your postcombat main phase, draw a card."
EACH_MAIN = "At the beginning of each main phase, draw a card."


def test_the_parser_says_which_phase():
    assert parse_trigger(Stream.of(COMBAT)).phases == {int(Phase.COMBAT)}
    assert parse_trigger(Stream.of(FIRST_MAIN)).phases == {int(Phase.PRECOMBAT_MAIN)}
    assert parse_trigger(Stream.of(SECOND_MAIN)).phases == {int(Phase.POSTCOMBAT_MAIN)}
    assert parse_trigger(Stream.of(EACH_MAIN)).phases == {
        int(Phase.PRECOMBAT_MAIN),
        int(Phase.POSTCOMBAT_MAIN),
    }


def test_an_intervening_if_keeps_the_phase():
    text = "At the beginning of combat on your turn, if you control a creature, draw a card."
    assert parse_trigger(Stream.of(text)).phases == {int(Phase.COMBAT)}


def test_each_phase_trigger_fires_once_in_its_own_phase(board):
    counts = resolutions_in_a_turn(board, COMBAT, FIRST_MAIN, SECOND_MAIN, EACH_MAIN)

    assert counts == {COMBAT: 1, FIRST_MAIN: 1, SECOND_MAIN: 1, EACH_MAIN: 2}


def test_your_phase_trigger_does_not_fire_on_an_opponents_turn(board):
    game = board.game
    board.scripts.add("Grizzly Bears", probe(COMBAT))
    board.play("Grizzly Bears", controller=1)
    resolved: list[str] = []

    def watch(_game, event):
        if event.kind is EventKind.ABILITY_RESOLVED:
            obj = _game.objects.get(event.object_id)
            if obj is not None and obj.ability is not None:
                resolved.append(obj.ability.text)

    game.observer = watch
    for player in game.players:
        game.agents[player.id] = FixedAgent()
    game.active_player = PlayerId(0)
    take_turn(game)

    assert COMBAT not in resolved
