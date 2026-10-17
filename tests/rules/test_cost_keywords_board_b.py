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


# ---------------------------------------------------------------------------
# Bargain (CR 702.166a-b)
# ---------------------------------------------------------------------------


def sacrifice(spec: ObjectFilter, text: str) -> Cost:
    return Cost((CostComponent(CostKind.SACRIFICE, amount=Value.of(1), filter=spec,
                               text=text),))


def bargain_as_the_rule_reads() -> Ability:
    """"You may sacrifice an artifact, enchantment, or token" - a choice of
    two costs, because no one filter says "or token"."""
    from mtgfish.rules.kernel.query import ControllerRelation

    yours = ControllerRelation.YOU
    return optional_cost("Bargain", Cost(choices=(
        sacrifice(ObjectFilter(types_any=CardType.ARTIFACT | CardType.ENCHANTMENT,
                               controller=yours), "sacrifice an artifact or enchantment"),
        sacrifice(ObjectFilter(is_token=True, controller=yours), "sacrifice a token"),
    )))


def if_bargained(*effects: Effect) -> Effect:
    return Effect(EffectKind.CONDITIONAL, condition=paid("bargain"), children=effects,
                  text="if this spell was bargained")


@pytest.fixture
def bargain(card_db):
    return setup(card_db, {"Fire Prophecy": (
        Ability.spell(GAIN_1, if_bargained(GAIN_1)), bargain_as_the_rule_reads())})


def bargain_offered(board, card) -> bool:
    return bool(offered(board, card, additional_costs=(0,)))


def test_bargain_sacrifices_an_artifact(bargain):
    bargain.play("Witch's Oven")
    card = bargain.hand("Fire Prophecy")
    give(bargain, 2, Color.RED)
    assert bargain_offered(bargain, card)
    cast(bargain, card, additional=(0,))
    assert bargain.in_graveyard() == ["Witch's Oven"]
    run(bargain)
    assert life(bargain) == 42


def test_bargain_sacrifices_a_token_when_that_is_what_there_is(bargain):
    bargain.token("Grizzly Bears")
    card = bargain.hand("Fire Prophecy")
    give(bargain, 2, Color.RED)
    assert bargain_offered(bargain, card)
    cast(bargain, card, additional=(0,))
    assert named(bargain, "Grizzly Bears") == []
    run(bargain)
    assert life(bargain) == 42


def test_bargain_is_not_offered_with_nothing_to_sacrifice(bargain):
    bargain.play("Grizzly Bears")  # a nontoken creature is not enough
    card = bargain.hand("Fire Prophecy")
    give(bargain, 2, Color.RED)
    assert offered(bargain, card)
    assert not bargain_offered(bargain, card)


def test_an_unbargained_spell_skips_the_rider(bargain):
    bargain.play("Witch's Oven")
    card = bargain.hand("Fire Prophecy")
    give(bargain, 2, Color.RED)
    cast(bargain, card)
    run(bargain)
    assert life(bargain) == 41
    assert named(bargain, "Witch's Oven")


def test_the_bargain_builder_needs_something_to_sacrifice(card_db):
    board = setup(card_db, {"Fire Prophecy": (Ability.spell(GAIN_1),
                                              *keyword("Bargain", None))})
    card = board.hand("Fire Prophecy")
    give(board, 2, Color.RED)
    assert not bargain_offered(board, card)


# ---------------------------------------------------------------------------
# Spree (CR 702.172a)
# ---------------------------------------------------------------------------

GAIN_3 = Effect(EffectKind.GAIN_LIFE, players=YOU, amount=Value.of(3), text="gain 3 life")


def spree_modes(*costs: str) -> Effect:
    """"Choose one or more" with "+ {cost} -" before each mode."""
    return Effect(
        EffectKind.CHOOSE_MODE,
        children=(GAIN_1, GAIN_3)[: len(costs)],
        amount=Value.of(len(costs)),
        modes_up_to=True,
        mode_costs=tuple(mana(cost) for cost in costs),
        text="choose one or more",
    )


@pytest.fixture
def spree(card_db):
    # Fire Prophecy costs {1}{R}.
    return setup(card_db, {"Fire Prophecy": (
        Ability.spell(spree_modes("{1}", "{2}")), *keyword("Spree", None))})


