"""The Commander format itself (CR 903).

Everything here is a rule that exists *because* the game is Commander: who may
be cast from the command zone and at what price (CR 903.8), what the
designation on a card means once the game starts (CR 903.3), and the
multiplayer defaults the format is played under (CR 903.2).

The command zone, the 21-damage clock and rule 903.9 are exercised in
``test_commander.py``; setup and the opening hand in ``test_engine_core.py``.
This file deliberately does not repeat them.

CR 903.11 (cards from outside the game) has nothing to test: the engine has no
sideboard and no outside-the-game zone, so there is nowhere for such a card to
come from. CR 903.12 (Brawl) and CR 903.13 (Commander Draft) are other
formats; this engine plays Commander.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.data.decks import parse_decklist
from mtgfish.rules.cr100_game_concepts.cr103_setup import new_game
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import AlternativeCost, Cost
from mtgfish.rules.cr100_game_concepts.player import COMMANDER_STARTING_LIFE
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell, compute_total_cost
from mtgfish.rules.kernel.enums import LETTER_TO_COLOR, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.rules.kernel.query import Value, ValueKind
from mtgfish.rules.kernel.values import evaluate


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities(), players=4)
    board.game.active_player = PlayerId(0)
    board.game.priority_player = PlayerId(0)
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    return board


class Declines:
    """An owner who leaves their commander wherever it landed (CR 903.9)."""

    def move_commander_to_command_zone(self, game, owner, obj, to_zone):
        return False


def commander_of(board, player: int):
    """The object a player's commander currently is."""
    return board.game.objects[board.game.player(PlayerId(player)).commanders[0]]


def give_mana(board, player: int, amount: int, color: str = "W") -> None:
    board.game.player(PlayerId(player)).mana_pool.add(
        ManaKind(LETTER_TO_COLOR[color]), amount
    )


def casts(board, player: int = 0, source=None) -> list:
    out = [
        a
        for a in legal_actions(board.game, PlayerId(player))
        if a.kind is ActionKind.CAST_SPELL
    ]
    if source is not None:
        out = [a for a in out if a.source == source]
    return out


# ---------------------------------------------------------------------------
# CR 903.2 - the multiplayer setup a Commander game is played under
# ---------------------------------------------------------------------------


def test_every_other_player_is_an_opponent(board):
    """CR 903.2: Free-for-All, so each player plays against all the others.

    The limited range of influence option is not used, which is what makes
    this a plain "everyone else" rather than a neighbourhood.
    """
    game = board.game
    for player in game.players:
        assert sorted(game.opponents(player.id)) == [
            p.id for p in game.players if p.id != player.id
        ]


def test_a_commander_game_can_also_be_two_players(card_db):
    """CR 903.2: two players is as legal a Commander game as four."""
    decks = [
        parse_decklist(
            "// Commander\n1 Kenrith, the Returned King\n// Deck\n99 Forest\n",
            card_db,
            name=f"P{i}",
        )
        for i in range(2)
    ]
    game = new_game(decks, seed=3)

    assert len(game.players) == 2
    assert len(game.command) == 2
    # CR 903.7, and the line that separates Commander from Brawl (CR 903.12f):
    # the starting total does not change with the number of players.
    assert all(p.life == COMMANDER_STARTING_LIFE for p in game.players)


def test_seating_is_random_and_seeded(card_db):
    """CR 806.3, reached through CR 903.2: the players are seated at random.

    Seeded, so a replay seats them the same way - a Commander game in which
    the seating changed between runs could not be replayed at all.
    """
    def order(seed: int):
        decks = [
            parse_decklist(
                "// Commander\n1 Kenrith, the Returned King\n// Deck\n99 Forest\n",
                card_db,
                name=f"P{i}",
            )
            for i in range(4)
        ]
        return list(new_game(decks, seed=seed).turn_order)

    assert order(11) == order(11)
    assert sorted(order(11)) == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# CR 903.3 - what the designation is, and what it is not
