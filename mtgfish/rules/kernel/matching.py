"""Evaluating ObjectFilters and PlayerFilters against the game state.

The other half of ``query.py``: that module describes sets of objects, this one
decides membership. Kept separate because the descriptions are pure data that
the parser builds and tests compare, while evaluation needs the whole game.

Every unsupported constraint fails closed. If a filter asks something this
module cannot answer, the object does not match. An over-broad match would let
a spell hit something it should not; a missed match merely does less than it
should, and shows up as a rules test failure rather than an illegal game state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

from .enums import Color, Zone
from .gameobject import GameObject
from .ids import NO_OBJECT, NO_PLAYER, ObjectId, PlayerId
from .query import (
    ControllerRelation,
    NumericConstraint,
    ObjectFilter,
    PlayerFilter,
    PlayerScope,
    ValueKind,
)

if TYPE_CHECKING:
    from .game import Game


def current_loyalty(obj, chars):
    """A planeswalker's loyalty as the game sees it (CR 306.5c)."""
    from ..cr300_card_types.cr300_characteristics import loyalty

    return loyalty(obj, chars)


def matches(
    game: Game,
    obj: GameObject,
    spec: ObjectFilter,
    *,
    source: ObjectId = NO_OBJECT,
    controller: PlayerId = NO_PLAYER,
    override: object | None = None,
    allow_stale: bool = False,
) -> bool:
    """Whether ``obj`` satisfies ``spec``.

    ``source`` and ``controller`` are the ability's source and its controller,
    needed for "you control", "another", and "this creature".

    ``override`` supplies characteristics to test against instead of the
    object's current ones. The layer system uses this to ask the counterfactual
    question CR 613.8a is built on - "would this effect still apply if that
    other effect had already been applied?" - without which dependency cannot
    be detected at all.

    ``allow_stale`` permits matching a superseded object, which only
    last-known-information lookups should do (CR 603.6e). Everything else must
    refuse: a creature that has since been bounced is not a legal target just
    because the husk still remembers standing on the battlefield.
    """
    if not obj.is_live and not allow_stale:
        return False

    if spec.source_only:
        return _is_same_object(game, obj.id, source)
    if spec.other_than_source and _is_same_object(game, obj.id, source):
        return False
    if spec.specific and obj.id not in spec.specific:
        return False

    if spec.zones and obj.zone not in spec.zones:
        return False

    if spec.from_top and not _near_top_of_library(game, obj, spec.from_top):
        return False

    # Phased-out permanents are treated as not existing (CR 702.26a), so they
    # match nothing unless a filter explicitly asks for them.
    if obj.phased_out and spec.phased_out is not True:
        return False

    if not _controller_matches(game, obj, spec, controller):
        return False

    chars = override if override is not None else game.characteristics(obj)

    # Compared as plain integers, deliberately. These are IntFlags, and
    # ``IntFlag.__and__`` constructs a new enum member for its result - a
    # dictionary lookup and an object allocation per test. This function runs
    # two million times in a single game, and the flag arithmetic in it was
    # 23% of the whole run's time. ``int()`` on an IntFlag is free; the enum
    # is still what everything stores and passes around.
    types = int(chars.types)
    if spec.types_all:
        wanted = int(spec.types_all)
        if types & wanted != wanted:
            return False
    if spec.types_any and not types & int(spec.types_any):
        return False
    if spec.types_none and types & int(spec.types_none):
        return False

    if spec.supertypes_all or spec.supertypes_none:
        supertypes = int(chars.supertypes)
        if spec.supertypes_all:
            wanted = int(spec.supertypes_all)
            if supertypes & wanted != wanted:
                return False
        if spec.supertypes_none and supertypes & int(spec.supertypes_none):
            return False

    subtypes = chars.subtypes
    if spec.subtypes_all and not all(s in subtypes for s in spec.subtypes_all):
        return False
    if spec.subtypes_any and not any(s in subtypes for s in spec.subtypes_any):
        return False
    if spec.subtypes_none and any(s in subtypes for s in spec.subtypes_none):
        return False

    if not _colors_match(chars.colors, spec):
        return False

    if spec.named and chars.name not in spec.named:
        return False
    if spec.not_named and chars.name in spec.not_named:
        return False

    if spec.tapped is not None and obj.tapped != spec.tapped:
        return False
    if spec.face_down is not None and obj.face_down != spec.face_down:
        return False
    if spec.is_token is not None and obj.is_token != spec.is_token:
        return False
    if spec.is_commander is not None and obj.is_commander != spec.is_commander:
        return False
    if spec.summoning_sick is not None and obj.summoning_sick != spec.summoning_sick:
        return False
    if spec.entered_this_turn is not None:
        if obj.entered_this_turn(game.turn) != spec.entered_this_turn:
            return False

    if spec.attacking is not None or spec.blocking is not None or spec.blocked is not None:
        combat = getattr(game, "combat", None)
        if combat is None:
            return False
        if spec.attacking is not None and combat.is_attacking(obj.id) != spec.attacking:
            return False
        if spec.blocking is not None and combat.is_blocking(obj.id) != spec.blocking:
            return False
        if spec.blocked is not None:
            # CR 509.1h: an attacker becomes blocked or unblocked only when
            # blockers are declared, and stops being either when it leaves
            # combat. Before that "not in the blocked set" is not "unblocked",
            # and a creature that is not attacking is neither.
            if not combat.blockers_declared or not combat.is_attacking(obj.id):
                return False
            if combat.is_blocked(obj.id) != spec.blocked:
                return False

    if spec.power is not None and not _numeric(
        game, chars.power, spec.power, obj, source, controller
    ):
        return False
    if spec.toughness is not None and not _numeric(
        game, chars.toughness, spec.toughness, obj, source, controller
    ):
        return False
    if spec.mana_value is not None and not _numeric(
        game, chars.mana_value, spec.mana_value, obj, source, controller
    ):
        return False
    # CR 306.5c: on the battlefield a planeswalker's loyalty *is* its loyalty
    # counters, not the number printed on the card. "Target planeswalker with
    # loyalty 3 or less" has to read the board, not the printing.
    if spec.loyalty is not None and not _numeric(
        game, current_loyalty(obj, chars), spec.loyalty, obj, source, controller
    ):
        return False

    if (
        spec.of_chosen_type
        or spec.of_chosen_color
        or spec.not_of_chosen_type
        or spec.of_chosen_name
    ):
        chooser = game.objects.get(source)
        if chooser is None:
            return False
        wanted = chooser.chosen_type
        if spec.of_chosen_type and (
            not wanted or wanted not in chars.type_line.subtypes
        ):
            return False
        if spec.not_of_chosen_type and (
            wanted and wanted in chars.type_line.subtypes
        ):
            return False
        if spec.of_chosen_color and not (int(chars.colors) & chooser.chosen_color):
            return False
        # CR 201.4 with CR 201.2a: names are compared as names. A chosen name
        # that matches nothing in the game is not a failure of the choice -
        # naming a card the opponent has not cast yet is the usual case.
        if spec.of_chosen_name and (
            not chooser.chosen_name or chars.name != chooser.chosen_name
        ):
            return False

    if spec.of_chosen_type or spec.of_chosen_color:
        chooser = game.objects.get(source)
        if chooser is None:
            return False
        if spec.of_chosen_type:
            wanted = chooser.chosen_type
            if not wanted or wanted not in chars.type_line.subtypes:
                return False
        if spec.of_chosen_color and not (int(chars.colors) & chooser.chosen_color):
            return False

    if spec.is_ability is not None:
        from .gameobject import ObjectKind

        if (obj.kind is ObjectKind.ABILITY) is not spec.is_ability:
            return False

    if spec.has_counter:
        count = obj.counter_count(spec.has_counter)
        if spec.counter_constraint is not None:
            if not _numeric(game, count, spec.counter_constraint, obj, source, controller):
                return False
        elif count <= 0:
            return False

    if spec.has_keyword and not all(chars.has_keyword(k) for k in spec.has_keyword):
        return False
    if spec.lacks_keyword and any(chars.has_keyword(k) for k in spec.lacks_keyword):
        return False

    if spec.attached_to is not None:
        if obj.attached_to == NO_OBJECT:
            return False
        host = game.objects.get(obj.attached_to)
        if host is None or not matches(
            game, host, spec.attached_to, source=source, controller=controller
        ):
            return False

    if spec.has_attached is not None:
        if not any(
            matches(game, game.objects[a], spec.has_attached, source=source, controller=controller)
            for a in obj.attachments
            if a in game.objects
        ):
            return False

    return True