def cast_modes(board, card, modes):
    return cast_spell(board.game, P0, Action(ActionKind.CAST_SPELL, source=card.id,
                                             mode_choices=tuple(modes)))


def test_each_chosen_spree_mode_adds_its_cost(spree):
    card = spree.hand("Fire Prophecy")
    give(spree, 5, Color.RED)
    cast_modes(spree, card, (0, 1))
    assert pool(spree) == 0, "{1}{R} plus {1} plus {2}"
    run(spree)
    assert life(spree) == 44


def test_an_unchosen_spree_mode_costs_nothing(spree):
    card = spree.hand("Fire Prophecy")
    give(spree, 5, Color.RED)
    cast_modes(spree, card, (1,))
    assert pool(spree) == 1, "{1}{R} plus {2}"
    run(spree)
    assert life(spree) == 43


def test_a_spree_spell_always_chooses_a_mode(spree):
    """Choose one or more: with nothing announced, one is chosen and paid."""
    card = spree.hand("Fire Prophecy")
    give(spree, 5, Color.RED)
    spell = cast_modes(spree, card, ())
    assert len(spell.chosen_modes) == 1
    assert pool(spree) == 5 - 2 - (1 if spell.chosen_modes == (0,) else 2)


def test_a_spree_spell_is_not_offered_without_its_cheapest_mode(spree):
    card = spree.hand("Fire Prophecy")
    give(spree, 2, Color.RED)
    assert not offered(spree, card), "{1}{R} alone buys no mode"
    give(spree, 1, Color.RED)
    assert offered(spree, card)


def test_the_spree_builder_adds_no_cost_of_its_own(card_db):
    board = setup(card_db, {"Fire Prophecy": keyword("Spree", None)})
    assert not board.chars(board.hand("Fire Prophecy")).additional_costs


# ---------------------------------------------------------------------------
# Conspire (CR 702.78a)
# ---------------------------------------------------------------------------


def conspire_as_the_rule_reads() -> Ability:
    """"You may tap two untapped creatures you control that each share a
    color with it"; the copy trigger is keyed on the payment."""
    from mtgfish.rules.kernel.query import ControllerRelation

    tap_two = CostComponent(
        CostKind.TAP_OTHER,
        amount=Value.of(2),
        filter=ObjectFilter(types_all=CardType.CREATURE, controller=ControllerRelation.YOU,
                            shares_color_with_source=True),
        text="tap two untapped creatures you control that each share a color with it",
    )
    return optional_cost("Conspire", Cost((tap_two,)))


@pytest.fixture
def conspire(card_db):
    # Fire Prophecy is red.
    return setup(card_db, {"Fire Prophecy": (Ability.spell(GAIN_1),
                                             conspire_as_the_rule_reads())})


def test_conspire_taps_two_creatures_sharing_a_color_and_copies(conspire):
    goblins = [conspire.play("Raging Goblin"), conspire.play("Goblin Piker")]
    card = conspire.hand("Fire Prophecy")
    give(conspire, 2, Color.RED)
    assert offered(conspire, card, additional_costs=(0,))
    cast(conspire, card, additional=(0,))
    assert all(g.tapped for g in goblins)
    run(conspire)
    assert life(conspire) == 42, "the spell and its copy"


def test_conspire_needs_creatures_that_share_a_color(conspire):
    conspire.play("Raging Goblin")
    conspire.play("Grizzly Bears")  # green
    card = conspire.hand("Fire Prophecy")
    give(conspire, 2, Color.RED)
    assert offered(conspire, card)
    assert not offered(conspire, card, additional_costs=(0,))


def test_unpaid_conspire_makes_no_copy(conspire):
    conspire.play("Raging Goblin")
    conspire.play("Goblin Piker")
    card = conspire.hand("Fire Prophecy")
    give(conspire, 2, Color.RED)
    cast(conspire, card)
    run(conspire)
    assert life(conspire) == 41


def test_the_conspire_builder_taps_two_creatures(card_db):
    board = setup(card_db, {"Fire Prophecy": (Ability.spell(GAIN_1),
                                              *keyword("Conspire", None))})
    card = board.hand("Fire Prophecy")
    give(board, 2, Color.RED)
    assert not offered(board, card, additional_costs=(0,)), "no creatures to tap"


