"""Companion and cards outside the game (CR 702.139, 103.2b, 116.2g, 400.11,
108.3, 903.11a)."""

from __future__ import annotations

import pytest
from harness import Board, ScriptedAbilities, keyword

from mtgfish.data.decks import parse_decklist
from mtgfish.rules.cr100_game_concepts.cr103_setup import new_game
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr116_special_actions import (
    SpecialKind,
    available,
    perform,
)
from mtgfish.rules.cr400_zones.cr400_outside_game import (
    bring_into_game,
    reveal_companion,
)
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import (
    KeywordInstance,
    build,
)
from mtgfish.rules.cr700_additional_rules.keywords import Status, lookup
from mtgfish.rules.kernel.enums import Color, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions

P0 = PlayerId(0)
P1 = PlayerId(1)

LURRUS = "Lurrus of the Dream-Den"
COMPANION_ABILITIES = build(KeywordInstance("Companion"))

DECK = "// Commander\n1 Kenrith, the Returned King\n// Deck\n99 Forest\n"


def companion_deck(card_db, companion: str = LURRUS, extra: str = ""):
    return parse_decklist(DECK + extra + f"// Companion\n1 {companion}\n", card_db, name="P")


def make_companion_board(card_db, *, scripted: bool = True, companion: str = LURRUS) -> Board:
    scripts = ScriptedAbilities({companion: COMPANION_ABILITIES} if scripted else {})
    decks = [
        companion_deck(card_db, companion),
        parse_decklist(DECK, card_db, name="Q"),
    ]
    game = new_game(decks, seed=1, ability_provider=scripts, randomize_turn_order=False)
    game.turn = 1
    game.active_player = P0
    game.priority_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    return Board(game=game, db=card_db, scripts=scripts)


def give_mana(board: Board, player: PlayerId = P0, amount: int = 3) -> None:
    board.game.player(player).mana_pool.add(ManaKind(Color.WHITE), amount)


def companion_actions(board: Board, player: PlayerId = P0):
    return [a for a in available(board.game, player) if a.kind is SpecialKind.COMPANION]


def objects_named(board: Board, name: str):
    return [
        obj
        for obj in board.game.objects.values()
        if obj.is_live and getattr(obj.card, "name", "") == name
    ]


# ---------------------------------------------------------------------------
# The decklist: a companion is declared, and is not part of the deck
# ---------------------------------------------------------------------------


def test_a_companion_section_names_the_companion_outside_the_deck(card_db):
    deck = companion_deck(card_db)
    assert deck.companion is not None and deck.companion.name == LURRUS
    assert LURRUS not in {entry.card.name for entry in deck.entries}
    assert deck.total_cards == 100
    codes = {issue.code for issue in deck.issues}
    # The condition is card text the engine cannot check, and it says so.
    assert "companion-condition-unchecked" in codes
    assert not deck.has_errors


def test_the_companion_survives_a_round_trip_through_text(card_db):
    deck = companion_deck(card_db)
    again = parse_decklist(deck.as_decklist(), card_db)
    assert again.companion == deck.companion
    assert [c.name for c in again.commanders] == ["Kenrith, the Returned King"]


def test_a_card_without_companion_cannot_be_the_companion(card_db):
    deck = companion_deck(card_db, "Grizzly Bears")
    assert "not-a-companion" in {issue.code for issue in deck.issues}


def test_a_companion_outside_the_commanders_identity_is_flagged(card_db):
    deck = parse_decklist(
        "// Commander\n1 Omnath, Locus of Mana\n// Deck\n99 Forest\n"
        f"// Companion\n1 {LURRUS}\n",
        card_db,
    )
    assert "companion-color-identity" in {issue.code for issue in deck.issues}
    # Control: inside Kenrith's five colours the same companion is fine.
    assert "companion-color-identity" not in {i.code for i in companion_deck(card_db).issues}


def test_a_companion_sharing_a_name_with_the_deck_is_flagged(card_db):
    deck = parse_decklist(
        "// Commander\n1 Kenrith, the Returned King\n// Deck\n98 Forest\n"
        f"1 {LURRUS}\n// Companion\n1 {LURRUS}\n",
        card_db,
    )
    assert "companion-name" in {issue.code for issue in deck.issues}


def test_only_one_companion_may_be_revealed(card_db):
    deck = parse_decklist(
        DECK + f"// Companion\n1 {LURRUS}\n1 Jegantha, the Wellspring\n", card_db
    )
    assert "too-many-companions" in {issue.code for issue in deck.issues}
    assert deck.companion.name == LURRUS


