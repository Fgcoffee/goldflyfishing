"""Commander (CR 903) and multiplayer (CR 800s).

The rules that make this format its own thing: the command zone, the tax, the
21-damage clock, and rule 903.9 - which is a state-based action for graveyard
and exile but a replacement effect for hand and library, and that split is
observable in whether dies-triggers fire.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.actions import deal_damage, destroy
from mtgfish.rules.enums import LossReason, Zone
from mtgfish.rules.ids import PlayerId
from mtgfish.rules.player import COMMANDER_DAMAGE_THRESHOLD


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities(), players=4)


def commander_of(board, player: int):
    return board.game.objects[board.game.player(PlayerId(player)).commanders[0]]


# ---------------------------------------------------------------------------
# The command zone (CR 903.6)
# ---------------------------------------------------------------------------


def test_commander_starts_in_the_command_zone(board):
    for player in board.game.players:
        assert len(player.commanders) == 1
        assert board.game.objects[player.commanders[0]].zone is Zone.COMMAND


def test_command_zone_is_not_the_library(board):
    """A Commander deck is 99 cards plus the commander."""
    for player in board.game.players:
        assert player.library_size + player.hand_size == 99


# ---------------------------------------------------------------------------
# Commander tax (CR 903.8)
# ---------------------------------------------------------------------------


def test_tax_starts_at_zero(board):
    player = board.game.player(PlayerId(0))
    assert player.commander_tax(player.commanders[0]) == 0


def test_tax_is_two_per_previous_cast(board):
    player = board.game.player(PlayerId(0))
    commander = player.commanders[0]
    for expected in (2, 4, 6):
        player.record_commander_cast(commander)
        assert player.commander_tax(commander) == expected


def test_tax_follows_the_commander_across_zones(board):
    """CR 400.7 makes it a new object every time; CR 903.8 tracks the commander.

    A commander that dies and is recast is a different object on every trip, so
    the tax has to follow the identity rather than the object.
    """
    game = board.game
    player = game.player(PlayerId(0))
    original = commander_of(board, 0)

    player.record_commander_cast(game.commander_identity(original.id))
    on_battlefield = game.move_object(original, Zone.BATTLEFIELD, to_player=PlayerId(0))

    assert game.commander_identity(on_battlefield.id) == original.id
    assert player.commander_tax(game.commander_identity(on_battlefield.id)) == 2


# ---------------------------------------------------------------------------
# Rule 903.9: where a commander goes, and by which mechanism
# ---------------------------------------------------------------------------
def test_a_dying_commander_really_dies_first(board):
    """CR 903.9a is a state-based action, not a replacement.

    The commander is put into the graveyard - it dies (CR 700.4), every
    dies-trigger sees it - and only at the next state-based action check does
    its owner get to move it to the command zone.
    """
    game = board.game
    commander = game.move_object(commander_of(board, 0), Zone.BATTLEFIELD, to_player=PlayerId(0))

    destroy(game, commander)
    assert game.player(PlayerId(0)).graveyard, "it goes to the graveyard first"

    board.sba()
    assert not game.player(PlayerId(0)).graveyard
    assert any(
        game.objects[o].is_commander and game.objects[o].owner == 0 for o in game.command
    )


def test_a_dying_commander_triggers_dies_abilities(board):
    """The whole reason the distinction matters.

    A Blood Artist triggers when your commander dies, because it genuinely
    died. Modelling 903.9 as a replacement would silently delete that trigger
    from every game it ever mattered in.
    """
    game = board.game
    game.log.enabled = True
    commander = game.move_object(commander_of(board, 0), Zone.BATTLEFIELD, to_player=PlayerId(0))

    before = len(game.log)
    destroy(game, commander)
    emitted = " ".join(e.text for e in game.log.entries[before:])

    assert "DIES" in emitted
    assert "LEAVES_BATTLEFIELD" in emitted


def test_an_exiled_commander_is_also_a_state_based_action(board):
    """CR 903.9a covers graveyard and exile alike."""
    from mtgfish.rules.actions import exile

    game = board.game
    commander = game.move_object(commander_of(board, 0), Zone.BATTLEFIELD, to_player=PlayerId(0))

    exile(game, commander)
    assert game.exile, "it is exiled first"

    board.sba()
    assert not game.exile
    assert len(game.command) == 4


def test_a_commander_headed_for_hand_is_replaced_instead(board):
    """CR 903.9b: hand and library really are a replacement effect.

    It never touches the hand, so nothing can respond to it being there and no
    state-based action check is needed.
    """
    game = board.game
    commander = game.move_object(commander_of(board, 0), Zone.BATTLEFIELD, to_player=PlayerId(0))

    from mtgfish.rules.actions import bounce

    bounce(game, commander)
    assert not game.player(PlayerId(0)).hand_size or all(
        not game.objects[o].is_commander for o in game.player(PlayerId(0)).hand
    )
    assert len(game.command) == 4


def test_a_non_commander_is_unaffected(board):
    game = board.game
    bears = board.play("Grizzly Bears", controller=0)
    destroy(game, bears)
    board.sba()
    assert game.player(PlayerId(0)).graveyard


def test_the_owner_may_decline_and_leave_it_dead(board):
    """The choice belongs to the commander's owner (CR 903.9a)."""

    class Declines:
        def move_commander_to_command_zone(self, game, owner, obj, to_zone):
            return False

    game = board.game
    game.agents[PlayerId(0)] = Declines()
    commander = game.move_object(commander_of(board, 0), Zone.BATTLEFIELD, to_player=PlayerId(0))

    destroy(game, commander)
    board.sba()
    assert game.player(PlayerId(0)).graveyard


