"""Whether a modal spell in hand is castable (CR 700.2a, 601.2c).

CR 601.2c says a spell needing a target cannot be cast without a legal one.
For a modal spell, which targets it needs depends on which mode is chosen -
and no mode has been chosen while the card is still in hand. CR 700.2a
settles it: a mode whose targets cannot be supplied cannot be chosen, so the
spell is castable as long as *some* mode is.

Judging it by every mode at once makes a charm uncastable whenever any single
one of its options has nothing to point at, which is most of the time.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import CardType, Phase, Step
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.rules.kernel.query import YOU, ObjectFilter, Value

CREATURES = ObjectFilter(types_all=CardType.CREATURE)
ARTIFACTS = ObjectFilter(types_all=CardType.ARTIFACT)

KILL_CREATURE = Effect(
    EffectKind.DESTROY, targets=CREATURES, is_targeted=True, text="destroy target creature"
)
KILL_ARTIFACT = Effect(
    EffectKind.DESTROY, targets=ARTIFACTS, is_targeted=True, text="destroy target artifact"
)
DRAW = Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1), text="draw a card")


def choose_one(*modes: Effect) -> Effect:
    return Effect(
        EffectKind.CHOOSE_MODE,
        children=modes,
        amount=Value.of(1),
        text="choose one",
    )


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.game.active_player = PlayerId(0)
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    _give_mana(board, 0, 5)
    return board


def _give_mana(board, player: int, amount: int, color: str = "R") -> None:
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.kernel.enums import LETTER_TO_COLOR

    board.game.player(PlayerId(player)).mana_pool.add(ManaKind(LETTER_TO_COLOR[color]), amount)


def _can_cast(board, card) -> bool:
    return any(
        action.kind is ActionKind.CAST_SPELL and action.source == card.id
        for action in legal_actions(board.game, PlayerId(0))
    )


def test_a_charm_is_castable_when_only_one_mode_has_a_target(board):
    """CR 700.2a: the artifact mode simply cannot be chosen, and that is not
    a reason the spell cannot be cast."""
    board.scripts.add(
        "Shock", Ability.spell(choose_one(KILL_CREATURE, KILL_ARTIFACT), text="choose one")
    )
    card = board.hand("Shock", controller=0)
    board.play("Grizzly Bears", controller=1)  # a creature, but no artifact

    assert _can_cast(board, card)


def test_a_charm_is_not_castable_when_no_mode_has_a_target(board):
    """CR 700.2b: if no mode can be chosen there is nothing to cast."""
    board.scripts.add(
        "Shock", Ability.spell(choose_one(KILL_CREATURE, KILL_ARTIFACT), text="choose one")
    )
    card = board.hand("Shock", controller=0)
    # An empty board: neither mode can be supplied a target.

    assert not _can_cast(board, card)


def test_a_mode_that_needs_no_target_keeps_the_spell_castable(board):
    """A mode with no target is always choosable, so the spell always is."""
    board.scripts.add(
        "Shock", Ability.spell(choose_one(KILL_ARTIFACT, DRAW), text="choose one")
    )
    card = board.hand("Shock", controller=0)

    assert _can_cast(board, card)


def test_a_non_modal_spell_still_needs_its_target(board):
    """The control: CR 601.2c is unchanged for everything that is not modal."""
    board.scripts.add("Shock", Ability.spell(KILL_ARTIFACT, text="destroy target artifact"))
    card = board.hand("Shock", controller=0)

    assert not _can_cast(board, card)


def test_a_non_modal_spell_is_castable_with_a_target(board):
    board.scripts.add("Shock", Ability.spell(KILL_CREATURE, text="destroy target creature"))
    card = board.hand("Shock", controller=0)
    board.play("Grizzly Bears", controller=1)

    assert _can_cast(board, card)
