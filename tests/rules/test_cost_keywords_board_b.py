"""Cost keywords, proved on a board - the second batch (CR 702).

The companion of ``test_cost_keywords_board_a``: each keyword is cast on a
board and every clause of its rule is checked, not only the price.

Where the engine can play the keyword, the test builds the abilities the rule
describes by hand - the shape a builder should produce - and plays them.
Where the keyword builder wires something else, a ``xfail(strict)`` test
builds the keyword with ``build()`` and states what the rule requires; it
starts passing (and so failing as an xfail) once the builder is right.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaCost, ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import (
    AdditionalCost,
    AlternativeCost,
    Cost,
    CostComponent,
    CostKind,
)
from mtgfish.rules.cr600_spells_and_abilities.abilities import (
    Ability,
    AbilityKind,
    TriggerCondition,
)
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind, TokenSpec
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.kernel.enums import CardType, Color, Phase, Step, Zone
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.rules.kernel.query import (
    YOU,
    Condition,
    ConditionKind,
    ObjectFilter,
    Value,
)

P0 = PlayerId(0)
P1 = PlayerId(1)

GAIN_1 = Effect(EffectKind.GAIN_LIFE, players=YOU, amount=Value.of(1), text="gain 1 life")
SELF = ObjectFilter(source_only=True)
STACK_AND_HAND = frozenset({Zone.HAND, Zone.STACK})


def mana(text: str) -> Cost:
    return Cost((CostComponent(CostKind.MANA, mana=ManaCost.parse(text)),))


def keyword(name: str, cost: str | None = "{1}", **kw) -> tuple[Ability, ...]:
    if cost is not None:
        kw["cost"] = mana(cost)
    return build(KeywordInstance(name, **kw))


def optional_cost(name: str, cost: Cost) -> Ability:
    """An optional additional cost (CR 118.8b), as a keyword states it."""
    return Ability(
        AbilityKind.STATIC,
        keyword=name,
        additional_cost=AdditionalCost(cost=cost, optional=True, keyword=name, text=name),
        functions_in=STACK_AND_HAND,
        text=name,
    )


def alternative_cost(name: str, cost: Cost, condition=None) -> Ability:
    """A cost paid rather than the mana cost (CR 118.9), from hand."""
    return Ability(
        AbilityKind.STATIC,
        keyword=name,
        alternative_cost=AlternativeCost(cost=cost, keyword=name, condition=condition,
                                         text=name),
        functions_in=frozenset({Zone.HAND}),
        text=name,
    )


def paid(name: str) -> Condition:
    """"If its [name] cost was paid" for an optional additional cost."""
    return Condition(ConditionKind.WAS_KICKED, keyword=name)


def when_this_enters(*effects: Effect, condition: Condition) -> Ability:
    return Ability.triggered(
        TriggerCondition(
            event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
            subject=SELF,
            intervening_if=condition,
            text="when this enters",
        ),
        *effects,
        text="when this enters",
    )


def copy_of_this(power: int = 0, toughness: int = 0) -> Effect:
    return Effect(
        EffectKind.CREATE_TOKEN,
        players=YOU,
        token=TokenSpec(copy_of=SELF, power=Value.of(power), toughness=Value.of(toughness)),
        text="create a token that's a copy of it",
    )


def setup(card_db, scripts: dict, players: int = 2):
    board = make_board(card_db, ScriptedAbilities(scripts), players=players)
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    return board


def give(board, amount: int, color: Color = Color.GREEN, player: PlayerId = P0) -> None:
    board.game.player(player).mana_pool.add(ManaKind(color), amount)


def pool(board, player: PlayerId = P0) -> int:
    return board.game.player(player).mana_pool.total


def life(board, player: PlayerId = P0) -> int:
    return board.game.player(player).life


def offered(board, card, player: PlayerId = P0, **match):
    return [
        a for a in legal_actions(board.game, player)
        if a.kind is ActionKind.CAST_SPELL and a.source == card.id
        and all(getattr(a, k) == v for k, v in match.items())
    ]


def alternative_index(board, card, name: str) -> int:
    costs = board.chars(card).alternative_costs
    return next(i for i, c in enumerate(costs) if c.keyword == name)


def additional_index(board, card, name: str) -> int:
    costs = board.chars(card).additional_costs
    return next(i for i, c in enumerate(costs) if c.keyword == name)


def cast(board, card, *, additional=(), alternative: int = -1, player: PlayerId = P0):
    return cast_spell(board.game, player, Action(
        ActionKind.CAST_SPELL, source=card.id, additional_costs=tuple(additional),
        alternative_cost=alternative))


def run(board) -> None:
    """Resolve everything, triggers included, until nothing is left."""
    for _ in range(20):
        board.settle()
        if not board.game.stack:
            return
        board.resolve_stack()


def named(board, name: str, player: PlayerId = P0) -> list:
    return [o for o in board.game.permanents(player) if board.chars(o).name == name]


# ---------------------------------------------------------------------------
# Multikicker (CR 702.33c, 702.33d)
# ---------------------------------------------------------------------------


def per_kick(*effects: Effect, name: str = "multikicker") -> Effect:
    return Effect(EffectKind.REPEAT_PER_PAYMENT, keywords=(name,), children=effects,
                  text="for each time it was kicked")


@pytest.fixture
def multikicker(card_db):
    return setup(card_db, {"Divination": (
        Ability.spell(per_kick(GAIN_1)), *keyword("Multikicker", "{1}"))})


def test_multikicker_is_charged_once_per_announcement(multikicker):
    card = multikicker.hand("Divination")
    give(multikicker, 6, Color.BLUE)
    spell = cast(multikicker, card, additional=(0, 0, 0))
    assert pool(multikicker) == 0, "{2}{U} and three times {1}"
    assert spell.additional_costs_paid == (0, 0, 0), "one record per payment"


def test_for_each_time_it_was_kicked_repeats_per_payment(multikicker):
    card = multikicker.hand("Divination")
    give(multikicker, 6, Color.BLUE)
    cast(multikicker, card, additional=(0, 0, 0))
    run(multikicker)
    assert life(multikicker) == 43


def test_an_unkicked_multikicker_spell_repeats_nothing(multikicker):
    card = multikicker.hand("Divination")
    give(multikicker, 3, Color.BLUE)
    cast(multikicker, card)
    run(multikicker)
    assert life(multikicker) == 40


def test_a_cost_paid_once_is_recorded_once(card_db):
    """CR 702.33a: kicker named twice is still one payment."""
    board = setup(card_db, {"Divination": (
        Ability.spell(per_kick(GAIN_1, name="kicker")), *keyword("Kicker", "{1}"))})
    card = board.hand("Divination")
    give(board, 6, Color.BLUE)
    spell = cast(board, card, additional=(0, 0))
    assert spell.additional_costs_paid == (0,)
    run(board)
    assert life(board) == 41


# ---------------------------------------------------------------------------
# Squad (CR 702.157a)
# ---------------------------------------------------------------------------


def squad_as_the_rule_reads(cost: str) -> tuple[Ability, ...]:
    """The cost, payable any number of times, and the linked enters trigger
    making a copy for each payment."""
    return (
        optional_cost("Squad", mana(cost)),
        when_this_enters(
            Effect(EffectKind.REPEAT_PER_PAYMENT, keywords=("squad",),
                   children=(copy_of_this(),), text="for each time"),
            condition=paid("squad"),
        ),
    )


@pytest.fixture
def squad(card_db):
    return setup(card_db, {"Grizzly Bears": squad_as_the_rule_reads("{2}")})


def test_squad_is_charged_once_per_announcement(squad):
    card = squad.hand("Grizzly Bears")
    give(squad, 6)
    cast(squad, card, additional=(0, 0))
    assert pool(squad) == 0, "{1}{G} and twice {2}"


def test_squad_makes_a_token_copy_for_each_payment(squad):
    card = squad.hand("Grizzly Bears")
    give(squad, 6)
    cast(squad, card, additional=(0, 0))
    run(squad)
    bears = named(squad, "Grizzly Bears")
    assert len(bears) == 3
    tokens = [b for b in bears if b.is_token]
    assert len(tokens) == 2
    assert all(squad.pt(t) == (2, 2) for t in tokens), "a copy, not a nameless 0/0"


def test_squad_unpaid_makes_no_token(squad):
    card = squad.hand("Grizzly Bears")
    give(squad, 2)
    cast(squad, card)
    run(squad)
    assert len(named(squad, "Grizzly Bears")) == 1


def test_a_squad_token_copy_does_not_trigger_squad_again(squad):
    """CR 707.2: the payment is not copiable - the copies were never cast."""
    card = squad.hand("Grizzly Bears")
    give(squad, 4)
    cast(squad, card, additional=(0,))
    run(squad)
    assert len(named(squad, "Grizzly Bears")) == 2


_SQUAD_BUILDER_BUG = pytest.mark.xfail(strict=True, reason=(
    "BUILDER BUG: Squad is built as an optional additional cost only; the "
    "linked 'when this enters, if its squad cost was paid, create a token "
    "that's a copy of it for each time' trigger is missing"))


@_SQUAD_BUILDER_BUG
def test_the_squad_builder_makes_the_copies(card_db):
    board = setup(card_db, {"Grizzly Bears": keyword("Squad", "{2}")})
    card = board.hand("Grizzly Bears")
    give(board, 6)
    cast(board, card, additional=(0, 0))
    run(board)
    assert len(named(board, "Grizzly Bears")) == 3


# ---------------------------------------------------------------------------
# Offspring (CR 702.175a)
# ---------------------------------------------------------------------------


def offspring_as_the_rule_reads(cost: str) -> tuple[Ability, ...]:
    return (
        optional_cost("Offspring", mana(cost)),
        when_this_enters(copy_of_this(1, 1), condition=paid("offspring")),
    )


@pytest.fixture
def offspring(card_db):
    return setup(card_db, {"Hill Giant": offspring_as_the_rule_reads("{2}")})


def test_offspring_makes_a_1_1_copy(offspring):
    card = offspring.hand("Hill Giant")
    give(offspring, 6, Color.RED)
    cast(offspring, card, additional=(0,))
    run(offspring)
    giants = named(offspring, "Hill Giant")
    assert len(giants) == 2
    (token,) = [g for g in giants if g.is_token]
    assert offspring.pt(token) == (1, 1), "CR 707.9b: the exception is part of the copy"
    assert offspring.chars(token).has_type(CardType.CREATURE)


def test_offspring_unpaid_makes_no_copy(offspring):
    card = offspring.hand("Hill Giant")
    give(offspring, 4, Color.RED)
    cast(offspring, card)
    run(offspring)
    assert len(named(offspring, "Hill Giant")) == 1


def test_a_kicker_payment_does_not_pay_for_offspring(card_db):
    """CR 607: the rider is linked to its own keyword's cost."""
    board = setup(card_db, {"Hill Giant": (
        *offspring_as_the_rule_reads("{2}"), optional_cost("Kicker", mana("{1}")))})
    card = board.hand("Hill Giant")
    give(board, 6, Color.RED)
    kicker = additional_index(board, card, "Kicker")
    cast(board, card, additional=(kicker,))
    run(board)
    assert len(named(board, "Hill Giant")) == 1


@pytest.mark.xfail(strict=True, reason=(
    "BUILDER BUG: Offspring is built as an optional additional cost only; the "
    "'when this enters, if its offspring cost was paid, create a token that's "
    "a copy of it, except it's 1/1' trigger is missing"))
def test_the_offspring_builder_makes_the_copy(card_db):
    board = setup(card_db, {"Hill Giant": keyword("Offspring", "{2}")})
    card = board.hand("Hill Giant")
    give(board, 6, Color.RED)
    cast(board, card, additional=(0,))
    run(board)
    assert len(named(board, "Hill Giant")) == 2
