"""Designations and player-scoped game state (CR 725, 726, 728, 731).

The monarch, the initiative, day and night, and rad counters. These are not
card abilities - they are rules the game itself runs, which is why they live
here rather than being scripted onto individual cards. A card says "you become
the monarch"; everything that follows from being the monarch is the game's job.

The monarch matters more in Commander than anywhere else: it is a persistent
card-advantage engine that changes hands through combat, and a goldfishing
simulation that ignores it will systematically underrate every deck that plays
it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .events import Event, EventKind
from .ids import NO_PLAYER, PlayerId

if TYPE_CHECKING:
    from .game import Game


# ---------------------------------------------------------------------------
# The monarch (CR 725)
# ---------------------------------------------------------------------------


def monarch(game: Game) -> PlayerId:
    """Who the monarch is, or NO_PLAYER (CR 725.2: at most one, ever)."""
    for player in game.players:
        if player.is_monarch and not player.has_lost:
            return player.id
    return NO_PLAYER


def become_monarch(game: Game, player_id: PlayerId) -> bool:
    """Make a player the monarch (CR 725.3).

    CR 725.2: there is only ever one, so this takes the crown rather than
    adding a second holder.
    """
    if game.player(player_id).has_lost:
        return False
    if game.player(player_id).is_monarch:
        return False

    for player in game.players:
        player.is_monarch = player.id == player_id
    game.log.record(
        game, f"{game.player(player_id).name} becomes the monarch", kind="monarch",
        player=player_id,
    )
    game.emit(Event(EventKind.BECAME_MONARCH, player=player_id))
    return True


def monarch_end_step_draw(game: Game) -> None:
    """CR 725.3b: the monarch draws a card at the beginning of their end step.

    A turn-based action of the game, not a triggered ability of anything, so it
    happens whether or not the card that created the monarchy is still around.
    """
    holder = monarch(game)
    if holder != NO_PLAYER and holder == game.active_player:
        game.log.record(game, "The monarch draws a card", kind="monarch", player=holder)
        game.draw(holder, 1)


def combat_damage_to_monarch(game: Game, victim: PlayerId, attacker_controller: PlayerId) -> None:
    """CR 725.4: dealing combat damage to the monarch takes the crown."""
    if game.player(victim).is_monarch and attacker_controller != victim:
        become_monarch(game, attacker_controller)


def monarch_left_the_game(game: Game) -> None:
    """CR 725.5: if the monarch leaves, the active player becomes the monarch.

    If the active player is the one who left, the next player in turn order
    takes it instead.
    """
    if monarch(game) != NO_PLAYER:
        return
    order = game.apnap_order()
    if order:
        become_monarch(game, order[0])


# ---------------------------------------------------------------------------
# The initiative (CR 726)
# ---------------------------------------------------------------------------


def initiative_holder(game: Game) -> PlayerId:
    for player in game.players:
        if player.has_initiative and not player.has_lost:
            return player.id
    return NO_PLAYER


def take_initiative(game: Game, player_id: PlayerId) -> bool:
    """CR 726.2: one holder at a time, exactly like the monarch."""
    if game.player(player_id).has_lost:
        return False
    already = game.player(player_id).has_initiative
    for player in game.players:
        player.has_initiative = player.id == player_id
    if not already:
        game.log.record(
            game, f"{game.player(player_id).name} takes the initiative",
            kind="initiative", player=player_id,
        )
        game.emit(Event(EventKind.TOOK_INITIATIVE, player=player_id))
    return not already


def combat_damage_to_initiative_holder(
    game: Game, victim: PlayerId, attacker_controller: PlayerId
) -> None:
    """CR 726.4: combat damage to the holder passes the initiative."""
    if game.player(victim).has_initiative and attacker_controller != victim:
        take_initiative(game, attacker_controller)


# ---------------------------------------------------------------------------
# Day and night (CR 731)
# ---------------------------------------------------------------------------


def is_day(game: Game) -> bool | None:
    """True for day, False for night, None when it is neither (CR 731.1).

    "Neither" is a real third state, not a missing value: the game only has a
    day/night designation once something creates one.
    """
    return game.day_night


def become_day(game: Game) -> None:
    if game.day_night is not True:
        game.day_night = True
        game.log.record(game, "It becomes day", kind="daynight")
        game.emit(Event(EventKind.DAY_NIGHT_CHANGED, amount=1))


def become_night(game: Game) -> None:
    if game.day_night is not False:
        game.day_night = False
        game.log.record(game, "It becomes night", kind="daynight")
        game.emit(Event(EventKind.DAY_NIGHT_CHANGED, amount=0))


def check_day_night_transition(game: Game, previous_active: PlayerId) -> None:
    """CR 731.3: the day/night flip, checked as a turn begins.

    Day becomes night if the previous player cast no spells during their turn;
    night becomes day if a player cast two or more during theirs. The count is
    of the *previous* turn, which is why the spell counter is read before it is
    reset.
    """
    if game.day_night is None or previous_active == NO_PLAYER:
        return
    cast = game.spells_cast_last_turn
    if game.day_night is True and cast == 0:
        become_night(game)
    elif game.day_night is False and cast >= 2:
        become_day(game)


# ---------------------------------------------------------------------------
# Rad counters (CR 728)
# ---------------------------------------------------------------------------


def rad_counter_milling(game: Game, player_id: PlayerId) -> None:
    """CR 728.2: the precombat main phase rad-counter procedure.

    Mill one card for each rad counter; for each nonland card milled this way,
    lose 1 life and remove a rad counter. Modelled as a turn-based action
    because that is what it is - it happens automatically, without the stack.
    """
    player = game.player(player_id)
    if player.rad <= 0:
        return

    from . import actions

    before = len(player.graveyard)
    actions.mill(game, player_id, player.rad)
    milled = player.graveyard[before:]

    nonlands = 0
    for object_id in milled:
        obj = game.objects.get(object_id)
        if obj is None:
            continue
        if not game.printed_characteristics(obj).is_land:
            nonlands += 1

    if nonlands:
        actions.lose_life(game, player_id, nonlands)
        player.rad = max(0, player.rad - nonlands)
        game.log.record(
            game,
            f"{player.name} loses {nonlands} life and {nonlands} rad counters",
            kind="rad",
            player=player_id,
        )


def add_rad_counters(game: Game, player_id: PlayerId, amount: int) -> int:
    if amount <= 0:
        return 0
    game.player(player_id).rad += amount
    return amount
