"""Turning keywords into abilities (CR 702).

A keyword is shorthand. "Flying" is a static ability; "Exalted" is a triggered
ability that reads "Whenever a creature you control attacks alone, that
creature gets +1/+1 until end of turn"; "Kicker {2}" is an optional additional
cost. The rules text of the card never spells any of that out, so something has
to expand it - and that something must be one place, not scattered through the
engine.

This module is that place, and it is deliberately the interface the parser will
call. The parser's job for a keyword line reduces to: recognise the name, read
its parameter, and hand a ``KeywordInstance`` to ``build``. Everything about
what the keyword *means* lives here, so adding a keyword never means touching
the parser, and a keyword's behaviour can be tested with no parser at all.

``build`` returns a tuple because one keyword can be several abilities: Modular
is an enters-with-counters replacement *and* a dies trigger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..cr100_game_concepts.cr106_mana import ManaCost
from ..cr100_game_concepts.cr118_costs import (
    AdditionalCost,
    AlternativeCost,
    Cost,
    CostComponent,
    CostKind,
)
from ..cr500_turn_structure.restrictions import Act, Restriction
from ..cr600_spells_and_abilities.abilities import Ability, AbilityKind, TriggerCondition
from ..cr600_spells_and_abilities.effects import Effect, EffectKind, TokenSpec
from ..kernel.enums import CardType, Color, Duration, Timing, Zone
from ..kernel.events import LEAVE_BATTLEFIELD_KINDS, EventKind
from ..kernel.query import (
    Comparison,
    Condition,
    ConditionKind,
    ControllerRelation,
    NumericConstraint,
    ObjectFilter,
    PlayerFilter,
    PlayerScope,
    Value,
    ValueKind,
)

BATTLEFIELD = frozenset({Zone.BATTLEFIELD})
GRAVEYARD = frozenset({Zone.GRAVEYARD})
HAND = frozenset({Zone.HAND})
#: A characteristic-defining ability works in every zone (CR 604.3).
ANY_ZONE = frozenset(Zone)

SELF = None  # An effect with no filter applies to its own source.
CREATURES_YOU_CONTROL = ObjectFilter(
    types_all=CardType.CREATURE, controller=ControllerRelation.YOU
)
YOU = PlayerFilter(PlayerScope.YOU)
EACH_OPPONENT = PlayerFilter(PlayerScope.EACH_OPPONENT)


@dataclass(frozen=True, slots=True)
class KeywordInstance:
    """One keyword as it appears on a card, with its parameter.

    "Annihilator 2" is ``KeywordInstance("Annihilator", amount=2)``;
    "Ward {2}" is ``KeywordInstance("Ward", cost=Cost.mana("{2}"))``;
    "Protection from red" carries a filter.
    """

    name: str
    amount: int = 0
    cost: Cost | None = None
    filter: ObjectFilter | None = None
    text: str = ""
    #: Where an alternative-cost keyword lets the card be cast from.
    from_zone: Zone | None = None
    #: The keyword's body, for the keywords that have one. Channel, forecast
    #: and "max speed" are shapes around an arbitrary effect - "[Cost],
    #: Discard this card: [effect]" - so the shape is built here and the
    #: parser supplies what goes inside it. Empty means the parser did not
    #: read the body, and the builder says so rather than inventing one.
    effects: tuple[Effect, ...] = ()

    @property
    def key(self) -> str:
        return self.name.lower()


Builder = Callable[[KeywordInstance], tuple[Ability, ...]]
BUILDERS: dict[str, Builder] = {}


def register(*names: str) -> Callable[[Builder], Builder]:
    def decorate(builder: Builder) -> Builder:
        for name in names:
            BUILDERS[name.lower()] = builder
        return builder

    return decorate


def build(instance: KeywordInstance) -> tuple[Ability, ...]:
    """Expand a keyword into the abilities that implement it.

    An unknown keyword produces an ability marked ``unparsed`` rather than
    nothing at all, so it appears in the coverage report instead of silently
    doing less than the card says.
    """
    builder = BUILDERS.get(instance.key)
    if builder is None:
        return (Ability.unreadable(instance.text or instance.name),)

    # The keyword's name is stamped here rather than in each builder. Around a
    # fifth of them used ``Ability.static(...)`` and never set it, so anything
    # asking "did this ability come from a keyword?" - the coverage report, the
    # cross-check, a card that cares about keyword abilities - got the wrong
    # answer for Bloodthirst, Modular, Fading and their kind.
    from dataclasses import replace

    return tuple(
        ability if ability.keyword or ability.unparsed
        else replace(ability, keyword=instance.name)
        for ability in builder(instance)
    )


def implemented_keywords() -> frozenset[str]:
    """Every keyword this module can expand, for the coverage registry."""
    return frozenset(BUILDERS)


# ---------------------------------------------------------------------------
# Simple statics: the keyword *is* the ability (CR 702.x)
# ---------------------------------------------------------------------------

#: Keywords whose entire meaning is "this object has this keyword", which the
#: rest of the engine already asks about by name - combat evasion, damage
#: modifiers, and the like.
_PLAIN_STATICS = (
    "Flying", "Reach", "Menace", "Fear", "Intimidate", "Shadow", "Horsemanship",
    "Skulk", "Deathtouch", "Double strike", "First strike", "Trample", "Lifelink",
    "Vigilance", "Defender", "Indestructible", "Hexproof", "Shroud", "Flash",
)


@register("Changeling")
def _changeling(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.73a: "this object is every creature type."

    A characteristic-defining ability, so it applies in every zone and in
    layer 4 rather than at a timestamp - which is what makes a changeling in
    a graveyard count for Tarmogoyf, and what makes Conspiracy naming a type
    interact the way it does. The list comes from the subtype registry built
    from the real card pool, so a new set adds types without code changes.
    """
    from ..cr200_parts_of_a_card.cr205_typeline import DEFAULT_REGISTRY

    return (
        Ability(
            AbilityKind.STATIC,
            effects=(
                Effect(
                    EffectKind.ADD_TYPE,
                    targets=ObjectFilter(source_only=True),
                    keywords=tuple(sorted(DEFAULT_REGISTRY.creature_types())),
                    text="is every creature type",
                ),
            ),
            keyword=instance.name,
            is_characteristic_defining=True,
            functions_in=ANY_ZONE,
            text=instance.text or instance.name,
        ),
    )


