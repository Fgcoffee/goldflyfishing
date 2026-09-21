"""Real cards, parsed, played on a board — the layer that was missing.

Thirty rules-test files existed before this one and twenty-six of them handed
the engine hand-built ``Ability`` objects. So the engine tests proved "given
this Ability, the engine is correct" and the parser tests proved "given this
card, the parser makes that Ability" — and *nothing tested the join*.

Every silent bug in this project has lived in that gap. The card parsed, the
opcodes were right, and one connection was missing:

* ``enters tapped`` produced a TAP effect nothing applied;
* Doubling Season produced a REPLACEMENT no registry read;
* Foundry Inspector produced a MODIFY_COST no cost calculation matched;
* Affinity produced a dynamic MODIFY_COST both cost helpers skipped;
* Convoke claimed IMPLEMENTED and the payment step never consulted it.

None of those is a rules bug or a parser bug. They are integration bugs, and
this file is where integration is tested: a card by name, on a board, with an
assertion about what the engine actually does.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr601_casting import cost_increases, cost_reductions
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


def _named(box, name, zone="BATTLEFIELD"):
    return next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == name and obj.zone.name == zone
    )


def _price(box, spell_name, board):
    box.put(spell_name, "hand", 0)
    for name in board:
        box.put(name, "battlefield", 0)
    box.game.invalidate_characteristics()

    spell = _named(box, spell_name, "HAND")
    base = box.game.characteristics(spell).mana_cost.mana_value
    reduction = sum(cost_reductions(box.game, spell, spell.controller))
    increase = sum(cost_increases(box.game, spell, spell.controller))
    return max(0, base + increase - reduction)


# ---------------------------------------------------------------------------
# Cost modification whose amount depends on the board
# ---------------------------------------------------------------------------


def test_affinity_reduces_by_the_number_of_artifacts(box):
    """The bug this test exists for is subtle and total.

    ``cost_reductions`` sorted by ``amount.constant``. A COUNT value has a
    constant of zero, so a dynamic reduction was skipped by the reduction path
    *and* by the increase path, and Affinity did nothing whatsoever - while
    the card reported as fully understood.
    """
    assert _price(box, "Thought Monitor", []) == 7


def test_affinity_with_artifacts_on_the_board(box, card_db):
    if card_db.lookup("Thought Monitor") is None:
        pytest.skip("Thought Monitor not in this pool")
    priced = _price(box, "Thought Monitor", ["Sol Ring", "Skullclamp", "Basilisk Collar"])
    assert priced == 4, "seven, less one for each of three artifacts"


def test_a_flat_reducer_still_works(box):
    assert _price(box, "Sol Ring", ["Foundry Inspector"]) == 0


def test_a_taxer_still_works(box):
    assert _price(box, "Sol Ring", ["Thalia, Guardian of Thraben"]) == 2


# ---------------------------------------------------------------------------
# Convoke: claimed IMPLEMENTED, never consulted
# ---------------------------------------------------------------------------


def _cast(box, spell_name, board, mana=0):
    box.put(spell_name, "hand", 0)
    for name in board:
        box.put(name, "battlefield", 0)
    if mana:
        box.give_mana(mana)
    box.game.invalidate_characteristics()

    offered = [a for a in box.legal() if spell_name.split(",")[0] in a["description"]]
    if not offered:
        return None
    return box.perform(offered[0]["index"])


def test_convoke_lets_creatures_pay_for_a_spell(box, card_db):
    if card_db.lookup("Chord of Calling") is None:
        pytest.skip("Chord of Calling not in this pool")
    result = _cast(box, "Chord of Calling", ["Grizzly Bears"] * 6)
    assert result is not None, "the spell must be offered at all"
    assert not result.get("error"), result


def test_convoke_taps_only_what_it_needs(box, card_db):
    if card_db.lookup("Chord of Calling") is None:
        pytest.skip("Chord of Calling not in this pool")
    _cast(box, "Chord of Calling", ["Grizzly Bears"] * 6)
    tapped = [
        obj for obj in box.state()["players"][0]["battlefield"] if obj["tapped"]
    ]
    assert 0 < len(tapped) < 6


def test_convoke_cannot_conjure_a_spell_from_nothing(box, card_db):
    """The bound has to be real in both directions: an upper bound that says
    yes to everything hides nothing, it just moves the failure later."""
    if card_db.lookup("Chord of Calling") is None:
        pytest.skip("Chord of Calling not in this pool")
    assert _cast(box, "Chord of Calling", []) is None


# ---------------------------------------------------------------------------
# The families fixed earlier, asserted end to end rather than by opcode
# ---------------------------------------------------------------------------


def test_a_tapland_played_from_hand_enters_tapped(box):
    box.put("Temple of Mystery", "hand", 0)
    action = next(a for a in box.legal() if "Temple" in a["description"])
    box.perform(action["index"])
    assert box.state()["players"][0]["battlefield"][0]["tapped"] is True


def test_a_dual_land_makes_two_mana_not_four(box):
    box.put("Simic Growth Chamber", "battlefield", 0)
    _named(box, "Simic Growth Chamber").tapped = False
    action = next(a for a in box.legal() if "Add" in a["description"])
    box.perform(action["index"])
    assert box.game.player(0).mana_pool.total == 2


def test_a_counter_doubler_doubles(box):
    from mtgfish.rules import actions

    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Branching Evolution", "battlefield", 0)
    box.game.invalidate_characteristics()

    bear = _named(box, "Grizzly Bears")
    actions.add_counters(box.game, bear, "+1/+1", 1, source=bear.id)
    assert bear.counter_count("+1/+1") == 2


def test_no_maximum_hand_size_is_honoured_in_cleanup(box):
    from mtgfish.rules.cr500_turn import _discard_to_hand_size

    for _ in range(10):
        box.put("Grizzly Bears", "hand", 0)
    box.put("Reliquary Tower", "battlefield", 0)
    box.game.invalidate_characteristics()

    _discard_to_hand_size(box.game)
    assert len(box.game.player(0).hand) == 10


def test_a_timing_permission_lets_a_sorcery_be_cast_in_combat(box):
    from mtgfish.rules.enums import Phase, Step

    box.put("Wrath of God", "hand", 0)
    box.put("Vedalken Orrery", "battlefield", 0)
    box.give_mana(10)
    box.game.phase, box.game.step = Phase.COMBAT, Step.DECLARE_ATTACKERS
    box.game.invalidate_characteristics()

    assert any("Wrath of God" in a["description"] for a in box.legal())
