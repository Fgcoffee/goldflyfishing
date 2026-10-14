"""The sandbox: the instrument for checking the parser and the rules by hand.

Tested headlessly and thoroughly, because this is what gets used to decide
whether a card's odd behaviour is a parser gap or a rules bug. An instrument
that lies is worse than no instrument.
"""

from __future__ import annotations

import pytest

from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db):
    return Sandbox(db=card_db)


# ---------------------------------------------------------------------------
# Looking cards up
# ---------------------------------------------------------------------------


def test_search_finds_cards_by_partial_name(box):
    names = [card["name"] for card in box.search("lightning b")]
    assert "Lightning Bolt" in names


def test_search_ignores_a_single_letter(box):
    """Otherwise every keystroke scans the whole pool."""
    assert box.search("l") == []


def test_inspect_shows_what_the_parser_made_of_a_card(box):
    """The whole reason the sandbox exists.

    A card that does nothing on the battlefield is either an unread ability or
    a rules bug, and those need completely different fixes - so the answer is
    available before the card is ever played.
    """
    info = box.inspect("Lightning Bolt")
    assert info["fully_parsed"] is True
    face = info["faces"][0]
    assert "3 damage" in face["oracle_text"]
    assert any("DAMAGE" in ability["effects"] for ability in face["abilities"])


def test_inspect_names_what_it_could_not_read(box, card_db):
    """An unreadable card has to say *where* the grammar stopped."""
    from mtgfish.parser import parse_card

    unreadable = None
    for card in card_db.iter_cards(commander_legal_only=True):
        if parse_card(card).failures:
            unreadable = card.name
            break
    assert unreadable

    info = box.inspect(unreadable)
    assert info["fully_parsed"] is False
    failures = [f for face in info["faces"] for f in face["failures"]]
    assert failures
    assert failures[0]["reason"]


def test_inspecting_a_missing_card_says_so(box):
    assert "error" in box.inspect("Definitely Not A Card")


# ---------------------------------------------------------------------------
# Building a position
# ---------------------------------------------------------------------------


def test_a_card_can_be_put_into_any_zone(box):
    for zone in ("battlefield", "hand", "graveyard", "exile"):
        state = box.put("Grizzly Bears", zone, 0)
        assert not state.get("error"), zone

    player = box.state()["players"][0]
    assert len(player["battlefield"]) == 1
    assert len(player["hand"]) == 1
    assert len(player["graveyard"]) == 1


def test_a_permanent_placed_on_the_battlefield_can_act_at_once(box):
    """Setting up a position is not playing a turn.

    Having to pass three turns before a creature can attack makes the
    instrument tedious without making it more truthful.
    """
    box.put("Grizzly Bears", "battlefield", 0)
    bear = box.state()["players"][0]["battlefield"][0]
    assert bear["summoning_sick"] is False


def test_putting_a_card_nowhere_real_is_refused(box):
    assert "error" in box.put("Grizzly Bears", "the bin", 0)


def test_an_unknown_card_is_refused(box):
    assert "error" in box.put("Definitely Not A Card", "battlefield", 0)


# ---------------------------------------------------------------------------
# Playing
# ---------------------------------------------------------------------------


def test_legal_actions_come_from_the_engine(box):
    """The sandbox must not offer anything the engine would refuse."""
    box.put("Lightning Bolt", "hand", 0)
    assert box.legal() == [], "no mana, so nothing is castable"

    box.give_mana(5)
    assert any("Lightning Bolt" in a["description"] for a in box.legal())


def test_the_operator_chooses_targets(box):
    """The engine's default takes the first legal candidate, which for a
    Lightning Bolt is as likely to be your own creature as theirs. The first
    time this sandbox was run it killed its own Grizzly Bears."""
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Serra Angel", "battlefield", 1)
    box.put("Lightning Bolt", "hand", 0)
    box.give_mana(5)

    bolt = next(a for a in box.legal() if "Lightning Bolt" in a["description"])
    groups = box.targets_for(bolt["index"])
    assert len(groups) == 1
    # Both creatures and both players (CR 115.4).
    names = {c["name"] for c in groups[0]["candidates"] if not c["is_player"]}
    assert names == {"Grizzly Bears", "Serra Angel"}

    angel = next(c["id"] for c in groups[0]["candidates"] if c["name"] == "Serra Angel")
    box.perform(bolt["index"], targets=[[angel]])
    state = box.resolve_top()

    mine = state["players"][0]["battlefield"]
    theirs = state["players"][1]["battlefield"]
    assert [o["name"] for o in mine] == ["Grizzly Bears"], "my creature should survive"
    assert theirs[0]["damage"] == 3