def test_a_sideboard_is_kept_outside_the_deck(card_db):
    deck = parse_decklist(DECK + "// Sideboard\n1 Sol Ring\nSB: 1 Arcane Signet\n", card_db)
    assert sorted(card.name for card in deck.sideboard) == ["Arcane Signet", "Sol Ring"]
    assert deck.total_cards == 100
    assert "Sol Ring" not in {entry.card.name for entry in deck.entries}


# ---------------------------------------------------------------------------
# Setup: revealed, and still outside the game (CR 103.2b, 400.11)
# ---------------------------------------------------------------------------


def test_the_companion_starts_outside_the_game(card_db):
    board = make_companion_board(card_db)
    player = board.game.player(P0)
    assert player.companion is not None and player.companion.name == LURRUS
    assert player.companion in player.outside_game
    # CR 400.11c: no object, so nothing in the game can touch it.
    assert objects_named(board, LURRUS) == []
    # The other player revealed nothing.
    assert board.game.player(P1).companion is None


def test_a_sideboard_card_is_outside_the_game_and_never_comes_in(card_db):
    decks = [parse_decklist(DECK + "// Sideboard\n1 Sol Ring\n", card_db, name="P")] * 2
    game = new_game(decks, seed=1, randomize_turn_order=False)
    assert [card.name for card in game.player(P0).outside_game] == ["Sol Ring"]
    assert not any(
        getattr(obj.card, "name", "") == "Sol Ring" for obj in game.objects.values()
    )


def test_only_a_card_with_companion_can_be_revealed(card_db):
    # Unscripted, the card has no companion ability as far as the game knows.
    board = make_companion_board(card_db, scripted=False)
    assert board.game.player(P0).companion is None
    give_mana(board)
    assert companion_actions(board) == []


def test_a_second_companion_is_not_revealed(card_db):
    board = make_companion_board(card_db)
    other = card_db.lookup("Jegantha, the Wellspring")
    board.scripts.add(other.name, *COMPANION_ABILITIES)
    assert not reveal_companion(board.game, P0, other)
    assert board.game.player(P0).companion.name == LURRUS


# ---------------------------------------------------------------------------
# The special action (CR 116.2g)
# ---------------------------------------------------------------------------


def test_the_special_action_is_offered_in_your_main_phase(card_db):
    board = make_companion_board(card_db)
    give_mana(board)
    (action,) = companion_actions(board)
    assert action.cost is not None and action.cost.mana_value == 3
    assert action.as_action() in legal_actions(board.game, P0)


def test_paying_three_puts_the_companion_into_hand(card_db):
    board = make_companion_board(card_db)
    give_mana(board, amount=4)
    (action,) = companion_actions(board)
    player = board.game.player(P0)

    assert perform(board.game, P0, action.as_action())

    assert player.mana_pool.total == 1
    (obj,) = objects_named(board, LURRUS)
    assert obj.zone is Zone.HAND and obj.id in player.hand
    # CR 108.3: owned by the player who brought it in.
    assert obj.owner == P0 and obj.controller == P0
    # CR 702.139c: in the game now, not outside it.
    assert player.companion not in player.outside_game
    assert player.companion_brought_in


def test_the_owner_is_whoever_brought_it_in(card_db):
    """CR 108.3: the second seat's companion is the second seat's card."""
    scripts = ScriptedAbilities({LURRUS: COMPANION_ABILITIES})
    decks = [parse_decklist(DECK, card_db, name="P"), companion_deck(card_db)]
    game = new_game(decks, seed=1, ability_provider=scripts, randomize_turn_order=False)
    game.active_player = P1
    game.priority_player = P1
    game.phase = Phase.POSTCOMBAT_MAIN
    game.step = Step.MAIN
    game.player(P1).mana_pool.add(ManaKind(Color.BLACK), 3)
    board = Board(game=game, db=card_db, scripts=scripts)

    assert companion_actions(board, P0) == []
    (action,) = companion_actions(board, P1)
    assert perform(game, P1, action.as_action())
    (obj,) = objects_named(board, LURRUS)
    assert obj.owner == P1 and obj.id in game.player(P1).hand


