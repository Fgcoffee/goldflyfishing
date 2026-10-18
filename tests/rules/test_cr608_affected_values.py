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


# ---------------------------------------------------------------------------
# What a cost or an instruction consumed
# ---------------------------------------------------------------------------


def _target_opponent(box, needle):
    from mtgfish.rules.kernel.ids import player_target

    box.give_mana(10)
    offered = [a for a in box.legal() if needle in a["description"]]
    assert offered, f"{needle} cannot be activated"
    result = box.perform(offered[0]["index"], targets=[[player_target(1)]])
    assert not result.get("error"), result.get("error")
    _drain(box)


def test_the_sacrificed_creatures_toughness_is_read_off_the_cost(box):
    """Diamond Valley: "{T}, Sacrifice a creature: You gain life equal to the
    sacrificed creature's toughness" - the creature as it last existed on
    the battlefield (CR 608.2h). It was unread."""
    box.put("Diamond Valley", "battlefield", 0)
    box.put("Colossal Dreadmaw", "battlefield", 0)
    me = box.game.player(ME)
    before = me.life
    box.give_mana(10)
    offered = [a for a in box.legal() if "Diamond Valley" in a["description"]]
    assert offered
    assert not box.perform(offered[0]["index"]).get("error")
    _drain(box)
    assert me.life == before + 6


def test_the_sacrificed_artifacts_mana_value(box):
    """Bosh: "Sacrifice an artifact: this deals damage equal to the
    sacrificed artifact's mana value to any target"."""
    box.put("Bosh, Iron Golem", "battlefield", 0)
    box.put("Wurmcoil Engine", "battlefield", 0)
    them = box.game.player(PlayerId(1))
    before = them.life
    _target_opponent(box, "Bosh")
    assert them.life == before - 6


def test_the_sacrificed_creatures_power_is_on_the_record(box):
    """Altar of Dementia's sacrifice cost was never recorded, so "the
    sacrificed creature's power" read nothing and milled nothing."""
    box.put("Altar of Dementia", "battlefield", 0)
    box.put("Colossal Dreadmaw", "battlefield", 0)
    box.put("Island", "library", 1, count=10)
    them = box.game.player(PlayerId(1))
    before = len(them.library)
    _target_opponent(box, "Altar of Dementia")
    assert len(them.library) == before - 6


def test_the_exiled_card_an_instruction_exiled(box):
    """Morbid Bloom: "Exile target creature card from a graveyard, then
    create X 1/1 Saprolings, where X is the exiled card's toughness"."""
    box.put("Colossal Dreadmaw", "graveyard", 1)
    box.put("Morbid Bloom", "hand", 0)
    card = _named(box, "Colossal Dreadmaw", "GRAVEYARD")
    _cast(box, "Morbid Bloom", targets=[[card.id]])
    saprolings = [
        obj
        for obj in box.game.permanents()
        if "Saproling" in str(box.game.characteristics(obj).type_line)
    ]
    assert len(saprolings) == 6


def _reads(card_db, name):
    from mtgfish.parser import parse_card

    card_db.registry()
    return parse_card(card_db.lookup(name))


def test_a_consumed_value_with_nothing_to_read_stays_unread(card_db):
    """Drach'Nyen's static bonus asks about a card exiled by a different
    ability, which no cost of its own recorded. (Fling's sacrifice is paid as
    it is cast and read off that record: test_cr601_additional_cost_record.)"""
    assert not _reads(card_db, "Drach'Nyen").fully_parsed


