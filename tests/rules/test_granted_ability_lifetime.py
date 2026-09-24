"""How long a granted ability lasts, and what ends it.

Two grants that look identical on the card behave completely differently, and
the difference is where the effect came from:

* A **resolved ability** with no stated duration creates a continuous effect
  that lasts indefinitely (CR 611.2b). It does *not* care what happens to the
  thing that granted it. Massimo, the Magician is the motivating case: it
  grants a creature a triggered ability, and that creature keeps the ability
  after Massimo dies, is exiled, or is bounced.

* A **static ability** of a permanent applies only while that permanent is
  there (CR 611.3). Levitation stops granting flying the moment it leaves.

Both end when the *recipient* changes zones, because that produces a new
object with no memory of anything (CR 400.7).

Worth pinning precisely because the engine gets it right for structural
reasons rather than by explicit intent: statics are re-derived on every
recomputation while resolved effects are stored. A refactor that unified them
would silently break one of the two, and the failure would look like a card
that is slightly too good.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.enums import CardType, Duration, Zone
from mtgfish.rules.kernel.query import ObjectFilter
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


def _named(box, name):
    return next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == name
    )


def _grant_flying(box, recipient, source):
    """What a resolving ability does: a continuous effect with no duration."""
    effect = Effect(
        EffectKind.GRANT_ABILITY,
        targets=ObjectFilter(specific=(recipient.id,), types_all=CardType.CREATURE),
        granted_abilities=(
            Ability(AbilityKind.STATIC, keyword="Flying", text="Flying"),
        ),
        duration=Duration.PERMANENT,
    )
    execute(
        Resolution(game=box.game, source=source.id, controller=source.controller),
        (effect,),
    )
    box.game.invalidate_characteristics()


# ---------------------------------------------------------------------------
# A grant from a resolved ability
# ---------------------------------------------------------------------------


def test_a_resolved_grant_applies(box):
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Serra Angel", "battlefield", 0)
    bear, granter = _named(box, "Grizzly Bears"), _named(box, "Serra Angel")

    _grant_flying(box, bear, granter)
    assert box.game.characteristics(bear).has_keyword("flying")


def test_a_resolved_grant_outlives_the_permanent_that_granted_it(box):
    """CR 611.2b. The Massimo case: the creature keeps the ability after the
    granter is gone, because a resolved effect with no duration is not tied
    to its source."""
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Serra Angel", "battlefield", 0)
    bear, granter = _named(box, "Grizzly Bears"), _named(box, "Serra Angel")

    _grant_flying(box, bear, granter)
    box.game.move_object(granter, Zone.GRAVEYARD)
    box.game.invalidate_characteristics()

    assert box.game.characteristics(bear).has_keyword("flying")


def test_a_resolved_grant_ends_when_the_creature_blinks(box):
    """CR 400.7: a permanent that changes zones is a new object, and the new
    object was never granted anything."""
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Serra Angel", "battlefield", 0)
    bear, granter = _named(box, "Grizzly Bears"), _named(box, "Serra Angel")

    _grant_flying(box, bear, granter)
    exiled = box.game.move_object(bear, Zone.EXILE)
    returned = box.game.move_object(exiled, Zone.BATTLEFIELD)
    box.game.invalidate_characteristics()

    assert not box.game.characteristics(returned).has_keyword("flying")


# ---------------------------------------------------------------------------
# A grant from a static ability
# ---------------------------------------------------------------------------


def test_a_static_grant_applies(box):
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Levitation", "battlefield", 0)
    box.game.invalidate_characteristics()
    assert box.game.characteristics(_named(box, "Grizzly Bears")).has_keyword("flying")


def test_a_static_grant_ends_with_its_source(box):
    """CR 611.3, and the contrast that makes the test above mean something:
    the same words on a permanent behave differently from the same words on a
    resolving spell."""
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Levitation", "battlefield", 0)
    box.game.invalidate_characteristics()

    box.game.move_object(_named(box, "Levitation"), Zone.GRAVEYARD)
    box.game.invalidate_characteristics()

    assert not box.game.characteristics(_named(box, "Grizzly Bears")).has_keyword(
        "flying"
    )


def test_the_two_kinds_of_grant_genuinely_differ(box):
    """Stated as one assertion, because it is the whole point: identical
    wording, opposite lifetimes, decided by where the effect came from."""
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Serra Angel", "battlefield", 0)
    box.put("Levitation", "battlefield", 0)
    bear = _named(box, "Grizzly Bears")

    _grant_flying(box, bear, _named(box, "Serra Angel"))
    box.game.move_object(_named(box, "Levitation"), Zone.GRAVEYARD)
    box.game.move_object(_named(box, "Serra Angel"), Zone.GRAVEYARD)
    box.game.invalidate_characteristics()

    assert box.game.characteristics(bear).has_keyword("flying"), (
        "the resolved grant survives both sources leaving"
    )
