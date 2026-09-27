"""Who "that player", "they" and "its controller" are.

A player pronoun means a player the ability has already mentioned, and which
one depends on the rest of the ability - never on the sentence it sits in.
"That player" after "whenever an opponent draws a card" is the drawer; after
"target player mills three cards" it is the player targeted. "Its controller"
after "destroy target creature" is the destroyed creature's controller; in
"counter target spell unless its controller pays {3}" it is the spell's; after
"whenever enchanted land becomes tapped" it is the land's.

The noun grammar cannot know any of that, so it reads the pronoun as an
unresolved reference (``PlayerScope.REFERRED_PLAYER``, or ``CONTROLLER_OF`` /
``OWNER_OF`` with no object) and this pass, which sees the whole ability,
binds it to a scope the engine answers from the resolution: the triggering
event's player or object, the objects acted on so far, or the player an
earlier instruction targeted.

What it cannot bind it refuses. A pronoun resolved to the wrong player is a
card that asks the wrong person to pay, and the owner's rule is that such a
card reports "not understood" instead. The engine resolves an unbound
reference to nobody, so nothing unbound is ever allowed through as read.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, replace

from ..rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from ..rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from ..rules.kernel.events import EventKind
from ..rules.kernel.query import ControllerRelation, PlayerFilter, PlayerScope, Value

#: The state is unknowable from the text - two candidates, or one that an
#: optional or conditional instruction may or may not have produced.
AMBIGUOUS = "ambiguous"

#: Referents for "its": what the engine can answer "its controller" about.
REMEMBERED = "remembered"  # the objects the resolution last acted on
TRIGGER_OBJECT = "trigger object"  # the object the triggering event is about
TRIGGER_SOURCE = "trigger source"  # what caused the triggering event

#: Opcodes whose executor asks the resolution who its players are
#: (``resolve._players``), so a bound scope reaches them. Any other opcode
#: reads ``players`` some other way or not at all, and a bound pronoun on it
#: would be carried and ignored - refused instead.
_ANSWERS_PLAYERS = frozenset(
    {
        EffectKind.ADD_ENERGY,
        EffectKind.ADD_EXPERIENCE,
        EffectKind.ADD_MANA,
        EffectKind.ADD_POISON,
        EffectKind.CREATE_TOKEN,
        EffectKind.DAMAGE,
        EffectKind.DISCARD,
        EffectKind.DRAW,
        EffectKind.EXTRA_TURN,
        EffectKind.GAIN_LIFE,
        EffectKind.LOOK_AT_TOP,
        EffectKind.LOSE_LIFE,
        EffectKind.MILL,
        EffectKind.OPTIONAL,
        EffectKind.PAY_COST,
        EffectKind.RESTRICTION,
        EffectKind.SACRIFICE,
        EffectKind.SEARCH_LIBRARY,
        EffectKind.SHUFFLE,
        EffectKind.UNLESS_PAYS,
    }
)

#: Scopes naming one player the engine works out from the resolution. After
#: an instruction naming one of them, "that player" means the same player.
_SINGLE_BOUND = frozenset(
    {
        PlayerScope.TRIGGER_PLAYER,
        PlayerScope.REMEMBERED_CONTROLLER,
        PlayerScope.REMEMBERED_OWNER,
        PlayerScope.TRIGGER_OBJECT_CONTROLLER,
        PlayerScope.TRIGGER_OBJECT_OWNER,
        PlayerScope.TRIGGER_SOURCE_CONTROLLER,
        PlayerScope.CHOSEN_PLAYER,
        PlayerScope.DEFENDING_PLAYER,
    }
)

#: Instructions whose children run in a resolution of their own (CR 603.7,
#: 603.12): nothing this resolution knows travels with them.
_OWN_RESOLUTION = frozenset({EffectKind.REFLEXIVE_TRIGGER, EffectKind.DELAYED_TRIGGER})

#: Instructions whose children always run, so what they establish holds after.
_ALWAYS_RUNS = frozenset({EffectKind.SEQUENCE})

#: Executors that overwrite ``Resolution.remembered`` with something other
#: than the object an instruction acted on (a payment, a discard, a pile).
_CLOBBERS_REMEMBERED = frozenset(
    {
        EffectKind.DISCARD,
        EffectKind.PAY_COST,
        EffectKind.SEEK,
        EffectKind.CONJURE,
        EffectKind.PERPETUALLY,
        EffectKind.LOOK_AT_TOP,
    }
)


@dataclass(slots=True)
class _State:
    #: The last single player the ability named in the third person, as the
    #: bound filter a later "that player" should copy; None for nobody yet.
    player: object = None
    #: What "its" would mean: one of the referent names above, or None.
    obj: object = None


class Unbound(Exception):
    """A reference this pass could not bind; the message says which."""


def bind_ability(ability: Ability) -> Ability:
    """The ability with every player reference bound; raises ``Unbound``."""
    if ability.unparsed:
        return ability
    one_shot = ability.kind in (
        AbilityKind.SPELL,
        AbilityKind.TRIGGERED,
        AbilityKind.ACTIVATED,
    )
    if one_shot:
        state = _initial_state(ability)
        effects = tuple(_bind_run(ability.effects, state))
        ability = replace(ability, effects=effects)
    if _mentions_referred(ability):
        raise Unbound("unbound player reference")
    return ability


def _initial_state(ability: Ability) -> _State:
    state = _State()
    trigger = ability.trigger if ability.kind is AbilityKind.TRIGGERED else None
    if trigger is None:
        return state
    if getattr(trigger, "alternatives", ()):
        # Halves about different things: "it" has no single antecedent.
        return _State(player=AMBIGUOUS, obj=AMBIGUOUS)
    players = trigger.players
    if players is not None and players.scope is not PlayerScope.YOU:
        # CR 603.2: the event matched the trigger's player filter, so the
        # event's player is the player the trigger names.
        state.player = PlayerFilter(PlayerScope.TRIGGER_PLAYER)
    elif players is None and getattr(trigger, "to_player", False):
        # "Whenever this creature deals combat damage to a player": the
        # damage event's player is the player dealt damage.
        state.player = PlayerFilter(PlayerScope.TRIGGER_PLAYER)
    elif (
        players is None
        and trigger.subject is not None
        and trigger.subject.controller is ControllerRelation.OPPONENT
    ):
        # "Whenever a creature an opponent controls dies, that player ...":
        # the opponent named is the one controlling the object the event is
        # about, as it last existed (CR 603.10a).
        state.player = PlayerFilter(PlayerScope.TRIGGER_OBJECT_CONTROLLER)
    elif (
        trigger.subject is None
        and trigger.source is not None
        and trigger.source.controller is ControllerRelation.OPPONENT
    ):
        # "Whenever a source an opponent controls deals damage to you, that
        # player ...": the event captured who controlled the source.
        state.player = PlayerFilter(PlayerScope.TRIGGER_SOURCE_CONTROLLER)
    if trigger.subject is not None and trigger.subject.source_only:
        # "When this Aura leaves the battlefield, enchanted permanent's
        # controller ...", "whenever this enchantment becomes the target of a
        # spell, that spell's controller ...": a card names its own
        # controller "you", so a possessive here is about some other object.
        # Becoming a target is the one event that says which - the spell or
        # ability targeting it, whose controller the event captured when it
        # happened (CR 603.2e). Anything else is refused.
        state.obj = (
            TRIGGER_SOURCE
            if trigger.source is None
            and set(getattr(trigger, "event_kinds", ())) == {EventKind.TARGETED}
            else AMBIGUOUS
        )
    elif trigger.subject is not None and trigger.source is not None:
        state.obj = AMBIGUOUS
    elif trigger.subject is not None:
        state.obj = TRIGGER_OBJECT
    elif trigger.source is not None:
        state.obj = TRIGGER_SOURCE
    return state


def _bind_run(effects, state: _State) -> list[Effect]:
    return [_bind_node(effect, state) for effect in effects]


def _bind_node(effect: Effect, state: _State) -> Effect:
    kind = effect.kind
    changes: dict = {}

    if kind is EffectKind.UNLESS_PAYS:
        # The payer is asked before the guarded instruction runs, but "its"
        # and "they" in "unless its controller pays", "target player loses 3
        # life unless they ..." are about that instruction's object or
        # player - so the guarded half is read first and the payer bound
        # from its shape, against what came before it.
        before = _State(state.player, state.obj)
        changes.update(_bind_children(effect, state))
        guarded = changes["children"][0] if changes.get("children") else None
        if effect.players is not None and _needs_binding(effect.players):
            changes["players"] = _bind_payer(effect, effect.players, before, guarded)
        bound_effect = replace(effect, **changes)
        _advance(bound_effect, state)
        return bound_effect

    if effect.players is not None and _needs_binding(effect.players):
        changes["players"] = _bind(effect, effect.players, state)
    if effect.actor is not None and _needs_binding(effect.actor):
        raise Unbound("player reference on a replacement's actor")
    if effect.restrictions and kind is EffectKind.RESTRICTION:
        bound = []
        for restriction in effect.restrictions:
            players = getattr(restriction, "players", None)
            if players is not None and _needs_binding(players):
                restriction = replace(restriction, players=_bind(effect, players, state))
            bound.append(restriction)
        changes["restrictions"] = tuple(bound)

    if effect.children or effect.otherwise:
        changes.update(_bind_children(effect, state))

    bound_effect = replace(effect, **changes) if changes else effect
    _advance(bound_effect, state)
    return bound_effect


def _bind_children(effect: Effect, state: _State) -> dict:
    kind = effect.kind
    if kind in _OWN_RESOLUTION:
        fresh = _State()
        return {
            "children": tuple(_bind_run(effect.children, fresh)),
            "otherwise": tuple(_bind_run(effect.otherwise, _State())),
        }
    if kind in _ALWAYS_RUNS:
        return {
            "children": tuple(_bind_run(effect.children, state)),
            "otherwise": tuple(_bind_run(effect.otherwise, state)),
        }
    # Branches that may or may not run - a mode, a "may", an "if", the
    # guarded half of an "unless". Each starts from what came before; what a
    # branch establishes is uncertain afterwards unless every branch left the
    # same thing.
    out = {}
    after: list[_State] = []
    for name in ("children", "otherwise"):
        branch = getattr(effect, name)
        if not branch:
            continue
        if kind is EffectKind.CHOOSE_MODE and name == "children":
            modes = []
            for mode in branch:
                local = _State(state.player, state.obj)
                modes.append(_bind_node(mode, local))
                after.append(local)
            out[name] = tuple(modes)
            continue
        local = _State(state.player, state.obj)
        out[name] = tuple(_bind_run(branch, local))
        after.append(local)
    for local in after:
        if local.player != state.player:
            state.player = AMBIGUOUS
        if local.obj != state.obj:
            state.obj = AMBIGUOUS
    return out


def _bind_payer(
    effect: Effect, players: PlayerFilter, before: _State, guarded: Effect | None
) -> PlayerFilter:
    """Who "unless that player / its controller pays" asks."""
    if guarded is not None:
        names_target_player = (
            guarded.is_targeted
            and guarded.targets is None
            and guarded.players is not None
            and guarded.players.scope
            in (PlayerScope.TARGET_PLAYER, PlayerScope.TARGET_OPPONENT)
        )
        if players.scope is PlayerScope.REFERRED_PLAYER:
            if names_target_player:
                return PlayerFilter(PlayerScope.AFFECTED_CONTROLLER)
            if guarded.players is not None and guarded.players.scope in _SINGLE_BOUND:
                # "... deals 3 damage to that creature's controller unless
                # that player pays {3}": the same player, named twice.
                return guarded.players
            if (
                guarded.is_targeted
                and _single(guarded)
                and guarded.targets.controller is ControllerRelation.OPPONENT
            ):
                # "Counter target spell an opponent controls unless they pay
                # {1}": the opponent named is the spell's controller.
                return PlayerFilter(PlayerScope.AFFECTED_CONTROLLER)
        elif players.scope is PlayerScope.CONTROLLER_OF and _single(guarded):
            # One object, so one controller. "Creatures can't block unless
            # their controller pays {X} for each blocking creature" is a tax
            # each controller pays as blockers are declared (CR 509.1d), not
            # one question asked at resolution, and is refused.
            return PlayerFilter(PlayerScope.AFFECTED_CONTROLLER)
    return _bind(effect, players, before)


def _needs_binding(players: PlayerFilter) -> bool:
    if players.scope is PlayerScope.REFERRED_PLAYER:
        return True
    return (
        players.scope in (PlayerScope.CONTROLLER_OF, PlayerScope.OWNER_OF)
        and not players.reference
    )


def _bind(effect: Effect, players: PlayerFilter, state: _State) -> PlayerFilter:
    if effect.kind not in _ANSWERS_PLAYERS:
        raise Unbound(f"player reference on {effect.kind.name}")
    if players.scope is PlayerScope.REFERRED_PLAYER:
        if isinstance(state.player, PlayerFilter):
            return state.player
        raise Unbound("'that player' with no single antecedent")

    owner = players.scope is PlayerScope.OWNER_OF
    if state.obj == REMEMBERED:
        return PlayerFilter(
            PlayerScope.REMEMBERED_OWNER if owner else PlayerScope.REMEMBERED_CONTROLLER
        )
    if state.obj == TRIGGER_OBJECT:
        return PlayerFilter(
            PlayerScope.TRIGGER_OBJECT_OWNER
            if owner
            else PlayerScope.TRIGGER_OBJECT_CONTROLLER
        )
    if state.obj == TRIGGER_SOURCE and not owner:
        # CR 603.3d-style capture: the event recorded who controlled its
        # source when it happened.
        return PlayerFilter(PlayerScope.TRIGGER_SOURCE_CONTROLLER)
    raise Unbound("'its controller' with no single antecedent")


def _advance(effect: Effect, state: _State) -> None:
    """What a later pronoun means once this instruction has been read."""
    from ..rules.cr600_spells_and_abilities.resolve import (
        _NAMING_ONLY,
        _REMEMBERING,
        _is_per_player_sacrifice,
    )

    kind = effect.kind
    # "This creature gets +X/+X": describing the source changes nothing the
    # resolution remembers (``resolve._execute_one``), so nor does it here.
    naming_self = (
        kind in _NAMING_ONLY and effect.targets is not None and effect.targets.source_only
    )
    players = effect.players
    if players is not None:
        scope = players.scope
        if scope in _SINGLE_BOUND:
            state.player = players
        elif scope in (PlayerScope.TARGET_PLAYER, PlayerScope.TARGET_OPPONENT):
            # Only an executor that reads its players through the resolution
            # records whom it targeted for a later "that player".
            state.player = (
                PlayerFilter(PlayerScope.CHOSEN_PLAYER)
                if effect.targets_a_player
                and kind in _ANSWERS_PLAYERS
                else AMBIGUOUS
            )
        elif scope is not PlayerScope.YOU and kind not in (
            EffectKind.SEQUENCE,
            EffectKind.CHOOSE_MODE,
        ):
            # "Each opponent", "an opponent", the UNLESS_PAYS payer peeked
            # from its child: nothing "that player" could safely copy.
            state.player = AMBIGUOUS

    if naming_self:
        pass
    elif kind in _REMEMBERING and effect.targets is not None:
        if _is_per_player_sacrifice(effect):
            state.obj = AMBIGUOUS
        elif _single(effect):
            state.obj = REMEMBERED
            named = _player_named_by(effect)
            if named is not None:
                state.player = named
        else:
            state.obj = AMBIGUOUS
    elif kind is EffectKind.COUNTER_SPELL and effect.targets is not None:
        state.obj = REMEMBERED if _single(effect) else AMBIGUOUS
    elif kind in _CLOBBERS_REMEMBERED or (
        kind is EffectKind.SACRIFICE and effect.targets is not None
    ):
        state.obj = AMBIGUOUS
    elif effect.targets is not None and not (
        effect.targets.source_only
        or effect.targets.remembered
        or effect.targets.trigger_source
    ):
        # "Up to one target creature gets -2/-0. Its controller mills two
        # cards": a new object was named, and this instruction does not tell
        # the engine which - so "its" no longer has an answer it can give.
        state.obj = AMBIGUOUS


def _player_named_by(effect: Effect):
    """The player an instruction on one object names along the way, if any.

    "Destroy target creature *an opponent controls*. That player ..." and
    "Return target permanent to *its owner's* hand. Then that player ..."
    name a player without a player phrase of their own; which one is read
    off the object as it was (CR 608.2h). Both at once is two candidates.
    """
    spec = effect.targets
    by_control = spec is not None and spec.controller is ControllerRelation.OPPONENT
    by_owner = effect.kind is EffectKind.RETURN_TO_HAND
    if by_control and by_owner:
        return AMBIGUOUS
    if by_control:
        return PlayerFilter(PlayerScope.REMEMBERED_CONTROLLER)
    if by_owner:
        return PlayerFilter(PlayerScope.REMEMBERED_OWNER)
    return None


def _single(effect: Effect) -> bool:
    """Whether the instruction acts on exactly one object."""
    spec = effect.targets
    if spec is None:
        return False
    if spec.remembered or spec.trigger_source:
        return True
    count = spec.count
    if isinstance(count, int):
        count = Value.of(count)
    one = count is not None and count.is_constant and count.constant == 1
    if effect.is_targeted:
        return count is None or one
    return one and not spec.up_to


def _mentions_referred(root) -> bool:
    """Whether an unresolved "that player" is left anywhere in the IR."""
    stack = [root]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, PlayerFilter):
            if node.scope is PlayerScope.REFERRED_PLAYER:
                return True
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            for field in dataclasses.fields(node):
                value = getattr(node, field.name, None)
                if value is None or isinstance(value, (str, int, float, bool)):
                    continue
                stack.append(value)
        elif isinstance(node, (tuple, list, frozenset, set)):
            stack.extend(item for item in node if not isinstance(item, (str, int)))
    return False
