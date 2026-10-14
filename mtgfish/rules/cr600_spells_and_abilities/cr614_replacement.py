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
    #: Life *gain* by a different amount ("you gain twice that much life
    #: instead"). It watches gains only: a life-gain doubler that also doubled
    #: its controller's life loss is a different card.
    MODIFY_LIFE_CHANGE = 11
    #: Life *loss* by a different amount ("they lose twice that much life
    #: instead"). Its own kind for the same reason - the two events are
    #: different words on the card and different events in the game.
    MODIFY_LIFE_LOSS = 3000
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

    #: Damage events: which *sources'* damage is caught - "if a source you
    #: control would deal damage", "prevent all damage that would be dealt by
    #: non-Spider creatures". ``subject``/``players`` are the recipient, which
    #: is a different object: reading the source filter against the damaged
    #: permanent tripled the damage Fiery Emancipation's controller *took*.
    damage_source: object | None = None
    #: CR 615: "dealt to and dealt by" one object - the shield catches damage
    #: whose recipient *or* whose source matches ``subject``.
    both_ways: bool = False
    #: Counter and token events: the player doing the putting or creating -
    #: "if *you* would put one or more counters", "if an opponent would".
    #: Matched against the controller of the event's source.
    actor: object | None = None
    #: "If an *effect* would put counters": counters that are part of a cost
    #: (CR 118) are not put by an effect, and neither are the turn-based lore
    #: counters of CR 714.3b. Both reach the event with no source object.
    effects_only: bool = False
    #: Zone changes: where the object would have gone, and where from, for
    #: "if a card would be put into a graveyard from anywhere" (``None`` is
    #: any zone). ``destination`` is where it goes instead.
    watched_to_zone: Zone | None = None
    watched_from_zone: Zone | None = None
    #: A gate on the replacement as a whole: "during your turn", "as long as
    #: ...". ``None`` is always.
    condition: object | None = None
    #: CR 611.2b: how long a replacement made by a resolving spell lasts, and
    #: the turn it began, which the "next turn" durations need. Zero is until
    #: used - or, for a static ability's, while the permanent has it.
    duration: int = 0
    created_turn: int = 0

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
        if chosen.one_shot and chosen.kind is not ReplacementKind.PREVENT_DAMAGE:
            # A prevention shield of a stated size is used up by amount, not
            # by the first event it touches (CR 615.7) - ``_perform`` spends it.
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

    out: list[ReplacementEffect] = []
    for object_id in list(game.battlefield):
        obj = game.objects.get(object_id)
        if obj is None or obj.phased_out:
            continue
        for ability in game.characteristics(obj).abilities:
            if ability.kind is not AbilityKind.STATIC or ability.unparsed:
                continue
            if any(effect.is_unparsed for effect in ability.effects):
                # Partly read: none of it applies, rather than the half that was.
                continue
            gate = None if ability.static_condition.is_always else ability.static_condition
            for effect, condition in _replacement_parts(ability.effects, gate):
                built = replacement_from_effect(
                    effect,
                    source=obj.id,
                    controller=obj.controller,
                    condition=condition,
                )
                if built is not None:
                    out.append(built)

    game.static_replacements_cache = out
    game.static_replacements_epoch = game.epoch
    return out


def _replacement_parts(effects, gate):
    """The replacement and prevention effects inside a static ability, with
    whatever condition guards each.

    SEQUENCEs are walked for the same reason the layer system walks them, and
    a CONDITIONAL with no "otherwise" is a gate on what it wraps: "During your
    turn, prevent all damage that would be dealt to this creature" parses to a
    CONDITIONAL around a PREVENT_DAMAGE, and the prevention applies exactly
    while the condition holds (CR 604.1: a static ability is always "on", and
    what it says is checked continuously).
    """
    from ..kernel.query import Condition, ConditionKind
    from .effects import EffectKind

    for effect in effects:
        if effect.kind in (EffectKind.REPLACEMENT, EffectKind.PREVENT_DAMAGE):
            yield effect, gate
        elif effect.kind is EffectKind.SEQUENCE:
            yield from _replacement_parts(effect.children, gate)
        elif effect.kind is EffectKind.CONDITIONAL and not effect.otherwise:
            inner = effect.condition
            if gate is not None:
                inner = Condition(
                    kind=ConditionKind.AND, operands=(gate, inner), text="and"
                )
            yield from _replacement_parts(effect.children, inner)


