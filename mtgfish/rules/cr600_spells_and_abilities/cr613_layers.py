"""The layer system (CR 613): how continuous effects combine.

This is the part of Magic that simulators get wrong quietly. A creature ends up
3/3 instead of 4/4, nothing crashes, and the statistics are subtly false.

Three rules drive the whole design:

**CR 613.1** - effects apply in seven layers, each addressing one kind of
characteristic, not in the order they were created. Layer 7 has its own
sublayers (CR 613.4), which is why a creature set to 1/1 while carrying three
+1/+1 counters is a 4/4.

**CR 613.6** - which effects apply is determined layer by layer, against the
game state as modified by all previous layers. This is why the whole board is
computed together rather than one object at a time: a static ability removed by
Humility in layer 6 must not go on to produce an effect in layer 7. Computing
one object in isolation cannot see that.

**CR 613.8** - within a layer, timestamp order is the default, but an effect
that *depends* on another waits for it. Dependency is a counterfactual: would
applying B first change what A applies to, or what A does? That question is
answered here by actually trying it, rather than by hard-coding known card
interactions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..cr100_game_concepts.cr106_mana import ManaCost
from ..cr200_parts_of_a_card.characteristics import Characteristics
from ..kernel.enums import CardType, Color, Layer, Zone
from ..kernel.gameobject import GameObject
from ..kernel.ids import NO_PLAYER, ObjectId, PlayerId
from ..kernel.matching import matches
from ..kernel.query import ValueKind
from .abilities import Ability, AbilityKind
from .effects import CONTINUOUS_KINDS, Effect, EffectKind

if TYPE_CHECKING:
    from ..kernel.game import ContinuousEffect, Game


#: Which layer each continuous-effect opcode applies in (CR 613.1).
EFFECT_LAYERS: dict[EffectKind, Layer] = {
    EffectKind.COPY_PERMANENT: Layer.COPY,
    EffectKind.TEXT_CHANGE: Layer.TEXT,
    EffectKind.ADD_TYPE: Layer.TYPE,
    EffectKind.REMOVE_TYPE: Layer.TYPE,
    EffectKind.SET_TYPE: Layer.TYPE,
    EffectKind.SET_COLOR: Layer.COLOR,
    EffectKind.GRANT_ABILITY: Layer.ABILITY,
    EffectKind.REMOVE_ABILITIES: Layer.ABILITY,
    EffectKind.SET_PT: Layer.PT_SET,
    EffectKind.MODIFY_PT: Layer.PT_MODIFY,
    EffectKind.SWITCH_PT: Layer.PT_SWITCH,
}

#: The full application order, sublayers included (CR 613.1, 613.4).
LAYER_ORDER: tuple[Layer, ...] = (
    Layer.COPY,
    Layer.FACE_DOWN,
    Layer.CONTROL,
    Layer.TEXT,
    Layer.TYPE,
    Layer.COLOR,
    Layer.ABILITY,
    Layer.PT_CDA,
    Layer.PT_SET,
    Layer.PT_MODIFY,
    Layer.PT_COUNTERS,
    Layer.PT_SWITCH,
)

#: CR 305.6: basic land types carry intrinsic mana abilities. These are not
#: printed on the card - a land that *becomes* a Swamp gains "{T}: Add {B}"
#: from the type itself, in layer 4. Blood Moon and Urborg are unintelligible
#: without this.
BASIC_LAND_MANA: dict[str, Color] = {
    "Plains": Color.WHITE,
    "Island": Color.BLUE,
    "Swamp": Color.BLACK,
    "Mountain": Color.RED,
    "Forest": Color.GREEN,
}


def layer_for(effect: Effect) -> Layer:
    return EFFECT_LAYERS.get(effect.kind, Layer.ABILITY)


# ---------------------------------------------------------------------------
# Board computation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Application:
    """One continuous effect applying to one object in one layer."""

    ce: ContinuousEffect
    target: ObjectId

    @property
    def timestamp(self) -> int:
        return self.ce.timestamp

    @property
    def is_cda(self) -> bool:
        return self.ce.is_cda


def compute_board(game: Game) -> dict[ObjectId, Characteristics]:
    """Characteristics of every object subject to continuous effects.

    Computed as a whole rather than per object, because CR 613.6 makes each
    layer depend on the result of the previous one across the entire board.

    CR 610.5: an ability a spell gains as its controller casts it applies from
    the moment the spell is on the stack, so stack objects are in scope here
    and not only permanents.
    """
    scope = [game.objects[i] for i in game.battlefield if i in game.objects]
    scope += [game.objects[i] for i in game.stack if i in game.objects]

    state: dict[ObjectId, Characteristics] = {
        obj.id: game.printed_characteristics(obj) for obj in scope
    }
    by_id = {obj.id: obj for obj in scope}

    # Publish the working state so filter evaluation during computation reads
    # partially-applied characteristics instead of recursing back into here.
    previous = game.board_in_progress
    game.board_in_progress = state
    try:
        # Derived once and grouped by layer, rather than rebuilt inside each
        # of them. The per-effect ``_still_exists`` check below is what keeps
        # CR 613.6 honest - an ability stripped in layer 6 stops producing
        # before its layer 7 effect applies - and it reads the live state, so
        # rebuilding the list eight times added nothing but time.
        by_layer = _by_layer(_live_effects(game, state, by_id))

        # CR 613.7a: when an ability is granted, the effect it generates is
        # stamped with the granting effect's timestamp if that is the later
        # one. Recorded as layer 6 runs, because that is where the grants
        # happen and nothing else knows which effect put an ability where.
        grants: dict[ObjectId, list[tuple[object, int]]] = {}

        for layer in LAYER_ORDER:
            _apply_layer(
                game, layer, state, by_id, by_layer.get(int(layer), ()), grants
            )
            if layer is Layer.ABILITY:
                # CR 613.6 with CR 611.3b: an ability *added* in this layer
                # generates a continuous effect of its own, and that effect
                # applies in the layers that follow. The effect list was
                # harvested once, before any layer ran, from the printed
                # characteristics - so a granted static ability appeared on
                # the object and then did nothing at all.
                #
                # Re-harvested once, after the abilities are settled. A
                # granted ability that itself grants another would need a
                # fixed point; that is rarer than this is common, and a
                # second pass here would double-apply the first grant.
                by_layer = _by_layer(_live_effects(game, state, by_id, grants))
    finally:
        game.board_in_progress = previous

    return state


def _apply_layer(
    game: Game,
    layer: Layer,
    state: dict[ObjectId, Characteristics],
    by_id: dict[ObjectId, GameObject],
    effects=(),
    grants: dict[ObjectId, list[tuple[object, int]]] | None = None,
) -> None:
    """Apply every effect belonging to one layer."""
    if layer is Layer.CONTROL:
        _apply_control(game, state, by_id)
        return
    if layer is Layer.FACE_DOWN:
        _apply_face_down(game, state, by_id)
        return
    if layer is Layer.ABILITY:
        # CR 613.1f puts keyword counters in this layer, alongside the effects
        # that add and remove abilities. Applied before them, so an
        # ability-removing effect later in the layer can still strip what a
        # counter granted.
        for object_id, obj in by_id.items():
            state[object_id] = _apply_keyword_counters(obj, state[object_id])
    if layer is Layer.PT_CDA:
        for object_id, obj in by_id.items():
            state[object_id] = _apply_cda(game, obj, state[object_id])
        return
    if layer is Layer.PT_COUNTERS:
        for object_id, obj in by_id.items():
            state[object_id] = _apply_counters(obj, state[object_id])
        return

    effects = list(effects)
    if not effects:
        return

    # Sorted once, not once per effect. Deterministic order is what matters
    # and the set of objects does not change inside a layer; sorting it again
    # for every effect was two and a half million sorts a game.
    ordered = sorted(by_id)

    for ce in order_effects(game, effects, state, by_id):
        # Re-check existence rather than trusting the list built at the top of
        # the layer. An effect ordered before this one may have stripped the
        # ability that generates it, and CR 613.8a counts "the existence of the
        # effect" as something a dependency can change.
        if not _still_exists(state, ce):
            continue
        # Applicability is settled against the state as it stands now, not as
        # it stood when the layer began.
        records = grants is not None and ce.effect.kind is EffectKind.GRANT_ABILITY
        for object_id in ordered:
            obj = by_id[object_id]
            if not _affects(game, ce, obj, state[object_id]):
                continue
            before = state[object_id]
            state[object_id] = apply_effect(game, obj, before, ce)
            if records:
                _record_grant(grants, object_id, before, state[object_id], ce.timestamp)


def _record_grant(
    grants: dict[ObjectId, list[tuple[object, int]]],
    object_id: ObjectId,
    before: Characteristics,
    after: Characteristics,
    timestamp: int,
) -> None:
    """Note which abilities this grant put on the object, and when.

    CR 613.7a needs the granting effect's timestamp, and only the moment of
    granting knows it. A grant appends, so the abilities past the end of the
    previous list are exactly the new ones. The latest grant wins, which is
    what "whichever is later" asks for when one ability arrives twice.
    """
    kept = grants.setdefault(object_id, [])
    for ability in after.abilities[len(before.abilities) :]:
        for index, (known, when) in enumerate(kept):
            if known == ability:
                if timestamp > when:
                    kept[index] = (ability, timestamp)
                break
        else:
            kept.append((ability, timestamp))


def _granted_timestamp(
    grants: dict[ObjectId, list[tuple[object, int]]] | None,
    object_id: ObjectId,
    ability,
) -> int:
    """When this ability was granted to this object, or zero if it was not."""
    if not grants:
        return 0
    for known, when in grants.get(object_id, ()):
        if known == ability:
            return when
    return 0


def _still_exists(state: dict[ObjectId, Characteristics], ce: ContinuousEffect) -> bool:
    """Whether the static ability generating this effect is still present."""
    if not ce.from_static_ability or ce.ability is None:
        return True
    chars = state.get(ce.source)
    if chars is None:
        return False
    return ce.ability in chars.abilities


def _by_layer(effects: list) -> dict[int, list]:
    """Group effects by the layer they apply in."""
    out: dict[int, list] = {}
    for ce in effects:
        out.setdefault(ce.layer, []).append(ce)
    return out


def _live_effects(
    game: Game,
    state: dict[ObjectId, Characteristics],
    by_id: dict[ObjectId, GameObject],
    grants: dict[ObjectId, list[tuple[object, int]]] | None = None,
) -> list[ContinuousEffect]:
    """Continuous effects currently in existence.

    Two sources. Effects from resolved spells persist for their duration
    (CR 611.2). Effects from static abilities are regenerated every time from
    the abilities that currently exist (CR 611.3) - persisting them would keep
    a destroyed Glorious Anthem pumping, and would hide Humility entirely.

    CR 604.2: that regeneration is the rule, not an optimisation. A static
    ability's effect lasts exactly as long as the object stays in the zone
    where the ability works and still has the ability, which is why the zone
    is checked here and ``_still_exists`` checks the ability.
    """
    from ..kernel.game import ContinuousEffect

    # Only effects that belong to a layer. Replacement effects created by
    # resolving spells are stored alongside continuous effects, but ``_affects``
    # says they apply to nothing, so they can neither change a characteristic
    # nor create or block a dependency. What they could do is sit in the
    # pairwise ordering: one game accumulated 426 of them from Increasing
    # Vengeance and spent minutes ordering effects that do nothing.
    out = [
        ce for ce in game.continuous_effects if not ce.expired and ce.effect.kind in EFFECT_LAYERS
    ]

    for object_id, obj in by_id.items():
        if obj.phased_out or obj.zone is not Zone.BATTLEFIELD:
            continue
        for ability in state[object_id].abilities:
            if ability.kind is not AbilityKind.STATIC or ability.unparsed:
                continue
            if not _static_condition_holds(game, obj, ability):
                continue
            # CR 613.7a: the object's timestamp, or the granting effect's,
            # whichever is later. A printed ability has no grant and keeps
            # the object's.
            timestamp = max(
                obj.timestamp, _granted_timestamp(grants, object_id, ability)
            )
            for effect in _continuous_parts(ability.effects):
                out.append(
                    ContinuousEffect(
                        effect=effect,
                        source=obj.id,
                        controller=obj.controller,
                        timestamp=timestamp,
                        layer=int(layer_for(effect)),
                        duration=effect.duration,
                        from_static_ability=True,
                        is_cda=ability.is_characteristic_defining,
                        ability=ability,
                    )
                )
    return out


def _continuous_parts(effects) -> list:
    """The continuous effects inside a static ability, sequences included.

    One sentence often changes two things at once - "equipped creature can't
    be blocked *and has shroud*", "gets +2/+2 and has trample", "has base
    power and toughness 3/3 and gains all creature types" - and the parser
    represents that as a SEQUENCE of two effects in two different layers.

    A SEQUENCE is not itself a continuous kind, so testing the top-level
    opcode dropped *both* halves and the whole ability did nothing. It is the
    same failure as an unread sentence, except that the coverage report calls
    it a success.
    """
    found: list = []
    stack = list(effects)
    while stack:
        effect = stack.pop(0)
        if effect.kind in CONTINUOUS_KINDS:
            found.append(effect)
        elif effect.kind is EffectKind.SEQUENCE:
            stack = list(effect.children) + stack
    return found


def _static_condition_holds(game: Game, obj: GameObject, ability: Ability) -> bool:
    if ability.static_condition.is_always:
        return True
    from ..kernel.conditions import holds

    return holds(game, ability.static_condition, source=obj.id, controller=obj.controller)


def _affects(
    game: Game, ce: ContinuousEffect, obj: GameObject, chars: Characteristics
) -> bool:
    """Whether a continuous effect applies to this object, given ``chars``.

    The two cheap tests come first on purpose: this is called once per effect
    per object per layer per board computation, which is millions of times a
    game, and the expensive half is the filter match at the bottom.

    CR 604.4: the question is asked afresh on every computation and never
    settled, so an Aura or Equipment moved to another permanent stops
    modifying the old one and starts modifying the new one. Nothing here
    targets: a static ability of an attachment modifies what it is on without
    ever choosing it as a target.

    CR 604.7: ``matches`` is called without ``allow_stale``, so a static
    ability cannot reach an object's last known information the way a
    resolving ability can. A creature that has left is simply not affected.
    """
    spec = ce.effect.targets
    if spec is None:
        # No filter means the effect applies to its own source, which is how
        # "this creature gets +1/+1" and every self-buff is expressed. Checked
        # before anything else because it is an integer comparison and it is
        # the commonest shape on the board.
        return obj.id == ce.source

    if obj.phased_out or ce.effect.kind not in EFFECT_LAYERS:
        return False

    return matches(
        game, obj, spec, source=ce.source, controller=ce.controller, override=chars
    )


# ---------------------------------------------------------------------------
# Dependency (CR 613.8)
# ---------------------------------------------------------------------------


def order_effects(
    game: Game,
    effects: list[ContinuousEffect],
    state: dict[ObjectId, Characteristics],
    by_id: dict[ObjectId, GameObject],
) -> list[ContinuousEffect]:
    """Order the effects in one layer (CR 613.7, 613.8).

    Timestamp order by default. An effect that depends on another is applied
    after it (CR 613.8b); a dependency loop falls back to timestamp order
    (CR 613.8c).

    Ordering is between *effects*, not between per-object applications. That
    matters: Blood Moon has to be applied before Urborg's ability everywhere,
    not merely on the one land they happen to share, or a land processed early
    would pick up a Swamp type from an ability that is about to stop existing.
    """
    # CR 613.3: within layers 2-6, characteristic-defining abilities apply
    # first, then everything else in timestamp order. A CDA is not a thing that
    # happened at a moment - it is what the object *is* - so it cannot be
    # jumped ahead of by an effect with an earlier timestamp.
    ordered = sorted(
        effects, key=lambda ce: (0 if ce.is_cda else 1, ce.timestamp, ce.source)
    )
    if len(ordered) < 2:
        return ordered

    count = len(ordered)
    probe = _DependencyProbe(game, ordered, state, by_id)
    depends: list[set[int]] = [set() for _ in range(count)]
    for i in range(count):
        for j in range(count):
            if i != j and probe.depends_on(i, j):
                depends[i].add(j)

    if not any(depends):
        return ordered

    remaining = set(range(count))
    out: list[ContinuousEffect] = []
    while remaining:
        ready = [i for i in remaining if not (depends[i] & remaining)]
        if not ready:
            # A dependency loop: CR 613.8c abandons dependency for the whole
            # loop and applies it in timestamp order instead.
            ready = list(remaining)
        chosen = min(ready, key=lambda i: (ordered[i].timestamp, ordered[i].source, i))
        out.append(ordered[chosen])
        remaining.discard(chosen)
    return out


def _depends_on(
    game: Game,
    a: ContinuousEffect,
    b: ContinuousEffect,
    state: dict[ObjectId, Characteristics],
    by_id: dict[ObjectId, GameObject],
) -> bool:
    """Whether effect ``a`` depends on effect ``b`` (CR 613.8a).

    Answered by trying it rather than by hard-coding card interactions: apply
    B, then ask whether A would still apply to the same objects and would still
    do the same thing to them. All three of CR 613.8a's criteria are covered -
    the existence of A, what A applies to, and what A does.

    The third clause of CR 613.8a excludes any dependency between a
    characteristic-defining ability and an ordinary effect.
    """
    return _DependencyProbe(game, [a, b], state, by_id).depends_on(0, 1)


class _DependencyProbe:
    """``_depends_on`` for every pair in a layer, without repeating work.

    Asking "does A depend on B" for every ordered pair used to recompute, per
    pair, everything about B: which objects it affects and what they look like
    once it has applied. None of that depends on A. With thirty effects in a
    layer - four copies of a deck full of "creatures you control have ..." -
    that was 870 walks of the whole board, each re-running every filter, and
    the board is recomputed after nearly every event. One game took over ten
    minutes and never tripped the action budget, because it was not looping;
    it was just doing the same sums again.

    So each effect's footprint is worked out once, and A's answers against the
    unmodified board are remembered. The state is not mutated while a layer is
    being ordered (ordering happens before any of its effects apply), which is
    what makes remembering them sound. The answers are the ones ``_depends_on``
    always gave, in the same order of checks.
    """

    __slots__ = ("game", "effects", "state", "by_id", "ids", "_footprints", "_applies", "_ops")

    def __init__(self, game, effects, state, by_id) -> None:
        self.game = game
        self.effects = effects
        self.state = state
        self.by_id = by_id
        self.ids = sorted(by_id)
        self._footprints: dict[int, dict[ObjectId, Characteristics]] = {}
        self._applies: dict[tuple[int, ObjectId], bool] = {}
        self._ops: dict[tuple[int, ObjectId], tuple] = {}

    def footprint(self, index: int) -> dict[ObjectId, Characteristics]:
        """Objects this effect applies to, in id order, each as it would become."""
        found = self._footprints.get(index)
        if found is None:
            effect = self.effects[index]
            found = {}
            for object_id in self.ids:
                obj = self.by_id[object_id]
                base = self.state[object_id]
                if _affects(self.game, effect, obj, base):
                    found[object_id] = apply_effect(self.game, obj, base, effect)
            self._footprints[index] = found
        return found

    def _applies_before(self, index: int, object_id: ObjectId) -> bool:
        key = (index, object_id)
        known = self._applies.get(key)
        if known is None:
            known = self._applies[key] = _affects(
                self.game, self.effects[index], self.by_id[object_id], self.state[object_id]
            )
        return known

    def _operation_before(self, index: int, object_id: ObjectId) -> tuple:
        key = (index, object_id)
        known = self._ops.get(key)
        if known is None:
            known = self._ops[key] = _operation(
                self.game, self.by_id[object_id], self.state[object_id], self.effects[index]
            )
        return known

    def depends_on(self, i: int, j: int) -> bool:
        a = self.effects[i]
        b = self.effects[j]
        if a is b or a.is_cda != b.is_cda:
            return False

        footprint = self.footprint(j)

        # "would change the text or the existence of the first effect": B
        # stripping the static ability behind A means A must wait for B - and
        # once it does, it will not be applied at all.
        if a.from_static_ability and a.ability is not None:
            source_chars = self.state.get(a.source)
            if (
                a.source in self.game.objects
                and source_chars is not None
                and a.ability in source_chars.abilities
                and a.source in footprint
                and a.ability not in footprint[a.source].abilities
            ):
                return True

        game = self.game
        for object_id, after_b in footprint.items():
            obj = self.by_id[object_id]
            applies_before = self._applies_before(i, object_id)
            if applies_before != _affects(game, a, obj, after_b):
                return True
            if applies_before and self._operation_before(i, object_id) != _operation(
                game, obj, after_b, a
            ):
                return True
        return False


def _would_remove_source_ability(
    game: Game,
    a: ContinuousEffect,
    b: ContinuousEffect,
    state: dict[ObjectId, Characteristics],
) -> bool:
    """Whether applying ``b`` would strip the ability that generates ``a``."""
    if not a.from_static_ability or a.ability is None:
        return False
    source = game.objects.get(a.source)
    if source is None:
        return False

    source_chars = state.get(source.id)
    if source_chars is None or a.ability not in source_chars.abilities:
        return False
    if not _affects(game, b, source, source_chars):
        return False

    return a.ability not in apply_effect(game, source, source_chars, b).abilities


def _operation(
    game: Game, obj: GameObject, chars: Characteristics, ce: ContinuousEffect
) -> tuple:
    """A signature of *what an effect does*, independent of what it does it to.

    Comparing results would be useless - applying the same effect to different
    starting values naturally gives different answers. What CR 613.8a asks is
    whether the effect's own behaviour changed, so the signature captures the
    opcode and its evaluated quantities.
    """
    effect = ce.effect
    amount = _value(game, effect.amount, ce, obj)
    amount2 = _value(game, effect.amount2, ce, obj)
    return (
        int(effect.kind),
        amount,
        amount2,
        int(effect.types),
        int(effect.colors),
        effect.keywords,
        effect.counter_type,
    )


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def apply_effect(
    game: Game, obj: GameObject, current: Characteristics, ce: ContinuousEffect
) -> Characteristics:
    """Apply one continuous effect to a characteristic set."""
    effect = ce.effect
    kind = effect.kind

    if kind is EffectKind.MODIFY_PT:
        return current.replace(
            power=(current.power or 0) + _value(game, effect.amount, ce, obj),
            toughness=(current.toughness or 0) + _value(game, effect.amount2, ce, obj),
        )

    if kind is EffectKind.SET_PT:
        # A setting effect may define only one half (CR 604.3 CDAs especially).
        # UNCHANGED means "leave it"; treating it as zero turns every such
        # creature into an X/0 that dies to a state-based action at once.
        return current.replace(
            power=(
                current.power
                if effect.amount.kind is ValueKind.UNCHANGED
                else _value(game, effect.amount, ce, obj)
            ),
            toughness=(
                current.toughness
                if effect.amount2.kind is ValueKind.UNCHANGED
                else _value(game, effect.amount2, ce, obj)
            ),
        )

    if kind is EffectKind.SWITCH_PT:
        return current.replace(power=current.toughness, toughness=current.power)

    if kind is EffectKind.SET_COLOR:
        return current.replace(colors=effect.colors)

    if kind is EffectKind.TEXT_CHANGE:
        return _apply_text_change(current, effect)

    if kind is EffectKind.ADD_TYPE:
        subtypes = effect.keywords
        if effect.of_chosen_type:
            # "This creature is the chosen type in addition to its other
            # types" - the type was picked as the permanent entered and is
            # recorded on it, so it is not on the card to be read.
            chooser = game.objects.get(ce.source) if ce is not None else None
            recorded = getattr(chooser, "chosen_type", "") if chooser else ""
            subtypes = (recorded,) if recorded else ()
        line = current.type_line.adding(types=effect.types, subtypes=subtypes)
        return _with_type_line(current, line)

    if kind is EffectKind.REMOVE_TYPE:
        line = current.type_line.removing(types=effect.types, subtypes=effect.keywords)
        return _with_type_line(current, line)

    if kind is EffectKind.SET_TYPE:
        sets_basic_land_type = bool(effect.types & CardType.LAND) and any(
            subtype in BASIC_LAND_MANA for subtype in effect.keywords
        )

        if sets_basic_land_type:
            # CR 305.7: it loses its old *land* types and keeps everything
            # else. Urza's Saga under a Blood Moon becomes a Mountain but is
            # still an Enchantment Land - Saga, and that matters: the
            # state-based action that sacrifices a finished Saga only applies
            # to a Saga "with one or more chapter abilities", and it has none
            # left, so it simply survives as a Mountain.
            removed = tuple(s for s in current.type_line.subtypes if _is_land_type(s))
            # ...and it loses every ability from its rules text, not merely the
            # ones its land types granted. That clause is why Blood Moon turns
            # Urborg off entirely rather than leaving it granting Swamp.
            stripped = current.with_abilities(())
        else:
            removed = current.type_line.subtypes
            stripped = current.with_abilities(
                tuple(a for a in current.abilities if not a.from_land_type)
            )

        line = current.type_line.removing(subtypes=removed)
        line = line.adding(types=effect.types, subtypes=effect.keywords)
        return _with_type_line(stripped, line)

    if kind is EffectKind.GRANT_ABILITY:
        granted = tuple(effect.granted_abilities) + tuple(
            Ability(AbilityKind.STATIC, keyword=word, text=word) for word in effect.keywords
        )
        return current.with_abilities(current.abilities + granted)

    if kind is EffectKind.REMOVE_ABILITIES:
        # Intrinsic land mana abilities are abilities too, and Humility takes
        # them (CR 613.1f applies to all abilities, printed or granted).
        return current.with_abilities(())

    if kind is EffectKind.COPY_PERMANENT:
        return _apply_copy(game, obj, current, ce)

    return current


def _apply_text_change(current: Characteristics, effect: Effect) -> Characteristics:
    """Layer 3: text-changing effects (CR 612).

    "Mountain becomes Island" and friends. CR 612.2 is the constraint that
    makes this narrow rather than a blind find-and-replace: only words *used in
    the correct way* change, so a land type word only changes where it is being
    used as a land type. Working on the parsed subtype list rather than on the
    rules text is what enforces that automatically - a subtype entry is by
    construction a subtype.

    The substitution is carried in ``keywords`` as alternating from/to pairs.
    """
    pairs = effect.keywords
    if len(pairs) < 2:
        return current

    mapping = {pairs[i]: pairs[i + 1] for i in range(0, len(pairs) - 1, 2)}
    line = current.type_line
    swapped = tuple(mapping.get(s, s) for s in line.subtypes)
    if swapped == line.subtypes:
        return current

    # Deduplicate: changing Mountain to Island on a Mountain Island leaves one
    # Island, not two.
    seen: list[str] = []
    for subtype in swapped:
        if subtype not in seen:
            seen.append(subtype)

    from ..cr200_parts_of_a_card.cr205_typeline import TypeLine

    return _with_type_line(current, TypeLine(line.supertypes, line.types, tuple(seen)))


def _is_land_type(subtype: str) -> bool:
    """Whether a subtype is a land type, as opposed to a creature or Saga type.

    Answered from the ingested subtype catalog rather than a hard-coded list,
    so a new land type from a future set is classified correctly with no code
    change.
    """
    from ..cr200_parts_of_a_card.cr205_typeline import DEFAULT_REGISTRY

    return bool(DEFAULT_REGISTRY.types_for(subtype) & CardType.LAND)


def _with_type_line(current: Characteristics, line) -> Characteristics:
    """Set a type line and refresh the intrinsic abilities its types imply.

    CR 305.6: land types carry mana abilities that are not printed anywhere. A
    land that becomes a Swamp gains "{T}: Add {B}" the moment its type changes,
    and loses it again when the type goes.
    """
    kept = tuple(a for a in current.abilities if not a.from_land_type)
    return current.replace(type_line=line, abilities=kept + intrinsic_land_abilities(line))


#: Memoized by type line. The result depends on nothing else, the type line is
#: hashable, and the whole pool has a few hundred distinct land lines - so the
#: cache is tiny and never invalidated. Before this it was rebuilt for every
#: characteristics lookup, which is several hundred thousand times a game.
_INTRINSIC_CACHE: dict[object, tuple[Ability, ...]] = {}


def intrinsic_land_abilities(line) -> tuple[Ability, ...]:
    """The mana abilities a land gets from its basic land types (CR 305.6)."""
    cached = _INTRINSIC_CACHE.get(line)
    if cached is not None:
        return cached
    built = _build_intrinsic_land_abilities(line)
    _INTRINSIC_CACHE[line] = built
    return built


def _build_intrinsic_land_abilities(line) -> tuple[Ability, ...]:
    from ..cr100_game_concepts.cr118_costs import TAP_COST
    from .effects import Effect as _Effect

    if not (line.types & CardType.LAND):
        return ()
    out: list[Ability] = []
    for subtype in line.subtypes:
        color = BASIC_LAND_MANA.get(subtype)
        if color is None:
            continue
        out.append(
            Ability(
                AbilityKind.ACTIVATED,
                effects=(_Effect(EffectKind.ADD_MANA, colors=color, text=f"Add {subtype} mana"),),
                cost=TAP_COST,
                is_mana_ability=True,
                text=f"{{T}}: Add mana ({subtype})",
                from_land_type=True,
            )
        )
    return tuple(out)


def _value(game: Game, value, ce: ContinuousEffect, obj: GameObject | None = None) -> int:
    """Evaluate a value in the context of a continuous effect.

    ``of_affected`` decides whose characteristics "it" means: the object being
    modified, or the ability's source.
    """
    from ..kernel.values import evaluate

    subject = obj.id if (value.of_affected and obj is not None) else ce.source
    return evaluate(game, value, source=subject, controller=ce.controller)


def _apply_copy(
    game: Game, obj: GameObject, current: Characteristics, ce: ContinuousEffect
) -> Characteristics:
    """Layer 1: copy effects (CR 613.2, 707).

    A copy takes only the *copiable values* - the printed characteristics as
    modified by other copy effects, and nothing else. Counters, damage, auras,
    and every effect in layers 2 through 7 are not copied, which is why a copy
    of a pumped creature is not pumped.
    """
    source_obj = game.objects.get(ce.effect.copy_source)
    if source_obj is None:
        return current
    return game.printed_characteristics(source_obj)


def _apply_control(
    game: Game,
    state: dict[ObjectId, Characteristics],
    by_id: dict[ObjectId, GameObject],
) -> None:
    """Layer 2: control-changing effects (CR 613.1b).

    Control is not a characteristic (CR 109.3), so it lives on the object
    rather than in Characteristics. It is still resolved here, in timestamp
    order, so that two competing control effects land the right way round.

    Recomputed from ``base_controller`` every time rather than written once
    and left. Applying only the effects that are currently in force made a
    control change permanent: Act of Treason's effect expired at cleanup, the
    effect was dropped from the list, and nothing ever handed the creature
    back - so a three-mana sorcery read "gain control of target creature"
    with no duration at all.
    """
    changes: dict[ObjectId, tuple[int, PlayerId]] = {}
    for ce in game.continuous_effects:
        if ce.expired or ce.effect.kind is not EffectKind.GAIN_CONTROL:
            continue
        for object_id, obj in by_id.items():
            if not _affects_control(game, ce, obj, state[object_id]):
                continue
            existing = changes.get(object_id)
            if existing is None or ce.timestamp > existing[0]:
                changes[object_id] = (ce.timestamp, ce.controller)

    for object_id, obj in by_id.items():
        change = changes.get(object_id)
        if change is not None:
            controller = change[1]
        elif obj.zone is Zone.BATTLEFIELD:
            # No effect in force means the permanent is controlled by whoever
            # put it onto the battlefield (CR 109.4), which is what reverts
            # it. Only permanents: CR 613.1b's layer is about them, and a
            # spell on the stack is controlled by whoever cast it - which is
            # not always its owner, and is nobody's to hand back.
            controller = obj.base_controller
        else:
            continue
        if controller == NO_PLAYER or obj.controller == controller:
            continue
        obj.controller = controller
        # CR 302.6: a change of control restarts summoning sickness. Handing a
        # creature back counts - it has not been controlled continuously since
        # its new controller's turn began either.
        obj.summoning_sick = True


def _affects_control(
    game: Game, ce: ContinuousEffect, obj: GameObject, chars: Characteristics
) -> bool:
    spec = ce.effect.targets
    if spec is None:
        return obj.id == ce.source
    from ..kernel.matching import matches

    return matches(
        game, obj, spec, source=ce.source, controller=ce.controller, override=chars
    )


def _apply_face_down(
    game: Game,
    state: dict[ObjectId, Characteristics],
    by_id: dict[ObjectId, GameObject],
) -> None:
    """Layer 1b: face-down permanents (CR 613.2b, 708.2).

    A face-down permanent is a 2/2 creature with no name, no mana cost, no
    types beyond Creature, and no abilities - whatever the card underneath
    says.
    """

    for object_id, obj in by_id.items():
        if not obj.face_down:
            continue
        from ..cr200_parts_of_a_card.cr205_typeline import TypeLine

        state[object_id] = Characteristics(
            name="",
            mana_cost=ManaCost(()),
            has_mana_cost=False,
            colors=Color.NONE,
            type_line=TypeLine(types=CardType.CREATURE),
            abilities=(),
            power=2,
            toughness=2,
            text="",
        )


#: A counter that modifies power and toughness: "+1/+1", "-2/-1", "+0/+2".
_PT_COUNTER_RE = re.compile(r"^([+-]\d+)/([+-]\d+)$")

#: CR 122.1b: the keywords a keyword counter can be. A closed list, because a
#: counter named anything else is an ordinary counter and grants nothing.
KEYWORD_COUNTERS = frozenset(
    {
        "flying", "first strike", "double strike", "deathtouch", "decayed",
        "exalted", "haste", "hexproof", "indestructible", "lifelink",
        "menace", "reach", "shadow", "trample", "vigilance",
    }
)


def _apply_keyword_counters(
    obj: GameObject, current: Characteristics
) -> Characteristics:
    """Layer 6: a keyword counter grants its keyword (CR 613.1f, 122.1b).

    The parser produces these - Spontaneous Flight puts a flying counter on a
    creature - and nothing read them, so the counter sat on the permanent
    granting nothing at all.
    """
    if not obj.counters:
        return current
    granted = tuple(
        Ability(AbilityKind.STATIC, keyword=name, text=name)
        for name, count in sorted(obj.counters.items())
        if count > 0 and name.lower() in KEYWORD_COUNTERS
    )
    if not granted:
        return current
    return current.replace(abilities=current.abilities + granted)


def _apply_counters(obj: GameObject, current: Characteristics) -> Characteristics:
    """Layer 7d: counters that change power and toughness (CR 613.4c, 122.1a).

    Applied after every other power/toughness modification, which is why a
    creature set to 1/1 by Humility while carrying three +1/+1 counters is a
    4/4 rather than a 1/1.

    Every "+X/+Y" counter counts, not only +1/+1 and -1/-1. Reading just those
    two left the rest inert: a +2/+2 counter from Tin-Wing Chimera, a +1/+0
    from a pump that uses them, a -2/-2 from Ebon Praetor. Only +1/+1 and
    -1/-1 annihilate in pairs (CR 704.5q), and that stays where it is, in the
    state-based actions.
    """
    if not obj.counters:
        return current
    power = toughness = 0
    for name, count in obj.counters.items():
        if count <= 0:
            continue
        match = _PT_COUNTER_RE.match(name)
        if match:
            power += int(match.group(1)) * count
            toughness += int(match.group(2)) * count
    if not power and not toughness:
        return current
    return current.replace(
        power=(current.power or 0) + power,
        toughness=(current.toughness or 0) + toughness,
    )


def _apply_cda(game: Game, obj: GameObject, current: Characteristics) -> Characteristics:
    """Layer 7a: characteristic-defining abilities (CR 613.4a, 604.3).

    A printed ``*`` arrives here as None. If no CDA defines it, it settles at
    zero on the battlefield, because the rules have no notion of an undefined
    power for a permanent.
    """
    for ability in current.abilities:
        if not ability.is_characteristic_defining:
            continue
        for effect in ability.effects:
            if effect.kind is EffectKind.SET_PT:
                from ..kernel.values import evaluate

                current = current.replace(
                    power=(
                        current.power
                        if effect.amount.kind is ValueKind.UNCHANGED
                        else evaluate(
                            game, effect.amount, source=obj.id, controller=obj.controller
                        )
                    ),
                    toughness=(
                        current.toughness
                        if effect.amount2.kind is ValueKind.UNCHANGED
                        else evaluate(
                            game, effect.amount2, source=obj.id, controller=obj.controller
                        )
                    ),
                )

    # A permanent that is a creature must have a power and a toughness; one
    # that is not has neither (CR 208.3). Defaulting everything to 0/0 would
    # give an enchantment a toughness and hand it to the state-based action
    # that destroys 0-toughness creatures.
    if obj.zone is Zone.BATTLEFIELD and current.is_creature:
        if current.power is None or current.toughness is None:
            current = current.replace(
                power=0 if current.power is None else current.power,
                toughness=0 if current.toughness is None else current.toughness,
            )
    return current


# ---------------------------------------------------------------------------
# Entry point used by Game
# ---------------------------------------------------------------------------


def compute_characteristics(game: Game, obj: GameObject) -> Characteristics:
    """One object's characteristics, from the whole-board computation."""
    if obj.zone not in (Zone.BATTLEFIELD, Zone.STACK):
        base = game.printed_characteristics(obj)
        return _apply_cda(game, obj, base)
    board = game.board(obj)
    return board.get(obj.id) or game.printed_characteristics(obj)
