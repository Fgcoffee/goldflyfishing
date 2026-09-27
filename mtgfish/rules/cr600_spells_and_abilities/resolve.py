"""Executing effects: the Effect IR interpreter.

Every opcode in ``effects.py`` is dispatched here. This is the other half of
the project's central safety property: the parser may only emit opcodes that
appear in ``EXECUTORS``, and every executor enforces its own rules. A mis-parse
can therefore produce the *wrong* effect, but never an illegal one - a
"destroy" that the parser invented from nowhere still respects indestructible,
still checks the target is on the battlefield, and still cannot touch a
permanent with hexproof it was never allowed to target.

``EffectKind.UNPARSED`` has no executor and does nothing at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Callable

from ..cr100_game_concepts import actions
from ..kernel.enums import Duration, Zone
from ..kernel.events import Event, EventKind
from ..kernel.gameobject import GameObject
from ..kernel.ids import NO_OBJECT, NO_PLAYER, ObjectId, PlayerId
from ..kernel.query import PlayerScope, ValueKind
from .effects import CONTINUOUS_KINDS, Effect, EffectKind

if TYPE_CHECKING:
    from ..kernel.game import Game


@dataclass(slots=True)
class Resolution:
    """Everything an executing effect needs to know about its own context."""

    game: Game
    source: ObjectId
    controller: PlayerId
    #: The stack object being resolved, when there is one.
    stack_object: GameObject | None = None
    #: Targets chosen at announcement (CR 601.2c), one tuple per targeting
    #: effect in the order the effects appear.
    targets: tuple = ()
    x_value: int = 0
    #: How big the triggering event was, for "that many" and "that much".
    event_amount: int = 0
    #: Objects the resolution has referred to, so later sentences can say "it".
    remembered: list[ObjectId] = field(default_factory=list)
    #: Index of the next targeting effect, used to line effects up with the
    #: targets chosen for them.
    target_index: int = 0
    #: CR 605.3b: a mana ability resolves with no stack object at all, so the
    #: modes chosen for it have nowhere else to travel. Set only when there is
    #: no stack object to read them off.
    chosen_modes: tuple[int, ...] = ()
    #: CR 706.2, 706.4: the results of the dice this resolution has rolled,
    #: after modifiers and after any ignored roll was dropped. What comes
    #: after the roll reads them through ``ValueKind.DIE_ROLL_RESULT``.
    die_results: tuple[int, ...] = ()
    #: CR 106.1: the colour picked for "add one mana of any color" when the
    #: payer chose it - the mana planner does, to pay the colour the cost needs.
    #: ``Color.NONE`` leaves the old deterministic first-colour pick.
    mana_color: int = 0
    #: The player a per-player instruction is currently being carried out for
    #: - what ``PlayerScope.THAT_PLAYER`` means (CR 701.55a).
    that_player: PlayerId = NO_PLAYER
    #: CR 608.2c: how much this resolution has itself done so far, by event
    #: kind - life lost, life gained, damage dealt - so "you gain life equal
    #: to the life lost this way" can read it. Totals, not counts.
    this_way: dict = field(default_factory=dict)
    #: The cards a "look at / reveal the top N" instruction set aside, in
    #: library order, for "from among them" and "the rest" to choose among
    #: (``ObjectFilter.from_pile``). A card leaves the pile when something is
    #: done with it; what is left when the last instruction runs is "the rest".
    pile: list[ObjectId] = field(default_factory=list)
    #: The event a triggered ability triggered on, for a resolution with no
    #: stack object to carry it - a triggered mana ability (CR 605.4a).
    trigger_event: object | None = None
    #: The players an earlier instruction of this resolution targeted, for
    #: "that player" after "target player ..." (``PlayerScope.CHOSEN_PLAYER``).
    chosen_players: list[PlayerId] = field(default_factory=list)

    def targets_for(self, effect: Effect) -> tuple[ObjectId, ...]:
        """The targets chosen for this effect at announcement."""
        if not effect.is_targeted:
            return ()
        if self.target_index < len(self.targets):
            chosen = self.targets[self.target_index]
            self.target_index += 1
            return tuple(chosen)
        return ()


def execute(resolution: Resolution, effects: tuple[Effect, ...]) -> None:
    """Run a list of effects in order (CR 608.2a)."""
    for effect in effects:
        # CR 608.2c: instructions are followed one after another, so what one
        # does does not happen at the same time as what the next does - a
        # "whenever one or more" ability sees them as separate (CR 603.2c).
        resolution.game.event_batch += 1
        execute_one(resolution, effect)


#: Opcodes whose subject a following sentence can refer to as "it". A draw or
#: a life gain has no object to remember, so they are absent rather than
#: remembering nothing and clearing what came before.
_REMEMBERING = frozenset(
    {
        EffectKind.DESTROY,
        EffectKind.EXILE,
        EffectKind.SACRIFICE,
        EffectKind.TAP,
        EffectKind.UNTAP,
        # Provoke's "if you do, untap that creature" (CR 702.39a).
        EffectKind.BLOCKS_SOURCE_IF_ABLE,
        EffectKind.RETURN_TO_HAND,
        EffectKind.PUT_ONTO_BATTLEFIELD,
        EffectKind.CREATE_TOKEN,
        EffectKind.ADD_COUNTERS,
        EffectKind.GAIN_CONTROL,
        EffectKind.COPY_PERMANENT,
        EffectKind.SEARCH_LIBRARY,
        EffectKind.DAMAGE,
        # "Reveal a creature card from among them and put it into your hand.
        # If it's legendary, ..." - "it" is the card that moved, not the
        # whole pile the look before it remembered.
        EffectKind.MOVE_ZONE,
        EffectKind.PUT_ON_LIBRARY,
        # "Counter target spell. Its controller mills three cards" - the
        # spell, as it was on the stack (CR 608.2h), is who "its" asks about.
        EffectKind.COUNTER_SPELL,
    }
)


#: Opcodes whose amounts "this way" can refer back to, and the events that
#: measure them. Only leaves: a SEQUENCE is tallied through its children, so
#: wrapping it too would count everything twice.
_TALLIED = frozenset(
    {
        EffectKind.DAMAGE,
        EffectKind.LOSE_LIFE,
        EffectKind.GAIN_LIFE,
        EffectKind.FIGHT,
    }
)


def _tally_events(game) -> dict[int, int]:
    """Running totals of the amounts "this way" can ask about, this turn.

    Read off the turn's event history, which every life change and every
    damage event already feeds - so damage that became life loss, lifelink
    that became life gain and a replacement that changed an amount are all
    counted as what actually happened rather than as what was asked for.
    """
    from ..kernel.events import EventKind as _EK

    wanted = (
        int(_EK.LIFE_LOST),
        int(_EK.LIFE_GAINED),
        int(_EK.DAMAGE_DEALT),
        int(_EK.COMBAT_DAMAGE_DEALT),
    )
    totals = dict.fromkeys(wanted, 0)
    for key, amount in game.turn_history.items():
        if len(key) == 2 and key[0] in totals:
            totals[key[0]] += amount
    return totals


def execute_one(resolution: Resolution, effect: Effect) -> None:
    if effect.kind in _TALLIED:
        before = _tally_events(resolution.game)
        _execute_one(resolution, effect)
        after = _tally_events(resolution.game)
        for kind, total in after.items():
            if total > before[kind]:
                resolution.this_way[kind] = (
                    resolution.this_way.get(kind, 0) + total - before[kind]
                )
        return
    _execute_one(resolution, effect)


def _execute_one(resolution: Resolution, effect: Effect) -> None:
    executor = EXECUTORS.get(effect.kind)
    if executor is None:
        # No executor: either UNPARSED, or an opcode the engine has declared
        # but not implemented. Either way it does nothing, loudly.
        resolution.game.log.record(
            resolution.game,
            f"Effect not executed ({effect.kind.name}): {effect.text or effect}",
            kind="unimplemented",
            player=resolution.controller,
        )
        return

    if (
        effect.kind in _REMEMBERING
        and effect.targets is not None
        and not _is_per_player_sacrifice(effect)
    ):
        # Captured *before* the effect runs: an object that dies to it is
        # exactly the one the next sentence wants to talk about, and after
        # the fact it is a different object (CR 400.7).
        #
        # ``targets_for`` advances a cursor as it hands out each targeting
        # effect's choices, so the peek has to put it back - otherwise the
        # executor reads the *next* effect's targets and the spell resolves
        # onto the wrong thing.
        cursor = resolution.target_index
        acted_on = [obj.id for obj in _objects(resolution, effect)]
        resolution.target_index = cursor

        executor(resolution, effect)
        if acted_on:
            resolution.remembered = acted_on
        return

    executor(resolution, effect)


# ---------------------------------------------------------------------------
# Selecting what an effect applies to
# ---------------------------------------------------------------------------


def _objects(
    resolution: Resolution, effect: Effect, *, chosen=None
) -> list[GameObject]:
    """The objects an effect acts on.

    Targeted effects use the targets chosen at announcement, re-checked for
    legality here because a target that became illegal is simply skipped
    (CR 608.2b). Untargeted effects match their filter fresh at resolution,
    which is why a Wrath of God kills creatures that arrived after it was cast.
    """
    game = resolution.game

    if effect.targets is not None and effect.targets.from_pile:
        return _from_pile(resolution, effect)

    # CR 608.2: a pronoun refers to whatever the resolution last acted on.
    # Answering it with the source instead would quietly redirect half the
    # removal spells in the pool at the card that cast them.
    if effect.targets is not None and effect.targets.remembered:
        found = []
        for object_id in resolution.remembered:
            obj = game.objects.get(object_id)
            # CR 400.7 makes a moved object a new one, and ``remembered`` was
            # recorded before the move - so "exile it, then cast it" would
            # look at the husk left behind and find it in the wrong zone.
            # An effect that acts on what it just moved means the object it
            # became.
            while obj is not None and obj.superseded_by:
                obj = game.objects.get(obj.superseded_by)
            if obj is not None:
                found.append(obj)
        return found

    if effect.targets is not None and effect.targets.trigger_source:
        # "Counter that spell or ability" (CR 702.21a): the object whose
        # action the ability triggered on, and only while it is still where
        # the filter says - a spell that has already left the stack is a new
        # object (CR 400.7) and is not countered.
        event = _trigger_event(resolution)
        obj = game.objects.get(event.source) if event is not None else None
        if obj is None or not obj.is_live:
            return []
        zones = effect.targets.zones
        return [obj] if not zones or obj.zone in zones else []

    if effect.is_targeted:
        from ..kernel.matching import matches

        out = []
        # ``chosen`` lets a caller that already read the targets pass them in,
        # so the cursor is not advanced twice for one effect.
        picked = resolution.targets_for(effect) if chosen is None else chosen
        for object_id in picked:
            if object_id < 0:
                continue  # A player target; ``_targeted_players`` handles it.
            obj = game.objects.get(object_id)
            if obj is None:
                continue
            if effect.targets is not None and not matches(
                game,
                obj,
                effect.targets,
                source=resolution.source,
                controller=resolution.controller,
            ):
                # CR 608.2b: an illegal target is skipped, not substituted.
                game.log.record(game, f"Target {obj} is no longer legal", kind="fizzle")
                continue
            out.append(obj)
        return out

    if effect.targets is None:
        obj = game.objects.get(resolution.source)
        return [obj] if obj is not None else []

    from ..kernel.matching import find

    matching = find(
        game, effect.targets, source=resolution.source, controller=resolution.controller
    )
    return _narrowed_to_count(resolution, effect, matching)


def _from_pile(resolution: Resolution, effect: Effect) -> list[GameObject]:
    """The cards of the pile a filter picks out ("a creature card from among
    them", "two of them", "the rest").

    Only cards still where they were looked at count: one already put into a
    hand is a new object (CR 400.7) and no longer "among them", and one put
    back on top was taken out of the pile when it was. The filter's zones are
    ignored - the pile is where the cards are - and its count is chosen by the
    effect's controller like any other untargeted count (CR 608.2d).
    """
    from ..kernel.matching import matches

    game = resolution.game
    loose = replace(effect.targets, from_pile=False, count=None, up_to=False)
    candidates: list[GameObject] = []
    for object_id in resolution.pile:
        obj = game.objects.get(object_id)
        if obj is None or not obj.is_live:
            continue
        if matches(
            game,
            obj,
            replace(loose, zones=frozenset({obj.zone})),
            source=resolution.source,
            controller=resolution.controller,
        ):
            candidates.append(obj)
    return _narrowed_to_count(resolution, effect, candidates)


def _narrowed_to_count(
    resolution: Resolution, effect: Effect, matching: list[GameObject]
) -> list[GameObject]:
    """An untargeted effect that names a number affects that many, not all.

    "Choose a creature you control" and "each creature you control" are
    different sentences, and the filter says which by carrying a count. The
    count was read only when the effect targeted, so every untargeted one
    acted on the whole matching set - "put a +1/+1 counter on a creature you
    control" put one on all of them.

    The choice belongs to the effect's controller (CR 608.2). With no agent to
    ask, the first few in ``find``'s deterministic order are taken, which
    keeps a replay reproducible.
    """
    spec = effect.targets
    if spec is not None and spec.count is None and spec.up_to and matching:
        return _any_number_of(resolution.game, resolution.controller, effect, matching)
    if spec is None or spec.count is None or len(matching) <= 1:
        return matching
    wanted = _value_of(resolution, spec.count)
    if wanted <= 0:
        # "Up to" nothing, or a count that evaluated to zero: CR 107.1b makes
        # it no objects rather than every object.
        return [] if spec.up_to else matching
    if wanted >= len(matching):
        return matching

    agent = resolution.game.agent_for(resolution.controller)
    chooser = getattr(agent, "choose_objects", None)
    if chooser is not None:
        picked = chooser(
            resolution.game, resolution.controller, effect, list(matching), wanted
        )
        kept = [obj for obj in matching if obj in (picked or ())][:wanted]
        if len(kept) == wanted:
            return kept
    return matching[:wanted]


def _any_number_of(
    game, player_id: PlayerId, effect: Effect, matching: list[GameObject]
) -> list[GameObject]:
    """"Sacrifice any number of lands", "exile any number of creature cards
    from your graveyard": the player picks how many, from none to all of them
    (CR 107.1c), as the effect resolves (CR 608.2d).

    The agent is asked with the whole matching set as the ceiling, and any
    subset it returns - including none - is the choice. With no agent to ask,
    all of them are taken, as the largest number "up to N" takes.
    """
    chooser = getattr(game.agent_for(player_id), "choose_objects", None)
    if chooser is None:
        return matching
    picked = chooser(game, player_id, effect, list(matching), len(matching))
    if picked is None:
        return matching
    return [obj for obj in matching if obj in picked]


def _value_of(resolution: Resolution, value) -> int:
    from ..kernel.values import evaluate

    return evaluate(
        resolution.game,
        value,
        source=resolution.source,
        controller=resolution.controller,
        x_value=resolution.x_value,
        event_amount=resolution.event_amount,
        die_results=resolution.die_results,
        remembered=tuple(resolution.remembered),
        this_way=resolution.this_way,
    )


def _targeted_players(resolution: Resolution, effect: Effect, chosen) -> list[PlayerId]:
    """Players among an effect's chosen targets (CR 115.4).

    Takes the already-read target list rather than reading it again: the
    cursor that hands targets out advances as it goes, so a second read would
    return the *next* effect's targets - which is how the first version of
    this silently dealt no damage at all.
    """
    from ..kernel.ids import is_player_target, target_player

    return [target_player(target) for target in chosen if is_player_target(target)]


def _trigger_event(resolution: Resolution):
    """The event a resolving triggered ability triggered on, if it has one."""
    stack_object = resolution.stack_object
    if stack_object is None:
        return resolution.trigger_event
    return getattr(stack_object, "trigger_event", None)


def _last_known(resolution: Resolution, object_id: ObjectId, *, owner: bool):
    """The controller or owner of an object as it last existed (CR 608.2h).

    The object a zone change left behind keeps its old controller - that is
    what ``superseded_by`` preserves - so it is read as it is, not followed
    to the card it became, whose controller is its owner (CR 400.7).
    """
    obj = resolution.game.objects.get(object_id)
    if obj is None:
        return NO_PLAYER
    return obj.owner if owner else obj.controller


def _defending_players(resolution: Resolution) -> list[PlayerId]:
    """"Defending player" (CR 506.2, 802.2a).

    An ability of an attacking creature - or one that refers to an attacking
    creature, such as the creature whose attack triggered it - means the
    player that creature is attacking, or the controller or protector of the
    permanent it attacks. Otherwise, with a single defending player, that
    player. With several and nothing to say which, nobody: guessing one would
    be a different card.
    """
    game = resolution.game
    combat = getattr(game, "combat", None)
    attacking = getattr(combat, "attacking", None) or {}
    if not attacking:
        return []
    event = _trigger_event(resolution)
    for object_id in (resolution.source, getattr(event, "object_id", NO_OBJECT)):
        if object_id in attacking:
            return [attacking[object_id]]
    defenders = list(dict.fromkeys(attacking.values()))
    return defenders if len(defenders) == 1 else []


def _referred_players(resolution: Resolution, effect: Effect) -> list[PlayerId] | None:
    """Players named by reference to something else the ability mentioned.

    ``None`` when the effect's scope is not one of these, so the caller goes
    on to the ordinary scopes. Each is answered from what this resolution
    knows - the triggering event, the objects acted on so far, the targets
    chosen for an earlier instruction - which is why ``resolve_players``,
    which knows only the game, cannot answer them.
    """
    spec = effect.players
    if spec is None:
        return None
    scope = spec.scope
    if scope is PlayerScope.REFERRED_PLAYER:
        # Never bound by the parser: nobody, rather than a guess.
        return []
    if scope is PlayerScope.DEFENDING_PLAYER:
        return _defending_players(resolution)
    if scope is PlayerScope.CHOSEN_PLAYER:
        return [
            pid for pid in resolution.chosen_players
            if not resolution.game.player(pid).has_lost
        ]
    event = _trigger_event(resolution)
    if scope is PlayerScope.TRIGGER_PLAYER:
        if event is None or event.player == NO_PLAYER:
            return []
        return [event.player]
    if scope in (PlayerScope.TRIGGER_OBJECT_CONTROLLER, PlayerScope.TRIGGER_OBJECT_OWNER):
        if event is None or event.object_id == NO_OBJECT:
            return []
        found = _last_known(
            resolution,
            event.object_id,
            owner=scope is PlayerScope.TRIGGER_OBJECT_OWNER,
        )
        return [] if found == NO_PLAYER else [found]
    if scope in (PlayerScope.REMEMBERED_CONTROLLER, PlayerScope.REMEMBERED_OWNER):
        owner = scope is PlayerScope.REMEMBERED_OWNER
        found = [_last_known(resolution, oid, owner=owner) for oid in resolution.remembered]
        return [pid for pid in dict.fromkeys(found) if pid != NO_PLAYER]
    if scope is PlayerScope.AFFECTED_CONTROLLER:
        # "Counter target spell unless its controller pays {3}": the payer is
        # asked before the guarded instruction runs, so its object is peeked
        # at without spending the target cursor it will read.
        # "Target player loses 3 life unless they ...": a player target of
        # the guarded instruction is the player it acts on.
        if not effect.children:
            return []
        guarded = effect.children[0]
        cursor = resolution.target_index
        players: list[PlayerId] = []
        if guarded.is_targeted:
            chosen = resolution.targets_for(guarded)
            objects = _objects(resolution, guarded, chosen=chosen)
            players = _targeted_players(resolution, guarded, chosen)
        else:
            objects = _objects(resolution, guarded)
        resolution.target_index = cursor
        found = players + [obj.controller for obj in objects]
        return [
            pid for pid in dict.fromkeys(found)
            if not resolution.game.player(pid).has_lost
        ]
    return None


def _players(resolution: Resolution, effect: Effect) -> list[PlayerId]:
    from ..kernel.matching import resolve_players
    from ..kernel.query import YOU

    if effect.is_targeted and effect.targets is None:
        # "Target player draws two cards", "target opponent loses that much
        # life": the player was chosen as the spell or ability went on the
        # stack (CR 601.2c, 603.3d), and that choice is who it happens to.
        # Resolving the scope instead read "target opponent" as *every*
        # opponent - one Sanguine Bond drain hit the whole table - and
        # "target player" as nobody, so Sign in Blood drew no cards. Two
        # players cannot tell those apart, which is how it went unseen.
        game = resolution.game
        chosen = resolution.targets_for(effect)
        picked = [
            player_id
            for player_id in _targeted_players(resolution, effect, chosen)
            # CR 608.2b: a player who has left the game is no longer legal.
            if not game.player(player_id).has_lost
        ]
        # "Target player mills three cards. That player ..." - the later
        # sentence means the player chosen here (CR 608.2c).
        resolution.chosen_players = list(picked)
        return picked

    referred = _referred_players(resolution, effect)
    if referred is not None:
        return referred

    if effect.players is not None and effect.players.scope is PlayerScope.THAT_PLAYER:
        return [] if resolution.that_player == NO_PLAYER else [resolution.that_player]
    if (
        effect.players is not None
        and effect.players.scope is PlayerScope.TRIGGER_SOURCE_CONTROLLER
    ):
        # CR 702.21a: ward's "that player" controls the spell or ability that
        # targeted - read off the event the ability triggered on, which
        # captured the controller as it happened (CR 603.3d).
        event = _trigger_event(resolution)
        if event is None or event.source_controller == NO_PLAYER:
            return []
        return [event.source_controller]
    return resolve_players(
        resolution.game, effect.players or YOU, controller=resolution.controller
    )


def _amount(resolution: Resolution, effect: Effect, *, second: bool = False) -> int:
    from ..kernel.values import evaluate

    value = effect.amount2 if second else effect.amount
    return evaluate(
        resolution.game,
        value,
        source=resolution.source,
        controller=resolution.controller,
        x_value=resolution.x_value,
        event_amount=resolution.event_amount,
        die_results=resolution.die_results,
        remembered=tuple(resolution.remembered),
        this_way=resolution.this_way,
    )


def _condition_holds(resolution: Resolution, effect: Effect) -> bool:
    if effect.condition.is_always:
        return True
    from ..kernel.conditions import holds

    remembered = tuple(resolution.remembered)
    if _asks_about_affected(effect.condition) and effect.children:
        # "Counter target instant spell if it's blue": the referent is what
        # the guarded effect would act on. Peeked without spending the
        # target cursor, exactly as ``execute_one`` does.
        cursor = resolution.target_index
        remembered = tuple(
            obj.id for obj in _objects(resolution, effect.children[0])
        )
        resolution.target_index = cursor
    stack_object = resolution.stack_object
    return holds(
        resolution.game,
        effect.condition,
        source=resolution.source,
        controller=resolution.controller,
        # What "it" means in "if it's blue" - only the resolution knows.
        remembered=remembered,
        # And in a triggered ability, what the trigger event was about.
        event=getattr(stack_object, "trigger_event", None),
    )


def _asks_about_affected(condition) -> bool:
    from ..kernel.query import ConditionKind

    if condition.kind is ConditionKind.AFFECTED_MATCHES:
        return True
    return any(_asks_about_affected(c) for c in condition.operands)


# ---------------------------------------------------------------------------
# Control flow
# ---------------------------------------------------------------------------


def _do_sequence(resolution: Resolution, effect: Effect) -> None:
    execute(resolution, effect.children)


def _do_conditional(resolution: Resolution, effect: Effect) -> None:
    if _condition_holds(resolution, effect):
        execute(resolution, effect.children)
    else:
        execute(resolution, effect.otherwise)


def _do_repeat(resolution: Resolution, effect: Effect) -> None:
    for _ in range(max(0, _amount(resolution, effect))):
        execute(resolution, effect.children)


def _do_optional(resolution: Resolution, effect: Effect) -> None:
    """"You may ..." - the controller decides on resolution.

    Routed through the agent so a bot can decline; with no agent the default is
    to take the option, which is right far more often than not.
    """
    if effect.players is not None and effect.players.scope not in _CONTROLLER_CHOOSES:
        _another_player_may(resolution, effect)
        return
    agent = resolution.game.agent_for(resolution.controller)
    if agent is None or agent.choose_optional(resolution.game, resolution.controller, effect):
        execute(resolution, effect.children)


#: "May" scopes still decided by the ability's controller: "you may", and the
#: plural forms, whose per-player handling is not built yet.
_CONTROLLER_CHOOSES = frozenset(
    {
        PlayerScope.YOU,
        PlayerScope.EACH_PLAYER,
        PlayerScope.EACH_OPPONENT,
        PlayerScope.OPPONENT,
    }
)


def _another_player_may(resolution: Resolution, effect: Effect) -> None:
    """"That player may pay {2}", "its controller may search their library".

    CR 608.2d: the player the instruction names makes the choice, and the
    instruction is theirs to carry out - the sentence has no other subject,
    so its "search", "pay" and "put" are done by that player (the parser
    refuses such a sentence if it also says "you"). Carried out by making
    them the resolution's "you" for the length of it. Nobody named - a
    referent that no longer exists - means nothing happens.
    """
    game = resolution.game
    for player_id in _players(resolution, effect):
        agent = game.agent_for(player_id)
        if agent is not None and not agent.choose_optional(game, player_id, effect):
            continue
        saved = resolution.controller
        resolution.controller = player_id
        try:
            execute(resolution, effect.children)
        finally:
            resolution.controller = saved


def _do_unless_pays(resolution: Resolution, effect: Effect) -> None:
    """"... unless that player pays {1}." (Rhystic Study and its whole family.)

    The payer is named by the effect and is usually an opponent, not the
    controller - which is why this cannot reuse OPTIONAL, whose choice always
    belongs to the controller.

    With no agent to ask, the default is to **pay**. That direction is chosen
    deliberately: this simulator exists to measure the deck being goldfished,
    and assuming opponents never pay would make every tax effect look far
    better than it is. An optimistic default flatters the deck under test,
    which is the one bias a measuring tool must not have.
    """
    game = resolution.game
    payers = _players(resolution, effect)
    if not payers:
        execute(resolution, effect.children)
        return

    from .cr601_casting import can_pay_cost, pay_cost

    for player_id in payers:
        cost = effect.pay_cost
        if cost is None or not can_pay_cost(
            game, player_id, cost, source=resolution.source
        ):
            execute(resolution, effect.children)
            continue
        agent = game.agent_for(player_id)
        willing = True
        if agent is not None and hasattr(agent, "choose_pay"):
            willing = agent.choose_pay(game, player_id, cost, effect)
        # ``can_pay_cost`` is an upper bound, so a willing player can still
        # fail to pay. A payment that did not happen must not waive the
        # effect, or every "unless that player pays" is free to dodge.
        if not (willing and pay_cost(game, player_id, cost, source=resolution.source)):
            execute(resolution, effect.children)


def _do_nothing(resolution: Resolution, effect: Effect) -> None:
    return


# ---------------------------------------------------------------------------
# Cards and zones
# ---------------------------------------------------------------------------


def _do_draw(resolution: Resolution, effect: Effect) -> None:
    count = _amount(resolution, effect)
    for player_id in _players(resolution, effect):
        resolution.game.draw(player_id, count)


def _do_mill(resolution: Resolution, effect: Effect) -> None:
    count = _amount(resolution, effect)
    game = resolution.game
    milled: list[ObjectId] = []
    for player_id in _players(resolution, effect):
        top = list(game.player(player_id).library[: max(0, count)])
        actions.mill(game, player_id, count, source=resolution.source)
        # "Mill three cards. You may put a land card from among them into
        # your hand" - the milled cards, as the objects they became in the
        # graveyard (CR 400.7), are the pile the next sentence chooses from.
        for object_id in top:
            obj = game.objects.get(object_id)
            while obj is not None and obj.superseded_by:
                obj = game.objects.get(obj.superseded_by)
            if obj is not None and obj.zone is Zone.GRAVEYARD:
                milled.append(obj.id)
    resolution.pile = milled


def _do_destroy(resolution: Resolution, effect: Effect) -> None:
    for obj in _objects(resolution, effect):
        actions.destroy(resolution.game, obj, source=resolution.source)


def _do_exile(resolution: Resolution, effect: Effect) -> None:
    """Exile, and for an "until" exile arrange the return (CR 610.3).

    "Exile target creature until this leaves the battlefield" is one one-shot
    effect now and a second one waiting on the named event. Only the first
    half existed: the parser read the duration and the executor ignored it, so
    every Banisher Priest was a Swords to Plowshares.
    """
    game = resolution.game
    until_source_leaves = effect.duration == int(Duration.WHILE_SOURCE_PERSISTS)
    if until_source_leaves and not _source_is_on_the_battlefield(resolution):
        # CR 610.3a, 610.3b: the named event has already happened, so nothing
        # moves at all - the creature is not exiled and then returned, it is
        # never exiled.
        return

    returning: list[ObjectId] = []
    # CR 406.3: "exile it face down".
    face_down = "face down" in effect.keywords
    for obj in _objects(resolution, effect):
        exiled = actions.exile(
            game,
            obj,
            source=resolution.source,
            link_id=_link_of(resolution),
            face_down=face_down,
        )
        if until_source_leaves and exiled is not None:
            returning.append(exiled.id)
    if returning:
        _return_when_the_source_leaves(resolution, tuple(returning))


def _link_of(resolution: Resolution) -> int:
    """CR 607.1: the link id of the ability resolving, zero for a spell."""
    stack_object = resolution.stack_object
    ability = getattr(stack_object, "ability", None) if stack_object else None
    return getattr(ability, "link_id", 0) or 0


def _source_is_on_the_battlefield(resolution: Resolution) -> bool:
    source = resolution.game.objects.get(resolution.source)
    return source is not None and source.is_permanent


def _return_when_the_source_leaves(
    resolution: Resolution, exiled: tuple[ObjectId, ...]
) -> None:
    """The second one-shot effect of an "until" exile (CR 610.3).

    DIVERGENCE. CR 610.3 creates the return as a one-shot effect immediately
    after the named event, using no stack. It is set up here as a delayed
    triggered ability (CR 603.7), which is the only "do this later" machinery
    the engine has, so the return goes on the stack and can be responded to.
    What returns, when, and to whom is right; the response window is not.
    """
    from ..kernel.query import ObjectFilter
    from .abilities import TriggerCondition

    create_delayed_trigger(
        resolution,
        TriggerCondition(
            event_kinds=frozenset({EventKind.LEAVES_BATTLEFIELD}),
            # The event is about an object that has already gone, so the
            # subject is matched against last known information (CR 603.6e).
            subject=ObjectFilter(specific=(resolution.source,), zones=frozenset()),
            functions_in=frozenset(Zone),
            uses_last_known_information=True,
            text="when the exiling permanent leaves the battlefield",
        ),
        (
            Effect(
                EffectKind.PUT_ONTO_BATTLEFIELD,
                targets=ObjectFilter(specific=exiled, zones=frozenset({Zone.EXILE})),
                # CR 610.3c: under its owner's control.
                under_owners_control=True,
                text="return the exiled card to the battlefield",
            ),
        ),
    )


def _is_per_player_sacrifice(effect: Effect) -> bool:
    """"Each opponent sacrifices a creature": named players, each choosing
    from their own permanents - not a filter over the whole board."""
    spec = effect.targets
    return (
        effect.kind is EffectKind.SACRIFICE
        and effect.players is not None
        and not effect.is_targeted
        and spec is not None
        and not spec.source_only
        and not spec.specific
        and not spec.remembered
    )


def _do_sacrifice(resolution: Resolution, effect: Effect) -> None:
    if not _is_per_player_sacrifice(effect):
        for obj in _objects(resolution, effect):
            actions.sacrifice(resolution.game, obj, source=resolution.source)
        return

    # CR 701.21a: only a permanent its controller controls can be sacrificed,
    # and CR 608.2d: the player sacrificing makes the choice.
    from ..kernel.matching import find

    game = resolution.game
    spec = effect.targets
    everything = find(game, spec, source=resolution.source, controller=resolution.controller)
    sacrificed: list[ObjectId] = []
    for player_id in _players(resolution, effect):
        theirs = [obj for obj in everything if obj.controller == player_id]
        for obj in _sacrifice_choice(resolution, effect, theirs, player_id):
            sacrificed.append(obj.id)
            actions.sacrifice(game, obj, source=resolution.source)
    if sacrificed:
        resolution.remembered = sacrificed


def _sacrifice_choice(
    resolution: Resolution, effect: Effect, theirs: list[GameObject], player_id: PlayerId
) -> list[GameObject]:
    """What this player sacrifices: as many as the effect says, their choice.

    Asked of the sacrificing player's agent; without one, the cheapest by
    mana value and then id, the default sacrifice costs use too.
    """
    spec = effect.targets
    if spec.count is None and spec.up_to and theirs:
        return _any_number_of(resolution.game, player_id, effect, theirs)
    if spec.count is None:
        return theirs
    wanted = _value_of(resolution, spec.count)
    if wanted <= 0 or not theirs:
        return []
    if wanted >= len(theirs):
        return theirs
    game = resolution.game
    chooser = getattr(game.agent_for(player_id), "choose_objects", None)
    if chooser is not None:
        picked = chooser(game, player_id, effect, list(theirs), wanted)
        kept = [obj for obj in theirs if obj in (picked or ())][:wanted]
        if len(kept) == wanted:
            return kept
    return sorted(theirs, key=lambda o: (game.characteristics(o).mana_value, o.id))[:wanted]


def _do_return_to_hand(resolution: Resolution, effect: Effect) -> None:
    for obj in _objects(resolution, effect):
        actions.bounce(resolution.game, obj, source=resolution.source)


def _do_move_zone(resolution: Resolution, effect: Effect) -> None:
    destination = effect.zone or Zone.GRAVEYARD
    objects = _objects(resolution, effect)
    # "Reveal it and put it into your hand" (CR 701.20): the reveal is part
    # of the instruction, and it is the card being moved that is shown.
    _reveal_if_said(resolution, effect, objects)
    for obj in objects:
        resolution.game.move_object(obj, destination, to_player=obj.owner)


def _reveal_if_said(
    resolution: Resolution, effect: Effect, objects: list[GameObject]
) -> None:
    if "reveal" not in effect.keywords:
        return
    game = resolution.game
    for obj in objects:
        game.emit(Event(EventKind.REVEALED, object_id=obj.id, player=obj.owner))


def _do_discard(resolution: Resolution, effect: Effect) -> None:
    game = resolution.game
    count = _amount(resolution, effect)
    discarded: list[ObjectId] = []
    for player_id in _players(resolution, effect):
        player = game.player(player_id)
        agent = game.agent_for(player_id)
        for _ in range(count):
            if not player.hand:
                break
            choice = (
                agent.choose_discard(game, player_id) if agent is not None else player.hand[-1]
            )
            obj = game.objects.get(choice) or game.objects[player.hand[-1]]
            discarded.append(obj.id)
            actions.discard(game, obj, source=resolution.source)
    # CR 608.2: "if you discarded a nonland card this way" asks about what
    # was discarded, as it last existed in hand.
    if discarded:
        resolution.remembered = discarded


def _do_create_token(resolution: Resolution, effect: Effect) -> None:
    """CR 111.1: create tokens, as many as the effect says, for whoever it says.

    Two bugs lived in the old one line, and both were quiet.

    The count guarded on ``effect.amount.constant``, which is the *value* a
    literal carries, not a flag saying the amount is a literal. Every
    non-literal amount therefore read as 0, which is falsy, and fell through
    to one token: Secure the Wastes for X=6 made one Soldier, Avenger of
    Zendikar made one Plant, and the whole go-wide archetype quietly stopped
    working. A literal 0 is how "create a token" arrives with no count at
    all, so that one still means one; an evaluated 0 - X=0 - correctly makes
    nothing.

    CR 111.2: "The player who creates a token is its owner. The token enters
    the battlefield under that player's control." The resolving player was
    passed regardless of what the effect said, so "target opponent creates a
    1/1" handed the token to the caster - Forbidden Orchard became a strictly
    better land and the Hunted cycle lost its drawback entirely.
    """
    if effect.token is None:
        return
    from ..cr100_game_concepts.cr111_tokens import create_tokens

    count = _count(resolution, effect)
    made: list = []
    for player_id in _players(resolution, effect):
        created = create_tokens(
            resolution.game,
            effect.token,
            player_id,
            count,
            source=resolution.source,
        )
        # CR 607.2c: "tokens created with this" are these and no others.
        for token in created:
            actions.record_link(
                resolution.game, resolution.source, token.id, _link_of(resolution)
            )
        made.extend(token.id for token in created)
    # CR 608.2c: "Create two 1/1 tokens. Sacrifice them at the beginning of
    # the next end step" - "them" is the tokens this instruction made. The
    # generic capture in ``_execute_one`` only sees an effect's *targets*,
    # and creating a token has none, so without this the pronoun still meant
    # whatever an earlier instruction acted on.
    if made:
        resolution.remembered = made


def _do_create_emblem(resolution: Resolution, effect: Effect) -> None:
    """CR 114.2: "[player] gets an emblem with [ability]"."""
    from ..cr100_game_concepts.cr111_tokens import create_emblem

    for player_id in _players(resolution, effect):
        create_emblem(
            resolution.game, player_id, tuple(effect.granted_abilities), text=effect.text
        )


def _do_restriction(resolution: Resolution, effect: Effect) -> None:
    """A prohibition from a resolved spell (CR 101.2, 611.2b).

    Registered standing rather than regenerated, because it outlives its
    source: "creatures can't attack this turn" keeps applying after the
    enchantment that said it has gone.

    It does not outlive its *duration*, though. Dropping ``effect.duration``
    here is what made every "this turn" prohibition permanent - one Falter
    and those creatures could never block again.
    """
    from ..kernel.query import PlayerFilter

    for restriction in effect.restrictions:
        players = restriction.players or effect.players
        pinned = (
            _referred_players(resolution, replace(effect, players=players))
            if players is not None
            else None
        )
        if pinned is not None:
            # "Defending player can't cast spells this turn", "that player
            # can't ...": who is meant is known only now, so the standing
            # prohibition names them rather than keeping a reference nothing
            # will be able to answer later.
            for player_id in pinned:
                _register_restriction(
                    resolution,
                    effect,
                    replace(
                        restriction,
                        players=PlayerFilter(PlayerScope.SPECIFIC, specific=player_id),
                    ),
                )
            continue
        _register_restriction(resolution, effect, restriction)


def _register_restriction(resolution: Resolution, effect: Effect, restriction) -> None:
    from ..cr500_turn_structure.restrictions import Restriction, register_standing

    register_standing(
        resolution.game,
        Restriction(
            act=restriction.act,
            subject=restriction.subject or effect.targets,
            players=restriction.players or effect.players,
            source=resolution.source,
            controller=resolution.controller,
            counterpart=restriction.counterpart,
            text=restriction.text or effect.text,
            duration=effect.duration,
            created_turn=resolution.game.turn,
        ),
    )


def _do_suspend_rule(resolution: Resolution, effect: Effect) -> None:
    """Switch a rule off (CR 101.1).

    The golden rule's seam for rules that are about the game rather than
    about an object - "creatures don't suffer summoning sickness" suspends
    CR 302.6 rather than granting anything. Which rules can be named, and how
    to add one, is in ``cr100_game_concepts/cr101_rule_overrides.py``.

    Registered standing for the same reason a prohibition is: it outlives its
    source, and its duration is what ends it. A suspension coming from a
    permanent's static ability is regenerated with the other continuous
    effects instead and never reaches here.
    """
    from ..cr100_game_concepts.cr101_rule_overrides import Rule, RuleOverride, register

    if not effect.rule:
        return
    try:
        rule = Rule(effect.rule)
    except ValueError:
        # A rule the engine has no name for is one it cannot suspend. Silence
        # here would be a card that claims to switch a rule off and does not,
        # which is the one failure this module is built to avoid.
        resolution.game.log.record(
            resolution.game,
            f"cannot suspend CR {effect.rule}: not a rule this engine can switch off",
            kind="unimplemented",
        )
        return
    register(
        resolution.game,
        RuleOverride(
            rule=rule,
            subject=effect.targets,
            players=effect.players,
            source=resolution.source,
            controller=resolution.controller,
            text=effect.text,
            duration=effect.duration,
            created_turn=resolution.game.turn,
        ),
    )


def _do_roll_dice(resolution: Resolution, effect: Effect) -> None:
    """Roll dice and act on the result (CR 706).

    CR 706.3b makes the whole of it one ability: the dice, the modifiers, the
    results table and anything that reads the result afterwards. So one
    opcode does all of it, and what follows the roll can read the number
    through ``ValueKind.DIE_ROLL_RESULT``.
    """
    from ..cr100_game_concepts import actions as game_actions

    game = resolution.game
    sides = effect.dice_sides
    if sides < 1:
        return
    count = _count(resolution, effect)
    natural = game_actions.roll_dice(game, resolution.controller, count, sides)
    if not natural:
        return

    # CR 706.6: an ignored roll is considered never to have happened, so it is
    # dropped before anything reads the results. Ties are broken by taking the
    # first, which is a choice the rule gives the player and the engine has to
    # make some way.
    kept = sorted(natural)[max(0, effect.dice_ignore_lowest):] if effect.dice_ignore_lowest else natural

    # CR 706.2: the natural result plus the modifiers is the result.
    results = tuple(roll + effect.dice_modifier for roll in kept)
    resolution.die_results = results

    if not effect.outcomes:
        # CR 706.4: no results table. The number is the point, and whatever
        # follows in the same ability reads it.
        return

    # CR 706.3a: each result picks the striation it falls in, and there is one
    # lookup per die - two dice with a table run the table twice.
    for result in results:
        for outcome in effect.outcomes:
            if outcome.covers(result):
                execute(resolution, outcome.effects)
                break


def _do_choose_quality(resolution: Resolution, effect: Effect) -> None:
    """CR 614.1b: choose a creature type, card type or colour.

    Recorded on the permanent that asked, because that is what every later
    "of the chosen type" sentence reads and because the choice lasts exactly
    as long as the permanent does.

    With no agent to ask, the choice is the commonest one among what the
    chooser already has - which is what a player picks and, more importantly,
    is a function of the game state and so keeps the run reproducible.
    """
    game = resolution.game
    obj = game.objects.get(resolution.source)
    if obj is None:
        return

    kind = effect.keywords[0] if effect.keywords else "creature type"
    agent = game.agent_for(resolution.controller)
    if agent is not None and hasattr(agent, "choose_quality"):
        picked = agent.choose_quality(game, resolution.controller, kind)
        if picked is not None:
            _record_quality(obj, kind, picked)
            return

    if kind == "color":
        obj.chosen_color = _commonest_colour(game, resolution.controller)
    elif kind == "card name":
        # CR 201.4: any name in the Oracle reference is legal, so there is no
        # "commonest" to fall back on the way there is for a colour. Naming
        # the card most worth naming needs a bot; with no agent to ask, the
        # deterministic stand-in is the commonest name among what the
        # opponents have on the battlefield, which is at least a name in this
        # game rather than an arbitrary string.
        obj.chosen_name = _commonest_opposing_name(game, resolution.controller)
    else:
        obj.chosen_type = _commonest_subtype(game, resolution.controller)
    game.invalidate_characteristics()


def _commonest_opposing_name(game, controller) -> str:
    """The name an opponent has most of on the battlefield (CR 201.4).

    A stand-in for a real choice, not a rule: CR 201.4 allows any name in the
    Oracle reference. What it buys is determinism and a name that at least
    exists in this game.
    """
    from collections import Counter

    seen: Counter[str] = Counter()
    for opponent in game.opponents(controller):
        for obj in game.permanents(opponent):
            name = game.characteristics(obj).name
            if name:
                seen[name] += 1
    ranked = sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[0][0] if ranked else ""


def _record_quality(obj, kind: str, picked) -> None:
    if kind == "color":
        obj.chosen_color = int(picked)
    elif kind == "card name":
        obj.chosen_name = str(picked)
    else:
        obj.chosen_type = str(picked)


def _commonest_subtype(game: Game, player: PlayerId) -> str:
    """The creature type this player has most of, on the battlefield and in hand."""
    from collections import Counter

    tally: Counter[str] = Counter()
    for zone in (game.battlefield, game.player(player).hand):
        for object_id in zone:
            obj = game.objects.get(object_id)
            if obj is None or obj.controller != player:
                continue
            for subtype in game.characteristics(obj).type_line.subtypes:
                tally[subtype] += 1
    if not tally:
        return ""
    # Ties broken by name so two identical boards always choose alike.
    best = max(tally.values())
    return sorted(name for name, count in tally.items() if count == best)[0]


def _commonest_colour(game: Game, player: PlayerId) -> int:
    """The colour this player has most of among their permanents."""
    from collections import Counter

    tally: Counter[int] = Counter()
    for object_id in game.battlefield:
        obj = game.objects.get(object_id)
        if obj is None or obj.controller != player:
            continue
        colors = int(game.characteristics(obj).colors)
        for bit in (1, 2, 4, 8, 16):
            if colors & bit:
                tally[bit] += 1
    if not tally:
        return 0
    best = max(tally.values())
    return min(bit for bit, count in tally.items() if count == best)


def _do_delayed_trigger(resolution: Resolution, effect: Effect) -> None:
    """CR 603.7: "at the beginning of the next end step, sacrifice it".

    The delayed ability is not an ability of anything on the battlefield; it
    belongs to the effect that made it, and it fires once even if that effect's
    source is long gone.
    """
    if effect.trigger is None:
        return
    create_delayed_trigger(
        resolution,
        effect.trigger,
        effect.children,
        repeating=effect.repeats,
    )


def _do_replacement(resolution: Resolution, effect: Effect) -> None:
    """A replacement effect set up by a resolving ability (CR 614.1).

    One shape is built here: "if it would leave the battlefield, exile it
    instead of putting it anywhere else" (unearth, CR 702.84a). It is about
    the permanent the ability returned, and that permanent is a new object
    once it leaves (CR 400.7), so the redirect is bound to its id and used up
    the first time it applies. Every other replacement a resolution makes goes
    where it always went.
    """
    from ..kernel.query import ObjectFilter
    from .cr614_replacement import ReplacementEffect, ReplacementKind, register

    spec = effect.targets
    if (
        effect.replacement_kind != ReplacementKind.REDIRECT_ZONE_CHANGE
        or effect.zone is None
        or spec is None
        or not spec.source_only
    ):
        if effect.replacement_kind:
            _register_replacement(resolution, effect)
        else:
            _register_continuous(resolution, effect)
        return

    for obj in _objects(resolution, effect):
        if not obj.is_permanent:
            continue
        register(
            resolution.game,
            ReplacementEffect(
                kind=ReplacementKind.REDIRECT_ZONE_CHANGE,
                event_kinds=frozenset({EventKind.ZONE_CHANGE}),
                source=resolution.source,
                controller=resolution.controller,
                subject=ObjectFilter(specific=(obj.id,), zones=frozenset({Zone.BATTLEFIELD})),
                destination=effect.zone,
                one_shot=True,
                text=effect.text,
            ),
        )


def _register_replacement(resolution: Resolution, effect: Effect) -> None:
    """CR 611.2, 614: a replacement effect a resolving spell or ability makes.

    "Until end of turn, if one or more tokens would be created under your
    control, twice that many are created instead." Such an effect was handed
    to the layer system as a continuous effect, where nothing ever consulted
    it - the replacement layer reads only its own registry and permanents'
    static abilities - so every one of them did nothing at all.

    It lasts for its stated duration (CR 611.2b), whether or not its source
    is still around. What it applies to is settled here when the effect names
    particular objects - a target, or "it" - because those objects are what
    the words mean (CR 608.2c), and a filter left live would catch others.
    """
    from dataclasses import replace as _replace

    from .cr614_replacement import register, replacement_from_effect

    game = resolution.game
    for targets, players in _bound_recipients(resolution, effect):
        built = replacement_from_effect(
            _replace(effect, targets=targets, players=players, is_targeted=False),
            source=resolution.source,
            controller=resolution.controller,
            duration=int(effect.duration),
            created_turn=game.turn,
        )
        if built is not None:
            register(game, built)


def _bound_recipients(resolution: Resolution, effect: Effect, *, each: bool = False):
    """What a resolving replacement or prevention effect is about, as
    ``(object filter, player filter)`` pairs - one pair per shield.

    Targets and pronouns become the specific objects and players they name.
    ``each`` asks for one pair per object or player a mass filter matches
    right now: "prevent the next 1 damage that would be dealt to each creature
    you control" is a shield on each creature, not one shield shared among
    them that the first point of damage uses up. Otherwise a mass filter is
    kept live, as the rules keep it - "prevent all damage that would be dealt
    to creatures this turn" protects a creature that arrives later too.
    """
    from ..kernel.query import ObjectFilter, PlayerFilter, PlayerScope

    def one(obj_id):
        return ObjectFilter(specific=(obj_id,), zones=frozenset()), None

    def player(pid):
        return None, PlayerFilter(PlayerScope.SPECIFIC, specific=pid)

    spec = effect.targets
    if effect.is_targeted:
        if spec is None:
            return [player(pid) for pid in _players(resolution, effect)]
        chosen = resolution.targets_for(effect)
        objects = _objects(resolution, effect, chosen=chosen)
        players = _targeted_players(resolution, effect, chosen)
        return [one(obj.id) for obj in objects] + [player(pid) for pid in players]
    if spec is not None and spec.remembered:
        return [one(obj.id) for obj in _objects(resolution, effect)]
    if each and spec is not None and not spec.source_only:
        pairs = [one(obj.id) for obj in _objects(resolution, effect)]
        if effect.players is not None:
            pairs += [player(pid) for pid in _players(resolution, effect)]
        return pairs
    if each and spec is None and effect.players is not None:
        return [player(pid) for pid in _players(resolution, effect)]
    return [(spec, effect.players)]


def _do_control_player(resolution: Resolution, effect: Effect) -> None:
    """CR 723.1: control another player during their next turn.

    CR 723.1a: multiple such effects on one player overwrite each other, latest
    wins. CR 723.6 is the limit that matters ethically and mechanically - the
    controller can never make the controlled player concede.

    CR 723.8: only the controlled player is recorded. The controlling player
    is not displaced by their own effect, so they go on making their own
    choices as well as the other player's.

    CR 723.4 costs nothing here: every agent is handed the whole Game, so
    anything the controlled player could see is already visible to whoever is
    deciding for them. A simulator with hidden zones would have to do work
    for this rule; this one has none to do.

    CR 723.7 is a gap rather than a rule honoured by omission: the effect that
    takes control of a player may also restrict what that player is allowed to
    do, or compel them, and there is nowhere on the effect to carry either.
    Control here is total and unconditioned. CR 723.2 is the same gap seen
    from the card side - the two cards that grant control for a limited
    duration need exactly that payload - and so is CR 723.3's duration, which
    is fixed at the controlled player's next turn.
    """
    game = resolution.game
    for player_id in _players(resolution, effect):
        if player_id == resolution.controller:
            # CR 723.9: an effect may give a player control of themselves,
            # which changes nothing.
            game.release_player(player_id)
            continue
        game.control_player(player_id, resolution.controller)


def _do_restart_game(resolution: Resolution, effect: Effect) -> None:
    """CR 727.1: restart the game.

    The game ends at once with no winner and no loser, and CR 727.1a makes the
    controller of this effect the starting player in the new one. Everything
    else - the new game's setup - happens outside this game object, because a
    restarted game is a new Game.
    """
    game = resolution.game
    game.restart_requested = True
    game.restart_starting_player = resolution.controller
    game.log.record(
        game,
        "The game will restart; no player wins or loses this one",
        kind="restart",
        player=resolution.controller,
    )


def _do_become_prepared(resolution: Resolution, effect: Effect) -> None:
    """CR 722.3a/c: a permanent with a prepare spell becomes prepared."""
    from ..cr700_additional_rules.cr722_preparation import become_prepared

    for obj in _objects(resolution, effect):
        become_prepared(resolution.game, obj)


def _do_reflexive_trigger(resolution: Resolution, effect: Effect) -> None:
    """CR 603.9: a reflexive triggered ability - the "when you do" half.

    "You may sacrifice a creature. When you do, draw a card." The second
    sentence is not a delayed trigger waiting for an event: it triggers
    *because the first sentence happened*, and only if it happened. So it is
    created and triggered inside this resolution, and goes on the stack above
    the ability that made it.

    The condition is the preceding action having been taken, which the
    resolution already knows - if nothing was remembered, nothing happened,
    and the reflexive ability never triggers.
    """
    if not resolution.remembered and effect.condition.is_always is False:
        return
    game = resolution.game
    from .abilities import Ability, AbilityKind

    ability = Ability(
        AbilityKind.TRIGGERED,
        effects=effect.children,
        text=effect.text or "when you do",
    )
    from .cr603_triggers import PendingTrigger

    game.pending_triggers.append(
        PendingTrigger(
            resolution.source,
            ability,
            Event(EventKind.ABILITY_TRIGGERED, object_id=resolution.source),
            resolution.controller,
        )
    )


def _current_incarnations(resolution: Resolution) -> list[ObjectId]:
    """What each remembered object has become within this resolution."""
    game = resolution.game
    out: list[ObjectId] = []
    for object_id in resolution.remembered:
        obj = game.objects.get(object_id)
        while obj is not None and obj.superseded_by:
            obj = game.objects.get(obj.superseded_by)
        if obj is not None:
            out.append(obj.id)
    return out


def create_delayed_trigger(
    resolution: Resolution, trigger, effects: tuple[Effect, ...], *, repeating: bool = False
) -> None:
    """CR 603.7: set up an ability that fires later.

    "At the beginning of the next end step, sacrifice it" is not an ability of
    any object - it belongs to the effect that created it, and it survives that
    effect's source leaving.

    CR 610.2: creating it is all the one-shot effect does. What it instructs
    happens later, when the delayed ability triggers and resolves, not here.
    """
    from .abilities import DelayedTrigger

    resolution.game.delayed_triggers.append(
        DelayedTrigger(
            trigger=trigger,
            effects=effects,
            controller=resolution.controller,
            source=resolution.source,
            repeating=repeating,
            # CR 603.7c: "return it", "sacrifice that creature" - the objects
            # this resolution was talking about when it made the ability, as
            # they are now: "exile it, then return it at the next end step"
            # means the card in exile, not the permanent it was (CR 400.7).
            remembered=tuple(_current_incarnations(resolution)),
        )
    )


# ---------------------------------------------------------------------------
# Damage and life
# ---------------------------------------------------------------------------


def _do_damage(resolution: Resolution, effect: Effect) -> None:
    game = resolution.game
    amount = _amount(resolution, effect)
    source_obj = game.objects.get(resolution.source)
    chars = game.characteristics(source_obj) if source_obj is not None else None
    deathtouch = bool(chars and chars.has_keyword("Deathtouch"))
    lifelink = bool(chars and chars.has_keyword("Lifelink"))

    # The chosen targets are read once, here, and split by kind. Both halves
    # come out of the same list (CR 115.4) and the cursor only moves forward.
    chosen = resolution.targets_for(effect) if effect.is_targeted else ()
    for obj in _objects(resolution, effect, chosen=chosen):
        actions.deal_damage(
            game,
            obj,
            amount,
            source=resolution.source,
            source_controller=resolution.controller,
            deathtouch=deathtouch,
            lifelink=lifelink,
        )
    recipients = _targeted_players(resolution, effect, chosen)
    if recipients:
        # "... deals 2 damage to target player. That player discards two
        # cards" (``PlayerScope.CHOSEN_PLAYER``).
        resolution.chosen_players = list(recipients)
    if not recipients and effect.players is not None and not effect.is_targeted:
        recipients = _players(resolution, effect)
    for player_id in recipients:
        actions.deal_damage(
            game,
            player_id,
            amount,
            source=resolution.source,
            source_controller=resolution.controller,
            lifelink=lifelink,
        )


def _do_gain_life(resolution: Resolution, effect: Effect) -> None:
    amount = _amount(resolution, effect)
    for player_id in _players(resolution, effect):
        actions.gain_life(resolution.game, player_id, amount, source=resolution.source)


def _do_lose_life(resolution: Resolution, effect: Effect) -> None:
    amount = _amount(resolution, effect)
    for player_id in _players(resolution, effect):
        actions.lose_life(resolution.game, player_id, amount, source=resolution.source)


def _do_set_life(resolution: Resolution, effect: Effect) -> None:
    target = _amount(resolution, effect)
    for player_id in _players(resolution, effect):
        player = resolution.game.player(player_id)
        delta = target - player.life
        if delta > 0:
            actions.gain_life(resolution.game, player_id, delta, source=resolution.source)
        elif delta < 0:
            actions.lose_life(resolution.game, player_id, -delta, source=resolution.source)


def _do_add_poison(resolution: Resolution, effect: Effect) -> None:
    amount = _amount(resolution, effect)
    for player_id in _players(resolution, effect):
        actions.add_poison(resolution.game, player_id, amount)


def _do_fight(resolution: Resolution, effect: Effect) -> None:
    """CR 701.12: each creature deals damage equal to its power to the other.

    Both deal damage even if the first one dies doing it, because the damage is
    simultaneous.
    """
    game = resolution.game
    combatants = _objects(resolution, effect)
    if len(combatants) != 2:
        return
    first, second = combatants
    first_chars = game.characteristics(first)
    second_chars = game.characteristics(second)
    if not (first_chars.is_creature and second_chars.is_creature):
        return
    actions.deal_damage(
        game,
        second,
        first_chars.power or 0,
        source=first.id,
        source_controller=first.controller,
        deathtouch=first_chars.has_keyword("Deathtouch"),
        lifelink=first_chars.has_keyword("Lifelink"),
    )
    actions.deal_damage(
        game,
        first,
        second_chars.power or 0,
        source=second.id,
        source_controller=second.controller,
        deathtouch=second_chars.has_keyword("Deathtouch"),
        lifelink=second_chars.has_keyword("Lifelink"),
    )


# ---------------------------------------------------------------------------
# Permanents
# ---------------------------------------------------------------------------


def _do_tap(resolution: Resolution, effect: Effect) -> None:
    for obj in _objects(resolution, effect):
        actions.tap(resolution.game, obj, source=resolution.source)


def _do_untap(resolution: Resolution, effect: Effect) -> None:
    for obj in _objects(resolution, effect):
        actions.untap(resolution.game, obj, source=resolution.source)


def _do_add_counters(resolution: Resolution, effect: Effect) -> None:
    amount = _amount(resolution, effect)
    objects = _objects(resolution, effect)

    if effect.divided and objects:
        # CR 601.2d: the counters are shared out among the targets, not given
        # to each of them. Every target must receive at least one, so the
        # division is as even as the numbers allow and the remainder goes to
        # the first - a deterministic split, which keeps replays exact.
        share, extra = divmod(amount, len(objects))
        for index, obj in enumerate(objects):
            given = share + (1 if index < extra else 0)
            if given:
                actions.add_counters(
                    resolution.game,
                    obj,
                    effect.counter_type,
                    given,
                    source=resolution.source,
                )
        return

    for obj in objects:
        actions.add_counters(
            resolution.game, obj, effect.counter_type, amount, source=resolution.source
        )


def _do_remove_counters(resolution: Resolution, effect: Effect) -> None:
    amount = _amount(resolution, effect)
    for obj in _objects(resolution, effect):
        actions.remove_counters(
            resolution.game, obj, effect.counter_type, amount, source=resolution.source
        )


def _do_proliferate(resolution: Resolution, effect: Effect) -> None:
    """CR 701.28: add one more of each kind of counter already there.

    The choice of which permanents and players to proliferate is the
    controller's; taking all of your own and none of theirs is the sane default
    and is what a bot would pick anyway.
    """
    game = resolution.game
    for obj in list(game.permanents(resolution.controller)):
        for kind in list(obj.counters):
            actions.add_counters(game, obj, kind, 1, source=resolution.source)


def _do_attach(resolution: Resolution, effect: Effect) -> None:
    """CR 701.3. ``targets`` is what it attaches *to*.

    What is attached is the ability's own source unless the effect names
    something else - "attach that Equipment to target creature" is about a
    different permanent, and moving the source would move the wrong one.
    """
    game = resolution.game

    moving = game.objects.get(resolution.source)
    if effect.attached_to is not None:
        from ..kernel.matching import find

        candidates = find(
            game,
            effect.attached_to,
            source=resolution.source,
            controller=resolution.controller,
        )
        if candidates:
            moving = candidates[0]
    if moving is None:
        return

    for obj in _objects(resolution, effect):
        actions.attach(game, moving, obj)


def _do_unattach(resolution: Resolution, effect: Effect) -> None:
    for obj in _objects(resolution, effect):
        actions.detach(resolution.game, obj)


def _do_gain_control(resolution: Resolution, effect: Effect) -> None:
    """Control change is a layer 2 continuous effect, not an immediate write."""
    _register_continuous(resolution, effect)


def _do_transform(resolution: Resolution, effect: Effect) -> None:
    """CR 701.28 / 712.18: turn it over, keeping the very same object."""
    from ..cr700_additional_rules.cr707_faces import transform

    for obj in _objects(resolution, effect):
        transform(resolution.game, obj)


def _do_copy_permanent(resolution: Resolution, effect: Effect) -> None:
    """CR 707.1: make a permanent a copy of another."""
    from ..cr700_additional_rules.cr707_faces import copy_permanent

    game = resolution.game
    copier = game.objects.get(resolution.source)
    if copier is None:
        return
    for original in _objects(resolution, effect):
        copy_permanent(game, copier, original)


def _do_copy_spell(resolution: Resolution, effect: Effect) -> None:
    """CR 707.10: a copy on the stack, which was never cast."""
    from ..cr700_additional_rules.cr707_faces import copy_spell

    count = _count(resolution, effect)
    for original in _objects(resolution, effect):
        for _ in range(count):
            copy_spell(resolution.game, original, resolution.controller)


def _do_goad(resolution: Resolution, effect: Effect) -> None:
    game = resolution.game
    for obj in _objects(resolution, effect):
        obj.goaded_by.add(resolution.controller)
        game.log.record(game, f"{obj} is goaded", kind="effect")


# ---------------------------------------------------------------------------
# The stack
# ---------------------------------------------------------------------------


def _do_counter_spell(resolution: Resolution, effect: Effect) -> None:
    """CR 701.5: remove a spell from the stack and put it in its graveyard."""
    game = resolution.game
    for obj in _objects(resolution, effect):
        if obj.zone is not Zone.STACK:
            continue
        chars = game.characteristics(obj)
        if chars.has_keyword("Can't be countered"):
            game.log.record(game, f"{obj} can't be countered", kind="effect")
            continue
        game.emit(Event(EventKind.COUNTERED, object_id=obj.id, source=resolution.source))
        if obj.is_ability_on_stack:
            game._remove_from_zone(obj)
            game.objects.pop(obj.id, None)
        else:
            game.move_object(obj, Zone.GRAVEYARD, to_player=obj.owner)


# ---------------------------------------------------------------------------
# Mana
# ---------------------------------------------------------------------------


def _mana_kind(symbol: str, *, snow: bool, restriction=None, until_end_of_combat=False):
    """One printed mana symbol, as a unit of mana in a pool.

    Hybrid symbols in a mana-*production* ability ("Add {G/U}") are a choice;
    the first color keeps the run deterministic, which replay depends on.
    """
    from ..cr100_game_concepts.cr106_mana import ManaKind, parse_mana_symbol
    from ..kernel.enums import Color

    parsed = parse_mana_symbol(symbol.strip("{}"))
    colors = list(parsed.colors)
    return ManaKind(
        colors[0] if colors else Color.NONE,
        snow=snow,
        restriction=restriction,
        until_end_of_combat=until_end_of_combat,
    )


def _count(resolution: Resolution, effect: Effect) -> int:
    """How many, for an effect whose amount is a count rather than a size.

    A literal zero is how "copy it" or "add mana" arrives with no number on
    it, and means one. Anything that is not a literal is evaluated, so "copy
    it X times" and "add mana equal to its power" get their real value.

    Guarding on ``effect.amount.constant`` instead reads the *value* a literal
    carries, not whether the amount is a literal at all - so every X and every
    "for each" collapsed to one. The same mistake made Secure the Wastes
    create a single Soldier.
    """
    if effect.amount.is_constant:
        return max(1, effect.amount.constant)
    return max(0, _amount(resolution, effect))


def _amount2(resolution: Resolution, effect: Effect) -> int:
    """The effect's second quantity, evaluated against the game."""
    from ..kernel.values import evaluate

    return evaluate(
        resolution.game,
        effect.amount2,
        source=resolution.source,
        controller=resolution.controller,
        event_amount=resolution.event_amount,
        die_results=resolution.die_results,
        remembered=tuple(resolution.remembered),
        this_way=resolution.this_way,
    )


def mana_color_choices(game, controller: PlayerId, effect: Effect):
    """The colours an "any color" mana effect may produce for this player.

    The effect's own set, narrowed to the commander's colour identity when it
    says so (CR 903.4); empty when that identity is undefined (CR 903.4f).
    The payment planner should ask this too, so what it plans to tap for and
    what resolving the ability adds cannot disagree.
    """
    from ..kernel.enums import Color

    colors = effect.colors
    if effect.colors_in_commander_identity:
        from ..cr903_commander.cr903_color_identity import commander_color_identity

        identity = commander_color_identity(game, controller)
        colors = Color(int(colors) & int(identity)) if identity is not None else Color.NONE
    return colors


def _do_add_mana(resolution: Resolution, effect: Effect) -> None:
    """CR 106.1: add mana to a player's pool."""
    if effect.players is not None and effect.players.scope is not PlayerScope.YOU:
        # "Its controller adds an additional {G}", "that player adds {C}":
        # the mana goes to the player named (CR 106.4), who is the "you" of
        # the rest of the instruction. Adding it to the ability's controller
        # put Wild Growth's extra mana in the aura's controller's pool.
        saved = resolution.controller
        try:
            for player_id in _players(resolution, effect):
                resolution.controller = player_id
                _do_add_mana(resolution, replace(effect, players=None))
        finally:
            resolution.controller = saved
        return
    from ..cr100_game_concepts.cr106_mana import ManaKind
    from ..kernel.enums import Supertype

    game = resolution.game
    source_obj = game.objects.get(resolution.source)
    # {S} costs care where the mana came from, not what color it is
    # (CR 107.4h), so the snow-ness of the source rides along with it.
    snow = bool(
        source_obj is not None
        and game.characteristics(source_obj).has_supertype(Supertype.SNOW)
    )
    player = game.player(resolution.controller)

    if effect.mana_produced:
        # "Add {B} for each Swamp you control": the symbol list is produced
        # once per matching permanent.
        repeat = 1
        if effect.amount2.kind is not ValueKind.CONSTANT or effect.amount2.constant:
            repeat = max(0, _amount2(resolution, effect))
        # The exact symbols the card prints. Looping a count over a color set
        # instead - which is what this did - turned "Add {G}{U}" into two
        # green *and* two blue, doubling the output of every dual land in the
        # format and quietly inflating every ramp statistic downstream.
        # CR 702.189a: "until end of combat, you don't lose this mana".
        combat_mana = effect.duration == int(Duration.END_OF_COMBAT)
        for _ in range(repeat):
            for symbol in effect.mana_produced:
                player.mana_pool.add(
                    _mana_kind(
                        symbol,
                        snow=snow,
                        restriction=effect.mana_restriction,
                        until_end_of_combat=combat_mana,
                    ),
                    1,
                )
    elif effect.colors_chosen:
        # "Add one mana of the chosen color" - the colour this permanent
        # recorded as it entered (CR 614.1b). Nothing on the card names it,
        # so it can only be read off the source at resolution.
        amount = _count(resolution, effect)
        recorded = getattr(source_obj, "chosen_color", 0) if source_obj else 0
        player.mana_pool.add(
            ManaKind(recorded, snow=snow, restriction=effect.mana_restriction),
            amount,
        )
    elif effect.colors:
        # No symbol list: "add N mana of any color", where the color set is
        # the menu and the player picks - the payer's choice when the payment
        # planner made one, otherwise a deterministic pick, first color.
        menu = mana_color_choices(game, resolution.controller, effect)
        if not menu:
            return  # CR 903.4f: no commander, no colour to choose.
        amount = _count(resolution, effect)
        wanted = resolution.mana_color
        chosen = wanted if wanted and (menu & wanted) == wanted else next(iter(menu))
        player.mana_pool.add(
            ManaKind(chosen, snow=snow, restriction=effect.mana_restriction), amount
        )
    else:
        amount = _count(resolution, effect)
        player.mana_pool.add(
            ManaKind(snow=snow, restriction=effect.mana_restriction), amount
        )
    game.emit(
        Event(EventKind.MANA_ADDED, player=resolution.controller, source=resolution.source)
    )


# ---------------------------------------------------------------------------
# Continuous effects (CR 611)
# ---------------------------------------------------------------------------


def _settles_its_set(effect: Effect) -> bool:
    """Whether CR 611.2c fixes this effect's set of objects when it begins.

    True for anything that modifies characteristics - the layered effects -
    and for a control change. Everything else modifies the rules of the game
    and keeps applying to objects that were not there at the time.
    """
    from .cr613_layers import EFFECT_LAYERS

    return effect.kind in EFFECT_LAYERS or effect.kind is EffectKind.GAIN_CONTROL


def _locked_amounts(resolution: Resolution, effect: Effect) -> Effect:
    """CR 608.2h: a resolving effect's numbers are worked out once, now.

    "Creatures you control get +X/+X until end of turn, where X is the
    number of Forests you control" is +3/+3 for the rest of the turn if
    there were three Forests when it resolved - playing a fourth changes
    nothing. Left as a live Value, the layer system re-counted it on every
    recomputation, and a value that only the resolution can answer ("for
    each Plains returned this way", "the life lost this way") came out
    zero afterwards, because nothing but the resolution remembers.

    A value that reads the *affected* object ("gets +X/+0, where X is its
    power") has one answer per object, and the layer system is what applies
    it object by object, so it is left for the layers to evaluate.
    """
    from dataclasses import replace as _replace

    from ..kernel.matching import value_subjects
    from ..kernel.query import Value, ValueKind

    changes = {}
    for name in ("amount", "amount2"):
        value = getattr(effect, name)
        if value.kind in (ValueKind.CONSTANT, ValueKind.UNCHANGED):
            continue
        affected, _from_source, _contextual = value_subjects(value)
        if affected:
            continue
        changes[name] = Value.of(
            _amount(resolution, effect, second=(name == "amount2"))
        )
    return _replace(effect, **changes) if changes else effect


def _register_continuous(resolution: Resolution, effect: Effect) -> None:
    """Create a continuous effect from a resolving spell or ability (CR 611.2).

    Its timestamp is the moment of creation, and it persists for its stated
    duration whether or not its source survives (CR 611.2b).
    """
    from ..kernel.game import ContinuousEffect
    from .cr613_layers import layer_for

    game = resolution.game
    resolved_effect = effect

    # CR 611.2c: an effect that modifies characteristics or changes control
    # settles the set of objects it affects when it begins, and that set never
    # changes afterwards. An effect that does neither modifies the rules of the
    # game instead, and goes on applying to objects that arrive later.
    #
    # Only targeted effects were frozen, so every untargeted mass effect kept
    # its filter live and re-matched the board on each recomputation: a pump
    # that read "creatures you control get +2/+2 until end of turn" also
    # pumped creatures that entered afterwards, followed control changes, and
    # caught permanents that became creatures later in the turn.
    if effect.is_targeted or _settles_its_set(effect):
        chosen = tuple(obj.id for obj in _objects(resolution, effect))
        if not chosen:
            # The set is empty and will stay empty, so the effect does nothing.
            # It must not be registered with an empty "specific" filter: an
            # empty tuple is falsy, which ObjectFilter reads as no constraint
            # at all, and the effect would apply to every object on the board.
            return
        from dataclasses import replace as _replace

        from ..kernel.query import ObjectFilter

        resolved_effect = _replace(
            effect,
            # CR 112.4: an effect that changed a permanent *spell* keeps
            # applying to the permanent it becomes, so the zones it may reach
            # are the ones its objects are in now, plus the battlefield they
            # may be heading for. Hard-coding the battlefield meant an effect
            # aimed at a spell was born unable to see it, and "target creature
            # spell becomes white" did nothing even while the spell was still
            # on the stack.
            targets=ObjectFilter(
                specific=chosen,
                zones=frozenset(
                    {game.objects[i].zone for i in chosen if i in game.objects}
                )
                | {Zone.BATTLEFIELD},
            ),
            is_targeted=False,
        )

    resolved_effect = _locked_amounts(resolution, resolved_effect)

    game.continuous_effects.append(
        ContinuousEffect(
            effect=resolved_effect,
            source=resolution.source,
            controller=resolution.controller,
            timestamp=game.ids.timestamp(),
            layer=int(layer_for(resolved_effect)),
            duration=effect.duration or int(Duration.PERMANENT),
            created_turn=game.turn,
        )
    )
    game.invalidate_characteristics()


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------


def _do_player_wins(resolution: Resolution, effect: Effect) -> None:
    for player_id in _players(resolution, effect):
        resolution.game.player_wins(player_id)


def _do_player_loses(resolution: Resolution, effect: Effect) -> None:
    from ..kernel.enums import LossReason

    for player_id in _players(resolution, effect):
        resolution.game.player_loses(player_id, LossReason.EFFECT)


def _do_become_monarch(resolution: Resolution, effect: Effect) -> None:
    game = resolution.game
    for player_id in _players(resolution, effect):
        for player in game.players:
            player.is_monarch = player.id == player_id
        game.emit(Event(EventKind.BECAME_MONARCH, player=player_id))


def _do_if_you_dont(resolution: Resolution, effect: Effect) -> None:
    """"You may sacrifice a creature. If you don't, ..." (CR 603.9, inverted).

    Fires precisely when the preceding optional action was *not* taken, which
    the resolution records the same way the positive form reads it: an action
    that happened leaves something remembered.
    """
    if resolution.remembered:
        return
    execute(resolution, effect.children)


def _do_pay_cost(resolution: Resolution, effect: Effect) -> None:
    """Charge a cost during a resolution ("you may pay {2}").

    Success is recorded in ``remembered`` because that is how the reflexive
    "if you do" half knows whether it happened (CR 603.9). A payment that
    could not be made must leave nothing remembered, or the rest of the card
    fires for free.
    """
    from .cr601_casting import can_pay_cost, pay_cost

    game = resolution.game
    cost = effect.pay_cost
    if cost is None:
        return
    for player_id in _players(resolution, effect):
        if can_pay_cost(game, player_id, cost) and pay_cost(game, player_id, cost):
            resolution.remembered.append(resolution.source)


def _do_start_engines(resolution: Resolution, effect: Effect) -> None:
    """CR 702.179a: if this player has no speed, their speed becomes 1.

    Idempotent: several permanents on one board each say this, and the second
    must not disturb a speed that is already running.
    """
    game = resolution.game
    for player_id in _players(resolution, effect):
        if game.player(player_id).start_engines():
            game.emit(
                Event(EventKind.SPEED_INCREASED, player=player_id, amount=1)
            )


def _do_add_energy(resolution: Resolution, effect: Effect) -> None:
    amount = _amount(resolution, effect)
    for player_id in _players(resolution, effect):
        resolution.game.player(player_id).energy += amount
        resolution.game.emit(
            Event(EventKind.ENERGY_GAINED, player=player_id, amount=amount)
        )


def _do_extra_land_drop(resolution: Resolution, effect: Effect) -> None:
    amount = max(1, _amount(resolution, effect))
    for player_id in _players(resolution, effect):
        resolution.game.player(player_id).max_lands += amount


def _do_extra_turn(resolution: Resolution, effect: Effect) -> None:
    for player_id in _players(resolution, effect):
        resolution.game.extra_turns.append(player_id)




# ---------------------------------------------------------------------------
# Library manipulation (CR 701.23, 701.20, 701.35, 701.42)
# ---------------------------------------------------------------------------


def _do_search_library(resolution: Resolution, effect: Effect) -> None:
    """CR 701.23: search a library for cards matching a description.

    The library is a hidden, *ordered* zone, so a search is followed by a
    shuffle (CR 701.23) - otherwise the searcher would learn the order of what
    they left behind.

    How many: the amount ("a card" is one, "up to two" is two - CR 701.23b
    lets the searcher find fewer of a stated quality, and the default finds
    as many as it can). "Any number of" has no amount; the player picks from
    everything that matches (CR 107.1c).

    Where to: the effect's zone. The library is zone 0, so it is compared
    with ``None`` rather than tested for truth - a test for truth sent every
    "then shuffle and put that card on top" tutor to the hand. A card bound
    for the top is put there *after* the shuffle, which is the order those
    tutors print. "Put one onto the battlefield tapped and the other into
    your hand" (``other to hand``) sends the first card found to the
    battlefield and the rest to the hand.
    """
    from ..kernel.matching import matches

    game = resolution.game
    spec = effect.targets
    any_number = spec is not None and spec.count is None and spec.up_to
    wanted = max(1, _amount(resolution, effect))
    destination = effect.zone if effect.zone is not None else Zone.HAND

    for player_id in _players(resolution, effect):
        player = game.player(player_id)
        found: list[GameObject] = []
        for object_id in list(player.library):
            obj = game.objects.get(object_id)
            if obj is None:
                continue
            if spec is None or matches(
                game, obj, spec, source=resolution.source, controller=player_id
            ):
                found.append(obj)
            if not any_number and len(found) >= wanted:
                break
        if any_number and found:
            found = _any_number_of(game, player_id, effect, found)

        # "Search your library for a basic land card, put it onto the
        # battlefield *tapped*" (CR 614.1c). Every ramp spell in the format
        # says it, and a land that arrives untapped is a full turn of mana the
        # deck does not have.
        tapped = "tapped" in effect.keywords
        on_top: list[GameObject] = []
        for index, obj in enumerate(found):
            where = destination
            if "other to hand" in effect.keywords and index > 0:
                where = Zone.HAND
            if where is Zone.LIBRARY:
                on_top.append(obj)
                continue
            moved = game.move_object(obj, where, to_player=player_id)
            if tapped and where is Zone.BATTLEFIELD and moved.zone is Zone.BATTLEFIELD:
                # A replacement effect may have sent it somewhere else.
                moved.tapped = True
        game.emit(
            Event(EventKind.SEARCHED_LIBRARY, player=player_id, amount=len(found))
        )
        if on_top:
            # "Then shuffle and put that card on top": set the card aside,
            # shuffle the rest, and put it back on top.
            for obj in on_top:
                player.library.remove(obj.id)
            game.shuffle_library(player_id)
            for obj in reversed(on_top):
                player.library.insert(0, obj.id)
        else:
            game.shuffle_library(player_id)
        resolution.remembered = [obj.id for obj in found]


def _do_shuffle(resolution: Resolution, effect: Effect) -> None:
    for player_id in _players(resolution, effect):
        resolution.game.shuffle_library(player_id)


# ---------------------------------------------------------------------------
# MTG Arena's digital-only mechanics (see digital_mechanics.py)
# ---------------------------------------------------------------------------


def _do_seek(resolution: Resolution, effect: Effect) -> None:
    """Seek: cards at random from the library that match, into the hand.

    The cards found are what a following "it" or "that card" means - "seek a
    land card, then put it onto the battlefield tapped". Nothing found is
    remembered as nothing, so a following sentence about "it" cannot fall
    back on something an earlier sentence did.
    """
    from ..cr700_additional_rules.digital_mechanics import seek

    game = resolution.game
    count = _count(resolution, effect)
    found: list[ObjectId] = []
    for player_id in _players(resolution, effect):
        found.extend(
            obj.id
            for obj in seek(
                game,
                player_id,
                effect.targets,
                count,
                source=resolution.source,
                controller=resolution.controller,
                to_zone=(Zone.HAND if effect.zone is None else effect.zone),
            )
        )
    resolution.remembered = found


def _do_conjure(resolution: Resolution, effect: Effect) -> None:
    """Conjure: cards created from outside the game, owned by who conjures.

    By name, the card comes from the game's catalogue; as a duplicate or as
    "a card named [this]", it is the card of the object named - and only a
    duplicate keeps that object's perpetual changes. What was conjured is
    what a following "it" means ("It perpetually gains flash").
    """
    from ..cr700_additional_rules.digital_mechanics import (
        catalog_card,
        conjure,
        conjure_duplicate,
        conjured_name,
    )

    game = resolution.game
    zone = (Zone.HAND if effect.zone is None else effect.zone)
    count = _count(resolution, effect)
    tapped = "tapped" in effect.keywords
    from_top = 0 if effect.amount2.is_constant and not effect.amount2.constant else _amount2(
        resolution, effect
    )
    made: list[ObjectId] = []
    players = _players(resolution, effect)
    card_name = conjured_name(effect)
    if card_name:
        card = catalog_card(game, card_name)
        if card is not None:
            for player_id in players:
                for _ in range(count):
                    obj = conjure(
                        game,
                        player_id,
                        card,
                        zone,
                        tapped=tapped,
                        from_top=from_top,
                        source=resolution.source,
                    )
                    if obj is not None:
                        made.append(obj.id)
    elif effect.targets is not None:
        originals = _objects(resolution, effect)
        duplicate = "duplicate" in effect.keywords
        for player_id in players:
            for original in originals:
                for _ in range(count):
                    obj = conjure_duplicate(
                        game,
                        player_id,
                        original,
                        zone,
                        keep_perpetual=duplicate,
                        tapped=tapped,
                        source=resolution.source,
                    )
                    if obj is not None:
                        made.append(obj.id)
    resolution.remembered = made


def _do_perpetually(resolution: Resolution, effect: Effect) -> None:
    """Perpetually: bind each change to each of the objects, for good.

    The objects are settled now, as any effect that changes characteristics
    settles its set (CR 611.2c) - "creature cards in your hand" means the ones
    there as this resolves, not cards drawn later. "A random" one is picked
    from ``Game.rng``. Amounts are worked out now and fixed.
    """
    from ..cr700_additional_rules.digital_mechanics import PERPETUAL_KINDS, perpetually

    game = resolution.game
    objects = _perpetual_objects(resolution, effect)
    changes = [node for node in effect.walk() if node.kind in PERPETUAL_KINDS]
    for obj in objects:
        for change in changes:
            amounts = None
            if change.kind in (EffectKind.MODIFY_PT, EffectKind.SET_PT):
                amounts = (
                    _amount(resolution, change),
                    _amount(resolution, change, second=True),
                )
            perpetually(game, obj, change, resolution.controller, amounts=amounts)
    if objects:
        resolution.remembered = [obj.id for obj in objects]


def _perpetual_objects(resolution: Resolution, effect: Effect) -> list[GameObject]:
    if "at random" not in effect.keywords or effect.targets is None:
        return _objects(resolution, effect)
    from dataclasses import replace as _replace

    from ..kernel.matching import find

    spec = effect.targets
    pool = find(
        resolution.game,
        _replace(spec, count=None, up_to=False),
        source=resolution.source,
        controller=resolution.controller,
    )
    wanted = _value_of(resolution, spec.count) if spec.count is not None else 1
    picked: list[GameObject] = []
    rng = resolution.game.rng
    while pool and len(picked) < wanted:
        picked.append(pool.pop(rng.randrange(len(pool))))
    return picked


def _do_reveal(resolution: Resolution, effect: Effect) -> None:
    """CR 701.20b. Revealing changes no zone; it only makes information public."""
    game = resolution.game
    for obj in _objects(resolution, effect):
        game.emit(Event(EventKind.REVEALED, object_id=obj.id, player=obj.owner))


def _do_scry(resolution: Resolution, effect: Effect) -> None:
    """CR 701.18: look at the top N, put any on the bottom in any order.

    The choice is the player's. With no agent the default keeps lands and
    cheap spells on top, deterministically, so replays reproduce.
    """
    _look_at_top(resolution, effect, to_graveyard=False)


def _do_surveil(resolution: Resolution, effect: Effect) -> None:
    """CR 701.44: the same as scry, except the rejects go to the graveyard."""
    _look_at_top(resolution, effect, to_graveyard=True)


def _look_at_top(resolution: Resolution, effect: Effect, *, to_graveyard: bool) -> None:
    game = resolution.game
    count = max(0, _amount(resolution, effect))
    if not count:
        return

    for player_id in _players(resolution, effect):
        player = game.player(player_id)
        looked = [game.objects[i] for i in player.library[:count] if i in game.objects]
        if not looked:
            continue

        agent = game.agent_for(player_id)
        if agent is not None and hasattr(agent, "choose_top_of_library"):
            keep = agent.choose_top_of_library(game, player_id, looked, to_graveyard)
        else:
            # A reasonable default that does not need a bot: keep lands and
            # anything cheap, reject the rest.
            keep = [
                obj
                for obj in looked
                if game.printed_characteristics(obj).is_land
                or game.printed_characteristics(obj).mana_value <= 3
            ]

        rejected = [obj for obj in looked if obj not in keep]
        for obj in rejected:
            if to_graveyard:
                game.move_object(obj, Zone.GRAVEYARD, to_player=player_id)
            else:
                game.move_object(obj, Zone.LIBRARY, to_player=player_id)

        game.emit(
            Event(
                EventKind.SURVEILLED if to_graveyard else EventKind.SCRIED,
                player=player_id,
                amount=count,
            )
        )


def _do_put_onto_battlefield(resolution: Resolution, effect: Effect) -> None:
    """Put a card onto the battlefield without casting it (CR 701.14).

    "Tapped" and "attacking" are how it arrives, and both were ignored: a card
    put in "tapped and attacking" arrived untapped and outside combat. Whether
    it can join the attack, and what it attacks, is ``enter_attacking``'s call
    (CR 506.3, 508.4) - handed whatever this ability's cost returned or tapped,
    because ninjutsu's Ninja attacks what the returned creature was attacking.
    """
    from ..cr500_turn_structure.cr506_combat import enter_attacking

    game = resolution.game
    tapped = "tapped" in effect.keywords
    attacking = "attacking" in effect.keywords
    # CR 708.3: "put it onto the battlefield face down" turns it face down
    # before it enters, as a 2/2 with nothing listed (CR 708.2a).
    face_down = "" if "face down" in effect.keywords else None
    stack_object = resolution.stack_object
    paid = tuple(stack_object.cost_paid_objects) if stack_object is not None else ()
    for obj in _objects(resolution, effect):
        if not obj.is_live:
            # CR 400.7: it already changed zones and what is left is the husk.
            # With no filter the effect is about its own source, so a card
            # answered in response was put onto the battlefield a second time
            # while the real one sat in exile.
            continue
        # CR 610.3c: a card coming back from an "until" exile returns to its
        # owner, not to whoever exiled it.
        to = obj.owner if effect.under_owners_control else resolution.controller
        permanent = game.move_object(obj, Zone.BATTLEFIELD, to_player=to, face_down=face_down)
        permanent.controller = to
        if permanent is not obj:
            # CR 607.2c: what was "put onto the battlefield with" the source.
            actions.record_link(
                game, resolution.source, permanent.id, _link_of(resolution)
            )
        if effect.counter_type:
            permanent.add_counters(effect.counter_type, max(1, _amount(resolution, effect)))
        game.invalidate_characteristics()
        if not permanent.is_permanent:
            continue  # A replacement effect sent it somewhere else.
        if tapped:
            permanent.tapped = True
        if attacking:
            enter_attacking(game, permanent, like=paid)


def _do_put_on_library(resolution: Resolution, effect: Effect) -> None:
    """Put objects on top of or on the bottom of their owners' libraries.

    "On the bottom" is ``keywords`` holding "bottom" (the older negative
    ``amount`` still reads as bottom too). It was dropped by the grammar, so
    Condemn put the attacker on *top* of its owner's library, where it is
    drawn again next turn.

    CR 401.4: several cards put into a library at once are ordered by their
    owner - "in any order" is the controller's choice (with no agent, the
    order they are listed in), and "in a random order" is the game's shuffle.

    A card already in that library - one of the cards looked at a moment
    ago - is moved *within* the zone: that is not a zone change (CR 400.7
    needs a new zone), so it stays the same object and triggers nothing.
    """
    game = resolution.game
    on_top = effect.amount.constant >= 0 and "bottom" not in effect.keywords
    objects = list(_objects(resolution, effect))
    _reveal_if_said(resolution, effect, objects)
    if "random" in effect.keywords:
        game.rng.shuffle(objects)
    if on_top:
        # Placed one at a time onto the top, so the last placed is on top:
        # reversed, the list reads top-down in the order it was chosen.
        objects.reverse()
    placed: set[ObjectId] = set()
    for obj in objects:
        placed.add(obj.id)
        library = game.player(obj.owner).library
        if obj.is_live and obj.zone is Zone.LIBRARY and obj.id in library:
            library.remove(obj.id)
            if on_top:
                library.insert(0, obj.id)
            else:
                library.append(obj.id)
            continue
        game.move_object(obj, Zone.LIBRARY, to_player=obj.owner, to_top=on_top)
    if placed and resolution.pile:
        resolution.pile = [i for i in resolution.pile if i not in placed]


def _do_look_at_top(resolution: Resolution, effect: Effect) -> None:
    """"Look at the top N cards of your library" / "reveal the top N cards".

    Looking changes nothing but what the player knows, and revealing only
    shows the cards to everyone (CR 701.20) - no card moves. What the
    instruction does is set the cards aside as the pile the following
    instructions choose among, and name them as "them" for a pronoun. Fewer
    cards than asked for is all of them: a short library is looked at in full.
    """
    game = resolution.game
    count = max(0, _amount(resolution, effect))
    pile: list[ObjectId] = []
    for player_id in _players(resolution, effect):
        pile.extend(game.player(player_id).library[:count])
    resolution.pile = pile
    resolution.remembered = list(pile)
    if "reveal" in effect.keywords:
        for object_id in pile:
            obj = game.objects[object_id]
            game.emit(Event(EventKind.REVEALED, object_id=object_id, player=obj.owner))


def _do_explore(resolution: Resolution, effect: Effect) -> None:
    """CR 701.40: reveal the top card; a land goes to hand, anything else gives
    a +1/+1 counter and the player chooses whether to keep it on top."""
    game = resolution.game
    for obj in _objects(resolution, effect):
        player = game.player(obj.controller)
        if not player.library:
            continue
        top = game.objects[player.library[0]]
        game.emit(Event(EventKind.REVEALED, object_id=top.id, player=player.id))

        if game.printed_characteristics(top).is_land:
            game.move_object(top, Zone.HAND, to_player=player.id)
        else:
            actions.add_counters(game, obj, "+1/+1", 1, source=resolution.source)
        game.emit(Event(EventKind.EXPLORED if hasattr(EventKind, "EXPLORED")
                        else EventKind.REVEALED, object_id=obj.id, player=player.id))


# ---------------------------------------------------------------------------
# Permanents
# ---------------------------------------------------------------------------


def _do_copy_permanent_effect(resolution: Resolution, effect: Effect) -> None:
    from ..cr700_additional_rules.cr707_faces import copy_permanent

    game = resolution.game
    copier = game.objects.get(resolution.source)
    if copier is None:
        return
    for original in _objects(resolution, effect):
        copy_permanent(game, copier, original)


def _do_turn_face_up(resolution: Resolution, effect: Effect) -> None:
    """An effect turns a face-down permanent face up (CR 708.7, 708.8).

    No cost is paid, so no megamorph counter (CR 702.37b); an instant or
    sorcery card is revealed and stays face down (CR 701.40g, 701.58g).
    """
    from ..cr700_additional_rules.cr708_face_down import turn_face_up

    for obj in _objects(resolution, effect):
        turn_face_up(resolution.game, obj)


def _do_turn_face_down(resolution: Resolution, effect: Effect) -> None:
    """CR 708.2a, 708.2b, and CR 712.16 stops a double-faced permanent being turned down."""
    from ..cr700_additional_rules.cr708_face_down import turn_face_down

    for obj in _objects(resolution, effect):
        turn_face_down(resolution.game, obj)


def _do_manifest(resolution: Resolution, effect: Effect) -> None:
    """CR 701.40a, 701.58a, 701.62a: manifest, cloak, or manifest dread.

    Manifest and cloak put each chosen card onto the battlefield face down,
    one at a time (CR 701.40e, 701.58e); manifest dread looks at two and
    manifests one. What turned it face down is recorded, because cloak adds
    ward {2} and both may later be turned up for the card's mana cost.
    """
    from ..cr700_additional_rules.cr708_face_down import manifest, manifest_dread

    game = resolution.game
    how = effect.keywords[0] if effect.keywords else "Manifest"
    if how.lower() == "manifest dread":
        manifest_dread(game, resolution.controller)
        return
    how = "Cloak" if how.lower() == "cloak" else "Manifest"
    for obj in list(_objects(resolution, effect)):
        manifest(game, resolution.controller, obj, how)


def _do_phase_out(resolution: Resolution, effect: Effect) -> None:
    """CR 702.26b: a phased-out permanent is treated as not existing.

    Only the ordinary phasing of CR 702.26 - out now, back in during its
    controller's next untap step. CR 610.4's "phase out *until* [event]" is
    **not implemented**, and CR 610.4a says the untap-step phase-in is exactly
    what should not happen to such a permanent.

    The gap starts before this function: the ``phase-out`` clause in the
    parser discards the "until ..." tail, so no duration ever arrives here.
    For the common Commander wording - "phase out until your next turn" - the
    two answers coincide, which is why nothing has noticed. CR 610.4b-d, on
    which object creates the second one-shot effect and when several of them
    are simultaneous, have nowhere to be expressed at all.
    """
    game = resolution.game
    for obj in _objects(resolution, effect):
        obj.phased_out = True
        obj.phasing_scheduled = True
        game.invalidate_characteristics()
        game.emit(Event(EventKind.PHASED_OUT, object_id=obj.id, player=obj.controller))


def _do_regenerate(resolution: Resolution, effect: Effect) -> None:
    """CR 701.15: create a regeneration shield, which replaces the next
    destruction with tapping, removing from combat, and clearing damage."""
    for obj in _objects(resolution, effect):
        obj.regeneration_shields += 1


def _do_monstrosity(resolution: Resolution, effect: Effect) -> None:
    """CR 701.31: if it is not monstrous, add N +1/+1 counters and make it so.

    "Monstrous" is tracked as a counter because that is exactly how it
    behaves - it is a property of the permanent that a new object would not
    inherit.
    """
    amount = max(1, _amount(resolution, effect))
    for obj in _objects(resolution, effect):
        if obj.counter_count("monstrous"):
            continue
        actions.add_counters(resolution.game, obj, "+1/+1", amount, source=resolution.source)
        obj.add_counters("monstrous", 1)
        # CR 701.37b: "when this becomes monstrous" watches this, not the
        # counters that came with it.
        resolution.game.emit(
            Event(
                EventKind.BECAME_MONSTROUS,
                object_id=obj.id,
                player=obj.controller,
                source=resolution.source,
            )
        )


def _do_adapt(resolution: Resolution, effect: Effect) -> None:
    """CR 701.43: add N +1/+1 counters, but only if it has none at all."""
    amount = max(1, _amount(resolution, effect))
    for obj in _objects(resolution, effect):
        if obj.counter_count("+1/+1"):
            continue
        actions.add_counters(resolution.game, obj, "+1/+1", amount, source=resolution.source)


def _do_exchange_control(resolution: Resolution, effect: Effect) -> None:
    """CR 701.10: two permanents swap controllers, or neither does."""
    objects = _objects(resolution, effect)
    if len(objects) != 2:
        return  # CR 701.10c: if either cannot be exchanged, nothing happens.
    first, second = objects
    first.controller, second.controller = second.controller, first.controller
    # The exchange has no duration (CR 701.10a), so it moves the *base* too.
    # Swapping only the current controller would last until the next time the
    # board was computed, when layer 2 handed both permanents straight back.
    first.base_controller, second.base_controller = (
        second.base_controller,
        first.base_controller,
    )
    first.summoning_sick = second.summoning_sick = True
    resolution.game.invalidate_characteristics()
    for obj in objects:
        resolution.game.emit(
            Event(EventKind.CONTROL_CHANGED, object_id=obj.id, player=obj.controller)
        )


# ---------------------------------------------------------------------------
# Life and damage
# ---------------------------------------------------------------------------


def _do_exchange_life(resolution: Resolution, effect: Effect) -> None:
    """CR 701.10: the two totals swap, as gains and losses."""
    players = _players(resolution, effect)
    if len(players) != 2:
        return
    game = resolution.game
    first, second = players
    a, b = game.player(first).life, game.player(second).life
    for player_id, target in ((first, b), (second, a)):
        current = game.player(player_id).life
        if target > current:
            actions.gain_life(game, player_id, target - current, source=resolution.source)
        elif target < current:
            actions.lose_life(game, player_id, current - target, source=resolution.source)


def _do_prevent_damage(resolution: Resolution, effect: Effect) -> None:
    """CR 615: register a prevention shield rather than acting immediately.

    Two narrowings the shield has always been able to express and was never
    given. "Prevent all *combat* damage" is a Fog and not a blanket, so it
    watches only the combat event; and "damage that would be dealt *to you*"
    names a player, which a shield with no subject and no players applies to
    everyone - a one-sided Fog that quietly protected the whole table.
    """
    from dataclasses import replace as _replace

    from .cr614_replacement import register, replacement_from_effect

    game = resolution.game
    # -1 is "all"; anything else is a shield of that size, worked out now
    # (CR 608.2h) and spent as it prevents (CR 615.7). A size that came out
    # at zero prevents nothing - it must not fall through to meaning "all".
    everything = effect.amount.is_constant and effect.amount.constant < 0
    size = -1 if everything else _amount(resolution, effect)
    if not everything and size <= 0:
        return

    # The shield protects what the words name. A targeted shield used to be
    # registered with the *targeting filter* as its subject, so "prevent all
    # damage that would be dealt to target creature this turn" protected
    # every creature on the battlefield.
    for targets, players in _bound_recipients(resolution, effect, each=not everything):
        built = replacement_from_effect(
            _replace(effect, targets=targets, players=players, is_targeted=False),
            source=resolution.source,
            controller=resolution.controller,
            duration=int(effect.duration),
            created_turn=game.turn,
        )
        if built is None:
            continue
        built.amount = size
        built.one_shot = not everything
        register(game, built)


def _do_redirect_damage(resolution: Resolution, effect: Effect) -> None:
    """CR 614.9: damage that would be dealt to one thing is dealt to another."""
    from .cr614_replacement import ReplacementEffect, ReplacementKind, register

    chosen = _objects(resolution, effect)
    if not chosen:
        return
    register(
        resolution.game,
        ReplacementEffect(
            kind=ReplacementKind.REDIRECT_DAMAGE,
            event_kinds=frozenset({EventKind.DAMAGE_DEALT, EventKind.COMBAT_DAMAGE_DEALT}),
            source=resolution.source,
            controller=resolution.controller,
            redirect_to=chosen[0].id,
            text=effect.text or "redirect damage",
        ),
    )


# ---------------------------------------------------------------------------
# Turn structure
# ---------------------------------------------------------------------------


def _do_end_turn(resolution: Resolution, effect: Effect) -> None:
    """CR 724: end the turn - the stack is exiled and the turn skips to cleanup."""
    game = resolution.game
    for object_id in list(game.stack):
        obj = game.objects.get(object_id)
        if obj is not None:
            game._remove_from_zone(obj)
            game.objects.pop(object_id, None)
    game.stack.clear()
    game.pending_triggers.clear()
    game.end_turn_requested = True
    game.log.record(game, "The turn ends immediately", kind="turn")


def _do_skip_step(resolution: Resolution, effect: Effect) -> None:
    resolution.game.skipped_steps.add(int(_amount(resolution, effect)))


def _do_take_initiative(resolution: Resolution, effect: Effect) -> None:
    from ..cr700_additional_rules.cr725_designations import take_initiative

    for player_id in _players(resolution, effect):
        take_initiative(resolution.game, player_id)


def _do_add_experience(resolution: Resolution, effect: Effect) -> None:
    amount = max(1, _amount(resolution, effect))
    for player_id in _players(resolution, effect):
        resolution.game.player(player_id).experience += amount


def _do_ring_tempts(resolution: Resolution, effect: Effect) -> None:
    """CR 701.54: the Ring tempts each of these players."""
    from ..cr700_additional_rules.cr701_ring import tempt

    for player_id in _players(resolution, effect):
        tempt(resolution.game, player_id)


def _do_sacrifice_blockers_at_end_of_combat(resolution: Resolution, effect: Effect) -> None:
    """Each creature blocking the attacker that triggered this is sacrificed
    by its controller at end of combat."""
    from ..cr700_additional_rules.cr701_ring import sacrifice_blockers_at_end_of_combat

    stack_object = resolution.stack_object
    event = getattr(stack_object, "trigger_event", None) if stack_object else None
    if event is None:
        return
    sacrifice_blockers_at_end_of_combat(
        resolution.game, event.object_id, resolution.controller
    )


def _do_harness(resolution: Resolution, effect: Effect) -> None:
    """CR 701.64a/b: only a permanent can become harnessed, and one that is
    stays so; nothing is tapped or paid - that is the ability's cost."""
    for obj in _objects(resolution, effect):
        if obj.zone is Zone.BATTLEFIELD and not obj.harnessed:
            obj.harnessed = True
            resolution.game.invalidate_characteristics()
            resolution.game.log.record(resolution.game, f"{obj} becomes harnessed", kind="designation")


def _do_become_renowned(resolution: Resolution, effect: Effect) -> None:
    """CR 702.112b: only a permanent can be or become renowned, and one that
    is stays so until it leaves the battlefield."""
    for obj in _objects(resolution, effect):
        if obj.zone is Zone.BATTLEFIELD and not obj.renowned:
            obj.renowned = True
            resolution.game.invalidate_characteristics()
            resolution.game.log.record(resolution.game, f"{obj} becomes renowned", kind="designation")
            resolution.game.emit(
                Event(
                    EventKind.BECAME_RENOWNED,
                    object_id=obj.id,
                    player=obj.controller,
                    source=resolution.source,
                )
            )


def _do_blocks_source_if_able(resolution: Resolution, effect: Effect) -> None:
    """A requirement (CR 509.1c) that each creature acted on blocks the source
    if able, for the effect's duration. Pinned to these objects: a creature
    or source that leaves and returns is a new object (CR 400.7) and no
    longer under it."""
    from ..cr500_turn_structure.restrictions import (
        Act,
        Requirement,
        register_standing_requirement,
    )
    from ..kernel.query import ObjectFilter

    game = resolution.game
    source = game.objects.get(resolution.source)
    if source is None or source.zone is not Zone.BATTLEFIELD:
        return
    for obj in _objects(resolution, effect):
        if obj.zone is not Zone.BATTLEFIELD:
            continue
        register_standing_requirement(
            game,
            Requirement(
                act=Act.BLOCK,
                subject=ObjectFilter(specific=(obj.id,)),
                counterpart=ObjectFilter(specific=(source.id,)),
                source=source.id,
                controller=resolution.controller,
                text=effect.text or f"{obj} blocks {source} if able",
                duration=effect.duration,
                created_turn=game.turn,
            ),
        )


def _do_enters_as_choice(resolution: Resolution, effect: Effect) -> None:
    """CR 208.2b, reached by resolution rather than by entering: the choice
    is made for the permanent the effect refers to. As a printed "as this
    enters" ability it is applied as a self-entry replacement instead
    (``cr614_replacement.apply_self_entry_replacements``)."""
    from .cr614_replacement import become_chosen_characteristics

    for obj in _objects(resolution, effect):
        if obj.zone is Zone.BATTLEFIELD:
            become_chosen_characteristics(resolution.game, obj, effect)


def _do_villainous_choice(resolution: Resolution, effect: Effect) -> None:
    """CR 701.55: "[a player] faces a villainous choice - [A], or [B]".

    The options are the children. Each player facing it chooses one and all
    of that option is performed (701.55a); several players face it one at a
    time in APNAP order (701.55d); an impossible option may be chosen and is
    done as far as possible (701.55b), which executing it gives for free.
    Inside an option, "that player" is the player facing the choice.
    """
    game = resolution.game
    options = effect.children
    if not options:
        return
    facing = _players(resolution, effect)
    order = [p for p in game.apnap_order() if p in facing]
    previous = resolution.that_player
    for player_id in order:
        agent = game.agent_for(player_id)
        chooser = getattr(agent, "choose_villainous_option", None)
        index = 0
        if chooser is not None:
            picked = chooser(game, player_id, options)
            if isinstance(picked, int) and 0 <= picked < len(options):
                index = picked
        game.log.record(
            game,
            f"{game.player(player_id).name} chooses: {options[index].text or index}",
            kind="choice",
            player=player_id,
        )
        resolution.that_player = player_id
        execute_one(resolution, options[index])
    resolution.that_player = previous


def _do_choose_mode(resolution: Resolution, effect: Effect) -> None:
    """CR 700.2: run only the modes chosen when the spell was announced.

    The choice is made at CR 601.2b, long before this point, which is why the
    modes are read off the stack object rather than asked for now.
    """
    stack_object = resolution.stack_object
    if stack_object is not None:
        chosen = stack_object.chosen_modes
    else:
        # CR 605.3b: a mana ability never becomes a stack object, so its modes
        # ride on the resolution itself.
        chosen = resolution.chosen_modes
    if not chosen:
        # CR 700.2d: a modal spell with no legal modes chosen does nothing.
        return
    for index in chosen:
        if 0 <= index < len(effect.children):
            execute_one(resolution, effect.children[index])


def _do_set_class_level(resolution: Resolution, effect: Effect) -> None:
    """CR 716.2a: "this Class's level becomes N".

    Becomes, not increases - so an effect that sets a Class to level 3 while it
    is level 1 is legal if something else granted it, and the level never goes
    backwards on its own.
    """
    from ..cr300_card_types.cr300_card_types import class_level, set_class_level

    amount = _amount(resolution, effect)
    for obj in _objects(resolution, effect):
        if class_level(resolution.game, obj.id) != amount:
            set_class_level(resolution.game, obj.id, amount)
            resolution.game.log.record(
                resolution.game,
                f"{obj} becomes level {amount}",
                kind="class-level",
                player=obj.controller,
            )
            # CR 716.2: gaining a level is something other abilities watch
            # for. Changing the level and telling nobody meant "When this
            # Class becomes level 2" could never fire.
            resolution.game.emit(
                Event(
                    EventKind.CLASS_LEVEL_GAINED,
                    object_id=obj.id,
                    player=obj.controller,
                    amount=amount,
                )
            )


def _do_become_solved(resolution: Resolution, effect: Effect) -> None:
    """CR 719.3b: the Case becomes solved, and stays solved until it leaves."""
    from ..cr300_card_types.cr300_card_types import become_solved

    for obj in _objects(resolution, effect):
        become_solved(resolution.game, obj.id)


def _do_flip_permanent(resolution: Resolution, effect: Effect) -> None:
    """CR 710.4: flip a permanent. One way, and only on the battlefield."""
    from ..cr300_card_types.cr300_card_types import flip

    for obj in _objects(resolution, effect):
        flip(resolution.game, obj)


def _do_change_targets(resolution: Resolution, effect: Effect) -> None:
    """CR 115.7: change the targets of a spell or ability on the stack.

    Every new target must be legal, and CR 115.3 forbids changing a target to
    one already chosen for that same instance. A change that cannot be made
    legally simply does not happen - the original target stays.
    """
    from ..kernel.matching import find

    game = resolution.game
    for obj in _objects(resolution, effect):
        if obj.zone is not Zone.STACK or obj.ability is None:
            continue
        rebuilt: list[tuple[ObjectId, ...]] = []
        changed = False
        for index, node in enumerate(
            _targeting_nodes(obj.ability, obj.chosen_modes)
        ):
            current = obj.targets[index] if index < len(obj.targets) else ()
            legal = [
                candidate.id
                for candidate in find(
                    game, node.targets, source=obj.id, controller=obj.controller
                )
                if candidate.id not in current
            ]
            if not legal:
                rebuilt.append(current)
                continue
            rebuilt.append((legal[0],))
            changed = True
        if changed:
            obj.targets = tuple(rebuilt)
            game.log.record(game, f"Targets changed for {obj}", kind="effect")


def _targeting_nodes(ability, chosen_modes: tuple[int, ...] = ()) -> list[Effect]:
    """The target slots an object on the stack actually has.

    CR 700.2c: an unchosen mode's targets were never chosen, so its targeting
    effects hold no slot. Walking every mode instead lined the object's
    targets up against another mode's filters and rebuilt slots that do not
    exist.
    """
    from .cr601_casting import targeted_nodes

    return [
        node
        for node in targeted_nodes(ability.effects, chosen_modes or None)
        if node.targets is not None
    ]


def _do_cast_without_paying(resolution: Resolution, effect: Effect) -> None:
    """CR 118.5: cast a card without paying its mana cost.

    Cascade, Ripple, suspend and rebound all end here. Casting is still
    casting: targets are chosen, the spell goes on the stack, and cast triggers
    fire. Only the mana is waived, and only if the controller wants to - "you
    may cast" is the usual wording.
    """
    from ..cr100_game_concepts.cr117_priority import Action, ActionKind
    from .cr601_casting import CastError, cast_spell

    game = resolution.game
    if effect.cast_a_copy:
        _cast_a_copy_without_paying(resolution, effect)
        return
    for obj in _objects(resolution, effect):
        if obj.zone is Zone.BATTLEFIELD or obj.zone is Zone.STACK:
            continue
        if not _wants(resolution, effect):
            continue
        obj.cast_without_paying = True
        try:
            cast_spell(
                game,
                resolution.controller,
                Action(
                    ActionKind.CAST_SPELL,
                    source=obj.id,
                    face_index=effect.face_index,
                ),
            )
        except CastError as exc:
            obj.cast_without_paying = False
            game.log.record(game, f"Free cast abandoned: {exc}", kind="illegal")


def _cast_a_copy_without_paying(resolution: Resolution, effect: Effect) -> None:
    """CR 707.12: cast a copy of an object, not the object.

    The copy is created in the zone the effect names (or where the object
    is) and cast from there while this ability resolves, following CR 601.2a-h.
    A copy that is not cast, or cannot be, stays behind as a copy of a card
    outside the stack and the battlefield, and CR 704.5e removes it.

    The object copied is the one the effect refers to, as it last existed:
    Paradigm's "this object" is a spell that has long since left the stack.
    """
    from ..cr100_game_concepts.cr117_priority import Action, ActionKind
    from ..kernel.gameobject import ObjectKind
    from .cr601_casting import CastError, cast_spell

    game = resolution.game
    spec = effect.targets
    if spec is None or spec.source_only:
        original = game.objects.get(resolution.source)
        originals = [original] if original is not None and original.card else []
    else:
        originals = _objects(resolution, effect)

    for original in originals:
        zone = effect.zone if effect.zone is not None else original.zone
        # CR 112.2a: the copy is owned by the player told to create and cast it.
        copy = game.create_object(
            original.card,
            resolution.controller,
            zone,
            kind=ObjectKind.COPY,
            face_index=original.face_index,
        )
        if not _wants(resolution, effect):
            continue
        copy.cast_without_paying = True
        try:
            cast_spell(
                game,
                resolution.controller,
                Action(ActionKind.CAST_SPELL, source=copy.id, face_index=effect.face_index),
            )
        except CastError as exc:
            game.log.record(game, f"Free cast of a copy abandoned: {exc}", kind="illegal")


def _do_cascade(resolution: Resolution, effect: Effect) -> None:
    """CR 702.85a, against this spell's mana value as it last existed - the
    ability resolves after the spell may have left the stack."""
    game = resolution.game
    spell = game.objects.get(resolution.source)
    if spell is None:
        return
    below = game.characteristics(spell).mana_value
    _exile_until_a_free_cast(
        resolution, lambda mana_value: mana_value < below, to_hand_if_not_cast=False
    )


def _do_discover(resolution: Resolution, effect: Effect) -> None:
    """CR 701.57a: discover N."""
    limit = _amount(resolution, effect)
    _exile_until_a_free_cast(
        resolution, lambda mana_value: mana_value <= limit, to_hand_if_not_cast=True
    )


def _exile_until_a_free_cast(
    resolution: Resolution, fits, *, to_hand_if_not_cast: bool
) -> None:
    """The procedure cascade and discover share.

    Exile from the top of the library one card at a time until a nonland card
    whose mana value fits (or the library runs out). That card may be cast
    without paying its mana cost; discover puts it into its owner's hand if
    it is not. Every other card exiled this way goes to the bottom of the
    library in a random order (Game.rng, so a replay orders them the same).
    """
    from ..cr100_game_concepts.cr117_priority import Action, ActionKind
    from .cr601_casting import CastError, cast_spell

    game = resolution.game
    player_id = resolution.controller
    library = game.player(player_id).library
    exiled: list[GameObject] = []
    hit: GameObject | None = None
    while library:
        card = game.objects[library[0]]
        moved = game.move_object(card, Zone.EXILE, to_player=card.owner)
        chars = game.characteristics(moved)
        if not chars.is_land and fits(chars.mana_value):
            hit = moved
            break
        exiled.append(moved)

    if hit is not None:
        cast = False
        if _wants(resolution, Effect(EffectKind.CAST_WITHOUT_PAYING, text="cast it free")):
            hit.cast_without_paying = True
            try:
                cast_spell(game, player_id, Action(ActionKind.CAST_SPELL, source=hit.id))
                cast = True
            except CastError as exc:
                hit.cast_without_paying = False
                game.log.record(game, f"Free cast abandoned: {exc}", kind="illegal")
        if not cast:
            if to_hand_if_not_cast:
                game.move_object(hit, Zone.HAND, to_player=hit.owner)
            else:
                exiled.append(hit)

    game.rng.shuffle(exiled)
    for obj in exiled:
        if obj.is_live and obj.zone is Zone.EXILE:
            game.move_object(obj, Zone.LIBRARY, to_player=obj.owner)


def _do_play_from_zone(resolution: Resolution, effect: Effect) -> None:
    """CR 601.3: play a card from somewhere other than your hand.

    A land played this way still uses the land drop (CR 305.2); a spell is cast
    normally and pays its cost, because permission to play is not permission to
    play for free.
    """
    from ..cr100_game_concepts.cr117_priority import Action, ActionKind
    from .cr601_casting import CastError, cast_spell, play_land

    game = resolution.game
    for obj in _objects(resolution, effect):
        if not _wants(resolution, effect):
            continue
        chars = game.characteristics(obj)
        player = game.player(resolution.controller)
        try:
            if chars.is_land:
                if player.lands_played >= player.max_lands:
                    continue
                play_land(
                    game,
                    resolution.controller,
                    Action(ActionKind.PLAY_LAND, source=obj.id),
                )
            else:
                cast_spell(
                    game,
                    resolution.controller,
                    Action(
                    ActionKind.CAST_SPELL,
                    source=obj.id,
                    face_index=effect.face_index,
                ),
                )
        except CastError as exc:
            game.log.record(game, f"Play abandoned: {exc}", kind="illegal")


def _do_extra_phase(resolution: Resolution, effect: Effect) -> None:
    """CR 500.8: an extra phase after the current one."""
    from ..kernel.enums import Phase

    game = resolution.game
    phase = (
        Phase(effect.amount.constant)
        if effect.amount.is_constant and effect.amount.constant
        else Phase.PRECOMBAT_MAIN
    )
    game.extra_phases.append(phase)
    game.log.record(game, f"Extra {phase.name} phase", kind="effect")


def _do_extra_step(resolution: Resolution, effect: Effect) -> None:
    """CR 500.8: an extra step after the current one."""
    from ..kernel.enums import Step

    game = resolution.game
    step = (
        Step(effect.amount.constant)
        if effect.amount.is_constant and effect.amount.constant
        else Step.MAIN
    )
    game.extra_steps.append(step)
    game.log.record(game, f"Extra {step.name} step", kind="effect")


def _do_untap_step_skip(resolution: Resolution, effect: Effect) -> None:
    """Skip the untap step.

    Distinct from "permanents don't untap": the whole step is skipped, so the
    turn-based actions in it - phasing above all - do not happen either.
    """
    from ..kernel.enums import Step

    resolution.game.skipped_steps.add(int(Step.UNTAP))


def _do_vote(resolution: Resolution, effect: Effect) -> None:
    """CR 701.34: each player votes, starting with the player after the
    controller and proceeding in turn order.

    The tally is recorded on the game so a later sentence can ask who won. The
    engine does not decide what winning means, because the card does.
    """
    game = resolution.game
    choices = tuple(range(max(2, len(effect.children))))
    tally: dict[int, int] = dict.fromkeys(choices, 0)

    for player_id in game.apnap_order(game.next_player(resolution.controller)):
        agent = game.agent_for(player_id)
        chooser = getattr(agent, "choose_vote", None)
        pick = chooser(game, player_id, choices) if chooser else choices[0]
        if pick not in tally:
            pick = choices[0]
        tally[pick] += 1

    game.last_vote = tally
    winner = max(tally, key=lambda choice: (tally[choice], -choice))
    if 0 <= winner < len(effect.children):
        execute_one(resolution, effect.children[winner])


def _do_venture(resolution: Resolution, effect: Effect) -> None:
    """CR 701.49: venture into the dungeon, or into [quality] (701.49d).

    The procedure - choosing a dungeon, following an arrow, completing the
    one whose bottommost room was reached - is ``cr309_dungeons``'s.
    """
    from ..cr300_card_types.cr309_dungeons import venture

    for player_id in _players(resolution, effect):
        venture(resolution.game, player_id, effect.dungeon_quality)


def _do_open_attraction(resolution: Resolution, effect: Effect) -> None:
    """CR 701.51b: open an Attraction, as many times as the sentence says.

    Each is its own opening - "open two Attractions" opens the top card, then
    the new top card - so each one triggers "whenever you open an Attraction"
    (CR 701.51c). The procedure is ``cr717_attractions``'s.
    """
    from ..cr700_additional_rules.cr717_attractions import open_attraction

    count = _count(resolution, effect)
    for player_id in _players(resolution, effect):
        for _ in range(max(0, count)):
            open_attraction(resolution.game, player_id)


def _do_roll_to_visit(resolution: Resolution, effect: Effect) -> None:
    """CR 701.52a: roll to visit your Attractions."""
    from ..cr700_additional_rules.cr717_attractions import roll_to_visit

    for player_id in _players(resolution, effect):
        result = roll_to_visit(resolution.game, player_id)
        # CR 706.4: the roll's number is there for the rest of the ability.
        resolution.die_results = (result,)


def _wants(resolution: Resolution, effect: Effect) -> bool:
    """Whether the controller takes an optional effect."""
    if effect.kind is not EffectKind.OPTIONAL and not effect.children:
        pass
    agent = resolution.game.agent_for(resolution.controller)
    chooser = getattr(agent, "choose_optional", None)
    if chooser is None:
        return True
    return bool(chooser(resolution.game, resolution.controller, effect))


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

Executor = Callable[[Resolution, Effect], None]

EXECUTORS: dict[EffectKind, Executor] = {
    EffectKind.SEQUENCE: _do_sequence,
    EffectKind.CONDITIONAL: _do_conditional,
    EffectKind.REPEAT: _do_repeat,
    EffectKind.OPTIONAL: _do_optional,
    EffectKind.NOTHING: _do_nothing,
    # Listed rather than left to the continuous-effect default below, which
    # still receives every replacement shape but unearth's.
    EffectKind.REPLACEMENT: _do_replacement,
    EffectKind.DRAW: _do_draw,
    EffectKind.DISCARD: _do_discard,
    EffectKind.MILL: _do_mill,
    EffectKind.MOVE_ZONE: _do_move_zone,
    EffectKind.DESTROY: _do_destroy,
    EffectKind.EXILE: _do_exile,
    EffectKind.SACRIFICE: _do_sacrifice,
    EffectKind.RETURN_TO_HAND: _do_return_to_hand,
    EffectKind.CREATE_TOKEN: _do_create_token,
    EffectKind.RESTRICTION: _do_restriction,
    EffectKind.ROLL_DICE: _do_roll_dice,
    EffectKind.SUSPEND_RULE: _do_suspend_rule,
    EffectKind.DAMAGE: _do_damage,
    EffectKind.GAIN_LIFE: _do_gain_life,
    EffectKind.LOSE_LIFE: _do_lose_life,
    EffectKind.SET_LIFE: _do_set_life,
    EffectKind.ADD_POISON: _do_add_poison,
    EffectKind.FIGHT: _do_fight,
    EffectKind.TAP: _do_tap,
    EffectKind.UNTAP: _do_untap,
    EffectKind.ADD_COUNTERS: _do_add_counters,
    EffectKind.REMOVE_COUNTERS: _do_remove_counters,
    EffectKind.PROLIFERATE: _do_proliferate,
    EffectKind.ATTACH: _do_attach,
    EffectKind.UNATTACH: _do_unattach,
    EffectKind.GAIN_CONTROL: _do_gain_control,
    EffectKind.GOAD: _do_goad,
    EffectKind.TRANSFORM: _do_transform,
    EffectKind.COPY_SPELL: _do_copy_spell,
    EffectKind.COUNTER_SPELL: _do_counter_spell,
    EffectKind.UNLESS_PAYS: _do_unless_pays,
    EffectKind.ADD_MANA: _do_add_mana,
    EffectKind.PLAYER_WINS: _do_player_wins,
    EffectKind.PLAYER_LOSES: _do_player_loses,
    EffectKind.BECOME_MONARCH: _do_become_monarch,
    EffectKind.START_ENGINES: _do_start_engines,
    EffectKind.PAY_COST: _do_pay_cost,
    EffectKind.IF_YOU_DONT: _do_if_you_dont,
    EffectKind.ADD_ENERGY: _do_add_energy,
    EffectKind.EXTRA_LAND_DROP: _do_extra_land_drop,
    EffectKind.EXTRA_TURN: _do_extra_turn,
    EffectKind.SEARCH_LIBRARY: _do_search_library,
    EffectKind.SHUFFLE: _do_shuffle,
    EffectKind.SEEK: _do_seek,
    EffectKind.CONJURE: _do_conjure,
    EffectKind.PERPETUALLY: _do_perpetually,
    EffectKind.REVEAL: _do_reveal,
    EffectKind.SCRY: _do_scry,
    EffectKind.SURVEIL: _do_surveil,
    EffectKind.PUT_ONTO_BATTLEFIELD: _do_put_onto_battlefield,
    EffectKind.PUT_ON_LIBRARY: _do_put_on_library,
    EffectKind.LOOK_AT_TOP: _do_look_at_top,
    EffectKind.EXPLORE: _do_explore,
    EffectKind.COPY_PERMANENT: _do_copy_permanent_effect,
    EffectKind.TURN_FACE_UP: _do_turn_face_up,
    EffectKind.TURN_FACE_DOWN: _do_turn_face_down,
    EffectKind.MANIFEST: _do_manifest,
    EffectKind.PHASE_OUT: _do_phase_out,
    EffectKind.REGENERATE: _do_regenerate,
    EffectKind.MONSTROSITY: _do_monstrosity,
    EffectKind.ADAPT: _do_adapt,
    EffectKind.EXCHANGE_CONTROL: _do_exchange_control,
    EffectKind.EXCHANGE_LIFE: _do_exchange_life,
    EffectKind.PREVENT_DAMAGE: _do_prevent_damage,
    EffectKind.REDIRECT_DAMAGE: _do_redirect_damage,
    EffectKind.END_TURN: _do_end_turn,
    EffectKind.SKIP_STEP: _do_skip_step,
    EffectKind.TAKE_INITIATIVE: _do_take_initiative,
    EffectKind.ADD_EXPERIENCE: _do_add_experience,
    EffectKind.RING_TEMPTS: _do_ring_tempts,
    EffectKind.CASCADE: _do_cascade,
    EffectKind.DISCOVER: _do_discover,
    EffectKind.VILLAINOUS_CHOICE: _do_villainous_choice,
    EffectKind.HARNESS: _do_harness,
    EffectKind.BECOME_RENOWNED: _do_become_renowned,
    EffectKind.BLOCKS_SOURCE_IF_ABLE: _do_blocks_source_if_able,
    EffectKind.ENTERS_AS_CHOICE: _do_enters_as_choice,
    EffectKind.SACRIFICE_BLOCKERS_AT_END_OF_COMBAT: _do_sacrifice_blockers_at_end_of_combat,
    EffectKind.SET_CLASS_LEVEL: _do_set_class_level,
    # A static ability, read by the trigger collector rather than resolved.
    # Present here because an opcode with no entry is an opcode the parser is
    # not allowed to emit.
    EffectKind.EXTRA_TRIGGER: _do_nothing,
    EffectKind.CHOOSE_QUALITY: _do_choose_quality,
    EffectKind.BECOME_SOLVED: _do_become_solved,
    EffectKind.FLIP_PERMANENT: _do_flip_permanent,
    EffectKind.DELAYED_TRIGGER: _do_delayed_trigger,
    EffectKind.REFLEXIVE_TRIGGER: _do_reflexive_trigger,
    EffectKind.CONTROL_PLAYER: _do_control_player,
    EffectKind.RESTART_GAME: _do_restart_game,
    EffectKind.BECOME_PREPARED: _do_become_prepared,
    EffectKind.CHANGE_TARGETS: _do_change_targets,
    EffectKind.CAST_WITHOUT_PAYING: _do_cast_without_paying,
    EffectKind.PLAY_FROM_ZONE: _do_play_from_zone,
    EffectKind.EXTRA_PHASE: _do_extra_phase,
    EffectKind.EXTRA_STEP: _do_extra_step,
    EffectKind.UNTAP_STEP_SKIP: _do_untap_step_skip,
    EffectKind.VOTE: _do_vote,
    EffectKind.VENTURE: _do_venture,
    EffectKind.OPEN_ATTRACTION: _do_open_attraction,
    EffectKind.ROLL_TO_VISIT: _do_roll_to_visit,
    EffectKind.CHOOSE_MODE: _do_choose_mode,
}

# Every continuous-effect opcode registers a continuous effect rather than
# changing the game state once (CR 611.2).
for _kind in CONTINUOUS_KINDS:
    EXECUTORS.setdefault(_kind, _register_continuous)
