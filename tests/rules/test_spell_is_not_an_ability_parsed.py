""""Spell" is a spell, not an ability on the stack (auditor's F31).

CR 112.1 and 113.7: Counterspell's "target spell" read as "an object on the
stack", so it could counter a triggered ability. Deflecting Swat's "target spell
or ability" still reaches spells.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, make_board

from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
    _candidates_for,
    cast_spell,
    targeting_effects,
)
from mtgfish.rules.kernel.enums import Color, Phase, Step
from mtgfish.rules.kernel.gameobject import ObjectKind
from mtgfish.rules.kernel.ids import PlayerId

P0, P1 = PlayerId(0), PlayerId(1)


@pytest.fixture
def stack(card_db):
    """A triggered ability (Doomed Traveler's dies trigger) under a Lightning
    Bolt on the stack."""
    from mtgfish.rules.cr100_game_concepts.actions import destroy

    board = make_board(card_db, OracleAbilities())
    for player in board.game.players:
        board.game.agents[player.id] = FixedAgent()
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    destroy(game, board.play("Doomed Traveler", controller=1))
    board.settle()
    victim = board.play("Llanowar Elves")
    bolt = board.hand("Lightning Bolt", controller=1)
    game.player(P1).mana_pool.add(ManaKind(Color.RED), 1)
    cast_spell(game, P1, Action(ActionKind.CAST_SPELL, source=bolt.id, targets=((victim.id,),)))
    kinds = {game.objects[i].kind for i in game.stack}
    assert ObjectKind.ABILITY in kinds and len(game.stack) == 2, "the setup"
    return board


def candidates(board, name) -> set:
    game = board.game
    card = board.hand(name)
    out = set()
    for effect in targeting_effects(game, card):
        out |= {
            "ability" if game.objects[i].kind is ObjectKind.ABILITY else "spell"
            for i in _candidates_for(game, card, effect, P0)
            if i in game.stack
        }
    return out


def test_counterspell_targets_the_spell_only(stack):
    assert candidates(stack, "Counterspell") == {"spell"}


def test_deflecting_swat_targets_a_spell_or_an_ability(stack):
    """"Change the target of target spell or ability with a single target":
    both are candidates (the Bolt has one target; whether Doomed Traveler's
    trigger qualifies is the 'single target' question, not this one)."""
    assert "spell" in candidates(stack, "Deflecting Swat")
