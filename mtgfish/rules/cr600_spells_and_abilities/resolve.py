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

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from ..cr100_game_concepts import actions
from ..kernel.enums import Duration, Zone
from ..kernel.events import Event, EventKind
from ..kernel.gameobject import GameObject
from ..kernel.ids import ObjectId, PlayerId
from ..kernel.query import ValueKind
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
        EffectKind.RETURN_TO_HAND,
        EffectKind.PUT_ONTO_BATTLEFIELD,
        EffectKind.CREATE_TOKEN,
        EffectKind.ADD_COUNTERS,
        EffectKind.GAIN_CONTROL,
        EffectKind.COPY_PERMANENT,
        EffectKind.SEARCH_LIBRARY,
        EffectKind.DAMAGE,
    }
)


def execute_one(resolution: Resolution, effect: Effect) -> None:
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

    if effect.kind in _REMEMBERING and effect.targets is not None:
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
        return [
            player_id
            for player_id in _targeted_players(resolution, effect, chosen)
            # CR 608.2b: a player who has left the game is no longer legal.
            if not game.player(player_id).has_lost
        ]

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
    )


def _condition_holds(resolution: Resolution, effect: Effect) -> bool:
    if effect.condition.is_always:
        return True
    from ..kernel.conditions import holds

    return holds(
        resolution.game,
        effect.condition,
        source=resolution.source,
        controller=resolution.controller,
        # What "it" means in "if it's blue" - only the resolution knows.
        remembered=tuple(resolution.remembered),
    )


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
    agent = resolution.game.agent_for(resolution.controller)
    if agent is None or agent.choose_optional(resolution.game, resolution.controller, effect):
        execute(resolution, effect.children)


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
        if cost is None or not can_pay_cost(game, player_id, cost):
            execute(resolution, effect.children)
            continue
        agent = game.agent_for(player_id)
        willing = True
        if agent is not None and hasattr(agent, "choose_pay"):
            willing = agent.choose_pay(game, player_id, cost, effect)
        # ``can_pay_cost`` is an upper bound, so a willing player can still
        # fail to pay. A payment that did not happen must not waive the
        # effect, or every "unless that player pays" is free to dodge.
        if not (willing and pay_cost(game, player_id, cost)):
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
    for player_id in _players(resolution, effect):
        actions.mill(resolution.game, player_id, count, source=resolution.source)


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
    for obj in _objects(resolution, effect):
        exiled = actions.exile(game, obj, source=resolution.source)
        if until_source_leaves and exiled is not None:
            returning.append(exiled.id)
    if returning:
        _return_when_the_source_leaves(resolution, tuple(returning))


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


def _do_sacrifice(resolution: Resolution, effect: Effect) -> None:
    for obj in _objects(resolution, effect):
        actions.sacrifice(resolution.game, obj, source=resolution.source)


def _do_return_to_hand(resolution: Resolution, effect: Effect) -> None:
    for obj in _objects(resolution, effect):
        actions.bounce(resolution.game, obj, source=resolution.source)


def _do_move_zone(resolution: Resolution, effect: Effect) -> None:
    destination = effect.zone or Zone.GRAVEYARD
    for obj in _objects(resolution, effect):
        resolution.game.move_object(obj, destination, to_player=obj.owner)


def _do_discard(resolution: Resolution, effect: Effect) -> None:
    game = resolution.game
    count = _amount(resolution, effect)
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
            actions.discard(game, obj, source=resolution.source)


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
    for player_id in _players(resolution, effect):
        create_tokens(
            resolution.game,
            effect.token,
            player_id,
            count,
            source=resolution.source,
        )


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
    from ..cr500_turn_structure.restrictions import Restriction, register_standing

    for restriction in effect.restrictions:
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


def _mana_kind(symbol: str, *, snow: bool, restriction=None):
    """One printed mana symbol, as a unit of mana in a pool.

    Hybrid symbols in a mana-*production* ability ("Add {G/U}") are a choice;
    the first color keeps the run deterministic, which replay depends on.
    """
    from ..cr100_game_concepts.cr106_mana import ManaKind, parse_mana_symbol
    from ..kernel.enums import Color

    parsed = parse_mana_symbol(symbol.strip("{}"))
    colors = list(parsed.colors)
    return ManaKind(
        colors[0] if colors else Color.NONE, snow=snow, restriction=restriction
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
    )


