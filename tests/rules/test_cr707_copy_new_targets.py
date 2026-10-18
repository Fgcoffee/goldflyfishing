"""The controller of a copy of a spell may choose new targets for it
(CR 707.10c), as replicate, storm and "copy target spell" all allow."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform
from mtgfish.rules.cr700_additional_rules.cr707_faces import copy_spell
from mtgfish.rules.kernel.enums import Color
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)


class Retargets(PassiveOpponent):
    def __init__(self, to: int) -> None:
        self.to = to
        self.offered: list = []

    def choose_new_targets(self, game, player, copy, current, candidates):
        self.offered = candidates
        return ((self.to,),)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    for name in ("Lightning Bolt", "Grizzly Bears"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def bolt_the_first_bear(box):
    box.put("Grizzly Bears", "battlefield", 1)
    box.put("Grizzly Bears", "battlefield", 1)
    box.put("Lightning Bolt", "hand", 0)
    first, second = sorted(
        obj.id
        for obj in box.game.objects.values()
        if obj.is_live and obj.card is not None and obj.card.name == "Grizzly Bears"
    )
    box.game.player(P0).mana_pool.add(ManaKind(Color.RED), 1)
    action = next(
        a
        for a in legal_actions(box.game, P0)
        if a.kind.name.startswith("CAST")
        and box.game.objects[a.source].card.name == "Lightning Bolt"
    )
    assert _perform(box.game, P0, replace(action, targets=((first,),)))
    spell = box.game.objects[box.game.stack[-1]]
    return spell, first, second


def test_the_copy_may_be_aimed_elsewhere(box):
    spell, first, second = bolt_the_first_bear(box)
    box.game.agents[0] = Retargets(second)
    copy = copy_spell(box.game, spell, P0, may_choose_new_targets=True)
    assert copy.targets == ((second,),)
    assert spell.targets == ((first,),)
    assert second in box.game.agents[0].offered[0]


def test_without_a_new_choice_the_copy_keeps_the_targets(box):
    spell, first, _ = bolt_the_first_bear(box)
    copy = copy_spell(box.game, spell, P0)
    assert copy.targets == ((first,),)


def test_a_choice_that_is_not_legal_is_not_taken(box):
    spell, first, _ = bolt_the_first_bear(box)
    box.game.agents[0] = Retargets(spell.id)  # a spell is no creature or player
    copy = copy_spell(box.game, spell, P0, may_choose_new_targets=True)
    assert copy.targets[0] and copy.targets[0][0] != spell.id


def test_a_copy_whose_text_does_not_say_so_keeps_the_targets(box):
    """CR 707.10c is permission an effect states; 707.10d-e copies are fixed."""
    spell, first, second = bolt_the_first_bear(box)
    box.game.agents[0] = Retargets(second)
    copy = copy_spell(box.game, spell, P0)
    assert copy.targets == ((first,),)


class WantsTheCopyOn(PassiveOpponent):
    """Says yes to "you may", and aims a copy at one creature."""

    def __init__(self, to: int) -> None:
        self.to = to

    def choose_optional(self, game, player, effect):
        return True

    def choose_new_targets(self, game, player, copy, current, candidates):
        return ((self.to,),)


def test_twincasts_rider_lets_the_copy_be_aimed_elsewhere(box):
    """The parsed "Copy target instant or sorcery spell. You may choose new
    targets for the copy." The second sentence is about the copy (CR 707.10c)
    and reaches the controller's choice; the original keeps its target."""
    if box.db.lookup("Twincast") is None or box.db.lookup("Hill Giant") is None:
        pytest.skip("Twincast or Hill Giant is not in this card pool")
    game = box.game
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Hill Giant", "battlefield", 1)
    box.put("Lightning Bolt", "hand", 1)
    box.put("Twincast", "hand", 0)
    bears = next(o for o in game.objects.values() if o.is_live and o.card.name == "Grizzly Bears")
    giant = next(o for o in game.objects.values() if o.is_live and o.card.name == "Hill Giant")
    game.player(PlayerId(1)).mana_pool.add(ManaKind(Color.RED), 1)
    game.player(P0).mana_pool.add(ManaKind(Color.BLUE), 2)

    def cast(player, name, target):
        action = next(
            a
            for a in legal_actions(game, player)
            if a.kind.name.startswith("CAST") and game.objects[a.source].card.name == name
        )
        assert _perform(game, player, replace(action, targets=((target,),)))
        return game.objects[game.stack[-1]]

    bolt = cast(PlayerId(1), "Lightning Bolt", bears.id)
    game.agents[0] = WantsTheCopyOn(giant.id)
    cast(P0, "Twincast", bolt.id)
    box.resolve_top()  # Twincast: the copy goes on the stack
    copy = game.objects[game.stack[-1]]
    assert copy.id != bolt.id
    assert copy.targets == ((giant.id,),)
    assert bolt.targets == ((bears.id,),)