@register("Devoid")
def _devoid(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.114a: "this object is colorless."

    Also a characteristic-defining ability (layer 5), which is why a devoid
    card with {B} in its cost is still colorless everywhere - in hand, on the
    stack, in the graveyard - rather than only once it resolves.
    """
    from ..kernel.enums import Color

    return (
        Ability(
            AbilityKind.STATIC,
            effects=(
                Effect(
                    EffectKind.SET_COLOR,
                    targets=ObjectFilter(source_only=True),
                    colors=Color.NONE,
                    text="is colorless",
                ),
            ),
            keyword=instance.name,
            is_characteristic_defining=True,
            functions_in=ANY_ZONE,
            text=instance.text or instance.name,
        ),
    )


@register("Decayed")
def _decayed(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.147a: "this creature can't block. When it attacks, sacrifice it
    at end of combat."

    Two abilities, and the second is a delayed trigger set up by the first -
    the sacrifice does not happen when it attacks, it happens at end of combat
    that turn, so the creature deals its damage first.
    """
    cant_block = Ability.static(
        Effect(
            EffectKind.RESTRICTION,
            targets=ObjectFilter(source_only=True),
            restrictions=(
                Restriction(
                    act=Act.BLOCK,
                    subject=ObjectFilter(source_only=True),
                    text="decayed: can't block",
                ),
            ),
            text="can't block",
        ),
        text="This creature can't block.",
    )
    sacrifice_at_eoc = Ability(
        AbilityKind.TRIGGERED,
        effects=(
            Effect(
                EffectKind.DELAYED_TRIGGER,
                trigger=TriggerCondition(
                    event_kinds=frozenset({EventKind.STEP_ENDED}),
                    text="at end of combat",
                ),
                children=(
                    Effect(
                        EffectKind.SACRIFICE,
                        targets=ObjectFilter(source_only=True),
                        text="sacrifice it",
                    ),
                ),
                text="at end of combat, sacrifice it",
            ),
        ),
        trigger=TriggerCondition(
            event_kinds=frozenset({EventKind.ATTACKS}),
            subject=ObjectFilter(source_only=True),
            text="when this creature attacks",
        ),
        keyword=instance.name,
        text="When it attacks, sacrifice it at end of combat.",
    )
    return (cant_block, sacrifice_at_eoc)


@register("Split second")
def _split_second(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.61a: while this spell is on the stack, players can't cast spells
    or activate abilities that aren't mana abilities.

    A static ability that functions from the stack (CR 604.3), which is the
    only reason the restriction collector looks there at all. Two things it
    deliberately does *not* stop, because the rule does not: mana abilities,
    and anything that is not casting or activating. Special actions - turning
    a morph face up above all - and triggered abilities still happen, which is
    the whole of what makes split second beatable.
    """
    everyone = PlayerFilter(PlayerScope.EACH_PLAYER)
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            effects=(
                Effect(
                    EffectKind.RESTRICTION,
                    players=everyone,
                    restrictions=(
                        Restriction(
                            act=Act.CAST_SPELL,
                            players=everyone,
                            text="split second: can't cast spells",
                        ),
                        Restriction(
                            act=Act.ACTIVATE_ABILITY,
                            players=everyone,
                            text="split second: can't activate non-mana abilities",
                        ),
                    ),
                    text=instance.text or instance.name,
                ),
            ),
            functions_in=frozenset({Zone.STACK}),
            text=instance.text or instance.name,
        ),
    )


@register(*_PLAIN_STATICS)
def _plain_static(instance: KeywordInstance) -> tuple[Ability, ...]:
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


# ---------------------------------------------------------------------------
# Combat triggers (CR 702.x)
# ---------------------------------------------------------------------------


def _attack_trigger(instance: KeywordInstance, *effects: Effect, subject=None) -> Ability:
    return Ability.triggered(
        TriggerCondition(
            event_kinds=frozenset({EventKind.ATTACKS}),
            subject=subject if subject is not None else ObjectFilter(source_only=True),
            functions_in=BATTLEFIELD,
            text=f"whenever this attacks ({instance.name})",
        ),
        *effects,
        text=instance.text or instance.name,
    )


@register("Battle Cry")
def _battle_cry(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.91a: whenever this creature attacks, each *other* attacking
    creature gets +1/+0 until end of turn."""
    others = ObjectFilter(
        types_all=CardType.CREATURE, attacking=True, other_than_source=True
    )
    return (
        _attack_trigger(
            instance,
            Effect(
                EffectKind.MODIFY_PT,
                targets=others,
                amount=Value.of(1),
                amount2=Value.of(0),
                duration=int(Duration.END_OF_TURN),
            ),
        ),
    )


@register("Exalted")
def _exalted(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.83a: whenever a creature you control attacks alone, it gets +1/+1.

    The "alone" clause is an intervening-if on the count of attackers, so it is
    checked both when it would trigger and again on resolution (CR 603.4).
    """
    attacking = ObjectFilter(types_all=CardType.CREATURE, attacking=True)
    alone = Condition(
        ConditionKind.OBJECT_COUNT,
        filter=attacking,
        constraint=NumericConstraint(Comparison.EQ, Value.of(1)),
        text="if it is attacking alone",
    )
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.ATTACKS}),
                subject=CREATURES_YOU_CONTROL,
                intervening_if=alone,
                functions_in=BATTLEFIELD,
                text="whenever a creature you control attacks alone",
            ),
            Effect(
                EffectKind.MODIFY_PT,
                targets=attacking,
                amount=Value.of(1),
                amount2=Value.of(1),
                duration=int(Duration.END_OF_TURN),
            ),
            text=instance.text or "Exalted",
        ),
    )


@register("Annihilator")
def _annihilator(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.86a: whenever this attacks, defending player sacrifices N permanents."""
    return (
        _attack_trigger(
            instance,
            Effect(
                EffectKind.SACRIFICE,
                targets=ObjectFilter(
                    types_any=CardType.ARTIFACT
                    | CardType.CREATURE
                    | CardType.ENCHANTMENT
                    | CardType.LAND
                    | CardType.PLANESWALKER,
                    controller=ControllerRelation.OPPONENT,
                ),
                amount=Value.of(max(1, instance.amount)),
                text=f"sacrifice {max(1, instance.amount)} permanents",
            ),
        ),
    )


@register("Melee")
def _melee(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.121a: +1/+1 for each opponent you attacked this combat."""
    return (
        _attack_trigger(
            instance,
            Effect(
                EffectKind.MODIFY_PT,
                amount=Value.of(1),
                amount2=Value.of(1),
                duration=int(Duration.END_OF_TURN),
            ),
        ),
    )


@register("Mentor")
def _mentor(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.134a: put a +1/+1 counter on an attacking creature with lesser power."""
    weaker = ObjectFilter(
        types_all=CardType.CREATURE,
        attacking=True,
        other_than_source=True,
        controller=ControllerRelation.YOU,
    )
    return (
        _attack_trigger(
            instance,
            Effect(
                EffectKind.ADD_COUNTERS,
                targets=weaker,
                counter_type="+1/+1",
                amount=Value.of(1),
                is_targeted=True,
            ),
        ),
    )


@register("Training")
def _training(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.149a: attacking with a stronger creature grows this one."""
    return (
        _attack_trigger(
            instance,
            Effect(
                EffectKind.ADD_COUNTERS, counter_type="+1/+1", amount=Value.of(1)
            ),
        ),
    )


@register("Dethrone")
def _dethrone(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.105a: attacking the player with the most life adds a counter."""
    return (
        _attack_trigger(
            instance,
            Effect(
                EffectKind.ADD_COUNTERS, counter_type="+1/+1", amount=Value.of(1)
            ),
        ),
    )


@register("Afflict")
def _afflict(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.130a: whenever this becomes blocked, defending player loses N life."""
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.BECOMES_BLOCKED}),
                subject=ObjectFilter(source_only=True),
                functions_in=BATTLEFIELD,
                text="whenever this becomes blocked",
            ),
            Effect(
                EffectKind.LOSE_LIFE,
                players=EACH_OPPONENT,
                amount=Value.of(max(1, instance.amount)),
            ),
            text=instance.text or f"Afflict {instance.amount}",
        ),
    )


# ---------------------------------------------------------------------------
# Death triggers
# ---------------------------------------------------------------------------


def _dies_trigger(instance: KeywordInstance, *effects: Effect) -> Ability:
    return Ability.triggered(
        TriggerCondition(
            event_kinds=frozenset({EventKind.DIES}),
            subject=ObjectFilter(source_only=True, zones=frozenset()),
            # A dies trigger has to function from the graveyard as well, since
            # that is where the object is by the time it fires (CR 603.6e).
            functions_in=frozenset({Zone.BATTLEFIELD, Zone.GRAVEYARD}),
            uses_last_known_information=True,
            text=f"when this dies ({instance.name})",
        ),
        *effects,
        text=instance.text or instance.name,
    )


@register("Persist")
def _persist(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.79a: if it had no -1/-1 counter, return it with one."""
    no_minus = Condition(
        ConditionKind.OBJECT_COUNT,
        filter=ObjectFilter(source_only=True, has_counter="-1/-1"),
        constraint=NumericConstraint(Comparison.EQ, Value.of(0)),
        text="if it had no -1/-1 counter on it",
    )
    return (
        _dies_trigger(
            instance,
            Effect(
                EffectKind.CONDITIONAL,
                condition=no_minus,
                # It returns *with* the counter. A separate "put a counter on
                # it" had no object to act on but the source - the card left
                # in the graveyard - so the creature came back without one
                # and could return again and again.
                children=(
                    Effect(
                        EffectKind.PUT_ONTO_BATTLEFIELD,
                        counter_type="-1/-1",
                        amount=Value.of(1),
                        text="return it with a -1/-1 counter on it",
                    ),
                ),
            ),
        ),
    )


@register("Undying")
def _undying(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.93a: the +1/+1 mirror of Persist."""
    no_plus = Condition(
        ConditionKind.OBJECT_COUNT,
        filter=ObjectFilter(source_only=True, has_counter="+1/+1"),
        constraint=NumericConstraint(Comparison.EQ, Value.of(0)),
        text="if it had no +1/+1 counter on it",
    )
    return (
        _dies_trigger(
            instance,
            Effect(
                EffectKind.CONDITIONAL,
                condition=no_plus,
                # It returns *with* the counter. A separate "put a counter on
                # it" had no object to act on but the source - the card left
                # in the graveyard - so the creature came back without one
                # and could return again and again.
                children=(
                    Effect(
                        EffectKind.PUT_ONTO_BATTLEFIELD,
                        counter_type="+1/+1",
                        amount=Value.of(1),
                        text="return it with a +1/+1 counter on it",
                    ),
                ),
            ),
        ),
    )


@register("Afterlife")
def _afterlife(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.135a: create N 1/1 white and black Spirit tokens with flying."""
    from ..cr600_spells_and_abilities.effects import TokenSpec
    from ..kernel.enums import Color

    spirit = TokenSpec(
        types=CardType.CREATURE,
        subtypes=("Spirit",),
        colors=Color.WHITE | Color.BLACK,
        power=Value.of(1),
        toughness=Value.of(1),
        keywords=("Flying",),
    )
    return (
        _dies_trigger(
            instance,
            Effect(
                EffectKind.CREATE_TOKEN,
                token=spirit,
                amount=Value.of(max(1, instance.amount)),
                players=YOU,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Enters-the-battlefield counters
# ---------------------------------------------------------------------------


def _enters_with_counters(
    instance: KeywordInstance, counter: str, amount: Value
) -> Ability:
    """A replacement effect that modifies how the permanent enters (CR 614.1c)."""
    return Ability.static(
        Effect(
            EffectKind.ADD_COUNTERS,
            counter_type=counter,
            amount=amount,
            text=f"enters with {amount} {counter} counters",
        ),
        text=instance.text or instance.name,
    )


@register("Modular")
def _modular(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.43a: enters with N +1/+1 counters, and moves them on death.

    Two abilities from one keyword, which is exactly why ``build`` returns a
    tuple.
    """
    amount = Value.of(max(1, instance.amount))
    move = _dies_trigger(
        instance,
        Effect(
            EffectKind.ADD_COUNTERS,
            targets=ObjectFilter(
                types_all=CardType.ARTIFACT | CardType.CREATURE,
                other_than_source=True,
            ),
            counter_type="+1/+1",
            amount=Value(ValueKind.COUNTERS, counter_type="+1/+1", of_affected=False),
            is_targeted=True,
        ),
    )
    return (_enters_with_counters(instance, "+1/+1", amount), move)


@register("Graft")
def _graft(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.58a: enters with N +1/+1 counters."""
    return (
        _enters_with_counters(instance, "+1/+1", Value.of(max(1, instance.amount))),
    )


@register("Fading")
def _fading(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.32a: enters with N fade counters; upkeep removes one or sacrifices."""
    return (
        _enters_with_counters(instance, "fade", Value.of(max(1, instance.amount))),
        _upkeep_counter_sacrifice(instance, "fade"),
    )


@register("Vanishing")
def _vanishing(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.63a: the time-counter version of Fading."""
    return (
        _enters_with_counters(instance, "time", Value.of(max(1, instance.amount))),
        _upkeep_counter_sacrifice(instance, "time"),
    )


def _upkeep_counter_sacrifice(instance: KeywordInstance, counter: str) -> Ability:
    return Ability.triggered(
        TriggerCondition(
            event_kinds=frozenset({EventKind.UPKEEP}),
            players=YOU,
            functions_in=BATTLEFIELD,
            text="at the beginning of your upkeep",
        ),
        Effect(
            EffectKind.REMOVE_COUNTERS, counter_type=counter, amount=Value.of(1)
        ),
        Effect(
            EffectKind.CONDITIONAL,
            condition=Condition(
                ConditionKind.OBJECT_COUNT,
                filter=ObjectFilter(source_only=True, has_counter=counter),
                constraint=NumericConstraint(Comparison.EQ, Value.of(0)),
            ),
            children=(Effect(EffectKind.SACRIFICE),),
        ),
        text=f"{instance.name} upkeep",
    )


@register("Bloodthirst")
def _bloodthirst(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.54a: enters with N +1/+1 counters if an opponent was dealt damage."""
    return (
        Ability.static(
            Effect(
                EffectKind.ADD_COUNTERS,
                counter_type="+1/+1",
                amount=Value.of(max(1, instance.amount)),
                condition=Condition(
                    ConditionKind.EVENT_THIS_TURN,
                    text="if an opponent was dealt damage this turn",
                ),
            ),
            text=instance.text or instance.name,
        ),
    )


# ---------------------------------------------------------------------------
# Cost keywords (CR 118.8, 118.9)
# ---------------------------------------------------------------------------


@register("Kicker", "Multikicker")
def _kicker(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.33a: an optional additional cost."""
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            additional_cost=AdditionalCost(
                cost=instance.cost or Cost(()),
                optional=True,
                keyword=instance.name,
                text=instance.text or instance.name,
            ),
            functions_in=frozenset({Zone.HAND, Zone.STACK}),
            text=instance.text or instance.name,
        ),
    )


@register("Casualty", "Bargain")
def _mandatory_additional(instance: KeywordInstance) -> tuple[Ability, ...]:
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            additional_cost=AdditionalCost(
                cost=instance.cost or Cost(()),
                optional=True,
                keyword=instance.name,
                text=instance.text or instance.name,
            ),
            functions_in=frozenset({Zone.HAND, Zone.STACK}),
            text=instance.text or instance.name,
        ),
    )


def _alternative(instance: KeywordInstance, zone: Zone | None) -> tuple[Ability, ...]:
    """An alternative cost, as a static ability working where the card is.

    CR 604.5: "you may pay [cost] rather than pay this object's mana cost"
    works while the spell is on the stack, which is where the payment is
    happening - hence the zone the card is cast from *and* the stack.
    CR 604.6: that zone is the other half. A static ability of this shape is
    one of the few that work from a zone the card is merely sitting in, so
    Flashback reaches a graveyard while an ordinary ability does not.
    """
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            alternative_cost=AlternativeCost(
                cost=instance.cost or Cost(()),
                from_zone=zone,
                keyword=instance.name,
                text=instance.text or instance.name,
            ),
            functions_in=frozenset({zone}) if zone is not None else HAND,
            text=instance.text or instance.name,
        ),
    )


@register("Flashback", "Jump-start", "Retrace", "Escape", "Aftermath", "Encore")
def _graveyard_alternative(instance: KeywordInstance) -> tuple[Ability, ...]:
    """Alternative costs that let a card be cast from a graveyard (CR 702.34)."""
    return _alternative(instance, Zone.GRAVEYARD)


@register("Dash", "Blitz", "Spectacle", "Prowl", "Surge", "Evoke", "Madness", "Mayhem")
def _hand_alternative(instance: KeywordInstance) -> tuple[Ability, ...]:
    """Alternative costs paid instead of the mana cost, from hand."""
    return _alternative(instance, None)


@register("Morph", "Megamorph", "Disguise")
def _morph(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.37a: morph is two abilities that look nothing alike.

    The first is a static ability functioning from hand: cast this card face
    down as a 2/2 colourless creature with no name, no types and no text, for
    {3}. That is an alternative cost, and the engine already knows what to do
    with one.

    The second is the permission to turn it face up for its morph cost - and
    that is a *special action* (CR 116.2b), not an activated ability. It uses
    no stack, so it cannot be responded to, which is the entire reason anyone
    plays morph creatures. It lives in ``special_actions``, which reads this
    ability off the card to find the cost; the ability is registered here so
    that the cost is attached to something.

    Disguise (702.168a) differs only in that the face-down creature has ward
    {2}; megamorph (702.37b) in that turning it up leaves a +1/+1 counter.
    Both of those live where they take effect rather than here.
    """
    face_down_cost = Cost((CostComponent(CostKind.MANA, mana=ManaCost.parse("{3}")),))
    abilities = [
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            alternative_cost=AlternativeCost(
                cost=face_down_cost,
                from_zone=Zone.HAND,
                keyword=instance.name,
                text=f"cast face down as a 2/2 for {{3}} ({instance.name})",
            ),
            functions_in=HAND,
            text=instance.text or instance.name,
        ),
        # The turn-face-up permission. Its cost is what the special action
        # charges; it is deliberately not an ACTIVATED ability, because an
        # activated ability would use the stack.
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            cost=instance.cost or Cost(()),
            functions_in=frozenset({Zone.BATTLEFIELD}),
            text=f"turn face up: {instance.cost or 'its morph cost'}",
        ),
    ]
    if instance.key == "disguise":
        # CR 702.168a: a disguised creature is face down *with ward {2}*.
        abilities.append(
            Ability.static(
                Effect(
                    EffectKind.GRANT_ABILITY,
                    targets=ObjectFilter(source_only=True, face_down=True),
                    granted_abilities=build(KeywordInstance("Ward", cost=Cost((
                        CostComponent(CostKind.MANA, mana=ManaCost.parse("{2}")),
                    )))),
                ),
                text="this creature has ward {2} while face down",
            )
        )
    return tuple(abilities)


@register("Convoke", "Delve", "Improvise")
def _cost_helper(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.51, 702.66, 702.126: alternate ways to pay part of the cost.

    Registered as a keyword the payment step consults rather than as a cost
    reduction, because they change *what may be spent*, not what is owed - the
    spell still has its full mana value, which matters for everything that
    asks.
    """
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            functions_in=frozenset({Zone.HAND, Zone.STACK}),
            text=instance.text or instance.name,
        ),
    )


# ---------------------------------------------------------------------------
# Activated-ability keywords
# ---------------------------------------------------------------------------


@register("Equip")
def _equip(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.6a: attach to a creature you control. Sorcery speed only."""
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=(
                Effect(
                    EffectKind.ATTACH,
                    targets=CREATURES_YOU_CONTROL,
                    is_targeted=True,
                    text="attach to target creature you control",
                ),
            ),
            cost=instance.cost or Cost(()),
            timing=Timing.SORCERY,
            keyword="Equip",
            text=instance.text or "Equip",
        ),
    )


def _discard_this_card(cost: Cost | None) -> Cost:
    """A keyword's cost with "Discard this card" on the end.

    The parser hands over only what is printed after the keyword - the {2} of
    "Cycling {2}" - and the discard lives in the reminder text. Without it the
    card never leaves hand and the ability can be activated again and again.
    It names the source, so payment discards this card rather than whichever
    card the player would rather lose.
    """
    cost = cost or Cost(())
    if any(
        c.kind is CostKind.DISCARD and c.filter is not None and c.filter.source_only
        for c in cost.components
    ):
        return cost
    return cost.with_component(
        CostComponent(
            CostKind.DISCARD,
            filter=SOURCE_ONLY,
            amount=Value.of(1),
            text="discard this card",
        )
    )


@register("Cycling")
def _cycling(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.29a: discard this card to draw a card. Functions from hand."""
    cost = _discard_this_card(instance.cost)
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=(Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1)),),
            cost=cost,
            keyword="Cycling",
            functions_in=HAND,
            text=instance.text or "Cycling",
        ),
    )


@register("Crew")
def _crew(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.122a: tap creatures with total power N to animate the Vehicle."""
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=(
                Effect(
                    EffectKind.ADD_TYPE,
                    types=CardType.CREATURE,
                    duration=int(Duration.END_OF_TURN),
                    text="becomes an artifact creature until end of turn",
                ),
            ),
            cost=Cost(
                (
                    CostComponent(
                        CostKind.TAP_OTHER,
                        amount=Value.of(max(1, instance.amount)),
                        filter=CREATURES_YOU_CONTROL,
                        text=f"tap creatures with total power {instance.amount}",
                    ),
                )
            ),
            keyword="Crew",
            text=instance.text or f"Crew {instance.amount}",
        ),
    )


@register("Unearth")
def _unearth(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.84a: "[Cost]: Return this card from your graveyard to the
    battlefield. It gains haste. Exile it at the beginning of the next end
    step. If it would leave the battlefield, exile it instead of putting it
    anywhere else. Activate only as a sorcery."

    Four sentences, and the first two alone are a reanimation spell: without
    the other two a Dregscape Zombie stayed for good. "It" is the permanent
    the card became, which a source-only filter finds across that one zone
    change. The card itself is looked for in the graveyard only, so a card
    exiled in response is a new object (CR 400.7) and nothing comes back.
    """
    from ..cr600_spells_and_abilities.cr614_replacement import ReplacementKind

    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=(
                Effect(
                    EffectKind.PUT_ONTO_BATTLEFIELD,
                    targets=ObjectFilter(source_only=True, zones=GRAVEYARD),
                    from_zone=Zone.GRAVEYARD,
                    text="return this card from your graveyard to the battlefield",
                ),
                # No duration: the rule says "it gains haste", not "until end
                # of turn". It is gone by then anyway unless the exile is
                # stopped, and then it keeps haste.
                Effect(
                    EffectKind.GRANT_ABILITY,
                    targets=SOURCE_ONLY,
                    keywords=("Haste",),
                    duration=int(Duration.PERMANENT),
                    text="it gains haste",
                ),
                _delayed(
                    TriggerCondition(
                        event_kinds=frozenset({EventKind.END_STEP}),
                        text="at the beginning of the next end step",
                    ),
                    Effect(EffectKind.EXILE, targets=SOURCE_ONLY, text="exile it"),
                    text="exile it at the beginning of the next end step",
                ),
                Effect(
                    EffectKind.REPLACEMENT,
                    targets=SOURCE_ONLY,
                    zone=Zone.EXILE,
                    replacement_kind=int(ReplacementKind.REDIRECT_ZONE_CHANGE),
                    text="if it would leave the battlefield, exile it instead",
                ),
            ),
            cost=instance.cost or Cost(()),
            timing=Timing.SORCERY,
            keyword="Unearth",
            functions_in=GRAVEYARD,
            text=instance.text or "Unearth",
        ),
    )


# ---------------------------------------------------------------------------
# Spell-cast triggers
# ---------------------------------------------------------------------------


@register("Prowess")
def _prowess(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.108a: +1/+1 until end of turn whenever you cast a noncreature spell."""
    noncreature = ObjectFilter(
        types_none=CardType.CREATURE, zones=frozenset({Zone.STACK})
    )
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.CAST_SPELL}),
                subject=noncreature,
                players=YOU,
                functions_in=BATTLEFIELD,
                text="whenever you cast a noncreature spell",
            ),
            Effect(
                EffectKind.MODIFY_PT,
                amount=Value.of(1),
                amount2=Value.of(1),
                duration=int(Duration.END_OF_TURN),
            ),
            text=instance.text or "Prowess",
        ),
    )


@register("Extort")
def _extort(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.101a: whenever you cast a spell, you may pay {W/B} to drain."""
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.CAST_SPELL}),
                players=YOU,
                functions_in=BATTLEFIELD,
                text="whenever you cast a spell",
            ),
            Effect(
                EffectKind.OPTIONAL,
                children=(
                    Effect(
                        EffectKind.LOSE_LIFE,
                        players=EACH_OPPONENT,
                        amount=Value.of(1),
                    ),
                    Effect(EffectKind.GAIN_LIFE, players=YOU, amount=Value.of(1)),
                ),
            ),
            text=instance.text or "Extort",
        ),
    )