def test_only_once_per_game(card_db):
    board = make_companion_board(card_db)
    give_mana(board, amount=6)
    (action,) = companion_actions(board)
    assert perform(board.game, P0, action.as_action())
    assert companion_actions(board) == []
    # Even with the card back outside the game, the action has been used.
    player = board.game.player(P0)
    player.outside_game.append(player.companion)
    # ... and no card of its name in the game to trip CR 903.11a instead.
    del board.game.objects[player.hand.pop()]
    assert companion_actions(board) == []
    assert not perform(board.game, P0, action.as_action())
    assert objects_named(board, LURRUS) == []


def test_not_without_three_mana(card_db):
    board = make_companion_board(card_db)
    give_mana(board, amount=2)
    assert companion_actions(board) == []
    # Performing it anyway spends nothing and brings nothing in.
    from mtgfish.rules.cr100_game_concepts.cr116_special_actions import SpecialAction

    assert not perform(board.game, P0, SpecialAction(SpecialKind.COMPANION).as_action())
    assert board.game.player(P0).mana_pool.total == 2
    assert objects_named(board, LURRUS) == []


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param(lambda g: setattr(g, "phase", Phase.COMBAT), id="combat"),
        pytest.param(lambda g: setattr(g, "phase", Phase.BEGINNING), id="upkeep"),
        pytest.param(lambda g: setattr(g, "active_player", P1), id="opponents-turn"),
        pytest.param(lambda g: g.stack.append(999_999), id="stack-not-empty"),
    ],
)
def test_timing_is_a_main_phase_of_your_turn_with_an_empty_stack(card_db, setup):
    board = make_companion_board(card_db)
    give_mana(board)
    assert companion_actions(board)  # control
    setup(board.game)
    assert companion_actions(board) == []
    assert not perform(board.game, P0, companion_action())


def companion_action():
    from mtgfish.rules.cr100_game_concepts.cr116_special_actions import SpecialAction

    return SpecialAction(SpecialKind.COMPANION).as_action()


# ---------------------------------------------------------------------------
# CR 903.11a: the door in has limits
# ---------------------------------------------------------------------------


def test_not_if_a_card_of_that_name_is_already_owned_in_the_game(card_db):
    board = make_companion_board(card_db)
    give_mana(board)
    board.play(LURRUS, 0)
    assert companion_actions(board) == []


def test_a_card_of_that_name_owned_by_someone_else_does_not_matter(card_db):
    board = make_companion_board(card_db)
    give_mana(board)
    board.play(LURRUS, 1)
    assert companion_actions(board)


def test_bringing_in_a_card_the_player_does_not_have_outside_fails(card_db):
    board = make_companion_board(card_db)
    card = card_db.lookup("Sol Ring")
    assert bring_into_game(board.game, P0, card, Zone.HAND) is None
    assert objects_named(board, "Sol Ring") == []


def test_bringing_in_respects_the_commanders_color_identity(card_db):
    board = make_companion_board(card_db)
    player = board.game.player(P0)
    # A mono-green commander: Lurrus (W/B) falls outside it.
    for object_id in player.commanders:
        board.game.objects[object_id].card = card_db.lookup("Omnath, Locus of Mana")
    give_mana(board)
    assert companion_actions(board) == []
    assert bring_into_game(board.game, P0, player.companion, Zone.HAND) is None


def test_bringing_in_refuses_a_starting_deck_name(card_db):
    board = make_companion_board(card_db)
    player = board.game.player(P0)
    player.starting_deck_names = player.starting_deck_names | {LURRUS}
    give_mana(board)
    assert companion_actions(board) == []


# ---------------------------------------------------------------------------
# The keyword itself
# ---------------------------------------------------------------------------


def test_the_companion_keyword_is_built_and_functions_in_no_zone():
    (ability,) = COMPANION_ABILITIES
    assert ability.keyword == "Companion"
    assert not ability.unparsed
    # CR 400.11: outside the game is not a zone.
    assert not any(ability.functions_in_zone(zone) for zone in Zone)
    assert lookup("Companion").status is Status.IMPLEMENTED


def test_a_bare_keyword_word_is_still_a_companion_ability(card_db):
    """The engine reads the keyword name, so a scripted bare keyword counts."""
    scripts = ScriptedAbilities({LURRUS: (keyword("Companion"),)})
    game = new_game(
        [companion_deck(card_db), parse_decklist(DECK, card_db)],
        seed=1,
        ability_provider=scripts,
        randomize_turn_order=False,
    )
    assert game.player(P0).companion is not None