# ---------------------------------------------------------------------------
# More Than Meets the Eye (CR 702.162a, 712.8c)
# ---------------------------------------------------------------------------


@pytest.fixture
def converted(card_db):
    # Goldbug, Humanity's Ally {1}{W}{U}, 3/3; its back face is Goldbug,
    # Scrappy Scout, a 1/3 Vehicle.
    return setup(card_db, {"Goldbug, Humanity's Ally": keyword(
        "More Than Meets the Eye", "{W}{U}")})


def test_a_card_cast_converted_is_on_the_stack_back_face_up(converted):
    card = converted.hand("Goldbug, Humanity's Ally")
    give(converted, 1, Color.WHITE)
    give(converted, 1, Color.BLUE)
    index = alternative_index(converted, card, "More Than Meets the Eye")
    assert offered(converted, card, alternative_cost=index)
    spell = cast(converted, card, alternative=index)
    assert pool(converted) == 0
    assert spell.face_index == 1
    assert converted.chars(spell).name == "Goldbug, Scrappy Scout"
    converted.resolve_stack()
    (vehicle,) = named(converted, "Goldbug, Scrappy Scout")
    assert "Vehicle" in converted.chars(vehicle).type_line.subtypes


def test_a_card_cast_for_its_mana_cost_is_front_face_up(converted):
    card = converted.hand("Goldbug, Humanity's Ally")
    give(converted, 1, Color.WHITE)
    give(converted, 2, Color.BLUE)
    spell = cast(converted, card)
    assert spell.face_index == 0
    converted.resolve_stack()
    assert named(converted, "Goldbug, Humanity's Ally")


# ---------------------------------------------------------------------------
# Impending (CR 702.176a) and bloodthirst (CR 702.54a): "enters with
# counters if ..."
# ---------------------------------------------------------------------------


def enters_with(counter: str, amount: int, condition: Condition) -> Ability:
    return Ability.static(
        Effect(EffectKind.ADD_COUNTERS, counter_type=counter, amount=Value.of(amount),
               condition=condition, text=f"enters with {amount} {counter} counters"),
        text=f"enters with {counter} counters if",
    )


def impending_as_the_rule_reads(n: int, cost: str) -> tuple[Ability, ...]:
    """The alternative cost, the counters it enters with, and "not a
    creature while it has a time counter" - all keyed on the payment."""
    from mtgfish.rules.kernel.query import Comparison, NumericConstraint

    impending_paid = Condition(ConditionKind.ALTERNATIVE_COST_PAID, keyword="Impending")
    has_time = Condition(ConditionKind.COUNTER_COUNT, counter_type="time",
                         constraint=NumericConstraint(Comparison.GE, Value.of(1)))
    return (
        alternative_cost("Impending", mana(cost)),
        enters_with("time", n, impending_paid),
        Ability.static(
            Effect(EffectKind.REMOVE_TYPE, targets=SELF, types=CardType.CREATURE,
                   text="it's not a creature"),
            condition=Condition(ConditionKind.AND, operands=(impending_paid, has_time)),
            text="as long as its impending cost was paid and it has a time counter",
        ),
    )


@pytest.fixture
def impending(card_db):
    return setup(card_db, {"Hill Giant": impending_as_the_rule_reads(2, "{R}")})


def test_an_impending_permanent_enters_with_time_counters_and_is_no_creature(impending):
    card = impending.hand("Hill Giant")
    give(impending, 1, Color.RED)
    cast(impending, card, alternative=alternative_index(impending, card, "Impending"))
    run(impending)
    (giant,) = named(impending, "Hill Giant")
    assert giant.counter_count("time") == 2
    assert not impending.chars(giant).is_creature


def test_cast_for_its_mana_cost_it_enters_without_counters(impending):
    card = impending.hand("Hill Giant")
    give(impending, 4, Color.RED)
    cast(impending, card)
    run(impending)
    (giant,) = named(impending, "Hill Giant")
    assert giant.counter_count("time") == 0
    assert impending.chars(giant).is_creature


@pytest.mark.xfail(strict=True, reason=(
    "BUILDER BUG: Impending is built as a plain alternative cost; CR 702.176a "
    "also needs a static ADD_COUNTERS 'enters with N time counters' (text "
    "starting 'enters ') with condition ALTERNATIVE_COST_PAID keyword "
    "'Impending', a static REMOVE_TYPE CREATURE on itself under AND(that, "
    "COUNTER_COUNT time >= 1), and an end-step trigger removing a time "
    "counter under the same condition"))
