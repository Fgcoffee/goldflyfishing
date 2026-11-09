""""Create a token that's a copy of target permanent you control" makes a
token (auditor's F58).

It was read as COPY_PERMANENT, the "becomes a copy" effect: the resolving
spell became the copy and no token was made (Replication Technique, Cackling
Counterpart). And a copy's ", except ..." tail was consumed and dropped - an
"except it's a 1/1" or "except it isn't legendary" played a different card
while the ability counted as read. Such a tail is now left unread.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, make_board

from mtgfish.parser import parse_card
from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.kernel.enums import Color, Phase, Step
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


class _Decline(FixedAgent):
    def choose_optional(self, game, player, effect):
        return False


@pytest.mark.parametrize(
    ("name", "color", "mana"),
    [("Replication Technique", Color.BLUE, 5), ("Cackling Counterpart", Color.BLUE, 3)],
)
def test_a_token_copy_is_made(card_db, name, color, mana):
    board = make_board(card_db, OracleAbilities())
    for player in board.game.players:
        board.game.agents[player.id] = _Decline()
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    bears = board.play("Grizzly Bears")
    card = board.hand(name)
    game.player(P0).mana_pool.add(ManaKind(color), mana)
    cast_spell(game, P0, Action(ActionKind.CAST_SPELL, source=card.id, targets=((bears.id,),)))
    board.resolve_stack()
    board.settle()
    board.resolve_stack()
    assert board.alive(0).count("Grizzly Bears") == 2


def test_an_except_tail_is_not_dropped(card_db):
    # "... a token that's a copy of target creature you control, except it
    # has haste" - not representable yet, so not read.
    assert not parse_card(card_db.lookup("Kiki-Jiki, Mirror Breaker")).fully_parsed


def test_soul_guide_lantern_exiles_only_opponents_graveyards(card_db):
    """"Exile each opponent's graveyard" exiled yours too: the owner was not
    on the filter, and only a *targeted* player scopes the zone."""
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import activate_ability

    board = make_board(card_db, OracleAbilities())
    for player in board.game.players:
        board.game.agents[player.id] = FixedAgent()
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    lantern = board.play("Soul-Guide Lantern")
    board.graveyard("Shock", 0)
    board.graveyard("Shock", 1)
    abilities = game.characteristics(lantern).abilities
    index = next(i for i, a in enumerate(abilities) if "each opponent" in a.text)
    activate_ability(
        game, P0, Action(ActionKind.ACTIVATE_ABILITY, source=lantern.id, ability_index=index)
    )
    board.resolve_stack()
    assert "Shock" in board.in_graveyard(0)
    assert board.in_graveyard(1) == []
