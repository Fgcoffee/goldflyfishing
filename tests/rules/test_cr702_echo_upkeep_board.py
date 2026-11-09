"""Echo and cumulative upkeep on the bench, accepted with nothing to pay:
the permanent is sacrificed; with the mana, it is paid for and stays
(CR 702.30a, 702.24a). Echo's "came under your control since your last
upkeep" is its own record, so the bench's no-summoning-sickness switch,
which clears summoning sickness, does not hide it."""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.kernel.enums import Color
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)


class Accepts(PassiveOpponent):
    def choose_optional(self, game, player, effect):
        return True


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    for name in ("Mogg War Marshal", "Mystic Remora"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = Accepts()
    table.game.agents[1] = PassiveOpponent()
    return table


def live(box, name):
    return [o for o in box.game.objects.values()
            if o.is_live and o.card is not None and o.card.name == name]


@pytest.mark.parametrize("card", ["Mogg War Marshal", "Mystic Remora"])
def test_accepted_with_nothing_to_pay_it_is_sacrificed(box, card):
    assert box.rules.no_summoning_sickness, "the bench default, which hid echo"
    box.put(card, "battlefield", 0)
    box.next_turn()
    (obj,) = live(box, card)
    assert obj.zone.name == "GRAVEYARD"


@pytest.mark.parametrize(("card", "colour", "mana"), [
    ("Mogg War Marshal", Color.RED, 2),
    ("Mystic Remora", Color.BLUE, 1),
])
def test_accepted_and_paid_it_stays_and_the_mana_is_spent(box, card, colour, mana):
    box.put(card, "battlefield", 0)
    box.game.player(P0).mana_pool.add(ManaKind(colour), mana)
    box.next_turn()
    (obj,) = live(box, card)
    assert obj.zone.name == "BATTLEFIELD"
    assert box.game.player(P0).mana_pool.total == 0
