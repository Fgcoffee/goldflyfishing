"""Priority (CR 117), and the agent interface that decides what to do with it.

The priority loop is the heartbeat of the game. Its shape is exact:

1. A player *would* receive priority. Before they do, state-based actions are
   performed repeatedly until none apply, and then any waiting triggered
   abilities are put on the stack (CR 117.5, 704.3, 603.3b). If either did
   anything, that repeats.
2. The player takes an action, or passes.
3. Taking an action returns priority to that same player (CR 117.3c).
4. When every player passes in succession, the top of the stack resolves - or,
   if the stack is empty, the step or phase ends (CR 117.4).

The "in succession" part is what makes counterspell wars work, and why passing
does not simply advance a counter: any action by anyone resets the chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol

from .cr603_triggers import put_triggers_on_stack
from .cr608_stack import resolve_top
from .cr704_sba import check_state_based_actions
from .enums import NO_PRIORITY_STEPS
from .events import Event, EventKind
from .ids import ObjectId, PlayerId

if TYPE_CHECKING:
    from .game import Game


class ActionKind(IntEnum):
    PASS = 0
    CAST_SPELL = 1
    ACTIVATE_ABILITY = 2
    PLAY_LAND = 3
    ACTIVATE_MANA_ABILITY = 4
    #: Special actions (CR 116) do not use the stack: turning a face-down
    #: creature face up, suspending, and so on.
    SPECIAL = 5


@dataclass(frozen=True, slots=True)
class Action:
    """Something a player with priority can do."""

    kind: ActionKind
    source: ObjectId = 0
    ability_index: int = -1
    targets: tuple = ()
    x_value: int = 0
    #: Which face of a split or modal double-faced card is being played.
    face_index: int = 0
    mode_choices: tuple[int, ...] = ()
    #: CR 118.9a: at most one alternative cost, chosen at CR 601.2b. An index
    #: into the object's ``alternative_costs``; -1 for none.
    alternative_cost: int = -1
    #: CR 118.8a: which optional additional costs are being paid, by index.
    additional_costs: tuple[int, ...] = ()

    def __str__(self) -> str:
        if self.kind is ActionKind.PASS:
            return "pass"
        return f"{self.kind.name.lower()}({self.source})"


PASS = Action(ActionKind.PASS)


class Agent(Protocol):
    """Whoever is deciding for a player.

    The rules engine calls these; it never inspects a bot's internals. Keeping
    the surface this narrow is what lets the same engine run a dumb bot, a
    trained pilot, or a scripted test without changing.
    """

    def choose_action(self, game: Game, player: PlayerId, legal: list[Action]) -> Action: ...

    def choose_optional(self, game: Game, player: PlayerId, effect) -> bool: ...

    def choose_discard(self, game: Game, player: PlayerId) -> ObjectId: ...

    def order_triggers(self, game: Game, player: PlayerId, triggers: list) -> list: ...

    def choose_targets(
        self, game: Game, player: PlayerId, source: ObjectId, candidates: list
    ) -> tuple: ...


class PassingAgent:
    """Passes on everything and takes every optional effect.

    The engine has to be runnable before any AI exists, and a game where nobody
    does anything is still a game whose turn structure, state-based actions,
    and trigger handling can be tested end to end.
    """

    def choose_action(self, game: Game, player: PlayerId, legal: list[Action]) -> Action:
        return PASS

    def choose_optional(self, game: Game, player: PlayerId, effect) -> bool:
        return True

    def choose_discard(self, game: Game, player: PlayerId) -> ObjectId:
        hand = game.player(player).hand
        return hand[-1] if hand else 0

    def order_triggers(self, game: Game, player: PlayerId, triggers: list) -> list:
        return triggers

    def choose_targets(
        self, game: Game, player: PlayerId, source: ObjectId, candidates: list
    ) -> tuple:
        # CR 601.2c: a target must be chosen if a legal one exists. Taking the
        # first is arbitrary but deterministic, which is what a replay needs;
        # a real agent overrides this.
        return tuple(
            (group[0],) if group else () for group in candidates
        )


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

#: Guard against a loop of effects that never lets the step end.
MAX_PRIORITY_ROUNDS = 2000

#: How many actions one game may take before it is treated as runaway. A real
#: four-player game to the 25-round cap takes a few thousand; this is well
#: above the worst honest game measured, so only something pathological -
#: a free ability, a loop with no cost - can reach it. See rules.turn.
ACTION_BUDGET = 20_000


def settle(game: Game) -> None:
    """Perform state-based actions and put triggers on the stack (CR 117.5).

    Repeats until neither does anything, because each can cause the other: a
    creature dying is a state-based action that triggers a dies-ability, and
    that ability going on the stack does not itself need another check, but the
    death of a second creature might.
    """
    from .cr603_triggers import check_state_triggers

    for _ in range(MAX_PRIORITY_ROUNDS):
        did_sba = check_state_based_actions(game)
        if game.game_over:
            return
        # CR 603.8: state triggers are checked alongside state-based actions,
        # because both are asking "is the game in a particular shape?".
        check_state_triggers(game)
        put_any = put_triggers_on_stack(game) > 0
        if not did_sba and not put_any:
            return


def give_priority(game: Game, player_id: PlayerId) -> None:
    game.priority_player = player_id
    game.emit(Event(EventKind.PRIORITY_RECEIVED, player=player_id))


def run_priority(game: Game) -> None:
    """Run the priority loop until the current step or phase should end.

    Returns when every player has passed in succession on an empty stack.
    """
    if game.step in NO_PRIORITY_STEPS and not game.pending_triggers:
        # CR 502.4 and 514.3: no priority in the untap or cleanup step - unless
        # something triggered or a state-based action happened, in which case
        # players do get priority and the step repeats.
        settle(game)
        if not game.stack and not game.pending_triggers:
            return

    order = game.apnap_order()
    if not order:
        return

    active = game.active_player
    current = active if active in order else order[0]
    passed_in_succession = 0

    # Loops are looked for one priority window at a time: an infinite happens
    # inside a step, and a window that carried history across steps would see
    # "declare attackers" and "declare attackers" a turn apart as a cycle.
    from .loops import action_step, begin_window, note_step, resolution_step

    begin_window(game)

    for _ in range(MAX_PRIORITY_ROUNDS):
        if game.game_over:
            return

        settle(game)
        if game.game_over:
            return

        order = game.apnap_order()
        if not order:
            return
        if current not in order:
            current = order[0]

        give_priority(game, current)
        action = _decide(game, current)

        # Read before performing: a sacrifice cost moves the source, and then
        # the step would not know what card it was.
        step = action_step(game, current, action) if action.kind is not ActionKind.PASS else None

        # An action that could not be completed was rewound and did not happen,
        # so it counts as a pass. Without this the loop would offer the same
        # impossible play back to the same player for ever.
        acted = action.kind is not ActionKind.PASS and _perform(game, current, action)

        if not acted and action.kind is not ActionKind.PASS:
            # Tell the agent, if it wants to know. Legality is an upper bound,
            # so an offered action can fail; an agent that is never told picks
            # it again at every priority, and each attempt costs a rewound
            # cast. Optional, like every other agent hook.
            notify = getattr(game.agent_for(current), "action_failed", None)
            if notify is not None:
                notify(game, current, action)

        if not acted:
            passed_in_succession += 1
            if passed_in_succession >= len(order):
                # CR 117.4: everyone passed in succession.
                if game.stack:
                    resolving = resolution_step(game, game.objects.get(game.stack[-1]))
                    resolve_top(game)
                    passed_in_succession = 0
                    # CR 117.3b: the active player receives priority after a
                    # spell or ability resolves.
                    current = game.active_player if game.active_player in order else order[0]
                    # A mandatory loop is made only of resolutions, so this is
                    # where one is noticed - and where it is ended, rather than
                    # being left to run into the iteration cap below.
                    if note_step(game, resolving) and game.game_over:
                        return
                    continue
                return
            current = _next_in_order(order, current)
            continue

        # CR 117.3c: taking an action gives that player priority again.
        passed_in_succession = 0

        # CR 732.2a: the player running an optional loop may skip to its
        # result. See rules.loops.
        if step is not None and note_step(game, step) and game.game_over:
            return

        # The runaway guard. Counted here because this is the only place an
        # action is actually taken, so anything that loops has to come
        # through it. See rules.turn.ACTION_BUDGET.
        game.actions_taken += 1
        if game.actions_taken > ACTION_BUDGET:
            obj = game.objects.get(getattr(action, "source", 0))
            name = getattr(getattr(obj, "card", None), "name", "") or "?"
            game.runaway = f"{action.kind.name} {name}"
            game.log.record(
                game,
                f"Action budget spent; stopping. Last action: {game.runaway}",
                kind="runaway",
            )
            game.game_over = True
            return

    # Two thousand rounds of priority in one step is not a game, it is a loop
    # that never needed an action - a spell whose copies copy it again, each
    # resolving and re-triggering. Merely leaving the step handed the same
    # stack to the next step, and the next, so one game ground on for minutes
    # while the action budget, which counts actions, never saw it. Stopped and
    # reported like any other runaway, naming what was on top of the stack.
    top = game.objects.get(game.stack[-1]) if game.stack else None
    name = getattr(getattr(top, "card", None), "name", "") or ("?" if top else "")
    game.runaway = f"RESOLVE {name}" if name else "PRIORITY LOOP"
    game.log.record(
        game,
        f"Priority loop hit its iteration cap; stopping. Top of stack: {name or 'empty'}",
        kind="runaway",
    )
    game.game_over = True


def _next_in_order(order: list[PlayerId], current: PlayerId) -> PlayerId:
    try:
        index = order.index(current)
    except ValueError:
        return order[0]
    return order[(index + 1) % len(order)]


def _decide(game: Game, player_id: PlayerId) -> Action:
    agent = game.agent_for(player_id)
    if agent is None:
        return PASS
    from .legality import legal_actions

    legal = legal_actions(game, player_id)
    if game.exhausted_loop_steps:
        # A loop shortcut this turn has already had its result applied; doing
        # it again would be the loop the shortcut replaced.
        from .loops import is_exhausted

        legal = [action for action in legal if not is_exhausted(game, player_id, action)]
    if not legal:
        return PASS
    choice = agent.choose_action(game, player_id, legal)
    return choice if choice is not None else PASS


def _perform(game: Game, player_id: PlayerId, action: Action) -> bool:
    """Carry out a chosen action.

    A ``CastError`` means the attempt could not be completed and CR 601.2h has
    already rewound it, so the game state is exactly as it was. That is not a
    crash - legality checks are deliberately an upper bound (they count
    available mana rather than solving the tap plan), so an occasional
    unpayable cast is expected and simply does not happen.
    """
    from .cr601_casting import CastError, activate_ability, cast_spell, play_land

    try:
        if action.kind is ActionKind.CAST_SPELL:
            cast_spell(game, player_id, action)
        elif action.kind in (ActionKind.ACTIVATE_ABILITY, ActionKind.ACTIVATE_MANA_ABILITY):
            activate_ability(game, player_id, action)
        elif action.kind is ActionKind.PLAY_LAND:
            play_land(game, player_id, action)
        elif action.kind is ActionKind.SPECIAL:
            # CR 116.1: no stack, no response window. Whatever it does has
            # already happened by the time the next player gets priority.
            from .cr116_special_actions import perform as perform_special

            return perform_special(game, player_id, action)
    except CastError as exc:
        game.log.record(game, f"Action abandoned: {exc}", kind="illegal", player=player_id)
        return False
    return True
