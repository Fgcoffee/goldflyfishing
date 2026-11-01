""""Target creature can't block this turn" restricts the chosen creature only.

Read without its target (auditor's F43), the restriction's filter matched
every creature: Chainwhip Cyclops stopped all creatures from blocking, and
Sly Instigator made every creature an opponent controls unblockable.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, make_board

from mtgfish.parser import parse_card
from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr500_turn_structure.cr506_combat import can_block
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import activate_ability
from mtgfish.rules.kernel.enums import Color, Phase, Step
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


def test_the_restriction_is_targeted(card_db):
    for name in ("Chainwhip Cyclops", "Phase Dolphin", "Sly Instigator"):
        (node,) = [
            n
            for a in parse_card(card_db.lookup(name)).faces[0].abilities
            for e in a.effects
            for n in e.walk()
            if n.kind.name == "RESTRICTION"
        ]
        assert node.is_targeted, name


@pytest.mark.xfail(strict=True, reason=(
    "ENGINE: resolve._register_restriction registers the restriction against "
    "the effect's filter, not the target chosen for it - every creature is "
    "restricted. The parser now marks it targeted (F43)."))
def test_chainwhip_cyclops_stops_only_its_target_blocking(card_db):
    board = make_board(card_db, OracleAbilities())
    for player in board.game.players:
        board.game.agents[player.id] = FixedAgent()
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    cyclops = board.play("Chainwhip Cyclops")
    bears = board.play("Grizzly Bears", controller=1)
    giant = board.play("Hill Giant", controller=1)
    game.player(P0).mana_pool.add(ManaKind(Color.RED), 4)
    abilities = game.characteristics(cyclops).abilities
    index = next(i for i, a in enumerate(abilities) if "block" in a.text)
    activate_ability(
        game,
        P0,
        Action(ActionKind.ACTIVATE_ABILITY, source=cyclops.id, ability_index=index, targets=((bears.id,),)),
    )
    board.resolve_stack()
    assert not can_block(game, bears, cyclops)
    assert can_block(game, giant, cyclops)
