"""A player may pay life equal to their whole life total (CR 119.4)."""

from __future__ import annotations

from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.kernel.enums import Phase, Step
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions

P0 = PlayerId(0)


def casts(board, card):
    return [
        a
        for a in legal_actions(board.game, P0)
        if a.kind is ActionKind.CAST_SPELL and a.source == card.id
    ]


def setup(card_db, life):
    board = make_board(card_db, ScriptedAbilities())
    board.game.active_player = P0
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    board.game.player(P0).life = life
    return board


def test_phyrexian_mana_may_be_paid_with_the_last_two_life(card_db):
    board = setup(card_db, 2)
    probe = board.hand("Gitaxian Probe")  # {U/P}
    (action,) = casts(board, probe)
    cast_spell(board.game, P0, action)
    assert board.game.player(P0).life == 0


def test_not_with_less_life_than_the_cost(card_db):
    board = setup(card_db, 1)
    assert casts(board, board.hand("Gitaxian Probe")) == []
