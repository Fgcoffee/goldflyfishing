"""The grammar of MTG Arena's perpetually, conjure and seek.

Each construct is read into its own opcode, and the shapes the engine cannot
honour are left unread rather than approximated.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.tokens import Stream
from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind
from mtgfish.rules.cr700_additional_rules.digital_mechanics import conjured_name
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.query import ControllerRelation


@pytest.fixture(autouse=True)
def _registry(card_db):
    return card_db


def _read(text):
    stream = Stream.of(text)
    effects = parse_effects(stream)
    return effects if stream.done else None


# -- perpetually --------------------------------------------------------------


def test_perpetual_pump_on_cards_in_your_hand_is_held_to_your_hand():
    (effect,) = _read("Creature cards in your hand perpetually get +1/+1.")
    assert effect.kind is EffectKind.PERPETUALLY
    assert effect.targets.zones == frozenset({Zone.HAND})
    assert effect.targets.owner is ControllerRelation.YOU
    (change,) = effect.children
    assert change.kind is EffectKind.MODIFY_PT and change.targets is None


def test_a_random_pick_and_a_chosen_card_are_one_card():
    (random,) = _read('A random nonland card in your hand perpetually gains "This spell costs {1} less to cast."')
    assert "at random" in random.keywords and random.targets.count.constant == 1
    (chosen,) = _read("Choose a nonland card in your hand. It perpetually gains flash.")
    assert "at random" not in chosen.keywords and chosen.targets.count.constant == 1


def test_two_perpetual_changes_in_one_sentence():
    (effect,) = _read('It perpetually gets +1/+1 and perpetually gains "This spell costs {1} less to cast."')
    assert [c.kind for c in effect.children] == [EffectKind.MODIFY_PT, EffectKind.GRANT_ABILITY]


@pytest.mark.parametrize(
    "text",
    [
        "Target creature perpetually gets -2/-0 until end of turn.",
        "creature cards in your graveyard perpetually get +1/+1 for each creature you control.",
        "a random nonland card in that player's hand perpetually gains flash.",
        "permanent cards in your graveyard perpetually gain flying.",
    ],
)
def test_perpetual_shapes_left_unread(text):
    assert _read(text) is None


# -- conjure ------------------------------------------------------------------


def test_conjure_a_named_card_reads_the_whole_printed_name():
    effects = _read("Conjure four cards named Fblthp, the Lost into your library, then shuffle.")
    conjure = effects[0]
    assert conjure.kind is EffectKind.CONJURE
    assert conjured_name(conjure) == "Fblthp, the Lost"
    assert conjure.zone is Zone.LIBRARY and conjure.amount.constant == 4
    assert effects[1].kind is EffectKind.SHUFFLE


def test_conjure_onto_the_battlefield_tapped_and_at_a_library_position():
    (tapped,) = _read("Conjure a card named Stonybrook Schoolmaster onto the battlefield tapped.")
    assert "tapped" in tapped.keywords and tapped.zone is Zone.BATTLEFIELD
    (placed,) = _read("Conjure a card named this into your library seventh from the top.")
    assert placed.targets.source_only and placed.amount2.constant == 7
    assert "duplicate" not in placed.keywords


def test_a_duplicate_is_marked_as_one():
    (effect,) = _read("Conjure a duplicate of it into your hand.")
    assert "duplicate" in effect.keywords and effect.targets.remembered


@pytest.mark.parametrize(
    "text",
    [
        "Conjure a card named Mox Jet into your library.",
        "Conjure a card named Blood Artist onto the battlefield attached to that creature.",
        "Conjure the Power Nine into your library, then shuffle.",
    ],
)
def test_conjure_shapes_left_unread(text):
    assert _read(text) is None


# -- seek ---------------------------------------------------------------------


def test_seek_reads_its_own_opcode_not_a_search():
    (effect,) = _read("Seek two nonland cards.")
    assert effect.kind is EffectKind.SEEK
    assert effect.amount.constant == 2 and effect.targets.zones == frozenset({Zone.LIBRARY})


def test_seek_a_land_and_a_nonland_card_is_two_seeks():
    (effect,) = _read("Seek a land card and a nonland card.")
    assert [c.kind for c in effect.children] == [EffectKind.SEEK, EffectKind.SEEK]


@pytest.mark.parametrize(
    "text",
    [
        "Seek a permanent card with mana value 3 or less.",
        "Seek two creature cards from among the top ten cards of your library, then shuffle.",
        "Seek that many nonland cards.",
    ],
)
def test_seek_shapes_left_unread(text):
    assert _read(text) is None
