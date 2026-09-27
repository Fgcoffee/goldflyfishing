"""A characteristic of the object the text refers to (CR 608.2h, 603.2).

"You lose life equal to that card's mana value", "its controller gains life
equal to its power", "you gain life equal to that creature's toughness" are
facts about one particular object - the one an earlier instruction acted on,
or the one a trigger event was about. They were read off the ability's own
source instead, so Reanimate cost the life of Reanimate's own mana value and
Swords to Plowshares gave back the power of an instant: none.

A pronoun in a triggered ability had no object at all: "this deals 2 damage
to it" in Aether Flash dealt damage to nothing.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, keyword, make_board

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ObjectFilter, PlayerFilter, PlayerScope, Value, ValueKind
from mtgfish.rules.kernel.values import evaluate
from mtgfish.ui.sandbox import Sandbox

ME = PlayerId(0)
REMEMBERED = ObjectFilter(remembered=True)


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


def _drain(box):
    box.settle()
    for _ in range(10):
        if not box.game.stack:
            break
        box.resolve_top()


def _cast(box, name, targets=None):
    box.give_mana(10)
    offered = [a for a in box.legal() if name in a["description"]]
    assert offered, f"{name} cannot be cast"
    result = box.perform(offered[0]["index"], targets=targets)
    assert not result.get("error"), result.get("error")
    _drain(box)


# ---------------------------------------------------------------------------
# The object an earlier instruction acted on
# ---------------------------------------------------------------------------


def test_divine_offering_gains_the_destroyed_artifacts_mana_value(box):
    """"Destroy target artifact. You gain life equal to its mana value." """
    box.put("Wurmcoil Engine", "battlefield", 1)
    box.put("Divine Offering", "hand", 0)
    me = box.game.player(ME)
    before = me.life
    target = _named(box, "Wurmcoil Engine")
    _cast(box, "Divine Offering", targets=[[target.id]])
    assert _named(box, "Wurmcoil Engine", "GRAVEYARD")
    assert me.life == before + 6


def test_reanimate_costs_the_returned_cards_mana_value(box):
    box.put("Colossal Dreadmaw", "graveyard", 0)
    box.put("Reanimate", "hand", 0)
    me = box.game.player(ME)
    before = me.life
    card = _named(box, "Colossal Dreadmaw", "GRAVEYARD")
    _cast(box, "Reanimate", targets=[[card.id]])
    assert _named(box, "Colossal Dreadmaw")
    assert me.life == before - 6


def test_a_sacrificed_creatures_power_is_read_as_it_last_was(box):
    """Disciple of Bolas: "sacrifice another creature. You gain X life and
    draw X cards, where X is that creature's power" - the creature is in the
    graveyard by then, and is asked as it was (CR 608.2h)."""
    box.put("Island", "library", 0, count=10)
    box.put("Colossal Dreadmaw", "battlefield", 0)
    me = box.game.player(ME)
    life, hand = me.life, me.hand_size
    box.put("Disciple of Bolas", "battlefield", 0)
    _drain(box)
    assert _named(box, "Colossal Dreadmaw", "GRAVEYARD")
    assert me.life == life + 6
    assert me.hand_size == hand + 6


def test_a_reflexive_ability_knows_what_was_sacrificed(box):
    """Disciple of Freyalise: "you may sacrifice another creature. If you do,
    you gain X life and draw X cards, where X is that creature's power". The
    "if you do" half is a reflexive ability (CR 603.12), resolved on its
    own, and still about the creature."""
    box.put("Island", "library", 0, count=10)
    box.put("Colossal Dreadmaw", "battlefield", 0)
    me = box.game.player(ME)
    life, hand = me.life, me.hand_size
    box.put("Disciple of Freyalise", "battlefield", 0)
    _drain(box)
    assert me.life == life + 6
    assert me.hand_size == hand + 6


# ---------------------------------------------------------------------------
# The object a trigger event was about
# ---------------------------------------------------------------------------


def test_it_in_a_trigger_is_the_object_the_event_was_about(box):
    """Aether Flash: "Whenever a creature enters, this enchantment deals 2
    damage to it"."""
    box.put("Aether Flash", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 1)
    _drain(box)
    assert _named(box, "Grizzly Bears", "GRAVEYARD")


def test_its_toughness_in_a_trigger_is_the_entering_creatures(box):
    """Angelic Chorus: "Whenever a creature you control enters, you gain life
    equal to its toughness" - the creature's, not the enchantment's."""
    box.put("Angelic Chorus", "battlefield", 0)
    me = box.game.player(ME)
    before = me.life
    box.put("Colossal Dreadmaw", "battlefield", 0)
    _drain(box)
    assert me.life == before + 6


def test_this_creatures_power_is_the_sources_even_in_a_pump(card_db):
    """"Another target creature gets +X/+0, where X is this creature's
    power" - the source's power, not the target's own."""
    from mtgfish.parser.nouns import parse_value
    from mtgfish.parser.tokens import Stream

    card_db.registry()
    value = parse_value(Stream.of("this creature's power"))
    assert value == Value(kind=ValueKind.POWER)


# ---------------------------------------------------------------------------
# The engine's side, on hand-built effects
# ---------------------------------------------------------------------------


def test_a_named_objects_value_reads_that_object(card_db):
    board = make_board(card_db, ScriptedAbilities())
    dreadmaw = board.play("Colossal Dreadmaw", controller=1)
    bears = board.play("Grizzly Bears", controller=0)
    value = Value(kind=ValueKind.POWER, filter=REMEMBERED)
    assert evaluate(board.game, value, source=bears.id, remembered=(dreadmaw.id,)) == 6
    # Outside a resolution nothing is remembered, and nothing is not the
    # source: zero rather than the Bears' 2.
    assert evaluate(board.game, value, source=bears.id) == 0


def test_the_named_dealer_deals_the_damage(card_db):
    """"It deals damage equal to its power": the creature is the source of
    the damage (CR 120.1), so its lifelink gains its controller life."""
    scripts = ScriptedAbilities().add("Vampire Nighthawk", keyword("Lifelink"))
    board = make_board(card_db, scripts)
    nighthawk = board.play("Vampire Nighthawk", controller=0)
    me, them = board.game.player(ME), board.game.player(PlayerId(1))
    start_me, start_them = me.life, them.life
    resolution = Resolution(game=board.game, source=0, controller=ME)
    resolution.remembered = [nighthawk.id]
    execute(
        resolution,
        (
            Effect(
                EffectKind.DAMAGE,
                players=PlayerFilter(PlayerScope.EACH_OPPONENT),
                amount=Value(kind=ValueKind.POWER, filter=REMEMBERED),
                damage_source=REMEMBERED,
            ),
        ),
    )
    assert them.life == start_them - 2
    assert me.life == start_me + 2


def test_a_countered_spell_is_remembered(card_db):
    """"Counter target spell. Create X Treasures, where X is that spell's
    mana value" - countering is something done to the spell."""
    from mtgfish.rules.cr600_spells_and_abilities.resolve import _REMEMBERING

    assert EffectKind.COUNTER_SPELL in _REMEMBERING
