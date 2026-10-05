"""Alternative costs written in a card's own text (CR 118.9), on a board.

"If you control a commander, you may cast this spell without paying its mana
cost" and "You may pay 1 life and exile a blue card from your hand rather than
pay this spell's mana cost" are read into ``Ability.alternative_cost``; these
check that the engine offers the cast exactly when the card says it may.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


def _one(box, name, zone=None):
    found = [
        obj
        for obj in box.game.objects.values()
        if obj.card is not None
        and obj.card.name == name
        and (zone is None or obj.zone is zone)
    ]
    assert found, name
    return found[-1]


def _alternative_casts(box, name, player=0):
    return [
        action
        for action in legal_actions(box.game, PlayerId(player))
        if action.alternative_cost >= 0
        and box.game.objects[action.source].card.name == name
    ]


def _opponent_casts_bolt(box):
    """A spell on the stack for a counterspell to target."""
    box.put("Lightning Bolt", "hand", 1)
    box.give_mana(5, 1)
    index = next(a["index"] for a in box.legal(1) if a["name"] == "Lightning Bolt")
    assert not box.perform(index, 1).get("error")


def _make_commander(box, obj, controller=None):
    obj.is_commander = True
    if controller is not None:
        obj.controller = obj.base_controller = PlayerId(controller)
    box.game.invalidate_characteristics()


def test_free_with_a_commander_on_the_battlefield(box):
    """CR 903.3d: controlling a commander means a commander permanent."""
    box.put("Fierce Guardianship", "hand", 0)
    _opponent_casts_bolt(box)
    assert not _alternative_casts(box, "Fierce Guardianship")

    box.put("Grizzly Bears", "battlefield", 0)
    assert not _alternative_casts(box, "Fierce Guardianship"), "not a commander"

    box.put("Kenrith, the Returned King", "battlefield", 0)
    _make_commander(box, _one(box, "Kenrith, the Returned King"))
    actions = _alternative_casts(box, "Fierce Guardianship")
    assert actions

    from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform

    box.give_mana(3, 0)
    pool_before = box.game.player(PlayerId(0)).mana_pool.total
    assert _perform(box.game, PlayerId(0), actions[0])
    assert box.game.player(PlayerId(0)).mana_pool.total == pool_before
    box.resolve_top()
    assert _one(box, "Lightning Bolt").zone is Zone.GRAVEYARD


def test_a_commander_in_the_command_zone_is_not_controlled(box):
    """CR 903.3d / 108.4: a card in the command zone has no controller."""
    box.put("Fierce Guardianship", "hand", 0)
    _opponent_casts_bolt(box)
    box.put("Kenrith, the Returned King", "command", 0)
    _make_commander(box, _one(box, "Kenrith, the Returned King"))
    assert not _alternative_casts(box, "Fierce Guardianship")


def test_any_commander_you_control_counts(box):
    """CR 903.3d says "a commander", not "your commander": an opponent's
    commander you have taken counts, one they still control does not."""
    box.put("Fierce Guardianship", "hand", 0)
    _opponent_casts_bolt(box)
    box.put("Kenrith, the Returned King", "battlefield", 1)
    kenrith = _one(box, "Kenrith, the Returned King")
    _make_commander(box, kenrith)
    assert not _alternative_casts(box, "Fierce Guardianship")
    _make_commander(box, kenrith, controller=0)
    assert _alternative_casts(box, "Fierce Guardianship")


def test_no_target_no_free_cast(box):
    """CR 601.2c: an alternative cost does not make a counterspell castable
    with nothing to counter."""
    box.put("Fierce Guardianship", "hand", 0)
    box.put("Kenrith, the Returned King", "battlefield", 0)
    _make_commander(box, _one(box, "Kenrith, the Returned King"))
    assert not _alternative_casts(box, "Fierce Guardianship")


def test_force_of_will_needs_another_blue_card(box):
    """It cannot exile itself: it is on the stack when the cost is paid
    (CR 601.2a, 601.2h)."""
    box.put("Force of Will", "hand", 0)
    _opponent_casts_bolt(box)
    assert not _alternative_casts(box, "Force of Will")
    box.put("Lightning Bolt", "hand", 0)
    assert not _alternative_casts(box, "Force of Will"), "red is not blue"
    box.put("Brainstorm", "hand", 0)
    assert _alternative_casts(box, "Force of Will")


def test_force_of_will_pays_life_and_exiles_the_blue_card(box):
    from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform

    box.put("Force of Will", "hand", 0)
    box.put("Brainstorm", "hand", 0)
    _opponent_casts_bolt(box)
    life = box.game.player(PlayerId(0)).life
    assert _perform(box.game, PlayerId(0), _alternative_casts(box, "Force of Will")[0])
    assert box.game.player(PlayerId(0)).life == life - 1
    hand = [box.game.objects[i].card.name for i in box.game.player(PlayerId(0)).hand]
    assert "Brainstorm" not in hand
    assert any(box.game.objects[i].card.name == "Brainstorm" for i in box.game.exile)
    box.resolve_top()
    assert _one(box, "Lightning Bolt").zone is Zone.GRAVEYARD