#: Event kinds that are damage being dealt (CR 120).
_DAMAGE_EVENTS = frozenset({EventKind.DAMAGE_DEALT, EventKind.COMBAT_DAMAGE_DEALT})


def replacement_from_effect(
    effect,
    *,
    source: ObjectId,
    controller: PlayerId,
    condition=None,
    duration: int = 0,
    created_turn: int = 0,
) -> ReplacementEffect | None:
    """The replacement effect an ``Effect`` describes, or ``None``.

    One builder for both places a replacement comes from - a permanent's
    static ability (CR 614.1, 615.1) and a resolving spell or ability that
    creates one for a duration (CR 611.2) - so the two cannot read the same
    words into different events. ``None`` for a REPLACEMENT the engine has no
    kind for, which is never registered.
    """
    from .effects import EffectKind

    if effect.kind is EffectKind.PREVENT_DAMAGE:
        return ReplacementEffect(
            kind=ReplacementKind.PREVENT_DAMAGE,
            event_kinds=_damage_events(effect.keywords),
            subject=effect.targets,
            players=effect.players,
            damage_source=effect.damage_source,
            both_ways=effect.both_ways,
            source=source,
            controller=controller,
            # A stated amount is a shield that is used up (CR 615.7); "all"
            # is the -1 sentinel, which never is.
            amount=effect.amount.constant,
            condition=_gate(condition, effect),
            duration=duration,
            created_turn=created_turn,
            text=effect.text or "prevent damage",
        )
    if effect.kind is not EffectKind.REPLACEMENT or not effect.replacement_kind:
        return None
    kind = ReplacementKind(effect.replacement_kind)
    events = _events_for(kind)
    if kind in (ReplacementKind.MODIFY_DAMAGE, ReplacementKind.PREVENT_DAMAGE):
        events = _damage_events(effect.keywords)
    return ReplacementEffect(
        kind=kind,
        event_kinds=events,
        subject=effect.targets,
        players=effect.players,
        damage_source=effect.damage_source,
        both_ways=effect.both_ways,
        actor=effect.actor,
        effects_only="an effect" in effect.keywords,
        source=source,
        controller=controller,
        amount=effect.amount.constant,
        multiplier=effect.multiplier,
        counter_type=effect.counter_type,
        destination=effect.zone,
        watched_to_zone=effect.event_zone,
        watched_from_zone=effect.from_zone,
        condition=_gate(condition, effect),
        duration=duration,
        created_turn=created_turn,
        text=effect.text,
    )


def _gate(condition, effect):
    """The ability's gate and the effect's own condition, together."""
    from ..kernel.query import Condition, ConditionKind

    own = None if effect.condition.is_always else effect.condition
    if condition is None:
        return own
    if own is None:
        return condition
    return Condition(kind=ConditionKind.AND, operands=(condition, own), text="and")