@register("Evolve")
def _evolve(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.100a: a bigger creature entering puts a +1/+1 counter on this."""
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
                subject=CREATURES_YOU_CONTROL,
                functions_in=BATTLEFIELD,
                text="whenever a creature you control enters",
            ),
            Effect(
                EffectKind.ADD_COUNTERS, counter_type="+1/+1", amount=Value.of(1)
            ),
            text=instance.text or "Evolve",
        ),
    )


# ---------------------------------------------------------------------------
# Protection, ward, and landwalk (CR 702.14, 702.16, 702.21)
# ---------------------------------------------------------------------------


@register("Protection", "Hexproof from")
def _protection(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.16a: "protection from [quality]", and the quality is load-bearing.

    CR 702.16b-e is the DEBT rule: it can't be Damaged, Enchanted or Equipped,
    Blocked, or Targeted by anything with the stated quality. All four consult
    ``quality``; a blanket ban would stop a white spell with protection from
    red.
    """
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            quality=instance.filter,
            text=instance.text or instance.name,
        ),
    )


@register("Ward")
def _ward(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.21a: ward is a *triggered* ability, not a targeting ban.

    "Whenever this becomes the target of a spell or ability an opponent
    controls, counter it unless that player pays [cost]." Modelling it as a
    targeting restriction would wrongly stop the spell being cast at all,
    rather than taxing it - and would make an unpaid ward uncounterable.
    """
    ward = Ability.triggered(
        TriggerCondition(
            event_kinds=frozenset({EventKind.TARGETED}),
            subject=ObjectFilter(source_only=True),
            functions_in=BATTLEFIELD,
            text="whenever this becomes the target of an opponent's spell",
        ),
            Effect(
                EffectKind.COUNTER_SPELL,
                targets=ObjectFilter(zones=frozenset({Zone.STACK})),
                condition=Condition(
                    ConditionKind.UNPARSED, text="unless that player pays the ward cost"
                ),
            text="counter it unless its controller pays the ward cost",
        ),
        text=instance.text or "Ward",
    )
    # The cost rides on the ability even though this is a trigger, because the
    # "unless that player pays" clause has to be able to read it.
    from dataclasses import replace as _replace

    return (_replace(ward, cost=instance.cost or Cost(()), keyword="Ward"),)


#: CR 702.14: landwalk, one per basic land type plus the general forms. Each is
#: "can't be blocked as long as defending player controls a [type]", which is a
#: property of the *defender's* board rather than of any blocker.
_LANDWALK_TYPES = {
    "plainswalk": "Plains",
    "islandwalk": "Island",
    "swampwalk": "Swamp",
    "mountainwalk": "Mountain",
    "forestwalk": "Forest",
    "desertwalk": "Desert",
}


@register(
    "Landwalk", "Plainswalk", "Islandwalk", "Swampwalk", "Mountainwalk",
    "Forestwalk", "Desertwalk", "Legendary landwalk", "Nonbasic landwalk",
)
def _landwalk(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.14c."""
    from ..kernel.enums import Supertype

    key = instance.key
    if instance.filter is not None:
        quality = instance.filter
    elif key in _LANDWALK_TYPES:
        quality = ObjectFilter(
            types_all=CardType.LAND, subtypes_any=(_LANDWALK_TYPES[key],)
        )
    elif key == "legendary landwalk":
        quality = ObjectFilter(
            types_all=CardType.LAND, supertypes_all=Supertype.LEGENDARY
        )
    elif key == "nonbasic landwalk":
        quality = ObjectFilter(
            types_all=CardType.LAND, supertypes_none=Supertype.BASIC
        )
    else:
        quality = ObjectFilter(types_all=CardType.LAND)

    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            quality=quality,
            text=instance.text or instance.name,
        ),
    )


# ---------------------------------------------------------------------------
# Poison and -1/-1 damage (CR 702.80, 702.90, 702.164)
# ---------------------------------------------------------------------------


@register("Infect", "Wither")
def _infect(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.90a / 702.80a: damage as -1/-1 counters, and to players as poison.

    Both are static abilities the damage routine consults, because the change
    happens as the damage is dealt rather than afterwards - it is still damage,
    and everything watching for damage still sees it.
    """
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


@register("Toxic", "Poisonous")
def _toxic(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.164a / 702.70a: combat damage to a player also gives poison."""
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.COMBAT_DAMAGE_DEALT}),
                source=ObjectFilter(source_only=True),
                players=EACH_OPPONENT,
                functions_in=BATTLEFIELD,
                text="whenever this deals combat damage to a player",
            ),
            Effect(
                EffectKind.ADD_POISON,
                players=EACH_OPPONENT,
                amount=Value.of(max(1, instance.amount)),
            ),
            text=instance.text or f"{instance.name} {instance.amount}",
        ),
    )


@register("Phasing")
def _phasing(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.26: phases out and back during its controller's untap steps."""
    return (
        Ability(AbilityKind.STATIC, keyword="Phasing", text=instance.text or "Phasing"),
    )


# ---------------------------------------------------------------------------
# The cycling family (CR 702.29)
# ---------------------------------------------------------------------------

_CYCLING_SEARCH = {
    "plainscycling": "Plains",
    "islandcycling": "Island",
    "swampcycling": "Swamp",
    "mountaincycling": "Mountain",
    "forestcycling": "Forest",
    "slivercycling": "Sliver",
    "wizardcycling": "Wizard",
}


@register(
    "Basic landcycling", "Landcycling", "Typecycling", "Plainscycling",
    "Islandcycling", "Swampcycling", "Mountaincycling", "Forestcycling",
    "Slivercycling", "Wizardcycling",
)
def _typecycling(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.29e: discard this card to search for a card of the named type.

    A variant of Cycling that fetches instead of drawing, so it shares the
    discard-as-cost shape and differs only in the effect.
    """
    from ..kernel.enums import Supertype

    key = instance.key
    if key in _CYCLING_SEARCH:
        wanted = ObjectFilter(
            subtypes_any=(_CYCLING_SEARCH[key],), zones=frozenset({Zone.LIBRARY})
        )
    elif key == "basic landcycling":
        wanted = ObjectFilter(
            types_all=CardType.LAND,
            supertypes_all=Supertype.BASIC,
            zones=frozenset({Zone.LIBRARY}),
        )
    else:
        wanted = ObjectFilter(types_all=CardType.LAND, zones=frozenset({Zone.LIBRARY}))

    cost = _discard_this_card(instance.cost)
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=(
                Effect(
                    EffectKind.SEARCH_LIBRARY,
                    targets=wanted,
                    amount=Value.of(1),
                    players=YOU,
                    text="search your library for a card",
                ),
            ),
            cost=cost,
            keyword=instance.name,
            functions_in=HAND,
            text=instance.text or instance.name,
        ),
    )


# ---------------------------------------------------------------------------
# Remaining combat triggers
# ---------------------------------------------------------------------------


@register("Bushido", "Rampage", "Flanking", "Frenzy", "Renown", "Enlist", "Mobilize",
          "Myriad", "Double team", "Undaunted", "Teamwork", "Provoke", "Firebending",
          "Increment", "Intensity")
def _misc_combat(instance: KeywordInstance) -> tuple[Ability, ...]:
    """Combat keywords that all reduce to "when this attacks or blocks, do N".

    Grouped because the engine hook is identical; the differences between them
    live in the amount and in which event they watch, both carried by the
    instance.
    """
    amount = Value.of(max(1, instance.amount))
    if instance.key in ("renown", "mobilize", "myriad", "double team", "teamwork"):
        return (
            _attack_trigger(
                instance,
                Effect(
                    EffectKind.ADD_COUNTERS, counter_type="+1/+1", amount=amount
                ),
            ),
        )
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.ATTACKS, EventKind.BLOCKS}),
                subject=ObjectFilter(source_only=True),
                functions_in=BATTLEFIELD,
                text=f"whenever this attacks or blocks ({instance.name})",
            ),
            Effect(
                EffectKind.MODIFY_PT,
                amount=amount,
                amount2=amount,
                duration=int(Duration.END_OF_TURN),
            ),
            text=instance.text or instance.name,
        ),
    )


# ---------------------------------------------------------------------------
# Remaining cast/ETB triggers
# ---------------------------------------------------------------------------


@register("Cascade", "Storm", "Gravestorm", "Ripple")
def _cast_copy_trigger(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.85 / 702.40: triggers on this spell being cast, from the stack."""
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.CAST_SPELL}),
                subject=ObjectFilter(source_only=True, zones=frozenset({Zone.STACK})),
                functions_in=frozenset({Zone.STACK}),
                text=f"when you cast this spell ({instance.name})",
            ),
            Effect(
                EffectKind.COPY_SPELL,
                targets=ObjectFilter(source_only=True, zones=frozenset({Zone.STACK})),
                amount=Value.of(max(1, instance.amount)),
                text=instance.text or instance.name,
            )
            if instance.key in ("storm", "gravestorm")
            else Effect(EffectKind.UNPARSED, text=instance.name),
            text=instance.text or instance.name,
        ),
    )


