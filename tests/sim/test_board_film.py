"""The board a replay draws, rather than the log it prints.

A log is exact and unreadable. After fifty turns it is a very long way of
saying "your deck made five thousand Ape tokens", and the questions a person
actually has - what is on the battlefield, what is tapped, what is attacking,
which of these cards does the engine not understand - are all answered by
looking rather than reading.

So a replay also records the position over time. What is tested here is the
part that has to be cheap and exact: the film says what the game said, it
records no board twice, and a watched game is the same game as an unwatched
one.
"""

from __future__ import annotations

import pytest

from mtgfish.rules.enums import Zone
from mtgfish.rules.events import Event, EventKind
from mtgfish.rules.ids import PlayerId
from mtgfish.sim.board import SAMPLE_AFTER, BoardCardRef, BoardRecorder

from harness import make_board


@pytest.fixture
def table(card_db):
    return make_board(card_db, players=4)


def record(table) -> BoardRecorder:
    recorder = BoardRecorder()
    recorder.sample(table.game)
    return recorder


# ---------------------------------------------------------------------------
# What a snapshot says
# ---------------------------------------------------------------------------


def test_a_snapshot_has_every_seat_and_their_permanents(table):
    table.play("Grizzly Bears", controller=0)
    table.play("Serra Angel", controller=2)
    recorder = record(table)

    board = recorder.film.snapshots[-1]
    assert len(board.seats) == 4
    owned = {seat.player: [c.card for c in seat.permanents] for seat in board.seats}
    assert len(owned[0]) == 1 and len(owned[2]) == 1
    assert owned[1] == [] and owned[3] == []


def test_a_permanent_carries_what_you_would_look_at(table):
    bear = table.play("Grizzly Bears", controller=0)
    bear.tapped = True
    bear.damage = 1
    bear.add_counters("+1/+1", 2)
    table.refresh()

    board = record(table).film.snapshots[-1]
    card = board.seats[0].permanents[0]
    assert card.tapped is True
    assert card.damage == 1
    assert card.counters == {"+1/+1": 2}
    assert (card.power, card.toughness) == (4, 4), "counters are in the printed p/t"


def test_life_and_the_hidden_zones_are_counted_not_listed(table):
    """A viewer needs the size of a hand, never its contents - and a replay
    that leaked them would be showing information no player had."""
    before = len(table.game.player(PlayerId(1)).hand)  # an opening hand was dealt
    table.hand("Grizzly Bears", controller=1)
    table.hand("Serra Angel", controller=1)
    table.graveyard("Lightning Bolt", controller=1)
    table.game.player(PlayerId(1)).life = 23

    seat = record(table).film.snapshots[-1].seats[1]
    assert seat.life == 23
    assert seat.hand == before + 2
    assert seat.graveyard == 1
    assert seat.library > 0
    # Counts, not contents: a replay that listed a hand would be showing
    # information no player at the table had.
    assert seat.hand_cards == []
    assert seat.graveyard_cards == []


def test_a_hand_is_only_listed_when_the_viewer_may_look(table):
    """The sandbox is a bench with one operator, not a game, so it shows
    everything. A replay is a game, and never does - which is why opening the
    zones has to be asked for rather than being the default."""
    table.hand("Grizzly Bears", controller=0)
    table.graveyard("Lightning Bolt", controller=0)

    shut = BoardRecorder()
    shut.sample(table.game)
    assert shut.film.snapshots[-1].seats[0].hand_cards == []

    open_bench = BoardRecorder(open_zones=True)
    open_bench.sample(table.game)
    seat = open_bench.film.snapshots[-1].seats[0]
    film = open_bench.film
    assert [film.cards[c.card].name for c in seat.graveyard_cards] == ["Lightning Bolt"]
    assert "Grizzly Bears" in [film.cards[c.card].name for c in seat.hand_cards]


def test_the_stack_is_bottom_first(table):
    """The way it resolves: the last one on is the first one off."""
    from mtgfish.rules.gameobject import ObjectKind

    first = table.game.create_object(
        table.db.lookup("Lightning Bolt"), PlayerId(0), Zone.STACK
    )
    second = table.game.create_object(
        table.db.lookup("Counterspell"), PlayerId(1), Zone.STACK
    )
    table.refresh()

    film = record(table).film
    stack = film.snapshots[-1].stack
    assert [film.cards[item.card].name for item in stack] == [
        "Lightning Bolt",
        "Counterspell",
    ]
    assert stack[-1].controller == 1


# ---------------------------------------------------------------------------
# Combat, which a log mentions once and a board shows
# ---------------------------------------------------------------------------


def test_an_attacker_says_who_it_is_attacking(table):
    from mtgfish.rules.combat import Combat

    bear = table.play("Grizzly Bears", controller=0)
    table.game.combat = Combat(attacking={bear.id: PlayerId(2)})

    card = record(table).film.snapshots[-1].seats[0].permanents[0]
    assert card.attacking == 2
    assert card.attacking_permanent == -1


def test_a_blocker_says_what_it_is_blocking(table):
    from mtgfish.rules.combat import Combat

    attacker = table.play("Grizzly Bears", controller=0)
    blocker = table.play("Serra Angel", controller=2)
    table.game.combat = Combat(
        attacking={attacker.id: PlayerId(2)},
        blockers={attacker.id: [blocker.id]},
        blocking={blocker.id: [attacker.id]},
    )

    board = record(table).film.snapshots[-1]
    defending = next(c for c in board.seats[2].permanents)
    assert defending.blocking == (int(attacker.id),)


