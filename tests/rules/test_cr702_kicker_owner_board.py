"""A card's own kicker, on a board (auditor's F25, the other half).

Ertai's Trickery ("Counter target spell if it was kicked.") is refused: its
"it" is the target spell, which the payment condition cannot ask about. A
card with kicker of its own still reads "if it was kicked" as its own
payment, in play.
"""

from __future__ import annotations



def _cast_bushwhacker(card_db, kicked: bool):
    from harness import make_board

    from mtgfish.parser.compile import OracleAbilities
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
    card = board.hand("Goblin Bushwhacker")
    game.player(p0).mana_pool.add(ManaKind(Color.RED), 2)
    costs = board.chars(card).additional_costs
    additional = (0,) if kicked and costs else ()
    cast_spell(game, p0, Action(ActionKind.CAST_SPELL, source=card.id, additional_costs=additional))
    board.resolve_stack()
    board.settle()
    board.resolve_stack()
    return board, bears


def test_on_the_board_a_kicked_bushwhacker_pumps(card_db):
    """The card's own kicker is still read in play: kicked, Goblin
    Bushwhacker gives creatures you control +1/+0 and haste."""
    board, bears = _cast_bushwhacker(card_db, kicked=True)
    assert board.pt(bears) == (3, 2)
    assert "haste" in {k.lower() for k in board.keywords(bears)}


def test_on_the_board_an_unkicked_bushwhacker_does_not(card_db):
    board, bears = _cast_bushwhacker(card_db, kicked=False)
    assert board.pt(bears) == (2, 2)