def test_the_option_expires_after_one_check(board):
    """CR 903.9a: "since the last time state-based actions were checked".

    A commander that has already sat through a check stays put, so a card that
    has been in a graveyard for a while cannot be scooped up later.
    """

    class Declines:
        def move_commander_to_command_zone(self, game, owner, obj, to_zone):
            return False

    game = board.game
    game.agents[PlayerId(0)] = Declines()
    commander = game.move_object(commander_of(board, 0), Zone.BATTLEFIELD, to_player=PlayerId(0))
    destroy(game, commander)
    board.sba()
    assert game.player(PlayerId(0)).graveyard

    # The owner changes their mind, but the window has closed.
    game.agents.pop(PlayerId(0))
    board.sba()
    assert game.player(PlayerId(0)).graveyard


# ---------------------------------------------------------------------------
# Commander damage (CR 903.10)
# ---------------------------------------------------------------------------


def test_twenty_one_damage_from_one_commander_is_lethal(board):
    game = board.game
    victim = game.player(PlayerId(1))
    commander = game.move_object(commander_of(board, 0), Zone.BATTLEFIELD, to_player=PlayerId(0))

    deal_damage(
        game,
        PlayerId(1),
        COMMANDER_DAMAGE_THRESHOLD,
        source=commander.id,
        source_controller=PlayerId(0),
        combat=True,
        is_commander_source=True,
    )
    board.sba()

    assert victim.has_lost
    assert victim.loss_reason is LossReason.COMMANDER_DAMAGE


def test_twenty_damage_is_not(board):
    game = board.game
    commander = game.move_object(commander_of(board, 0), Zone.BATTLEFIELD, to_player=PlayerId(0))
    deal_damage(
        game,
        PlayerId(1),
        20,
        source=commander.id,
        source_controller=PlayerId(0),
        combat=True,
        is_commander_source=True,
    )
    board.sba()
    assert not game.player(PlayerId(1)).has_lost


