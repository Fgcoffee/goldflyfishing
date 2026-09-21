"""Evaluating Conditions (the boolean structures from ``query.py``).

Used by intervening-if clauses (CR 603.4), by "if"/"unless" inside effects, and
by "as long as" in static abilities. One evaluator for all three, because they
are the same question asked at different moments.

``UNPARSED`` evaluates to False. An ability guarded by a condition we could not
read must not fire - reading an unknown condition as "true" would let a card do
something it should not, which is exactly the failure mode this project is
built to avoid.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .enums import Phase
from .ids import NO_OBJECT, NO_PLAYER, ObjectId, PlayerId
from .query import Condition, ConditionKind

if TYPE_CHECKING:
    from .game import Game


def _remembered_matches(game, condition, remembered, controller) -> bool:
    """Whether the object the resolution last acted on fits the description.

    Uses last-known information deliberately: "when this dies, if it was a
    creature" asks about the object as it was, and by the time the ability
    resolves it is in a graveyard and no longer a creature at all.
    """
    from .matching import matches

    if not remembered or condition.filter is None:
        return False
    for object_id in remembered:
        obj = game.objects.get(object_id)
        if obj is None:
            continue
        if matches(
            game,
            obj,
            condition.filter,
            controller=controller,
            allow_stale=True,
        ):
            return True
    return False


def _happened_this_turn(game, condition, controller) -> bool:
    """How much of something happened this turn, against a constraint.

    ``counter_type`` selects between "how many times" and "how much" - three
    spells cast is a count, seven damage dealt is an amount, and the same
    event kind can be asked either way.
    """
    from .matching import resolve_players
    from .query import YOU
    from .values import evaluate

    players = resolve_players(game, condition.players or YOU, controller=controller)
    if not players:
        return False

    counting = condition.counter_type == "count"
    total = 0
    for event_kind in condition.event_kinds:
        for player in players:
            key = (
                (int(event_kind), int(player), "count")
                if counting
                else (int(event_kind), int(player))
            )
            total += game.turn_history.get(key, 0)

    if condition.constraint is None:
        return total > 0
    return condition.constraint.comparison.holds(
        total,
        evaluate(game, condition.constraint.value, controller=controller),
    )


def _compare_counts(game, condition, controller) -> bool:
    """One player's count of something against a value, player by player.

    ``players`` says whose permanents to count and ``constraint`` what to
    measure them against. Each named player is asked separately and one
    satisfied player is enough, because "an opponent controls more lands than
    you" is about some single opponent - summing them all would make it true
    in almost every four-player game.
    """
    from dataclasses import replace

    from .matching import find, resolve_players
    from .query import ControllerRelation, PlayerFilter, PlayerScope
    from .values import evaluate

    if condition.filter is None or condition.constraint is None:
        return False

    players = resolve_players(
        game, condition.players or PlayerFilter(PlayerScope.OPPONENT),
        controller=controller,
    )
    target = evaluate(game, condition.constraint.value, controller=controller)

    for player in players:
        spec = replace(
            condition.filter,
            controller=ControllerRelation.SPECIFIC,
            controller_specific=player,
        )
        count = len(find(game, spec, controller=controller))
        if condition.constraint.comparison.holds(count, target):
            return True
    return False


def holds(
    game: Game,
    condition: Condition,
    *,
    source: ObjectId = NO_OBJECT,
    controller: PlayerId = NO_PLAYER,
    remembered: tuple[ObjectId, ...] = (),
) -> bool:
    """Whether a condition is currently true.

    ``remembered`` is what the resolution last acted on, and it is here
    because some conditions are about *that* rather than about the board: "if
    it's blue", "if it was a creature". Nothing in the game state knows what
    "it" refers to, so the caller with a resolution has to say.
    """
    kind = condition.kind

    if kind is ConditionKind.REMEMBERED_MATCHES:
        return _remembered_matches(game, condition, remembered, controller)

    if kind is ConditionKind.ALWAYS:
        return True
    if kind is ConditionKind.WAS_CAST:
        obj = game.objects.get(source)
        return bool(obj is not None and getattr(obj, "was_cast", False))

    if kind is ConditionKind.COMPARE_COUNTS:
        return _compare_counts(game, condition, controller)

    if kind is ConditionKind.EVENT_THIS_TURN:
        return _happened_this_turn(game, condition, controller)

    if kind is ConditionKind.PLAYER_COUNT:
        from .matching import resolve_players
        from .query import YOU
        from .values import evaluate

        players = resolve_players(
            game, condition.players or YOU, controller=controller
        )
        # A player who has left the game is not an opponent any more, which
        # matters in exactly the four-player setting this simulates.
        living = [p for p in players if game.player(p).is_active_in_game]
        if condition.constraint is None:
            return bool(living)
        return condition.constraint.comparison.holds(
            len(living),
            evaluate(game, condition.constraint.value, controller=controller),
        )

    if kind is ConditionKind.AT_MAX_SPEED:
        # CR 702.178a. The controller is the player whose speed is asked
        # about - a "Max speed" ability on your permanent reads your speed,
        # not the active player's.
        if controller == NO_PLAYER:
            return False
        return game.player(controller).at_max_speed
    if kind is ConditionKind.NEVER or kind is ConditionKind.UNPARSED:
        return False

    if kind is ConditionKind.AND:
        return all(holds(game, c, source=source, controller=controller) for c in condition.operands)
    if kind is ConditionKind.OR:
        return any(holds(game, c, source=source, controller=controller) for c in condition.operands)
    if kind is ConditionKind.NOT:
        return not any(
            holds(game, c, source=source, controller=controller) for c in condition.operands
        )

    if kind in (ConditionKind.OBJECT_COUNT, ConditionKind.CONTROLS_MATCHING):
        if condition.filter is None or condition.constraint is None:
            return False
        from .matching import find
        from .values import evaluate

        count = len(find(game, condition.filter, source=source, controller=controller))
        expected = evaluate(game, condition.constraint.value, source=source, controller=controller)
        return condition.constraint.comparison.holds(count, expected)

    if kind in (ConditionKind.LIFE, ConditionKind.CARDS_IN_HAND):
        if condition.constraint is None:
            return False
        from .matching import resolve_players
        from .query import YOU
        from .values import evaluate

        players = resolve_players(game, condition.players or YOU, controller=controller)
        if not players:
            return False
        expected = evaluate(game, condition.constraint.value, source=source, controller=controller)
        for player_id in players:
            player = game.player(player_id)
            actual = player.life if kind is ConditionKind.LIFE else player.hand_size
            if not condition.constraint.comparison.holds(actual, expected):
                return False
        return True

    if kind is ConditionKind.IS_YOUR_TURN:
        return game.active_player == controller

    if kind is ConditionKind.COUNTER_COUNT:
        if condition.constraint is None:
            return False
        from .values import evaluate

        subject_id = _subject(game, condition, source)
        subject = game.objects.get(subject_id)
        if subject is None:
            return False
        expected = evaluate(game, condition.constraint.value, source=source, controller=controller)
        actual = subject.counter_count(condition.counter_type)
        return condition.constraint.comparison.holds(actual, expected)

    if kind is ConditionKind.CLASS_LEVEL:
        # CR 716.2d: a permanent with no level is treated as level 1, so this
        # answers for any permanent, Class or not.
        if condition.constraint is None:
            return False
        from .card_types import class_level
        from .values import evaluate

        subject = _subject(game, condition, source)
        if subject == NO_OBJECT:
            return False
        expected = evaluate(game, condition.constraint.value, source=source, controller=controller)
        return condition.constraint.comparison.holds(class_level(game, subject), expected)

    if kind is ConditionKind.IS_SOLVED:
        from .card_types import is_solved

        subject = _subject(game, condition, source)
        return subject != NO_OBJECT and is_solved(game, subject)

    if kind is ConditionKind.NO_MANA_SPENT:
        # Asked of the spell that triggered this, which the engine records as
        # it is cast. A spell cast for free spent nothing; so did one whose
        # cost worked out to zero.
        spell = game.objects.get(source)
        if spell is None:
            return False
        return not spell.mana_spent

    if kind is ConditionKind.WAS_KICKED:
        # CR 702.33b: kicked means the optional additional cost was chosen at
        # announcement (CR 601.2b), which the spell records as it is cast.
        spell = game.objects.get(source)
        return bool(spell is not None and spell.additional_costs_paid)

    if kind is ConditionKind.IS_STEP:
        # CR 307.5 and friends: "activate only during your upkeep". Both halves
        # matter - the right step, and the right player's turn - because an
        # upkeep-only ability is not activatable during an opponent's upkeep
        # unless the card says so.
        if condition.constraint is None:
            return False
        from .values import evaluate

        expected = evaluate(game, condition.constraint.value, source=source, controller=controller)
        if not condition.constraint.comparison.holds(int(game.step), expected):
            return False
        if condition.players is not None:
            from .matching import resolve_players

            allowed = resolve_players(game, condition.players, controller=controller)
            return game.active_player in allowed
        return True

    if kind is ConditionKind.IS_MAIN_PHASE:
        return game.phase in (Phase.PRECOMBAT_MAIN, Phase.POSTCOMBAT_MAIN)

    return False


def _subject(game: Game, condition: Condition, source: ObjectId) -> ObjectId:
    """Which object a designation condition is asking about.

    Almost always the source itself - "this Class", "this Case" - so a filter
    that is anything other than a specific object resolves to the source rather
    than guessing.
    """
    if condition.filter is not None and condition.filter.specific:
        return condition.filter.specific[0]
    return source
