"""Evaluating dynamic values (the ``Value`` structures from ``query.py``).

"Equal to the number of Swamps you control" is a number that has to be worked
out when the effect applies, not when it was written. Keeping evaluation here,
away from the data, means the parser can build a Value without a game and the
tests can compare two Values for equality.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

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
    die_results: tuple[int, ...] = (),
    remembered: tuple[ObjectId, ...] = (),
    this_way: Mapping[int, int] | None = None,
) -> int:
    """Work out what a Value currently is.

    ``remembered`` and ``this_way`` are the resolving spell or ability's own
    memory: the objects it last acted on ("each creature destroyed this
    way") and how much it has done so far ("the life lost this way"). Only a
    resolution has them; everywhere else they are empty, and a value that
    needs them is zero rather than a guess.
    """
    kind = value.kind

    if kind is ValueKind.CONSTANT:
        return value.constant

    if kind is ValueKind.EVENT_AMOUNT:
        return event_amount

    # CR 706.4: the result of the roll this ability just made. Several dice
    # total, which is what "the result" means when more than one was rolled.
    if kind is ValueKind.DIE_ROLL_RESULT:
        return sum(die_results)

    if kind is ValueKind.EVENT_COUNT_THIS_TURN:
        return _this_turn(game, value, controller)

    if kind is ValueKind.EVENT_AMOUNT_THIS_TURN:
        return _this_turn(game, value, controller, amounts=True)

    # CR 608.2c: what this resolution has itself done so far. Totalled by
    # event amount, not by event: an opponent losing 5 life is 5.
    if kind is ValueKind.AMOUNT_THIS_WAY:
        tally = this_way or {}
        return sum(tally.get(int(event_kind), 0) for event_kind in value.event_kinds)

    # CR 702.23a-b: the creatures blocking the source as this is evaluated.
    if kind is ValueKind.BLOCKERS_OF_SOURCE:
        combat = getattr(game, "combat", None)
        if combat is None:
            return 0
        return len(combat.blockers.get(source, ()))

    # CR 702.125b: players who have left the game are not counted.
    if kind is ValueKind.PLAYER_COUNT:
        from .matching import resolve_players

        if value.players is None:
            return 0
        return sum(
            1
            for player in resolve_players(game, value.players, controller=controller)
            if game.player(player).is_active_in_game
        )

    if kind is ValueKind.X:
        obj = game.objects.get(source)
        return obj.x_value if obj is not None else x_value

    if kind is ValueKind.COUNT:
        if value.filter is None:
            return 0
        return len(_among(game, value.filter, source, controller, remembered))

    if value.filter is not None and kind in _NAMED_OBJECT_VALUES:
        return _of_named_objects(game, value, source, controller, remembered)

    if kind in (ValueKind.POWER, ValueKind.TOUGHNESS, ValueKind.MANA_VALUE):
        # "Its controller gains life equal to its power", "then gains life
        # equal to that creature's toughness": in a resolving instruction
        # "its" is the object the previous instruction acted on, as it last
        # existed (CR 608.2h) - not the spell or ability's source. Reading the
        # source made Swords to Plowshares gain its controller 0 life, and
        # Solitude gain them Solitude's own power. A continuous effect
        # (CR 613) names its own subject and passes no ``remembered``.
        # The parser settles most such references to a named object before
        # they get here (``object_referents``, read by ``_of_named_objects``
        # above); this is the reading of one it left as "its".
        subject = remembered[0] if value.of_affected and remembered else source
        obj = game.objects.get(subject)
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
        colours = 0
        for obj in _among(game, value.filter, source, controller, remembered):
            colours |= int(game.characteristics(obj).colors)
        return bin(colours).count("1")

    # CR 903.4: a commander's colour identity, which is fixed before the
    # game and does not change when the commander changes zones.
    if kind is ValueKind.COMMANDER_COLOUR_IDENTITY:
        from ..cr100_game_concepts.cr107_numbers import Undeterminable

        from ..cr903_commander.cr903_color_identity import commander_color_identity

        identity = (
            commander_color_identity(game, controller) if controller != NO_PLAYER else None
        )
        if identity is None:
            # CR 903.4f: with no commander the quality is *undefined*, not
            # zero. An ability referring to it does nothing, which a zero
            # gives for free - but a cost referring to it is unpayable, and a
            # cost of zero is the most payable cost there is. The sentinel is
            # worth zero to every reader and tells the cost machinery apart.
            return Undeterminable("a commander's colour identity, and there is no commander")
        return bin(int(identity)).count("1")

    if kind is ValueKind.PARTY_SIZE:
        from ..cr700_additional_rules.cr700_general import party_size

        return party_size(game, controller) if controller != NO_PLAYER else 0

    if kind is ValueKind.DEVOTION:
        return _devotion(game, value, controller, source)

    if kind is ValueKind.COST_PAID_POWER:
        obj = game.objects.get(source)
        if obj is None:
            return 0
        total = 0
        for object_id in getattr(obj, "cost_paid_objects", ()):
            chars = _paid_as_it_was(game, object_id)
            if chars is not None:
                total += chars.power or 0
        return total

    if kind is ValueKind.COST_PAID:
        # "The sacrificed creature's toughness": each object the cost
        # consumed, as it last existed (CR 608.2h) - the record keeps the
        # object from before it moved.
        obj = game.objects.get(source)
        if obj is None or not value.operands:
            return 0
        wanted = value.operands[0].kind
        total = 0
        for object_id in getattr(obj, "cost_paid_objects", ()):
            chars = _paid_as_it_was(game, object_id)
            if chars is None:
                continue
            if wanted is ValueKind.TOUGHNESS:
                total += chars.toughness or 0
            elif wanted is ValueKind.MANA_VALUE:
                total += chars.mana_value
            elif wanted is ValueKind.POWER:
                total += chars.power or 0
        return total

    if kind in (
        ValueKind.GREATEST_AMONG,
        ValueKind.LEAST_AMONG,
        ValueKind.TOTAL_AMONG,
    ):
        return _superlative(game, value, kind, source, controller, x_value, remembered)

    if kind in (
        ValueKind.LIFE_TOTAL,
        ValueKind.CARDS_IN_HAND,
        ValueKind.POISON_COUNTERS,
        ValueKind.ENERGY,
    ):
        return _player_value(game, value, kind, controller)

    return _arithmetic(
        game,
        value,
        kind,
        source,
        controller,
        x_value,
        event_amount,
        die_results=die_results,
        remembered=remembered,
        this_way=this_way,
    )


#: Characteristics a Value can read off an object its ``filter`` names rather
#: than off the ability's source.
_NAMED_OBJECT_VALUES = frozenset(
    {
        ValueKind.POWER,
        ValueKind.TOUGHNESS,
        ValueKind.MANA_VALUE,
        ValueKind.COUNTERS,
        ValueKind.MANA_SPENT,
    }
)


def _of_named_objects(
    game: Game,
    value: Value,
    source: ObjectId,
    controller: PlayerId,
    remembered: tuple[ObjectId, ...],
) -> int:
    """"Its power", "that card's mana value", "the number of +1/+1 counters
    on enchanted creature" - a characteristic of the object the text names.

    The object is picked out by the value's filter, so Reanimate's "you lose
    life equal to that card's mana value" reads the card it returned and not
    Reanimate itself. A remembered object is asked as it was when the
    resolution acted on it (CR 608.2h): a creature exiled by Swords to
    Plowshares is a card in exile now, and "its power" is the power it last
    had on the battlefield - which is why the pre-move object is kept and
    read here rather than the one it became.

    Nothing named is zero, as a value about an object that is not there is
    everywhere else. More than one is their total, which only a filter that
    names a group can produce.
    """
    total = 0
    for obj in _among(game, value.filter, source, controller, remembered):
        if value.kind is ValueKind.COUNTERS:
            total += obj.counter_count(value.counter_type)
            continue
        if value.kind is ValueKind.MANA_SPENT:
            # CR 601.2g: recorded on the spell as it was cast.
            total += getattr(obj, "mana_spent", 0)
            continue
        chars = game.characteristics(obj)
        if value.kind is ValueKind.POWER:
            total += chars.power or 0
        elif value.kind is ValueKind.TOUGHNESS:
            total += chars.toughness or 0
        else:
            total += chars.mana_value
    return total


def _paid_as_it_was(game: Game, object_id):
    """An object a cost used, as it is now or as it last existed (CR 608.2h).

    One still where it was - a creature tapped to pay - is read as it is
    now. One the payment moved is read from the record taken as it was paid
    (``Game.cost_paid_last_known``): the object kept after the move is off
    the board and would read as printed.
    """
    paid = game.objects.get(object_id)
    if paid is None:
        return None
    if paid.superseded_by:
        known = game.cost_paid_last_known.get(object_id)
        if known is not None:
            return known
    return game.characteristics(paid)


def _among(
    game: Game,
    spec,
    source: ObjectId,
    controller: PlayerId,
    remembered: tuple[ObjectId, ...],
) -> list:
    """The objects a value's filter ranges over.

    An ordinary filter is matched against the game. A *remembered* one -
    "each creature destroyed this way", "cards discarded this way", "those
    cards" - names what the resolution acted on, and nothing else: matched
    against the whole game it counted every permanent on the battlefield,
    so Fumigate gained a life for each creature that *survived* it.

    The remembered objects are asked about as they were when acted on
    (CR 608.2h and last-known information, CR 608.2g): a creature that was
    destroyed is a card in a graveyard now, and "creature destroyed this
    way" is still about it. Their zones are not asked either, for the same
    reason - the filter's zone describes where they were, not where they are.
    """
    from dataclasses import replace

    from .matching import find, matches

    if not spec.remembered:
        return find(game, spec, source=source, controller=controller)
    described = replace(spec, remembered=False, zones=frozenset(), count=None, up_to=False)
    found = []
    for object_id in remembered:
        obj = game.objects.get(object_id)
        if obj is None or obj in found:
            continue
        if matches(
            game, obj, described, source=source, controller=controller, allow_stale=True
        ):
            found.append(obj)
    return found


def _this_turn(
    game: Game, value: Value, controller: PlayerId, *, amounts: bool = False
) -> int:
    """How many times an event happened this turn, for the named players -
    or, with ``amounts``, how much of it.

    Counting occurrences answers "how many spells", "how many times";
    totalling amounts answers "how much life" (EVENT_AMOUNT_THIS_TURN). The
    game keeps both tallies, keyed apart, for every event.
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
            key = (
                (int(event_kind), int(player))
                if amounts
                else (int(event_kind), int(player), "count")
            )
            total += game.turn_history.get(key, 0)
    return total


def _devotion(game: Game, value: Value, controller: PlayerId, source: ObjectId = NO_OBJECT) -> int:
    """CR 700.5: coloured mana symbols among permanents a player controls.

    Counted per *symbol*, not per permanent - a card costing {B}{B} adds two -
    and a hybrid symbol counts once if either half matches (CR 700.5), which
    is why this asks the symbol which colours can pay it rather than testing
    equality.
    """
    from .query import YOU

    wanted = value.colors
    if value.filter is not None and value.filter.of_chosen_color:
        # "Your devotion to that color": the colour the source chose. None
        # chosen is devotion to nothing.
        from .enums import Color

        chooser = game.objects.get(source)
        wanted = Color(getattr(chooser, "chosen_color", 0) or 0) if chooser else Color.NONE
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
    remembered: tuple[ObjectId, ...] = (),
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
    found = _among(game, value.filter, source, controller, remembered)
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
    *,
    die_results: tuple[int, ...] = (),
    remembered: tuple[ObjectId, ...] = (),
    this_way: Mapping[int, int] | None = None,
) -> int:
    operands = [
        evaluate(
            game,
            operand,
            source=source,
            controller=controller,
            x_value=x_value,
            event_amount=event_amount,
            die_results=die_results,
            remembered=remembered,
            this_way=this_way,
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
