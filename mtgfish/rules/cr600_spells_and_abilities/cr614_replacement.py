"""Replacement and prevention effects (CR 614, 615, 616).

A replacement effect watches for an event that *would* happen and changes it
before it does. Nothing is undone, because nothing happened in the first place
- which is why "if a creature would die, exile it instead" produces no dies
trigger, and why a permanent that enters tapped was never untapped.

Three rules do the work:

**CR 614.5** - a replacement effect applies at most once to any given event.
Otherwise two Doubling Seasons would loop forever.

**CR 616.1** - when several could apply, the affected object's controller (or
the affected player) chooses the order, one at a time, re-checking what still
applies after each.

**CR 614.15** - a self-replacement effect, one that modifies the very event
that put its own source somewhere, applies before any other.

Rule 903.9 is only *half* here, and the split matters. Since the 2020 rules
update the Commander zone-change rule is two different mechanisms:

* **CR 903.9b** - a commander that would be put into its owner's **hand or
  library** may go to the command zone instead. That is a replacement, and it
  lives in this module.
* **CR 903.9a** - a commander that is **in a graveyard or in exile**, having
  been put there since the last state-based action check, may be moved to the
  command zone by its owner. That is a *state-based action*, and it lives in
  ``sba.py``.

The difference is observable and it is not subtle. A commander that dies really
does go to the graveyard: it died (CR 700.4), every dies-trigger sees it, a
Blood Artist triggers, and only afterwards - when state-based actions are next
checked - does its owner get to move it. Treating that half as a replacement
would silently delete a trigger from every game.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from ..kernel.enums import Zone
from ..kernel.events import Event, EventKind
from ..kernel.ids import NO_OBJECT, NO_PLAYER, ObjectId, PlayerId

if TYPE_CHECKING:
    from ..kernel.game import Game


class ReplacementKind(IntEnum):
    """What a replacement effect does to the event it catches."""

    #: "enters the battlefield tapped"
    ENTERS_TAPPED = 0
    #: "enters with N +1/+1 counters on it"
    ENTERS_WITH_COUNTERS = 1
    #: "if it would enter, instead ..." - send it somewhere else entirely.
    REDIRECT_ZONE_CHANGE = 2
    #: CR 903.9: a commander changing zones may go to the command zone.
    COMMANDER_ZONE_CHOICE = 3
    #: Damage: prevent some or all of it (CR 615).
    PREVENT_DAMAGE = 4
    #: Damage: change the amount, as doubling or halving effects do.
    MODIFY_DAMAGE = 5
    #: Damage: deal it to something else instead (CR 614.9).
    REDIRECT_DAMAGE = 6
    #: Counters: add extra, as Doubling Season does.
    MODIFY_COUNTERS = 7
    #: Cards: draw a different number, or not at all.
    MODIFY_DRAW = 8
    #: The event simply does not happen.
    PREVENT_ENTIRELY = 9
    #: CR 614.12: a self-replacement effect - one printed on the very object
    #: whose event it replaces, like "if this would be put into a graveyard,
    #: exile it instead". CR 616.1 applies it before anything else, so it is
    #: its own kind rather than an ordering flag on the others.
    SELF_REPLACEMENT = 12
    #: Rain of Gore and friends: "if a spell or ability would cause its
    #: controller to gain life, that player loses that much life instead".
    #: The gain never happens, so nothing that watches for life gain sees it.
    LIFE_GAIN_BECOMES_LOSS = 10
    #: Life gain or loss by a different amount (doubling, halving).
    MODIFY_LIFE_CHANGE = 11
    #: CR 614.1: "if an effect would create one or more tokens, it creates
    #: twice that many instead".
    MODIFY_TOKENS = 13
    #: "Lands you control enter untapped" - the counterpart to ENTERS_TAPPED,
    #: and the reason it needs its own kind rather than a negative amount: it
    #: has to be able to *beat* a tapland's own text, which it does by being
    #: applied last (see ``_order_entry_replacements``).
    ENTERS_UNTAPPED = 14


@dataclass(slots=True)
class ReplacementEffect:
    """One active replacement or prevention effect."""

    kind: ReplacementKind
    event_kinds: frozenset[EventKind]
    source: ObjectId = NO_OBJECT
    controller: PlayerId = NO_PLAYER

    #: Which objects' events this catches. None means "the source's own".
    subject: object | None = None
    #: Which players' events this catches.
    players: object | None = None

    #: How much: counters added, damage prevented, and so on.
    amount: int = 0
    #: Applied before ``amount``: "twice that many, plus one" is multiply then
    #: add, and cards exist for each half.
    multiplier: int = 1
    counter_type: str = "+1/+1"
    #: Where a redirect sends the object.
    destination: Zone | None = None
    #: What a redirect sends damage to.
    redirect_to: ObjectId = NO_OBJECT

    #: CR 614.15: applies before every other replacement effect.
    is_self_replacement: bool = False
    #: CR 615.5: a shield that is used up once it applies.
    one_shot: bool = False
    used: bool = False

    text: str = ""

    def __str__(self) -> str:
        return self.text or self.kind.name.lower()


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def _self_replacements_first(effects: list) -> list:
    """CR 616.1: self-replacement effects apply before all others.

    "As this enters, choose a colour" happens before "permanents enter tapped",
    because the object's own replacement of its own event is not something the
    affected player gets to order.
    """
    return sorted(effects, key=lambda ce: 0 if _is_self_replacement(ce) else 1)


def _is_self_replacement(ce) -> bool:
    return getattr(ce, "kind", None) is ReplacementKind.SELF_REPLACEMENT


def apply_replacements(game: Game, event: Event) -> Event | None:
    """Run every applicable replacement effect over an event.

    Returns the modified event, or None if the event is replaced out of
    existence entirely. Each effect applies at most once (CR 614.5), tracked by
    identity, and the set of applicable effects is re-derived after every
    application because applying one can change what the others see
    (CR 616.1).

    CR 616.2 is why the set is re-derived rather than settled once: an effect
    that did not apply to the original event can become applicable to the
    event as another effect has modified it, and the two then combine.
    """
    applied: set[int] = set()
    current = event

    for _ in range(32):  # A hard stop; 32 nested replacements is already absurd.
        candidates = [
            effect
            for effect in _active(game)
            if id(effect) not in applied and _applies(game, effect, current)
        ]
        if not candidates:
            return current

        # CR 614.15: self-replacement first, then the affected player's choice.
        candidates.sort(key=lambda e: (not e.is_self_replacement, e.source))
        chosen = _choose(game, current, candidates)

        applied.add(id(chosen))
        current = _perform(game, chosen, current)
        if chosen.one_shot:
            chosen.used = True
        if current is None:
            return None

    return current


def _active(game: Game) -> list[ReplacementEffect]:
    """Replacements in force: those a resolution registered, plus those a
    permanent's static ability supplies.

    The second half was missing entirely. ``register`` is called only from
    resolution executors, so a replacement *printed on a permanent* - which is
    most of them: Doubling Season, Hardened Scales, Furnace of Rath - was
    parsed, stored on the ability, and then never consulted by anything. The
    card sat on the battlefield doing nothing.

    Derived rather than persisted, for the reason CR 611.3 gives: a
    replacement from a permanent that has left is not in force any more.
    """
    return [e for e in game.replacement_effects if not e.used] + static_replacements(game)


def static_replacements(game: Game) -> list[ReplacementEffect]:
    cached = game.static_replacements_cache
    if cached is not None and game.static_replacements_epoch == game.epoch:
        return cached

    from .abilities import AbilityKind
    from .effects import EffectKind

    out: list[ReplacementEffect] = []
    for object_id in list(game.battlefield):
        obj = game.objects.get(object_id)
        if obj is None or obj.phased_out:
            continue
        for ability in game.characteristics(obj).abilities:
            if ability.kind is not AbilityKind.STATIC or ability.unparsed:
                continue
            for effect in ability.effects:
                if effect.kind is not EffectKind.REPLACEMENT:
                    continue
                if not effect.replacement_kind:
                    continue  # A shape the engine has no kind for.
                out.append(
                    ReplacementEffect(
                        kind=ReplacementKind(effect.replacement_kind),
                        event_kinds=_events_for(
                            ReplacementKind(effect.replacement_kind)
                        ),
                        subject=effect.targets,
                        players=effect.players,
                        source=obj.id,
                        controller=obj.controller,
                        amount=effect.amount.constant,
                        multiplier=effect.multiplier,
                        text=effect.text,
                    )
                )

    game.static_replacements_cache = out
    game.static_replacements_epoch = game.epoch
    return out


def _events_for(kind: ReplacementKind) -> frozenset:
    """Which events a replacement of this kind watches."""
    if kind is ReplacementKind.MODIFY_COUNTERS:
        return frozenset({EventKind.COUNTER_ADDED})
    if kind is ReplacementKind.MODIFY_TOKENS:
        return frozenset({EventKind.TOKEN_CREATED})
    if kind is ReplacementKind.MODIFY_DRAW:
        return frozenset({EventKind.DREW_CARD})
    if kind in (ReplacementKind.ENTERS_TAPPED, ReplacementKind.ENTERS_UNTAPPED):
        # A static "creatures your opponents control enter tapped" watches
        # every entry, not just its own source's. Returning no events at all
        # meant such an ability registered and then never fired.
        return frozenset({EventKind.ENTERS_BATTLEFIELD})
    if kind is ReplacementKind.ENTERS_WITH_COUNTERS:
        return frozenset({EventKind.ENTERS_BATTLEFIELD})
    if kind in (ReplacementKind.MODIFY_DAMAGE, ReplacementKind.PREVENT_DAMAGE):
        return frozenset(
            {EventKind.DAMAGE_DEALT, EventKind.COMBAT_DAMAGE_DEALT}
        )
    if kind is ReplacementKind.MODIFY_LIFE_CHANGE:
        return frozenset({EventKind.LIFE_GAINED, EventKind.LIFE_LOST})
    return frozenset()


def _choose(
    game: Game, event: Event, candidates: list[ReplacementEffect]
) -> ReplacementEffect:
    if len(candidates) > 1 and any(
        c.kind is ReplacementKind.ENTERS_UNTAPPED for c in candidates
    ):
        # CR 616.1 lets the affected object's controller order these. Anyone
        # would apply "enters untapped" after "enters tapped" rather than
        # before, so the tapland's own text is applied first and overridden.
        others = [c for c in candidates if c.kind is not ReplacementKind.ENTERS_UNTAPPED]
        if others:
            return others[0]
    """CR 616.1: the affected object's controller, or the affected player, chooses.

    With no agent to ask, the first in the deterministic order is taken, which
    keeps replays exact.
    """
    if len(candidates) == 1:
        return candidates[0]

    affected = event.player
    if affected == NO_PLAYER and event.object_id != NO_OBJECT:
        obj = game.objects.get(event.object_id)
        if obj is not None:
            affected = obj.controller

    agent = game.agent_for(affected) if affected != NO_PLAYER else None
    if agent is not None and hasattr(agent, "order_replacements"):
        picked = agent.order_replacements(game, affected, event, candidates)
        if picked in candidates:
            return picked
    return candidates[0]


def _applies(game: Game, effect: ReplacementEffect, event: Event) -> bool:
    if event.kind not in effect.event_kinds:
        return False

    if effect.subject is not None:
        obj = game.objects.get(event.object_id)
        if obj is None:
            return False
        from ..kernel.matching import matches

        if not matches(
            game, obj, effect.subject, source=effect.source, controller=effect.controller
        ):
            return False
    elif effect.source != NO_OBJECT and effect.kind in (
        ReplacementKind.ENTERS_TAPPED,
        ReplacementKind.ENTERS_WITH_COUNTERS,
    ):
        # A self-only replacement applies to its own source and nothing else.
        if event.object_id != effect.source:
            return False

    if effect.players is not None and event.player != NO_PLAYER:
        from ..kernel.matching import resolve_players

        allowed = resolve_players(game, effect.players, controller=effect.controller)
        if event.player not in allowed:
            return False

    return True


def _perform(game: Game, effect: ReplacementEffect, event: Event) -> Event | None:
    """Carry out one replacement, returning the modified event."""
    kind = effect.kind

    if kind is ReplacementKind.PREVENT_ENTIRELY:
        game.log.record(game, f"{event.kind.name} is prevented ({effect})", kind="replace")
        return None

    if kind is ReplacementKind.PREVENT_DAMAGE:
        prevented = event.amount if effect.amount <= 0 else min(event.amount, effect.amount)
        remaining = event.amount - prevented
        game.emit_raw(
            Event(
                EventKind.DAMAGE_PREVENTED,
                object_id=event.object_id,
                player=event.player,
                source=event.source,
                amount=prevented,
            )
        )
        if remaining <= 0:
            return None
        return event.with_amount(remaining)

    if kind is ReplacementKind.MODIFY_DAMAGE:
        new_amount = max(0, event.amount + effect.amount)
        return None if new_amount == 0 else event.with_amount(new_amount)

    if kind is ReplacementKind.REDIRECT_DAMAGE:
        return event.replaced(object_id=effect.redirect_to, player=NO_PLAYER)

    if kind in (
        ReplacementKind.MODIFY_COUNTERS,
        ReplacementKind.MODIFY_DRAW,
        ReplacementKind.MODIFY_TOKENS,
        ReplacementKind.MODIFY_DAMAGE,
        ReplacementKind.MODIFY_LIFE_CHANGE,
    ):
        # One arithmetic for the whole family: multiply, then add. Every
        # amount-changing replacement in the game is one or the other -
        # "twice that many", "that many plus one", "double that damage" - and
        # giving each event kind its own formula is how they drifted apart.
        return event.with_amount(
            max(0, event.amount * effect.multiplier + effect.amount)
        )

    if kind is ReplacementKind.LIFE_GAIN_BECOMES_LOSS:
        # The event becomes a different event. The life gain never happens at
        # all, so a lifelink creature under Rain of Gore drains its controller
        # and no "whenever you gain life" trigger fires.
        return event.replaced(kind=EventKind.LIFE_LOST)

    if kind is ReplacementKind.MODIFY_LIFE_CHANGE:
        return event.with_amount(max(0, event.amount + effect.amount))

    if kind is ReplacementKind.ENTERS_TAPPED:
        obj = game.objects.get(event.object_id)
        if obj is not None:
            obj.tapped = True
        return event

    if kind is ReplacementKind.ENTERS_UNTAPPED:
        obj = game.objects.get(event.object_id)
        if obj is not None:
            obj.tapped = False
        return event

    if kind is ReplacementKind.ENTERS_WITH_COUNTERS:
        obj = game.objects.get(event.object_id)
        if obj is not None:
            obj.add_counters(effect.counter_type, effect.amount)
            game.invalidate_characteristics()
        return event

    if kind in (ReplacementKind.REDIRECT_ZONE_CHANGE, ReplacementKind.COMMANDER_ZONE_CHOICE):
        if effect.destination is None:
            return event
        return event.replaced(to_zone=effect.destination)

    return event


# ---------------------------------------------------------------------------
# Commander rule 903.9
# ---------------------------------------------------------------------------


#: CR 903.9b applies to these zones only. Graveyard and exile are handled by
#: the state-based action in ``sba.py``, because that is what the rule says.
_REPLACED_COMMANDER_ZONES = (Zone.HAND, Zone.LIBRARY)


def apply_self_entry_replacements(game: Game, obj) -> None:
    """CR 614.1c: "as this enters" effects, applied from the object's own text.

    These are self-replacement effects (CR 614.15) and they differ from every
    other replacement in one way that matters here: they are supplied by the
    permanent that is entering, which is not yet on the battlefield and so has
    contributed nothing to any registry. A tapland's "enters tapped" therefore
    never fired - the engine had the machinery, the parser produced the effect,
    and nothing connected the two. Every tapland in the format entered
    untapped, which silently turns a whole category of land into a fast land
    and inflates early-turn mana across every deck.

    Applied at the moment of entry rather than registered, because the effect
    exists only for this one event and has no source on the battlefield to
    hang off.
    """
    from ..cr100_game_concepts.actions import add_counters
    from .abilities import AbilityKind
    from .effects import EffectKind

    for ability in game.characteristics(obj).abilities:
        if ability.kind is not AbilityKind.STATIC or ability.unparsed:
            continue
        for effect in ability.effects:
            for node in effect.walk():
                if node.kind is EffectKind.COPY_PERMANENT and _is_entry_copy(node):
                    _enter_as_copy(game, obj, node)
                    continue
                if not _is_self_entry(node):
                    continue
                if node.kind is EffectKind.TAP:
                    obj.tapped = True
                elif node.kind is EffectKind.ADD_COUNTERS:
                    add_counters(
                        game,
                        obj,
                        node.counter_type or "+1/+1",
                        max(1, node.amount.constant),
                        source=obj.id,
                    )


def _is_entry_copy(effect) -> bool:
    """Whether this is "enter as a copy of ..." rather than a copy on resolution.

    Told apart by the same text marker the other entry shapes use. A copy
    effect from a resolving spell - Clone cast normally, Cackling Counterpart -
    goes through the stack and must not be applied here as well.
    """
    return effect.text.startswith("enters as a copy")


def _enter_as_copy(game: Game, obj, effect) -> None:
    """CR 614.1c and 706.2: the permanent enters already being something else.

    The choice is the controller's and it is optional on most of these cards,
    but declining leaves a 0/0 that dies to state-based actions before anyone
    can respond - so with no agent to ask, copying is the default and the
    agent may override it.

    The "except it's a Shapeshifter Rogue in addition to its other types"
    half of the sentence is not applied: the parser consumes that tail without
    modelling it, so the copy is faithful except for the added subtypes. That
    costs a tribal interaction and nothing else, where not copying at all
    costs the whole card.
    """
    from ..cr700_additional_rules.cr707_faces import copy_permanent
    from ..kernel.matching import find

    spec = effect.targets
    if spec is None:
        return

    candidates = [
        other
        for other in find(game, spec, source=obj.id, controller=obj.controller)
        if other.id != obj.id
    ]
    if not candidates:
        return  # Nothing to copy; it enters as itself, which is the rule.

    agent = game.agent_for(obj.controller)
    if agent is not None and hasattr(agent, "choose_copy_target"):
        picked = agent.choose_copy_target(game, obj.controller, candidates)
        if picked is None:
            return
        chosen = picked
    else:
        # The biggest creature, ties broken by id so replays match. A clone
        # copying the best thing on the board is both the obvious play and a
        # function of the game state, which is what keeps a run reproducible.
        chosen = max(
            candidates,
            key=lambda other: (
                game.characteristics(other).power or 0,
                game.characteristics(other).mana_value,
                other.id,
            ),
        )

    copy_permanent(game, obj, chosen)
    game.invalidate_characteristics()
    game.log.record(
        game,
        f"{obj} enters as a copy of {chosen}",
        kind="replace",
        player=obj.controller,
    )


def _is_self_entry(effect) -> bool:
    """Whether this effect is one of the "as it enters" shapes.

    Keyed on the effect's own text rather than a dedicated opcode because the
    parser already distinguishes them there, and inventing a new opcode would
    mean every other consumer had to learn about it.
    """
    from .effects import EffectKind

    if effect.kind not in (EffectKind.TAP, EffectKind.ADD_COUNTERS):
        return False
    # ``None`` targets means the effect's own source (see Effect.targets), and
    # the keyword builders use that form while the grammar writes an explicit
    # self-filter. Both are the permanent that is entering.
    if effect.targets is not None and not effect.targets.source_only:
        return False
    return effect.text.startswith("enters ")


def commander_zone_replacement(game: Game, event: Event) -> Event:
    """CR 903.9b: a commander headed for its owner's hand or library.

    Only those two zones. A commander bound for a graveyard or exile goes
    there - it dies, triggers fire - and its owner moves it afterwards as a
    state-based action (CR 903.9a).
    """
    obj = game.objects.get(event.object_id)
    if obj is None or not obj.is_commander:
        return event
    if event.to_zone not in _REPLACED_COMMANDER_ZONES:
        return event

    if not wants_command_zone(game, obj, event.to_zone):
        return event

    game.log.record(
        game,
        f"{obj} goes to the command zone instead of {event.to_zone.name.lower()}",
        kind="commander",
        player=obj.owner,
    )
    game.emit_raw(
        Event(
            EventKind.COMMANDER_MOVED_TO_COMMAND_ZONE,
            object_id=obj.id,
            player=obj.owner,
            from_zone=event.from_zone,
        )
    )
    return event.replaced(to_zone=Zone.COMMAND)


def wants_command_zone(game: Game, obj, to_zone: Zone) -> bool:
    """Whether the commander's owner takes the CR 903.9 option.

    The choice is always the owner's. Taking it is right in nearly every
    situation - a commander in the command zone is recastable and one in a
    graveyard usually is not - so that is the default when no agent answers.
    """
    agent = game.agent_for(obj.owner)
    if agent is not None and hasattr(agent, "move_commander_to_command_zone"):
        return bool(agent.move_commander_to_command_zone(game, obj.owner, obj, to_zone))
    return True


# ---------------------------------------------------------------------------
# Registration helpers
# ---------------------------------------------------------------------------


def register(game: Game, effect: ReplacementEffect) -> ReplacementEffect:
    game.replacement_effects.append(effect)
    return effect


def clear_expired(game: Game) -> None:
    game.replacement_effects = [e for e in game.replacement_effects if not e.used]
