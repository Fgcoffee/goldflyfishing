"""Granted triggered abilities are read: the engine runs them now.

The rules session's engine collects layered abilities (CR 613.1f, 603.2) and
gives a granted leaves-the-battlefield trigger the card it became as its
source (CR 400.7e, 603.10a). The auditing session checked a granted quoted
dies trigger, exalted, ward, prowess, and granted persist and undying
returning once; the rules session has board tests for granted persist,
undying and modular. Granted afterlife is checked here on a board.
"""

from __future__ import annotations

import pytest
from harness import make_board

from mtgfish.parser import parse_card
from mtgfish.parser.compile import OracleAbilities


@pytest.mark.parametrize(
    "name",
    [
        "Abnormal Endurance",
        "Sublime Archangel",
        "Mikaeus, the Unhallowed",
        "Undying Evil",
        "Afterlife Insurance",
        "Blaster, Combat DJ // Blaster, Morale Booster",
    ],
)
def test_a_granted_trigger_is_read(card_db, name):
    parsed = parse_card(card_db.lookup(name))
    reasons = {f.reason for face in parsed.faces for f in face.failures}
    assert not any("granted" in r for r in reasons), reasons


def test_a_granted_static_keyword_is_still_read(card_db):
    assert parse_card(card_db.lookup("Levitation")).fully_parsed


def test_granted_afterlife_makes_a_spirit(card_db):
    """Afterlife Insurance: "Creatures you control gain afterlife 1 until end
    of turn." A Bear that then dies leaves a 1/1 flying Spirit."""
    from mtgfish.rules.cr100_game_concepts.actions import destroy
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
    from mtgfish.rules.kernel.enums import Color, Phase, Step
    from mtgfish.rules.kernel.ids import PlayerId

    p0 = PlayerId(0)
    board = make_board(card_db, OracleAbilities())
    game = board.game
    game.active_player = p0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    bears = board.play("Grizzly Bears")
    card = board.hand("Afterlife Insurance")
    game.player(p0).mana_pool.add(ManaKind(Color.WHITE), 2)
    game.player(p0).mana_pool.add(ManaKind(Color.BLACK), 2)
    cast_spell(game, p0, Action(ActionKind.CAST_SPELL, source=card.id))
    board.resolve_stack()
    destroy(game, bears)
    board.settle()
    board.resolve_stack()
    board.settle()
    assert any(name.startswith("Spirit") for name in board.alive(0))
