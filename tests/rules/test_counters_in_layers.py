"""Counters that change characteristics (CR 613.1f, 613.4c, 122.1a, 122.1b).

The layer system read exactly two counter names, "+1/+1" and "-1/-1", so
everything else a counter can do was inert:

  * CR 122.1a: a +X/+Y counter adds X to power and Y to toughness. A +2/+2
    counter from Tin-Wing Chimera, a +1/+0, a -2/-2 from Ebon Praetor - all
    sat on the permanent doing nothing.
  * CR 122.1b: a keyword counter grants its keyword. Spontaneous Flight puts
    a flying counter on a creature; the creature did not fly.

Both are reachable: the parser emits ADD_COUNTERS for them from real cards.
Only +1/+1 and -1/-1 annihilate in pairs (CR 704.5q), and that stays in the
state-based actions where it belongs.
"""

from __future__ import annotations

from harness import make_board


def test_plus_one_counters_still_work(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    bear.counters["+1/+1"] = 3
    board.refresh()
    assert board.pt(bear) == (5, 5)


def test_minus_one_counters_still_work(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    bear.counters["-1/-1"] = 1
    board.refresh()
    assert board.pt(bear) == (1, 1)


def test_a_plus_two_counter_adds_two(card_db):
    """CR 122.1a: the counter's own numbers, not a fixed +1/+1."""
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    bear.counters["+2/+2"] = 1
    board.refresh()
    assert board.pt(bear) == (4, 4)


def test_an_uneven_counter_moves_only_what_it_names(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    bear.counters["+1/+0"] = 2
    board.refresh()
    assert board.pt(bear) == (4, 2)


def test_a_negative_counter_subtracts(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    bear.counters["-0/-1"] = 1
    board.refresh()
    assert board.pt(bear) == (2, 1)


def test_counters_of_different_kinds_add_up(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    bear.counters.update({"+1/+1": 1, "+2/+2": 1, "+1/+0": 2})
    board.refresh()
    assert board.pt(bear) == (7, 5)


def test_a_counter_that_is_not_about_power_is_ignored(card_db):
    """Charge, loyalty, lore and the rest leave power and toughness alone."""
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    bear.counters.update({"charge": 4, "lore": 2})
    board.refresh()
    assert board.pt(bear) == (2, 2)


# -- keyword counters (CR 613.1f, 122.1b) -----------------------------------


def test_a_keyword_counter_grants_its_keyword(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    assert "flying" not in {k.lower() for k in board.keywords(bear)}
    bear.counters["flying"] = 1
    board.refresh()
    assert "flying" in {k.lower() for k in board.keywords(bear)}


def test_several_keyword_counters_all_apply(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    bear.counters.update({"deathtouch": 1, "indestructible": 1})
    board.refresh()
    have = {k.lower() for k in board.keywords(bear)}
    assert {"deathtouch", "indestructible"} <= have


def test_only_the_keywords_the_rule_lists_count(card_db):
    """CR 122.1b is a closed list; a "charge" counter is not a keyword."""
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    bear.counters.update({"charge": 3, "flying": 1})
    board.refresh()
    have = {k.lower() for k in board.keywords(bear)}
    assert "flying" in have
    assert "charge" not in have


def test_a_keyword_counter_and_a_pt_counter_are_independent(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    bear.counters.update({"flying": 1, "+1/+1": 2})
    board.refresh()
    assert board.pt(bear) == (4, 4)
    assert "flying" in {k.lower() for k in board.keywords(bear)}