@register("Exploit", "Soulbond", "Soulshift", "Champion", "Tribute", "Unleash",
          "Riot", "Demonstrate", "Ravenous", "Backup", "For Mirrodin!", "Hideaway",
          "Ingest", "Awaken", "Amplify", "Devour", "Fabricate", "Sunburst",
          )
def _etb_trigger(instance: KeywordInstance) -> tuple[Ability, ...]:
    """Keywords that trigger, or modify how the permanent enters.

    Grouped by shape: all of them fire on this permanent entering the
    battlefield, and differ only in what they then do.
    """
    amount = Value.of(max(1, instance.amount))
    if instance.key in ("amplify", "devour", "fabricate", "sunburst", "backup"):
        return (
            Ability.static(
                Effect(
                    EffectKind.ADD_COUNTERS, counter_type="+1/+1", amount=amount
                ),
                text=instance.text or instance.name,
            ),
        )
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
                subject=ObjectFilter(source_only=True),
                functions_in=BATTLEFIELD,
                text=f"when this enters ({instance.name})",
            ),
            Effect(EffectKind.UNPARSED, text=instance.name),
            text=instance.text or instance.name,
        ),
    )


@register("Echo", "Cumulative upkeep", "Suspend")
def _upkeep_keyword(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.30 / 702.24: upkeep-triggered costs and counters."""
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.UPKEEP}),
                players=YOU,
                functions_in=BATTLEFIELD,
                text="at the beginning of your upkeep",
            ),
            Effect(EffectKind.UNPARSED, text=instance.name),
            text=instance.text or instance.name,
        ),
    )


# ---------------------------------------------------------------------------
# Remaining alternative and additional costs
# ---------------------------------------------------------------------------


@register("Overload", "Foretell", "Emerge", "Freerunning", "Bestow", "Miracle",
          "Warp", "Impending", "Offering", "Assist", "Cleave", "Gift", "Disturb",
          "Embalm", "Eternalize", "Mutate", "Prototype")
def _more_alternatives(instance: KeywordInstance) -> tuple[Ability, ...]:
    zone = Zone.GRAVEYARD if instance.key in ("disturb", "embalm", "eternalize") else None
    return _alternative(instance, zone)


@register("Entwine", "Escalate", "Replicate", "Conspire", "Splice", "Squad",
          "Spree", "Tiered", "Offspring", "Buyback")
def _more_additionals(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 118.8: optional additional costs that change what the spell does."""
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            additional_cost=AdditionalCost(
                cost=instance.cost or Cost(()),
                optional=True,
                keyword=instance.name,
                text=instance.text or instance.name,
            ),
            functions_in=frozenset({Zone.HAND, Zone.STACK}),
            text=instance.text or instance.name,
        ),
    )


@register("Affinity")
def _affinity(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.41: this spell costs {1} less for each matching permanent."""
    return (
        Ability.static(
            Effect(
                EffectKind.MODIFY_COST,
                targets=ObjectFilter(source_only=True, zones=frozenset({Zone.STACK})),
                # Negative, because the engine's convention is
                # negative-is-cheaper and Affinity makes a spell *cheaper*.
                # A bare COUNT is positive, which read as a cost increase.
                amount=Value(
                    ValueKind.PRODUCT,
                    constant=-1,
                    operands=(
                        Value(
                            ValueKind.COUNT,
                            filter=instance.filter
                            or ObjectFilter(
                                types_all=CardType.ARTIFACT,
                                controller=ControllerRelation.YOU,
                            ),
                        ),
                    ),
                ),
                text=instance.text or instance.name,
            ),
            text=instance.text or instance.name,
        ),
    )


# ---------------------------------------------------------------------------
# Remaining activated abilities
# ---------------------------------------------------------------------------


@register("Fortify", "Reconfigure")
def _fortify(instance: KeywordInstance) -> tuple[Ability, ...]:
    """The Equip pattern, applied to lands and to Equipment Creatures."""
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=(
                Effect(
                    EffectKind.ATTACH,
                    targets=ObjectFilter(
                        types_all=CardType.LAND
                        if instance.key == "fortify"
                        else CardType.CREATURE,
                        controller=ControllerRelation.YOU,
                    ),
                    is_targeted=True,
                ),
            ),
            cost=instance.cost or Cost(()),
            timing=Timing.SORCERY,
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


@register("Start your engines!")
def _start_your_engines(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.179a: a static ability, applied as a state-based action.

    "If a player controls a permanent with start your engines! and that player
    has no speed, their speed becomes 1. This is a state-based action."

    So there is nothing for the ability itself to do: it is a marker the
    state-based action looks for, and CR 704.5aa in ``cr704_sba`` is what
    actually starts the engine. Modelling it as an enters-the-battlefield
    trigger instead put it on the stack, where it could be responded to and
    countered, and left a permanent that arrived any other way - reanimated,
    blinked, a token copy, a change of control - never starting at all.
    """
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


@register("Station")
def _station(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 721: "Tap another untapped creature you control: Put charge counters
    equal to that creature's power on this Spacecraft. Station only as a
    sorcery."

    Three things this shares with no other keyword, and all three were wrong
    when it was lumped in with the generic counter-adders:

    * the cost taps *another* creature, not this permanent, so the Spacecraft
      itself may be summoning sick and it still works;
    * the creature tapped may itself be summoning sick, because CR 302.6
      restricts only the {T} symbol in a permanent's own cost;
    * the number of counters is that creature's power, not one.
    """
    from ..cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind
    from ..cr600_spells_and_abilities.effects import Effect, EffectKind
    from ..kernel.enums import CardType
    from ..kernel.query import ControllerRelation, ObjectFilter, Value, ValueKind

    creature = ObjectFilter(
        types_all=CardType.CREATURE,
        controller=ControllerRelation.YOU,
        other_than_source=True,
        tapped=False,
    )
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=(
                Effect(
                    EffectKind.ADD_COUNTERS,
                    counter_type="charge",
                    amount=Value(kind=ValueKind.COST_PAID_POWER),
                ),
            ),
            cost=Cost(
                (
                    CostComponent(
                        CostKind.TAP_OTHER,
                        filter=creature,
                        amount=Value.of(1),
                        text="tap another untapped creature you control",
                    ),
                )
            ),
            timing=Timing.SORCERY,
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


@register("Outlast", "Level Up", "Boast", "Exhaust", "Saddle",
          "Harmonize", "Craft", "Job select", "Specialize")
def _counter_activated(instance: KeywordInstance) -> tuple[Ability, ...]:
    """Activated keywords whose effect is putting counters on this permanent."""
    counter = "level" if instance.key == "level up" else "+1/+1"
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=(
                Effect(
                    EffectKind.ADD_COUNTERS,
                    counter_type=counter,
                    amount=Value.of(max(1, instance.amount)),
                ),
            ),
            cost=instance.cost or Cost(()),
            timing=Timing.SORCERY,
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


@register("Transfigure", "Scavenge", "Reinforce", "Channel", "Forecast", "Aura Swap")
def _zone_activated(instance: KeywordInstance) -> tuple[Ability, ...]:
    """Activated keywords that function from hand or graveyard (CR 113.6)."""
    zone = (
        Zone.GRAVEYARD
        if instance.key in ("scavenge", "unearth")
        else Zone.HAND
    )
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=(Effect(EffectKind.UNPARSED, text=instance.name),),
            cost=instance.cost or Cost(()),
            keyword=instance.name,
            functions_in=frozenset({zone}),
            text=instance.text or instance.name,
        ),
    )


# ---------------------------------------------------------------------------
# Remaining statics and state
# ---------------------------------------------------------------------------


@register("Living metal", "Living weapon", "Umbra armor", "Compleated", "Solved",
          "Max speed", "Epic", "Daybound", "Nightbound",
          "Ascend", "Companion", "Double agenda",
          "Hidden agenda", "Absorb", "Cipher", "Fuse", "Recover", "Rebound",
          "Haunt", "Read Ahead", "More Than Meets the Eye", "Augment", "Dredge",
)
def _remaining_statics(instance: KeywordInstance) -> tuple[Ability, ...]:
    """Keywords whose behaviour is a static the engine reads by name.

    Registered so a card carrying them is not treated as unreadable. The
    behaviour beyond "the object has this keyword" is not modelled for all of
    them yet, which the coverage report reflects.
    """
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


# ---------------------------------------------------------------------------
# Keywords whose behaviour is a shape the engine already executes
#
# Everything below replaces a catch-all bucket. Each one is the CR's own
# wording turned into effects, and the docstring quotes the rule it came from
# so a wrong expansion is visible without opening the rulebook.
# ---------------------------------------------------------------------------


SOURCE_ONLY = ObjectFilter(source_only=True)
#: CR 603.6c: the events a leaves-the-battlefield trigger watches.
LEAVE_BATTLEFIELD_TRIGGER = LEAVE_BATTLEFIELD_KINDS


def _body(instance: KeywordInstance) -> tuple[Effect, ...]:
    """The effects a keyword wraps, or a loud placeholder if there are none.

    Channel, forecast and "max speed" are grammar, not behaviour: "[Cost],
    Discard this card: [effect]" is a shape, and the effect inside it is
    ordinary card text. The shape belongs here and the body belongs to the
    parser, so a builder called without one reports the gap rather than
    guessing at it.
    """
    if instance.effects:
        return instance.effects
    return (Effect(EffectKind.UNPARSED, text=instance.text or instance.name),)


def _amount(instance: KeywordInstance, default: int = 1) -> Value:
    """The keyword's numeric parameter - "absorb 2", "soulshift 4"."""
    return Value.of(instance.amount if instance.amount else default)

CREATURES_YOU_CONTROL = ObjectFilter(
    types_all=CardType.CREATURE, controller=ControllerRelation.YOU
)


def _delayed(trigger: TriggerCondition, *effects: Effect, text: str = "") -> Effect:
    return Effect(
        EffectKind.DELAYED_TRIGGER,
        trigger=trigger,
        children=effects,
        text=text,
    )


def _at_next_upkeep(text: str = "at the beginning of your next upkeep") -> TriggerCondition:
    return TriggerCondition(
        event_kinds=frozenset({EventKind.UPKEEP}),
        players=PlayerFilter(PlayerScope.YOU),
        text=text,
    )


# -- damage prevention -------------------------------------------------------


@register("Absorb")
def _absorb(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.64a: "If a source would deal damage to this creature, prevent N
    of that damage."

    A prevention shield that never runs out - it applies to every source
    separately and resets nothing, which is why absorb 1 blanks an army of
    1/1s rather than one of them.
    """
    return (
        Ability.static(
            Effect(
                EffectKind.PREVENT_DAMAGE,
                targets=SOURCE_ONLY,
                amount=_amount(instance),
                duration=Duration.PERMANENT,
                text=f"prevent {instance.amount} damage from each source",
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Umbra armor")
def _umbra_armor(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.89a (totem armor): "If enchanted permanent would be destroyed,
    instead remove all damage from it and destroy this Aura."

    A replacement effect on the *enchanted* permanent, not on the Aura, which
    is why totem armor saves a creature from a wrath and dies doing it.
    """
    return (
        Ability.static(
            Effect(
                EffectKind.REPLACEMENT,
                targets=ObjectFilter(attached_to=SOURCE_ONLY),
                children=(
                    Effect(
                        EffectKind.DESTROY,
                        targets=SOURCE_ONLY,
                        text="destroy this Aura instead",
                    ),
                ),
                duration=Duration.WHILE_SOURCE_PERSISTS,
                text="totem armor",
            ),
            text=instance.text or instance.name,
        ),
    )


# -- enters-the-battlefield triggers ----------------------------------------


@register("Living weapon")
def _living_weapon(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.92a: "When this Equipment enters, create a 0/0 black Phyrexian
    Germ creature token, then attach this to it."

    The Germ is 0/0 and survives only because the Equipment is attached: take
    the Equipment away and a state-based action kills it.
    """
    germ = TokenSpec(
        name="Germ",
        types=CardType.CREATURE,
        subtypes=("Phyrexian", "Germ"),
        colors=Color.BLACK,
        power=Value.of(0),
        toughness=Value.of(0),
    )
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
                subject=SOURCE_ONLY,
                text="when this Equipment enters",
            ),
            Effect(EffectKind.CREATE_TOKEN, token=germ, players=YOU),
            Effect(
                EffectKind.ATTACH,
                targets=SOURCE_ONLY,
                text="attach this to it",
            ),
            text=instance.text or instance.name,
        ),
    )


@register("For Mirrodin!")
def _for_mirrodin(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.163a: "When this Equipment enters, create a 2/2 red Rebel
    creature token, then attach this to it."
    """
    rebel = TokenSpec(
        name="Rebel",
        types=CardType.CREATURE,
        subtypes=("Rebel",),
        colors=Color.RED,
        power=Value.of(2),
        toughness=Value.of(2),
    )
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
                subject=SOURCE_ONLY,
                text="when this Equipment enters",
            ),
            Effect(EffectKind.CREATE_TOKEN, token=rebel, players=YOU),
            Effect(EffectKind.ATTACH, targets=SOURCE_ONLY, text="attach this to it"),
            text=instance.text or instance.name,
        ),
    )


@register("Exploit")
def _exploit(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.110a: "When this creature enters, you may sacrifice a creature."

    The sacrifice is optional and is its own trigger; whatever the card does
    *because* it exploited is a separate "when this exploits a creature"
    trigger printed on the card, which the parser will read.
    """
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
                subject=SOURCE_ONLY,
                text="when this creature enters",
            ),
            Effect(
                EffectKind.OPTIONAL,
                children=(
                    Effect(
                        EffectKind.SACRIFICE,
                        targets=CREATURES_YOU_CONTROL,
                        text="sacrifice a creature",
                    ),
                ),
                text="you may sacrifice a creature",
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Champion")
def _champion(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.72a: "When this permanent enters, sacrifice it unless you exile
    another [quality] you control. When this permanent leaves the battlefield,
    that card returns to the battlefield."

    Two linked abilities (CR 607), which is why blinking the championing
    creature returns the exiled one - the link is to the exile event, not to
    the object.
    """
    quality = instance.filter or CREATURES_YOU_CONTROL
    exile_one = Ability.triggered(
        TriggerCondition(
            event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
            subject=SOURCE_ONLY,
            text="when this permanent enters",
        ),
        Effect(
            EffectKind.CONDITIONAL,
            condition=Condition(
                kind=ConditionKind.CONTROLS_MATCHING,
                filter=quality,
                constraint=NumericConstraint.at_least(1),
                text="you control something to champion",
            ),
            children=(
                Effect(
                    EffectKind.EXILE,
                    targets=quality,
                    zone=Zone.EXILE,
                    text="exile another one you control",
                ),
            ),
            otherwise=(
                Effect(EffectKind.SACRIFICE, targets=SOURCE_ONLY, text="sacrifice it"),
            ),
            text="exile another one you control, or sacrifice this",
        ),
        text="champion (enter)",
    )
    return_it = Ability.triggered(
        TriggerCondition(
            event_kinds=LEAVE_BATTLEFIELD_TRIGGER,
            subject=SOURCE_ONLY,
            uses_last_known_information=True,
            text="when this permanent leaves the battlefield",
        ),
        Effect(
            EffectKind.PUT_ONTO_BATTLEFIELD,
            from_zone=Zone.EXILE,
            targets=ObjectFilter(zones=frozenset({Zone.EXILE}), controller=ControllerRelation.YOU),
            text="return the championed card",
        ),
        text="champion (leave)",
    )
    return (exile_one, return_it)


@register("Riot")
def _riot(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.136a: "This creature enters with your choice of a +1/+1 counter
    on it or haste."

    A replacement effect with a mode, chosen as it enters - so the choice is
    made once and cannot be changed later.
    """
    return (
        Ability.static(
            Effect(
                EffectKind.CHOOSE_MODE,
                children=(
                    Effect(
                        EffectKind.ADD_COUNTERS,
                        counter_type="+1/+1",
                        amount=Value.of(1),
                        text="a +1/+1 counter",
                    ),
                    Effect(
                        EffectKind.GRANT_ABILITY,
                        targets=SOURCE_ONLY,
                        granted_abilities=(
                            Ability(AbilityKind.STATIC, keyword="Haste", text="Haste"),
                        ),
                        duration=Duration.PERMANENT,
                        text="haste",
                    ),
                ),
                text="a +1/+1 counter or haste",
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Unleash")
def _unleash(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.98a: "You may have this creature enter with a +1/+1 counter on
    it. It can't block as long as it has a +1/+1 counter on it."

    The drawback is conditioned on the counter, not on the choice, so removing
    the counter later lets it block again.
    """
    has_counter = Condition(
        kind=ConditionKind.COUNTER_COUNT,
        filter=SOURCE_ONLY,
        counter_type="+1/+1",
        constraint=NumericConstraint.at_least(1),
        text="it has a +1/+1 counter on it",
    )
    return (
        Ability.static(
            Effect(
                EffectKind.OPTIONAL,
                children=(
                    Effect(
                        EffectKind.ADD_COUNTERS,
                        counter_type="+1/+1",
                        amount=Value.of(1),
                        text="enters with a +1/+1 counter",
                    ),
                ),
                text="you may have it enter with a +1/+1 counter",
            ),
            text=instance.text or instance.name,
        ),
        Ability.static(
            Effect(
                EffectKind.RESTRICTION,
                targets=SOURCE_ONLY,
                restrictions=(
                    Restriction(
                        act=Act.BLOCK,
                        subject=SOURCE_ONLY,
                        text="unleash: can't block with a +1/+1 counter on it",
                    ),
                ),
                text="can't block",
            ),
            condition=has_counter,
            text="It can't block as long as it has a +1/+1 counter on it.",
        ),
    )


@register("Ravenous")
def _ravenous(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.156a: "This creature enters with X +1/+1 counters on it. If X is
    5 or more, draw a card when it enters."
    """
    x_counters = Ability.static(
        Effect(
            EffectKind.ADD_COUNTERS,
            counter_type="+1/+1",
            amount=Value(kind=ValueKind.X),
            text="enters with X +1/+1 counters",
        ),
        text="This creature enters with X +1/+1 counters on it.",
    )
    draw = Ability.triggered(
        TriggerCondition(
            event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
            subject=SOURCE_ONLY,
            intervening_if=Condition(
                kind=ConditionKind.COUNTER_COUNT,
                filter=SOURCE_ONLY,
                counter_type="+1/+1",
                constraint=NumericConstraint.at_least(5),
                text="X is 5 or more",
            ),
            text="when it enters, if X is 5 or more",
        ),
        Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1)),
        text="If X is 5 or more, draw a card when it enters.",
    )
    return (x_counters, draw)


# -- dies and leaves triggers ------------------------------------------------


@register("Soulshift")
def _soulshift(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.46a: "When this permanent dies, you may return target Spirit card
    with mana value N or less from your graveyard to your hand."
    """
    spirits = ObjectFilter(
        types_all=CardType.CREATURE,
        subtypes_any=("Spirit",),
        zones=frozenset({Zone.GRAVEYARD}),
        owner=ControllerRelation.YOU,
        mana_value=NumericConstraint.at_most(instance.amount or 1),
    )
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.DIES}),
                subject=SOURCE_ONLY,
                uses_last_known_information=True,
                text="when this permanent dies",
            ),
            Effect(
                EffectKind.OPTIONAL,
                children=(
                    Effect(
                        EffectKind.RETURN_TO_HAND,
                        targets=spirits,
                        is_targeted=True,
                        text=f"return a Spirit with mana value {instance.amount} or less",
                    ),
                ),
                text="you may return it to your hand",
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Recover")
def _recover(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.59a: "When a creature is put into your graveyard from the
    battlefield, you may pay [cost]. If you do, return this card from your
    graveyard to your hand. Otherwise, exile it."

    Functions from the graveyard, which is the whole point - an ability that
    only worked on the battlefield would never fire.
    """
    return (
        Ability(
            AbilityKind.TRIGGERED,
            effects=(
                Effect(
                    EffectKind.OPTIONAL,
                    children=(
                        Effect(
                            EffectKind.RETURN_TO_HAND,
                            targets=SOURCE_ONLY,
                            text="return this card to your hand",
                        ),
                    ),
                    otherwise=(
                        Effect(EffectKind.EXILE, targets=SOURCE_ONLY, text="exile it"),
                    ),
                    text="you may pay the recover cost",
                ),
            ),
            cost=instance.cost or Cost(()),
            trigger=TriggerCondition(
                event_kinds=frozenset({EventKind.DIES}),
                subject=ObjectFilter(
                    types_all=CardType.CREATURE, controller=ControllerRelation.YOU
                ),
                functions_in=frozenset({Zone.GRAVEYARD}),
                uses_last_known_information=True,
                text="when a creature you control dies",
            ),
            functions_in=frozenset({Zone.GRAVEYARD}),
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


@register("Haunt")
def _haunt(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.55a: "When this permanent dies, exile it haunting target
    creature."

    The exile is the linked half (CR 607); what the haunt *does* is printed on
    the card as a second trigger, so only the exile lives here.
    """
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.DIES}),
                subject=SOURCE_ONLY,
                uses_last_known_information=True,
                text="when this permanent dies",
            ),
            Effect(
                EffectKind.EXILE,
                targets=SOURCE_ONLY,
                text="exile it haunting target creature",
            ),
            text=instance.text or instance.name,
        ),
    )


