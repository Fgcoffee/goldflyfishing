"""Designations and player-scoped state (CR 705, 706, 725, 726, 728, 731).

The monarch is the one that matters most for Commander. It is a persistent
card-advantage engine that changes hands through combat, and any simulation
that ignores it systematically underrates every deck that plays it.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.rules.actions import flip_coin, roll_die
from mtgfish.rules.cr725_designations import (
    add_rad_counters,
    become_day,
    become_monarch,
    become_night,
    check_day_night_transition,
    initiative_holder,
    is_day,
    monarch,
    monarch_end_step_draw,
    rad_counter_milling,
    take_initiative,
)
from mtgfish.rules.enums import LossReason
from mtgfish.rules.ids import NO_PLAYER, PlayerId


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities(), players=4)


# ---------------------------------------------------------------------------
# The monarch (CR 725)
# ---------------------------------------------------------------------------


def test_nobody_starts_as_the_monarch(board):
    assert monarch(board.game) == NO_PLAYER


def test_becoming_the_monarch(board):
    assert become_monarch(board.game, PlayerId(0))
    assert monarch(board.game) == 0


def test_only_one_monarch_at_a_time(board):
    """CR 725.2."""
    become_monarch(board.game, PlayerId(0))
    become_monarch(board.game, PlayerId(2))
    assert monarch(board.game) == 2
    assert sum(1 for p in board.game.players if p.is_monarch) == 1


def test_the_monarch_draws_at_their_end_step(board):
    """CR 725.3b, and it is a turn-based action of the game itself."""
    game = board.game
    become_monarch(game, PlayerId(0))
    game.active_player = PlayerId(0)
    before = game.player(PlayerId(0)).hand_size

    monarch_end_step_draw(game)
    assert game.player(PlayerId(0)).hand_size == before + 1


def test_a_non_monarch_does_not_draw(board):
    game = board.game
    become_monarch(game, PlayerId(1))
    game.active_player = PlayerId(0)
    before = [p.hand_size for p in game.players]

    monarch_end_step_draw(game)
    assert [p.hand_size for p in game.players] == before


def test_combat_damage_steals_the_crown(board):
    """CR 725.4."""
    from mtgfish.rules.cr506_combat import deal_combat_damage, declare_attackers

    game = board.game
    become_monarch(game, PlayerId(1))
    game.active_player = PlayerId(0)
    attacker = board.play("Grizzly Bears", controller=0)
    game.agents[PlayerId(0)] = FixedAgent(attackers={attacker.id: 1})

    declare_attackers(game)
    deal_combat_damage(game)

    assert monarch(game) == 0


def test_blocked_damage_does_not_steal_the_crown(board):
    """No combat damage reaches the monarch, so the crown stays put."""
    from mtgfish.rules.cr506_combat import deal_combat_damage, declare_attackers, declare_blockers

    game = board.game
    become_monarch(game, PlayerId(1))
    game.active_player = PlayerId(0)
    attacker = board.play("Grizzly Bears", controller=0)
    blocker = board.play("Grizzly Bears", controller=1)
    game.agents[PlayerId(0)] = FixedAgent(attackers={attacker.id: 1})
    game.agents[PlayerId(1)] = FixedAgent(blockers={blocker.id: [attacker.id]})

    declare_attackers(game)
    declare_blockers(game)
    deal_combat_damage(game)

    assert monarch(game) == 1


def test_the_crown_passes_when_the_monarch_leaves(board):
    """CR 725.5: it does not simply vanish."""
    game = board.game
    become_monarch(game, PlayerId(1))
    game.player_loses(PlayerId(1), LossReason.LIFE)

    assert monarch(game) != NO_PLAYER
    assert monarch(game) != 1


# ---------------------------------------------------------------------------
# The initiative (CR 726)
# ---------------------------------------------------------------------------


def test_taking_the_initiative(board):
    assert take_initiative(board.game, PlayerId(2))
    assert initiative_holder(board.game) == 2


def test_only_one_initiative_holder(board):
    """CR 726.2."""
    take_initiative(board.game, PlayerId(0))
    take_initiative(board.game, PlayerId(3))
    assert initiative_holder(board.game) == 3
    assert sum(1 for p in board.game.players if p.has_initiative) == 1


def test_combat_damage_passes_the_initiative(board):
    from mtgfish.rules.cr506_combat import deal_combat_damage, declare_attackers

    game = board.game
    take_initiative(game, PlayerId(1))
    game.active_player = PlayerId(0)
    attacker = board.play("Grizzly Bears", controller=0)
    game.agents[PlayerId(0)] = FixedAgent(attackers={attacker.id: 1})

    declare_attackers(game)
    deal_combat_damage(game)

    assert initiative_holder(game) == 0


# ---------------------------------------------------------------------------
# Day and night (CR 731)
# ---------------------------------------------------------------------------


def test_the_game_starts_as_neither_day_nor_night(board):
    """CR 731.1: "neither" is a real third state, not a missing value."""
    assert is_day(board.game) is None


def test_day_becomes_night_when_nobody_casts_a_spell(board):
    """CR 731.3."""
    game = board.game
    become_day(game)
    game.spells_cast_this_turn = 0
    game.spells_cast_last_turn = 0

    check_day_night_transition(game, PlayerId(0))
    assert is_day(game) is False


def test_day_stays_day_when_a_spell_was_cast(board):
    game = board.game
    become_day(game)
    game.spells_cast_last_turn = 1

    check_day_night_transition(game, PlayerId(0))
    assert is_day(game) is True


def test_night_becomes_day_after_two_spells(board):
    game = board.game
    become_night(game)
    game.spells_cast_last_turn = 2

    check_day_night_transition(game, PlayerId(0))
    assert is_day(game) is True


def test_night_stays_night_after_one_spell(board):
    game = board.game
    become_night(game)
    game.spells_cast_last_turn = 1

    check_day_night_transition(game, PlayerId(0))
    assert is_day(game) is False


def test_neither_never_transitions(board):
    """With no day/night designation, nothing flips."""
    game = board.game
    game.spells_cast_last_turn = 0
    check_day_night_transition(game, PlayerId(0))
    assert is_day(game) is None


# ---------------------------------------------------------------------------
# Rad counters (CR 728)
# ---------------------------------------------------------------------------


def test_rad_counters_mill_and_drain(board):
    """CR 728.2: mill that many, then lose 1 life per nonland milled."""
    game = board.game
    player = game.player(PlayerId(0))
    add_rad_counters(game, PlayerId(0), 3)

    library_before = player.library_size
    rad_counter_milling(game, PlayerId(0))

    assert player.library_size == library_before - 3
    # The test decks are lands and creatures, so at least some are nonland.
    milled_nonlands = 40 - player.life if player.life < 40 else 0
    assert player.rad == 3 - milled_nonlands


def test_no_rad_counters_does_nothing(board):
    game = board.game
    player = game.player(PlayerId(0))
    before = (player.library_size, player.life)

    rad_counter_milling(game, PlayerId(0))
    assert (player.library_size, player.life) == before


def test_milling_only_lands_costs_no_life(board):
    """The nonland clause is the whole point: lands mill for free."""
    game = board.game
    player = game.player(PlayerId(0))
    # Rebuild the library out of lands only.
    from mtgfish.rules.enums import Zone

    forest = board.db.lookup("Forest")
    player.library.clear()
    for _ in range(5):
        game.create_object(forest, PlayerId(0), Zone.LIBRARY)

    add_rad_counters(game, PlayerId(0), 3)
    rad_counter_milling(game, PlayerId(0))

    assert player.life == 40
    assert player.rad == 3


# ---------------------------------------------------------------------------
# Coin flips and dice (CR 705, 706)
# ---------------------------------------------------------------------------


def test_coin_flips_come_from_the_seeded_rng(card_db):
    """Replay reconstructs a game from its seed; a flip from anywhere else
    would diverge immediately."""
    results = []
    for _ in range(2):
        board = make_board(card_db, ScriptedAbilities())
        results.append([flip_coin(board.game, PlayerId(0)) for _ in range(20)])
    assert results[0] == results[1]


def test_dice_rolls_are_in_range(board):
    for _ in range(50):
        assert 1 <= roll_die(board.game, PlayerId(0), 20) <= 20


def test_dice_rolls_are_reproducible(card_db):
    results = []
    for _ in range(2):
        board = make_board(card_db, ScriptedAbilities())
        results.append([roll_die(board.game, PlayerId(0), 6) for _ in range(20)])
    assert results[0] == results[1]