def _do_add_mana(resolution: Resolution, effect: Effect) -> None:
    """CR 106.1: add mana to a player's pool."""
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
        for _ in range(repeat):
            for symbol in effect.mana_produced:
                player.mana_pool.add(
                    _mana_kind(symbol, snow=snow, restriction=effect.mana_restriction),
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
        # the menu and the player picks - the payer's choice when there was
        # one, otherwise a deterministic pick, first color.
        amount = _count(resolution, effect)
        wanted = resolution.mana_color
        chosen = (
            wanted
            if wanted and (effect.colors & wanted) == wanted
            else next(iter(effect.colors))
        )
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
    """
    from ..kernel.matching import matches

    game = resolution.game
    wanted = max(1, _amount(resolution, effect))
    destination = effect.zone or Zone.HAND

    for player_id in _players(resolution, effect):
        player = game.player(player_id)
        found: list[GameObject] = []
        for object_id in list(player.library):
            obj = game.objects.get(object_id)
            if obj is None:
                continue
            if effect.targets is None or matches(
                game, obj, effect.targets, source=resolution.source, controller=player_id
            ):
                found.append(obj)
            if len(found) >= wanted:
                break

        # "Search your library for a basic land card, put it onto the
        # battlefield *tapped*" (CR 614.1c). Every ramp spell in the format
        # says it, and a land that arrives untapped is a full turn of mana the
        # deck does not have.
        tapped = "tapped" in effect.keywords and destination is Zone.BATTLEFIELD
        for obj in found:
            moved = game.move_object(obj, destination, to_player=player_id)
            if tapped and moved.zone is Zone.BATTLEFIELD:
                # A replacement effect may have sent it somewhere else.
                moved.tapped = True
        game.emit(
            Event(EventKind.SEARCHED_LIBRARY, player=player_id, amount=len(found))
        )
        game.shuffle_library(player_id)


def _do_shuffle(resolution: Resolution, effect: Effect) -> None:
    for player_id in _players(resolution, effect):
        resolution.game.shuffle_library(player_id)


def _do_reveal(resolution: Resolution, effect: Effect) -> None:
    """CR 701.16. Revealing changes no zone; it only makes information public."""
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
        permanent = game.move_object(obj, Zone.BATTLEFIELD, to_player=to)
        permanent.controller = to
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
    game = resolution.game
    on_top = effect.amount.constant >= 0
    for obj in _objects(resolution, effect):
        game.move_object(obj, Zone.LIBRARY, to_player=obj.owner, to_top=on_top)


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
    """CR 701.34 / 707.9: a face-down permanent is turned up.

    CR 116.2b makes this a *special action*: it uses no stack and cannot be
    responded to, which is why a morph flip cannot be answered.
    """
    game = resolution.game
    for obj in _objects(resolution, effect):
        if not obj.face_down:
            continue
        obj.face_down = False
        game.invalidate_characteristics()
        game.emit(
            Event(EventKind.TURNED_FACE_UP, object_id=obj.id, player=obj.controller)
        )


def _do_turn_face_down(resolution: Resolution, effect: Effect) -> None:
    """CR 701.34b, and CR 712.16 stops a double-faced permanent being turned down."""
    from ..cr700_additional_rules.cr707_faces import layout_of

    game = resolution.game
    for obj in _objects(resolution, effect):
        if obj.face_down:
            continue
        from ..kernel.enums import Layout

        if layout_of(obj) in (Layout.TRANSFORM, Layout.MODAL_DFC, Layout.MELD):
            continue
        obj.face_down = True
        game.invalidate_characteristics()
        game.emit(
            Event(EventKind.TURNED_FACE_DOWN, object_id=obj.id, player=obj.controller)
        )


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
    from .cr614_replacement import ReplacementEffect, ReplacementKind, register

    if "combat" in effect.keywords:
        watched = frozenset({EventKind.COMBAT_DAMAGE_DEALT})
    else:
        watched = frozenset({EventKind.DAMAGE_DEALT, EventKind.COMBAT_DAMAGE_DEALT})

    register(
        resolution.game,
        ReplacementEffect(
            kind=ReplacementKind.PREVENT_DAMAGE,
            event_kinds=watched,
            subject=effect.targets,
            players=effect.players,
            source=resolution.source,
            controller=resolution.controller,
            amount=_amount(resolution, effect),
            one_shot=effect.amount.constant > 0,
            text=effect.text or "prevent damage",
        ),
    )


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
    """CR 701.51: the Ring tempts you - the count matters, not just the fact."""
    for player_id in _players(resolution, effect):
        player = resolution.game.player(player_id)
        player.ring_tempted_count += 1
        resolution.game.emit(
            Event(
                EventKind.RING_TEMPTED,
                player=player_id,
                amount=player.ring_tempted_count,
            )
        )


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
    """CR 701.46: venture into the dungeon.

    Position tracking only. Which dungeon, and what each room does, is card
    text the parser has not read yet - so the position advances and the event
    fires, and nothing pretends to know what the room said.
    """
    game = resolution.game
    for player_id in _players(resolution, effect):
        player = game.player(player_id)
        player.dungeon_room += 1
        game.emit(
            Event(
                EventKind.DUNGEON_VENTURED,
                player=player_id,
                amount=player.dungeon_room,
            )
        )


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
    EffectKind.REVEAL: _do_reveal,
    EffectKind.SCRY: _do_scry,
    EffectKind.SURVEIL: _do_surveil,
    EffectKind.PUT_ONTO_BATTLEFIELD: _do_put_onto_battlefield,
    EffectKind.PUT_ON_LIBRARY: _do_put_on_library,
    EffectKind.EXPLORE: _do_explore,
    EffectKind.COPY_PERMANENT: _do_copy_permanent_effect,
    EffectKind.TURN_FACE_UP: _do_turn_face_up,
    EffectKind.TURN_FACE_DOWN: _do_turn_face_down,
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
    EffectKind.CHOOSE_MODE: _do_choose_mode,
}

# Every continuous-effect opcode registers a continuous effect rather than
# changing the game state once (CR 611.2).
for _kind in CONTINUOUS_KINDS:
    EXECUTORS.setdefault(_kind, _register_continuous)