def _is_same_object(game: Game, object_id: ObjectId, source: ObjectId) -> bool:
    """Whether two ids refer to the same object across a zone change.

    CR 400.7 makes them formally different objects, and everything else in the
    engine treats them that way. But a self-referential trigger genuinely has
    to span one change: a dies-trigger's source is the card now in the
    graveyard, while the "this creature" it talks about is the permanent that
    left the battlefield. One step of the chain, deliberately - two would start
    conflating things the rules keep apart.
    """
    if object_id == source:
        return True
    source_obj = game.objects.get(source)
    if source_obj is not None and source_obj.previous_id == object_id:
        return True
    other = game.objects.get(object_id)
    return other is not None and other.previous_id == source


def _controller_matches(
    game: Game, obj: GameObject, spec: ObjectFilter, controller: PlayerId
) -> bool:
    relation = spec.controller
    if relation is ControllerRelation.ANY:
        return True
    if relation is ControllerRelation.YOU:
        return obj.controller == controller
    if relation is ControllerRelation.OPPONENT:
        return obj.controller != controller and controller != NO_PLAYER
    if relation is ControllerRelation.SPECIFIC:
        return obj.controller == spec.controller_specific
    if relation is ControllerRelation.SAME_AS_SOURCE:
        return obj.controller == controller
    return True


