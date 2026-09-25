"""Exile costs are paid from the zone they name, with what they name.

Three readings of the same kind of cost were each cheaper than the card:

- "Exile a blue card from your hand" had no payment at all, so Force of Will
  cost 1 life and nothing else, blue card or no blue card.
- "Exile this artifact" was read as exiling a card from the graveyard, so
  Inquisitive Puppet paid with whatever was in the graveyard and stayed in
  play - and City of Shadows' "Exile a creature you control" did the same.
- A keyword's cost was read as its mana symbols only, so Escape exiled
  nothing, and Awaken, whose cost comes after a number, cost nothing at all.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.compile import parse_card
from mtgfish.parser.costs import parse_cost
from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind, _perform
from mtgfish.rules.cr100_game_concepts.cr118_costs import EXILE_ZONES, CostKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
    CastError,
    activate_ability,
    cast_spell,
)
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

YOU = PlayerId(0)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    return table


def _need(box, *names):
    for name in names:
        if box.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")


def _place(box, name, zone, player=0):
    """Place a card and return the object, not the sandbox's state summary."""
    box.put(name, zone, player)
    return next(
        obj
        for obj in reversed(list(box.game.objects.values()))
        if obj.card is not None and obj.card.name == name and obj.zone is Zone[zone.upper()]
    )


def _names(game, object_ids):
    return sorted(game.objects[object_id].card.name for object_id in object_ids)


def _casts(game, obj, *, alternative):
    return [
        action
        for action in legal_actions(game, YOU)
        if action.kind is ActionKind.CAST_SPELL
        and action.source == obj.id
        and (action.alternative_cost >= 0) is alternative
    ]


def _exiling(game, obj):
    """The index of the object's ability whose cost exiles something."""
    abilities = game.characteristics(obj).abilities
    return next(
        index
        for index, ability in enumerate(abilities)
        if any(component.kind in EXILE_ZONES for component in ability.cost.components)
    )


def _offered(game, obj, index):
    return [
        action
        for action in legal_actions(game, YOU)
        if action.kind in (ActionKind.ACTIVATE_ABILITY, ActionKind.ACTIVATE_MANA_ABILITY)
        and action.source == obj.id
        and action.ability_index == index
    ]


def _alternative(box, name, keyword):
    _need(box, name)
    faces = parse_card(box.db.lookup(name)).faces
    return next(
        ability.alternative_cost
        for face in faces
        for ability in face.abilities
        if ability.alternative_cost is not None and ability.alternative_cost.keyword == keyword
    )


def _a_spell_to_counter(box):
    bears = _place(box, "Grizzly Bears", "hand")
    box.give_mana(1)
    (cast,) = _casts(box.game, bears, alternative=False)
    assert _perform(box.game, YOU, cast)


# ---------------------------------------------------------------------------
# Exile a card from your hand
# ---------------------------------------------------------------------------


def test_force_of_will_exiles_another_blue_card_and_pays_life(box):
    _need(box, "Force of Will", "Brainstorm", "Mountain", "Grizzly Bears")
    game = box.game
    player = game.player(YOU)
    _a_spell_to_counter(box)
    force = _place(box, "Force of Will", "hand")
    _place(box, "Mountain", "hand")
    _place(box, "Brainstorm", "hand")
    pool, life = player.mana_pool.total, player.life

    (pitch,) = _casts(game, force, alternative=True)
    assert _perform(game, YOU, pitch)

    assert "Force of Will" in _names(game, game.stack)
    assert player.life == life - 1
    assert player.mana_pool.total == pool, "the alternative cost has no mana in it"
    assert _names(game, player.hand) == ["Mountain"]
    assert _names(game, game.exile) == ["Brainstorm"]


def test_force_of_will_is_not_its_own_blue_card(box):
    """Force of Will is blue, but it is on the stack by the time it pays."""
    _need(box, "Force of Will", "Mountain", "Grizzly Bears")
    game = box.game
    player = game.player(YOU)
    _a_spell_to_counter(box)
    force = _place(box, "Force of Will", "hand")
    _place(box, "Mountain", "hand")
    life = player.life
    assert len(game.characteristics(force).alternative_costs) == 1

    assert not _casts(game, force, alternative=True)
    with pytest.raises(CastError):
        cast_spell(
            game, YOU, Action(ActionKind.CAST_SPELL, source=force.id, alternative_cost=0)
        )
    assert _names(game, player.hand) == ["Force of Will", "Mountain"]
    assert player.life == life
    assert not game.exile


# ---------------------------------------------------------------------------
# Exile a permanent
# ---------------------------------------------------------------------------