# ---------------------------------------------------------------------------
# The object phrase in "the power of X", "counters on X", "mana spent to cast X"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("the power of this creature", Value(kind=ValueKind.POWER)),
        ("the power of that creature", Value(kind=ValueKind.POWER, filter=REMEMBERED)),
        (
            "the number of +1/+1 counters on that creature",
            Value(kind=ValueKind.COUNTERS, counter_type="+1/+1", filter=REMEMBERED),
        ),
        (
            "the number of +1/+1 counters on it",
            Value(kind=ValueKind.COUNTERS, counter_type="+1/+1", of_affected=True),
        ),
        ("the amount of mana spent to cast this spell", Value(kind=ValueKind.MANA_SPENT)),
        (
            "the amount of mana spent to cast that spell",
            Value(kind=ValueKind.MANA_SPENT, filter=REMEMBERED),
        ),
    ],
)
def test_the_object_phrase_is_read(card_db, text, expected):
    from mtgfish.parser.nouns import parse_value
    from mtgfish.parser.tokens import Stream

    card_db.registry()
    stream = Stream.of(text)
    assert parse_value(stream) == expected
    assert stream.done


@pytest.mark.parametrize(
    "text",
    ["the power of target creature", "the number of +1/+1 counters on target creature"],
)
def test_a_targeted_object_phrase_is_not_the_source(card_db, text):
    """Its target is chosen by some other instruction; read as the source's
    power it was a different card."""
    from mtgfish.parser.nouns import parse_value
    from mtgfish.parser.tokens import Stream

    card_db.registry()
    stream = Stream.of(text)
    value = parse_value(stream)
    assert value is None or not stream.done


def test_mana_spent_on_the_spell_a_trigger_saw(box):
    """Aberrant Manawurm: "+X/+0, where X is the amount of mana spent to cast
    that spell" - the instant's, not the Manawurm's own."""
    box.put("Aberrant Manawurm", "battlefield", 0)
    box.put("Divination", "hand", 0)
    box.put("Island", "library", 0, count=5)
    _cast(box, "Divination")
    assert box.game.characteristics(_named(box, "Aberrant Manawurm")).power == 2 + 3


# ---------------------------------------------------------------------------
# Antecedents the resolution cannot name
# ---------------------------------------------------------------------------


def test_it_after_several_tokens_is_not_one_of_them(card_db):
    """Rolling Hamsphere: "create three Hamster tokens, then it deals X
    damage" - "it" is the Vehicle, not the first Hamster."""
    ability = _reads(card_db, "Rolling Hamsphere").faces[0].abilities[1]
    damage = [e for top in ability.effects for e in top.walk() if e.kind is EffectKind.DAMAGE]
    assert damage and damage[0].damage_source is None


def test_that_creature_after_an_unremembered_instruction_stays_unread(card_db):
    """Hunter's Bow: "attach it to target creature you control. That creature
    deals damage equal to its power" - the attached creature, which the
    resolution does not remember. Read as the Equipment's power it did
    nothing."""
    ability = _reads(card_db, "Hunter's Bow").faces[0].abilities[0]
    assert ability.unparsed


def test_counters_on_them_are_each_affected_creatures(box):
    """Toxrill: "Creatures you don't control get -1/-1 for each slime counter
    on them" - each creature's own counters, not the source's."""
    box.put("Toxrill, the Corrosive", "battlefield", 0)
    box.put("Colossal Dreadmaw", "battlefield", 1, count=2)
    _drain(box)
    slimed, clean = list(box.game.permanents(PlayerId(1)))
    slimed.add_counters("slime", 2)
    box.game.invalidate_characteristics()
    assert box.game.characteristics(slimed).power == 4
    assert box.game.characteristics(clean).power == 6


def test_it_after_a_singular_untargeted_object_is_that_object(card_db):
    """Bind the Monster: "tap enchanted creature. It deals damage to you equal
    to its power" - the enchanted creature deals it."""
    ability = _reads(card_db, "Bind the Monster").faces[0].abilities[1]
    damage = [e for top in ability.effects for e in top.walk() if e.kind is EffectKind.DAMAGE]
    assert damage[0].damage_source == REMEMBERED
    assert damage[0].amount == Value(kind=ValueKind.POWER, filter=REMEMBERED)


def test_the_power_of_the_exiled_card_is_not_the_target(card_db):
    """Bishop of Binding: "target Vampire gets +X/+X, where X is the power of
    the exiled card" - a card another ability exiled, not the Vampire."""
    ability = _reads(card_db, "Bishop of Binding").faces[0].abilities[-1]
    assert ability.unparsed
