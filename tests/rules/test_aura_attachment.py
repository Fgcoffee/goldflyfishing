"""Aura spells target, and enter attached (CR 303.4, 303.4a).

Neither half was implemented. An Aura's target comes from its enchant
ability rather than from a targeting effect, so nothing offered one and an
Aura was cast with no target at all; then it resolved onto the battlefield
attached to nothing, and CR 704.5m put it straight into the graveyard.

Every Aura cast from hand was a dead card - 732 of them in the pool - and
CR 303 is a rule group the coverage table scores as implemented.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr500_turn_structure.restrictions import Act, prohibited
from mtgfish.rules.kernel.enums import Zone
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    sandbox = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    sandbox.give_mana(10, 0)
    return sandbox


def _named(box, name):
    """The live object for a card, i.e. the one no later object superseded."""
    found = [
        o
        for o in box.game.objects.values()
        if o.card is not None and o.card.name == name and o.is_live
    ]
    return found[-1] if found else None


def _cast(box, name, target_id):
    actions = [a for a in box.legal(0) if name in str(a)]
    assert actions, f"{name} is not castable"
    box.perform(actions[0]["index"], 0, targets=[[target_id]])
    box.resolve_top()
    box.settle()


def test_an_aura_spell_offers_its_enchant_target(box):
    """CR 303.4a: the target is defined by the enchant ability."""
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Pacifism", "hand", 0)
    box.game.invalidate_characteristics()

    actions = [a for a in box.legal(0) if "Pacifism" in str(a)]
    assert actions
    groups = box.targets_for(actions[0]["index"], 0)
    assert len(groups) == 1
    names = [c["name"] for c in groups[0]["candidates"]]
    assert "Grizzly Bears" in names


def test_an_aura_enters_attached_to_what_it_targeted(box):
    """CR 303.4: it enters the battlefield attached to that object."""
    bear = box.put("Grizzly Bears", "battlefield", 0)
    box.put("Pacifism", "hand", 0)
    box.game.invalidate_characteristics()
    host = _named(box, "Grizzly Bears")

    _cast(box, "Pacifism", host.id)

    aura = _named(box, "Pacifism")
    assert aura.zone is Zone.BATTLEFIELD, "it must not be binned by CR 704.5m"
    assert aura.attached_to == host.id
    assert bear is not None


def test_the_aura_effect_actually_applies(box):
    """The point of attaching: Pacifism stops the creature attacking."""
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Pacifism", "hand", 0)
    box.game.invalidate_characteristics()
    host = _named(box, "Grizzly Bears")
    assert prohibited(box.game, Act.ATTACK, obj=host) is None

    _cast(box, "Pacifism", host.id)

    host = _named(box, "Grizzly Bears")
    assert prohibited(box.game, Act.ATTACK, obj=host) is not None


def test_an_aura_with_no_legal_host_is_not_castable(box):
    """CR 601.2c: a spell that needs a target cannot be cast without one."""
    box.put("Pacifism", "hand", 0)
    box.game.invalidate_characteristics()
    assert [a for a in box.legal(0) if "Pacifism" in str(a)] == []


def test_a_non_aura_enchantment_is_unaffected(box):
    """Only Auras gain a target; an ordinary enchantment still has none."""
    box.put("Doubling Season", "hand", 0)
    box.game.invalidate_characteristics()
    actions = [a for a in box.legal(0) if "Doubling Season" in str(a)]
    assert actions
    assert box.targets_for(actions[0]["index"], 0) == []