def test_the_impending_builder_enters_with_time_counters(card_db):
    board = setup(card_db, {"Hill Giant": keyword("Impending", "{R}", amount=2)})
    card = board.hand("Hill Giant")
    give(board, 1, Color.RED)
    cast(board, card, alternative=alternative_index(board, card, "Impending"))
    run(board)
    (giant,) = named(board, "Hill Giant")
    assert giant.counter_count("time") == 2


def an_opponent_was_dealt_damage() -> Condition:
    from mtgfish.rules.kernel.query import PlayerFilter, PlayerScope

    return Condition(ConditionKind.EVENT_THIS_TURN,
                     players=PlayerFilter(PlayerScope.OPPONENT),
                     event_kinds=(int(EventKind.DAMAGE_DEALT),
                                  int(EventKind.COMBAT_DAMAGE_DEALT)))


@pytest.fixture
def bloodthirst(card_db):
    return setup(card_db, {"Hill Giant": (
        enters_with("+1/+1", 2, an_opponent_was_dealt_damage()),)})


def test_bloodthirst_after_an_opponent_was_dealt_damage(bloodthirst):
    from mtgfish.rules.cr100_game_concepts.actions import deal_damage

    deal_damage(bloodthirst.game, P1, 1, source_controller=P0)
    card = bloodthirst.hand("Hill Giant")
    give(bloodthirst, 4, Color.RED)
    cast(bloodthirst, card)
    run(bloodthirst)
    (giant,) = named(bloodthirst, "Hill Giant")
    assert bloodthirst.pt(giant) == (5, 5)


def test_no_bloodthirst_when_no_opponent_was_dealt_damage(bloodthirst):
    card = bloodthirst.hand("Hill Giant")
    give(bloodthirst, 4, Color.RED)
    cast(bloodthirst, card)
    run(bloodthirst)
    (giant,) = named(bloodthirst, "Hill Giant")
    assert bloodthirst.pt(giant) == (3, 3)


@pytest.mark.xfail(strict=True, reason=(
    "BUILDER BUG: Bloodthirst's ADD_COUNTERS has no text, so it is not read "
    "as an 'enters with' effect, and its EVENT_THIS_TURN condition has no "
    "event_kinds or players, so it never holds; it needs text 'enters with N "
    "+1/+1 counters' and condition=Condition(EVENT_THIS_TURN, "
    "players=PlayerFilter(OPPONENT), event_kinds=(DAMAGE_DEALT, "
    "COMBAT_DAMAGE_DEALT))"))
def test_the_bloodthirst_builder_after_an_opponent_was_dealt_damage(card_db):
    from mtgfish.rules.cr100_game_concepts.actions import deal_damage

    board = setup(card_db, {"Hill Giant": keyword("Bloodthirst", None, amount=2)})
    deal_damage(board.game, P1, 1, source_controller=P0)
    card = board.hand("Hill Giant")
    give(board, 4, Color.RED)
    cast(board, card)
    run(board)
    (giant,) = named(board, "Hill Giant")
    assert board.pt(giant) == (5, 5)


# ---------------------------------------------------------------------------
# Jump-start (CR 702.133a)
# ---------------------------------------------------------------------------


def jump_start_as_the_rule_reads() -> Ability:
    """Cast from the graveyard for the mana cost plus a discard."""
    discard = CostComponent(CostKind.DISCARD, amount=Value.of(1), text="discard a card")
    return Ability(
        AbilityKind.STATIC,
        keyword="Jump-start",
        alternative_cost=AlternativeCost(cost=Cost((discard,)), from_zone=Zone.GRAVEYARD,
                                         pays_mana_cost=True, keyword="Jump-start",
                                         text="Jump-start"),
        functions_in=frozenset({Zone.GRAVEYARD}),
        text="Jump-start",
    )


@pytest.fixture
def jump_start(card_db):
    # Fire Prophecy costs {1}{R}.
    return setup(card_db, {"Fire Prophecy": (Ability.spell(GAIN_1),
                                             jump_start_as_the_rule_reads())})