# -- upkeep costs ------------------------------------------------------------


@register("Echo")
def _echo(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.30a: "At the beginning of your upkeep, if this came under your
    control since the beginning of your last upkeep, sacrifice it unless you
    pay its echo cost."

    The intervening-if is what makes echo cost you once rather than every turn.
    """
    return (
        Ability(
            AbilityKind.TRIGGERED,
            effects=(
                Effect(
                    EffectKind.OPTIONAL,
                    otherwise=(
                        Effect(
                            EffectKind.SACRIFICE, targets=SOURCE_ONLY, text="sacrifice it"
                        ),
                    ),
                    text="pay the echo cost, or sacrifice it",
                ),
            ),
            cost=instance.cost or Cost(()),
            trigger=TriggerCondition(
                event_kinds=frozenset({EventKind.UPKEEP}),
                players=PlayerFilter(PlayerScope.YOU),
                intervening_if=Condition(
                    kind=ConditionKind.CONTROLS_MATCHING,
                    filter=ObjectFilter(source_only=True, entered_this_turn=None),
                    constraint=NumericConstraint.at_least(1),
                    text="it came under your control since your last upkeep",
                ),
                text="at the beginning of your upkeep",
            ),
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


@register("Cumulative upkeep")
def _cumulative_upkeep(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.24a: "At the beginning of your upkeep, put an age counter on this
    permanent, then sacrifice it unless you pay its upkeep cost for each age
    counter on it."

    The counter goes on first, so the first upkeep already costs one - a
    detail that decides whether a Fading-style card lives an extra turn.
    """
    return (
        Ability(
            AbilityKind.TRIGGERED,
            effects=(
                Effect(
                    EffectKind.ADD_COUNTERS,
                    targets=SOURCE_ONLY,
                    counter_type="age",
                    amount=Value.of(1),
                    text="put an age counter on it",
                ),
                Effect(
                    EffectKind.OPTIONAL,
                    otherwise=(
                        Effect(
                            EffectKind.SACRIFICE, targets=SOURCE_ONLY, text="sacrifice it"
                        ),
                    ),
                    text="pay the cumulative upkeep, or sacrifice it",
                ),
            ),
            cost=instance.cost or Cost(()),
            trigger=TriggerCondition(
                event_kinds=frozenset({EventKind.UPKEEP}),
                players=PlayerFilter(PlayerScope.YOU),
                text="at the beginning of your upkeep",
            ),
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


# -- cast-from-elsewhere and free casts -------------------------------------


@register("Cascade")
def _cascade(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.85a: "When you cast this spell, exile cards from the top of your
    library until you exile a nonland card whose mana value is less than this
    spell's mana value. You may cast that spell without paying its mana cost.
    Put the exiled cards on the bottom in a random order."
    """
    cheaper = ObjectFilter(
        types_none=CardType.LAND,
        zones=frozenset({Zone.EXILE}),
        owner=ControllerRelation.YOU,
    )
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.CAST_SPELL}),
                subject=SOURCE_ONLY,
                functions_in=frozenset({Zone.STACK}),
                text="when you cast this spell",
            ),
            Effect(
                EffectKind.EXILE,
                players=YOU,
                from_zone=Zone.LIBRARY,
                amount=Value.of(1),
                text="exile until you hit a cheaper nonland card",
            ),
            Effect(
                EffectKind.CAST_WITHOUT_PAYING,
                targets=cheaper,
                text="you may cast it without paying its mana cost",
            ),
            Effect(
                EffectKind.PUT_ON_LIBRARY,
                targets=ObjectFilter(zones=frozenset({Zone.EXILE}), owner=ControllerRelation.YOU),
                text="put the rest on the bottom in a random order",
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Ripple")
def _ripple(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.60a: "When you cast this spell, you may reveal the top N cards of
    your library. You may cast any revealed cards with the same name as this
    spell without paying their mana costs. Put the rest on the bottom."
    """
    same_name = ObjectFilter(
        zones=frozenset({Zone.LIBRARY}), owner=ControllerRelation.YOU
    )
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.CAST_SPELL}),
                subject=SOURCE_ONLY,
                functions_in=frozenset({Zone.STACK}),
                text="when you cast this spell",
            ),
            Effect(
                EffectKind.REVEAL,
                players=YOU,
                from_zone=Zone.LIBRARY,
                amount=_amount(instance),
                text=f"reveal the top {instance.amount} cards",
            ),
            Effect(
                EffectKind.CAST_WITHOUT_PAYING,
                targets=same_name,
                text="cast the ones with the same name for free",
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Rebound")
def _rebound(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.88a: "If this spell was cast from your hand, instead of putting it
    into your graveyard as it resolves, exile it and, at the beginning of your
    next upkeep, you may cast it from exile without paying its mana cost."

    A replacement on its own resolution plus a delayed trigger - which is why
    a countered rebound spell rebounds nothing.
    """
    return (
        Ability.static(
            Effect(
                EffectKind.EXILE,
                targets=SOURCE_ONLY,
                text="exile it instead of putting it into your graveyard",
            ),
            Effect(
                EffectKind.DELAYED_TRIGGER,
                trigger=_at_next_upkeep(),
                children=(
                    Effect(
                        EffectKind.CAST_WITHOUT_PAYING,
                        targets=SOURCE_ONLY,
                        text="you may cast it from exile for free",
                    ),
                ),
                text="at the beginning of your next upkeep, cast it for free",
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Epic")
def _epic(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.50a: "For the rest of the game, you can't cast spells. At the
    beginning of each of your upkeeps, copy this spell except for its epic
    ability. You may choose new targets for the copy."
    """
    return (
        Ability.spell(
            Effect(
                EffectKind.RESTRICTION,
                players=YOU,
                restrictions=(
                    Restriction(
                        act=Act.CAST_SPELL,
                        players=PlayerFilter(PlayerScope.YOU),
                        text="epic: you can't cast spells",
                    ),
                ),
                duration=Duration.PERMANENT,
                text="for the rest of the game, you can't cast spells",
            ),
            Effect(
                EffectKind.DELAYED_TRIGGER,
                trigger=_at_next_upkeep("at the beginning of each of your upkeeps"),
                repeats=True,
                children=(
                    Effect(
                        EffectKind.COPY_SPELL,
                        targets=SOURCE_ONLY,
                        text="copy this spell except for its epic ability",
                    ),
                ),
                text="copy this spell at each of your upkeeps",
            ),
            text=instance.text or instance.name,
        ),
    )


# -- graveyard and hand alternatives -----------------------------------------


@register("Dredge")
def _dredge(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.52a: "If you would draw a card, you may instead mill N cards and
    return this card from your graveyard to your hand."

    A replacement effect that functions from the graveyard, and one that
    replaces the *draw* - so a player with fewer than N cards in library
    cannot dredge, and must draw and lose.
    """
    return (
        Ability(
            AbilityKind.STATIC,
            effects=(
                Effect(
                    EffectKind.REPLACEMENT,
                    players=YOU,
                    children=(
                        Effect(
                            EffectKind.MILL,
                            players=YOU,
                            amount=_amount(instance),
                            text=f"mill {instance.amount} cards",
                        ),
                        Effect(
                            EffectKind.RETURN_TO_HAND,
                            targets=SOURCE_ONLY,
                            text="return this card to your hand",
                        ),
                    ),
                    text="instead of drawing, dredge",
                ),
            ),
            keyword=instance.name,
            functions_in=frozenset({Zone.GRAVEYARD}),
            text=instance.text or instance.name,
        ),
    )


@register("Scavenge")
def _scavenge(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.97a: "[Cost], Exile this card from your graveyard: Put a number of
    +1/+1 counters equal to this card's power on target creature. Activate only
    as a sorcery."

    The exile is in the reminder text, not after the keyword, so the parser
    hands over only the mana. Without it the card stayed in the graveyard and
    could scavenge every turn. The power is read from the card as it last
    existed in the graveyard (CR 608.2h), since by resolution it is in exile.
    """
    exile_this = CostComponent(
        CostKind.EXILE_FROM_GRAVEYARD,
        filter=SOURCE_ONLY,
        amount=Value.of(1),
        text="exile this card from your graveyard",
    )
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=(
                Effect(
                    EffectKind.ADD_COUNTERS,
                    targets=ObjectFilter(types_all=CardType.CREATURE),
                    is_targeted=True,
                    counter_type="+1/+1",
                    amount=Value(kind=ValueKind.POWER, of_affected=False),
                    text="+1/+1 counters equal to this card's power",
                ),
            ),
            cost=(instance.cost or Cost(())).with_component(exile_this),
            timing=Timing.SORCERY,
            keyword=instance.name,
            functions_in=frozenset({Zone.GRAVEYARD}),
            text=instance.text or instance.name,
        ),
    )


@register("Transmute")
def _transmute(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.53a: "[Cost], Discard this card: Search your library for a card
    with the same mana value as the discarded card, reveal that card, and put
    it into your hand. Then shuffle your library. Activate only as a sorcery."

    The mana value is the source's, not the candidate's, which is what
    ``of_affected=False`` says in a filter's bound. The source is the object
    that was discarded: by resolution the card is a new object in the
    graveyard (CR 400.7), and the one the ability came from is kept as it was
    in hand - last-known information.
    """
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=(
                Effect(
                    EffectKind.SEARCH_LIBRARY,
                    players=YOU,
                    targets=ObjectFilter(
                        zones=frozenset({Zone.LIBRARY}),
                        mana_value=NumericConstraint(
                            Comparison.EQ,
                            Value(kind=ValueKind.MANA_VALUE, of_affected=False),
                        ),
                    ),
                    zone=Zone.HAND,
                    amount=Value.of(1),
                    text="search for a card with the same mana value as the discarded card",
                ),
                Effect(EffectKind.SHUFFLE, players=YOU, text="then shuffle"),
            ),
            cost=_discard_this_card(instance.cost),
            timing=Timing.SORCERY,
            keyword=instance.name,
            functions_in=HAND,
            text=instance.text or instance.name,
        ),
    )


@register("Transfigure")
def _transfigure(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.71a: "[Cost], Sacrifice this creature: Search your library for a
    creature card with the same mana value as this creature, put it onto the
    battlefield, then shuffle. Activate only as a sorcery."
    """
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=(
                Effect(
                    EffectKind.SEARCH_LIBRARY,
                    players=YOU,
                    targets=ObjectFilter(
                        types_all=CardType.CREATURE, zones=frozenset({Zone.LIBRARY})
                    ),
                    zone=Zone.BATTLEFIELD,
                    amount=Value.of(1),
                    text="search for a creature with the same mana value",
                ),
                Effect(EffectKind.SHUFFLE, players=YOU, text="then shuffle"),
            ),
            cost=instance.cost or Cost(()),
            timing=Timing.SORCERY,
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


@register("Reinforce")
def _reinforce(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.77a: "[Cost], Discard this card: Put N +1/+1 counters on target
    creature."
    """
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=(
                Effect(
                    EffectKind.ADD_COUNTERS,
                    targets=ObjectFilter(types_all=CardType.CREATURE),
                    is_targeted=True,
                    counter_type="+1/+1",
                    amount=_amount(instance),
                    text=f"put {instance.amount} +1/+1 counters on target creature",
                ),
            ),
            cost=_discard_this_card(instance.cost),
            keyword=instance.name,
            functions_in=HAND,
            text=instance.text or instance.name,
        ),
    )


@register("Channel")
def _channel(instance: KeywordInstance) -> tuple[Ability, ...]:
    """Channel is an ability word (CR 207.2c), not a keyword ability, so it has
    no CR 702 entry of its own. The shape is "[Cost], Discard this card:
    [Effect]. Activate only as a sorcery."

    The effect is card text, so the shape is here and the body comes from the
    parser. It is an activated ability from hand, which is the part the engine
    has to get right.
    """
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=_body(instance),
            cost=_discard_this_card(instance.cost),
            timing=Timing.SORCERY,
            keyword=instance.name,
            functions_in=HAND,
            text=instance.text or instance.name,
        ),
    )


@register("Forecast")
def _forecast(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.57a: "[Cost]: [Effect]. Activate only during your upkeep and only
    once each turn."

    Activated from hand while the card is revealed, which is the only reason
    it needs its own builder rather than the generic activated one.
    """
    from ..kernel.enums import Step

    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=_body(instance),
            cost=instance.cost or Cost(()),
            once_each_turn=True,
            activation_condition=Condition(
                kind=ConditionKind.IS_STEP,
                players=PlayerFilter(PlayerScope.YOU),
                constraint=NumericConstraint.exactly(int(Step.UPKEEP)),
                text="only during your upkeep",
            ),
            keyword=instance.name,
            functions_in=HAND,
            text=instance.text or instance.name,
        ),
    )


@register("Aura Swap")
def _aura_swap(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.65a: "[Cost]: Exchange this Aura with an Aura card in your hand."
    """
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=(
                Effect(
                    EffectKind.EXCHANGE_CONTROL,
                    targets=SOURCE_ONLY,
                    zone=Zone.HAND,
                    text="exchange this Aura with an Aura card in your hand",
                ),
            ),
            cost=instance.cost or Cost(()),
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


# -- attacking and blocking --------------------------------------------------


@register("Ninjutsu", "Commander ninjutsu")
def _ninjutsu(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.49a: "[Cost], Reveal this card from your hand, Return an unblocked
    attacking creature you control to its owner's hand: Put this card onto the
    battlefield from your hand tapped and attacking."

    Returning the attacker is part of the cost, so the ability can only be
    activated while there is one to return - and CR 509.1h means there is none
    until blockers have been declared. The Ninja then attacks whatever the
    returned creature was attacking (CR 702.49c), which the resolution reads
    from the creature the cost returned.

    Commander ninjutsu (CR 702.49d) is the same ability that also works from
    the command zone, which is the only difference between the two.
    """
    from ..kernel.enums import Step

    zones = (
        frozenset({Zone.HAND, Zone.COMMAND})
        if instance.key == "commander ninjutsu"
        else HAND
    )
    cost = instance.cost or Cost(())
    if not any(c.kind is CostKind.RETURN_TO_HAND for c in cost.components):
        cost = cost.with_component(
            CostComponent(
                CostKind.RETURN_TO_HAND,
                filter=ObjectFilter(
                    types_all=CardType.CREATURE,
                    controller=ControllerRelation.YOU,
                    attacking=True,
                    blocked=False,
                ),
                amount=Value.of(1),
                text="return an unblocked attacking creature you control to its owner's hand",
            )
        )
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=(
                Effect(
                    EffectKind.PUT_ONTO_BATTLEFIELD,
                    # This card, and only while it is still where the ability
                    # works from: a Ninja discarded in response is not put onto
                    # the battlefield from the graveyard.
                    targets=ObjectFilter(source_only=True, zones=zones),
                    keywords=("tapped", "attacking"),
                    text="put this onto the battlefield tapped and attacking",
                ),
            ),
            cost=cost,
            # The declare blockers step through the end of combat step: the
            # only window in which an attacker can be unblocked (CR 509.1h,
            # 506.3d).
            activation_condition=Condition(
                kind=ConditionKind.AND,
                operands=(
                    Condition(
                        kind=ConditionKind.IS_STEP,
                        constraint=NumericConstraint(
                            Comparison.GE, Value.of(int(Step.DECLARE_BLOCKERS))
                        ),
                    ),
                    Condition(
                        kind=ConditionKind.IS_STEP,
                        constraint=NumericConstraint(
                            Comparison.LE, Value.of(int(Step.END_OF_COMBAT))
                        ),
                    ),
                ),
                text="only once blockers have been declared",
            ),
            keyword=instance.name,
            functions_in=zones,
            text=instance.text or instance.name,
        ),
    )


@register("Soulbond")
def _soulbond(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.95a: "You may pair this creature with another unpaired creature
    when either enters. They remain paired for as long as you control both."

    Two triggers, because either arrival can make the pair. What the pairing
    *grants* is printed separately on the card.
    """
    unpaired = ObjectFilter(
        types_all=CardType.CREATURE,
        controller=ControllerRelation.YOU,
        other_than_source=True,
    )
    pair = Effect(
        EffectKind.OPTIONAL,
        children=(
            Effect(
                EffectKind.ATTACH,
                targets=unpaired,
                text="pair with another unpaired creature",
            ),
        ),
        text="you may pair this creature with another unpaired creature",
    )
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
                subject=SOURCE_ONLY,
                text="when this creature enters",
            ),
            pair,
            text="soulbond (this enters)",
        ),
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
                subject=unpaired,
                text="when another creature you control enters",
            ),
            pair,
            text="soulbond (another enters)",
        ),
    )


@register("Ingest")
def _ingest(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.115a: "Whenever this creature deals combat damage to a player,
    that player exiles the top card of their library."
    """
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.COMBAT_DAMAGE_DEALT}),
                source=SOURCE_ONLY,
                text="whenever this creature deals combat damage to a player",
            ),
            Effect(
                EffectKind.EXILE,
                players=PlayerFilter(PlayerScope.TARGET_PLAYER),
                from_zone=Zone.LIBRARY,
                amount=Value.of(1),
                text="that player exiles the top card of their library",
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Tribute")
def _tribute(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.104a: "As this creature enters, an opponent of your choice may
    place N +1/+1 counters on it."

    A replacement effect an *opponent* chooses, which is the unusual part - the
    controller does not get to decide, and the "if tribute wasn't paid" trigger
    printed on the card reads the outcome.
    """
    return (
        Ability.static(
            Effect(
                EffectKind.OPTIONAL,
                players=PlayerFilter(PlayerScope.OPPONENT),
                children=(
                    Effect(
                        EffectKind.ADD_COUNTERS,
                        counter_type="+1/+1",
                        amount=_amount(instance),
                        text=f"place {instance.amount} +1/+1 counters on it",
                    ),
                ),
                text="an opponent may pay tribute",
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Demonstrate")
def _demonstrate(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.144a: "When you cast this spell, you may copy it. If you do,
    choose an opponent to also copy it."
    """
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.CAST_SPELL}),
                subject=SOURCE_ONLY,
                functions_in=frozenset({Zone.STACK}),
                text="when you cast this spell",
            ),
            Effect(
                EffectKind.OPTIONAL,
                children=(
                    Effect(
                        EffectKind.COPY_SPELL,
                        targets=SOURCE_ONLY,
                        players=YOU,
                        text="copy it",
                    ),
                    Effect(
                        EffectKind.COPY_SPELL,
                        targets=SOURCE_ONLY,
                        players=PlayerFilter(PlayerScope.OPPONENT),
                        text="an opponent copies it too",
                    ),
                ),
                text="you may copy it, and an opponent also copies it",
            ),
            text=instance.text or instance.name,
        ),
    )