def test_damage_from_two_commanders_does_not_combine(board):
    """CR 903.10: 21 from a *single* commander, not 21 in total.

    The victim's life is raised out of the way first, so this measures the
    commander-damage clock rather than an ordinary death by life loss.
    """
    game = board.game
    victim = game.player(PlayerId(2))
    victim.life = 100

    first = game.move_object(commander_of(board, 0), Zone.BATTLEFIELD, to_player=PlayerId(0))
    second = game.move_object(commander_of(board, 1), Zone.BATTLEFIELD, to_player=PlayerId(1))

    for commander, controller in ((first, 0), (second, 1)):
        deal_damage(
            game,
            PlayerId(2),
            20,
            source=commander.id,
            source_controller=PlayerId(controller),
            combat=True,
            is_commander_source=True,
        )
    board.sba()

    assert sum(victim.commander_damage.values()) == 40
    assert max(victim.commander_damage.values()) == 20
    assert not victim.has_lost


def test_only_combat_damage_counts(board):
    """CR 903.10a: non-combat damage from a commander does not accumulate."""
    game = board.game
    victim = game.player(PlayerId(1))
    commander = game.move_object(commander_of(board, 0), Zone.BATTLEFIELD, to_player=PlayerId(0))

    deal_damage(
        game,
        PlayerId(1),
        21,
        source=commander.id,
        source_controller=PlayerId(0),
        combat=False,
        is_commander_source=True,
    )
    assert not victim.commander_damage


def test_commander_damage_survives_the_commander_dying_and_returning(board):
    """The clock follows the commander, not the object."""
    game = board.game
    victim = game.player(PlayerId(1))
    commander = game.move_object(commander_of(board, 0), Zone.BATTLEFIELD, to_player=PlayerId(0))
    identity = game.commander_identity(commander.id)

    deal_damage(
        game, PlayerId(1), 11, source=commander.id, source_controller=PlayerId(0),
        combat=True, is_commander_source=True,
    )
    destroy(game, commander)
    board.sba()  # CR 903.9a moves it home at the next check.

    reborn = next(
        game.objects[o] for o in game.command if game.objects[o].owner == 0
    )
    back = game.move_object(reborn, Zone.BATTLEFIELD, to_player=PlayerId(0))
    deal_damage(
        game, PlayerId(1), 10, source=back.id, source_controller=PlayerId(0),
        combat=True, is_commander_source=True,
    )

    assert victim.commander_damage[identity] == 21
    board.sba()
    assert victim.loss_reason is LossReason.COMMANDER_DAMAGE


# ---------------------------------------------------------------------------
# Multiplayer (CR 800.4)
# ---------------------------------------------------------------------------


def test_a_departing_player_takes_their_objects_with_them(board):
    """CR 800.4a."""
    game = board.game
    board.play("Grizzly Bears", controller=1)
    assert any(o.owner == 1 for o in game.objects.values())

    game.player_loses(PlayerId(1), LossReason.LIFE)
    assert not any(o.owner == 1 for o in game.objects.values())


def test_control_effects_end_when_the_controller_leaves(board):
    """CR 800.4e: a permanent they controlled but did not own reverts."""
    game = board.game
    bears = board.play("Grizzly Bears", controller=0)
    bears.controller = PlayerId(1)

    game.player_loses(PlayerId(1), LossReason.LIFE)

    assert bears.id in game.battlefield
    assert bears.controller == 0


def test_turn_order_survives_a_departure(board):
    game = board.game
    original = list(game.turn_order)
    game.player_loses(game.turn_order[1], LossReason.CONCEDE)

    assert game.turn_order == original
    remaining = game.turn_order_from(game.turn_order[0])
    assert len(remaining) == 3
    assert remaining == [p for p in original if not game.player(p).has_lost]


def test_game_ends_with_one_survivor(board):
    game = board.game
    for player_id in game.turn_order[1:]:
        game.player_loses(player_id, LossReason.LIFE)
    assert game.game_over
    assert game.winners == (game.turn_order[0],)


def test_apnap_order_is_active_player_first(board):
    """CR 101.4, and it drives every simultaneous choice in the game."""
    game = board.game
    game.active_player = game.turn_order[2]
    order = game.apnap_order()
    assert order[0] == game.turn_order[2]
    assert order == game.turn_order[2:] + game.turn_order[:2]