def test_exile_this_creature_exiles_the_creature_not_a_graveyard_card(box):
    _need(box, "Inquisitive Puppet", "Island")
    game = box.game
    player = game.player(YOU)
    puppet = _place(box, "Inquisitive Puppet", "battlefield")
    _place(box, "Island", "graveyard")
    index = _exiling(game, puppet)

    (exile,) = _offered(game, puppet, index)
    assert _perform(game, YOU, exile)

    assert _names(game, game.exile) == ["Inquisitive Puppet"]
    assert _names(game, player.graveyard) == ["Island"]
    assert not [o for o in game.permanents(YOU) if o.card is not None]
    box.resolve_top()
    assert [o for o in game.permanents(YOU) if "Human" in game.characteristics(o).subtypes]

    # Paid once, it cannot be paid again: the creature is gone.
    with pytest.raises(CastError):
        activate_ability(
            game,
            YOU,
            Action(ActionKind.ACTIVATE_ABILITY, source=puppet.id, ability_index=index),
        )
    assert _names(game, player.graveyard) == ["Island"]


def test_exile_this_creature_needs_nothing_in_the_graveyard(box):
    """Charged against the graveyard, an empty one hid the ability entirely."""
    _need(box, "Inquisitive Puppet")
    game = box.game
    puppet = _place(box, "Inquisitive Puppet", "battlefield")

    assert _offered(game, puppet, _exiling(game, puppet))


def test_exile_a_creature_you_control_exiles_a_creature(box):
    _need(box, "City of Shadows", "Grizzly Bears", "Island")
    game = box.game
    player = game.player(YOU)
    city = _place(box, "City of Shadows", "battlefield")
    city.tapped = False
    _place(box, "Island", "graveyard")
    index = _exiling(game, city)

    assert not _offered(game, city, index), "no creature to exile"

    _place(box, "Grizzly Bears", "battlefield")
    (exile,) = _offered(game, city, index)
    assert _perform(game, YOU, exile)

    assert _names(game, game.exile) == ["Grizzly Bears"]
    assert _names(game, player.graveyard) == ["Island"]
    box.resolve_top()
    assert city.counter_count("storage") == 1


def test_exile_this_card_from_your_graveyard_is_not_paid_from_play(box):
    """CR 113.6m: a cost that exiles the card from a graveyard works only there."""
    _need(box, "Ghoulcaller's Accomplice", "Island")
    game = box.game
    player = game.player(YOU)
    accomplice = _place(box, "Ghoulcaller's Accomplice", "battlefield")
    _place(box, "Island", "graveyard")
    box.give_mana(4)
    index = _exiling(game, accomplice)

    assert game.characteristics(accomplice).abilities[index].functions_in == {Zone.GRAVEYARD}
    assert not _offered(game, accomplice, index)
    with pytest.raises(CastError):
        activate_ability(
            game,
            YOU,
            Action(ActionKind.ACTIVATE_ABILITY, source=accomplice.id, ability_index=index),
        )
    assert accomplice.zone is Zone.BATTLEFIELD
    assert _names(game, player.graveyard) == ["Island"]


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("{1}, Exile this artifact", CostKind.EXILE_FROM_BATTLEFIELD),
        ("Exile a creature you control", CostKind.EXILE_FROM_BATTLEFIELD),
        ("Exile this card from your graveyard", CostKind.EXILE_FROM_GRAVEYARD),
        ("Exile a blue card from your hand", CostKind.EXILE_FROM_HAND),
    ],
)
def test_an_exile_cost_names_its_zone(text, kind):
    cost, reason = parse_cost(text)
    assert cost is not None, reason
    assert cost.components[-1].kind is kind


def test_an_exile_cost_from_a_zone_nothing_pays_from_is_unreadable():
    """Nivmagus Elemental's "Exile an instant or sorcery spell you control"."""
    cost, reason = parse_cost("Exile an instant or sorcery spell you control")
    assert cost is None and reason


# ---------------------------------------------------------------------------
# Keyword costs
# ---------------------------------------------------------------------------


def test_escape_exiles_the_other_cards_it_names(box):
    _need(box, "Fruit of Tizerus", "Island")
    game = box.game
    player = game.player(YOU)
    fruit = _place(box, "Fruit of Tizerus", "graveyard")
    for _ in range(4):
        _place(box, "Island", "graveyard")
    box.give_mana(4)
    pool = player.mana_pool.total

    (escape,) = _casts(game, fruit, alternative=True)
    assert _perform(game, YOU, escape)

    assert _names(game, game.stack) == ["Fruit of Tizerus"]
    assert _names(game, game.exile) == ["Island"] * 3
    assert _names(game, player.graveyard) == ["Island"]
    assert player.mana_pool.total == pool - 4


