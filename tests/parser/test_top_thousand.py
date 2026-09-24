"""Families closed while working the most-played thousand cards.

Grouped by grammatical construct rather than by card, because that is how the
work was done: "this ability triggers only once each turn" is one rule and
four cards, and a test per card would hide that.
"""

from __future__ import annotations

import pytest

from mtgfish.parser import parse_card
from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.tokens import Stream
from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind


@pytest.fixture(autouse=True)
def _registry(card_db):
    card_db.registry()


def _reads(text):
    stream = Stream.of(text)
    return parse_effects(stream) is not None and stream.done


def _card(card_db, name):
    card = card_db.lookup(name)
    if card is None:
        pytest.skip(f"{name} not in this pool")
    return card


# ---------------------------------------------------------------------------
# Riders on the ability rather than on the board
# ---------------------------------------------------------------------------


def test_a_once_each_turn_rider_lands_on_the_trigger(card_db):
    """CR 603.2f. The sentence is about the ability it sits in, so it produces
    no game action - but leaving it unread failed the whole ability and took
    the trigger with it."""
    abilities = [
        a
        for face in parse_card(_card(card_db, "Morbid Opportunist")).faces
        for a in face.abilities
        if a.trigger is not None
    ]
    assert abilities
    assert any(a.trigger.once_each_turn for a in abilities)


def test_the_rider_does_not_leak_onto_ordinary_triggers(card_db):
    abilities = [
        a
        for face in parse_card(_card(card_db, "Grizzly Bears")).faces
        for a in face.abilities
        if a.trigger is not None
    ]
    assert all(not a.trigger.once_each_turn for a in abilities)


# ---------------------------------------------------------------------------
# Delayed triggers written as a tail
# ---------------------------------------------------------------------------


def test_a_trailing_time_makes_the_effect_delayed(card_db):
    """"You draw a card *at the beginning of the next turn's upkeep*" is not a
    draw now. Read as an immediate draw, Arcane Denial becomes a straight
    two-card draw for its controller."""
    stream = Stream.of("You draw a card at the beginning of the next turn's upkeep.")
    effects = parse_effects(stream)
    assert effects is not None and stream.done

    kinds = {node.kind for effect in effects for node in effect.walk()}
    assert EffectKind.DELAYED_TRIGGER in kinds


def test_an_undelayed_draw_is_still_immediate(card_db):
    stream = Stream.of("You draw a card.")
    effects = parse_effects(stream)
    assert effects is not None and stream.done
    kinds = {node.kind for effect in effects for node in effect.walk()}
    assert EffectKind.DELAYED_TRIGGER not in kinds
    assert EffectKind.DRAW in kinds


# ---------------------------------------------------------------------------
# A zone as a target
# ---------------------------------------------------------------------------


def test_a_whole_graveyard_can_be_exiled(card_db):
    """"Exile target player's graveyard" names a *container*, which the object
    grammar cannot express - it reads descriptions of objects."""
    assert _reads("Exile target player's graveyard.")


@pytest.mark.parametrize("name", ["Bojuka Bog"])
def test_graveyard_hate_reads(card_db, name):
    assert parse_card(_card(card_db, name)).fully_parsed


def test_a_possessive_player_is_one_token(card_db):
    """The tokenizer keeps "player's" whole, so "target player" never matched
    it. Listing the possessive forms is cheaper than splitting them, which
    would ripple through every phrase in the grammar."""
    from mtgfish.parser.nouns import parse_player_filter

    stream = Stream.of("target player's graveyard")
    players, targeted = parse_player_filter(stream)
    assert players is not None
    assert targeted


# ---------------------------------------------------------------------------
# Copy-on-enter, retargeting, Class levels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["Clone", "Phyrexian Metamorph", "Spark Double"]
)
def test_enter_as_a_copy_reads(card_db, name):
    """The "except ..." riders change what the copy becomes and are not
    modelled; they are read and dropped rather than failing the card, which
    would lose the copy as well as the exception."""
    assert parse_card(_card(card_db, name)).fully_parsed


@pytest.mark.parametrize("name", ["Deflecting Swat", "Patchwork Banner"])
def test_retargeting_and_chosen_type_read(card_db, name):
    assert parse_card(_card(card_db, name)).fully_parsed


def test_target_spell_or_ability_reads(card_db):
    """An ability on the stack is not an object the noun grammar reads, so the
    phrase stops at "spell" and leaves "or ability" behind."""
    assert _reads("You may choose new targets for target spell or ability.")


def test_a_class_level_reads(card_db):
    assert _reads("Level 2")


# ---------------------------------------------------------------------------
# Mana colour sources, written once
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Add one mana of any color.",
        "Add one mana of any color in your commander's color identity.",
        "Add one mana of any type that a land you control could produce.",
        "Add one mana of any of the exiled card's colors.",
        "Add one mana of the chosen color.",
        "Add six {G}.",
        "Add an additional {B}.",
        "Add {W}, {U}, or {B}.",
    ],
)
def test_every_way_a_card_names_its_colour(card_db, text):
    """One reader for the family. Separate branches for "of any color" and
    "of any one color" had already drifted apart before this."""
    assert _reads(text)