def _colors_match(colors: Color, spec: ObjectFilter) -> bool:
    """As integers, for the same reason the type checks are - see ``matches``."""
    if spec.must_be_colorless and colors is not Color.NONE:
        return False
    if spec.must_be_coloured and colors is Color.NONE:
        return False
    if spec.must_be_multicolored and colors.count < 2:
        return False
    if spec.must_be_monocolored and colors.count != 1:
        return False
    if not (spec.colors_all or spec.colors_any or spec.colors_none):
        return True

    value = int(colors)
    if spec.colors_all:
        wanted = int(spec.colors_all)
        if value & wanted != wanted:
            return False
    if spec.colors_any and not value & int(spec.colors_any):
        return False
    if spec.colors_none and value & int(spec.colors_none):
        return False
    return True


#: Value kinds that read a characteristic of one particular object, and so have
#: to say which: the object being tested, or the ability's source.
_CHARACTERISTIC_VALUES = frozenset(
    {ValueKind.POWER, ValueKind.TOUGHNESS, ValueKind.MANA_VALUE, ValueKind.COUNTERS}
)
#: Kinds whose operands are evaluated once per object they range over, with
#: that object as the subject, so the operands' flags say nothing about whose
#: characteristic the whole value reads.
_PER_OBJECT_VALUES = frozenset(
    {ValueKind.GREATEST_AMONG, ValueKind.LEAST_AMONG, ValueKind.TOTAL_AMONG}
)
#: Kinds that read something belonging to the ability's source rather than to
#: any object a filter ranges over: the X chosen as the spell was cast, the
#: mana actually spent on it, whatever its cost consumed.
_SOURCE_VALUES = frozenset(
    {ValueKind.X, ValueKind.MANA_SPENT, ValueKind.COST_PAID_POWER}
)
#: Arithmetic over other values. These ask the game nothing themselves, so
#: they read whatever their operands read and need whatever their operands
#: need.
_ARITHMETIC_VALUES = frozenset(
    {
        ValueKind.SUM,
        ValueKind.PRODUCT,
        ValueKind.DIFFERENCE,
        ValueKind.HALF_ROUNDED_UP,
        ValueKind.HALF_ROUNDED_DOWN,
        ValueKind.MAXIMUM,
        ValueKind.MINIMUM,
    }
)


def _numeric(
    game: Game,
    actual: int | None,
    constraint: NumericConstraint,
    obj: GameObject,
    source: ObjectId = NO_OBJECT,
    controller: PlayerId = NO_PLAYER,
) -> bool:
    """Compare one of ``obj``'s numbers with a bound that may vary.

    Two kinds of bound, read off two different objects. A characteristic
    marked ``of_affected`` is the tested object's own, which is what "its
    power" and Birthing Pod's "1 plus the sacrificed creature's mana value"
    mean. Everything else is a fact about the ability rather than about the
    candidate: the X chosen as the spell was cast, the number of lands *you*
    control, transmute's "the same mana value as the discarded card"
    (CR 702.53a). Read off each candidate instead, those bounds go wrong in
    both directions - transmute would compare every card with itself and match
    them all, while "mana value X or less" would compare every card against
    its own X, which is zero, and match nothing.

    "You" is always the ability's controller (CR 109.5), whichever object the
    bound is read off, so a counted bound uses that player when there is one.

    A bound that needs the ability with none to ask, or that mixes both
    subjects, has no single object to evaluate against and fails closed.
    """
    if actual is None:
        return False
    value = constraint.value
    if value.kind is ValueKind.CONSTANT:
        return constraint.comparison.holds(actual, value.constant)
    from .values import evaluate

    affected, from_source, contextual = _subjects(value)
    if affected and from_source:
        return False
    if affected:
        subject = obj.id
    elif contextual and source == NO_OBJECT:
        return False
    else:
        subject = source
    asker = controller if controller != NO_PLAYER else obj.controller
    expected = evaluate(game, value, source=subject, controller=asker)
    return constraint.comparison.holds(actual, expected)


