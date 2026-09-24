"""Infinite loops: noticed, shortcut, and ended (CR 732.2a, CR 104.4b).

Before this, an infinite could not do anything. An optional combo - an untap
engine, a sacrifice-and-return loop - was cut off by the bot's own cap of eight
activations per ability per turn, so it never got far enough to win. A
mandatory one - Sanguine Bond and Exquisite Blood - resolved trigger after
trigger until the priority loop hit its two-thousand-round cap and the game was
thrown out as a runaway with no winner. Either way the loop that should have
ended the game ended the *measurement* instead.

The rules already say what to do. CR 732.2a lets the player running an
optional loop propose a number of iterations and skip to the result; CR 104.4b
says a loop of mandatory actions that nothing can stop is a draw. This module is
those two rules.

**Noticing.** Every action taken and every stack object resolved in a priority
window is reduced to a short *step* signature - who did what with which card to
what. When the same cycle of steps repeats ``LOOP_REPETITIONS`` times in a row,
and the board is the same shape at the start of each cycle, and each cycle
changes the game by exactly the same amounts, it is a loop.

**Not misfiring.** The dangerous case is not the infinite, it is the bounded
repeat that looks identical for eight cycles and then stops: Blood Artist
watching a board wipe kill twelve identical tokens. Two things keep that from
being extended into a kill:

* the *shape* includes the stack and the pending triggers, so a chain draining
  a finite stack of triggers changes shape every cycle and is never a loop; and
* anything a cycle *consumes* - cards, counters, tokens, floating mana - caps
  the shortcut at what is actually there. A loop that spends a token per cycle
  with four tokens left runs four more times, not forty.

**Doing it.** The per-cycle change is scaled directly: life, poison, energy,
cards moving between library, hand, graveyard and exile, counters, tokens,
floating mana, until-end-of-turn power and toughness, and the per-turn tallies
storm and friends read. Triggers are suppressed while it is applied, because
every trigger that fires once per cycle is already *in* the cycle and already in
the change being scaled.

**How many times.** An optional loop is run as far as its controller would run
it: far enough to kill every opponent it can reach, never far enough to kill
the controller, and never further than its fuel. A loop that only builds
resources is run to a generous cap. A loop that changes nothing is stopped, and
recorded - it is far more likely a card read wrong than a real combo. A
mandatory loop runs until the first player loses or its fuel runs out; if
neither ever happens, the game is a draw.

What is *not* scaled: effects that change which objects exist in ways a count
cannot describe (a token copy of a permanent is cloned from its card, so it
loses the copy), and loops that span turns (extra-turn engines), which the turn
cap still handles.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from .enums import Zone
from .ids import is_player_target, target_player

if TYPE_CHECKING:
    from .game import Game

#: How many identical cycles make a loop. Eight is well past any honest
#: coincidence - two identical triggers in a row is ordinary play, eight
#: identical cycles with identical consequences on an unchanging board is not.
LOOP_REPETITIONS = 8

#: The longest cycle, in steps, that is looked for. A step is one action or one
#: resolution, so twelve covers every two-card and most three-card loops.
MAX_PERIOD = 12

#: How far a loop that builds resources without ending the game is run.
#: Generous on purpose - these only exist to let the bot do something with the
#: result - and bounded, because a million tokens is a slower simulator, not a
#: better answer.
MANA_CAP = 1000
COUNTER_CAP = 1000
LIFE_CAP = 1000
PUMP_CAP = 1000
ZONE_CAP = 1000
#: Tokens are real objects every board computation walks, so this one is
#: small. The *effects* of making them - life drained per token - are scaled in
#: full; only the tokens themselves stop being materialised here.
TOKEN_CAP = 100
#: A ceiling on any single shortcut, whatever the arithmetic says.
MAX_SHORTCUT = 100_000

INF = math.inf

#: What a cycle can run out of. Only the looping player's own resources bound a
#: loop: an opponent with an empty library does not stop you milling them.
CONSUMABLE = frozenset({"library", "hand", "graveyard", "exile", "mana", "counters", "tokens"})

#: Bookkeeping that changes as a side effect of any activity at all - priority
#: passes are counted in the per-turn history. Scaled for consistency, never
#: treated as the loop achieving something.
DERIVED = frozenset({"turn", "spells_cast"})

_GROWTH_CAPS = {
    "life": LIFE_CAP,
    "energy": LIFE_CAP,
    "experience": LIFE_CAP,
    "mana": MANA_CAP,
    "counters": COUNTER_CAP,
    "tokens": TOKEN_CAP,
    "pump": PUMP_CAP,
    "hand": ZONE_CAP,
    "graveyard": ZONE_CAP,
    "exile": ZONE_CAP,
}

#: The zones a card can be moved between by a scaled cycle.
_ZONE_KEYS = (
    ("library", Zone.LIBRARY),
    ("hand", Zone.HAND),
    ("graveyard", Zone.GRAVEYARD),
    ("exile", Zone.EXILE),
)

#: Why a shortcut stopped where it did - kept as constants because the outcome
#: of a kill decides whether the loop may carry on against the next player.
WINS = "far enough to finish the opponents it can reach"
RAN_OUT = "until it ran out of what it spends"
DECLINED = "finishing it would kill the player running it"
NOTHING = "it changed nothing"
RESOURCES = "as far as the resources are worth"
NOBODY = "a mandatory loop nothing can stop"
LOST = "until a player lost"
EMPTY = "it has nothing left to spend"


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Snapshot:
    """The game at one cycle boundary: what must not change, and what may."""

    shape: tuple
    values: dict
    #: The newest continuous effect's timestamp, so the effects one cycle made
    #: can be told apart from the ones before it.
    mark: int


@dataclass(slots=True)
class LoopWindow:
    """Recent steps and the repetition runs over them, for one priority window."""

    history: deque = field(default_factory=lambda: deque(maxlen=MAX_PERIOD + 1))
    #: ``runs[p]``: consecutive steps equal to the step ``p`` before them.
    runs: list = field(default_factory=lambda: [0] * (MAX_PERIOD + 1))
    snapshots: dict = field(default_factory=dict)


def begin_window(game: Game) -> None:
    """A fresh window. Loops are looked for inside one priority window only."""
    game.loop_window = LoopWindow()


def action_step(game: Game, player: int, action) -> tuple:
    """One action as a step. Read *before* the action is performed, because
    performing it can move the source - a sacrifice cost - and then its name is
    gone."""
    obj = game.objects.get(action.source)
    return (
        "A",
        int(player),
        action.kind.name,
        _name(obj),
        action.ability_index,
        action.x_value,
        _targets(game, action.targets),
    )


def resolution_step(game: Game, obj) -> tuple:
    """One resolving stack object as a step. Read before it resolves."""
    if obj is None:
        return ("R", -1, "", ())
    return ("R", int(obj.controller), _label(obj), _targets(game, getattr(obj, "targets", ())))


def note_step(game: Game, step: tuple) -> bool:
    """Record a step, and shortcut the loop it completes if it completes one.

    Returns True when something was done about a loop, so the caller knows the
    game may now be over.
    """
    window = game.loop_window
    if window is None or game.game_over:
        return False

    history = window.history
    history.append(step)
    size = len(history)

    fundamental = 0
    for period in range(1, MAX_PERIOD + 1):
        if size > period and history[-1] == history[-1 - period]:
            window.runs[period] += 1
        elif window.runs[period]:
            window.runs[period] = 0
            window.snapshots.pop(period, None)
        # The shortest period that has repeated at least once. A loop of two
        # steps also repeats with period four, and four is not the loop.
        if not fundamental and window.runs[period] >= period:
            fundamental = period

    if not fundamental:
        return False
    run = window.runs[fundamental]
    if run % fundamental:
        return False  # Mid-cycle.

    snapshots = window.snapshots.setdefault(fundamental, [])
    snapshots.append(_snapshot(game))
    if len(snapshots) > 4:
        del snapshots[0]

    cycles = run // fundamental + 1
    if cycles < LOOP_REPETITIONS or len(snapshots) < 3:
        return False

    cycle = tuple(history[index] for index in range(size - fundamental, size))
    return _consider(game, cycle, snapshots)


def is_exhausted(game: Game, player: int, action) -> bool:
    """Whether this action belongs to a loop already shortcut this turn."""
    if not game.exhausted_loop_steps:
        return False
    return action_step(game, player, action) in game.exhausted_loop_steps


def is_continuing(game: Game, player: int, action) -> bool:
    """Whether this action belongs to a loop that won and may carry on.

    A loop that killed one opponent is re-targeted at the next; the bot's cap
    on repeated activations must not stop it before the detector sees the new
    cycle.
    """
    if not game.continuing_loop_steps:
        return False
    return action_step(game, player, action) in game.continuing_loop_steps


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Contestant:
    """A living player, as far as deciding a shortcut is concerned."""

    id: int
    life: int
    poison: int = 0
    library: int = 0
    cannot_lose: bool = False


@dataclass(frozen=True, slots=True)
class Decision:
    """How many more cycles to run, and why."""

    cycles: int
    outcome: str  # "shortcut", "stopped", "declined", "draw" or "ends"
    reason: str


def decide(
    *,
    optional: bool,
    controller: int,
    delta: dict,
    values: dict,
    players: list[Contestant],
) -> Decision:
    """How far a loop is run. Pure, so it can be tested without a game.

    ``delta`` is what one cycle changes, ``values`` what there is now, both
    keyed the same way (see ``_snapshot``).
    """
    bound = _consumable_bound(delta, values, controller, optional)
    deaths = _deaths(delta, players)
    if optional:
        return _decide_optional(controller, delta, bound, deaths, players)
    return _decide_mandatory(bound, deaths)


def _consumable_bound(delta: dict, values: dict, controller: int, optional: bool) -> float:
    """How many more cycles the looping player's resources pay for."""
    bound = INF
    for key, change in delta.items():
        if change >= 0 or key[0] not in CONSUMABLE or key[1] != controller:
            continue
        available = values.get(key, 0)
        # An optional loop that draws its controller's library stops with a
        # card left: drawing the last one is fine, drawing from empty is not,
        # and nobody chooses to lose to their own combo.
        if (
            optional
            and key[0] == "library"
            and delta.get(("hand", controller), 0) > 0
        ):
            available = max(0, available - 1)
        bound = min(bound, available // -change)
    return bound


def _deaths(delta: dict, players: list[Contestant]) -> dict:
    """For each player, how many more cycles until the loop makes them lose."""
    deaths: dict = {}
    for player in players:
        if player.cannot_lose:
            continue
        cycles = INF
        life = delta.get(("life", player.id), 0)
        if life < 0:
            cycles = min(cycles, math.ceil(max(0, player.life) / -life))
        poison = delta.get(("poison", player.id), 0)
        if poison > 0:
            cycles = min(cycles, math.ceil(max(0, 10 - player.poison) / poison))
        # A loop that draws a player's cards makes them draw from an empty
        # library one cycle after the last card (CR 704.5b).
        library = delta.get(("library", player.id), 0)
        if library < 0 and delta.get(("hand", player.id), 0) > 0:
            cycles = min(cycles, player.library // -library + 1)
        deaths[player.id] = cycles
    return deaths


def _decide_optional(controller, delta, bound, deaths, players) -> Decision:
    own = deaths.get(controller, INF)
    survive = own - 1 if own != INF else INF
    limit = min(bound, survive)

    goals = [cycles for player, cycles in deaths.items() if player != controller and cycles != INF]
    # Milling an opponent's library out is a win too, one draw step later.
    for player in players:
        if player.id == controller:
            continue
        change = delta.get(("library", player.id), 0)
        if change < 0 and delta.get(("hand", player.id), 0) <= 0:
            goals.append(math.ceil(player.library / -change))

    if goals:
        reachable = [goal for goal in goals if goal <= limit]
        if reachable:
            return Decision(_clamp(max(reachable)), "shortcut", WINS)
        if survive <= bound:
            return Decision(0, "declined", DECLINED)
        return Decision(_clamp(limit), "shortcut", RAN_OUT)

    growth = _growth_cycles(delta)
    if growth is None:
        return Decision(0, "stopped", NOTHING)
    cycles = min(limit, growth)
    if cycles <= 0:
        return Decision(0, "stopped", EMPTY)
    return Decision(_clamp(cycles), "shortcut", RESOURCES if cycles == growth else RAN_OUT)


def _decide_mandatory(bound, deaths) -> Decision:
    first = min(deaths.values(), default=INF)
    cycles = min(bound, first)
    if cycles == INF:
        # CR 104.4b: nothing can break it and nothing ends it.
        return Decision(0, "draw", NOBODY)
    if cycles <= 0:
        return Decision(0, "ends", EMPTY)
    return Decision(_clamp(cycles), "shortcut", LOST if first <= bound else RAN_OUT)


def _growth_cycles(delta: dict) -> int | None:
    """How many cycles a resource-only loop is worth, or None if it grows nothing."""
    caps = [
        max(1, _GROWTH_CAPS[key[0]] // change)
        for key, change in delta.items()
        if change > 0 and key[0] in _GROWTH_CAPS
    ]
    return min(caps) if caps else None


def _clamp(cycles: float) -> int:
    return int(min(cycles, MAX_SHORTCUT))


# ---------------------------------------------------------------------------
# Recognising
# ---------------------------------------------------------------------------


def _consider(game: Game, cycle: tuple, snapshots: list) -> bool:
    """Whether the last three boundaries describe one steady loop, and if so act."""
    first, second, third = snapshots[-3:]
    if not first.shape == second.shape == third.shape:
        return False

    before = _delta(first.values, second.values)
    latest = _delta(second.values, third.values)
    if before != latest:
        return False

    earlier_effects = _cycle_effects(game, first.mark, second.mark)
    latest_effects = _cycle_effects(game, second.mark, third.mark)
    if _effect_signature(earlier_effects) != _effect_signature(latest_effects):
        return False

    return _resolve(game, cycle, third.values, latest, latest_effects)


def _resolve(game: Game, cycle: tuple, values: dict, delta: dict, effects: list) -> bool:
    optional = any(step[0] == "A" for step in cycle)
    controller = _controller(game, cycle)
    pumps = _pump_groups(effects)

    decision_delta = dict(delta)
    for index, (template, count, power, toughness) in enumerate(pumps):
        size = max(abs(power), abs(toughness)) * count
        if size:
            decision_delta[("pump", template.controller, index)] = size
    decision_delta = {key: change for key, change in decision_delta.items() if key[0] not in DERIVED}

    decision = decide(
        optional=optional,
        controller=controller,
        delta=decision_delta,
        values=values,
        players=_contestants(game),
    )
    description = describe(cycle)
    # Whatever happens, finding the *next* loop needs fresh cycles.
    game.loop_window = LoopWindow()
    actions = {step for step in cycle if step[0] == "A"}

    if decision.outcome == "draw":
        _draw(game, description)
        return True

    if decision.cycles <= 0:
        if not optional:
            return False  # It ends on its own at the next cycle.
        game.exhausted_loop_steps.update(actions)
        _note(game, decision.outcome, description, 0, decision.reason)
        return True

    victim = _aimed_at(cycle, controller, decision_delta)
    if victim is not None and decision.reason in (WINS, LOST):
        total = _run_reaimed(game, delta, pumps, victim, controller, optional)
        _note(game, "shortcut", description, total, f"{decision.reason}, one target at a time")
    else:
        _apply(game, delta, pumps, decision.cycles)
        _note(game, "shortcut", description, decision.cycles, decision.reason)
    if optional:
        if decision.reason == WINS:
            # Killing one opponent re-targets the loop at the next, and that is
            # a new cycle the detector has to see - so it may carry on.
            game.continuing_loop_steps.update(actions)
        else:
            game.exhausted_loop_steps.update(actions)
    return True


def _contestants(game: Game) -> list[Contestant]:
    return [
        Contestant(p.id, p.life, p.poison, len(p.library), p.cannot_lose)
        for p in game.living_players
    ]


#: What a loop can do *to* a player that would be done to another player just
#: the same if it were aimed at them instead.
_VICTIM_KEYS = frozenset({"life", "poison", "library", "hand", "graveyard", "exile"})


def _aimed_at(cycle: tuple, controller: int, delta: dict) -> int | None:
    """The one opponent a loop is pointed at, when pointing it is a choice.

    Sanguine Bond says "target opponent", and a target is chosen every time
    the trigger goes on the stack - so the opponent a measured cycle happened
    to hit is not a property of the loop, it is a choice its controller keeps
    making. Recognised only when that is unambiguous: every player target in
    the cycle is the same opponent, and nothing the cycle does to any
    opponent falls on anyone else.
    """
    aimed: set[int] = set()
    for step in cycle:
        aimed.update(_player_targets(step[-1]))
    if len(aimed) != 1:
        return None
    (victim,) = aimed
    if victim == controller:
        return None

    hit = False
    for key in delta:
        if len(key) < 2 or key[1] == controller:
            continue
        if key[1] != victim or key[0] not in _VICTIM_KEYS:
            return None
        hit = True
    return victim if hit else None


def _player_targets(targets) -> list[int]:
    found: list[int] = []
    for item in targets:
        if not isinstance(item, tuple):
            continue
        if len(item) == 2 and item[0] == "p":
            found.append(item[1])
        else:
            found.extend(_player_targets(item))
    return found


def _reaim(delta: dict, source: int, target: int) -> dict:
    if source == target:
        return dict(delta)
    out: dict = {}
    for key, change in delta.items():
        if key[0] in _VICTIM_KEYS and len(key) >= 2 and key[1] == source:
            key = (key[0], target, *key[2:])
        out[key] = out.get(key, 0) + change
    return out


def _run_reaimed(
    game: Game, delta: dict, pumps: list, victim: int, controller: int, optional: bool
) -> int:
    """A loop aimed at one opponent, run to lethal on each opponent in turn.

    The player running it drains the first opponent to exactly lethal and then
    points it at the next - forty pings each, not forty in total and not a
    thousand. Scaled against only the opponent it happened to be aimed at, it
    killed that one player and left its last trigger targeting someone who
    had gone, where it fizzled: a combo that should end the game killed a
    third of the table.

    Each opponent is decided afresh, because the controller's fuel and life
    after the first kill are what bound the second.
    """
    order = [victim] + [p.id for p in game.living_players if p.id not in (controller, victim)]
    total = 0
    for target in order:
        aimed = _reaim(delta, victim, target)
        decision = decide(
            optional=optional,
            controller=controller,
            delta={key: change for key, change in aimed.items() if key[0] not in DERIVED},
            values=_snapshot(game).values,
            players=_contestants(game),
        )
        if decision.cycles <= 0:
            break
        _apply(game, aimed, pumps, decision.cycles)
        total += decision.cycles
        if decision.reason not in (WINS, LOST) or game.player(controller).life <= 0:
            break
    return total


def _controller(game: Game, cycle: tuple) -> int:
    for step in cycle:
        if step[0] == "A":
            return step[1]
    for step in cycle:
        if step[0] == "R" and step[1] >= 0:
            return step[1]
    return int(game.active_player)


def describe(cycle: tuple) -> str:
    """A loop, in words a report can show."""
    parts: list[str] = []
    for step in cycle:
        if step[0] == "A":
            label = f"{step[2].lower().replace('_', ' ')} {step[3]}".strip()
        else:
            text = step[2] if len(step[2]) <= 60 else step[2][:57] + "..."
            label = f"resolve {text}".strip()
        if label not in parts:
            parts.append(label)
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Measuring
# ---------------------------------------------------------------------------


def _snapshot(game: Game) -> Snapshot:
    """What a cycle boundary looks like.

    ``shape`` is everything a real loop leaves exactly as it found it: the
    step, the stack and what is waiting to go on it, which non-token
    permanents are where and tapped, and who is alive. ``values`` is
    everything a loop is allowed to change, keyed so that the player a
    resource belongs to is always the second element.
    """
    values: dict = {}
    living: list[int] = []
    for player in game.players:
        if player.has_lost:
            continue
        pid = player.id
        living.append(pid)
        values[("life", pid)] = player.life
        values[("poison", pid)] = player.poison
        values[("energy", pid)] = player.energy
        values[("experience", pid)] = player.experience
        values[("library", pid)] = len(player.library)
        values[("hand", pid)] = len(player.hand)
        values[("graveyard", pid)] = len(player.graveyard)
        for kind, amount in player.mana_pool.buckets.items():
            if amount:
                values[("mana", pid, kind)] = amount

    for object_id in game.zone_list(Zone.EXILE):
        obj = game.objects.get(object_id)
        if obj is not None:
            key = ("exile", obj.owner)
            values[key] = values.get(key, 0) + 1

    board: list[tuple] = []
    for obj in game.permanents():
        name = _name(obj)
        if obj.is_token:
            key = ("tokens", obj.controller, name)
            values[key] = values.get(key, 0) + 1
        else:
            board.append((name, obj.controller, obj.tapped, obj.phased_out))
        for kind, amount in obj.counters.items():
            if amount:
                values[("counters", obj.controller, obj.id, kind)] = amount

    for key, amount in game.turn_history.items():
        values[("turn", *key)] = amount
    values[("spells_cast",)] = game.spells_cast_this_turn

    stack = tuple(_label(game.objects.get(object_id)) for object_id in game.stack)
    shape = (
        int(game.step),
        stack,
        len(game.pending_triggers),
        tuple(sorted(board)),
        tuple(living),
    )
    mark = max((ce.timestamp for ce in game.continuous_effects), default=0)
    return Snapshot(shape=shape, values=values, mark=mark)


def _delta(before: dict, after: dict) -> dict:
    """What changed between two boundaries, non-zero entries only.

    Counters are keyed by object, and a loop that recurs a permanent makes a
    new object every cycle - so a counter key present on one side only is an
    object that came or went, not a count that moved, and is left out.
    """
    out: dict = {}
    for key in before.keys() | after.keys():
        if key[0] == "counters" and (key not in before or key not in after):
            continue
        change = after.get(key, 0) - before.get(key, 0)
        if change:
            out[key] = change
    return out


def _cycle_effects(game: Game, low: int, high: int) -> list:
    """Continuous effects created by resolutions between two boundaries."""
    return [
        ce
        for ce in game.continuous_effects
        if low < ce.timestamp <= high and not ce.from_static_ability and not ce.expired
    ]


def _effect_signature(effects: list) -> tuple:
    """The effects a cycle made, comparable between cycles."""
    from ..cr600_spells_and_abilities.effects import EffectKind

    signature: dict = {}
    for ce in effects:
        effect = ce.effect
        targets = _specific(effect)
        if (
            effect.kind is EffectKind.MODIFY_PT
            and effect.amount.is_constant
            and effect.amount2.is_constant
        ):
            key = ("pt", targets, effect.amount.constant, effect.amount2.constant, ce.duration)
        else:
            key = ("other", int(effect.kind), targets)
        signature[key] = signature.get(key, 0) + 1
    return tuple(sorted(signature.items(), key=repr))


def _pump_groups(effects: list) -> list:
    """Constant power/toughness changes a cycle made, grouped for scaling.

    Returns ``(template, count, power, toughness)`` per distinct pump. Anything
    else a cycle does with continuous effects - granting an ability, setting a
    power - is the same after one application as after a thousand, so it
    needs no scaling.
    """
    from ..cr600_spells_and_abilities.effects import EffectKind

    groups: dict = {}
    for ce in effects:
        effect = ce.effect
        if effect.kind is not EffectKind.MODIFY_PT:
            continue
        if not (effect.amount.is_constant and effect.amount2.is_constant):
            continue
        key = (
            _specific(effect),
            effect.amount.constant,
            effect.amount2.constant,
            ce.duration,
            ce.controller,
            ce.source,
        )
        if key in groups:
            groups[key][1] += 1
        else:
            groups[key] = [ce, 1]
    return [
        (ce, count, ce.effect.amount.constant, ce.effect.amount2.constant)
        for ce, count in groups.values()
    ]


def _specific(effect) -> tuple:
    targets = effect.targets
    if targets is None or not targets.specific:
        return ()
    return tuple(sorted(targets.specific))


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def _apply(game: Game, delta: dict, pumps: list, cycles: int) -> None:
    """Scale one cycle's change by ``cycles``.

    Triggers are off while it happens: every trigger that fires once per
    cycle is already part of the cycle, so its consequences are already in
    ``delta``, and letting it fire again on the lump would count it twice. The
    per-turn tallies are put back afterwards and scaled from the cycle too, for
    the same reason - moving the cards emits zone changes that would otherwise
    be counted on top.
    """
    game.last_loop_turn = game.turn
    saved_history = dict(game.turn_history)
    game.suppress_triggers = True
    try:
        _apply_players(game, delta, cycles)
        _apply_zones(game, delta, cycles)
        _apply_counters(game, delta, cycles)
        _apply_tokens(game, delta, cycles)
        _apply_pumps(game, pumps, cycles)
    finally:
        game.suppress_triggers = False

    game.turn_history.clear()
    game.turn_history.update(saved_history)
    for key, change in delta.items():
        if key[0] == "turn":
            inner = key[1:]
            game.turn_history[inner] = game.turn_history.get(inner, 0) + change * cycles
        elif key[0] == "spells_cast":
            game.spells_cast_this_turn += change * cycles
    game.invalidate_characteristics()


def _apply_players(game: Game, delta: dict, cycles: int) -> None:
    for player in game.living_players:
        pid = player.id
        life = delta.get(("life", pid), 0) * cycles
        if life > 0:
            player.gain_life(life)
        elif life < 0:
            player.lose_life(-life)

        poison = delta.get(("poison", pid), 0) * cycles
        if poison > 0:
            player.poison += poison

        for name in ("energy", "experience"):
            change = delta.get((name, pid), 0) * cycles
            if change:
                setattr(player, name, max(0, getattr(player, name) + change))

        pool = player.mana_pool
        for key, change in delta.items():
            if key[0] != "mana" or key[1] != pid:
                continue
            amount = change * cycles
            if amount > 0:
                pool.add(key[2], min(amount, MANA_CAP))
            elif amount < 0:
                available = pool.buckets.get(key[2], 0)
                if available:
                    pool.remove(key[2], min(-amount, available))


def _apply_zones(game: Game, delta: dict, cycles: int) -> None:
    """Cards moved between one player's zones, as the cycle moved them.

    Matched as flows - whatever a zone lost goes to whatever gained - because a
    count is all a cycle's change records. A draw that runs past the end of the
    library is the one shortfall that matters: it is a loss (CR 704.5b), so the
    flag the state-based action reads is set.
    """
    for player in game.living_players:
        flows = {zone: delta.get((name, player.id), 0) * cycles for name, zone in _ZONE_KEYS}
        sources = [[zone, -amount] for zone, amount in flows.items() if amount < 0]
        for sink, wanted in [(zone, amount) for zone, amount in flows.items() if amount > 0]:
            for source in sources:
                if wanted <= 0:
                    break
                take = min(wanted, source[1])
                if take <= 0:
                    continue
                moved = _move_cards(game, player, source[0], sink, take)
                if source[0] is Zone.LIBRARY and sink is Zone.HAND and moved < take:
                    player.attempted_draw_from_empty_library = True
                source[1] -= take
                wanted -= take


def _move_cards(game: Game, player, source: Zone, sink: Zone, count: int) -> int:
    if source is Zone.EXILE:
        pool = [
            object_id
            for object_id in game.zone_list(Zone.EXILE)
            if (obj := game.objects.get(object_id)) is not None and obj.owner == player.id
        ]
    else:
        pool = list(player.zone(source))

    moved = 0
    for object_id in pool[:count]:
        obj = game.objects.get(object_id)
        if obj is None:
            continue
        game.move_object(obj, sink, to_player=player.id)
        moved += 1
        if source is Zone.LIBRARY and sink is Zone.HAND:
            player.cards_drawn_this_turn += 1
    return moved


def _apply_counters(game: Game, delta: dict, cycles: int) -> None:
    for key, change in delta.items():
        if key[0] != "counters":
            continue
        _, _controller, object_id, kind = key
        obj = game.objects.get(object_id)
        if obj is None or obj.zone is not Zone.BATTLEFIELD:
            continue
        amount = change * cycles
        if amount > 0:
            obj.add_counters(kind, min(amount, COUNTER_CAP))
        else:
            obj.counters[kind] = max(0, obj.counters.get(kind, 0) + amount)


def _apply_tokens(game: Game, delta: dict, cycles: int) -> None:
    """More of the tokens a cycle made, or fewer of the ones it spent.

    New tokens are cloned from one the cycle already made, so a token that is
    a *copy* of a permanent comes out as its underlying card rather than the
    copy. The cap keeps a thousand-token board from becoming a thousand-object
    board computation; the life those tokens drained is scaled in full either
    way.
    """
    for key, change in delta.items():
        if key[0] != "tokens":
            continue
        _, controller, name = key
        amount = change * cycles
        existing = sorted(
            (obj for obj in game.permanents(controller) if obj.is_token and _name(obj) == name),
            key=lambda obj: obj.id,
        )
        if amount > 0 and existing:
            template = existing[0]
            for _ in range(min(amount, TOKEN_CAP)):
                clone = game.create_object(
                    template.card, controller, Zone.BATTLEFIELD, kind=template.kind
                )
                clone.entered_battlefield_turn = game.turn
                clone.summoning_sick = True
        elif amount < 0:
            for obj in existing[:-amount]:
                game.move_object(obj, Zone.GRAVEYARD, to_player=obj.owner)


def _apply_pumps(game: Game, pumps: list, cycles: int) -> None:
    """One merged power/toughness effect per pump, rather than a thousand.

    A thousand separate +2/+2 effects is the same creature and a thousand
    entries every board computation walks.
    """
    from .query import Value

    for template, count, power, toughness in pumps:
        size = max(abs(power), abs(toughness))
        if not size:
            continue
        scale = min(count * cycles, max(1, PUMP_CAP // size))
        game.continuous_effects.append(
            replace(
                template,
                effect=replace(
                    template.effect,
                    amount=Value.of(power * scale),
                    amount2=Value.of(toughness * scale),
                ),
                timestamp=game.ids.timestamp(),
            )
        )


# ---------------------------------------------------------------------------
# Ending and recording
# ---------------------------------------------------------------------------


def _draw(game: Game, description: str) -> None:
    """CR 104.4b: a loop of mandatory actions that nothing can stop."""
    game.loop_draw = True
    game.loops.append(f"draw: {description}")
    game.log.record(
        game,
        f"Mandatory loop that nothing can stop ({description}); "
        "the game is a draw (CR 104.4b)",
        kind="loop",
    )
    game.game_over = True
    game.winners = ()


def _note(game: Game, outcome: str, description: str, cycles: int, reason: str) -> None:
    game.loops.append(f"{outcome}: {description}")
    game.log.record(
        game,
        f"Loop {outcome} (CR 732.2a): {description} - {cycles} more time"
        f"{'' if cycles == 1 else 's'}, {reason}",
        kind="loop",
    )


def _name(obj) -> str:
    return getattr(getattr(obj, "card", None), "name", "") or ""


def _label(obj) -> str:
    if obj is None:
        return ""
    ability = getattr(obj, "ability", None)
    if ability is not None:
        return getattr(ability, "text", "") or str(ability)
    return _name(obj)


def _targets(game: Game, targets) -> tuple:
    if not targets:
        return ()
    out = []
    for item in targets:
        if isinstance(item, (tuple, list)):
            out.append(tuple(_target(game, target) for target in item))
        else:
            out.append(_target(game, item))
    return tuple(out)


def _target(game: Game, target) -> tuple:
    if isinstance(target, int):
        if is_player_target(target):
            return ("p", int(target_player(target)))
        obj = game.objects.get(target)
        if obj is not None:
            return ("o", _name(obj) or _label(obj))
    return ("?",)
