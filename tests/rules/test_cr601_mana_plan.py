"""Paying for a spell: which mana abilities to activate (CR 601.2g-h).

The engine used to tap every untapped source in id order, each for its first
mana ability, until the pool covered the cost. A pain land's first ability is
{C}, so a Karplusan Forest never made {R}; "any colour" always made white. The
cast was then rewound as unpayable - with the lands it had tapped left tapped,
though the log said the game state was unchanged - and in replays bots were
seen abandoning casts they plainly had the mana for.

These cards come from the real card pool and the real parser, because the bug
was in how real lands were read, not in anything a scripted ability shows.
"""

from __future__ import annotations

import pytest
from harness import make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaCost
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import CastError, cast_spell
from mtgfish.rules.cr600_spells_and_abilities.mana_plan import plan_payment
from mtgfish.rules.kernel.enums import Phase
from mtgfish.rules.kernel.legality import legal_actions


@pytest.fixture
def board(card_db):
    from mtgfish.parser.compile import OracleAbilities

    board = make_board(card_db, OracleAbilities())
    board.game.phase = Phase.PRECOMBAT_MAIN
    return board


def cast(board, card, controller=0):
    return cast_spell(
        board.game, controller, Action(ActionKind.CAST_SPELL, source=card.id)
    )


def offered(board, card, player=0) -> bool:
    return any(
        a.kind is ActionKind.CAST_SPELL and a.source == card.id
        for a in legal_actions(board.game, player)
    )


def test_a_pain_land_pays_for_the_colour_it_can_make(board):
    """Karplusan Forest's first ability is {C}. A Bolt needs {R}."""
    land = board.play("Karplusan Forest")
    bolt = board.hand("Lightning Bolt")

    assert offered(board, bolt)
    cast(board, bolt)

    assert land.tapped
    assert board.game.player(0).life == 39  # CR 107.4: the pain was paid


def test_a_basic_land_is_tapped_before_a_pain_land(board):
    """Both can pay {R}; only one of them hurts."""
    pain = board.play("Karplusan Forest")
    mountain = board.play("Mountain")
    bolt = board.hand("Lightning Bolt")

    cast(board, bolt)

    assert mountain.tapped and not pain.tapped
    assert board.game.player(0).life == 40


def test_a_modal_land_makes_the_colour_the_cost_needs(board):
    """Jungle Hollow: "Add {B} or {G}". The first mode is {B}."""
    board.play("Jungle Hollow")
    growth = board.hand("Giant Growth")
    board.play("Grizzly Bears")

    assert offered(board, growth)
    cast(board, growth)


def test_any_colour_is_the_colour_the_cost_needs(board):
    """Birds of Paradise made white, whatever the spell wanted."""
    board.play("Birds of Paradise")
    bolt = board.hand("Lightning Bolt")

    cast(board, bolt)


def test_only_what_the_cost_needs_is_tapped(board):
    forests = [board.play("Forest") for _ in range(3)]
    mountain = board.play("Mountain")

    plan = plan_payment(board.game, 0, ManaCost.parse("{R}"))

    assert [option.source for option in plan] == [mountain.id]
    assert not any(forest.tapped for forest in forests)


def test_a_signet_is_paid_for_by_another_source(board):
    """Rakdos Signet: {1}, {T}: Add {B}{R}. Its {1} comes from the Forest."""
    board.play("Forest")
    board.play("Rakdos Signet")
    terminate = board.hand("Terminate")
    board.play("Grizzly Bears", controller=1)

    assert offered(board, terminate)


def test_colours_nothing_can_make_are_not_offered(board):
    """The count of untapped lands used to be the whole affordability check."""
    for _ in range(5):
        board.play("Forest")
    bolt = board.hand("Lightning Bolt")

    assert not offered(board, bolt)


def test_a_rewound_cast_untaps_what_it_tapped(board, monkeypatch):
    """CR 601.2h: an illegal cast is undone, mana abilities included.

    The log said "the game state is unchanged" while the lands tapped on the
    way to finding out stayed tapped, their mana floating until the step
    ended. A plan that taps the wrong land is forced here, since the planner
    no longer makes one.
    """
    from mtgfish.rules.cr600_spells_and_abilities import mana_plan

    forest = board.play("Forest")
    bolt = board.hand("Lightning Bolt")
    wrong = mana_plan.mana_options(board.game, 0)[0][:1]
    monkeypatch.setattr(mana_plan, "plan_payment", lambda *a, **k: wrong)

    with pytest.raises(CastError):
        cast(board, bolt)

    assert not forest.tapped
    assert board.game.player(0).mana_pool.total == 0
