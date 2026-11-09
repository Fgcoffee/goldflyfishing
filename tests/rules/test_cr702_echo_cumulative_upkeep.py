"""Echo and cumulative upkeep from their real oracle text, on a board.

Both were built as an OPTIONAL with nothing to pay and an "otherwise" that
never ran: declining kept the permanent, and so did accepting with no mana
(CR 702.30a, 702.24a). Echo's intervening-if also asked "you control it",
so it charged every upkeep rather than the first.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.actions import attach
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr500_turn_structure.cr500_turn import TurnOptions, _turn_based_actions
from mtgfish.rules.kernel.enums import Color, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0 = PlayerId(0)


class SaysYes(PassiveOpponent):
    def choose_optional(self, game, player, effect):
        return True


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = SaysYes()
    table.game.agents[1] = PassiveOpponent()
    return table


def need(box, *names):
    for name in names:
        if box.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")


def live(box, name):
    """The named card's live permanents."""
    return [o for o in box.game.objects.values()
            if o.is_live and o.zone is Zone.BATTLEFIELD
            and o.card is not None and o.card.name == name]


def place(box, name, *, new=True):
    """Put a permanent down; ``new`` as if it entered during the last turn."""
    box.put(name, "battlefield", 0)
    (obj,) = live(box, name)
    obj.summoning_sick = new
    obj.newly_controlled = new  # CR 702.30a: under its control since before the last turn
    return obj


def mana(box, red=0, blue=0, generic=0):
    pool = box.game.player(P0).mana_pool
    for color, amount in ((Color.RED, red), (Color.BLUE, blue), (Color.NONE, generic)):
        if amount:
            pool.add(ManaKind(color), amount)


def upkeep(box):
    """CR 502, 503: P0's untap step, then the upkeep and its triggers."""
    game = box.game
    game.active_player = P0
    for step in (Step.UNTAP, Step.UPKEEP):
        game.phase, game.step = Phase.BEGINNING, step
        _turn_based_actions(game, step, TurnOptions())
    box.settle()
    for _ in range(10):
        if not game.stack:
            break
        box.resolve_top()
    box.settle()


def test_echo_paid_keeps_it(box):
    need(box, "Stingscourger")
    scourger = place(box, "Stingscourger")
    mana(box, red=1, generic=3)
    upkeep(box)
    assert live(box, "Stingscourger") == [scourger]
    assert box.game.player(P0).mana_pool.total == 0


def test_echo_unpaid_sacrifices_it(box):
    need(box, "Firemaw Kavu")
    place(box, "Firemaw Kavu")
    mana(box, red=1, generic=2)  # {5}{R} asked, four available
    upkeep(box)
    assert not live(box, "Firemaw Kavu")
    assert box.game.player(P0).mana_pool.total == 3


def test_echo_declined_sacrifices_it(box):
    need(box, "Stingscourger")
    box.game.agents[0] = PassiveOpponent()
    place(box, "Stingscourger")
    mana(box, red=1, generic=3)
    upkeep(box)
    assert not live(box, "Stingscourger")


def test_echo_asks_only_once(box):
    """CR 702.30a: controlled since the last upkeep - nothing to pay."""
    need(box, "Stingscourger")
    scourger = place(box, "Stingscourger", new=False)
    upkeep(box)
    assert live(box, "Stingscourger") == [scourger]


def test_echo_paid_once_then_free(box):
    need(box, "Stingscourger")
    scourger = place(box, "Stingscourger")
    mana(box, red=1, generic=3)
    upkeep(box)
    upkeep(box)
    assert live(box, "Stingscourger") == [scourger]


def test_cumulative_upkeep_grows_with_age_counters(box):
    """CR 702.24a: one age counter, {1}; two, {2}; then {3} with two in the
    pool is unpaid and Flow of Maggots goes."""
    need(box, "Flow of Maggots")
    maggots = place(box, "Flow of Maggots", new=False)
    for age, pool in ((1, 1), (2, 2)):
        mana(box, generic=pool)
        upkeep(box)
        assert live(box, "Flow of Maggots") == [maggots], age
        assert maggots.counter_count("age") == age
        assert box.game.player(P0).mana_pool.total == 0
    mana(box, generic=2)
    upkeep(box)
    assert not live(box, "Flow of Maggots")
    assert box.game.player(P0).mana_pool.total == 2


@pytest.mark.parametrize("name", ["Kjeldoran Javelineer", "Flow of Maggots"])
def test_cumulative_upkeep_with_no_mana_sacrifices(box, name):
    need(box, name)
    place(box, name, new=False)
    upkeep(box)
    assert not live(box, name)


def test_cumulative_upkeep_charges_coloured_mana_per_counter(box):
    """Mystic Might, {1}{U}: two age counters ask {2}{U}{U}, not {3}{U} -
    which the pool and the Plains could pay."""
    need(box, "Mystic Might", "Plains")
    box.put("Plains", "battlefield", 0)
    (plains,) = live(box, "Plains")
    might = place(box, "Mystic Might", new=False)
    attach(box.game, might, plains)
    might.add_counters("age", 1)
    mana(box, blue=1, generic=3)
    upkeep(box)
    assert not live(box, "Mystic Might")
    assert box.game.player(P0).mana_pool.total == 4


def test_cumulative_upkeep_coloured_paid(box):
    need(box, "Mystic Might", "Plains")
    box.put("Plains", "battlefield", 0)
    (plains,) = live(box, "Plains")
    might = place(box, "Mystic Might", new=False)
    attach(box.game, might, plains)
    might.add_counters("age", 1)
    mana(box, blue=2, generic=2)
    upkeep(box)
    assert live(box, "Mystic Might") == [might]
    assert might.counter_count("age") == 2
    assert box.game.player(P0).mana_pool.total == 0