def zones_of(board, name: str) -> list[Zone]:
    return sorted(
        o.zone for o in board.game.objects.values()
        if o.is_live and o.card is not None and o.card.name == name
    )


def jump(board, card):
    index = alternative_index(board, card, "Jump-start")
    assert offered(board, card, alternative_cost=index)
    return cast(board, card, alternative=index)


def test_jump_start_casts_from_the_graveyard_and_exiles_it(jump_start):
    card = jump_start.graveyard("Fire Prophecy")
    jump_start.hand("Grizzly Bears")
    give(jump_start, 2, Color.RED)
    jump(jump_start, card)
    assert jump_start.in_graveyard() == ["Grizzly Bears"], "the discard"
    assert pool(jump_start) == 0, "and the mana cost"
    run(jump_start)
    assert life(jump_start) == 41
    assert zones_of(jump_start, "Fire Prophecy") == [Zone.EXILE]


def test_a_countered_jump_started_spell_is_exiled_too(jump_start):
    card = jump_start.graveyard("Fire Prophecy")
    jump_start.hand("Grizzly Bears")
    give(jump_start, 2, Color.RED)
    spell = jump(jump_start, card)
    jump_start.game.move_object(spell, Zone.GRAVEYARD)
    assert zones_of(jump_start, "Fire Prophecy") == [Zone.EXILE]


def test_jump_start_needs_a_card_to_discard(jump_start):
    game = jump_start.game
    for object_id in list(game.player(P0).hand):
        game.move_object(game.objects[object_id], Zone.LIBRARY)
    card = jump_start.graveyard("Fire Prophecy")
    give(jump_start, 2, Color.RED)
    assert not offered(jump_start, card)


@pytest.mark.xfail(strict=True, reason=(
    "BUILDER BUG: Jump-start is built inert (MISBUILT_KEYWORDS); it needs an "
    "AlternativeCost(from_zone=GRAVEYARD, pays_mana_cost=True, cost=Cost(("
    "CostComponent(DISCARD, amount=1),)), keyword='Jump-start') functioning "
    "in the graveyard - the exile is the engine's, keyed on the keyword"))
def test_the_jump_start_builder_casts_from_the_graveyard(card_db):
    board = setup(card_db, {"Fire Prophecy": (Ability.spell(GAIN_1),
                                              *keyword("Jump-start", None))})
    card = board.graveyard("Fire Prophecy")
    board.hand("Grizzly Bears")
    give(board, 2, Color.RED)
    assert offered(board, card)


# ---------------------------------------------------------------------------
# Cleave (CR 702.148a)
# ---------------------------------------------------------------------------


@pytest.fixture
def cleave(card_db):
    """The bracketed text removed is the rider: with it, gain 1; without, 3."""
    rider = Effect(
        EffectKind.CONDITIONAL,
        condition=Condition(ConditionKind.ALTERNATIVE_COST_PAID, keyword="Cleave"),
        children=(GAIN_3,),
        otherwise=(GAIN_1,),
        text="if this spell's cleave cost was paid",
    )
    return setup(card_db, {"Fire Prophecy": (Ability.spell(rider),
                                             *keyword("Cleave", "{3}{R}"))})


def test_a_cleaved_spell_does_what_its_unbracketed_text_says(cleave):
    card = cleave.hand("Fire Prophecy")
    give(cleave, 4, Color.RED)
    index = alternative_index(cleave, card, "Cleave")
    assert offered(cleave, card, alternative_cost=index)
    cast(cleave, card, alternative=index)
    assert pool(cleave) == 0
    run(cleave)
    assert life(cleave) == 43


def test_a_spell_cast_for_its_mana_cost_keeps_its_brackets(cleave):
    card = cleave.hand("Fire Prophecy")
    give(cleave, 2, Color.RED)
    cast(cleave, card)
    run(cleave)
    assert life(cleave) == 41


# ---------------------------------------------------------------------------
# Assist (CR 702.132a)
# ---------------------------------------------------------------------------


def test_assist_is_not_an_alternative_cost(card_db):
    board = setup(card_db, {"Hill Giant": keyword("Assist", None)})
    card = board.hand("Hill Giant")
    assert not board.chars(card).alternative_costs
    assert not offered(board, card), "no mana: nobody has paid anything"
