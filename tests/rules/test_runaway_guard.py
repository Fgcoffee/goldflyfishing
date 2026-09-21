"""A cost that consumes nothing is a free ability, and a free ability is a hang.

This is the regression test for the worst bug this project has had. Every
sacrifice-yourself cost - "{T}, Sacrifice this land: Search your library..." on
every fetchland, "Sacrifice this artifact: Draw a card" on Commander's Sphere -
was built with an amount of *zero*. Paying it sacrificed zero permanents, so
the permanent stayed, so the cost could be paid again, so the bot paid it
again: four and a half thousand times in a single step, until the priority loop
hit its iteration cap.

Nothing was wrong with the rules engine and nothing was wrong with the parse.
The ability existed, the opcode was right, the cost was charged - to nobody.
A thousand-game run managed forty games overnight.

Two tests, because the fix has two halves:

* the amount is now one, so the cost is actually paid;
* and if a future clause forgets again, the engine floors a consuming cost at
  one rather than letting it be free.

Plus the backstop: a game that somehow still runs away is stopped and named,
because a simulator that hangs is worse than one that reports a bad number.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.compile import parse_card
from mtgfish.rules.cr118_costs import CONSUMING_COSTS, CostComponent, CostKind, required_amount
from mtgfish.rules.query import ValueKind


@pytest.fixture
def db(card_db):
    card_db.registry()
    return card_db


def _components(db, name):
    card = db.lookup(name)
    if card is None:
        pytest.skip(f"{name} is not in this card pool")
    return [
        component
        for face in parse_card(card).faces
        for ability in face.abilities
        if ability.cost
        for component in ability.cost.components
    ]


@pytest.mark.parametrize(
    "name",
    ["Commander's Sphere", "Evolving Wilds", "Terramorphic Expanse"],
)
def test_a_sacrifice_cost_actually_sacrifices_something(db, name):
    """The cost that hung the simulator, on the three commonest cards with it."""
    sacrifices = [
        component
        for component in _components(db, name)
        if component.kind is CostKind.SACRIFICE
    ]
    assert sacrifices, f"{name} should have a sacrifice cost"
    for component in sacrifices:
        assert component.amount.kind is not ValueKind.CONSTANT or (
            component.amount.constant >= 1
        ), "a cost that consumes nothing is a free ability"


def test_no_played_card_has_a_cost_that_consumes_nothing(db):
    """The whole class, across the cards people actually play.

    Written as a sweep rather than a list of names because the bug was never
    about one card - it was about a default, and a default reaches everything.
    """
    offenders = []
    for card in db.iter_by_play_rate(limit=1500):
        for face in parse_card(card).faces:
            for ability in face.abilities:
                if not ability.cost:
                    continue
                for component in ability.cost.components:
                    if component.kind not in CONSUMING_COSTS:
                        continue
                    amount = component.amount
                    if amount.kind is ValueKind.CONSTANT and amount.constant <= 0:
                        offenders.append(f"{card.name}: {component.kind.name}")
    assert not offenders, offenders[:10]


def test_the_engine_floors_a_consuming_cost_even_if_the_parser_forgets():
    """The second half of the fix: a typo should cost a card, not the run."""
    for kind in CONSUMING_COSTS:
        assert required_amount(CostComponent(kind), 0) == 1

    # Costs that are not about consuming objects keep their zero - "add {0}"
    # and an untapped permanent's tap cost are not quantities of anything.
    assert required_amount(CostComponent(CostKind.MANA), 0) == 0
    assert required_amount(CostComponent(CostKind.TAP_SELF), 0) == 0

    # And a real amount is never inflated.
    assert required_amount(CostComponent(CostKind.SACRIFICE), 3) == 3


def test_a_runaway_game_is_stopped_and_named(card_db):
    """The backstop, exercised by spending the budget directly.

    A game that hits it must end, and must say what it was doing - "the stall
    rate is 3% and the culprit was X" is actionable, and a progress bar that
    never moves is not.
    """
    from mtgfish.rules.cr117_priority import ACTION_BUDGET
    from mtgfish.rules.cr500_turn import TurnOptions, run_game
    from mtgfish.sim.runner import _worker_state, new_game

    card_db.registry()
    decklist = "\n".join(["60 Forest", "39 Grizzly Bears", "", "// Commander", "1 Omnath, Locus of Mana"])
    decks, provider = _worker_state((decklist,) * 4)

    game = new_game(decks, seed=1, ability_provider=provider)
    game.actions_taken = ACTION_BUDGET + 1
    game.runaway = "ACTIVATE_ABILITY Some Broken Card"

    finished = run_game(game, TurnOptions(max_rounds=1))
    assert finished.runaway, "the game should still be marked as a runaway"

    # On the record, not only in the log: the log is off unless a replay asked
    # for it, so a runaway that lived there alone would be invisible in every
    # ordinary run - which is exactly how this went unnoticed for so long.
    from mtgfish.sim.observer import finish, new_record

    record = finish(finished, new_record(0, 1, len(finished.players)))
    assert record.runaway == "ACTIVATE_ABILITY Some Broken Card"
