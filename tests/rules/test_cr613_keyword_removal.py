"""CR 613.1f: removing named abilities in layer 6, and granted protection.

"Loses flying" removes flying and nothing else; "loses all abilities" removes
everything. Protection granted by a static ability protects only from its
quality (CR 702.16).
"""

from __future__ import annotations

from harness import make_board

from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr100_game_concepts.actions import protected_from


def test_colossus_hammer_removes_only_flying(card_db):
    board = make_board(card_db, OracleAbilities())
    angel = board.play("Serra Angel")
    hammer = board.play("Colossus Hammer")
    actions.attach(board.game, hammer, angel)
    board.refresh()
    keywords = {k.lower() for k in board.keywords(angel)}
    assert "flying" not in keywords
    assert "vigilance" in keywords
    assert board.pt(angel) == (14, 14)


def test_sword_protection_is_from_its_colours_only(card_db):
    board = make_board(card_db, OracleAbilities())
    bears = board.play("Grizzly Bears")
    sword = board.play("Sword of Fire and Ice")
    actions.attach(board.game, sword, bears)
    board.refresh()
    bolt = board.hand("Lightning Bolt", controller=1)
    swords = board.hand("Swords to Plowshares", controller=1)
    assert protected_from(board.game, bears, bolt)
    assert not protected_from(board.game, bears, swords)
