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


# ---------------------------------------------------------------------------
# Prowl (CR 702.76a) and freerunning (CR 702.173a)
# ---------------------------------------------------------------------------


def combat_damage(board, source, amount: int = 1, to: PlayerId = P1) -> None:
    from mtgfish.rules.cr100_game_concepts.actions import deal_damage

    deal_damage(board.game, to, amount, source=source.id,
                source_controller=source.controller, combat=True,
                is_commander_source=source.is_commander)


PROWL_CONDITION = Condition(ConditionKind.COMBAT_DAMAGE_BY_SHARED_CREATURE_TYPE)


@pytest.fixture
def prowl(card_db):
    # Latchkey Faerie is a Faerie Rogue.
    return setup(card_db, {"Latchkey Faerie": (
        alternative_cost("Prowl", mana("{U}"), PROWL_CONDITION),)})


def prowl_offered(board, card) -> bool:
    return bool(offered(board, card, alternative_cost=alternative_index(board, card, "Prowl")))


def test_prowl_is_offered_after_a_rogue_you_control_dealt_combat_damage(prowl):
    card = prowl.hand("Latchkey Faerie")
    give(prowl, 1, Color.BLUE)
    assert not prowl_offered(prowl, card), "no damage yet"
    combat_damage(prowl, prowl.play("Boggart Arsonists"))  # a Goblin Rogue
    assert prowl_offered(prowl, card)
    spell = cast(prowl, card, alternative=alternative_index(prowl, card, "Prowl"))
    assert spell.alternative_cost_paid == "Prowl"
    assert pool(prowl) == 0


def test_prowl_needs_a_shared_creature_type(prowl):
    card = prowl.hand("Latchkey Faerie")
    give(prowl, 1, Color.BLUE)
    combat_damage(prowl, prowl.play("Grizzly Bears"))
    assert not prowl_offered(prowl, card)


def test_prowl_needs_combat_damage_by_a_source_you_controlled(prowl):
    from mtgfish.rules.cr100_game_concepts.actions import deal_damage

    card = prowl.hand("Latchkey Faerie")
    give(prowl, 1, Color.BLUE)
    combat_damage(prowl, prowl.play("Boggart Arsonists", controller=1), to=P0)
    assert not prowl_offered(prowl, card), "an opponent's Rogue"
    rogue = prowl.play("Boggart Arsonists")
    deal_damage(prowl.game, P1, 2, source=rogue.id, source_controller=P0)
    assert not prowl_offered(prowl, card), "noncombat damage"


def test_prowl_asks_what_the_source_was_when_it_dealt_the_damage(prowl):
    """The Rogue may be gone by now; it was a Rogue you controlled then."""
    card = prowl.hand("Latchkey Faerie")
    give(prowl, 1, Color.BLUE)
    rogue = prowl.play("Boggart Arsonists")
    combat_damage(prowl, rogue)
    prowl.game.move_object(rogue, Zone.GRAVEYARD)
    assert prowl_offered(prowl, card)


def test_prowl_lasts_only_this_turn(prowl):
    card = prowl.hand("Latchkey Faerie")
    give(prowl, 1, Color.BLUE)
    combat_damage(prowl, prowl.play("Boggart Arsonists"))
    # What a new turn does to "this turn" (CR 500.1, take_turn).
    prowl.game.turn_history.clear()
    assert not prowl_offered(prowl, card)


@pytest.mark.xfail(strict=True, reason=(
    "BUILDER BUG: Prowl is built as an unconditional alternative cost; it "
    "needs condition=Condition(COMBAT_DAMAGE_BY_SHARED_CREATURE_TYPE)"))
def test_the_prowl_builder_needs_combat_damage(card_db):
    board = setup(card_db, {"Latchkey Faerie": keyword("Prowl", "{U}")})
    card = board.hand("Latchkey Faerie")
    give(board, 1, Color.BLUE)
    assert not prowl_offered(board, card)


ASSASSIN_OR_COMMANDER = Condition(ConditionKind.OR, operands=(
    Condition(ConditionKind.COMBAT_DAMAGE_DEALT_BY, filter=ObjectFilter(
        types_all=CardType.CREATURE, subtypes_any=("Assassin",))),
    Condition(ConditionKind.COMBAT_DAMAGE_DEALT_BY, filter=ObjectFilter(
        types_all=CardType.CREATURE, is_commander=True)),
))


@pytest.fixture
def freerunning(card_db):
    return setup(card_db, {"Fire Prophecy": (
        Ability.spell(GAIN_1),
        alternative_cost("Freerunning", mana("{R}"), ASSASSIN_OR_COMMANDER))})


