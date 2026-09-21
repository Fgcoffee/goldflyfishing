"""The shape of a turn (CR 500-513).

Three things the turn loop was getting wrong, all of them about the phases and
steps an effect adds rather than the ones every turn has.

An extra main phase was run as a *precombat* main phase, so the turn-based
actions CR 505.4 and CR 728.1 reserve for the precombat main happened twice in
a turn. And extra turns, phases and steps were all taken in the order they were
created, where CR 500.7, CR 500.8 and CR 500.9 each say the most recently
created one goes first.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr117_priority import PASS
from mtgfish.rules.cr500_turn_structure.cr500_turn import (
    TURN_SEQUENCE,
    TurnOptions,
    _next_active_player,
    _run_extras,
    _TurnProgress,
    take_turn,
)
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, TriggerCondition
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import Phase, Step
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.ids import PlayerId


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def _steps_of(game) -> list[tuple[Phase, Step]]:
    """Record every step that begins, in order, as it begins."""
    seen: list[tuple[Phase, Step]] = []

    def watch(g, event):
        if event.kind is EventKind.STEP_BEGAN:
            seen.append((g.phase, g.step))

    game.observer = watch
    return seen


def _pass_with_everything(board) -> None:
    for player in board.game.players:
        board.game.agents[player.id] = FixedAgent()


# ---------------------------------------------------------------------------
# The steps a turn has (CR 500.1, 501.1, 505.1, 506.1, 512.1)
# ---------------------------------------------------------------------------


def test_the_turn_sequence_is_the_one_the_rules_describe():
    """CR 501.1, CR 505.1, CR 506.1, CR 512.1, in one place."""
    assert TURN_SEQUENCE == (
        (Phase.BEGINNING, Step.UNTAP),
        (Phase.BEGINNING, Step.UPKEEP),
        (Phase.BEGINNING, Step.DRAW),
        (Phase.PRECOMBAT_MAIN, Step.MAIN),
        (Phase.COMBAT, Step.BEGINNING_OF_COMBAT),
        (Phase.COMBAT, Step.DECLARE_ATTACKERS),
        (Phase.COMBAT, Step.DECLARE_BLOCKERS),
        (Phase.COMBAT, Step.COMBAT_DAMAGE),
        (Phase.COMBAT, Step.END_OF_COMBAT),
        (Phase.POSTCOMBAT_MAIN, Step.MAIN),
        (Phase.ENDING, Step.END_STEP),
        (Phase.ENDING, Step.CLEANUP),
    )


def test_an_ordinary_turn_has_one_precombat_main_phase(board):
    """CR 505.1a: the first main phase, and only that one."""
    seen = _steps_of(board.game)
    _pass_with_everything(board)
    take_turn(board.game)

    mains = [phase for phase, step in seen if step is Step.MAIN]
    assert mains == [Phase.PRECOMBAT_MAIN, Phase.POSTCOMBAT_MAIN]


# ---------------------------------------------------------------------------
# CR 505.1a: every main phase after the first is a postcombat main phase
# ---------------------------------------------------------------------------


class _AddsAnExtraMainPhase:
    """Asks for one additional main phase, during the postcombat main.

    Which is how every "additional main phase" effect asks for it: the
    resolution appends ``Phase.PRECOMBAT_MAIN`` to ``extra_phases``.
    """

    def __init__(self) -> None:
        self.asked = False

    def choose_action(self, game, player, legal):
        if game.phase is Phase.POSTCOMBAT_MAIN and not self.asked:
            self.asked = True
            game.extra_phases.append(Phase.PRECOMBAT_MAIN)
        return PASS

    def choose_optional(self, game, player, effect):
        return True

    def choose_discard(self, game, player):
        hand = game.player(player).hand
        return hand[-1] if hand else 0

    def order_triggers(self, game, player, triggers):
        return triggers

    def declare_attackers(self, game, player, candidates):
        return {}

    def declare_blockers(self, game, player, combat, available):
        return {}


def test_an_extra_main_phase_is_a_postcombat_main_phase(board):
    """CR 505.1a: only the turn's first main phase is the precombat one."""
    seen = _steps_of(board.game)
    _pass_with_everything(board)
    board.game.agents[0] = _AddsAnExtraMainPhase()
    board.game.active_player = PlayerId(0)

    take_turn(board.game)

    mains = [phase for phase, step in seen if step is Step.MAIN]
    assert len(mains) == 3, mains
    assert mains == [
        Phase.PRECOMBAT_MAIN,
        Phase.POSTCOMBAT_MAIN,
        Phase.POSTCOMBAT_MAIN,
    ]


def test_a_saga_gets_one_lore_counter_however_many_main_phases_there_are(board):
    """CR 505.4 with CR 505.1a: the lore counter is a turn-based action of the
    precombat main phase, and a turn has exactly one of those.

    The reason CR 505.1a is worth implementing rather than noting: an extra
    main phase ticked every Saga on the board a second time.
    """
    from mtgfish.rules.cr300_card_types.cr300_card_types import chapter_ability

    board.scripts.add(
        "History of Benalia",
        chapter_ability(1, Effect(EffectKind.NOTHING, text="chapter I")),
        chapter_ability(2, Effect(EffectKind.NOTHING, text="chapter II")),
        chapter_ability(3, Effect(EffectKind.NOTHING, text="chapter III")),
    )
    saga = board.play("History of Benalia", controller=0)
    saga.counters.clear()

    _pass_with_everything(board)
    board.game.agents[0] = _AddsAnExtraMainPhase()
    board.game.active_player = PlayerId(0)

    take_turn(board.game)

    assert saga.counters.get("lore", 0) == 1


