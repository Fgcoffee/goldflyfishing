""""When you cast this spell" triggers on the stack (CR 113.6k, 601.2i):
Wretched Gryff from its parsed text."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform
from mtgfish.rules.kernel.enums import Color
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    for name in ("Wretched Gryff", "Island"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def test_casting_the_gryff_draws_a_card(box):
    game = box.game
    box.put("Island", "library", 0, count=3)
    box.put("Wretched Gryff", "hand", 0)
    game.player(P0).mana_pool.add(ManaKind(Color.BLUE), 8)
    hand = len(game.player(P0).hand)
    action = next(
        a for a in legal_actions(game, P0)
        if a.kind.name.startswith("CAST") and game.objects[a.source].card.name == "Wretched Gryff"
        and a.alternative_cost < 0
    )
    assert _perform(game, P0, replace(action))
    box.settle()
    for _ in range(5):
        if not game.stack:
            break
        box.resolve_top()
    # The Gryff left the hand and the trigger drew one.
    assert len(game.player(P0).hand) == hand