def freerunning_offered(board, card) -> bool:
    index = alternative_index(board, card, "Freerunning")
    return bool(offered(board, card, alternative_cost=index))


def test_freerunning_after_an_assassin_dealt_combat_damage(freerunning):
    card = freerunning.hand("Fire Prophecy")
    give(freerunning, 1, Color.RED)
    assert not freerunning_offered(freerunning, card)
    combat_damage(freerunning, freerunning.play("Ezio Auditore da Firenze"))
    assert freerunning_offered(freerunning, card)


def test_freerunning_after_a_commander_dealt_combat_damage(freerunning):
    card = freerunning.hand("Fire Prophecy")
    give(freerunning, 1, Color.RED)
    combat_damage(freerunning, freerunning.play("Grizzly Bears", is_commander=True))
    assert freerunning_offered(freerunning, card)


def test_freerunning_not_after_another_creature(freerunning):
    card = freerunning.hand("Fire Prophecy")
    give(freerunning, 1, Color.RED)
    combat_damage(freerunning, freerunning.play("Grizzly Bears"))
    assert not freerunning_offered(freerunning, card)


@pytest.mark.xfail(strict=True, reason=(
    "BUILDER BUG: Freerunning is built as an unconditional alternative cost; "
    "it needs condition=OR(COMBAT_DAMAGE_DEALT_BY Assassin creature, "
    "COMBAT_DAMAGE_DEALT_BY creature with is_commander)"))
def test_the_freerunning_builder_needs_combat_damage(card_db):
    board = setup(card_db, {"Fire Prophecy": (Ability.spell(GAIN_1),
                                              *keyword("Freerunning", "{R}"))})
    card = board.hand("Fire Prophecy")
    give(board, 1, Color.RED)
    assert not freerunning_offered(board, card)


# ---------------------------------------------------------------------------
# Spectacle (CR 702.137a)
# ---------------------------------------------------------------------------


def an_opponent_lost_life() -> Condition:
    from mtgfish.rules.kernel.query import PlayerFilter, PlayerScope

    return Condition(ConditionKind.EVENT_THIS_TURN,
                     players=PlayerFilter(PlayerScope.OPPONENT),
                     event_kinds=(int(EventKind.LIFE_LOST),))


@pytest.fixture
def spectacle(card_db):
    return setup(card_db, {"Fire Prophecy": (
        Ability.spell(GAIN_1),
        alternative_cost("Spectacle", mana("{R}"), an_opponent_lost_life()))})


def spectacle_offered(board, card) -> bool:
    index = alternative_index(board, card, "Spectacle")
    return bool(offered(board, card, alternative_cost=index))


def test_spectacle_after_an_opponent_lost_life(spectacle):
    from mtgfish.rules.cr100_game_concepts.actions import lose_life

    card = spectacle.hand("Fire Prophecy")
    give(spectacle, 1, Color.RED)
    assert not spectacle_offered(spectacle, card)
    lose_life(spectacle.game, P1, 1)
    assert spectacle_offered(spectacle, card)
    spell = cast(spectacle, card, alternative=alternative_index(spectacle, card, "Spectacle"))
    assert spell.alternative_cost_paid == "Spectacle"
    assert pool(spectacle) == 0


def test_spectacle_not_after_you_lost_life(spectacle):
    from mtgfish.rules.cr100_game_concepts.actions import lose_life

    card = spectacle.hand("Fire Prophecy")
    give(spectacle, 1, Color.RED)
    lose_life(spectacle.game, P0, 1)
    assert not spectacle_offered(spectacle, card)


@pytest.mark.xfail(strict=True, reason=(
    "BUILDER BUG: Spectacle is built as an unconditional alternative cost; it "
    "needs condition=Condition(EVENT_THIS_TURN, players=PlayerFilter(OPPONENT), "
    "event_kinds=(LIFE_LOST,))"))
def test_the_spectacle_builder_needs_an_opponent_to_have_lost_life(card_db):
    board = setup(card_db, {"Fire Prophecy": (Ability.spell(GAIN_1),
                                              *keyword("Spectacle", "{R}"))})
    card = board.hand("Fire Prophecy")
    give(board, 1, Color.RED)
    assert not spectacle_offered(board, card)


# ---------------------------------------------------------------------------
# Gift (CR 702.174)
# ---------------------------------------------------------------------------