def test_a_spell_goes_on_the_stack_before_it_resolves(box):
    """CR 601.2a. A sandbox that resolved spells immediately would hide every
    ordering bug there is."""
    box.put("Serra Angel", "battlefield", 1)
    box.put("Lightning Bolt", "hand", 0)
    box.give_mana(5)

    bolt = next(a for a in box.legal() if "Lightning Bolt" in a["description"])
    state = box.perform(bolt["index"])
    assert [o["name"] for o in state["stack"]] == ["Lightning Bolt"]

    state = box.resolve_top()
    assert state["stack"] == []


def test_an_action_the_engine_refuses_reports_why(box):
    assert "error" in box.perform(999)


def test_activating_a_mana_ability_works(box):
    """End to end through the engine: a parsed ability, activated by hand."""
    box.put("Llanowar Elves", "battlefield", 0)
    elves = next(
        (a for a in box.legal() if "Add" in a["description"]), None
    )
    assert elves, [a["description"] for a in box.legal()]

    state = box.perform(elves["index"])
    assert state["players"][0]["mana"] >= 1
    assert state["players"][0]["battlefield"][0]["tapped"] is True


# ---------------------------------------------------------------------------
# The opponent
# ---------------------------------------------------------------------------


def test_the_opponent_does_nothing(box):
    """Deliberately inert. An opponent that fights back makes it impossible to
    tell your card's behaviour from the opponent's."""
    box.put("Serra Angel", "battlefield", 1)
    box.put("Grizzly Bears", "battlefield", 0)

    before = box.state()
    for _ in range(6):
        box.advance()
    after = box.state()

    assert after["players"][0]["life"] == before["players"][0]["life"]
    assert len(after["players"][1]["battlefield"]) == 1


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def test_advancing_moves_through_the_steps(box):
    """Steps rather than whole turns, because the interesting bugs live in the
    boundaries."""
    seen = set()
    for _ in range(8):
        state = box.advance()
        seen.add((state["phase"], state["step"]))
    assert len(seen) > 3


def test_a_whole_turn_can_be_taken(box):
    before = box.state()["turn"]
    state = box.next_turn()
    assert state["turn"] > before


def test_reset_clears_the_board(box):
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Serra Angel", "battlefield", 1)
    state = box.reset()
    assert all(not p["battlefield"] for p in state["players"])
    assert state["turn"] == 1


# ---------------------------------------------------------------------------
# What the UI is shown
# ---------------------------------------------------------------------------


def test_state_is_plain_data(box):
    """The bridge only encodes it; anything exotic would not survive JSON."""
    import json

    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Lightning Bolt", "hand", 0)
    json.dumps(box.state())  # must not raise


def test_an_unreadable_permanent_is_flagged_on_the_board(box, card_db):
    """So the operator can see at a glance that a card is inert rather than
    wondering why it is doing nothing."""
    from mtgfish.parser import parse_card

    name = None
    for card in card_db.iter_cards(commander_legal_only=True):
        parsed = parse_card(card)
        if parsed.failures and card.faces and "Creature" in str(card.faces[0].type_line):
            name = card.name
            break
    if name is None:
        pytest.skip("no unreadable creature in the pool")

    box.put(name, "battlefield", 0)
    permanent = box.state()["players"][0]["battlefield"][0]
    assert permanent["unreadable"] is True


def test_keywords_reach_the_board_display(box):
    box.put("Serra Angel", "battlefield", 0)
    permanent = box.state()["players"][0]["battlefield"][0]
    assert "Flying" in permanent["keywords"]
    assert permanent["power"] == 4


# ---------------------------------------------------------------------------
# CR 115.4: "any target" includes players
# ---------------------------------------------------------------------------


