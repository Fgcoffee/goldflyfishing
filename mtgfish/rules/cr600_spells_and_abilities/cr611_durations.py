"""When a continuous effect or a standing prohibition stops applying (CR 611.2b).

A duration is stated when the effect is created and checked at exactly three
moments: as a turn begins, as the end of combat step ends, and in the cleanup
step. Nothing else needs to look at one, so the whole rule lives here rather
than being spread across the turn structure.

Only ``END_OF_TURN`` was ever honoured. Everything else - "until end of
combat", "until your next turn", "until the end of your next turn" - was
created and then never expired, so those effects behaved as though they had no
duration at all. That is not a small difference: a creature stolen "until end
of turn" was stolen for good, and "creatures can't block this turn" locked
those creatures out of blocking for the rest of the game.

The two "next turn" durations cannot be resolved into a turn number when the
effect is created, because an extra turn taken in between changes which turn
counts as the controller's next one (CR 500.7). Each effect records the turn
it began on instead, and the check asks whether its controller has since begun
a later turn - which is the same question, asked at a moment when the answer
is knowable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..kernel.enums import Duration
from ..kernel.ids import NO_PLAYER

if TYPE_CHECKING:
    from ..kernel.game import Game


def _controllers_later_turn(game: Game, entry) -> bool:
    """Is this the controller's own turn, and a later one than it began on?

    An effect with no controller - one from a turn-based action or a test that
    did not name one - is treated as the active player's, so it still expires
    rather than lasting for ever.
    """
    controller = getattr(entry, "controller", NO_PLAYER)
    if controller == NO_PLAYER:
        controller = game.active_player
    # CR 800.4m: a player who has left the game never takes another turn, so
    # an effect waiting for it would wait for ever. The rule ends it when that
    # turn would have begun; the next turn boundary is where that falls, since
    # their seat is skipped rather than played (CR 800.4k).
    if game.player(controller).has_lost:
        return True
    return game.active_player == controller and game.turn > entry.created_turn


def _is_over(game: Game, entry, moment: Duration) -> bool:
    """Whether ``entry``'s duration has run out at ``moment``."""
    duration = entry.duration

    if moment is Duration.END_OF_COMBAT:
        return duration == int(Duration.END_OF_COMBAT)

    if moment is Duration.YOUR_NEXT_TURN:
        # "Until your next turn" ends as that turn begins, before anything in
        # it happens (CR 611.2b). An "until end of combat" effect that somehow
        # outlived its combat goes with it, so a missed end-of-combat step -
        # one skipped, or a phase that never reached it - cannot strand one.
        if duration == int(Duration.END_OF_COMBAT):
            return True
        return duration == int(Duration.YOUR_NEXT_TURN) and _controllers_later_turn(
            game, entry
        )

    if moment is Duration.END_OF_TURN:
        # CR 514.2: until-end-of-turn effects end in the cleanup step.
        if duration == int(Duration.END_OF_TURN):
            return True
        return duration == int(
            Duration.END_OF_YOUR_NEXT_TURN
        ) and _controllers_later_turn(game, entry)

    return False


def _expire(game: Game, moment: Duration) -> None:
    """Drop everything whose duration has run out, and rebuild the caches."""
    changed = False

    for effect in game.continuous_effects:
        if not effect.expired and _is_over(game, effect, moment):
            effect.expired = True
            changed = True
    if changed:
        game.continuous_effects = [e for e in game.continuous_effects if not e.expired]

    kept = [r for r in game.standing_restrictions if not _is_over(game, r, moment)]
    if len(kept) != len(game.standing_restrictions):
        game.standing_restrictions = kept
        game.restrictions_cache = None
        changed = True

    kept_permissions = [
        p for p in game.standing_permissions if not _is_over(game, p, moment)
    ]
    if len(kept_permissions) != len(game.standing_permissions):
        game.standing_permissions = kept_permissions
        game.permissions_epoch = -1
        changed = True

    if changed:
        game.invalidate_characteristics()


def expire_at_end_of_combat(game: Game) -> None:
    """CR 511.2: "until end of combat" expires at the end of the combat phase,
    which CR 511.3 places as the end of combat step ends."""
    _expire(game, Duration.END_OF_COMBAT)


def expire_at_start_of_turn(game: Game) -> None:
    """"Until your next turn" ends as that turn begins (CR 611.2b)."""
    _expire(game, Duration.YOUR_NEXT_TURN)


def expire_at_cleanup(game: Game) -> None:
    """CR 514.2: "until end of turn" and "until the end of your next turn"."""
    _expire(game, Duration.END_OF_TURN)