# -- designations ------------------------------------------------------------


@register("Ascend")
def _ascend(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.131a: "If you control ten or more permanents, you get the city's
    blessing for the rest of the game."

    A state trigger on a permanent (702.131b) - it checks continuously, so
    reaching ten permanents at any moment gives the blessing immediately, and
    losing them afterwards does not take it away.
    """
    return (
        Ability(
            AbilityKind.TRIGGERED,
            effects=(
                Effect(
                    EffectKind.ADD_COUNTERS,
                    players=YOU,
                    counter_type="city's blessing",
                    amount=Value.of(1),
                    text="you get the city's blessing",
                ),
            ),
            trigger=TriggerCondition(
                event_kinds=frozenset(),
                is_state_trigger=True,
                intervening_if=Condition(
                    kind=ConditionKind.CONTROLS_MATCHING,
                    filter=ObjectFilter(controller=ControllerRelation.YOU),
                    constraint=NumericConstraint.at_least(10),
                    text="you control ten or more permanents",
                ),
                text="if you control ten or more permanents",
            ),
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


@register("Daybound", "Nightbound")
def _day_night(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.145: the day-night cycle, as transform triggers. Daybound is
    702.145b-d and nightbound 702.145e-g; both live under the one rule.

    Daybound turns the permanent to its night face when it becomes night;
    nightbound the reverse. The cycle itself is tracked on the game (CR 726),
    so these only have to react to it.
    """
    return (
        Ability(
            AbilityKind.TRIGGERED,
            effects=(
                Effect(
                    EffectKind.TRANSFORM,
                    targets=SOURCE_ONLY,
                    text="transform it",
                ),
            ),
            trigger=TriggerCondition(
                event_kinds=frozenset({EventKind.DAY_NIGHT_CHANGED}),
                text="when day becomes night, or night becomes day",
            ),
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


@register("Living metal")
def _living_metal(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.161a: "As long as it's your turn, this Vehicle is an artifact
    creature."

    Layer 4, conditioned on whose turn it is - so a Transformer blocks on your
    turn only if something gave it vigilance, and never attacks on someone
    else's.
    """
    return (
        Ability.static(
            Effect(
                EffectKind.ADD_TYPE,
                targets=SOURCE_ONLY,
                types=CardType.CREATURE,
                duration=Duration.WHILE_SOURCE_PERSISTS,
                text="is an artifact creature",
            ),
            condition=Condition(kind=ConditionKind.IS_YOUR_TURN, text="it's your turn"),
            text=instance.text or instance.name,
        ),
    )


# ---------------------------------------------------------------------------
# Cost-payment helpers, hideaway, and the formats this simulator does not run
# ---------------------------------------------------------------------------


@register("Convoke", "Improvise")
def _tap_to_help_cast(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.51a convoke, CR 702.126a improvise.

    "Your creatures can help cast this spell" and "your artifacts can help cast
    this spell": each permanent tapped this way pays for {1} or, for convoke,
    one mana of its colour. It is not a cost reduction - the spell's mana value
    is unchanged, which matters for everything that asks - so it is registered
    as a permission the payment step consults.
    """
    helper = (
        ObjectFilter(types_all=CardType.CREATURE, controller=ControllerRelation.YOU)
        if instance.key == "convoke"
        else ObjectFilter(types_all=CardType.ARTIFACT, controller=ControllerRelation.YOU)
    )
    return (
        Ability(
            AbilityKind.STATIC,
            effects=(
                Effect(
                    EffectKind.PERMISSION,
                    targets=helper,
                    keywords=(instance.name,),
                    text=f"{instance.name}: tap to help pay",
                ),
            ),
            keyword=instance.name,
            quality=helper,
            functions_in=frozenset({Zone.HAND, Zone.STACK}),
            text=instance.text or instance.name,
        ),
    )


@register("Hideaway")
def _hideaway(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.75a: "When this permanent enters, look at the top N cards of your
    library, exile one face down, then put the rest on the bottom in a random
    order."

    The exiled card is remembered by a linked ability (CR 607); the condition
    that lets you play it is printed separately on the card.
    """
    return (
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
                subject=SOURCE_ONLY,
                text="when this permanent enters",
            ),
            Effect(
                EffectKind.REVEAL,
                players=YOU,
                from_zone=Zone.LIBRARY,
                amount=_amount(instance, 4),
                text=f"look at the top {instance.amount or 4} cards",
            ),
            Effect(
                EffectKind.EXILE,
                players=YOU,
                from_zone=Zone.LIBRARY,
                amount=Value.of(1),
                text="exile one face down",
            ),
            Effect(
                EffectKind.PUT_ON_LIBRARY,
                players=YOU,
                text="put the rest on the bottom in a random order",
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Suspend")
def _suspend(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.62a: three abilities in one keyword.

    Exiling the card is a *special action* (CR 116.2f) and lives in
    ``special_actions``. What remains here is the pair of abilities that
    function from exile: remove a time counter at each of your upkeeps, and
    cast it for free when the last one comes off. The creature also gains haste
    until it next leaves the battlefield (702.62e).
    """
    tick = Ability(
        AbilityKind.TRIGGERED,
        effects=(
            Effect(
                EffectKind.REMOVE_COUNTERS,
                targets=SOURCE_ONLY,
                counter_type="time",
                amount=Value.of(1),
                text="remove a time counter",
            ),
        ),
        trigger=TriggerCondition(
            event_kinds=frozenset({EventKind.UPKEEP}),
            players=PlayerFilter(PlayerScope.YOU),
            intervening_if=Condition(
                kind=ConditionKind.COUNTER_COUNT,
                filter=SOURCE_ONLY,
                counter_type="time",
                constraint=NumericConstraint.at_least(1),
                text="it has a time counter on it",
            ),
            functions_in=frozenset({Zone.EXILE}),
            text="at the beginning of your upkeep, if it is suspended",
        ),
        keyword=instance.name,
        functions_in=frozenset({Zone.EXILE}),
        text="At the beginning of your upkeep, remove a time counter.",
    )
    cast_it = Ability(
        AbilityKind.TRIGGERED,
        effects=(
            Effect(
                EffectKind.CAST_WITHOUT_PAYING,
                targets=SOURCE_ONLY,
                text="cast it without paying its mana cost",
            ),
        ),
        trigger=TriggerCondition(
            event_kinds=frozenset({EventKind.COUNTER_REMOVED}),
            subject=SOURCE_ONLY,
            intervening_if=Condition(
                kind=ConditionKind.COUNTER_COUNT,
                filter=SOURCE_ONLY,
                counter_type="time",
                constraint=NumericConstraint.exactly(0),
                text="the last time counter was removed",
            ),
            functions_in=frozenset({Zone.EXILE}),
            text="when the last time counter is removed",
        ),
        keyword=instance.name,
        functions_in=frozenset({Zone.EXILE}),
        text="When the last time counter is removed, cast it without paying its mana cost.",
    )
    haste = Ability.static(
        Effect(
            EffectKind.GRANT_ABILITY,
            targets=SOURCE_ONLY,
            granted_abilities=(Ability(AbilityKind.STATIC, keyword="Haste", text="Haste"),),
            duration=Duration.WHILE_SOURCE_PERSISTS,
            text="it has haste",
        ),
        text="If you cast it this way, it gains haste until you lose control of it.",
    )
    return (tick, cast_it, haste)


@register("Awaken")
def _awaken(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.113a: "Awaken N - [cost]. If you cast this spell for its awaken
    cost, also put N +1/+1 counters on target land you control and it becomes a
    0/0 Elemental creature with haste. It's still a land."
    """
    lands = ObjectFilter(types_all=CardType.LAND, controller=ControllerRelation.YOU)
    animate = Ability.spell(
        Effect(
            EffectKind.ADD_COUNTERS,
            targets=lands,
            is_targeted=True,
            counter_type="+1/+1",
            amount=_amount(instance),
            text=f"put {instance.amount} +1/+1 counters on target land you control",
        ),
        Effect(
            EffectKind.ADD_TYPE,
            targets=lands,
            types=CardType.CREATURE,
            keywords=("Elemental",),
            duration=Duration.PERMANENT,
            text="it becomes a 0/0 Elemental creature with haste; it's still a land",
        ),
        text=instance.text or instance.name,
    )
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            alternative_cost=AlternativeCost(
                cost=instance.cost or Cost(()),
                from_zone=Zone.HAND,
                keyword=instance.name,
                text=instance.text or instance.name,
            ),
            functions_in=HAND,
            text=instance.text or instance.name,
        ),
        animate,
    )


@register("Cipher")
def _cipher(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.99a: "Then you may exile this spell card encoded on a creature you
    control. Whenever that creature deals combat damage to a player, its
    controller may cast a copy of this card without paying its mana cost."
    """
    yours = ObjectFilter(types_all=CardType.CREATURE, controller=ControllerRelation.YOU)
    return (
        Ability.spell(
            Effect(
                EffectKind.OPTIONAL,
                children=(
                    Effect(
                        EffectKind.EXILE,
                        targets=SOURCE_ONLY,
                        text="exile this spell card encoded on a creature you control",
                    ),
                ),
                text="you may encode this on a creature you control",
            ),
            text=instance.text or instance.name,
        ),
        Ability(
            AbilityKind.TRIGGERED,
            effects=(
                Effect(
                    EffectKind.CAST_WITHOUT_PAYING,
                    targets=SOURCE_ONLY,
                    text="cast a copy without paying its mana cost",
                ),
            ),
            trigger=TriggerCondition(
                event_kinds=frozenset({EventKind.COMBAT_DAMAGE_DEALT}),
                source=yours,
                functions_in=frozenset({Zone.EXILE}),
                text="whenever the encoded creature deals combat damage to a player",
            ),
            keyword=instance.name,
            functions_in=frozenset({Zone.EXILE}),
            text=instance.text or instance.name,
        ),
    )


@register("Fuse")
def _fuse(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.102a: "You may cast one or both halves of this card from your hand."

    A permission on the split card rather than an effect - CR 709 already knows
    how to cast either half, so fuse only removes the "one of" restriction.
    """
    return (
        Ability(
            AbilityKind.STATIC,
            effects=(
                Effect(
                    EffectKind.PERMISSION,
                    targets=SOURCE_ONLY,
                    keywords=("Fuse",),
                    text="you may cast both halves from your hand",
                ),
            ),
            keyword=instance.name,
            functions_in=HAND,
            text=instance.text or instance.name,
        ),
    )


@register("More Than Meets the Eye")
def _more_than_meets_the_eye(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.162a: "You may cast this card converted for [cost]."

    An alternative cost that also picks the other face, which is exactly what
    ``CastMode`` was built for.
    """
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            alternative_cost=AlternativeCost(
                cost=instance.cost or Cost(()),
                from_zone=Zone.HAND,
                keyword=instance.name,
                text="cast this card converted",
            ),
            functions_in=HAND,
            text=instance.text or instance.name,
        ),
    )


@register("Compleated")
def _compleated(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.150a: "[Phyrexian symbol] can be paid with 2 life. If life was
    paid, this planeswalker enters with that many fewer loyalty counters."

    The mana half is already handled by Phyrexian symbols in ``mana.py``; what
    is left is the loyalty penalty, as a replacement on entering.
    """
    return (
        Ability.static(
            Effect(
                EffectKind.REMOVE_COUNTERS,
                targets=SOURCE_ONLY,
                counter_type="loyalty",
                amount=Value(kind=ValueKind.X),
                text="enters with that many fewer loyalty counters",
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Solved")
def _solved(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.169a / 719.3c: "Solved - [ability]" works only once the Case has
    the solved designation."""
    from ..cr300_card_types.cr300_card_types import solved_ability

    inner = Ability(
        AbilityKind.STATIC,
        effects=(Effect(EffectKind.UNPARSED, text=instance.text or instance.name),),
        keyword=instance.name,
        text=instance.text or instance.name,
    )
    return solved_ability(inner)


@register("Max speed")
def _max_speed(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.178a: "[Ability] - Active only while you have max speed."

    Speed is a player designation that starts at 1 and rises to 4 (CR 702.178,
    "Start your engines!"); max speed is speed 4.
    """
    return (
        Ability.static(
            *_body(instance),
            condition=Condition(
                kind=ConditionKind.COUNTER_COUNT,
                players=PlayerFilter(PlayerScope.YOU),
                counter_type="speed",
                constraint=NumericConstraint.at_least(4),
                text="you have max speed",
            ),
            text=instance.text or instance.name,
        ),
    )


#: Keywords that belong to formats this simulator does not run. Named rather
#: than omitted, because a card carrying one must be *reported* as out of
#: scope rather than quietly doing nothing and looking implemented.
#:
#: Augment/Host is Unstable; agendas are Conspiracy Draft. Neither is
#: Commander-legal, so no deck this program simulates can contain them.
OUT_OF_FORMAT = ("Augment", "Double agenda", "Hidden agenda")


@register(*OUT_OF_FORMAT)
def _out_of_format(instance: KeywordInstance) -> tuple[Ability, ...]:
    """Registered so the coverage report names them, never silently ignored."""
    return (Ability.unreadable(instance.text or instance.name),)


#: Mechanics from sets released after this engine's card pool snapshot, whose
#: rules text the Comprehensive Rules describes but which no Commander-legal
#: card in the pool uses yet. They are shapes without bodies, and the registry
#: says so.
NOT_YET_MODELLED = ("Companion",)


@register(*NOT_YET_MODELLED)
def _not_yet_modelled(instance: KeywordInstance) -> tuple[Ability, ...]:
    return (
        Ability(
            AbilityKind.STATIC,
            effects=(Effect(EffectKind.UNPARSED, text=instance.text or instance.name),),
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


@register("Banding")
def _banding(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.22: banding, the oldest and strangest evasion-ish keyword.

    Two halves. The declaration half - attacking creatures forming a band that
    must all attack the same defender (702.22c-d) - is a choice the attacking
    player makes, and lives with attacker declaration. The half that decides
    games is 702.22j: while a creature with banding is blocking, the *defending*
    player orders the attacker's blockers for damage assignment, so the
    attacker can no longer aim lethal damage where they please. ``combat.py``
    reads the keyword for exactly that swap.

    "Bands with other [quality]" (702.22b) is the same ability with a
    restriction on who may join, so it carries a quality filter and is
    otherwise identical.
    """
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            quality=instance.filter,
            text=instance.text or instance.name,
        ),
    )


@register("Companion")
def _companion(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.139a: "If this card is your chosen companion, you may pay {3} and
    put it into your hand from outside the game any time you could cast a
    sorcery. This starts the game outside the game."

    Paying the {3} is a special action (CR 116.2g), so the ability here is the
    permission the special action reads, plus the deck-building condition the
    parser fills in. In Commander the companion sits in the command zone
    alongside the commander, which is why it functions from there.
    """
    return (
        Ability(
            AbilityKind.STATIC,
            effects=(
                Effect(
                    EffectKind.PERMISSION,
                    targets=ObjectFilter(source_only=True),
                    keywords=("Companion",),
                    text="you may pay {3} to put this into your hand",
                ),
            ),
            keyword=instance.name,
            quality=instance.filter,
            functions_in=frozenset({Zone.COMMAND, Zone.EXILE}),
            text=instance.text or instance.name,
        ),
    )


@register("Mutate")
def _mutate(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.140a and CR 730: mutate is an alternative cost *plus* a merge.

    "If you cast this spell for its mutate cost, put it over or under target
    non-Human creature you own. They merge into a mutated creature."

    The merge is what makes mutate strange, and CR 730.2c is the part engines
    get wrong: the merged permanent is *the same object it was before*. It has
    not just entered the battlefield, it keeps its counters, its damage, its
    auras and its summoning sickness. So this is not "exile one and make a
    copy" - it is a copy effect in layer 1 applied to a permanent that never
    left, which is why mutating onto a creature with a +1/+1 counter keeps the
    counter.
    """
    non_human_you_own = ObjectFilter(
        types_all=CardType.CREATURE,
        subtypes_none=("Human",),
        owner=ControllerRelation.YOU,
    )
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            alternative_cost=AlternativeCost(
                cost=instance.cost or Cost(()),
                from_zone=Zone.HAND,
                keyword=instance.name,
                text=instance.text or instance.name,
            ),
            functions_in=HAND,
            text=instance.text or instance.name,
        ),
        Ability.spell(
            Effect(
                EffectKind.COPY_PERMANENT,
                targets=non_human_you_own,
                is_targeted=True,
                # CR 730.2a: the merged permanent has the characteristics of
                # its topmost component, and that is a copiable effect - so it
                # goes through the copy machinery rather than replacing the
                # object.
                duration=Duration.PERMANENT,
                text="merge with target non-Human creature you own",
            ),
            text="Mutate: put this over or under the target; they merge.",
        ),
    )


@register("Haste")
def _haste(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.10b: ignores summoning sickness.

    A bare name, and that is the whole implementation - ``can_attack`` and the
    tap-ability check ask ``has_keyword("Haste")`` directly. It needs a builder
    all the same: without one the parser produces an *unreadable* ability, the
    keyword never reaches the characteristics, and every hasty creature in the
    pool sits summoning-sick. Nothing failed, because the registry's
    hand-written table said "implemented" and no builder existed to contradict
    it.
    """
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


@register("Enchant")
def _enchant(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.5b: what this Aura may be attached to.

    The quality is load-bearing: it decides what the Aura can legally enchant
    when it enters, and CR 704.5n puts it into the graveyard if it is ever
    attached to something it does not match.
    """
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            quality=instance.filter,
            functions_in=frozenset({Zone.BATTLEFIELD, Zone.STACK}),
            text=instance.text or instance.name,
        ),
    )


@register("Partner", "Partner with", "Friends forever", "Choose a Background",
          "Doctor's companion")
def _partner(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.124 and the Commander variants (CR 903.3b-f).

    These are deck-construction abilities: they change how many commanders a
    deck may have, and ``decks/model.py`` enforces that. On the battlefield
    they do nothing at all, which is why a bare name is the right expansion -
    but only a *readable* one, or the commander itself parses as unreadable.
    """
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            quality=instance.filter,
            functions_in=ANY_ZONE,
            text=instance.text or instance.name,
        ),
    )


# ---------------------------------------------------------------------------
# Returning a creature to hand as an alternative cost
#
# Sneak and web-slinging are the same shape as ninjutsu - pay something and
# bounce one of your own creatures - but they are *alternative costs on a
# spell* rather than an activated ability, so the card is genuinely cast and
# everything that watches casting sees it.
# ---------------------------------------------------------------------------


def _return_creature_cost(
    cost: Cost, creature: ObjectFilter, description: str
) -> Cost:
    """Add "return a creature you control to its owner's hand" to a cost.

    Skipped when the parser already read a return component out of the
    keyword's own text, so a cost is never charged twice.
    """
    if any(c.kind is CostKind.RETURN_TO_HAND for c in cost.components):
        return cost
    return cost.with_component(
        CostComponent(
            CostKind.RETURN_TO_HAND,
            filter=creature,
            amount=Value.of(1),
            text=description,
        )
    )


@register("Web-slinging")
def _web_slinging(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.188a: web-slinging is an alternative cost (CR 601.2b, 601.2f-h).

    "Web-slinging [cost]" lets the spell be cast for that cost plus returning
    a *tapped* creature you control to its owner's hand, instead of its mana
    cost. The tapped creature is the whole restriction - it is normally one
    that has just attacked - so it belongs in the cost rather than in a
    condition: a cost that cannot be paid is what makes the option disappear
    from the list of legal casts, and CR 118.9a still allows only one
    alternative cost on the spell.

    Unlike sneak (CR 702.190a) there is no timing clause: the spell's own
    timing rules apply unchanged.
    """
    tapped_creature = ObjectFilter(
        types_all=CardType.CREATURE,
        controller=ControllerRelation.YOU,
        tapped=True,
    )
    cost = _return_creature_cost(
        instance.cost or Cost(()),
        tapped_creature,
        "return a tapped creature you control to its owner's hand",
    )
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            alternative_cost=AlternativeCost(
                cost=cost,
                from_zone=None,
                keyword=instance.name,
                text=instance.text or instance.name,
            ),
            functions_in=HAND,
            text=instance.text or instance.name,
        ),
    )


@register("Sneak")
def _sneak(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.190a: an alternative cost with a timing window of its own.

    "Sneak [cost]" is the cost, plus returning an unblocked creature you
    control to its owner's hand, and it may only be paid when you could cast
    an instant during your declare blockers step.

    Both halves are expressed where the engine already enforces them. The
    returned creature is a cost component, exactly as it is for ninjutsu
    (CR 702.49a), so the dry run in cost payment refuses the cast while there
    is nothing unblocked to return - and CR 509.1h means nothing is unblocked
    until blockers have been declared. The step itself is the alternative
    cost's condition.

    CR 702.190b - a permanent spell cast this way enters tapped and attacking
    what the returned creature was attacking - is a flag on the alternative
    cost. The spell records which alternative cost paid for it, and
    resolution reads that record, so only a sneak cast arrives attacking.
    """
    from ..kernel.enums import Step

    unblocked_attacker = ObjectFilter(
        types_all=CardType.CREATURE,
        controller=ControllerRelation.YOU,
        attacking=True,
        blocked=False,
    )
    cost = _return_creature_cost(
        instance.cost or Cost(()),
        unblocked_attacker,
        "return an unblocked creature you control to its owner's hand",
    )
    return (
        Ability(
            AbilityKind.STATIC,
            keyword=instance.name,
            alternative_cost=AlternativeCost(
                cost=cost,
                from_zone=None,
                condition=Condition(
                    kind=ConditionKind.IS_STEP,
                    players=YOU,
                    constraint=NumericConstraint.exactly(
                        int(Step.DECLARE_BLOCKERS)
                    ),
                    text="during your declare blockers step",
                ),
                instant_speed=True,
                enters_tapped_and_attacking=True,
                keyword=instance.name,
                text=instance.text or instance.name,
            ),
            functions_in=HAND,
            text=instance.text or instance.name,
        ),
    )


# ---------------------------------------------------------------------------
# Power-up
# ---------------------------------------------------------------------------


@register("Power-up")
def _power_up(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.193a: "Power-up - [Cost]: [Effect]" is an activated ability with
    two riders - a cost reduction on the turn the permanent entered, and a
    limit of one activation.

    The ability itself is ordinary, so it is built as one: the parser supplies
    the body, the keyword supplies the cost. Both riders are flags the
    activation code reads: the reduction (CR 702.193b, applied as CR 118.7
    applies any reduction by specific mana) in ``activation_mana_cost``, and
    "only once" - once for the life of the object, not once a turn - in
    ``activation_limit_reached``.
    """
    return (
        Ability(
            AbilityKind.ACTIVATED,
            effects=_body(instance),
            cost=instance.cost or Cost(()),
            only_once=True,
            reduced_by_own_mana_cost_on_entry=True,
            keyword=instance.name,
            text=instance.text or instance.name,
        ),
    )


# ---------------------------------------------------------------------------
# Paradigm
# ---------------------------------------------------------------------------


@register("Paradigm")
def _paradigm(instance: KeywordInstance) -> tuple[Ability, ...]:
    """CR 702.192a: two spell abilities, one of which delays a repeating
    trigger that copies the spell into exile for a free cast each precombat
    main phase, and one of which exiles the spell.

    The exile half is built exactly; the copy half is not. Three things it
    needs and the engine does not have:

    * a copy made *in exile* rather than on the stack (CR 707.10 through
      ``COPY_SPELL`` puts it on the stack, and ignores a zone);
    * a trigger at the beginning of a main phase - ``PHASE_BEGAN`` is the
      event the parser uses for that phrase and nothing emits it, and
      ``STEP_BEGAN`` cannot say *which* step;
    * "if this is the first time a spell you control with this name has
      resolved this game", which no condition kind can ask.

    So the shape is here, the exile happens, and the copy is left unread
    rather than approximated - the approximation would hand the card a free
    cast every turn from the first resolution onwards with nothing to stop it
    compounding.
    """
    return (
        Ability.spell(
            Effect(
                EffectKind.DELAYED_TRIGGER,
                trigger=TriggerCondition(
                    event_kinds=frozenset({EventKind.PHASE_BEGAN}),
                    players=YOU,
                    intervening_if=Condition(
                        kind=ConditionKind.IS_MAIN_PHASE,
                        text="a main phase",
                    ),
                    functions_in=frozenset({Zone.EXILE}),
                    text="at the beginning of each of your precombat main phases",
                ),
                repeats=True,
                children=(
                    Effect(
                        EffectKind.UNPARSED,
                        text="create a copy of this in exile and you may cast it",
                    ),
                ),
                text="each of your precombat main phases, a free copy",
            ),
            Effect(
                EffectKind.EXILE,
                targets=SOURCE_ONLY,
                text="exile this spell",
            ),
            text=instance.text or instance.name,
        ),
    )
