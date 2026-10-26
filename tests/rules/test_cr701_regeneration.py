""""Can't be regenerated" stops a regeneration shield being applied
(CR 701.19c).

Wrath of God's second sentence is read as a prohibition on what the destroy
acted on, which only exists once the destroy has run. The shield had already
saved the creature by then, so "they can't be regenerated" did nothing.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    for name in ("Wrath of God", "Day of Judgment", "Grizzly Bears"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def shielded_bears(box):
    box.put("Grizzly Bears", "battlefield", 1)
    (bears,) = [
        obj
        for obj in box.game.objects.values()
        if obj.is_live and obj.card is not None and obj.card.name == "Grizzly Bears"
    ]
    bears.regeneration_shields = 1
    return bears


def cast_and_resolve(box, name):
    box.put(name, "hand", 0)
    box.give_mana(5, 0)
    action = next(
        action
        for action in legal_actions(box.game, P0)
        if action.kind.name.startswith("CAST")
        and box.game.objects[action.source].card.name == name
    )
    assert _perform(box.game, P0, action)
    box.settle()
    for _ in range(5):
        if not box.game.stack:
            break
        box.resolve_top()


def test_wrath_of_god_destroys_through_a_regeneration_shield(box):
    bears = shielded_bears(box)
    cast_and_resolve(box, "Wrath of God")

    zones = [
        obj.zone
        for obj in box.game.objects.values()
        if obj.is_live and obj.card is not None and obj.card.name == "Grizzly Bears"
    ]
    assert zones == [Zone.GRAVEYARD]
    # The shield was not applied: the old object still carries it.
    assert bears.regeneration_shields == 1


def test_a_plain_destroy_is_regenerated(box):
    """Control: Day of Judgment says nothing about regeneration, so the
    shield replaces the destruction."""
    bears = shielded_bears(box)
    cast_and_resolve(box, "Day of Judgment")

    assert bears.is_live and bears.zone is Zone.BATTLEFIELD
    assert bears.tapped
    assert bears.regeneration_shields == 0
