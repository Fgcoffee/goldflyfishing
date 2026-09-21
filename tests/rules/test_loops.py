"""An infinite loop ends the game it is in, instead of the measurement.

Before ``rules.loops``, a mandatory loop - Sanguine Bond and Exquisite Blood -
resolved trigger after trigger until the priority loop's iteration cap threw the
game out as a runaway with no winner, and an optional one was cut off by the
bot's eight-activation cap before it could do anything. A deck whose whole plan
is an infinite scored as a deck that stalls.

Three groups of tests:

* real boards through the real priority loop - the combo that kills, and the
  bounded repeat (a board wipe under an aristocrat) that must *not* be mistaken
  for one and extended into a kill it never had;
* synthetic cycles fed straight to the detector, so each outcome - win, fuel
  runs out, declined, stopped, resource cap, draw - is pinned without needing a
  card that happens to parse into exactly that loop;
* the arithmetic of ``decide`` on its own.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules import actions
from mtgfish.rules.cr106_mana import ManaKind
from mtgfish.rules.cr117_priority import run_priority
from mtgfish.rules.enums import Color, Zone
from mtgfish.rules.gameobject import ObjectKind
from mtgfish.rules.loops import (
    DECLINED,
    LOOP_REPETITIONS,
    LOST,
    MANA_CAP,
    NOTHING,
    RAN_OUT,
    WINS,
    Contestant,
    begin_window,
    decide,
    note_step,
)
from mtgfish.ui.sandbox import STRICT_BENCH, PassiveOpponent, Sandbox

ENGINE = ("A", 0, "ACTIVATE_ABILITY", "Some Engine", 0, 0, ())
TRIGGER = ("R", 0, "Whenever something happens, nothing does.", ())


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    # The sandbox is the quickest way to a board, but every rule it normally
    # suspends is on here: these tests are about a loop that *kills*, and a
    # bench where nobody can lose would score every one of them as a stall.
    table = Sandbox(
        db=card_db, verdicts=VerdictStore(tmp_path / "v.json"), rules=STRICT_BENCH
    )
    # Both seats inert: a loop of triggers needs no decisions, and a synthetic
    # cycle is driven by the test rather than by a bot.
    table.game.agents[0] = PassiveOpponent()
    return table


def _need(box, *names):
    for name in names:
        if box.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")


def _spin(game, step, change, *, limit=60):
    """Feed one step per cycle, changing the game each time, until the detector acts.

    Returns how many cycles really ran, or None if nothing was ever done.
    """
    begin_window(game)
    for cycle in range(1, limit + 1):
        change(game)
        if note_step(game, step):
            return cycle
    return None


# ---------------------------------------------------------------------------
# Real boards
# ---------------------------------------------------------------------------


def test_sanguine_bond_and_exquisite_blood_end_the_game(box):
    """The canonical mandatory infinite: it kills, it does not hit a cap.

    The life totals are exact on purpose. One life gained starts it, and each
    cycle drains one and gains one, so the opponent dies on the fortieth drain
    and the controller finishes at 40 + 1 + 40. A shortcut that fired the
    cycle's triggers on its own lump sum would overshoot; one that miscounted
    cycles would stop short and leave the game running.
    """
    _need(box, "Sanguine Bond", "Exquisite Blood")
    game = box.game
    box.put("Sanguine Bond", "battlefield", 0)
    box.put("Exquisite Blood", "battlefield", 0)

    actions.gain_life(game, 0, 1)
    run_priority(game)

    assert game.game_over
    assert game.winners == (0,)
    assert game.player(1).has_lost
    assert not game.runaway, "the loop was left to run into the iteration cap"
    assert game.player(0).life == 81
    assert len(game.loops) == 1 and game.loops[0].startswith("shortcut")


def test_a_loop_kill_set_off_by_combat_is_not_a_combat_win(box):
    """Exquisite Blood sees combat damage, so the kill lands on a combat turn.

    Reported as COMBAT_DAMAGE, every combo kill in a simulation hid in the
    beatdown column of the win-reason chart.
    """
    _need(box, "Sanguine Bond", "Exquisite Blood")
    from mtgfish.sim.observer import Observer, finish, new_record
    from mtgfish.sim.records import WinReason

    game = box.game
    record = new_record(0, 1, len(game.players))
    observer = Observer(record, 0)
    game.observer = observer
    # Combat damage was dealt this turn - it is what started the loop.
    observer._last_combat_damage_turn = game.turn

    box.put("Sanguine Bond", "battlefield", 0)
    box.put("Exquisite Blood", "battlefield", 0)
    actions.gain_life(game, 0, 1)
    run_priority(game)

    finish(game, record)
    assert record.winner == 0
    assert record.win_reason is WinReason.NONCOMBAT_DAMAGE


def test_a_board_wipe_under_an_aristocrat_is_not_a_loop(box):
    """The misfire this module is most careful about.

    Thirty tokens dying at once put thirty identical triggers on the stack, and
    they resolve as thirty identical steps with identical consequences - far
    more than the eight it takes to call something a loop. Extended, it would
    kill an opponent at 35 who should end on 5. The stack shrinking every cycle
    is what says it is not one.
    """
    _need(box, "Zulaport Cutthroat", "Grizzly Bears")
    game = box.game
    game.player(1).life = 35
    box.put("Zulaport Cutthroat", "battlefield", 0)
    bears = box.db.lookup("Grizzly Bears")
    tokens = [game.create_object(bears, 0, Zone.BATTLEFIELD, kind=ObjectKind.TOKEN) for _ in range(30)]
    game.invalidate_characteristics()

    for token in tokens:
        actions.destroy(game, token)
    run_priority(game)

    assert game.loops == []
    assert game.player(1).life == 5
    assert game.player(0).life == 70
    assert not game.game_over


def test_the_bot_cap_does_not_beat_the_detector():
    """A cap of eight activations stopped every combo exactly one cycle short."""
    from mtgfish.ai.simple import SimpleAgent

    assert SimpleAgent.MAX_ACTIVATIONS_PER_TURN > LOOP_REPETITIONS


# ---------------------------------------------------------------------------
# Synthetic cycles
# ---------------------------------------------------------------------------


def test_an_optional_drain_loop_is_run_to_the_kill(box):
    game = box.game

    def drain(g):
        g.player(1).lose_life(2)

    ran = _spin(game, ENGINE, drain)
    assert ran is not None and ran >= LOOP_REPETITIONS

    run_priority(game)  # State-based actions see the result.
    assert game.player(1).has_lost
    assert game.player(0).life == 40
    assert game.loops[0].startswith("shortcut")
    # Killing a player re-aims the loop, so it is allowed to carry on.
    assert ENGINE in game.continuing_loop_steps


def test_a_pinger_does_exactly_forty_per_player_then_moves_on(box):
    """Infinite one-damage pings in a four-player pod.

    The loop needs forty per opponent and no more: each opponent must end on
    exactly zero, not minus a thousand. The ping is aimed at one opponent, and
    aiming it is a choice made again every activation - so the shortcut drains
    that opponent to lethal and re-aims at the next, finishing the table.
    """
    from mtgfish.rules.cr117_priority import settle
    from mtgfish.rules.player import Player

    game = box.game
    for index in (2, 3):
        game.players.append(Player(id=index, name=f"Opponent {index}"))
        game.players[index].life = 40
        game.turn_order.append(index)

    begin_window(game)
    for _ in range(400):
        living = [p for p in game.living_players if p.id != 0]
        if not living or game.game_over:
            break
        target = living[0]
        step = ("A", 0, "ACTIVATE_ABILITY", "Pinger", 0, 0, (("p", target.id),))
        target.lose_life(1)
        note_step(game, step)
        settle(game)

    assert game.game_over
    assert game.winners == (0,)
    assert [p.life for p in game.players[1:]] == [0, 0, 0], "overshot the kill"
    assert any(entry.startswith("shortcut") for entry in game.loops)


def test_sanguine_bond_finishes_a_four_player_table(box):
    """"Target opponent" is re-aimed until the table is empty.

    Aimed only at the opponent the measured cycle happened to hit, the loop
    killed that one player and its next trigger fizzled on a player who had
    gone - one kill out of three from a combo that wins the game. One life
    gained, then forty drains for each of three opponents: 40 + 1 + 120.
    """
    _need(box, "Sanguine Bond", "Exquisite Blood")
    from mtgfish.rules.player import Player

    game = box.game
    for index in (2, 3):
        game.players.append(Player(id=index, name=f"Opponent {index}"))
        game.players[index].life = 40
        game.turn_order.append(index)
        game.agents[index] = PassiveOpponent()
    box.put("Sanguine Bond", "battlefield", 0)
    box.put("Exquisite Blood", "battlefield", 0)

    actions.gain_life(game, 0, 1)
    run_priority(game)

    assert game.game_over
    assert game.winners == (0,)
    assert all(game.player(index).has_lost for index in (1, 2, 3))
    assert not game.runaway
    assert game.player(0).life == 161


def test_a_loop_that_was_answered_can_carry_on(box):
    """The kill is shortcut; the opponent responds and survives; the loop goes again."""
    game = box.game

    def ping(g):
        g.player(1).lose_life(1)

    assert _spin(game, ENGINE, ping) is not None
    assert game.player(1).life == 0

    # A response before state-based actions: they gain ten.
    game.player(1).life = 10
    assert ENGINE not in game.exhausted_loop_steps

    assert _spin(game, ENGINE, ping) is not None
    assert game.player(1).life == 0
    run_priority(game)
    assert game.player(1).has_lost


def test_an_optional_loop_stops_where_its_fuel_does(box):
    """Each cycle mills one of the controller's own cards to drain one life.

    Twenty cards pay for twenty drains, and forty are needed. The loop runs
    exactly as far as twenty and no further - not to the kill, and not into a
    library that is not there.
    """
    game = box.game
    for _ in range(20):
        box.put("Forest", "library", 0)

    def mill_to_drain(g):
        me = g.player(0)
        g.move_object(g.objects[me.library[-1]], Zone.GRAVEYARD, to_player=0)
        g.player(1).lose_life(1)

    assert _spin(game, ENGINE, mill_to_drain) is not None

    assert len(game.player(0).library) == 0
    assert len(game.player(0).graveyard) == 20
    assert game.player(1).life == 20
    assert not game.player(1).has_lost
    assert RAN_OUT in game.log.entries[-1].text or game.loops[0].startswith("shortcut")
    assert ENGINE in game.exhausted_loop_steps


def test_an_optional_loop_that_would_kill_its_controller_first_is_declined(box):
    game = box.game
    game.player(0).life = 20

    def trade_life(g):
        g.player(0).lose_life(1)
        g.player(1).lose_life(1)

    assert _spin(game, ENGINE, trade_life) is not None

    assert game.loops[0].startswith("declined")
    assert game.player(0).life > 0
    assert not game.game_over
    assert ENGINE in game.exhausted_loop_steps


def test_an_optional_loop_that_changes_nothing_is_stopped(box):
    """Far more often a card read wrong than a real combo, so it is recorded."""
    game = box.game
    assert _spin(game, ENGINE, lambda g: None) is not None

    assert game.loops[0].startswith("stopped")
    assert [p.life for p in game.players] == [40, 40]
    assert ENGINE in game.exhausted_loop_steps


def test_a_mana_loop_is_run_to_a_generous_cap(box):
    game = box.game
    green = ManaKind(Color.GREEN)

    def add_mana(g):
        g.player(0).mana_pool.add(green, 1)

    assert _spin(game, ENGINE, add_mana) is not None

    assert game.player(0).mana_pool.total >= MANA_CAP
    assert game.loops[0].startswith("shortcut")
    assert not game.game_over


def test_a_mandatory_loop_nothing_ends_is_a_draw(box):
    """CR 104.4b - and recorded as a loop draw, not a stall-out."""
    from mtgfish.sim.observer import finish, new_record
    from mtgfish.sim.records import WinReason

    game = box.game
    assert _spin(game, TRIGGER, lambda g: None) is not None

    assert game.game_over
    assert game.winners == ()
    assert game.loop_draw

    record = finish(game, new_record(0, 1, len(game.players)))
    assert record.loop_draw
    assert record.loops and record.loops[0].startswith("draw")
    assert WinReason.LOOP_DRAW.name == "LOOP_DRAW"


def test_a_repeat_that_stops_short_of_eight_cycles_is_left_alone(box):
    game = box.game

    def drain(g):
        g.player(1).lose_life(1)

    assert _spin(game, ENGINE, drain, limit=LOOP_REPETITIONS - 1) is None
    assert game.player(1).life == 40 - (LOOP_REPETITIONS - 1)
    assert game.loops == []


def test_an_uneven_cycle_is_not_a_loop(box):
    """Same steps, different consequences each time - a ramping effect, not a loop."""
    game = box.game
    amounts = iter(range(1, 100))

    def ramp(g):
        g.player(1).lose_life(next(amounts) % 3)

    assert _spin(game, ENGINE, ramp, limit=30) is None
    assert game.loops == []


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------


def _players(*lives, **extra):
    return [Contestant(index, life, **extra) for index, life in enumerate(lives)]


def test_a_mandatory_loop_runs_until_the_first_loss():
    decision = decide(
        optional=False,
        controller=0,
        delta={("life", 1): -2},
        values={},
        players=_players(40, 10),
    )
    assert (decision.cycles, decision.reason) == (5, LOST)


def test_an_optional_loop_kills_every_opponent_it_can_reach():
    decision = decide(
        optional=True,
        controller=0,
        delta={("life", 1): -1, ("life", 2): -1, ("life", 3): -1},
        values={},
        players=_players(40, 10, 30, 25),
    )
    assert (decision.cycles, decision.reason) == (30, WINS)


def test_poison_counts_to_ten():
    decision = decide(
        optional=True,
        controller=0,
        delta={("poison", 1): 1},
        values={},
        players=[Contestant(0, 40), Contestant(1, 40, poison=3)],
    )
    assert (decision.cycles, decision.reason) == (7, WINS)


def test_a_draw_loop_leaves_its_controller_a_card():
    """Nobody chooses to lose to their own combo by drawing from an empty library."""
    decision = decide(
        optional=True,
        controller=0,
        delta={("library", 0): -1, ("hand", 0): 1, ("life", 1): -1},
        values={("library", 0): 10},
        players=[Contestant(0, 40, library=10), Contestant(1, 40)],
    )
    assert decision.cycles == 9


def test_an_opponent_who_cannot_lose_is_not_a_goal():
    decision = decide(
        optional=True,
        controller=0,
        delta={("life", 1): -1},
        values={},
        players=[Contestant(0, 40), Contestant(1, 5, cannot_lose=True)],
    )
    assert decision.outcome == "stopped"
    assert decision.reason == NOTHING


def test_declining_is_a_decision_not_an_error():
    decision = decide(
        optional=True,
        controller=0,
        delta={("life", 0): -1, ("life", 1): -1},
        values={},
        players=_players(20, 40),
    )
    assert (decision.cycles, decision.outcome, decision.reason) == (0, "declined", DECLINED)
