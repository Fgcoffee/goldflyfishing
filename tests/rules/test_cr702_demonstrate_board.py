"""Demonstrate (CR 702.144a): when you cast the spell you may copy it, and
if you do, an opponent you choose copies it too; each may choose new targets
for their copy. Excavation Technique cast from its parsed text."""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.gameobject import ObjectKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0, P1 = PlayerId(0), PlayerId(1)
TECHNIQUE = "Excavation Technique"
TARGET = "Grizzly Bears"


class Demonstrates(PassiveOpponent):
    """Says yes to the optional copy, and names the opponent it is offered."""

    def __init__(self) -> None:
        self.offered: tuple = ()

    def choose_optional(self, game, player, effect):
        return True

    def choose_copying_opponent(self, game, player, spell, opponents):
        self.offered = opponents
        return opponents[0]


class Retargets(PassiveOpponent):
    def __init__(self, to: int) -> None:
        self.to = to

    def choose_new_targets(self, game, player, copy, current, candidates):
        return ((self.to,),)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    for name in (TECHNIQUE, TARGET):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def bears(box, player):
    return [
        o.id for o in box.game.objects.values()
        if o.is_live and o.card is not None and o.card.name == TARGET
        and o.zone is Zone.BATTLEFIELD and o.controller == player
    ]


def cast_at(box, target):
    box.put(TECHNIQUE, "hand", 0)
    box.give_mana(5)
    (action,) = [
        a for a in box.legal(0) if a["name"] == TECHNIQUE and a["kind"].startswith("CAST")
    ]
    state = box.perform(action["index"], targets=[[target]])
    assert not state.get("error"), state.get("error")
    original = box.game.objects[box.game.stack[-1]]
    box.settle()
    # The demonstrate trigger is above the spell (CR 113.6k: it triggers on
    # the stack as the spell is cast).
    assert len(box.game.stack) == 2
    box.resolve_top()
    return original


def copies(box):
    return [
        box.game.objects[i] for i in box.game.stack
        if box.game.objects[i].kind is ObjectKind.COPY
    ]


def test_the_caster_and_the_chosen_opponent_each_copy_it(box):
    box.put(TARGET, "battlefield", 1)
    box.put(TARGET, "battlefield", 0)
    (theirs,) = bears(box, P1)
    (mine,) = bears(box, P0)
    caster = Demonstrates()
    box.game.agents[0] = caster
    box.game.agents[1] = Retargets(mine)
    original = cast_at(box, theirs)
    made = copies(box)
    assert sorted(int(c.controller) for c in made) == [0, 1]
    assert caster.offered == (P1,)
    by = {int(c.controller): c for c in made}
    assert by[0].targets == ((theirs,),)
    # CR 707.10c: the opponent's copy is theirs to aim.
    assert by[1].targets == ((mine,),)
    assert original.targets == ((theirs,),)
    # The opponent's copy is on top, so it resolves first.
    assert box.game.stack[-1] == by[1].id


def test_declining_makes_no_copy(box):
    """Control: no copy for anyone when the caster does not copy it."""
    box.put(TARGET, "battlefield", 1)
    (theirs,) = bears(box, P1)
    original = cast_at(box, theirs)
    assert copies(box) == []
    assert box.game.stack == [original.id]
