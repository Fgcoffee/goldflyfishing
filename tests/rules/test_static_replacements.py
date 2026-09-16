"""Replacement effects printed on permanents (CR 614).

The engine had a full replacement layer and the parser could read the
sentences, and the two were never connected: ``register`` is called only from
resolution executors, so a replacement printed on a *permanent* was parsed,
stored on the ability, and then consulted by nothing. Doubling Season sat on
the battlefield doing nothing at all.

Two bugs, and the second is the one worth remembering: even after deriving
them, every caller guarded the replacement path with ``if
game.replacement_effects``, a truthiness check on the registry list. Effects
that were never in that list were skipped before they were asked - which looks
exactly like a card that does not work.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules import actions
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


def _counters_after(box, permanents, placed=1):
    box.put("Grizzly Bears", "battlefield", 0)
    for name in permanents:
        box.put(name, "battlefield", 0)
    box.game.invalidate_characteristics()

    bear = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Grizzly Bears"
    )
    actions.add_counters(box.game, bear, "+1/+1", placed, source=bear.id)
    return bear.counter_count("+1/+1")


def test_without_a_doubler_counters_land_as_printed(box):
    assert _counters_after(box, []) == 1


def test_hardened_scales_adds_one(box):
    assert _counters_after(box, ["Hardened Scales"]) == 2


def test_branching_evolution_doubles(box):
    assert _counters_after(box, ["Branching Evolution"]) == 2


def test_two_doublers_both_apply(box):
    """CR 614.5: each replacement applies at most once, and the affected
    player orders them. Either order is legal; what must not happen is one of
    them being skipped."""
    assert _counters_after(box, ["Hardened Scales", "Branching Evolution"]) in (3, 4)


def test_a_doubler_that_has_left_stops_applying(box):
    """CR 611.3: derived from live permanents, so it ends with its source."""
    from mtgfish.rules.enums import Zone

    box.put("Branching Evolution", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 0)
    box.game.invalidate_characteristics()

    doubler = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Branching Evolution"
    )
    box.game.move_object(doubler, Zone.GRAVEYARD)
    box.game.invalidate_characteristics()

    bear = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Grizzly Bears"
    )
    actions.add_counters(box.game, bear, "+1/+1", 1, source=bear.id)
    assert bear.counter_count("+1/+1") == 1


def test_the_fast_path_asks_about_static_replacements(box):
    """The guard that hid all of this.

    ``game.replacement_effects`` is empty on a board whose only replacement
    comes from a permanent, so a truthiness check on it skipped the whole
    layer.
    """
    box.put("Branching Evolution", "battlefield", 0)
    box.game.invalidate_characteristics()
    assert not box.game.replacement_effects
    assert box.game.has_replacements


@pytest.mark.parametrize(
    "name", ["Hardened Scales", "Branching Evolution", "Corpsejack Menace"]
)
def test_the_card_reads_completely(card_db, name):
    from mtgfish.parser import parse_card

    card = card_db.lookup(name)
    if card is None:
        pytest.skip(f"{name} not in this pool")
    assert parse_card(card).fully_parsed
