"""Abilities (CR 113) and trigger conditions (CR 603).

Magic has exactly four kinds of ability, and the distinction is mechanical
rather than cosmetic:

* **Spell abilities** (CR 113.3a) are the instructions on an instant or
  sorcery. They function only while the spell is on the stack.
* **Activated abilities** (CR 113.3b) are ``cost: effect``. Anyone with
  priority who meets the timing restriction may activate them.
* **Triggered abilities** (CR 113.3c) start with "when", "whenever", or "at",
  and go on the stack the next time a player would receive priority.
* **Static abilities** (CR 113.3d) simply do something continuously, and never
  use the stack. CR 604.1: they are statements rather than instructions -
  there is nothing to activate and nothing to trigger.

The field that gets forgotten is ``functions_in`` (CR 113.6). An ability works
only on the battlefield unless it says otherwise - which is why Flashback works
from a graveyard and Cycling from hand, while a creature's tap ability does
nothing while the creature is in exile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from ..cr100_game_concepts.cr118_costs import FREE, Cost
from ..kernel.enums import Timing, Zone
from ..kernel.events import EventKind
from ..kernel.query import ALWAYS, Condition, ObjectFilter, PlayerFilter
from .effects import Effect


class AbilityKind(IntEnum):
    SPELL = 0
    ACTIVATED = 1
    TRIGGERED = 2
    STATIC = 3


#: The default: abilities function on the battlefield only (CR 113.6).
BATTLEFIELD_ONLY = frozenset({Zone.BATTLEFIELD})
#: Spell abilities function on the stack (CR 113.6a).
STACK_ONLY = frozenset({Zone.STACK})


@dataclass(frozen=True, slots=True)
class TriggerCondition:
    """When a triggered ability triggers (CR 603.2).

    ``intervening_if`` is the clause that follows "if" *before* the effect, as
    in "At the beginning of your upkeep, if you control three or more Elves,
    draw a card." CR 603.4 makes this uniquely fiddly: the condition is checked
    both when the ability would trigger and again as it resolves, and the
    ability does nothing if it is false either time. Ordinary "if" clauses
    inside the effect are checked only on resolution, and live on the Effect.
    """

    event_kinds: frozenset[EventKind] = frozenset()

    #: Constraint on the object the event is about ("whenever a *creature you
    #: control* dies").
    subject: ObjectFilter | None = None
    #: Constraint on what caused the event ("whenever a creature is dealt
    #: damage *by a source you control*").
    source: ObjectFilter | None = None
    #: Constraint on the player the event is about ("whenever *an opponent*
    #: draws a card").
    players: PlayerFilter | None = None

    intervening_if: Condition = ALWAYS

    #: "Whenever a player casts their *second* spell each turn": the trigger
    #: fires on the nth such event of the turn and on no other. Zero means
    #: every occurrence, which is the ordinary case.
    ordinal: int = 0

    #: Whole alternative conditions, for a disjunction whose halves are about
    #: different subjects. ``event_kinds`` can express "dies or is exiled"
    #: because both are about the same object; it cannot express "a creature
    #: dies or a creature *card* leaves your graveyard", because the two
    #: halves constrain different things.
    alternatives: tuple = ()

    #: Zones the ability's source must be in for the trigger to work. Dies
    #: triggers are the reason this is not simply the battlefield: the object
    #: is already in the graveyard when the ability triggers, and the game looks
    #: back in time (CR 603.10a, 603.6e).
    functions_in: frozenset[Zone] = BATTLEFIELD_ONLY

    #: State triggers (CR 603.8): conditions rather than events, which trigger
    #: again the moment they become true and only once while they stay true.
    #: CR 714.2b: for a Saga chapter ability, the chapter number. A chapter
    #: trigger is not "whenever a lore counter is added" - it fires only when
    #: the count *crosses* this number, so the check needs the value and the
    #: event's amount together. Zero for every non-chapter trigger.
    chapter: int = 0
    is_state_trigger: bool = False

    #: Which counter a COUNTER_ADDED / COUNTER_REMOVED trigger cares about.
    #: Empty means any kind. "When the last defense counter is removed"
    #: (CR 310.12b) is about defense counters and nothing else, and without
    #: this a Siege would fire on every -1/-1 counter too.
    counter_kind: str = ""

    #: CR 603.2f: some triggers fire at most once in a turn.
    once_each_turn: bool = False

    #: For PHASE_BEGAN: which phases, as ``Phase`` values. "At the beginning
    #: of combat" (CR 507.1) and "at the beginning of your precombat main
    #: phase" (CR 505.1a) share the event and differ only here. Empty means
    #: any phase.
    phases: frozenset[int] = frozenset()
    #: For STEP_BEGAN: which steps, as ``Step`` values - "at end of combat" is
    #: the end of combat step beginning (CR 511.2). Empty means any step.
    steps: frozenset[int] = frozenset()

    #: For damage events: the damage must have been dealt to a player, not to
    #: a permanent. "Deals combat damage to a player" names its recipient, and
    #: combat damage to a blocking creature is combat damage too.
    to_player: bool = False

    #: Set for abilities that trigger on the source leaving the battlefield, so
    #: the engine knows to evaluate them against last-known information
    #: (CR 603.6e, 608.2g).
    uses_last_known_information: bool = False

    text: str = ""

    def __str__(self) -> str:
        return self.text or "/".join(sorted(k.name for k in self.event_kinds))


@dataclass(frozen=True, slots=True)
class Ability:
    """One ability of a card, token, or emblem."""

    kind: AbilityKind
    effects: tuple[Effect, ...] = ()
    text: str = ""

    # -- activated ----------------------------------------------------------
    cost: Cost = FREE
    timing: Timing = Timing.INSTANT
    #: CR 605: a mana ability does not use the stack and cannot be responded
    #: to. An activated ability qualifies when it has no target, could add mana,
    #: and is not a loyalty ability (CR 605.1a).
    is_mana_ability: bool = False
    #: CR 606: loyalty abilities are sorcery-speed and once per turn per
    #: permanent, and their cost is a loyalty counter change.
    is_loyalty_ability: bool = False
    #: CR 602.5b and friends: "activate only once each turn".
    once_each_turn: bool = False
    #: CR 602.5b: "activate only once" - once for as long as the object
    #: exists, which CR 400.7 ends at a zone change.
    only_once: bool = False
    #: CR 702.193a/b: power-up's cost is reduced by the permanent's own mana
    #: cost on the turn it entered, symbol by symbol as CR 118.7 reduces.
    reduced_by_own_mana_cost_on_entry: bool = False
    activation_condition: Condition = ALWAYS

    # -- triggered ----------------------------------------------------------
    trigger: TriggerCondition | None = None

    # -- static -------------------------------------------------------------
    #: For static abilities, the condition under which they apply at all
    #: ("as long as you control a Forest").
    static_condition: Condition = ALWAYS

    # -- shared -------------------------------------------------------------
    #: CR 113.6. Overridden for abilities that work in hand, graveyard, or exile.
    functions_in: frozenset[Zone] = BATTLEFIELD_ONLY
    #: The keyword this ability came from, if any. Lets the engine answer "does
    #: this creature have flying?" without re-reading text.
    keyword: str = ""
    #: CR 607: linked abilities refer to each other ("exile it" / "you may play
    #: that card"). Two abilities on the same object sharing a link id are
    #: linked.
    link_id: int = 0
    #: CR 702.16a: the "from [quality]" of Protection and Hexproof from, as a
    #: filter over the *source*. Without it protection is a blanket ban, which
    #: over-protects: protection from red must not stop a white spell.
    quality: object | None = None
    #: CR 118.9: an alternative cost this object offers (Flashback, Evoke).
    #: Carried on a static ability because that is what it is - a continuously
    #: applying permission to pay something else.
    alternative_cost: object | None = None
    #: CR 118.8: an additional cost imposed by this object (Kicker, Casualty).
    additional_cost: object | None = None
    #: CR 714: which chapter of a Saga this ability is. Zero for everything
    #: else. Needed by the state-based action that sacrifices a finished Saga.
    chapter: int = 0
    #: CR 604.3: a characteristic-defining ability defines a characteristic
    #: rather than changing one. It functions in every zone, applies in layer
    #: 7a, and CR 613.8a excludes it from depending on ordinary effects.
    is_characteristic_defining: bool = False
    #: CR 305.6: this ability comes from a basic land type rather than the card,
    #: so it must vanish when the type does. Marked rather than inferred because
    #: "Nonbasic lands are Mountains" has to strip exactly these.
    from_land_type: bool = False
    #: True when the parser could not fully read the ability. Such an ability
    #: is registered so it is visible in the coverage report, but never fires.
    unparsed: bool = False

    # -- construction helpers ----------------------------------------------

    @classmethod
    def static(cls, *effects: Effect, condition: Condition = ALWAYS, text: str = "") -> Ability:
        return cls(
            AbilityKind.STATIC, effects=effects, static_condition=condition, text=text
        )

    @classmethod
    def activated(
        cls,
        cost: Cost,
        *effects: Effect,
        timing: Timing = Timing.INSTANT,
        text: str = "",
    ) -> Ability:
        return cls(AbilityKind.ACTIVATED, effects=effects, cost=cost, timing=timing, text=text)

    @classmethod
    def triggered(
        cls, trigger: TriggerCondition, *effects: Effect, text: str = ""
    ) -> Ability:
        return cls(
            AbilityKind.TRIGGERED,
            effects=effects,
            trigger=trigger,
            functions_in=trigger.functions_in,
            text=text,
        )

    @classmethod
    def spell(cls, *effects: Effect, text: str = "") -> Ability:
        return cls(AbilityKind.SPELL, effects=effects, functions_in=STACK_ONLY, text=text)

    @classmethod
    def unreadable(cls, text: str) -> Ability:
        """An ability we could not parse.

        It exists as an object so the card reports honestly, and it never does
        anything, because an ability the engine does not understand must not be
        allowed to guess.
        """
        from .effects import unparsed as unparsed_effect

        return cls(AbilityKind.STATIC, effects=(unparsed_effect(text),), text=text, unparsed=True)

    # -- queries ------------------------------------------------------------

    @property
    def uses_stack(self) -> bool:
        """Static abilities and mana abilities never use the stack (CR 605.3b)."""
        if self.kind is AbilityKind.STATIC:
            return False
        return not self.is_mana_ability

    @property
    def is_targeted(self) -> bool:
        return any(node.is_targeted for effect in self.effects for node in effect.walk())

    def functions_in_zone(self, zone: Zone) -> bool:
        return zone in self.functions_in

    def __str__(self) -> str:
        if self.text:
            return self.text
        if self.kind is AbilityKind.ACTIVATED:
            return f"{self.cost}: {'; '.join(str(e) for e in self.effects)}"
        if self.kind is AbilityKind.TRIGGERED:
            return f"{self.trigger}: {'; '.join(str(e) for e in self.effects)}"
        return "; ".join(str(e) for e in self.effects)


@dataclass(slots=True)
class DelayedTrigger:
    """A trigger created by a resolving spell or ability (CR 603.7).

    "At the beginning of the next end step, sacrifice it" is not an ability of
    any object - it is a delayed trigger set up by the effect that created it.
    It fires at most once unless it says otherwise, and it remembers who
    created it because that player controls it.
    """

    trigger: TriggerCondition
    effects: tuple[Effect, ...]
    controller: int
    source: int
    #: Delayed triggers usually fire once and then vanish (CR 603.7b).
    repeating: bool = False
    expired: bool = False
    #: Objects the effect referred to when it was created, remembered because
    #: the trigger fires later and the references must survive.
    remembered: tuple[int, ...] = field(default_factory=tuple)
