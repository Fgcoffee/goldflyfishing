"""Replacing the number of tokens an effect creates (CR 111.1, 614.1c).

Doubling Season, Parallel Lives and Anointed Procession all say the same
thing: "if an effect would create one or more tokens under your control, it
creates twice that many of those tokens instead". The parser read that
correctly and the replacement system registered it correctly, and the two were
never joined up - ``create_tokens`` looped ``count`` times and never asked.

The only TOKEN_CREATED event was emitted once per token *after* each one was
made, which is the one moment at which changing the number means nothing. So
the replacement matched an event, dutifully recomputed an amount, and threw it
away. Every token doubler in the format was a blank card.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr111_tokens import create_tokens
from mtgfish.rules.cr600_spells_and_abilities.effects import TokenSpec
from mtgfish.rules.kernel.enums import CardType
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.values import Value
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


SOLDIER = TokenSpec(
    types=CardType.CREATURE,
    subtypes=("Soldier",),
    power=Value(constant=1),
    toughness=Value(constant=1),
)


def _tokens_made(box, doublers, count=1, controller=0):
    for name in doublers:
        box.put(name, "battlefield", 0)
    box.game.invalidate_characteristics()
    return len(create_tokens(box.game, SOLDIER, PlayerId(controller), count))


def test_without_a_doubler_the_count_is_what_was_asked_for(box):
    assert _tokens_made(box, [], count=2) == 2


def test_doubling_season_doubles(box):
    assert _tokens_made(box, ["Doubling Season"], count=2) == 4


def test_anointed_procession_doubles(box):
    assert _tokens_made(box, ["Anointed Procession"], count=1) == 2


def test_two_doublers_quadruple(box):
    """CR 614.5: each applies once, and the second doubles what the first left.

    Four, not three: the replacements compose rather than adding their
    multipliers together.
    """
    assert _tokens_made(box, ["Doubling Season", "Anointed Procession"], count=1) == 4


def test_a_doubler_only_doubles_its_own_controllers_tokens(box):
    """"under your control" - the opponent's tokens are not doubled."""
    assert _tokens_made(box, ["Doubling Season"], count=2, controller=1) == 2


def test_a_doubler_that_has_left_stops_applying(box):
    """CR 611.3: the replacement is derived from live permanents."""
    from mtgfish.rules.kernel.enums import Zone

    box.put("Doubling Season", "battlefield", 0)
    box.game.invalidate_characteristics()

    doubler = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Doubling Season"
    )
    box.game.move_object(doubler, Zone.GRAVEYARD)
    box.game.invalidate_characteristics()

    assert len(create_tokens(box.game, SOLDIER, PlayerId(0), 2)) == 2