# ---------------------------------------------------------------------------


def test_the_designation_belongs_to_the_card_not_the_copy(board):
    """CR 903.3: a second object made from the same card is not a commander.

    The designation is an attribute of one physical card. An object that
    merely copies a commander - a token, a Body Double - is not one, so the
    21-damage clock and the command zone never apply to it.
    """
    game = board.game
    original = commander_of(board, 0)
    impostor = game.create_object(original.card, PlayerId(0), Zone.BATTLEFIELD)

    assert original.is_commander
    assert not impostor.is_commander


def test_the_designation_survives_every_zone_it_passes_through(board):
    """CR 903.3: the card keeps it even as it changes zones.

    Each move makes a new object (CR 400.7), so this is a property that has to
    be carried forward deliberately rather than one that simply persists. The
    owner declines rule 903.9 throughout, so the card really does visit every
    zone instead of being sent home from the first one.
    """
    game = board.game
    game.agents[PlayerId(0)] = Declines()
    obj = commander_of(board, 0)
    for zone in (Zone.BATTLEFIELD, Zone.GRAVEYARD, Zone.EXILE, Zone.HAND):
        obj = game.move_object(obj, zone, to_player=PlayerId(0))
        assert obj.zone is zone
        assert obj.is_commander, f"lost the designation in {zone.name}"


def test_a_commander_is_still_a_commander_face_down(board):
    """CR 903.3: turning it face down does not undo the designation."""
    obj = commander_of(board, 0)
    obj.face_down = True
    assert obj.is_commander


def test_a_partner_pair_gives_one_player_two_commanders(card_db):
    """CR 903.3 through CR 702.124: a legal pair is two commanders, not one.

    Both go to the command zone (CR 903.6), both carry the designation, and
    each is taxed on its own (CR 903.8) - the tax counts casts of a commander,
    so casting one of a pair leaves the other at its printed cost.
    """
    decks = [
        parse_decklist(
            "// Commander\n1 Thrasios, Triton Hero\n1 Tymna the Weaver\n"
            "// Deck\n98 Island\n",
            card_db,
            name=f"P{i}",
        )
        for i in range(2)
    ]
    game = new_game(decks, seed=5)
    player = game.player(PlayerId(0))

    assert len(player.commanders) == 2
    assert len(game.command) == 4
    for object_id in player.commanders:
        commander = game.objects[object_id]
        assert commander.is_commander
        assert commander.zone is Zone.COMMAND

    first, second = player.commanders
    player.record_commander_cast(first)
    assert player.commander_tax(first) == 2
    assert player.commander_tax(second) == 0


# ---------------------------------------------------------------------------
# CR 903.4 - colour identity, seen from inside the game
# ---------------------------------------------------------------------------


def test_colour_identity_is_readable_during_the_game(board):
    """CR 903.4: the number of colours in a player's commander's identity.

    Kenrith is a five-colour commander, and abilities that ask this question
    have to be able to get an answer while the game is running.
    """
    value = Value(kind=ValueKind.COMMANDER_COLOUR_IDENTITY)
    assert evaluate(board.game, value, controller=PlayerId(0)) == 5


def test_colour_identity_does_not_change_when_the_commander_does(board):
    """CR 903.4a: it was established before the game and stays established."""
    game = board.game
    value = Value(kind=ValueKind.COMMANDER_COLOUR_IDENTITY)
    before = evaluate(game, value, controller=PlayerId(0))

    game.move_object(commander_of(board, 0), Zone.BATTLEFIELD, to_player=PlayerId(0))

    assert evaluate(game, value, controller=PlayerId(0)) == before


# ---------------------------------------------------------------------------
# CR 903.8 - casting a commander from the command zone
# ---------------------------------------------------------------------------


def test_a_commander_may_be_cast_from_the_command_zone(board):
    """CR 903.8: the permission that makes the format work."""
    give_mana(board, 0, 5)
    assert casts(board, 0, source=commander_of(board, 0).id)