def test_escape_is_not_offered_without_enough_other_cards(box):
    """Fruit of Tizerus and two Islands make three cards, but only two others."""
    _need(box, "Fruit of Tizerus", "Island")
    game = box.game
    player = game.player(YOU)
    fruit = _place(box, "Fruit of Tizerus", "graveyard")
    for _ in range(2):
        _place(box, "Island", "graveyard")
    box.give_mana(4)
    pool = player.mana_pool.total
    assert [a.keyword for a in game.characteristics(fruit).alternative_costs] == ["Escape"]

    assert not _casts(game, fruit, alternative=True)
    with pytest.raises(CastError):
        cast_spell(
            game, YOU, Action(ActionKind.CAST_SPELL, source=fruit.id, alternative_cost=0)
        )
    assert _names(game, player.graveyard) == ["Fruit of Tizerus", "Island", "Island"]
    assert player.mana_pool.total == pool
    assert not game.exile


def test_escape_reads_the_exile_as_part_of_its_cost(box):
    alternative = _alternative(box, "Uro, Titan of Nature's Wrath", "Escape")
    mana, exile = alternative.cost.components
    assert str(mana.mana) == "{G}{G}{U}{U}"
    assert exile.kind is CostKind.EXILE_FROM_GRAVEYARD
    assert exile.filter.other_than_source
    assert exile.amount.constant == 5


def test_an_unreadable_cost_after_the_mana_fails_the_whole_cost(box):
    """Nethergoyf's escape exile is beyond the grammar; its mana alone is not the cost."""
    alternative = _alternative(box, "Nethergoyf", "Escape")
    assert alternative.cost.is_unparsed


def test_text_after_the_mana_that_is_not_a_cost_leaves_the_mana(box):
    """"Prototype {1}{B} - 1/1": the rest is a size, not something to pay.

    Prototype is not an alternative cost (CR 718.3), so the cost sits on the
    keyword's own ability rather than on an ``AlternativeCost``.
    """
    _need(box, "Goring Warplow")
    faces = parse_card(box.db.lookup("Goring Warplow")).faces
    prototype = next(
        ability
        for face in faces
        for ability in face.abilities
        if ability.keyword == "Prototype"
    )
    assert prototype.alternative_cost is None
    assert [c.kind for c in prototype.cost.components] == [CostKind.MANA]
    assert str(prototype.cost.mana_component) == "{1}{B}"


def test_awaken_reads_the_cost_after_its_number(box):
    alternative = _alternative(box, "Ondu Rising", "Awaken")
    assert str(alternative.cost.mana_component) == "{4}{W}"


def test_awaken_is_charged_and_not_offered_without_the_mana(box):
    _need(box, "Ondu Rising", "Plains")
    game = box.game
    player = game.player(YOU)
    rising = _place(box, "Ondu Rising", "hand")
    _place(box, "Plains", "battlefield")

    assert not _casts(game, rising, alternative=True), "one land does not make {4}{W}"

    box.give_mana(1)
    pool = player.mana_pool.total
    (awaken,) = _casts(game, rising, alternative=True)
    assert _perform(game, YOU, awaken)
    assert _names(game, game.stack) == ["Ondu Rising"]
    assert player.mana_pool.total == pool - 5


# ---------------------------------------------------------------------------
# Costs with no payment branch at all
# ---------------------------------------------------------------------------


def test_exile_the_top_card_of_your_library_exiles_it(box):
    """EXILE_FROM_LIBRARY had no branch in payment, so Royal Herbalist's cost was free."""
    _need(box, "Royal Herbalist", "Island", "Mountain")
    game = box.game
    player = game.player(YOU)
    herbalist = _place(box, "Royal Herbalist", "battlefield")
    box.give_mana(2)
    index = _exiling(game, herbalist)

    assert not _offered(game, herbalist, index), "an empty library pays nothing"

    _place(box, "Island", "library")
    _place(box, "Mountain", "library")
    top, below = (game.objects[i].card.name for i in player.library[:2])
    (exile,) = _offered(game, herbalist, index)
    assert _perform(game, YOU, exile)

    assert _names(game, game.exile) == [top]
    assert _names(game, player.library) == [below]


def test_the_untap_symbol_needs_a_tapped_source_and_untaps_it(box):
    """{Q} had no branch either, so Order of Whiteclay untapped nothing to pay it."""
    _need(box, "Order of Whiteclay", "Grizzly Bears")
    game = box.game
    order = _place(box, "Order of Whiteclay", "battlefield")
    _place(box, "Grizzly Bears", "graveyard")
    box.give_mana(3)
    index = next(
        i
        for i, ability in enumerate(game.characteristics(order).abilities)
        if any(c.kind is CostKind.UNTAP_SELF for c in ability.cost.components)
    )

    order.tapped = False
    assert not _offered(game, order, index), "an untapped creature cannot pay {Q}"

    order.tapped, order.summoning_sick = True, True
    assert not _offered(game, order, index), "CR 302.6 applies to {Q} as to {T}"

    order.summoning_sick = False
    (untap,) = _offered(game, order, index)
    assert _perform(game, YOU, untap)
    assert not order.tapped