def chosen_player_draws() -> Effect:
    """CR 702.174e: "Gift a card" - the chosen player draws a card."""
    from mtgfish.rules.kernel.query import PlayerFilter, PlayerScope

    return Effect(
        EffectKind.GIVE_GIFT,
        children=(Effect(EffectKind.DRAW, players=PlayerFilter(PlayerScope.THAT_PLAYER),
                         amount=Value.of(1), text="the chosen player draws a card"),),
        text="gift a card",
    )


def gift_cost() -> Ability:
    """CR 702.174a: "you may choose an opponent" - a cost with nothing to pay."""
    return optional_cost("Gift", Cost(()))


def hand_count(board, player: PlayerId) -> int:
    return len(board.game.player(player).hand)


@pytest.fixture
def gift_spell(card_db):
    return setup(card_db, {"Fire Prophecy": (
        Ability.spell(chosen_player_draws(), GAIN_1), gift_cost())})


def test_a_promised_gift_goes_to_the_chosen_opponent(gift_spell):
    card = gift_spell.hand("Fire Prophecy")
    give(gift_spell, 2, Color.RED)
    before = hand_count(gift_spell, P1)
    spell = cast(gift_spell, card, additional=(0,))
    assert spell.chosen_player == P1
    run(gift_spell)
    assert hand_count(gift_spell, P1) == before + 1
    assert life(gift_spell) == 41, "and the rest of the spell happens too"


def test_no_gift_when_none_was_promised(gift_spell):
    card = gift_spell.hand("Fire Prophecy")
    give(gift_spell, 2, Color.RED)
    before = hand_count(gift_spell, P1)
    cast(gift_spell, card)
    run(gift_spell)
    assert hand_count(gift_spell, P1) == before
    assert life(gift_spell) == 41


def test_the_caster_chooses_which_opponent(card_db):
    board = setup(card_db, {"Fire Prophecy": (
        Ability.spell(chosen_player_draws()), gift_cost())}, players=3)

    class PicksTheLast:
        def choose_gift_recipient(self, game, player, opponents):
            return opponents[-1]

    board.game.agents[0] = PicksTheLast()
    card = board.hand("Fire Prophecy")
    give(board, 2, Color.RED)
    before = hand_count(board, PlayerId(2))
    spell = cast(board, card, additional=(0,))
    assert spell.chosen_player == PlayerId(2)
    run(board)
    assert hand_count(board, PlayerId(2)) == before + 1


def test_a_gift_permanent_gives_as_it_enters(card_db):
    """CR 702.174b: on a permanent, an enters trigger - the choice made as
    the spell was cast is the permanent's."""
    board = setup(card_db, {"Grizzly Bears": (
        gift_cost(), when_this_enters(chosen_player_draws(), condition=paid("gift")))})
    card = board.hand("Grizzly Bears")
    give(board, 2)
    before = hand_count(board, P1)
    cast(board, card, additional=(0,))
    run(board)
    (bears,) = named(board, "Grizzly Bears")
    assert bears.chosen_player == P1
    assert hand_count(board, P1) == before + 1


def test_giving_a_gift_triggers_whenever_you_give_a_gift(card_db):
    """CR 702.174c."""
    watcher = Ability.triggered(
        TriggerCondition(event_kinds=frozenset({EventKind.GIFT_GIVEN}), players=YOU,
                         text="whenever you give a gift"),
        GAIN_1,
        text="whenever you give a gift, gain 1 life",
    )
    board = setup(card_db, {
        "Fire Prophecy": (Ability.spell(chosen_player_draws()), gift_cost()),
        "Grizzly Bears": (watcher,),
    })
    board.play("Grizzly Bears")
    card = board.hand("Fire Prophecy")
    give(board, 2, Color.RED)
    cast(board, card, additional=(0,))
    run(board)
    assert life(board) == 41


@pytest.mark.xfail(strict=True, reason=(
    "BUILDER BUG: Gift is built as an alternative cost; CR 702.174a makes it "
    "an optional additional cost ('you may choose an opponent', keyword Gift, "
    "an empty Cost) plus a GIVE_GIFT effect, or an enters trigger "
    "intervening-if WAS_KICKED keyword 'gift' wrapping GIVE_GIFT on a permanent"))
def test_the_gift_builder_is_an_additional_cost(card_db):
    board = setup(card_db, {"Fire Prophecy": keyword("Gift", None)})
    card = board.hand("Fire Prophecy")
    chars = board.chars(card)
    assert not chars.alternative_costs
    assert [c.keyword for c in chars.additional_costs] == ["Gift"]