def _damage_events(keywords) -> frozenset:
    """"combat damage" and "noncombat damage" are narrower than "damage".

    Dropping the qualifier made every Fog also stop a Lightning Bolt, and made
    a noncombat-damage doubler double combat damage as well.
    """
    if "combat" in keywords:
        return frozenset({EventKind.COMBAT_DAMAGE_DEALT})
    if "noncombat" in keywords:
        return frozenset({EventKind.DAMAGE_DEALT})
    return _DAMAGE_EVENTS


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
        return _DAMAGE_EVENTS
    if kind in (ReplacementKind.MODIFY_LIFE_CHANGE, ReplacementKind.LIFE_GAIN_BECOMES_LOSS):
        return frozenset({EventKind.LIFE_GAINED})
    if kind is ReplacementKind.MODIFY_LIFE_LOSS:
        return frozenset({EventKind.LIFE_LOST})
    if kind is ReplacementKind.REDIRECT_ZONE_CHANGE:
        return frozenset({EventKind.ZONE_CHANGE})
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

    if effect.condition is not None and not effect.condition.is_always:
        from ..kernel.conditions import holds

        if not holds(game, effect.condition, source=effect.source, controller=effect.controller):
            return False

    # Each family of event says who is involved in its own fields, so each is
    # asked its own question. One generic test read "the object" of a damage
    # event - the permanent being damaged - against a filter describing the
    # damage's *source*.
    if event.kind in _DAMAGE_EVENTS:
        return _damage_applies(game, effect, event)
    if event.kind is EventKind.TOKEN_CREATED:
        return _token_applies(game, effect, event)
    if event.kind is EventKind.COUNTER_ADDED:
        # "+1/+1 counters" names a kind; Hardened Scales does not add a -1/-1
        # counter. An empty kind is "one or more counters" of any kind.
        if effect.counter_type and (not event.data or event.data[0] != effect.counter_type):
            return False
        if effect.effects_only and event.source == NO_OBJECT:
            return False
        if not _actor_matches(game, effect, event):
            return False
    if event.kind is EventKind.ZONE_CHANGE:
        if effect.watched_to_zone is not None and event.to_zone is not effect.watched_to_zone:
            return False
        if (
            effect.watched_from_zone is not None
            and event.from_zone is not effect.watched_from_zone
        ):
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


def _damage_applies(game: Game, effect: ReplacementEffect, event: Event) -> bool:
    """CR 120, 614.9, 615: a damage event has a recipient and a source.

    ``subject`` and ``players`` describe the recipient - an object, a player,
    or either - and ``damage_source`` the source. A shield "dealt to and dealt
    by" one object catches the damage whichever way round it is.
    """
    if effect.both_ways:
        return _recipient_matches(game, effect, event) or _source_matches(
            game, effect, effect.subject, event
        )
    if not _recipient_matches(game, effect, event):
        return False
    if effect.damage_source is not None and not _source_matches(
        game, effect, effect.damage_source, event
    ):
        return False
    return True


def _recipient_matches(game: Game, effect: ReplacementEffect, event: Event) -> bool:
    """Who is being dealt the damage: nothing named means anyone."""
    subject, players = effect.subject, effect.players
    if subject is None and players is None:
        return True
    if event.object_id != NO_OBJECT:
        if subject is None:
            # "Damage that would be dealt to you" names a player and no
            # object. The event's ``player`` for damage to a permanent is its
            # controller, which is not who is being dealt the damage.
            return False
        obj = game.objects.get(event.object_id)
        if obj is None:
            return False
        from ..kernel.matching import matches

        return matches(
            game, obj, subject, source=effect.source, controller=effect.controller
        )
    if event.player == NO_PLAYER:
        return False
    if players is not None:
        from ..kernel.matching import resolve_players

        return event.player in resolve_players(
            game, players, controller=effect.controller
        )
    return bool(getattr(subject, "includes_players", False))


def _source_matches(game: Game, effect: ReplacementEffect, spec, event: Event) -> bool:
    """Whether the damage's source fits ``spec``.

    Last-known information is allowed (CR 609.7b): a source that has left the
    battlefield by the time its damage is dealt is still the source, as it
    last existed.
    """
    if spec is None:
        return True
    obj = game.objects.get(event.source)
    if obj is None:
        return False
    from ..kernel.matching import matches

    return matches(
        game,
        obj,
        spec,
        source=effect.source,
        controller=effect.controller,
        allow_stale=True,
    )