def test_burn_can_be_cast_with_an_empty_board(box):
    """A spell that can target a player always has a legal target.

    Lightning Bolt was uncastable on an empty board, which meant burn could
    never be cast on turn one and could never finish anybody - the simulator
    reported combat damage as the only way any game was ever won.
    """
    box.put("Lightning Bolt", "hand", 0)
    box.give_mana(5)
    assert any("Lightning Bolt" in a["description"] for a in box.legal())


def test_a_player_is_offered_as_a_target(box):
    box.put("Lightning Bolt", "hand", 0)
    box.give_mana(5)
    bolt = next(a for a in box.legal() if "Lightning Bolt" in a["description"])

    candidates = box.targets_for(bolt["index"])[0]["candidates"]
    players = [c for c in candidates if c["is_player"]]
    assert len(players) == 2
    assert {c["controller"] for c in players} == {0, 1}


def test_damage_to_a_chosen_player_lands(box):
    box.put("Lightning Bolt", "hand", 0)
    box.give_mana(5)
    bolt = next(a for a in box.legal() if "Lightning Bolt" in a["description"])

    candidates = box.targets_for(bolt["index"])[0]["candidates"]
    opponent = next(c["id"] for c in candidates if c["is_player"] and c["controller"] == 1)

    box.perform(bolt["index"], targets=[[opponent]])
    state = box.resolve_top()
    assert state["players"][1]["life"] == 37
    assert state["players"][0]["life"] == 40, "it should hit who you aimed at"


def test_a_spell_aimed_at_a_player_does_not_fizzle(box):
    """CR 608.2b checks targets again on resolution.

    A player cannot be destroyed or bounced, so they stay a legal target -
    but the check only knew about objects, so every player-targeted spell was
    countered on resolution for having "all targets illegal".
    """
    box.put("Lightning Bolt", "hand", 0)
    box.give_mana(5)
    bolt = next(a for a in box.legal() if "Lightning Bolt" in a["description"])
    candidates = box.targets_for(bolt["index"])[0]["candidates"]
    target = next(c["id"] for c in candidates if c["is_player"])

    box.perform(bolt["index"], targets=[[target]])
    state = box.resolve_top()
    assert not any("countered" in e["text"].lower() for e in state["log"])


def test_object_and_player_targets_do_not_collide(box):
    """Object ids and player ids are both small integers in different spaces.

    The encoding keeps them apart by sign, and this is what catches it if that
    ever stops being true.
    """
    from mtgfish.rules.kernel.ids import is_player_target, player_target, target_player

    for player in (0, 1, 7):
        encoded = player_target(player)
        assert is_player_target(encoded)
        assert target_player(encoded) == player
    assert not is_player_target(1)
    assert not is_player_target(99999)


# ---------------------------------------------------------------------------
# What the bench switches off
# ---------------------------------------------------------------------------
#
# A sandbox is an instrument, not a game, and the rules that end games end
# sessions before they show anything. Every one of these was a way to be told
# nothing: a board with no library decked whoever drove it on the first draw
# step, a life total set low to watch a drain effect ended the session instead
# of demonstrating it, and mana handed over for a test evaporated at the next
# step boundary.


def test_the_bench_starts_with_the_session_enders_switched_off(box):
    rules = box.state()["rules"]
    assert rules["players_cannot_lose"]
    assert rules["draws_from_an_empty_library_do_nothing"]
    assert rules["mana_pools_persist"]


def test_every_switch_says_what_it_does(box):
    """A checkbox called "players cannot lose" with nothing beside it reads as
    a cheat rather than as the reason the instrument works at all."""
    state = box.state()
    assert set(state["rule_descriptions"]) == set(state["rules"])
    assert all(text.strip() for text in state["rule_descriptions"].values())


def test_taking_turns_on_an_empty_board_does_not_deck_anybody(box):
    """The one that made the sandbox useless. A position is assembled card by
    card, so there is no library, and every draw step was fatal."""
    for _ in range(6):
        state = box.next_turn()

    assert not any(player["has_lost"] for player in state["players"])
    assert not state["game_over"]


