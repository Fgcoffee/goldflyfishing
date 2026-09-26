"""Looking at and revealing the top of a library, and what happens to the pile.

"Look at the top N cards" used to be read as SCRY, and "put one of them into
your hand" / "the rest" as moves of whatever the resolution remembered, with
no filter and no count. These pin the construct: the look is its own opcode,
the parts choose from the pile, and "the rest" only exists beside a pile.
"""

from __future__ import annotations

import pytest

from mtgfish.parser import parse_card
from mtgfish.parser.clauses import parse_effects, pile_references_ok
from mtgfish.parser.tokens import Stream
from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.query import PlayerScope


@pytest.fixture(autouse=True)
def _registry(card_db):
    card_db.registry()


def _read(text):
    stream = Stream.of(text)
    effects = parse_effects(stream)
    assert effects is not None and stream.done, text
    return [node for effect in effects for node in effect.walk()]


def test_looking_is_not_scrying():
    nodes = _read("Look at the top four cards of your library.")
    kinds = [n.kind for n in nodes]
    assert EffectKind.SCRY not in kinds
    assert kinds == [EffectKind.LOOK_AT_TOP]
    assert nodes[0].amount.constant == 4
    assert "reveal" not in nodes[0].keywords


def test_revealing_the_top_is_a_revealed_look():
    (node,) = _read("Reveal the top five cards of your library.")
    assert node.kind is EffectKind.LOOK_AT_TOP
    assert "reveal" in node.keywords


def test_target_players_library_is_targeted():
    (node,) = _read("Look at the top three cards of target player's library.")
    assert node.is_targeted and node.players.scope is PlayerScope.TARGET_PLAYER


def test_each_players_library_is_left_unread():
    stream = Stream.of("Reveal the top card of each player's library.")
    effects = parse_effects(stream)
    assert effects is None or not any(
        n.kind is EffectKind.LOOK_AT_TOP for e in effects for n in e.walk()
    )


def test_parts_choose_from_the_pile_by_count_and_description():
    nodes = _read(
        "Look at the top six cards of your library. Put up to two creature cards "
        "with mana value 3 or less from among them onto the battlefield. Put the "
        "rest on the bottom of your library in a random order."
    )
    put = next(n for n in nodes if n.kind is EffectKind.PUT_ONTO_BATTLEFIELD)
    assert put.targets.from_pile and put.targets.up_to
    assert put.targets.count.constant == 2
    assert put.targets.mana_value is not None
    rest = next(n for n in nodes if n.kind is EffectKind.PUT_ON_LIBRARY)
    assert rest.targets.from_pile and rest.targets.count is None
    assert "bottom" in rest.keywords and "random" in rest.keywords


def test_an_elided_verb_list_is_three_parts():
    nodes = _read(
        "Look at the top three cards of your library. Put one of those cards into "
        "your hand, one on top of your library, and one on the bottom of your library."
    )
    moves = [n for n in nodes if n.targets is not None and n.targets.from_pile]
    assert [m.kind for m in moves] == [
        EffectKind.MOVE_ZONE,
        EffectKind.PUT_ON_LIBRARY,
        EffectKind.PUT_ON_LIBRARY,
    ]
    assert moves[0].zone is Zone.HAND
    assert "bottom" not in moves[1].keywords and "bottom" in moves[2].keywords


def test_reveal_and_put_is_one_choice():
    nodes = _read(
        "Look at the top four cards of your library. You may reveal a creature "
        "card from among them and put it into your hand."
    )
    move = next(n for n in nodes if n.kind is EffectKind.MOVE_ZONE)
    assert "reveal" in move.keywords and move.targets.from_pile
    assert move.targets.count.constant == 1


def test_the_bottom_of_a_library_is_not_the_top():
    (node,) = _read("Put target attacking creature on the bottom of its owner's library.")
    assert node.kind is EffectKind.PUT_ON_LIBRARY and "bottom" in node.keywords


def test_the_rest_without_a_pile_is_refused():
    stream = Stream.of("Put the rest into your graveyard.")
    effects = parse_effects(stream)
    assert effects is not None
    assert not pile_references_ok(effects)


def test_the_rest_on_one_branch_of_if_you_dont_is_refused(card_db):
    card = card_db.lookup("Planar Genesis")
    if card is None:
        pytest.skip("not in pool")
    assert not parse_card(card).fully_parsed


@pytest.mark.parametrize(
    "name", ["Impulse", "Grisly Salvage", "Dig Through Time", "Goblin Ringleader", "Ponder"]
)
def test_pile_cards_read(card_db, name):
    card = card_db.lookup(name)
    if card is None:
        pytest.skip("not in pool")
    parsed = parse_card(card)
    assert parsed.fully_parsed
    kinds = {
        n.kind
        for face in parsed.faces
        for a in face.abilities
        for e in a.effects
        for n in e.walk()
    }
    assert EffectKind.LOOK_AT_TOP in kinds and EffectKind.SCRY not in kinds