def test_nobody_is_attacking_outside_combat(table):
    table.play("Grizzly Bears", controller=0)
    card = record(table).film.snapshots[-1].seats[0].permanents[0]
    assert card.attacking == -1 and card.blocking == ()


# ---------------------------------------------------------------------------
# Identity is stored once
# ---------------------------------------------------------------------------


def test_the_same_card_is_stored_once_however_many_are_on_the_table(table):
    """The five-thousand-Ape-tokens problem: a board that repeated the name and
    the art for every copy is most of forty megabytes of JSON."""
    for _ in range(12):
        table.play("Grizzly Bears", controller=0)
    film = record(table).film

    assert len(film.cards) == 1
    assert len({c.card for c in film.snapshots[-1].seats[0].permanents}) == 1
    assert len(film.snapshots[-1].seats[0].permanents) == 12


def test_a_token_is_marked_as_one(table):
    """So the viewer can draw it as a token. Not so it can skip looking the art
    up - Scryfall has token faces, and a clone token is a copy of a real card
    with real art."""
    table.token("Grizzly Bears", controller=0)
    film = record(table).film
    assert film.cards[0].token is True
    assert film.cards[0].name == "Grizzly Bears"


def test_a_token_and_the_card_it_copies_are_different_entries(table):
    """They are drawn differently and one of them has no printing, so they
    cannot share a slot in the card table."""
    table.play("Grizzly Bears", controller=0)
    table.token("Grizzly Bears", controller=0)
    film = record(table).film
    assert len(film.cards) == 2
    assert {c.token for c in film.cards} == {True, False}


def test_a_real_card_carries_its_printing(table):
    table.play("Grizzly Bears", controller=0)
    film = record(table).film
    assert film.cards[0].oracle_id
    assert film.cards[0].token is False


# ---------------------------------------------------------------------------
# A board is never stored twice
# ---------------------------------------------------------------------------


def test_an_unchanged_board_is_not_recorded_again(table):
    table.play("Grizzly Bears", controller=0)
    recorder = BoardRecorder()
    for _ in range(20):
        recorder.sample(table.game)
    assert len(recorder.film.snapshots) == 1


def test_a_changed_board_is(table):
    recorder = BoardRecorder()
    recorder.sample(table.game)
    table.play("Grizzly Bears", controller=0)
    recorder.sample(table.game)
    assert len(recorder.film.snapshots) == 2


def test_a_life_total_counts_as_a_change(table):
    """It is on screen, so it is part of the board."""
    recorder = BoardRecorder()
    recorder.sample(table.game)
    table.game.player(PlayerId(0)).life -= 3
    recorder.sample(table.game)
    assert len(recorder.film.snapshots) == 2


def test_the_frame_moves_even_when_the_board_does_not(table):
    """A collapsed sample is the same picture staying current for longer, so
    looking one up at a later frame still finds it."""
    recorder = BoardRecorder()
    recorder.sample(table.game)
    table.game.log.enabled = True
    table.game.log.record(table.game, "something that changed nothing")
    recorder.sample(table.game)

    assert len(recorder.film.snapshots) == 1
    assert recorder.film.at_frame(50) is recorder.film.snapshots[0]


# ---------------------------------------------------------------------------
# Finding the board for a log position
# ---------------------------------------------------------------------------


def test_the_board_for_a_frame_is_the_last_one_at_or_before_it(table):
    from mtgfish.sim.board import BoardFilm, BoardSnapshot

    film = BoardFilm(
        snapshots=[
            BoardSnapshot(frame=0, turn=1, phase="", step="", active=0),
            BoardSnapshot(frame=10, turn=2, phase="", step="", active=0),
            BoardSnapshot(frame=20, turn=3, phase="", step="", active=0),
        ]
    )
    assert film.at_frame(0).turn == 1
    assert film.at_frame(9).turn == 1
    assert film.at_frame(10).turn == 2
    assert film.at_frame(999).turn == 3


def test_a_film_with_no_boards_answers_nothing_rather_than_raising():
    from mtgfish.sim.board import BoardFilm

    assert BoardFilm().at_frame(3) is None


# ---------------------------------------------------------------------------
# Watching must not change the game
# ---------------------------------------------------------------------------


def test_the_recorder_only_samples_events_it_was_asked_for(table):
    recorder = BoardRecorder()
    recorder(table.game, Event(EventKind.SHUFFLED, player=PlayerId(0)))
    assert not recorder.film.snapshots
    assert EventKind.SHUFFLED not in SAMPLE_AFTER

    recorder(table.game, Event(EventKind.ENTERS_BATTLEFIELD, player=PlayerId(0)))
    assert len(recorder.film.snapshots) == 1


def test_every_sampled_event_is_a_real_event_kind():
    """A kind that no longer exists is a board that silently stops updating."""
    assert SAMPLE_AFTER
    assert all(isinstance(kind, EventKind) for kind in SAMPLE_AFTER)


def test_a_card_reference_is_hashable_so_it_can_be_interned():
    ref = BoardCardRef(name="Grizzly Bears", oracle_id="x", type_line="Creature")
    assert {ref: 0}[ref] == 0
    assert ref == BoardCardRef(name="Grizzly Bears", oracle_id="x", type_line="Creature")
