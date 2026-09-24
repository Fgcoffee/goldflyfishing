"""How a replay is presented, which is not the same as how a game is played.

Two complaints this answers, and both were about a replay that was technically
complete and practically unreadable.

**Turn numbers were the engine's.** ``Game.turn`` counts every player's turn, so
a four-player game's fourth round is turn thirteen. The viewer showed that
number, and nobody means it by "turn four".

**Entries were dropped by kind.** The viewer hid ``event`` *and* ``info`` - and
``info`` is the default kind, carrying "all targets illegal; spell is
countered". The one line that explained a game was the line being thrown away.

None of this needs a real game, and it is tested without one on purpose: the
numbering a person reads is decided in one function, and deciding it inside a
function that first plays a whole game makes it untestable without a card pool.
"""

from __future__ import annotations

import pytest

from mtgfish.rules.kernel.enums import Phase, Step
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.log import GameLog, LogEntry
from mtgfish.sim.replay import (
    DETAIL_LEVELS,
    KEY_KINDS,
    NOISE_KINDS,
    build_turns,
    shown_at,
)

SEATS = {0: "You", 1: "Opponent 2", 2: "Opponent 3", 3: "Opponent 4"}


def entry(turn: int, active: int, kind: str = "info", text: str = "something",
          player: int | None = None, step: Step = Step.MAIN) -> LogEntry:
    return LogEntry(
        turn=turn,
        phase=Phase.PRECOMBAT_MAIN,
        step=step,
        player=PlayerId(active if player is None else player),
        kind=kind,
        text=text,
        active_player=PlayerId(active),
    )


def four_player_log(rounds: int = 3) -> list[LogEntry]:
    """A table of four, each taking ``rounds`` turns, in order."""
    entries = []
    turn = 0
    for _ in range(rounds):
        for seat in range(4):
            turn += 1
            entries.append(entry(turn, seat, "turn", f"--- Turn {turn} ---"))
            entries.append(entry(turn, seat, "land", "plays Island"))
            entries.append(entry(turn, seat, "cast", "casts Sol Ring"))
    return entries


# ---------------------------------------------------------------------------
# Turns are numbered by the player who took them
# ---------------------------------------------------------------------------


def test_each_turn_is_numbered_by_the_seat_that_took_it():
    """The complaint, exactly: game turn 13 is somebody's turn four."""
    _, turns, _ = build_turns(four_player_log(rounds=4), SEATS)

    thirteenth = next(t for t in turns if t.turn == 13)
    assert thirteenth.player == 0
    assert thirteenth.player_turn == 4
    assert thirteenth.label == "You - turn 4"


def test_the_global_number_is_kept_as_well():
    """It is what a digest or a bug report refers to, so it is not thrown away
    in favour of the friendly one."""
    _, turns, _ = build_turns(four_player_log(), SEATS)
    assert [t.turn for t in turns] == list(range(1, 13))


def test_every_seat_counts_its_own_turns():
    _, turns, _ = build_turns(four_player_log(rounds=3), SEATS)
    for seat in range(4):
        mine = [t for t in turns if t.player == seat]
        assert [t.player_turn for t in mine] == [1, 2, 3]


def test_an_extra_turn_is_that_player_taking_another_one():
    """CR 500.7. The engine's counter moves, so it is a new turn - and it is
    the *same seat's* next turn, not the next seat's."""
    entries = [
        entry(1, 0, "turn", "--- Turn 1 ---"),
        entry(2, 0, "turn", "--- Turn 2 ---"),  # an extra turn
        entry(3, 1, "turn", "--- Turn 3 ---"),
    ]
    _, turns, _ = build_turns(entries, SEATS)
    assert [(t.player, t.player_turn) for t in turns] == [(0, 1), (0, 2), (1, 1)]


def test_an_entry_belonging_to_another_player_does_not_start_a_turn():
    """An opponent who draws during your turn is an entry with their id on your
    turn. Numbering from ``player`` rather than ``active_player`` opened a turn
    for them in the middle of yours."""
    entries = [
        entry(1, 0, "turn", "--- Turn 1 ---"),
        entry(1, 0, "info", "Opponent 2 draws a card", player=1),
        entry(1, 0, "cast", "casts Sol Ring"),
    ]
    frames, turns, _ = build_turns(entries, SEATS)
    assert len(turns) == 1
    assert turns[0].player == 0
    assert frames[1].player == 1, "the entry still belongs to whoever it was about"
    assert frames[1].active_player == 0


def test_a_seat_with_no_name_still_gets_a_label():
    _, turns, _ = build_turns([entry(1, 2, "turn", "--- Turn 1 ---")], {})
    assert turns[0].label == "P2 - turn 1"


# ---------------------------------------------------------------------------
# What happens before the first turn is not a turn
# ---------------------------------------------------------------------------


def test_the_pre_game_log_is_not_counted_as_somebody_s_first_turn():
    """Shuffling, opening hands and turn order are all logged on turn zero.

    Counting that block as a turn shifted the numbering of whichever seat
    happened to be active during setup - by one, for the whole game - and
    pulled every one of their end-of-turn snapshots off by one with it.
    """
    entries = [entry(0, 2, "setup", "Turn order: P2, P3, P0, P1")]
    entries += four_player_log(rounds=2)

    _, turns, _ = build_turns(entries, SEATS)
    assert turns[0].is_setup
    assert turns[0].label == "Before the game"
    assert turns[0].player_turn == 0

    first_for_p2 = next(t for t in turns[1:] if t.player == 2)
    assert first_for_p2.player_turn == 1


