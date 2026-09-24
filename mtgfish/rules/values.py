"""Evaluating dynamic values (the ``Value`` structures from ``query.py``).

"Equal to the number of Swamps you control" is a number that has to be worked
out when the effect applies, not when it was written. Keeping evaluation here,
away from the data, means the parser can build a Value without a game and the
tests can compare two Values for equality.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .ids import NO_OBJECT, NO_PLAYER, ObjectId, PlayerId
from .query import Value, ValueKind

if TYPE_CHECKING:
    from .game import Game


def evaluate(
    game: Game,
    value: Value,
    *,
    source: ObjectId = NO_OBJECT,
    controller: PlayerId = NO_PLAYER,
    x_value: int = 0,
    event_amount: int = 0,
) -> int:
    """Work out what a Value currently is."""
    kind = value.kind

    if kind is ValueKind.CONSTANT:
        return value.constant

    if kind is ValueKind.EVENT_AMOUNT:
        return event_amount

    if kind is ValueKind.EVENT_COUNT_THIS_TURN:
        return _this_turn(game, value, controller)

    if kind is ValueKind.X:
        obj = game.objects.get(source)
        return obj.x_value if obj is not None else x_value

    if kind is ValueKind.COUNT:
        if value.filter is None:
            return 0
        from .matching import find

        return len(find(game, value.filter, source=source, controller=controller))

    if kind in (ValueKind.POWER, ValueKind.TOUGHNESS, ValueKind.MANA_VALUE):
        obj = game.objects.get(source)
        if obj is None:
            return 0
        chars = game.characteristics(obj)
        if kind is ValueKind.POWER:
            return chars.power or 0
        if kind is ValueKind.TOUGHNESS:
            return chars.toughness or 0
        return chars.mana_value

    if kind is ValueKind.COUNTERS:
        obj = game.objects.get(source)
        return obj.counter_count(value.counter_type) if obj is not None else 0

    if kind is ValueKind.MANA_SPENT:
        obj = game.objects.get(source)
        return getattr(obj, "mana_spent", 0) if obj is not None else 0

    if kind is ValueKind.COLOURS_AMONG:
        if value.filter is None:
            return 0
        from .matching import find

        colours = 0
        for obj in find(game, value.filter, controller=controller):
            colours |= int(game.characteristics(obj).colors)
        return bin(colours).count("1")

    if kind is ValueKind.COMMANDER_COLOUR_IDENTITY:
        player = game.player(controller) if controller != NO_PLAYER else None
        if player is None:
            return 0
        identity = 0
        for object_id in getattr(player, "commanders", ()):
            commander = game.objects.get(object_id)
            if commander is not None and commander.card is not None:
                identity |= int(commander.card.color_identity)
        return bin(identity).count("1")

    if kind is ValueKind.DEVOTION:
        return _devotion(game, value, controller)

    if kind is ValueKind.COST_PAID_POWER:
        obj = game.objects.get(source)
        if obj is None:
            return 0
        total = 0
        for object_id in getattr(obj, "cost_paid_objects", ()):
            paid = game.objects.get(object_id)
            if paid is not None:
                total += game.characteristics(paid).power or 0
        return total

    if kind in (
        ValueKind.GREATEST_AMONG,
        ValueKind.LEAST_AMONG,
        ValueKind.TOTAL_AMONG,
    ):
        return _superlative(game, value, kind, source, controller, x_value)

    if kind in (
        ValueKind.LIFE_TOTAL,
        ValueKind.CARDS_IN_HAND,
        ValueKind.POISON_COUNTERS,
        ValueKind.ENERGY,
    ):
        return _player_value(game, value, kind, controller)

    return _arithmetic(game, value, kind, source, controller, x_value, event_amount)


def _this_turn(game: Game, value: Value, controller: PlayerId) -> int:
    """How many times an event happened this turn, for the named players.

    Counts occurrences rather than totalling amounts: every card in this
    family asks "how many spells", "how many times", never "how much".
    """
    from .matching import resolve_players
    from .query import PlayerFilter, PlayerScope

    players = resolve_players(
        game,
        value.players or PlayerFilter(PlayerScope.YOU),
        controller=controller,
    )
    total = 0
    for event_kind in value.event_kinds:
        for player in players:
            total += game.turn_history.get((int(event_kind), int(player), "count"), 0)
    return total


def _devotion(game: Game, value: Value, controller: PlayerId) -> int:
    """CR 700.5: coloured mana symbols among permanents a player controls.

    Counted per *symbol*, not per permanent - a card costing {B}{B} adds two -
    and a hybrid symbol counts once if either half matches (CR 700.5), which
    is why this asks the symbol which colours can pay it rather than testing
    equality.
    """
    from .query import YOU

    wanted = value.colors
    if not wanted:
        return 0

    players = value.players or YOU
    from .matching import resolve_players

    owners = set(resolve_players(game, players, controller=controller))
    if not owners:
        owners = {controller}

    total = 0
    for obj in game.permanents():
        if obj.controller not in owners:
            continue
        cost = game.characteristics(obj).mana_cost
        for symbol in getattr(cost, "symbols", ()):
            if symbol.colors & wanted:
                total += 1
    return total


def _superlative(
    game: Game,
    value: Value,
    kind: ValueKind,
    source: ObjectId,
    controller: PlayerId,
    x_value: int,
) -> int:
    """"the greatest mana value among permanents you control".

    The inner Value is evaluated once per matching object, with that object as
    the source - which is what makes POWER, TOUGHNESS and MANA_VALUE work here
    without any of them knowing this exists. An empty set is zero, not an
    error: a superlative over nothing is how "for each" clauses read on an
    empty board.
    """
    if value.filter is None or not value.operands:
        return 0
    from .matching import find

    found = find(game, value.filter, source=source, controller=controller)
    if not found:
        return 0
    inner = value.operands[0]
    amounts = [
        evaluate(game, inner, source=obj.id, controller=controller, x_value=x_value)
        for obj in found
    ]
    if kind is ValueKind.TOTAL_AMONG:
        return sum(amounts)
    return max(amounts) if kind is ValueKind.GREATEST_AMONG else min(amounts)


def _player_value(game: Game, value: Value, kind: ValueKind, controller: PlayerId) -> int:
    from .matching import resolve_players
    from .query import YOU

    players = resolve_players(game, value.players or YOU, controller=controller)
    if not players:
        return 0
    # A player-scoped value referring to several players uses the largest,
    # which is what "equal to the greatest life total" and friends want; a
    # single-player scope has exactly one entry either way.
    amounts = []
    for player_id in players:
        player = game.player(player_id)
        if kind is ValueKind.LIFE_TOTAL:
            amounts.append(player.life)
        elif kind is ValueKind.CARDS_IN_HAND:
            amounts.append(player.hand_size)
        elif kind is ValueKind.POISON_COUNTERS:
            amounts.append(player.poison)
        else:
            amounts.append(player.energy)
    return max(amounts)


def _arithmetic(
    game: Game,
    value: Value,
    kind: ValueKind,
    source: ObjectId,
    controller: PlayerId,
    x_value: int,
    event_amount: int = 0,
) -> int:
    operands = [
        evaluate(
            game,
            operand,
            source=source,
            controller=controller,
            x_value=x_value,
            event_amount=event_amount,
        )
        for operand in value.operands
    ]

    if kind is ValueKind.SUM:
        return sum(operands) + value.constant
    if kind is ValueKind.PRODUCT:
        total = value.constant or 1
        for operand in operands:
            total *= operand
        return total
    if kind is ValueKind.DIFFERENCE:
        if not operands:
            return 0
        result = operands[0]
        for operand in operands[1:]:
            result -= operand
        return result
    if kind is ValueKind.HALF_ROUNDED_UP:
        return (operands[0] + 1) // 2 if operands else 0
    if kind is ValueKind.HALF_ROUNDED_DOWN:
        return operands[0] // 2 if operands else 0
    if kind is ValueKind.MAXIMUM:
        return max(operands) if operands else 0
    if kind is ValueKind.MINIMUM:
        return min(operands) if operands else 0
    return 0
