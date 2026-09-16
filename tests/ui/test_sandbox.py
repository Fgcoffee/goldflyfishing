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
    from mtgfish.rules.ids import is_player_target, player_target, target_player

    for player in (0, 1, 7):
        encoded = player_target(player)
        assert is_player_target(encoded)
        assert target_player(encoded) == player
    assert not is_player_target(1)
    assert not is_player_target(99999)
