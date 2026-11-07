"""Bestow (CR 702.103): cast bestowed, the card is an Aura spell with
enchant creature, a bestowed Aura on the battlefield, and a creature again
once it is no longer attached (702.103e-f). Nyxborn Rollicker, parsed."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.actions import destroy
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform
from mtgfish.rules.kernel.enums import Color, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)
CARD = "Nyxborn Rollicker"  # {R} 1/1; bestow {1}{R}; enchanted creature gets +1/+1


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    for name in (CARD, "Grizzly Bears"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def live(box, name):
    return [o for o in box.game.objects.values()
            if o.is_live and o.card is not None and o.card.name == name]


def bestow_on_the_bears(box, *, resolve=True):
    game = box.game
    box.put("Grizzly Bears", "battlefield", 0)
    box.put(CARD, "hand", 0)
    game.player(P0).mana_pool.add(ManaKind(Color.RED), 2)
    action = next(
        a for a in legal_actions(game, P0)
        if a.kind.name.startswith("CAST") and game.objects[a.source].card.name == CARD
        and a.alternative_cost >= 0
    )
    (bears,) = live(box, "Grizzly Bears")
    assert _perform(game, P0, replace(action, targets=((bears.id,),)))
    (spell,) = live(box, CARD)
    if resolve:
        box.settle()
        while game.stack:
            box.resolve_top()
    return bears, spell


def test_a_bestowed_spell_is_an_aura_and_resolves_attached(box):
    bears, spell = bestow_on_the_bears(box, resolve=False)
    chars = box.game.characteristics(spell)
    assert not chars.is_creature and chars.has_subtype("Aura")
    box.settle()
    while box.game.stack:
        box.resolve_top()
    (aura,) = live(box, CARD)
    assert aura.zone is Zone.BATTLEFIELD and aura.attached_to == bears.id
    assert not box.game.characteristics(aura).is_creature
    assert box.game.characteristics(bears).power == 3


def test_it_becomes_a_creature_when_its_host_dies(box):
    bears, _ = bestow_on_the_bears(box)
    destroy(box.game, bears)
    box.settle()
    (rollicker,) = live(box, CARD)
    assert rollicker.zone is Zone.BATTLEFIELD
    assert box.game.characteristics(rollicker).is_creature
    assert not rollicker.attached_to


def test_with_its_target_gone_it_resolves_as_a_creature(box):
    bears, _ = bestow_on_the_bears(box, resolve=False)
    destroy(box.game, bears)
    box.settle()
    while box.game.stack:
        box.resolve_top()
    (rollicker,) = live(box, CARD)
    assert rollicker.zone is Zone.BATTLEFIELD
    assert box.game.characteristics(rollicker).is_creature
