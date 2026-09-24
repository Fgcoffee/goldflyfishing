"""Full games, end to end.

The determinism tests here are the ones the whole statistics and replay design
rests on. Replays are not stored - a replay is the same seed simulated again -
so if one seed can produce two different games, every clickable datapoint in
the UI would open a replay of a game that never happened.
"""

from __future__ import annotations

import pytest

from mtgfish.ai import SimpleAgent
from mtgfish.data.decks import parse_decklist
from mtgfish.rules.cr100_game_concepts.cr103_setup import (
    new_game,
    simple_land_policy,
    take_mulligans,
)
from mtgfish.rules.cr500_turn_structure.cr500_turn import (
    TURN_SEQUENCE,
    TurnOptions,
    run_game,
    take_turn,
)
from mtgfish.rules.kernel.enums import Step

DECK = (
    "// Commander\n1 Kenrith, the Returned King\n"
    "// Deck\n38 Forest\n31 Llanowar Elves\n30 Grizzly Bears\n"
)


def decks(card_db):
    return [parse_decklist(DECK, card_db, name=f"D{i}") for i in range(4)]


def play(card_db, seed: int, *, log: bool = False, rounds: int = 25):
    game = new_game(decks(card_db), seed=seed, log_enabled=log)
    for player in game.players:
        game.agents[player.id] = SimpleAgent()
    take_mulligans(game, simple_land_policy)
    run_game(game, TurnOptions(max_rounds=rounds))
    return game


# ---------------------------------------------------------------------------
# Turn structure (CR 500-514)
# ---------------------------------------------------------------------------


def test_turn_sequence_is_the_comprehensive_rules_order():
    phases = [phase for phase, _ in TURN_SEQUENCE]
    assert phases == sorted(phases), "phases must run in CR 500.1 order"

    steps = [step for _, step in TURN_SEQUENCE]
    assert steps[:3] == [Step.UNTAP, Step.UPKEEP, Step.DRAW]
    assert steps[-2:] == [Step.END_STEP, Step.CLEANUP]
    assert steps.count(Step.MAIN) == 2, "a precombat and a postcombat main phase"


def test_a_single_turn_advances_the_counter(card_db):
    game = new_game(decks(card_db), seed=3)
    for player in game.players:
        game.agents[player.id] = SimpleAgent()
    assert game.turn == 0
    take_turn(game)
    assert game.turn == 1
    assert game.step is Step.CLEANUP


def test_the_active_player_draws_for_turn(card_db):
    """CR 504.1. In multiplayer nobody skips it - CR 103.8a is a two-player rule.

    Measured by the draw counter rather than by hand size, because the bot
    spends cards during the turn it just drew them in.
    """
    game = new_game(decks(card_db), seed=3)
    for player in game.players:
        game.agents[player.id] = SimpleAgent()
    take_turn(game)
    assert game.player(game.active_player).cards_drawn_this_turn >= 1


def test_only_the_active_player_draws(card_db):
    game = new_game(decks(card_db), seed=3)
    for player in game.players:
        game.agents[player.id] = SimpleAgent()
    take_turn(game)
    for player in game.players:
        if player.id != game.active_player:
            assert player.cards_drawn_this_turn == 0


def test_cleanup_discards_down_to_hand_size(card_db):
    """CR 514.1."""
    game = new_game(decks(card_db), seed=4)
    for player in game.players:
        game.agents[player.id] = SimpleAgent()
    player = game.player(game.active_player)
    game.draw(player.id, 6)
    assert player.hand_size > player.max_hand_size

    take_turn(game)
    assert player.hand_size <= player.max_hand_size


def test_mana_pools_are_empty_between_turns(card_db):
    """CR 500.4: mana does not survive a step, let alone a turn."""
    game = play(card_db, 5, rounds=3)
    assert all(p.mana_pool.total == 0 for p in game.players)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_the_same_seed_replays_identically(card_db):
    first = play(card_db, 11)
    second = play(card_db, 11)

    assert first.log.digest() == second.log.digest()
    assert first.turn == second.turn
    assert first.winners == second.winners


def test_different_seeds_diverge(card_db):
    assert play(card_db, 11).log.digest() != play(card_db, 12).log.digest()


def test_enabling_logging_does_not_change_the_game(card_db):
    """A replay runs with logging on; it must reproduce the run that had it off."""
    quiet = play(card_db, 13)
    loud = play(card_db, 13, log=True)

    assert quiet.log.digest() == loud.log.digest()
    assert quiet.winners == loud.winners
    assert len(loud.log) > 0 and len(quiet.log) == 0


# ---------------------------------------------------------------------------
# Games actually finish
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_game_reaches_a_conclusion(card_db, seed):
    game = play(card_db, seed)
    assert game.game_over or game.turn >= 4 * 25
    if game.game_over:
        assert len(game.winners) == 1
        assert sum(1 for p in game.players if p.has_lost) == 3


def test_the_turn_cap_is_respected(card_db):
    """The stall-out limit is the simulation's guarantee of termination."""
    game = play(card_db, 7, rounds=3)
    assert game.turn <= 3 * 4


def test_life_totals_move(card_db):
    """A game where nothing happens would still pass every other test here."""
    game = play(card_db, 2)
    assert any(p.life != 40 for p in game.players)