def _subjects(value) -> tuple[bool, bool, bool]:
    """What a bound reads: the tested object, the ability's source, anything.

    Three answers, because the caller needs to tell apart "read this off the
    candidate", "read this off the ability" and "this asks the game nothing at
    all". The last one matters because arithmetic over constants is the same
    number with or without an ability to ask, and so must not fail closed for
    want of a source it never wanted.
    """
    affected = from_source = contextual = False
    pending = [value]
    while pending:
        node = pending.pop()
        kind = node.kind
        if kind is ValueKind.CONSTANT:
            continue
        if kind in _ARITHMETIC_VALUES:
            pending.extend(node.operands)
            continue
        contextual = True
        if kind in _CHARACTERISTIC_VALUES:
            if node.of_affected:
                affected = True
            else:
                from_source = True
        elif kind in _SOURCE_VALUES:
            from_source = True
        elif kind not in _PER_OBJECT_VALUES:
            # An unrecognised kind might read anything, so its operands are
            # still walked: a bound this module cannot place must fail closed
            # rather than be answered off the wrong object.
            pending.extend(node.operands)
    return affected, from_source, contextual


# ---------------------------------------------------------------------------
# Finding matching objects
# ---------------------------------------------------------------------------


def find(
    game: Game,
    spec: ObjectFilter,
    *,
    source: ObjectId = NO_OBJECT,
    controller: PlayerId = NO_PLAYER,
) -> list[GameObject]:
    """Every object matching ``spec``, in a deterministic order.

    Iteration follows zone order, which is stable, rather than the object
    table, which is insertion-ordered but reflects allocation history. Replay
    depends on this being reproducible.
    """
    out: list[GameObject] = []
    for zone in sorted(spec.zones or {Zone.BATTLEFIELD}):
        for obj in _objects_in_zone(game, zone):
            if matches(game, obj, spec, source=source, controller=controller):
                out.append(obj)
    return out


def _near_top_of_library(game: Game, obj: GameObject, depth: int) -> bool:
    """CR 401.2: a library is ordered, so "the top card" is a real position.

    Checked against the owner's library because that is the only library the
    card is in - "the top card of their library" narrows *which* library via
    the filter's owner constraint, not via this.
    """
    if obj.zone is not Zone.LIBRARY:
        return False
    player = game.player(obj.owner)
    return obj.id in player.library[:depth]


def _objects_in_zone(game: Game, zone: Zone) -> Iterator[GameObject]:
    if zone in (Zone.BATTLEFIELD, Zone.STACK, Zone.EXILE, Zone.COMMAND):
        for object_id in game.zone_list(zone):
            yield game.objects[object_id]
        return
    for player in game.players:
        for object_id in player.zone(zone):
            yield game.objects[object_id]


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------


def resolve_players(
    game: Game, spec: PlayerFilter, *, controller: PlayerId = NO_PLAYER
) -> list[PlayerId]:
    """Which players a PlayerFilter refers to, in APNAP order (CR 101.4)."""
    scope = spec.scope

    if scope is PlayerScope.SPECIFIC and spec.specific is not None:
        return [spec.specific]
    if scope is PlayerScope.YOU:
        return [controller] if controller != NO_PLAYER else []
    if scope is PlayerScope.ACTIVE_PLAYER:
        return [game.active_player]
    if scope is PlayerScope.MONARCH:
        return [p.id for p in game.players if p.is_monarch and not p.has_lost]
    if scope in (PlayerScope.EACH_OPPONENT, PlayerScope.OPPONENT, PlayerScope.TARGET_OPPONENT):
        return [p for p in game.apnap_order() if p != controller]
    if scope is PlayerScope.EACH_PLAYER:
        return game.apnap_order()
    if scope in (PlayerScope.CONTROLLER_OF, PlayerScope.OWNER_OF):
        obj = game.objects.get(spec.reference)
        if obj is None:
            return []
        return [obj.controller if scope is PlayerScope.CONTROLLER_OF else obj.owner]
    return []
