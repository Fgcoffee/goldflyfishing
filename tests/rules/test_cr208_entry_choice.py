"""Choosing power and toughness as it enters (CR 208.2b)."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ObjectFilter, Value

P0 = PlayerId(0)
SELF = ObjectFilter(source_only=True)


def option(power: int, toughness: int, *keywords: str) -> Effect:
    parts = [
        Effect(
            EffectKind.SET_PT,
            targets=SELF,
            amount=Value.of(power),
            amount2=Value.of(toughness),
            text=f"a {power}/{toughness} creature",
        )
    ]
    if keywords:
        parts.append(Effect(EffectKind.GRANT_ABILITY, targets=SELF, keywords=keywords))
    return Effect(EffectKind.SEQUENCE, children=tuple(parts), text=parts[0].text)


PLASMA = Ability.static(
    Effect(
        EffectKind.ENTERS_AS_CHOICE,
        children=(option(3, 3), option(2, 2, "Flying"), option(1, 6, "Defender")),
        text="as this enters, it becomes your choice of",
    )
)


class Picks:
    def __init__(self, index: int) -> None:
        self.index = index

    def choose_entry_option(self, game, player, obj, options):
        return self.index


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities({"Grizzly Bears": (PLASMA,)}))


def enter(board):
    card = board.hand("Grizzly Bears")
    return board.game.move_object(card, Zone.BATTLEFIELD, to_player=P0)


def test_the_default_choice_is_the_first(board):
    bears = enter(board)
    board.refresh()
    assert board.pt(bears) == (3, 3)


def test_the_controller_chooses(board):
    board.game.agents[P0] = Picks(1)
    bears = enter(board)
    board.refresh()
    assert board.pt(bears) == (2, 2)
    assert "Flying" in board.keywords(bears)


def test_the_choice_ends_when_it_leaves(board):
    """CR 400.7: a new object makes a new choice, or none off the battlefield."""
    board.game.agents[P0] = Picks(2)
    bears = enter(board)
    back = board.game.move_object(bears, Zone.HAND)
    board.refresh()
    assert board.game.characteristics(back).power == 2  # the printed Grizzly Bears


def test_the_choice_is_made_again_as_it_turns_face_up(card_db):
    """CR 208.2b: "as this creature enters or is turned face up"."""
    from dataclasses import replace

    from mtgfish.rules.cr700_additional_rules.cr708_face_down import turn_face_up

    face_up_choice = Ability.static(
        replace(
            PLASMA.effects[0],
            children=(option(5, 1), option(1, 5)),
            also_when_turned_face_up=True,
        )
    )
    board = make_board(card_db, ScriptedAbilities({"Grizzly Bears": (face_up_choice,)}))
    board.game.agents[P0] = Picks(1)
    bears = board.play("Grizzly Bears", face_down=True)
    board.refresh()
    assert board.pt(bears) == (2, 2)  # face down: a 2/2 with no abilities

    assert turn_face_up(board.game, bears)
    board.refresh()
    assert board.pt(bears) == (1, 5)
