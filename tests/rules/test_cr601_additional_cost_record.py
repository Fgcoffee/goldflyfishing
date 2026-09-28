"""What a spell's additional cost consumed (CR 601.2h, 608.2h).

"As an additional cost to cast this spell, sacrifice a creature" is paid
while the spell is cast, alongside its mana cost (CR 601.2f-h) - not carried
out on resolution. The creature is gone before the spell can be responded to,
and a countered Bone Splinters has still cost its creature.

"The sacrificed creature's power" then asks about that creature as it last
existed on the battlefield (CR 608.2h). Nothing else in the resolution can
name it: it is in the graveyard, a new object (CR 400.7), and the spell's own
source is the spell. The engine keeps the record of what the cost consumed on
the spell, and Fling, Life's Legacy and friends read it.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.kernel.ids import PlayerId, player_target
from mtgfish.ui.sandbox import Sandbox

ME = PlayerId(0)
THEM = PlayerId(1)


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


def _offer(box, name):
    box.give_mana(10)
    return [a for a in box.legal() if name in a["description"] and a["description"].startswith("cast")]


def _cast(box, name, targets=None, resolve=True):
    offered = _offer(box, name)
    assert offered, f"{name} cannot be cast"
    result = box.perform(offered[0]["index"], targets=targets)
    assert not result.get("error"), result.get("error")
    if resolve:
        box.settle()
        for _ in range(10):
            if not box.game.stack:
                break
            box.resolve_top()


def test_the_sacrifice_is_paid_while_casting(box):
    """Bone Splinters: the creature is in the graveyard while the spell is
    still on the stack - it is a cost (CR 601.2h), not an instruction."""
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Colossal Dreadmaw", "battlefield", 1)
    box.put("Bone Splinters", "hand", 0)
    target = _named(box, "Colossal Dreadmaw")
    _cast(box, "Bone Splinters", targets=[[target.id]], resolve=False)
    assert box.game.stack
    assert _named(box, "Grizzly Bears", "GRAVEYARD")


def test_no_creature_no_cast(box):
    """CR 601.2h: a cost that cannot be paid stops the cast."""
    box.put("Colossal Dreadmaw", "battlefield", 1)
    box.put("Bone Splinters", "hand", 0)
    assert not _offer(box, "Bone Splinters")


def test_fling_deals_the_sacrificed_creatures_power_as_it_last_existed(box):
    """Fling: the power the creature had on the battlefield (CR 608.2h),
    counters included - not the printed power, and not Fling's."""
    box.put("Grizzly Bears", "battlefield", 0)
    bears = _named(box, "Grizzly Bears")
    bears.add_counters("+1/+1", 3)
    box.game.invalidate_characteristics()
    box.put("Fling", "hand", 0)
    them = box.game.player(THEM)
    before = them.life
    _cast(box, "Fling", targets=[[player_target(1)]])
    assert _named(box, "Grizzly Bears", "GRAVEYARD")
    assert them.life == before - 5


def test_lifes_legacy_draws_the_sacrificed_creatures_power(box):
    box.put("Colossal Dreadmaw", "battlefield", 0)
    box.put("Life's Legacy", "hand", 0)
    for _ in range(10):
        box.put("Forest", "library", 0)
    hand = len(box.game.player(ME).hand)
    _cast(box, "Life's Legacy")
    # Life's Legacy left the hand; six cards came in.
    assert len(box.game.player(ME).hand) == hand - 1 + 6


def test_momentous_fall_reads_power_then_toughness_off_the_same_creature(box):
    """"You draw cards equal to the sacrificed creature's power, then you
    gain life equal to its toughness" - "its" is the sacrificed creature."""
    box.put("Hill Giant", "battlefield", 0)  # 3/3
    box.put("Momentous Fall", "hand", 0)
    for _ in range(10):
        box.put("Forest", "library", 0)
    me = box.game.player(ME)
    hand, life = len(me.hand), me.life
    _cast(box, "Momentous Fall")
    assert len(me.hand) == hand - 1 + 3
    assert me.life == life + 3


def test_worthy_cause_gains_the_sacrificed_creatures_toughness(box):
    box.put("Wall of Stone", "battlefield", 0)  # 0/8
    box.put("Worthy Cause", "hand", 0)
    me = box.game.player(ME)
    life = me.life
    _cast(box, "Worthy Cause")
    assert me.life == life + 8


def test_call_for_blood_shrinks_by_the_sacrificed_power(box):
    """A continuous effect's amount, fixed as the spell resolves (CR 608.2h)
    from what the cost consumed."""
    box.put("Hill Giant", "battlefield", 0)
    box.put("Colossal Dreadmaw", "battlefield", 1)
    box.put("Call for Blood", "hand", 0)
    target = _named(box, "Colossal Dreadmaw")
    _cast(box, "Call for Blood", targets=[[target.id]])
    box.game.invalidate_characteristics()
    chars = box.game.characteristics(target)
    assert (chars.power, chars.toughness) == (3, 3)


def test_eldritch_evolution_bounds_the_search_by_the_sacrificed_mana_value(card_db):
    """"Mana value X or less, where X is 2 plus the sacrificed creature's mana
    value" - read off what the cost consumed, not the card being matched."""
    from mtgfish.parser import parse_card
    from mtgfish.rules.kernel.query import ValueKind

    parsed = parse_card(card_db.lookup("Eldritch Evolution"))
    assert parsed.fully_parsed
    cost, spell = parsed.faces[0].abilities[:2]
    assert cost.additional_cost is not None
    text = repr(spell)
    assert ValueKind.COST_PAID.name in text and "of_affected=True" not in text


def test_an_x_additional_cost_is_left_to_the_resolution(card_db):
    """Toxic Deluge's "pay X life" names the spell's X, which a cost's
    amount is not evaluated with; it is not lifted onto the cast."""
    from mtgfish.parser import parse_card

    parsed = parse_card(card_db.lookup("Toxic Deluge"))
    assert all(a.additional_cost is None for a in parsed.faces[0].abilities)