def test_a_life_total_below_zero_is_shown_rather_than_fatal(box):
    """Not losing is not the same as not being hit: the life total is the
    evidence that the effect under test worked."""
    box.set_life(-6, 0)
    state = box.advance()
    assert state["players"][0]["life"] == -6
    assert not state["players"][0]["has_lost"]
    assert not state["game_over"]


def test_mana_survives_the_next_step(box):
    """Handing over mana and then advancing a step used to throw it away, so
    every test of a multi-step sequence had to re-hand it."""
    box.give_mana(10, 0)
    before = box.state()["players"][0]["mana"]
    state = box.advance()
    assert state["players"][0]["mana"] == before


def test_a_creature_cast_on_the_bench_can_act_at_once(box):
    """``put`` always did this for a card placed directly. A creature actually
    cast - which is the more honest test - was still summoning-sick."""
    box.put("Grizzly Bears", "hand", 0)
    box.give_mana(5, 0)
    bears = next(a for a in box.legal() if "Grizzly Bears" in a["description"])
    box.perform(bears["index"])
    state = box.resolve_top()
    assert state["players"][0]["battlefield"][0]["summoning_sick"] is False


def play_every_land(box) -> int:
    """Play lands from hand until the engine stops offering them."""
    played = 0
    while True:
        lands = [a for a in box.legal() if a["kind"] == "PLAY_LAND"]
        if not lands:
            return played
        box.perform(lands[0]["index"])
        played += 1


def test_land_drops_stay_unlimited_after_the_first_turn(box):
    """``max_lands`` is reset to one at the end of every turn, so setting it
    once at reset stopped working the moment a turn ended."""
    box.next_turn()
    box.next_turn()
    box.put("Forest", "hand", 0, 3)
    assert play_every_land(box) == 3


# -- and every one of them can be put back ----------------------------------


def test_a_rule_switched_back_on_is_enforced_again(box):
    """The rule itself is sometimes the thing being tested: to watch a player
    actually deck, you have to be able to ask for it."""
    box.set_rule("players_cannot_lose", False)
    box.set_rule("draws_from_an_empty_library_do_nothing", False)

    for _ in range(4):
        state = box.next_turn()
        if state["game_over"]:
            break

    assert state["players"][0]["has_lost"]
    assert state["players"][0]["loss_reason"] == "EMPTY_LIBRARY"


def test_switching_a_rule_back_on_restores_the_real_value(box):
    """CR 305.2 again: one land a turn, as in a game."""
    box.set_rule("unlimited_land_drops", False)
    box.put("Forest", "hand", 0, 3)
    assert play_every_land(box) == 1


def test_an_unknown_switch_is_refused_rather_than_ignored(box):
    """The caller is a UI sending strings. A typo that silently did nothing
    looks exactly like a switch that does not work."""
    assert "error" in box.set_rule("players_cannot_win", True)
    assert "error" in box.set_rules({"nonsense": True})
    assert box.state()["rules"]["players_cannot_lose"], "nothing should have changed"


def test_several_switches_can_be_set_at_once(box):
    """What a panel of checkboxes sends."""
    state = box.set_rules(
        {"players_cannot_lose": False, "mana_pools_persist": False}
    )
    assert state["rules"]["players_cannot_lose"] is False
    assert state["rules"]["mana_pools_persist"] is False
    assert state["rules"]["no_summoning_sickness"] is True


def test_the_switches_survive_a_reset(box):
    """Clearing the board is not a reason to start losing to your own draw
    step again."""
    box.set_rule("players_cannot_lose", False)
    assert box.reset()["rules"]["players_cannot_lose"] is False


# ---------------------------------------------------------------------------
# Stocking a position
# ---------------------------------------------------------------------------


def test_several_copies_can_be_placed_at_once(box):
    """Drawing from an empty library is harmless on the bench, but harmless is
    not useful: "draw three" has nothing to show without a library."""
    state = box.put("Forest", "library", 0, 12)
    assert state["players"][0]["library"] == 12

    state = box.next_turn()
    state = box.next_turn()
    assert state["players"][0]["library"] < 12, "the draw step should have drawn"