def _actor_matches(game: Game, effect: ReplacementEffect, event: Event) -> bool:
    """"If *you* would put ..." - the controller of what is doing it."""
    if effect.actor is None:
        return True
    obj = game.objects.get(event.source)
    if obj is None:
        return False
    from ..kernel.matching import resolve_players

    return obj.controller in resolve_players(
        game, effect.actor, controller=effect.controller
    )


def _token_applies(game: Game, effect: ReplacementEffect, event: Event) -> bool:
    """CR 111.1: who the tokens would be created for, and what they are.

    ``players`` is "under your control"; ``subject`` narrows the tokens
    ("creature tokens", "one or more Treasure tokens"), read against the
    definition the tokens are about to be made from, because none of them
    exists yet.
    """
    if not _actor_matches(game, effect, event):
        return False
    if effect.players is not None:
        from ..kernel.matching import resolve_players

        if event.player not in resolve_players(
            game, effect.players, controller=effect.controller
        ):
            return False
    if effect.subject is not None:
        spec = event.data[0] if event.data else None
        if spec is None or not token_spec_matches(spec, effect.subject):
            return False
    return True


def token_spec_matches(spec, wanted) -> bool:
    """Whether a token about to be created fits a type description.

    Only the constraints a token definition can answer are asked - card types,
    subtypes and colours. The grammar only builds token filters out of those,
    so nothing a filter says is left unasked.
    """
    types = int(spec.types)
    if wanted.types_all and types & int(wanted.types_all) != int(wanted.types_all):
        return False
    if wanted.types_any and not types & int(wanted.types_any):
        return False
    if wanted.types_none and types & int(wanted.types_none):
        return False
    subtypes = set(spec.subtypes)
    if wanted.subtypes_all and not set(wanted.subtypes_all) <= subtypes:
        return False
    if wanted.subtypes_any and not set(wanted.subtypes_any) & subtypes:
        return False
    if wanted.subtypes_none and set(wanted.subtypes_none) & subtypes:
        return False
    colors = int(spec.colors)
    if wanted.colors_all and colors & int(wanted.colors_all) != int(wanted.colors_all):
        return False
    if wanted.colors_any and not colors & int(wanted.colors_any):
        return False
    return not (wanted.colors_none and colors & int(wanted.colors_none))


def _perform(game: Game, effect: ReplacementEffect, event: Event) -> Event | None:
    """Carry out one replacement, returning the modified event."""
    kind = effect.kind

    if kind is ReplacementKind.PREVENT_ENTIRELY:
        game.log.record(game, f"{event.kind.name} is prevented ({effect})", kind="replace")
        return None

    if kind is ReplacementKind.PREVENT_DAMAGE:
        prevented = event.amount if effect.amount <= 0 else min(event.amount, effect.amount)
        remaining = event.amount - prevented
        if effect.one_shot and effect.amount > 0:
            # CR 615.7: "the next 3 damage" is a shield of three, spent a
            # point at a time across however many events it takes.
            effect.amount -= prevented
            if effect.amount <= 0:
                effect.used = True
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

    if kind is ReplacementKind.REDIRECT_DAMAGE:
        return event.replaced(object_id=effect.redirect_to, player=NO_PLAYER)

    if kind in (
        ReplacementKind.MODIFY_COUNTERS,
        ReplacementKind.MODIFY_DRAW,
        ReplacementKind.MODIFY_TOKENS,
        ReplacementKind.MODIFY_DAMAGE,
        ReplacementKind.MODIFY_LIFE_CHANGE,
        ReplacementKind.MODIFY_LIFE_LOSS,
    ):
        # One arithmetic for the whole family: multiply, then add. Every
        # amount-changing replacement in the game is one or the other -
        # "twice that many", "that many plus one", "double that damage" - and
        # giving each event kind its own formula is how they drifted apart.
        # Damage had drifted: it had a branch of its own, above this one,
        # that added and never multiplied, so every damage doubler in the
        # format dealt the damage it would have dealt anyway.
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

    _saga_enters_with_lore_counters(game, obj)
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
                if node.kind is EffectKind.ENTERS_AS_CHOICE:
                    become_chosen_characteristics(game, obj, node)
                    continue
                if node.kind is EffectKind.BECOME_PREPARED:
                    # CR 722.3a: "This creature enters prepared."
                    from ..cr700_additional_rules.cr722_preparation import (
                        become_prepared,
                    )

                    become_prepared(game, obj)
                elif node.kind is EffectKind.TAP:
                    obj.tapped = True
                elif node.kind is EffectKind.ADD_COUNTERS:
                    add_counters(
                        game,
                        obj,
                        node.counter_type or "+1/+1",
                        max(1, node.amount.constant),
                        source=obj.id,
                    )