def test_nothing_else_in_the_command_zone_may_be_cast(board):
    """CR 903.8 permits a commander, and only a commander.

    Everything else that lives in the command zone - an emblem (CR 114), a
    card put there by some other effect - stays there. Offering it as a spell
    would let a player cast a card that was never in their deck.
    """
    game = board.game
    intruder = game.create_object(
        board.db.lookup("Grizzly Bears"), PlayerId(0), Zone.COMMAND
    )
    give_mana(board, 0, 5, "G")
    give_mana(board, 0, 5)

    assert not casts(board, 0, source=intruder.id)


def test_an_opponents_commander_is_not_yours_to_cast(board):
    """CR 903.8: a player may cast a commander *they own*."""
    give_mana(board, 0, 10)
    assert not casts(board, 0, source=commander_of(board, 1).id)


def test_casting_it_is_sorcery_speed(board):
    """CR 903.8 grants a zone, not a timing: a creature spell is still one.

    Without this a commander could be flashed in during combat, which is a
    different game entirely.
    """
    game = board.game
    give_mana(board, 0, 5)
    game.active_player = PlayerId(1)

    assert not casts(board, 0, source=commander_of(board, 0).id)


def test_the_tax_is_part_of_the_cost(board):
    """CR 903.8: {2} more for each previous cast from the command zone."""
    game = board.game
    player = game.player(PlayerId(0))
    commander = commander_of(board, 0)
    player.record_commander_cast(game.commander_identity(commander.id))

    total = compute_total_cost(
        game,
        commander,
        PlayerId(0),
        Action(ActionKind.CAST_SPELL, source=commander.id),
        from_command_zone=True,
    )
    assert total.final_mana().mana_value == 7  # {4}{W} plus {2}


def test_the_tax_puts_it_out_of_reach(board):
    """The tax has to reach the legality check, not only the payment.

    A commander offered as castable and then found unpayable mid-cast costs
    the bot its turn.
    """
    game = board.game
    commander = commander_of(board, 0)
    give_mana(board, 0, 5)
    assert casts(board, 0, source=commander.id), "affordable before any tax"

    game.player(PlayerId(0)).record_commander_cast(game.commander_identity(commander.id))
    assert not casts(board, 0, source=commander.id)


def test_the_tax_applies_to_an_alternative_cost_too(board):
    """CR 118.9d: an alternative cost is not a way to dodge the tax."""
    game = board.game
    board.scripts.add(
        "Kenrith, the Returned King",
        Ability(
            AbilityKind.STATIC,
            alternative_cost=AlternativeCost(
                cost=Cost.mana("{W}"), keyword="Whatever", text="pay {W} instead"
            ),
            text="alternative cost",
        ),
    )
    game.invalidate_characteristics()
    commander = commander_of(board, 0)
    give_mana(board, 0, 1)

    offered = [a for a in casts(board, 0, source=commander.id) if a.alternative_cost == 0]
    assert offered, "one white mana pays the alternative cost"

    game.player(PlayerId(0)).record_commander_cast(game.commander_identity(commander.id))
    offered = [a for a in casts(board, 0, source=commander.id) if a.alternative_cost == 0]
    assert not offered, "the {2} tax is on top of the alternative cost"


def test_only_a_cast_from_the_command_zone_raises_the_tax(board):
    """CR 903.8 counts casts from the command zone, not casts of a commander.

    A commander that was in hand - because its owner declined to send it home
    - is cast at its printed cost, and leaves the tax where it was.
    """
    game = board.game
    game.agents[PlayerId(0)] = Declines()
    commander = game.move_object(
        commander_of(board, 0), Zone.HAND, to_player=PlayerId(0)
    )
    identity = game.commander_identity(commander.id)
    give_mana(board, 0, 5)

    cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=commander.id))

    assert game.player(PlayerId(0)).commander_tax(identity) == 0
