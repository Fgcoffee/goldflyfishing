"""An "until" exile and the effect that ends it (CR 610.3).

"Exile target creature until this creature leaves the battlefield" is two
one-shot effects: one now, and a second immediately after the named event that
puts the card back. The parser read the duration and the executor dropped it,
so every Banisher Priest exiled for good.

The abilities here are hand-built: what is under test is the rule, not any
card's wording.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, TriggerCondition
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import CardType, Duration, Zone
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ObjectFilter

CREATURES = ObjectFilter(types_all=CardType.CREATURE)

EXILE_UNTIL = Effect(
    EffectKind.EXILE,
    targets=CREATURES,
    is_targeted=True,
    duration=int(Duration.WHILE_SOURCE_PERSISTS),
    text="exile target creature until this leaves the battlefield",
)


def banisher() -> Ability:
    """"When this creature enters, exile target creature until it leaves." """
    return Ability.triggered(
        TriggerCondition(
            event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
            subject=ObjectFilter(source_only=True),
            text="when this creature enters",
        ),
        EXILE_UNTIL,
        text="When this creature enters, exile target creature until it leaves.",
    )


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.scripts.add("Banisher Priest", banisher())
    return board


def _arrive(board, controller: int = 0):
    """Put the exiling permanent onto the battlefield and let it trigger."""
    priest = board.play("Banisher Priest", controller=controller)
    board.game.emit(
        Event(
            EventKind.ENTERS_BATTLEFIELD,
            object_id=priest.id,
            player=PlayerId(controller),
        )
    )
    board.settle()
    board.resolve_stack()
    return priest


def test_the_exiled_creature_comes_back_when_the_source_leaves(board):
    """CR 610.3: the second one-shot effect returns it to the battlefield."""
    game = board.game
    victim = board.play("Grizzly Bears", controller=1)
    priest = _arrive(board)

    assert not victim.is_live, "the creature was never exiled"
    assert "Grizzly Bears" not in board.alive(1)

    actions.destroy(game, priest)
    board.settle()
    board.resolve_stack()

    assert "Grizzly Bears" in board.alive(1)


def test_it_returns_under_its_owners_control(board):
    """CR 610.3c: to its owner, not to whoever exiled it."""
    game = board.game
    board.play("Grizzly Bears", controller=1)
    priest = _arrive(board, controller=0)

    actions.destroy(game, priest)
    board.settle()
    board.resolve_stack()

    assert "Grizzly Bears" in board.alive(1)
    assert "Grizzly Bears" not in board.alive(0)


def test_an_exile_with_no_duration_is_permanent(board):
    """The control: an ordinary exile stays exiled."""
    game = board.game
    board.scripts.add(
        "Banisher Priest",
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
                subject=ObjectFilter(source_only=True),
                text="when this creature enters",
            ),
            Effect(
                EffectKind.EXILE,
                targets=CREATURES,
                is_targeted=True,
                text="exile target creature",
            ),
            text="When this creature enters, exile target creature.",
        ),
    )
    board.play("Grizzly Bears", controller=1)
    priest = _arrive(board)

    actions.destroy(game, priest)
    board.settle()
    board.resolve_stack()

    assert "Grizzly Bears" not in board.alive(1)


def test_nothing_moves_if_the_source_has_already_gone(board):
    """CR 610.3a and 610.3b: the named event happened first, so the creature
    is never exiled in the first place."""
    game = board.game
    victim = board.play("Grizzly Bears", controller=1)
    priest = board.play("Banisher Priest", controller=0)
    game.emit(
        Event(EventKind.ENTERS_BATTLEFIELD, object_id=priest.id, player=PlayerId(0))
    )
    board.settle()
    # The trigger is on the stack; the permanent dies in response.
    actions.destroy(game, priest)
    board.resolve_stack()

    assert victim.is_live
    assert victim.zone is Zone.BATTLEFIELD
    assert "Grizzly Bears" in board.alive(1)


def test_another_permanent_leaving_does_not_bring_it_back(board):
    """The waiting effect is about one permanent, not about any of them."""
    game = board.game
    board.play("Grizzly Bears", controller=1)
    _arrive(board)
    bystander = board.play("Sol Ring", controller=0)

    actions.destroy(game, bystander)
    board.settle()
    board.resolve_stack()

    assert "Grizzly Bears" not in board.alive(1)