def _saga_enters_with_lore_counters(game: Game, obj) -> None:
    """CR 714.3a and 714.3b: a Saga's intrinsic "enters with" lore counters.

    Printed on no card - the chapter symbols imply it - so it is applied from
    the Saga subtype rather than from a parsed ability. The counters are put
    on with ``add_counters`` so the counter event is seen: entering with a
    lore counter is what triggers chapter I (CR 714.2b).

    Without read ahead, one counter (CR 714.3a). With read ahead (CR 702.155b,
    714.3b), as it enters its controller chooses a number between one and its
    final chapter number and it enters with that many. The agent is asked
    through ``choose_read_ahead(game, player, obj, final)``; without one the
    choice is one, which is the ordinary Saga's start.
    """
    from ..cr100_game_concepts.actions import add_counters
    from ..cr300_card_types.cr300_card_types import final_chapter

    chars = game.characteristics(obj)
    if not chars.has_subtype("Saga"):
        return
    amount = 1
    if chars.has_keyword("Read Ahead"):
        final = final_chapter(chars)
        chooser = getattr(game.agent_for(obj.controller), "choose_read_ahead", None)
        if chooser is not None and final > 1:
            picked = chooser(game, obj.controller, obj, final)
            if isinstance(picked, int) and 1 <= picked <= final:
                amount = picked
    add_counters(game, obj, "lore", amount, source=obj.id)


def become_chosen_characteristics(game: Game, obj, effect) -> None:
    """CR 208.2b: "it becomes your choice of a 3/3 creature, a 2/2 creature
    with flying, or ...", as it enters or is turned face up.

    The controller picks one of ``effect.children``; without an agent to ask,
    the first, so a replay picks the same. The option's effects apply to this
    object from now on, from the object itself, as the resolution machinery
    applies any continuous effect: CR 611.2c settles them on this object, so
    the choice ends when CR 400.7 makes it a new one.

    DIVERGENCE: CR 208.2b makes the chosen values copiable (CR 707.2); here
    they are continuous effects, so a copy of the permanent copies the card
    as printed instead.
    """
    from .resolve import Resolution, execute

    options = effect.children
    if not options:
        return
    index = 0
    chooser = getattr(game.agent_for(obj.controller), "choose_entry_option", None)
    if chooser is not None:
        picked = chooser(game, obj.controller, obj, options)
        if isinstance(picked, int) and 0 <= picked < len(options):
            index = picked
    execute(Resolution(game=game, source=obj.id, controller=obj.controller), (options[index],))
    game.invalidate_characteristics()
    game.log.record(
        game,
        f"{obj} becomes {options[index].text or f'option {index + 1}'}",
        kind="replace",
        player=obj.controller,
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

    if effect.kind is EffectKind.ENTERS_AS_CHOICE:
        return True
    if effect.kind not in (
        EffectKind.TAP, EffectKind.ADD_COUNTERS, EffectKind.BECOME_PREPARED
    ):
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
