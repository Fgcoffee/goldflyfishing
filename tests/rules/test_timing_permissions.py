"""Timing: the two separate ways a sorcery gets cast outside a main phase.

They are genuinely different mechanisms and it is worth keeping them apart,
because only one of them existed:

* **CR 608.2f** - a *resolving effect* tells you to cast a card, and timing
  simply does not apply. This works structurally, because the timing gate
  lives in the action enumerator and a resolving effect calls ``cast_spell``
  directly. Worth a test precisely because it works by construction rather
  than by intent, and a later refactor could easily move the gate.

* **CR 113.6** - a *continuous permission* grants the timing the card lacks
  ("you may cast spells as though they had flash"). This did not exist at all.
  The rules are maximally restrictive by default, so without something that
  says "yes", Vedalken Orrery and every card like it did nothing.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.enums import Phase, Step
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


def _into_combat(box):
    box.game.phase = Phase.COMBAT
    box.game.step = Step.DECLARE_ATTACKERS
    box.game.invalidate_characteristics()


def _castable(box):
    return [action["description"] for action in box.legal()]


# ---------------------------------------------------------------------------
# CR 113.6: granted timing
# ---------------------------------------------------------------------------


def test_a_sorcery_cannot_normally_be_cast_in_combat(box):
    """The baseline. Without this the next test proves nothing."""
    box.put("Wrath of God", "hand", 0)
    box.give_mana(10)
    _into_combat(box)
    assert not any("Wrath of God" in text for text in _castable(box))


def test_an_effect_can_grant_the_timing_a_card_lacks(box):
    """Vedalken Orrery: "You may cast spells as though they had flash."."""
    box.put("Wrath of God", "hand", 0)
    box.put("Vedalken Orrery", "battlefield", 0)
    box.give_mana(10)
    _into_combat(box)
    assert any("Wrath of God" in text for text in _castable(box))


def test_the_permission_ends_with_its_source(box):
    """CR 611.3: a permission from a permanent that has left is not a
    permission any more - the same rule that governs prohibitions."""
    box.put("Wrath of God", "hand", 0)
    box.put("Vedalken Orrery", "battlefield", 0)
    box.give_mana(10)
    _into_combat(box)
    assert any("Wrath of God" in text for text in _castable(box))

    orrery = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Vedalken Orrery"
    )
    from mtgfish.rules.enums import Zone

    box.game.move_object(orrery, Zone.GRAVEYARD)
    box.game.invalidate_characteristics()

    assert not any("Wrath of God" in text for text in _castable(box))


def test_granted_timing_does_not_make_an_unaffordable_spell_castable(box):
    """A permission to ignore *timing* is not a permission to ignore cost."""
    box.put("Wrath of God", "hand", 0)
    box.put("Vedalken Orrery", "battlefield", 0)
    _into_combat(box)  # no mana given
    assert not any("Wrath of God" in text for text in _castable(box))


def test_an_instant_is_unaffected(box):
    box.put("Lightning Bolt", "hand", 0)
    box.give_mana(10)
    _into_combat(box)
    assert any("Lightning Bolt" in text for text in _castable(box))


# ---------------------------------------------------------------------------
# CR 608.2f: cast from a resolving effect
# ---------------------------------------------------------------------------


def test_casting_from_a_resolving_effect_ignores_timing(box):
    """CR 608.2f. Massimo's granted ability copies an exiled sorcery and casts
    it during combat damage - a point at which no sorcery could normally be
    cast at all.

    This holds because the timing check lives in the action enumerator and a
    resolving effect calls ``cast_spell`` directly. That is correct, but it is
    correct by structure rather than by intent, so it is pinned here.
    """
    from mtgfish.rules.casting import cast_spell
    from mtgfish.rules.ids import PlayerId
    from mtgfish.rules.priority import Action, ActionKind

    box.put("Wrath of God", "hand", 0)
    box.give_mana(10)
    _into_combat(box)

    wrath = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Wrath of God"
    )
    # The enumerator refuses it...
    assert not any("Wrath of God" in text for text in _castable(box))
    # ...but an effect that instructs the cast is not asking the enumerator.
    cast_spell(
        box.game,
        PlayerId(0),
        Action(ActionKind.CAST_SPELL, source=wrath.id),
    )
    assert [obj["name"] for obj in box.state()["stack"]] == ["Wrath of God"]
