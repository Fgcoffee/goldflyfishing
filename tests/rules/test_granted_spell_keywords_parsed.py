"""Keywords granted to "spells you cast", read from the real cards, on a board.

The engine reads a grant to spells while the spell's costs are chosen (CR
601.2a-b, rules session d4f6a3e). Before that, every such grant was ignored
while the card counted as read (auditor's F26): Wort never offered conspire,
Chief Engineer never convoked. Conspire and casualty, granted, now work and are
checked on the board here, with no grant as the control; the keywords the
engine still ignores while casting are refused.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, make_board

from mtgfish.parser import parse_card
from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind
from mtgfish.rules.kernel.enums import Color, Phase, Step
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions

P0 = PlayerId(0)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, OracleAbilities())
    for player in board.game.players:
        board.game.agents[player.id] = FixedAgent()
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    return board


def give(board, amount, color):
    board.game.player(P0).mana_pool.add(ManaKind(color), amount)


def casts(board, card):
    return [
        a for a in legal_actions(board.game, P0)
        if a.kind is ActionKind.CAST_SPELL and a.source == card.id
    ]


@pytest.mark.parametrize("granter", ["Wort, the Raidmother", None])
def test_granted_conspire(board, granter):
    if granter:
        board.play(granter)
    board.play("Goblin Piker")
    board.play("Goblin Piker")
    bolt = board.hand("Lightning Bolt")
    give(board, 1, Color.RED)
    offered = sorted(a.additional_costs for a in casts(board, bolt))
    assert offered == ([(), (0,)] if granter else [()])


@pytest.mark.parametrize("granter", ["Silverquill, the Disputant", None])
def test_granted_casualty(board, granter):
    if granter:
        board.play(granter)
    board.play("Grizzly Bears")
    bolt = board.hand("Lightning Bolt")
    give(board, 1, Color.RED)
    offered = sorted(a.additional_costs for a in casts(board, bolt))
    assert offered == ([(), (0,)] if granter else [()])


@pytest.mark.parametrize(
    "name",
    [
        "Chief Engineer",  # convoke
        "Eirdu, Carrier of Dawn // Isilu, Carrier of Twilight",  # convoke
        "Inspiring Statuary",  # improvise
        "Teval, Arbiter of Virtue",  # delve
        "The First Sliver",  # cascade
        "Hunting Velociraptor",  # prowl
    ],
)
def test_grants_the_engine_ignores_while_casting_are_refused(card_db, name):
    """On d4f6a3e, Mind Stone cannot be convoked with Chief Engineer out,
    Grizzly Bears cannot be improvised with Inspiring Statuary, a Hill Giant
    cannot delve with Teval, and a Sliver Hivelord does not cascade with The
    First Sliver. Until it can, the grant is unread rather than a card that
    looks right and does nothing."""
    face = parse_card(card_db.lookup(name)).faces[0]
    grants = [a for a in face.abilities if "spells you cast ha" in a.text.lower()]
    assert grants and all(a.unparsed for a in grants)
    assert any("granted to spells" in f.reason for f in face.failures)


@pytest.mark.parametrize("granter", ["Deeproot Historian", None])
def test_granted_retrace_casts_from_the_graveyard_for_a_land(board, granter):
    """CR 702.81a. Granted retrace used to be shrunk to the bare name and was
    never offered; built in full it carries its alternative cost."""
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
    from mtgfish.rules.kernel.enums import Zone

    game = board.game
    if granter:
        board.play(granter)
    _empty_hand(board)
    looter = board.hand("Merfolk Looter")
    game.move_object(looter, Zone.GRAVEYARD, to_player=P0)
    board.hand("Forest")
    give(board, 2, Color.BLUE)
    offered = [
        a for a in legal_actions(game, P0)
        if a.kind is ActionKind.CAST_SPELL
        and game.printed_characteristics(game.objects[a.source]).name == "Merfolk Looter"
    ]
    if not granter:
        assert offered == []
        return
    (action,) = offered
    cast_spell(game, P0, action)
    board.resolve_stack()
    assert "Merfolk Looter" in board.alive(0)
    assert "Forest" in board.in_graveyard(0)
    assert game.player(P0).hand == [] and game.player(P0).mana_pool.total == 0


def _graveyard_casts(board, name):
    game = board.game
    return [
        a for a in legal_actions(game, P0)
        if a.kind is ActionKind.CAST_SPELL
        and game.printed_characteristics(game.objects[a.source]).name == name
    ]


def _empty_hand(board):
    """The board deals an opening hand of Forests; a test about which card a
    cost may discard starts from nothing."""
    from mtgfish.rules.kernel.enums import Zone

    game = board.game
    for i in list(game.player(P0).hand):
        game.move_object(game.objects[i], Zone.LIBRARY, to_player=P0)


def test_printed_retrace_pays_the_mana_cost_and_discards_a_land(board):
    """CR 702.81a, from Raven's Crime ({B}, retrace): built as a bare
    graveyard alternative cost, it was cast for nothing."""
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
    from mtgfish.rules.kernel.enums import Zone

    game = board.game
    _empty_hand(board)
    game.move_object(board.hand("Raven's Crime"), Zone.GRAVEYARD, to_player=P0)
    assert _graveyard_casts(board, "Raven's Crime") == [], "no mana, no land"
    board.hand("Forest")
    assert _graveyard_casts(board, "Raven's Crime") == [], "a land but no mana"
    give(board, 1, Color.BLACK)
    (action,) = _graveyard_casts(board, "Raven's Crime")
    cast_spell(game, P0, action)
    assert game.player(P0).mana_pool.total == 0
    assert "Forest" in board.in_graveyard(0)


def test_retrace_will_not_discard_a_nonland_card(board):
    from mtgfish.rules.kernel.enums import Zone

    _empty_hand(board)
    board.game.move_object(board.hand("Raven's Crime"), Zone.GRAVEYARD, to_player=P0)
    board.hand("Grizzly Bears")
    give(board, 1, Color.BLACK)
    assert _graveyard_casts(board, "Raven's Crime") == []