def test_a_silly_number_of_copies_is_capped(box):
    box.put("Forest", "library", 0, 10_000)
    assert box.state()["players"][0]["library"] == box.MAX_PUT


def test_searching_finds_a_card_by_the_start_of_its_name(box):
    """The type-ahead used the "did you mean" fuzzy matcher, and by edit
    distance "Grizzly" is not close enough to "Grizzly Bears" to be offered -
    a type-ahead that fails on the thing you are typing."""
    assert "Grizzly Bears" in [card["name"] for card in box.search("Grizzly")]
    assert "Grizzly Bears" in [card["name"] for card in box.search("izzly bea")]


# ---------------------------------------------------------------------------
# What the board says about itself
# ---------------------------------------------------------------------------


def test_the_board_reports_the_library_and_the_game_being_over(box):
    """A card can still say "you win the game", after which nothing responds.
    A board stopped for that reason has to say so, or it reads as broken."""
    state = box.state()
    assert state["game_over"] is False
    assert state["winners"] == []
    assert all("library" in player for player in state["players"])


def test_the_log_the_board_carries_back_is_not_all_events(box):
    """A board a few turns in produces tens of thousands of events; a window of
    the raw log was three useful lines and 197 of them."""
    for _ in range(3):
        box.next_turn()
    log = box.state()["log"]
    assert log
    assert not any(entry["kind"] == "event" for entry in log)
    assert any(entry["kind"] == "turn" for entry in log)


# ---------------------------------------------------------------------------
# The bench as a board
# ---------------------------------------------------------------------------
#
# The sandbox used to describe its board in text, which has the same problem
# the replay had: a list of names is not a board, and "what is tapped, what is
# out, what did the parser fail to read" are all questions you answer by
# looking. It is drawn by the replay's viewer now, from the same snapshot, so
# there is only one board to learn.


def test_the_bench_can_be_drawn_as_a_board(box):
    from mtgfish.sim.board import BoardRecorder

    box.put("Serra Angel", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 1, 2)

    recorder = BoardRecorder(open_zones=True)
    recorder.sample(box.game)
    board = recorder.film.snapshots[-1]

    assert len(board.seats) == 2
    assert len(board.seats[0].permanents) == 1
    assert len(board.seats[1].permanents) == 2


def test_the_bench_shows_hands_because_it_has_one_operator(box):
    """A replay never does - it would be showing information nobody had. The
    sandbox is a bench, and the operator put those cards there themselves."""
    from mtgfish.sim.board import BoardRecorder

    box.put("Lightning Bolt", "hand", 0, 2)
    box.put("Grizzly Bears", "graveyard", 0)

    recorder = BoardRecorder(open_zones=True)
    recorder.sample(box.game)
    seat = recorder.film.snapshots[-1].seats[0]
    names = [recorder.film.cards[c.card].name for c in seat.hand_cards]
    assert names == ["Lightning Bolt", "Lightning Bolt"]
    assert [recorder.film.cards[c.card].name for c in seat.graveyard_cards] == [
        "Grizzly Bears"
    ]


def test_a_tapped_permanent_is_tapped_on_the_board(box):
    from mtgfish.sim.board import BoardRecorder

    box.put("Llanowar Elves", "battlefield", 0)
    elves = next(a for a in box.legal() if "Add" in a["description"])
    box.perform(elves["index"])

    recorder = BoardRecorder(open_zones=True)
    recorder.sample(box.game)
    assert recorder.film.snapshots[-1].seats[0].permanents[0].tapped is True


# ---------------------------------------------------------------------------
# Finding a card by typing its name
# ---------------------------------------------------------------------------


def test_a_name_typed_in_full_is_offered_first(box):
    """Play rate orders the rest, and a basic land has no play rate at all - so
    "Forest" offered Forest Bear, and putting four of them on the battlefield
    gave the operator four 2/2 creatures where they asked for lands."""
    assert box.search("Forest")[0]["name"] == "Forest"
    assert box.search("Island")[0]["name"] == "Island"
    assert box.search("Swamp")[0]["name"] == "Swamp"


def test_a_partial_name_still_ranks_by_play_rate(box):
    names = [card["name"] for card in box.search("lightning b")]
    assert names[0] == "Lightning Bolt"