# ---------------------------------------------------------------------------
# CR 500.7: the most recently created extra turn is taken first
# ---------------------------------------------------------------------------


def test_the_most_recently_created_extra_turn_is_taken_first(board):
    """CR 500.7. Created for player 1 first, then for player 0."""
    game = board.game
    game.active_player = PlayerId(0)
    game.extra_turns.extend([PlayerId(1), PlayerId(0)])

    assert _next_active_player(game) == PlayerId(0)
    assert _next_active_player(game) == PlayerId(1)


def test_with_no_extra_turns_the_turn_passes_around_the_table(board):
    game = board.game
    game.active_player = PlayerId(0)
    assert _next_active_player(game) == PlayerId(1)


# ---------------------------------------------------------------------------
# CR 500.8, CR 500.9: the most recently created extra phase or step goes first
# ---------------------------------------------------------------------------


def test_the_most_recently_created_extra_phase_happens_first(board):
    """CR 500.8. An "additional combat phase followed by an additional main
    phase" is built by creating the main phase first and the combat phase
    second; taken in creation order, the card's own sequence came out
    backwards."""
    game = board.game
    seen = _steps_of(game)
    _pass_with_everything(board)
    game.active_player = PlayerId(0)
    game.phase = Phase.POSTCOMBAT_MAIN
    game.step = Step.MAIN
    game.extra_phases.extend([Phase.PRECOMBAT_MAIN, Phase.COMBAT])

    _run_extras(game, TurnOptions(), _TurnProgress(main_phases=2))

    order = [step for _, step in seen]
    assert order.index(Step.BEGINNING_OF_COMBAT) < order.index(Step.MAIN)


def test_the_most_recently_created_extra_step_happens_first(board):
    """CR 500.9, the same rule one level down."""
    game = board.game
    seen = _steps_of(game)
    _pass_with_everything(board)
    game.active_player = PlayerId(0)
    game.phase = Phase.BEGINNING
    game.step = Step.UPKEEP
    game.extra_steps.extend([Step.DRAW, Step.UPKEEP])

    _run_extras(game, TurnOptions(), _TurnProgress())

    assert [step for _, step in seen] == [Step.UPKEEP, Step.DRAW]


def test_an_extra_main_step_is_still_counted_as_a_main_phase(board):
    """CR 505.1b counts the main phases that have occurred this turn, whether
    the turn sequence produced them or an effect did."""
    game = board.game
    seen = _steps_of(game)
    _pass_with_everything(board)
    game.active_player = PlayerId(0)
    game.extra_steps.append(Step.MAIN)

    _run_extras(game, TurnOptions(), _TurnProgress(main_phases=1))

    assert seen == [(Phase.POSTCOMBAT_MAIN, Step.MAIN)]


# ---------------------------------------------------------------------------
# CR 513.2: the end step does not back up
# ---------------------------------------------------------------------------


def _end_step_probe() -> Ability:
    return Ability.triggered(
        TriggerCondition(
            event_kinds=frozenset({EventKind.END_STEP}),
            text="at the beginning of the end step",
        ),
        Effect(EffectKind.NOTHING, text="end step probe"),
        text="end step probe",
    )


def _resolutions_of(game) -> list[str]:
    resolved: list[str] = []

    def watch(g, event):
        if event.kind is EventKind.ABILITY_RESOLVED:
            obj = g.objects.get(event.object_id)
            if obj is not None and obj.ability is not None:
                resolved.append(obj.ability.text)

    game.observer = watch
    return resolved


class _PlaysACreatureInTheEndStep(FixedAgent):
    def __init__(self, board, name: str) -> None:
        super().__init__()
        self.board = board
        self.name = name
        self.done = False

    def choose_action(self, game, player, legal):
        if game.step is Step.END_STEP and not self.done:
            self.done = True
            self.board.play(self.name, controller=0)
        return PASS


def test_an_end_step_trigger_already_on_the_battlefield_fires(board):
    """The probe works: CR 513.1a, the ordinary case."""
    board.scripts.add("Grizzly Bears", _end_step_probe())
    board.play("Grizzly Bears", controller=0)
    resolved = _resolutions_of(board.game)
    _pass_with_everything(board)

    take_turn(board.game)
    assert "end step probe" in resolved


def test_a_permanent_arriving_during_the_end_step_does_not_trigger(board):
    """CR 513.2: the step does not back up for it. It waits for the next
    turn's end step."""
    board.scripts.add("Grizzly Bears", _end_step_probe())
    resolved = _resolutions_of(board.game)
    _pass_with_everything(board)
    board.game.agents[0] = _PlaysACreatureInTheEndStep(board, "Grizzly Bears")
    board.game.active_player = PlayerId(0)

    take_turn(board.game)

    assert "end step probe" not in resolved
    assert board.alive(0) == ["Grizzly Bears"], "it did arrive"
