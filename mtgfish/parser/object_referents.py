"""Whose characteristic "its power" is, in a one-shot effect.

The object half of reference settling; ``referents`` binds the players
("that player", "its controller") the same abilities name.

The value grammar reads "its power" and "that creature's toughness" without
knowing what the rest of the ability is about, so it can only say *which*
possessive was written: "its" (``of_affected``) or "that <noun>'s" (a value
whose filter is ``remembered``). What the words refer to depends on the
sentences around them, and in a resolving spell or ability there are only a
few candidates (CR 608.2c, 608.2h):

* the object an earlier instruction acted on - Reanimate's "that card", the
  creature Swords to Plowshares exiled;
* in a triggered ability, the object the trigger event was about - "whenever
  another creature you control enters, you gain life equal to its toughness";
* the one dealing the damage - "this creature deals damage equal to its
  power";
* the ability's own source, when nothing else has been mentioned - "{T},
  sacrifice this: you gain life equal to its power".

This pass settles each reference against those candidates, in order, and
rewrites it to the object the engine will read: the source (a plain value) or
the resolution's remembered object (a value whose filter is ``remembered``).
A reference it cannot settle - "put X counters on target creature, where X is
its power", which means the target of the very effect being carried out -
makes the ability unreadable instead of being answered off the wrong object.

Continuous effects are left alone: there "its power" is the affected
object's own, which the layer system evaluates object by object.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import replace

from ..rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from ..rules.cr600_spells_and_abilities.effects import (
    CONTINUOUS_KINDS,
    Effect,
    EffectKind,
)
from ..rules.kernel.query import ConditionKind, ObjectFilter, Value, ValueKind

#: The object the resolution is talking about - what a settled reference reads.
REMEMBERED = ObjectFilter(remembered=True)

_CHARACTERISTICS = frozenset(
    {
        ValueKind.POWER,
        ValueKind.TOUGHNESS,
        ValueKind.MANA_VALUE,
        ValueKind.COUNTERS,
        ValueKind.MANA_SPENT,
    }
)
#: "The sacrificed creature's power", "the exiled card's mana value": an
#: object that was consumed - by the cost, or by an earlier instruction.
_CONSUMED = frozenset({ValueKind.COST_PAID_POWER, ValueKind.COST_PAID})
#: The characteristics ``ValueKind.COST_PAID`` can read off what a cost
#: consumed (``kernel.values``).
_READ_OFF_THE_COST = frozenset({ValueKind.POWER, ValueKind.TOUGHNESS, ValueKind.MANA_VALUE})
#: Values whose operands are read once per object they range over: "its" in
#: there is each of those objects, not a reference to settle.
_PER_OBJECT = frozenset(
    {ValueKind.GREATEST_AMONG, ValueKind.LEAST_AMONG, ValueKind.TOTAL_AMONG}
)

#: Instructions after which the resolution remembers what they acted on, and
#: so what a later "it" names (``resolve._REMEMBERING``, plus the ones that
#: record their objects themselves).
_REMEMBERS = frozenset(
    {
        EffectKind.DESTROY,
        EffectKind.EXILE,
        EffectKind.SACRIFICE,
        EffectKind.TAP,
        EffectKind.UNTAP,
        EffectKind.RETURN_TO_HAND,
        EffectKind.PUT_ONTO_BATTLEFIELD,
        EffectKind.CREATE_TOKEN,
        EffectKind.ADD_COUNTERS,
        EffectKind.DOUBLE_COUNTERS,
        EffectKind.GAIN_CONTROL,
        EffectKind.COPY_PERMANENT,
        EffectKind.SEARCH_LIBRARY,
        EffectKind.DAMAGE,
        EffectKind.MOVE_ZONE,
        EffectKind.PUT_ON_LIBRARY,
        EffectKind.COUNTER_SPELL,
        EffectKind.REGENERATE,
        EffectKind.CHANGE_TARGETS,
        EffectKind.MODIFY_PT,
        EffectKind.GRANT_ABILITY,
        # These record what they found or looked at themselves.
        EffectKind.LOOK_AT_TOP,
        EffectKind.SEEK,
        EffectKind.CONJURE,
    }
)

#: Conditions that name the spell itself ("if this spell was kicked"), after
#: which "it" is the spell.
_ABOUT_THIS_SPELL = frozenset(
    {ConditionKind.WAS_KICKED, ConditionKind.ALTERNATIVE_COST_PAID, ConditionKind.WAS_CAST}
)

#: Instructions that record what they acted on even with no object filter
#: (the rest are remembered through their targets).
_RECORDS_ITSELF = frozenset(
    {
        EffectKind.SACRIFICE,
        EffectKind.DISCARD,
        EffectKind.CREATE_TOKEN,
        EffectKind.SEARCH_LIBRARY,
        EffectKind.LOOK_AT_TOP,
        EffectKind.SEEK,
        EffectKind.CONJURE,
    }
)

# What a reference can be settled against at a point in the ability.
_NOTHING = 0  # nothing mentioned yet: "it" is the source
_TRIGGER = 1  # a trigger event's object is in hand
_ACTED_ON = 2  # an earlier instruction acted on something
_LOST = 3  # inside a delayed ability, which remembers only what survived
_AMBIGUOUS = 4  # a trigger about two objects at once
_SELF = 5  # the last instruction acted on the source itself
_COST = 6  # a cost consumed an object the text goes on to talk about
# An earlier instruction acted on several objects ("create three Hamster
# tokens"). "Their power" and "those creatures" are them; a singular "it"
# cannot be, and is the source only when nothing else was mentioned before.
_MANY_AFTER_NOTHING = 7
_MANY = 8


class Unsettled(Exception):
    """A reference with no object the engine can read it off."""


#: Whether the ability being settled may have consumed an object to pay its
#: cost - which is what "the sacrificed creature's toughness" asks about when
#: no instruction of its own sacrificed one.
_PAID: ContextVar[bool] = ContextVar("_PAID", default=False)

#: Cost components that consume an object the text can go on to describe.
_CONSUMING_COSTS = frozenset(
    {
        "TAP_OTHER",
        "SACRIFICE",
        "DISCARD",
        "DISCARD_AT_RANDOM",
        "EXILE_FROM_GRAVEYARD",
        "EXILE_FROM_HAND",
        "EXILE_FROM_LIBRARY",
        "EXILE_FROM_BATTLEFIELD",
        "RETURN_TO_HAND",
    }
)


def _may_have_paid(ability: Ability, spell_paid: bool = False) -> bool:
    """Whether the ability's cost may consume an object.

    An activated ability's cost, and a spell's mandatory additional cost
    ("As an additional cost to cast this spell, sacrifice a creature"), are
    paid with a record of what they consumed (CR 601.2h), which "the
    sacrificed creature's power" reads as the object last existed (CR
    608.2h). ``spell_paid`` says the spell's face carries such a cost - a
    line of its own, not part of this ability. A triggered ability has no
    cost at all.
    """
    if ability.kind is AbilityKind.SPELL:
        return spell_paid
    if ability.kind is not AbilityKind.ACTIVATED:
        return False
    costs = [ability.cost, *getattr(ability.cost, "choices", ())]
    return any(
        component.kind.name in _CONSUMING_COSTS
        for cost in costs
        for component in getattr(cost, "components", ())
    )


def settle_referents(ability: Ability, *, spell_paid: bool = False) -> Ability | None:
    """The ability with every one-shot "its"/"that <noun>'s" settled, or
    None when one of them cannot be."""
    if ability.kind not in (AbilityKind.SPELL, AbilityKind.ACTIVATED, AbilityKind.TRIGGERED):
        if any(
            _mentions_cost(effect.amount) or _mentions_cost(effect.amount2)
            for top in ability.effects
            for effect in top.walk()
        ):
            # "Equipped creature gets +X/+0, where X is the exiled card's
            # power": a static ability pays no cost, and what was exiled by
            # some other ability is nothing it can read.
            return None
        return ability
    state = _NOTHING
    if ability.kind is AbilityKind.TRIGGERED and ability.trigger is not None:
        subject = ability.trigger.subject
        cause = ability.trigger.source
        about = [spec for spec in (subject, cause) if spec is not None and not spec.source_only]
        if subject is not None and cause is not None and about:
            # "Whenever an Archer you control deals damage to a creature,
            # that Archer deals that much damage to that creature's
            # controller": two objects the event was about, and "it" could
            # be either.
            state = _AMBIGUOUS
        elif about:
            state = _TRIGGER
    if state == _NOTHING and any(
        _mentions_cost(effect.amount) or _mentions_cost(effect.amount2)
        for top in ability.effects
        for effect in top.walk()
    ):
        # "Draw cards equal to the sacrificed creature's power, then gain
        # life equal to its toughness": "its" is the object the cost
        # consumed, which only its power can be read off.
        state = _COST
    token = _PAID.set(_may_have_paid(ability, spell_paid))
    try:
        effects, _ = _settle_all(ability.effects, state)
    except Unsettled:
        return None
    finally:
        _PAID.reset(token)
    return ability if effects == ability.effects else replace(ability, effects=effects)


def _settle_all(
    effects: tuple[Effect, ...], state: int, it_is_source: bool = False
) -> tuple[tuple[Effect, ...], int]:
    out = []
    for effect in effects:
        settled, state = _settle(effect, state, it_is_source)
        out.append(settled)
    return tuple(out), state


def _settle(effect: Effect, state: int, it_is_source: bool = False) -> tuple[Effect, int]:
    changes: dict = {}
    after = state
    if effect.kind is EffectKind.CONDITIONAL and effect.condition.kind in _ABOUT_THIS_SPELL:
        # "If this spell was kicked, it deals 3 damage to that player": the
        # condition has just named the spell, and "it" is the spell.
        it_is_source = True
    if effect.kind is EffectKind.CHOOSE_MODE:
        # Only one mode happens, so nothing one mode does is "earlier" for
        # another.
        children = tuple(
            _settle(child, state, it_is_source)[0] for child in effect.children
        )
        if children != effect.children:
            changes["children"] = children
    elif effect.kind is EffectKind.DELAYED_TRIGGER:
        # CR 603.7c: a delayed ability remembers only the objects still where
        # they were expected, so a value about one that has left is not
        # there to read later.
        inner = _LOST if state != _NOTHING else _NOTHING
        children, _ = _settle_all(effect.children, inner)
        if children != effect.children:
            changes["children"] = children
    else:
        if effect.children:
            children, after = _settle_all(effect.children, state, it_is_source)
            if children != effect.children:
                changes["children"] = children
        if effect.otherwise:
            otherwise, _ = _settle_all(effect.otherwise, state, it_is_source)
            if otherwise != effect.otherwise:
                changes["otherwise"] = otherwise

    if _condition_points_back(effect.condition):
        # "If its power is less than the revealed card's power": conditions
        # are evaluated without the resolution's memory, so a value about a
        # remembered object would be read as nothing.
        raise Unsettled
    if effect.targets is not None and _bound_points_back(effect.targets):
        # "A card with mana value equal to that card's mana value": a filter
        # is matched without the resolution's memory - even a continuous
        # effect's, whose set is fixed as it begins (CR 611.2c).
        raise Unsettled
    if effect.kind not in CONTINUOUS_KINDS and not effect.children:
        changes.update(_settle_leaf(effect, state, it_is_source))
    elif not effect.children:
        changes.update(_settle_continuous(effect, state))

    if effect.kind in _REMEMBERS or effect.kind is EffectKind.DISCARD:
        # An instruction that acted on the source itself ("sacrifice this
        # artifact") leaves the source remembered, and a later "that
        # creature's" cannot mean the object the trigger was about.
        own = effect.targets
        if own is not None and own.source_only:
            if effect.kind not in (EffectKind.MODIFY_PT, EffectKind.GRANT_ABILITY):
                after = _SELF
        elif own is not None or effect.kind in _RECORDS_ITSELF:
            if not _plural(effect):
                after = _ACTED_ON
            elif after in (_NOTHING, _MANY_AFTER_NOTHING):
                after = _MANY_AFTER_NOTHING
            else:
                after = _MANY
    elif (
        not effect.children
        and effect.targets is not None
        and not effect.targets.source_only
        and not effect.targets.remembered
    ):
        # "Attach it to target creature you control. That creature deals
        # damage equal to its power": the instruction names an object the
        # resolution does not remember, and that object is the nearer
        # antecedent. Whatever a later reference is settled to, it is not
        # that one.
        after = _AMBIGUOUS
    settled = replace(effect, **changes) if changes else effect
    return settled, after


def _settle_leaf(effect: Effect, state: int, it_is_source: bool = False) -> dict:
    changes: dict = {}
    dealer = effect.damage_source
    if effect.kind is EffectKind.DAMAGE and dealer is not None and dealer.remembered:
        # "It deals damage", "that creature deals damage": the dealer is a
        # reference like any other.
        pronoun = dealer == REMEMBERED
        if (pronoun and it_is_source) or _referent(
            effect, state, pronoun=pronoun, is_dealer=True
        ) is None:
            changes["damage_source"] = None
            dealer = None
    for name in ("amount", "amount2"):
        value = getattr(effect, name)
        new = _settle_value(value, effect, state, dealer)
        if new is not value:
            changes[name] = new
    if effect.token is not None:
        token = effect.token
        power = _settle_value(token.power, effect, state, dealer)
        toughness = _settle_value(token.toughness, effect, state, dealer)
        if power is not token.power or toughness is not token.toughness:
            changes["token"] = replace(token, power=power, toughness=toughness)
    return changes


def _plural(effect: Effect) -> bool:
    """Whether an instruction may have acted on more than one object, which
    a singular "it" then cannot mean."""
    if effect.kind is EffectKind.CREATE_TOKEN:
        amount = effect.amount
        return not (amount is not None and amount.is_constant and amount.constant <= 1)
    spec = effect.targets
    if spec is None or spec.remembered or spec.source_only:
        return False
    count = spec.count
    if isinstance(count, int):
        count = Value.of(count)
    if count is None:
        # No number written: "target creature", "enchanted creature" and
        # "all creatures" alike. Only a number says there are several;
        # "all creatures ... it" is not something cards write.
        return False
    return not (count.is_constant and count.constant <= 1)


def _bound_points_back(spec: ObjectFilter) -> bool:
    """Whether a numeric bound in the filter is about a remembered object."""
    from dataclasses import fields

    from ..rules.kernel.query import NumericConstraint

    for field in fields(spec):
        bound = getattr(spec, field.name)
        if isinstance(bound, NumericConstraint) and _mentions_remembered(bound.value):
            return True
    return False


def _condition_points_back(condition) -> bool:
    if condition.value is not None and _mentions_remembered(condition.value):
        return True
    if condition.constraint is not None and _mentions_remembered(condition.constraint.value):
        return True
    if condition.filter is not None and _bound_points_back(condition.filter):
        return True
    return any(_condition_points_back(op) for op in condition.operands)


def _mentions_remembered(value: Value) -> bool:
    """Whether a value needs the resolution's memory - which bounds and
    conditions are evaluated without."""
    if value.kind in _CHARACTERISTICS and value.filter is not None and value.filter.remembered:
        return True
    if value.kind in _CONSUMED and not _PAID.get():
        # "A creature card with mana value equal to 1 plus the sacrificed
        # creature's mana value" after "you may sacrifice a creature": the
        # instruction's object is only in the resolution's memory, and with
        # no cost there is no record to read instead.
        return True
    return any(_mentions_remembered(op) for op in value.operands)


def _mentions_cost(value: Value) -> bool:
    if value.kind in _CONSUMED:
        return True
    return any(_mentions_cost(op) for op in value.operands)


def _settle_continuous(effect: Effect, state: int) -> dict:
    """"That creature's power" in a continuous effect.

    A value about a remembered object is worked out as the effect begins
    (CR 608.2h) and needs something remembered to read. With nothing
    mentioned before, "target creature gets +X/+X, where X is that
    creature's power" means the affected creature, which is how the layer
    system reads an ``of_affected`` value.
    """
    changes: dict = {}

    def settle(value: Value) -> Value:
        if value.kind in _PER_OBJECT:
            return value
        if value.kind in _CONSUMED:
            return _consumed(value, state)
        if value.kind in _CHARACTERISTICS and value.filter == REMEMBERED:
            if state in (_ACTED_ON, _TRIGGER, _MANY, _MANY_AFTER_NOTHING):
                return value
            own = effect.targets
            if state == _NOTHING and own is not None and not (own.source_only or own.remembered):
                return replace(value, filter=None, of_affected=True)
            raise Unsettled
        if not value.operands:
            return value
        operands = tuple(settle(op) for op in value.operands)
        if all(new is old for new, old in zip(operands, value.operands)):
            return value
        return replace(value, operands=operands)

    for name in ("amount", "amount2"):
        value = getattr(effect, name)
        new = settle(value)
        if new is not value:
            changes[name] = new
    return changes


def _consumed(value: Value, state: int) -> Value:
    """"The sacrificed creature's toughness": the object an earlier
    instruction of this ability consumed, or else the one its cost did.

    Read off the cost only when nothing else was acted on first - with both
    a cost and an instruction in play the words do not say which - and only
    when there is a cost that consumes anything: a triggered ability's "you
    may sacrifice a creature. If you do, ..." names what it sacrificed
    itself, and the ability has no cost record to read.
    """
    characteristic = (
        ValueKind.POWER if value.kind is ValueKind.COST_PAID_POWER else value.operands[0].kind
    )
    acted = state in (_ACTED_ON, _MANY, _MANY_AFTER_NOTHING)
    if acted and not _PAID.get():
        return Value(kind=characteristic, filter=REMEMBERED)
    if state in (_NOTHING, _COST, _SELF) and _PAID.get():
        return value
    raise Unsettled


def _settle_value(value: Value, effect: Effect, state: int, dealer) -> Value:
    if value.kind in _PER_OBJECT:
        return value
    if value.kind in _CONSUMED:
        return _consumed(value, state)
    if value.kind in _CHARACTERISTICS:
        if value.of_affected:
            pronoun = True
        elif value.filter is not None and value.filter == REMEMBERED:
            pronoun = False
        else:
            return value
        if effect.kind is EffectKind.DAMAGE and pronoun:
            # "This creature deals damage equal to its power": "its" is the
            # dealer's, whoever else has been mentioned.
            found = REMEMBERED if dealer is not None else None
        elif (
            pronoun
            and state == _COST
            and _PAID.get()
            and value.kind in _READ_OFF_THE_COST
            and not value.counter_type
            and (effect.targets is None or effect.targets.source_only)
        ):
            # "Draw cards equal to the sacrificed creature's power, then you
            # gain life equal to its toughness": nothing but the consumed
            # creature has been mentioned, and its toughness is on the same
            # record, as it last existed (CR 608.2h).
            return Value(kind=ValueKind.COST_PAID, operands=(Value(kind=value.kind),))
        else:
            found = _referent(effect, state, pronoun=pronoun)
        return replace(value, of_affected=False, filter=found)
    if not value.operands:
        return value
    operands = tuple(_settle_value(op, effect, state, dealer) for op in value.operands)
    if all(new is old for new, old in zip(operands, value.operands)):
        return value
    return replace(value, operands=operands)


def _referent(
    effect: Effect, state: int, *, pronoun: bool, is_dealer: bool = False
) -> ObjectFilter | None:
    """The object a reference names: the remembered one, or None for the
    source. Raises ``Unsettled`` when neither is right."""
    own = effect.targets
    if (
        pronoun
        and not is_dealer
        and own is not None
        and not own.source_only
        and not own.remembered
    ):
        # The instruction names an object of its own, which is the nearer
        # antecedent: "put X +1/+1 counters on target creature, where X is
        # its power" means the target's power, and the resolution does not
        # remember the target until the instruction has been carried out.
        raise Unsettled
    if state in (_ACTED_ON, _TRIGGER):
        return REMEMBERED
    if state in (_MANY, _MANY_AFTER_NOTHING):
        # "Create three Hamster tokens, then it deals X damage": "it" is not
        # the tokens, and it can be the source only if the source was all
        # there was to refer to before them.
        if not pronoun:
            return REMEMBERED
        if state == _MANY or not is_dealer:
            # A value's "it" may be "them" ("for each counter on them"),
            # which is the objects just made; which one is not said.
            raise Unsettled
        return None
    if state == _SELF:
        # "Sacrifice this creature and it deals 3 damage": the remembered
        # object is the source. "That creature" is not.
        if pronoun:
            return None
        raise Unsettled
    if state in (_LOST, _AMBIGUOUS, _COST):
        raise Unsettled
    # Nothing mentioned before this instruction. "Its" with no antecedent is
    # the source; "that creature's" always points back at something already
    # mentioned, and here nothing was.
    if not pronoun:
        raise Unsettled
    return None
