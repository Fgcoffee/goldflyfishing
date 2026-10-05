"""X in an activation cost: "[-X]" loyalty abilities and "{X}:" abilities.

CR 107.3a: the controller announces X as the ability is activated, and while
it is on the stack any X in its activation cost equals that value. CR 107.3k:
that X is the activation's own, independent of the X its source was cast
with. CR 107.7: a negative loyalty symbol may show an X - "[-X]" removes X
loyalty counters - and CR 606.6 forbids activating it for more loyalty than
the permanent has.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mtgfish.parser.compile import parse_card
from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind, _perform
from mtgfish.rules.cr100_game_concepts.cr118_costs import CostKind
from mtgfish.rules.kernel.enums import Phase, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

YOU = PlayerId(0)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    table.game.active_player = YOU
    table.game.phase = Phase.PRECOMBAT_MAIN
    return table


def _need(box, *names):
    for name in names:
        if box.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")


def _place(box, name, zone="battlefield", player=0):
    before = set(box.game.objects)
    box.put(name, zone, player)
    return next(
        box.game.objects[oid]
        for oid in box.game.objects
        if oid not in before and box.game.objects[oid].card.name == name
    )


def _on_battlefield(box, name):
    return any(
        o.card.name == name for player in (0, 1) for o in box.game.permanents(PlayerId(player))
    )


def _ability_index(box, obj, prefix):
    abilities = box.game.characteristics(obj).abilities
    return next(i for i, a in enumerate(abilities) if a.text.startswith(prefix))


def _walker(box, name, loyalty):
    walker = _place(box, name)
    walker.counters["loyalty"] = loyalty
    box.game.invalidate_characteristics()
    return walker


def test_minus_x_is_read_as_a_loyalty_cost_of_x(card_db):
    card = card_db.lookup("Tezzeret the Seeker")
    if card is None:
        pytest.skip("Tezzeret the Seeker is not in this card pool")
    parsed = parse_card(card)
    ability = next(a for a in parsed.faces[0].abilities if a.text.startswith("-X"))
    assert not ability.unparsed
    assert ability.is_loyalty_ability
    (component,) = ability.cost.components
    assert component.kind is CostKind.LOYALTY
    assert ability.cost.asks_for_x


def test_tezzeret_minus_x_removes_x_and_searches_for_mana_value_x_or_less(box):
    _need(box, "Tezzeret the Seeker", "Mind Stone", "Wurmcoil Engine")
    walker = _walker(box, "Tezzeret the Seeker", 5)
    big = _place(box, "Wurmcoil Engine", "library")
    _place(box, "Mind Stone", "library")
    index = _ability_index(box, walker, "-X")

    action = Action(ActionKind.ACTIVATE_ABILITY, source=walker.id, ability_index=index)
    assert _perform(box.game, YOU, replace(action, x_value=3))
    assert walker.counter_count("loyalty") == 2
    box.resolve_top()
    assert _on_battlefield(box, "Mind Stone")
    assert not _on_battlefield(box, "Wurmcoil Engine")
    assert big.zone is Zone.LIBRARY
    # The announced X belonged to that activation alone (CR 107.3k).
    assert walker.x_value == 0


def test_minus_x_cannot_remove_more_loyalty_than_the_permanent_has(box):
    """CR 606.6."""
    _need(box, "Tezzeret the Seeker", "Mind Stone")
    walker = _walker(box, "Tezzeret the Seeker", 2)
    _place(box, "Mind Stone", "library")
    index = _ability_index(box, walker, "-X")

    action = Action(ActionKind.ACTIVATE_ABILITY, source=walker.id, ability_index=index)
    assert not _perform(box.game, YOU, replace(action, x_value=3))
    assert walker.counter_count("loyalty") == 2
    assert not box.game.stack


def test_minus_x_with_x_of_zero_is_offered(box):
    """CR 107.3a: zero is a legal X, and the only one offered without a choice."""
    _need(box, "Tezzeret the Seeker")
    walker = _walker(box, "Tezzeret the Seeker", 1)
    index = _ability_index(box, walker, "-X")
    assert any(
        a.source == walker.id and a.ability_index == index for a in legal_actions(box.game, YOU)
    )


def test_minus_x_damage_uses_the_announced_x(box):
    _need(box, "Chandra Nalaar", "Grizzly Bears")
    walker = _walker(box, "Chandra Nalaar", 6)
    bears = _place(box, "Grizzly Bears", player=1)
    index = _ability_index(box, walker, "-X")

    action = Action(
        ActionKind.ACTIVATE_ABILITY,
        source=walker.id,
        ability_index=index,
        targets=((bears.id,),),
    )
    assert _perform(box.game, YOU, replace(action, x_value=2))
    assert walker.counter_count("loyalty") == 4
    box.resolve_top()
    assert not _on_battlefield(box, "Grizzly Bears")


def test_minus_x_damage_of_one_does_not_kill_a_two_toughness_creature(box):
    _need(box, "Chandra Nalaar", "Grizzly Bears")
    walker = _walker(box, "Chandra Nalaar", 6)
    bears = _place(box, "Grizzly Bears", player=1)
    index = _ability_index(box, walker, "-X")

    action = Action(
        ActionKind.ACTIVATE_ABILITY,
        source=walker.id,
        ability_index=index,
        targets=((bears.id,),),
    )
    assert _perform(box.game, YOU, replace(action, x_value=1))
    box.resolve_top()
    assert bears.zone is Zone.BATTLEFIELD
    assert bears.damage == 1
    assert walker.counter_count("loyalty") == 5


def test_mana_x_in_an_activation_cost_is_paid_and_used(box):
    """"{X}: Put X tower counters on this enchantment" - X is paid in mana and
    is the number of counters. Before, X was neither charged nor used."""
    _need(box, "Helix Pinnacle")
    pinnacle = _place(box, "Helix Pinnacle")
    box.give_mana(5, 0)
    pool_before = box.game.player(YOU).mana_pool.total
    index = _ability_index(box, pinnacle, "{X}")

    action = Action(ActionKind.ACTIVATE_ABILITY, source=pinnacle.id, ability_index=index)
    assert _perform(box.game, YOU, replace(action, x_value=3))
    box.resolve_top()
    assert pinnacle.counter_count("tower") == 3
    assert box.game.player(YOU).mana_pool.total == pool_before - 3
