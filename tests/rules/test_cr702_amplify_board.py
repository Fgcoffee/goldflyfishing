"""Amplify N (CR 702.38a): as the creature enters, its controller reveals
any number of cards from their hand that share a creature type with it, and
it enters with N +1/+1 counters for each. Canopy Crawler (a Beast with
amplify 1) cast from its parsed text."""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)
CRAWLER = "Canopy Crawler"
BEASTS = ("Glowering Rogon", "Feral Throwback")
NOT_A_BEAST = "Grizzly Bears"


class RevealsOne(PassiveOpponent):
    def __init__(self) -> None:
        self.offered: tuple = ()

    def choose_amplify_reveals(self, game, player, obj, eligible):
        self.offered = eligible
        return eligible[:1]


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    for name in (CRAWLER, *BEASTS, NOT_A_BEAST):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def cast_crawler(box):
    box.put(CRAWLER, "hand", 0)
    box.give_mana(5)
    (action,) = [a for a in box.legal(0) if a["name"] == CRAWLER and a["kind"].startswith("CAST")]
    state = box.perform(action["index"])
    assert not state.get("error"), state.get("error")
    box.resolve_top()
    (crawler,) = [
        o for o in box.game.objects.values()
        if o.is_live and o.card is not None and o.card.name == CRAWLER
    ]
    assert crawler.zone is Zone.BATTLEFIELD
    return crawler


def test_a_counter_for_each_beast_revealed(box):
    for name in (*BEASTS, NOT_A_BEAST):
        box.put(name, "hand", 0)
    assert cast_crawler(box).counter_count("+1/+1") == 2


def test_no_beast_in_hand_no_counters(box):
    """Control: the counters come from the reveal, not from the keyword alone."""
    box.put(NOT_A_BEAST, "hand", 0)
    assert cast_crawler(box).counter_count("+1/+1") == 0


def test_the_reveal_is_a_choice(box):
    """'Any number': the player may reveal fewer than they could."""
    for name in BEASTS:
        box.put(name, "hand", 0)
    agent = RevealsOne()
    box.game.agents[0] = agent
    assert cast_crawler(box).counter_count("+1/+1") == 1
    names = sorted(box.game.objects[i].card.name for i in agent.offered)
    assert names == sorted(BEASTS)


def test_amplify_n_is_n_counters_per_card(box):
    """Kilnmouth Dragon, amplify 3: three counters for each Dragon revealed."""
    for name in ("Kilnmouth Dragon", "Shivan Dragon"):
        if box.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    box.put("Shivan Dragon", "hand", 0)
    box.put("Kilnmouth Dragon", "hand", 0)
    box.give_mana(7)
    (action,) = [
        a for a in box.legal(0)
        if a["name"] == "Kilnmouth Dragon" and a["kind"].startswith("CAST")
    ]
    assert not box.perform(action["index"]).get("error")
    box.resolve_top()
    (dragon,) = [
        o for o in box.game.objects.values()
        if o.is_live and o.card is not None and o.card.name == "Kilnmouth Dragon"
    ]
    assert dragon.zone is Zone.BATTLEFIELD
    assert dragon.counter_count("+1/+1") == 3
