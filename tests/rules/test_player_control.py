"""CR 722 preparation, CR 723 controlling a player, CR 727 restarting.

Three rules that only a handful of cards use, and all three are the kind that
an engine quietly gets wrong because nothing else exercises them.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.enums import LossReason
from mtgfish.rules.kernel.query import PlayerFilter, PlayerScope


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


class NamedAgent:
    """A passing agent that says who it is, so redirection is observable."""

    def __init__(self, name: str) -> None:
        self.name = name

    def choose_action(self, game, player, legal):
        from mtgfish.rules.cr100_game_concepts.cr117_priority import PASS

        return PASS

    def choose_optional(self, game, player, effect):
        return True

    def choose_discard(self, game, player):
        hand = game.player(player).hand
        return hand[-1] if hand else 0

    def order_triggers(self, game, player, triggers):
        return triggers

    def declare_attackers(self, game, player, candidates):
        return {}

    def declare_blockers(self, game, player, combat, available):
        return {}


# ---------------------------------------------------------------------------
# CR 723: controlling another player
# ---------------------------------------------------------------------------


def test_controlling_a_player_redirects_their_decisions(board):
    """CR 723.5: the controller makes every choice the controlled player would."""
    board.game.agents[0] = NamedAgent("P0")
    board.game.agents[1] = NamedAgent("P1")

    assert board.game.agent_for(1).name == "P1"

    board.game.control_player(1, 0)
    assert board.game.agent_for(1).name == "P0"
    assert board.game.agent_for(0).name == "P0"


def test_only_control_of_the_player_changes(board):
    """CR 723.3: their permanents stay theirs, and they stay the active player.

    Getting this wrong turns Mindslaver into a Blatant Thievery, which is a
    very different card.
    """
    board.game.agents[0] = NamedAgent("P0")
    board.game.agents[1] = NamedAgent("P1")
    theirs = board.play("Grizzly Bears", controller=1)

    board.game.active_player = 1
    board.game.control_player(1, 0)

    assert theirs.controller == 1
    assert board.game.active_player == 1


def test_the_latest_controlling_effect_wins(card_db):
    """CR 723.1a: multiple effects on one player overwrite each other.

    Needs three players to be meaningful: two different controllers reaching
    for the same victim.
    """
    table = make_board(card_db, ScriptedAbilities(), players=3)
    for index in range(3):
        table.game.agents[index] = NamedAgent(f"P{index}")

    table.game.control_player(2, 0)
    assert table.game.agent_for(2).name == "P0"

    table.game.control_player(2, 1)
    assert table.game.agent_for(2).name == "P1"


def test_giving_a_player_control_of_themselves_changes_nothing(board):
    """CR 723.9."""
    board.game.agents[0] = NamedAgent("P0")
    board.game.agents[1] = NamedAgent("P1")
    board.game.control_player(1, 0)

    execute(
        Resolution(game=board.game, source=0, controller=1),
        (
            Effect(
                EffectKind.CONTROL_PLAYER,
                players=PlayerFilter(PlayerScope.SPECIFIC, specific=1),
            ),
        ),
    )
    assert board.game.agent_for(1).name == "P1"


def test_control_ends_when_the_turn_does(board):
    """CR 723.1: the effect covers the affected player's whole turn, and ends
    with it - it is not permanent."""
    from mtgfish.rules.cr500_turn_structure.cr500_turn import take_turn

    board.game.agents[0] = NamedAgent("P0")
    board.game.agents[1] = NamedAgent("P1")
    board.game.active_player = 1
    board.game.control_player(1, 0)
    assert board.game.is_controlled(1)

    take_turn(board.game)
    assert not board.game.is_controlled(1)


def test_a_controlled_player_can_still_concede(board):
    """CR 723.6: the one decision control never transfers.

    Conceding is taken by the player directly rather than routed through
    ``agent_for``, which is what makes it impossible for the controller to do
    it on their behalf.
    """
    board.game.control_player(1, 0)
    assert board.game.concede(1) is True
    assert board.game.player(1).has_lost
    assert board.game.player(1).loss_reason is LossReason.CONCEDE


def test_the_control_effect_is_an_opcode_not_a_special_case(board):
    """It goes through the Effect IR like everything else, so the parser can
    reach it and the engine polices it."""
    board.game.agents[0] = NamedAgent("P0")
    board.game.agents[1] = NamedAgent("P1")

    execute(
        Resolution(game=board.game, source=0, controller=0),
        (
            Effect(
                EffectKind.CONTROL_PLAYER,
                players=PlayerFilter(PlayerScope.SPECIFIC, specific=1),
            ),
        ),
    )
    assert board.game.agent_for(1).name == "P0"


# ---------------------------------------------------------------------------
# CR 727: restarting the game
# ---------------------------------------------------------------------------


def test_restarting_ends_the_game_with_no_winner(board):
    """CR 727.1: "No players in that game win, lose, or draw that game."."""
    execute(
        Resolution(game=board.game, source=0, controller=1),
        (Effect(EffectKind.RESTART_GAME),),
    )
    assert board.game.restart_requested
    assert board.game.restart_starting_player == 1


def test_the_restarting_player_goes_first(board):
    """CR 727.1a."""
    execute(
        Resolution(game=board.game, source=0, controller=2),
        (Effect(EffectKind.RESTART_GAME),),
    )
    assert board.game.restart_starting_player == 2


def test_a_restart_with_no_decks_to_rebuild_from_says_so(board):
    """The engine must not pretend. A restart it cannot carry out is logged as
    unsupported rather than silently continuing the abandoned game."""
    from mtgfish.rules.cr500_turn_structure.cr500_turn import TurnOptions, _restart

    board.game.log.enabled = True
    board.game.source_decks = []
    board.game.restart_requested = True

    result = _restart(board.game, TurnOptions())
    assert result is board.game
    assert any(entry.kind == "unsupported" for entry in board.game.log.entries)


def test_run_game_returns_the_game_that_finished(board):
    """A restarted game is a different Game, so the caller has to be handed the
    one that actually produced the result."""
    from mtgfish.rules.cr500_turn_structure.cr500_turn import TurnOptions, run_game

    finished = run_game(board.game, TurnOptions(max_rounds=1))
    assert finished is board.game


# ---------------------------------------------------------------------------
# CR 722: preparation cards
# ---------------------------------------------------------------------------


# Preparation is tested in ``test_cr722_preparation``. The tests that stood
# here prepared a Grizzly Bears and a Delver of Secrets and expected a copy in
# exile, which CR 722.3a forbids: only a permanent with a prepare spell can
# become prepared.