def test_the_setup_block_keeps_its_entries():
    """Not a turn is not the same as not worth showing: a mulligan to five is
    the first thing that happened to that game."""
    entries = [
        entry(0, 0, "setup", "Turn order: P0, P1"),
        entry(0, 0, "mulligan", "P0 mulligans to 6"),
        entry(1, 0, "turn", "--- Turn 1 ---"),
    ]
    frames, turns, _ = build_turns(entries, SEATS)
    assert turns[0].end - turns[0].start == 2
    assert frames[1].text == "P0 mulligans to 6"
    assert shown_at("setup", "key") and shown_at("mulligan", "key")


# ---------------------------------------------------------------------------
# Every frame knows where it belongs
# ---------------------------------------------------------------------------


def test_frames_carry_the_turn_they_belong_to():
    """So the viewer can label, filter or group without a second lookup."""
    frames, _, _ = build_turns(four_player_log(rounds=2), SEATS)
    last = frames[-1]
    assert last.turn == 8
    assert last.active_player == 3
    assert last.player_turn == 2


def test_a_turn_covers_exactly_its_own_frames():
    frames, turns, _ = build_turns(four_player_log(rounds=2), SEATS)
    assert turns[-1].end == len(frames)
    for turn in turns:
        covered = frames[turn.start : turn.end]
        assert covered, "a turn with no frames should not exist"
        assert {f.turn for f in covered} == {turn.turn}
    # Contiguous and complete: nothing between two turns, nothing left over.
    assert sum(t.end - t.start for t in turns) == len(frames)


def test_what_happened_in_a_turn_is_counted_for_the_sidebar():
    """So a fifty-turn game can be scanned rather than read end to end."""
    _, turns, _ = build_turns(four_player_log(rounds=1), SEATS)
    assert all(t.spells_cast == 1 and t.lands_played == 1 for t in turns)


def test_turn_starts_still_point_at_the_first_frame_of_each_turn():
    frames, turns, starts = build_turns(four_player_log(rounds=2), SEATS)
    for turn in turns:
        assert starts[turn.turn] == turn.start
        assert frames[starts[turn.turn]].turn == turn.turn


def test_an_empty_log_produces_nothing_rather_than_failing():
    frames, turns, starts = build_turns([], SEATS)
    assert (frames, turns, starts) == ([], [], {})


# ---------------------------------------------------------------------------
# Nothing is dropped silently
# ---------------------------------------------------------------------------


def test_the_default_level_shows_the_kind_that_used_to_be_dropped():
    """``info`` is the default kind. "All targets illegal; spell is countered"
    is recorded with it, and the old viewer hid every one of them."""
    assert shown_at("info", "normal")


def test_only_the_raw_event_stream_is_hidden_by_default():
    assert not shown_at("event", "normal")
    assert shown_at("event", "everything")


def test_an_unclassified_kind_shows_rather_than_vanishes():
    """A kind nobody has thought about appears in the viewer. The failure mode
    to avoid is a new sort of entry silently never being shown."""
    assert shown_at("a-kind-invented-tomorrow", "normal")
    assert shown_at("a-kind-invented-tomorrow", "everything")
    assert not shown_at("a-kind-invented-tomorrow", "key")


def test_key_moments_are_what_was_played_and_what_it_did():
    for kind in ("cast", "land", "combat", "loss", "turn"):
        assert shown_at(kind, "key"), kind


def test_the_levels_are_ordered_from_least_to_most():
    kinds = ["turn", "cast", "info", "sba", "event", "made-up"]
    counts = [
        sum(shown_at(kind, level) for kind in kinds) for level in DETAIL_LEVELS
    ]
    assert counts == sorted(counts)
    assert counts[0] < counts[-1]


def test_key_and_noise_do_not_overlap():
    assert not (KEY_KINDS & NOISE_KINDS)


# ---------------------------------------------------------------------------
# The log itself
# ---------------------------------------------------------------------------


class FakeGame:
    turn = 7
    phase = Phase.PRECOMBAT_MAIN
    step = Step.MAIN
    active_player = PlayerId(2)


def test_the_log_records_whose_turn_it_was():
    """Not recoverable afterwards from the entry's own player: an opponent
    drawing on your turn carries their id, not the turn's."""
    log = GameLog(enabled=True)
    log.record(FakeGame(), "Opponent 2 draws a card", kind="info", player=PlayerId(1))
    assert log.entries[0].player == PlayerId(1)
    assert log.entries[0].active_player == PlayerId(2)


def test_recording_whose_turn_it_was_does_not_change_the_digest():
    """The digest is the determinism check. Two runs of one seed must agree,
    and what the viewer needs must not be able to move it."""
    log = GameLog(enabled=True)
    log.record(FakeGame(), "a thing happened")
    quiet = GameLog(enabled=False)
    quiet.record(FakeGame(), "a thing happened")
    assert log.digest() == quiet.digest()


@pytest.mark.parametrize("level", DETAIL_LEVELS)
def test_every_level_is_a_level_the_viewer_can_ask_for(level):
    assert isinstance(shown_at("cast", level), bool)
