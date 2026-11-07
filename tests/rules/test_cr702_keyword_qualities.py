"""A keyword whose quality was not read is not played as if it had none:
"Affinity for Slivers" does not count artifacts (CR 702.41a), and "Equip
legendary creature {3}" is not a free equip of any creature (CR 702.6c)."""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.kernel.enums import Color
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def need(box, *names):
    for name in names:
        if box.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")


def test_affinity_for_slivers_is_not_cheapened_by_artifacts(box):
    need(box, "Thrumming Hivepool", "Ornithopter")
    box.put("Ornithopter", "battlefield", 0, count=6)
    box.put("Thrumming Hivepool", "hand", 0)  # {6}
    box.game.player(P0).mana_pool.add(ManaKind(Color.NONE), 1)
    game = box.game
    assert not any(
        a.kind.name.startswith("CAST") and game.objects[a.source].card.name == "Thrumming Hivepool"
        for a in legal_actions(game, P0)
    )


def test_affinity_for_artifacts_still_counts_them(box):
    """Control: Frogmite ({4}, affinity for artifacts) with four artifacts."""
    need(box, "Frogmite", "Ornithopter")
    box.put("Ornithopter", "battlefield", 0, count=4)
    box.put("Frogmite", "hand", 0)
    game = box.game
    assert any(
        a.kind.name.startswith("CAST") and game.objects[a.source].card.name == "Frogmite"
        for a in legal_actions(game, P0)
    )


def test_an_equip_whose_quality_and_cost_were_lost_is_not_free(box):
    need(box, "Blackblade Reforged", "Grizzly Bears")
    box.put("Blackblade Reforged", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 0)
    game = box.game
    blade = next(o for o in game.objects.values()
                 if o.is_live and o.card is not None and o.card.name == "Blackblade Reforged")
    assert not [a for a in legal_actions(game, P0)
                if a.kind.name == "ACTIVATE_ABILITY" and a.source == blade.id]
    game.player(P0).mana_pool.add(ManaKind(Color.NONE), 7)
    assert [a for a in legal_actions(game, P0)
            if a.kind.name == "ACTIVATE_ABILITY" and a.source == blade.id], "Equip {7} still works"
