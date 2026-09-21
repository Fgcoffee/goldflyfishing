"""Double-faced cards and copying (CR 707, 712).

Two rules that cut against the grain of the rest of the engine:

* **CR 712.18** - transforming does *not* create a new object, unlike every
  other change of what something is (CR 400.7). Counters, damage, and auras all
  survive.
* **CR 707.2** - a copy takes only copiable values, so a copy of a pumped,
  countered-up creature is a plain copy of the card.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr117_priority import ActionKind
from mtgfish.rules.cr707_faces import (
    can_transform,
    castable_face_indices,
    copy_permanent,
    copy_spell,
    playable_land_face_indices,
    transform,
)
from mtgfish.rules.enums import CardType, Phase, Step, Zone
from mtgfish.rules.ids import PlayerId
from mtgfish.rules.legality import legal_actions


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.game.active_player = PlayerId(0)
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    return board


def give_mana(board, amount: int, color: str = "G", player: int = 0):
    from mtgfish.rules.cr106_mana import ManaKind
    from mtgfish.rules.enums import LETTER_TO_COLOR

    board.game.player(PlayerId(player)).mana_pool.add(
        ManaKind(LETTER_TO_COLOR[color]), amount
    )


# ---------------------------------------------------------------------------
# Transforming (CR 712.9, 712.18)
# ---------------------------------------------------------------------------


def test_a_transforming_card_can_transform(board):
    delver = board.play("Delver of Secrets")
    assert can_transform(board.game, delver)


def test_transforming_swaps_the_face(board):
    delver = board.play("Delver of Secrets")
    assert board.game.characteristics(delver).name == "Delver of Secrets"

    assert transform(board.game, delver)
    assert board.game.characteristics(delver).name == "Insectile Aberration"
    assert board.pt(delver) == (3, 2)


def test_transforming_keeps_the_same_object(board):
    """CR 712.18 - the exception to CR 400.7 that makes every counter survive."""
    delver = board.play("Delver of Secrets")
    original_id = delver.id
    delver.add_counters("+1/+1", 2)
    delver.damage = 1

    transform(board.game, delver)

    assert delver.id == original_id
    assert delver.id in board.game.battlefield
    assert delver.counter_count("+1/+1") == 2
    assert delver.damage == 1
    # 3/2 back face, plus two +1/+1 counters.
    assert board.pt(delver) == (5, 4)


def test_transforming_back_again(board):
    delver = board.play("Delver of Secrets")
    transform(board.game, delver)
    transform(board.game, delver)
    assert board.game.characteristics(delver).name == "Delver of Secrets"


def test_a_single_faced_card_cannot_transform(board):
    assert not can_transform(board.game, board.play("Grizzly Bears"))
    assert not transform(board.game, board.play("Grizzly Bears"))


def test_a_card_in_hand_cannot_transform(board):
    """CR 712.9 applies to permanents."""
    assert not can_transform(board.game, board.hand("Delver of Secrets"))


def test_an_mdfc_with_a_spell_back_does_not_transform(board):
    """CR 712.10: it would have to become a sorcery, so nothing happens.

    Bala Ged Recovery is Sorcery // Land. On the battlefield it is the land
    face, and turning over would make it a sorcery - which cannot be a
    permanent - so it simply does not transform.
    """
    card = board.play("Bala Ged Recovery")
    card.face_index = 1  # The land face, as it would actually be in play.
    board.refresh()
    assert board.game.characteristics(card).name == "Bala Ged Sanctuary"
    assert not can_transform(board.game, card)
    assert not transform(board.game, card)


# ---------------------------------------------------------------------------
# Which face gets played (CR 709.4, 712.11, 712.12)
# ---------------------------------------------------------------------------


def test_a_normal_card_has_one_castable_face(board):
    assert castable_face_indices(board.game, board.hand("Grizzly Bears")) == [0]


def test_a_transforming_card_is_cast_front_face_up(board):
    """CR 712.11."""
    assert castable_face_indices(board.game, board.hand("Delver of Secrets")) == [0]


def test_a_split_card_offers_both_halves(board):
    """CR 709.4."""
    assert castable_face_indices(board.game, board.hand("Fire // Ice")) == [0, 1]


def test_an_mdfc_land_back_is_played_not_cast(board):
    """CR 712.12: the land face is a land drop, not a spell."""
    card = board.hand("Bala Ged Recovery")  # Sorcery // Land
    assert castable_face_indices(board.game, card) == [0]
    assert playable_land_face_indices(board.game, card) == [1]


def test_both_split_halves_appear_as_legal_actions(board):
    """Fire is {1}{R} and Ice is {1}{U}, so each needs its own colour."""
    card = board.hand("Fire // Ice", controller=0)
    give_mana(board, 3, "R")
    give_mana(board, 3, "U")

    casts = [
        a
        for a in legal_actions(board.game, PlayerId(0))
        if a.kind is ActionKind.CAST_SPELL and a.source == card.id
    ]
    assert sorted(a.face_index for a in casts) == [0, 1]


def test_only_the_affordable_half_is_offered(board):
    """Legality is judged on the face's own cost, not the card's front."""
    card = board.hand("Fire // Ice", controller=0)
    give_mana(board, 3, "U")  # Enough for Ice, not for Fire.

    casts = [
        a
        for a in legal_actions(board.game, PlayerId(0))
        if a.kind is ActionKind.CAST_SPELL and a.source == card.id
    ]
    assert [a.face_index for a in casts] == [1]


def test_an_mdfc_land_face_is_offered_as_a_land_drop(board):
    card = board.hand("Bala Ged Recovery", controller=0)
    lands = [
        a
        for a in legal_actions(board.game, PlayerId(0))
        if a.kind is ActionKind.PLAY_LAND and a.source == card.id
    ]
    assert [a.face_index for a in lands] == [1]


def test_playing_an_mdfc_land_face_enters_with_that_face_up(board):
    """CR 712.12."""
    from mtgfish.rules.cr117_priority import Action
    from mtgfish.rules.cr601_casting import play_land

    game = board.game
    card = board.hand("Bala Ged Recovery", controller=0)
    play_land(game, PlayerId(0), Action(ActionKind.PLAY_LAND, source=card.id, face_index=1))

    permanent = next(iter(game.permanents(PlayerId(0))))
    chars = game.characteristics(permanent)
    assert chars.name == "Bala Ged Sanctuary"
    assert chars.has_type(CardType.LAND)


# ---------------------------------------------------------------------------
# Copying permanents (CR 707.2, 707.3)
# ---------------------------------------------------------------------------


def test_a_copy_takes_the_originals_characteristics(board):
    game = board.game
    original = board.play("Serra Angel", controller=1)
    copier = board.play("Grizzly Bears", controller=0)

    copy_permanent(game, copier, original)

    chars = game.characteristics(copier)
    assert chars.name == "Serra Angel"
    assert board.pt(copier) == (4, 4)


def test_a_copy_does_not_take_counters(board):
    """CR 707.2: counters are not a copiable value."""
    game = board.game
    original = board.play("Grizzly Bears", controller=1)
    original.add_counters("+1/+1", 3)
    board.refresh()
    assert board.pt(original) == (5, 5)

    copier = board.play("Hill Giant", controller=0)
    copy_permanent(game, copier, original)

    assert board.pt(copier) == (2, 2), "a plain copy of the card, not of the board state"


def test_a_copy_does_not_take_a_pump_effect(board):
    """CR 613.2: only layer 1 and text-changing are copiable."""
    from mtgfish.rules.abilities import Ability
    from mtgfish.rules.effects import Effect, EffectKind
    from mtgfish.rules.query import ControllerRelation, ObjectFilter, Value

    game = board.game
    board.scripts.add(
        "Glorious Anthem",
        Ability.static(
            Effect(
                EffectKind.MODIFY_PT,
                targets=ObjectFilter(
                    types_all=CardType.CREATURE, controller=ControllerRelation.YOU
                ),
                amount=Value.of(1),
                amount2=Value.of(1),
            ),
            text="Creatures you control get +1/+1.",
        ),
    )
    original = board.play("Grizzly Bears", controller=1)
    board.play("Glorious Anthem", controller=1)
    assert board.pt(original) == (3, 3)

    copier = board.play("Hill Giant", controller=0)
    copy_permanent(game, copier, original)
    assert board.pt(copier) == (2, 2)


def test_the_copy_keeps_its_own_counters(board):
    """The copy's own counters still apply in layer 7d - they were never copied
    away, they are simply its own."""
    game = board.game
    original = board.play("Grizzly Bears", controller=1)
    copier = board.play("Hill Giant", controller=0)
    copier.add_counters("+1/+1", 2)

    copy_permanent(game, copier, original)
    assert board.pt(copier) == (4, 4)


def test_copying_a_transformed_permanent_uses_the_face_that_is_up(board):
    """CR 707.8."""
    game = board.game
    delver = board.play("Delver of Secrets", controller=1)
    transform(game, delver)

    copier = board.play("Grizzly Bears", controller=0)
    copy_permanent(game, copier, delver)

    assert game.characteristics(copier).name == "Insectile Aberration"


def test_a_later_copy_effect_wins(board):
    """Two copy effects in layer 1 resolve by timestamp (CR 613.7)."""
    game = board.game
    first = board.play("Serra Angel", controller=1)
    second = board.play("Hill Giant", controller=1)
    copier = board.play("Grizzly Bears", controller=0)

    copy_permanent(game, copier, first)
    copy_permanent(game, copier, second)

    assert game.characteristics(copier).name == "Hill Giant"


# ---------------------------------------------------------------------------
# Copying spells (CR 707.10)
# ---------------------------------------------------------------------------


def test_copying_a_spell_puts_a_copy_on_the_stack(board):
    from mtgfish.rules.cr117_priority import Action
    from mtgfish.rules.cr601_casting import cast_spell

    game = board.game
    card = board.hand("Grizzly Bears", controller=0)
    give_mana(board, 4, "G")
    spell = cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))

    copy = copy_spell(game, spell)
    assert copy is not None
    assert len(game.stack) == 2
    assert copy.zone is Zone.STACK


def test_a_copied_spell_was_not_cast(board):
    """CR 707.10: nothing that watches for a spell being cast sees a copy.

    This is exactly why storm and cascade copies do not chain into each other.
    """
    from mtgfish.rules.cr117_priority import Action
    from mtgfish.rules.cr601_casting import cast_spell

    game = board.game
    game.log.enabled = True
    card = board.hand("Grizzly Bears", controller=0)
    give_mana(board, 4, "G")
    spell = cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))

    before = len(game.log)
    copy_spell(game, spell)
    emitted = " ".join(e.text for e in game.log.entries[before:])
    assert "CAST_SPELL" not in emitted
    assert "COPIED" in emitted


def test_a_copy_takes_the_originals_choices(board):
    """CR 707.2: modes, targets, and the value of X come along."""
    from mtgfish.rules.cr117_priority import Action
    from mtgfish.rules.cr601_casting import cast_spell

    game = board.game
    card = board.hand("Grizzly Bears", controller=0)
    give_mana(board, 4, "G")
    spell = cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    spell.x_value = 3

    copy = copy_spell(game, spell)
    assert copy.x_value == 3
    assert copy.targets == spell.targets


def test_a_copy_of_a_spell_ceases_to_exist_off_the_stack(board):
    """CR 704.5e: it never reaches the graveyard as a card would."""
    from mtgfish.rules.cr117_priority import Action
    from mtgfish.rules.cr601_casting import cast_spell

    game = board.game
    card = board.hand("Grizzly Bears", controller=0)
    give_mana(board, 4, "G")
    spell = cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    copy = copy_spell(game, spell)
    assert len(game.stack) == 2

    game.move_object(copy, Zone.GRAVEYARD, to_player=PlayerId(0))
    board.sba()

    assert len(game.stack) == 1, "the original is still there"
    assert not game.player(PlayerId(0)).graveyard, "the copy left no card behind"
