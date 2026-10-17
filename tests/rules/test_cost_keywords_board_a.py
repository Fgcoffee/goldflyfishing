"""Cost keywords, proved on a board: the whole keyword, not just the price.

Each keyword is built with the real builder, cast or activated on a board, and
every clause of its CR 702 rule is checked. A cost keyword whose payment works
but whose other abilities do nothing is a keyword that is not implemented -
a dashed creature that never goes back to hand is just a cheap creature.

Keywords whose builder wires the wrong operations are marked ``xfail(strict)``
with the reason; the test states what the rule requires and starts passing
(and so failing as an xfail) once the builder is right.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaCost, ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.kernel.enums import CardType, Color, Phase, Step, Zone
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.rules.kernel.query import YOU, ObjectFilter, Value

P0 = PlayerId(0)
P1 = PlayerId(1)

GAIN_2 = Effect(EffectKind.GAIN_LIFE, players=YOU, amount=Value.of(2), text="gain 2 life")
GAIN_3 = Effect(EffectKind.GAIN_LIFE, players=YOU, amount=Value.of(3), text="gain 3 life")
DRAW_1 = Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1), text="draw a card")


def mana(text: str) -> Cost:
    return Cost((CostComponent(CostKind.MANA, mana=ManaCost.parse(text)),))


def keyword(name: str, cost: str = "{1}", **kw) -> tuple[Ability, ...]:
    return build(KeywordInstance(name, cost=mana(cost), **kw))


def choose(*modes: Effect, count: int = 1) -> Effect:
    """"Choose one", or with a count above one, "choose one or more"."""
    return Effect(EffectKind.CHOOSE_MODE, children=modes, amount=Value.of(count),
                  modes_up_to=count > 1, text="choose")


def setup(card_db, scripts: dict, players: int = 2):
    board = make_board(card_db, ScriptedAbilities(scripts), players=players)
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    return board


def give(board, amount: int, color: Color = Color.RED, player: PlayerId = P0) -> None:
    board.game.player(player).mana_pool.add(ManaKind(color), amount)


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


def cast_for(board, card, name: str):
    """Cast a card for its ``name`` alternative cost, as legality offers it."""
    index = alternative_index(board, card, name)
    (action,) = offered(board, card, alternative_cost=index)
    return cast_spell(board.game, P0, action)


def zones_of(board, name: str) -> list[Zone]:
    return sorted(
        o.zone for o in board.game.objects.values()
        if o.is_live and o.card is not None and o.card.name == name
    )


def permanent(board, name: str, player: PlayerId = P0):
    return next(
        (o for o in board.game.permanents(player) if board.chars(o).name == name), None
    )


def end_step(board) -> None:
    """The beginning of the end step: delayed abilities trigger and resolve."""
    board.game.phase = Phase.ENDING
    board.game.step = Step.END_STEP
    board.game.emit(Event(EventKind.END_STEP, player=P0))
    board.settle()
    board.resolve_stack()
    board.settle()
    board.resolve_stack()


def life(board, player: PlayerId = P0) -> int:
    return board.game.player(player).life


def hand_size(board, player: PlayerId = P0) -> int:
    return len(board.game.player(player).hand)


# ---------------------------------------------------------------------------
# Buyback (CR 702.27a)
# ---------------------------------------------------------------------------


@pytest.fixture
def buyback(card_db):
    return setup(card_db, {
        "Divination": (Ability.spell(GAIN_2), *keyword("Buyback", "{3}")),
    })


def test_buyback_paid_returns_the_spell_to_hand_as_it_resolves(buyback):
    card = buyback.hand("Divination")
    give(buyback, 7, Color.BLUE)
    index = additional_index(buyback, card, "Buyback")
    cast_spell(buyback.game, P0, Action(ActionKind.CAST_SPELL, source=card.id,
                                        additional_costs=(index,)))
    assert buyback.game.player(P0).mana_pool.total == 1, "{2}{U} plus {3} was paid"
    buyback.resolve_stack()
    assert life(buyback) == 42, "the spell still did what it says"
    assert zones_of(buyback, "Divination") == [Zone.HAND]


def test_buyback_unpaid_goes_to_the_graveyard(buyback):
    """Control: the return belongs to the payment, not to the card."""
    card = buyback.hand("Divination")
    give(buyback, 6, Color.BLUE)
    cast_spell(buyback.game, P0, Action(ActionKind.CAST_SPELL, source=card.id))
    buyback.resolve_stack()
    assert zones_of(buyback, "Divination") == [Zone.GRAVEYARD]


def test_a_countered_buyback_spell_goes_to_the_graveyard(buyback):
    """"As it resolves": a spell that never resolves is not bought back."""
    card = buyback.hand("Divination")
    give(buyback, 6, Color.BLUE)
    index = additional_index(buyback, card, "Buyback")
    spell = cast_spell(buyback.game, P0, Action(ActionKind.CAST_SPELL, source=card.id,
                                                additional_costs=(index,)))
    buyback.game.move_object(spell, Zone.GRAVEYARD)
    assert zones_of(buyback, "Divination") == [Zone.GRAVEYARD]


# ---------------------------------------------------------------------------
# Dash (CR 702.109a)
# ---------------------------------------------------------------------------


@pytest.fixture
def dash(card_db):
    return setup(card_db, {"Grizzly Bears": keyword("Dash", "{R}")})


def test_dash_is_offered_from_hand_for_its_cost(dash):
    card = dash.hand("Grizzly Bears")
    give(dash, 1)
    index = alternative_index(dash, card, "Dash")
    assert offered(dash, card, alternative_cost=index)
    assert not offered(dash, card, alternative_cost=-1), "{1}{G} is not affordable"


def test_a_dashed_creature_has_haste(dash):
    card = dash.hand("Grizzly Bears")
    give(dash, 1)
    cast_for(dash, card, "Dash")
    dash.resolve_stack()
    bears = permanent(dash, "Grizzly Bears")
    assert bears is not None
    assert dash.chars(bears).has_keyword("Haste")


def test_a_dashed_creature_returns_to_hand_at_the_next_end_step(dash):
    card = dash.hand("Grizzly Bears")
    give(dash, 1)
    cast_for(dash, card, "Dash")
    dash.resolve_stack()
    end_step(dash)
    assert zones_of(dash, "Grizzly Bears") == [Zone.HAND]


def test_a_creature_cast_for_its_mana_cost_neither_hastes_nor_returns(dash):
    """Control: both riders belong to the dash payment."""
    card = dash.hand("Grizzly Bears")
    give(dash, 2, Color.GREEN)
    cast_spell(dash.game, P0, Action(ActionKind.CAST_SPELL, source=card.id))
    dash.resolve_stack()
    bears = permanent(dash, "Grizzly Bears")
    assert not dash.chars(bears).has_keyword("Haste")
    end_step(dash)
    assert zones_of(dash, "Grizzly Bears") == [Zone.BATTLEFIELD]


def test_dashs_return_does_not_follow_the_card_to_a_new_object(dash):
    """CR 400.7, 603.7c: bounced and recast for its mana cost, it is a new
    object the delayed ability knows nothing about."""
    card = dash.hand("Grizzly Bears")
    give(dash, 1)
    cast_for(dash, card, "Dash")
    dash.resolve_stack()
    bounced = dash.game.move_object(permanent(dash, "Grizzly Bears"), Zone.HAND)
    give(dash, 2, Color.GREEN)
    cast_spell(dash.game, P0, Action(ActionKind.CAST_SPELL, source=bounced.id))
    dash.resolve_stack()
    end_step(dash)
    assert zones_of(dash, "Grizzly Bears") == [Zone.BATTLEFIELD]


# ---------------------------------------------------------------------------
# Blitz (CR 702.152a)
# ---------------------------------------------------------------------------


@pytest.fixture
def blitz(card_db):
    return setup(card_db, {"Grizzly Bears": keyword("Blitz", "{R}")})


def test_a_blitzed_creature_has_haste(blitz):
    card = blitz.hand("Grizzly Bears")
    give(blitz, 1)
    cast_for(blitz, card, "Blitz")
    blitz.resolve_stack()
    assert blitz.chars(permanent(blitz, "Grizzly Bears")).has_keyword("Haste")


def test_a_blitzed_creature_is_sacrificed_at_the_next_end_step_and_draws(blitz):
    card = blitz.hand("Grizzly Bears")
    give(blitz, 1)
    cast_for(blitz, card, "Blitz")
    blitz.resolve_stack()
    before = hand_size(blitz)
    end_step(blitz)
    assert zones_of(blitz, "Grizzly Bears") == [Zone.GRAVEYARD]
    assert hand_size(blitz) == before + 1, "when it dies, draw a card"


def test_a_blitzed_creature_that_dies_another_way_draws_too(blitz):
    from mtgfish.rules.cr100_game_concepts.actions import destroy

    card = blitz.hand("Grizzly Bears")
    give(blitz, 1)
    cast_for(blitz, card, "Blitz")
    blitz.resolve_stack()
    before = hand_size(blitz)
    destroy(blitz.game, permanent(blitz, "Grizzly Bears"))
    blitz.settle()
    blitz.resolve_stack()
    assert hand_size(blitz) == before + 1


def test_a_creature_cast_normally_has_no_blitz_riders(blitz):
    """Control."""
    from mtgfish.rules.cr100_game_concepts.actions import destroy

    card = blitz.hand("Grizzly Bears")
    give(blitz, 2, Color.GREEN)
    cast_spell(blitz.game, P0, Action(ActionKind.CAST_SPELL, source=card.id))
    blitz.resolve_stack()
    bears = permanent(blitz, "Grizzly Bears")
    assert not blitz.chars(bears).has_keyword("Haste")
    end_step(blitz)
    assert zones_of(blitz, "Grizzly Bears") == [Zone.BATTLEFIELD]
    before = hand_size(blitz)
    destroy(blitz.game, permanent(blitz, "Grizzly Bears"))
    blitz.settle()
    blitz.resolve_stack()
    assert hand_size(blitz) == before


# ---------------------------------------------------------------------------
# Warp (CR 702.185a)
# ---------------------------------------------------------------------------


@pytest.fixture
def warp(card_db):
    return setup(card_db, {"Grizzly Bears": keyword("Warp", "{R}")})


def test_a_warped_permanent_is_exiled_at_the_next_end_step(warp):
    card = warp.hand("Grizzly Bears")
    give(warp, 1)
    cast_for(warp, card, "Warp")
    warp.resolve_stack()
    assert permanent(warp, "Grizzly Bears") is not None
    end_step(warp)
    assert zones_of(warp, "Grizzly Bears") == [Zone.EXILE]


def test_a_warped_card_may_be_cast_from_exile_after_this_turn_only(warp):
    card = warp.hand("Grizzly Bears")
    give(warp, 1)
    cast_for(warp, card, "Warp")
    warp.resolve_stack()
    end_step(warp)
    (exiled,) = [o for o in warp.game.objects.values()
                 if o.is_live and o.zone is Zone.EXILE and o.card.name == "Grizzly Bears"]
    give(warp, 2, Color.GREEN)
    warp.game.phase, warp.game.step = Phase.PRECOMBAT_MAIN, Step.MAIN
    assert not offered(warp, exiled), "not during the turn it was warped"

    warp.game.turn += 1
    (action,) = offered(warp, exiled)
    assert action.alternative_cost == -1, "for its mana cost; warp is from hand only"
    cast_spell(warp.game, P0, action)
    warp.resolve_stack()
    assert zones_of(warp, "Grizzly Bears") == [Zone.BATTLEFIELD]


def test_a_creature_cast_for_its_mana_cost_is_not_exiled(warp):
    """Control."""
    card = warp.hand("Grizzly Bears")
    give(warp, 2, Color.GREEN)
    cast_spell(warp.game, P0, Action(ActionKind.CAST_SPELL, source=card.id))
    warp.resolve_stack()
    end_step(warp)
    assert zones_of(warp, "Grizzly Bears") == [Zone.BATTLEFIELD]


def test_a_card_exiled_by_something_else_is_not_castable(warp):
    """Control: the permission is warp's, not every exiled card's."""
    from mtgfish.rules.cr100_game_concepts.actions import exile

    bears = warp.play("Grizzly Bears")
    exile(warp.game, bears)
    warp.game.turn += 1
    give(warp, 2, Color.GREEN)
    (exiled,) = [o for o in warp.game.objects.values()
                 if o.is_live and o.zone is Zone.EXILE and o.card.name == "Grizzly Bears"]
    assert not offered(warp, exiled)


# ---------------------------------------------------------------------------
# Entwine (CR 702.42a-b) and Escalate (CR 702.120a)
# ---------------------------------------------------------------------------


@pytest.fixture
def entwine(card_db):
    return setup(card_db, {
        "Divination": (Ability.spell(choose(GAIN_2, GAIN_3)), *keyword("Entwine", "{2}")),
    })


def test_entwine_paid_follows_every_mode(entwine):
    card = entwine.hand("Divination")
    give(entwine, 5, Color.BLUE)
    index = additional_index(entwine, card, "Entwine")
    spell = cast_spell(entwine.game, P0, Action(
        ActionKind.CAST_SPELL, source=card.id, mode_choices=(0,), additional_costs=(index,)))
    assert spell.chosen_modes == (0, 1)
    assert entwine.game.player(P0).mana_pool.total == 0, "{2}{U} plus {2}"
    entwine.resolve_stack()
    assert life(entwine) == 45


def test_entwine_unpaid_follows_one_mode(entwine):
    """Control."""
    card = entwine.hand("Divination")
    give(entwine, 5, Color.BLUE)
    cast_spell(entwine.game, P0, Action(ActionKind.CAST_SPELL, source=card.id,
                                        mode_choices=(1,)))
    entwine.resolve_stack()
    assert life(entwine) == 43


@pytest.fixture
def escalate(card_db):
    return setup(card_db, {
        "Divination": (
            Ability.spell(choose(GAIN_2, GAIN_3, DRAW_1, count=3)),
            *keyword("Escalate", "{1}"),
        ),
    })


@pytest.mark.parametrize("modes, extra", [((0,), 0), ((0, 1), 1), ((0, 1, 2), 2)])
def test_escalate_costs_its_cost_for_each_mode_beyond_the_first(escalate, modes, extra):
    card = escalate.hand("Divination")
    give(escalate, 6, Color.BLUE)
    cast_spell(escalate.game, P0, Action(ActionKind.CAST_SPELL, source=card.id,
                                         mode_choices=modes))
    assert escalate.game.player(P0).mana_pool.total == 6 - 3 - extra


def test_escalate_cannot_be_dodged_by_not_announcing_it(escalate):
    """Escalate is not optional: three modes cannot be cast for {2}{U}."""
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import CastError

    card = escalate.hand("Divination")
    give(escalate, 3, Color.BLUE)
    with pytest.raises(CastError):
        cast_spell(escalate.game, P0, Action(ActionKind.CAST_SPELL, source=card.id,
                                             mode_choices=(0, 1, 2)))


# ---------------------------------------------------------------------------
# Replicate (CR 702.56a)
# ---------------------------------------------------------------------------


@pytest.fixture
def replicate(card_db):
    return setup(card_db, {
        "Divination": (Ability.spell(GAIN_2), *keyword("Replicate", "{1}")),
    })


def test_replicate_is_paid_once_per_payment_and_copies_that_many_times(replicate):
    card = replicate.hand("Divination")
    give(replicate, 6, Color.BLUE)
    index = additional_index(replicate, card, "Replicate")
    cast_spell(replicate.game, P0, Action(ActionKind.CAST_SPELL, source=card.id,
                                          additional_costs=(index, index)))
    assert replicate.game.player(P0).mana_pool.total == 1, "{2}{U} plus {1} twice"
    replicate.settle()
    replicate.resolve_stack()
    replicate.settle()
    replicate.resolve_stack()
    assert life(replicate) == 40 + 2 * 3, "the spell and two copies"
    assert zones_of(replicate, "Divination") == [Zone.GRAVEYARD]


def test_replicate_unpaid_makes_no_copy(replicate):
    """Control."""
    card = replicate.hand("Divination")
    give(replicate, 6, Color.BLUE)
    cast_spell(replicate.game, P0, Action(ActionKind.CAST_SPELL, source=card.id))
    replicate.settle()
    replicate.resolve_stack()
    assert life(replicate) == 42


def test_kicker_is_paid_once_however_often_it_is_announced(card_db):
    """Control: only a cost paid "any number of times" may be paid twice."""
    board = setup(card_db, {
        "Divination": (Ability.spell(GAIN_2), *keyword("Kicker", "{1}")),
    })
    card = board.hand("Divination")
    give(board, 6, Color.BLUE)
    cast_spell(board.game, P0, Action(ActionKind.CAST_SPELL, source=card.id,
                                      additional_costs=(0, 0)))
    assert board.game.player(P0).mana_pool.total == 2


# ---------------------------------------------------------------------------
# Emerge (CR 702.119a, 702.119c)
# ---------------------------------------------------------------------------


def emerge_as_the_rule_reads(cost: str) -> Ability:
    """What the Emerge builder should produce: the alternative cost *with* its
    sacrifice of a creature (the parser hands over only the mana)."""
    from mtgfish.rules.cr100_game_concepts.cr118_costs import AlternativeCost
    from mtgfish.rules.cr600_spells_and_abilities.abilities import AbilityKind
    from mtgfish.rules.kernel.query import ControllerRelation

    sacrifice = CostComponent(
        CostKind.SACRIFICE,
        amount=Value.of(1),
        filter=ObjectFilter(types_all=CardType.CREATURE, controller=ControllerRelation.YOU),
        text="sacrifice a creature",
    )
    return Ability(
        AbilityKind.STATIC,
        keyword="Emerge",
        alternative_cost=AlternativeCost(
            cost=mana(cost).with_component(sacrifice), keyword="Emerge", text="Emerge"
        ),
        functions_in=frozenset({Zone.HAND}),
        text="Emerge",
    )


@pytest.fixture
def emerge(card_db):
    return setup(card_db, {"Craw Wurm": (emerge_as_the_rule_reads("{4}{G}"),)})


def test_emerge_is_reduced_by_the_sacrificed_creatures_mana_value(emerge):
    emerge.play("Hill Giant")
    card = emerge.hand("Craw Wurm")
    give(emerge, 1, Color.GREEN)
    cast_for(emerge, card, "Emerge")
    assert emerge.game.player(P0).mana_pool.total == 0, "{4}{G} less 4 is {G}"
    assert emerge.in_graveyard() == ["Hill Giant"]
    emerge.resolve_stack()
    assert permanent(emerge, "Craw Wurm") is not None


def test_emerge_is_not_offered_when_the_discount_is_too_small(emerge):
    """Control: Grizzly Bears takes only 2 off, and {G} does not pay {2}{G}."""
    emerge.play("Grizzly Bears")
    card = emerge.hand("Craw Wurm")
    give(emerge, 1, Color.GREEN)
    assert not offered(emerge, card, alternative_cost=alternative_index(emerge, card, "Emerge"))


def test_emerge_sacrifices_the_creature_the_discount_came_from(emerge):
    """CR 702.119c: the creature chosen with the cost is the one sacrificed."""
    giant = emerge.play("Hill Giant")
    emerge.play("Grizzly Bears")

    class PicksTheGiant:
        def choose_cost_permanents(self, game, player, candidates, amount):
            return [c for c in candidates if c.id == giant.id]

    emerge.game.agents[0] = PicksTheGiant()
    card = emerge.hand("Craw Wurm")
    give(emerge, 1, Color.GREEN)
    cast_for(emerge, card, "Emerge")
    assert emerge.in_graveyard() == ["Hill Giant"]
    assert permanent(emerge, "Grizzly Bears") is not None


@pytest.mark.xfail(strict=True, reason=(
    "BUILDER BUG: Emerge is built as a bare alternative cost with no "
    "'sacrifice a creature' component"))
def test_the_emerge_builder_requires_a_sacrifice(card_db):
    board = setup(card_db, {"Craw Wurm": keyword("Emerge", "{4}{G}")})
    card = board.hand("Craw Wurm")
    give(board, 5, Color.GREEN)
    assert not offered(board, card, alternative_cost=alternative_index(board, card, "Emerge"))


# ---------------------------------------------------------------------------
# Keywords whose builder wires the wrong operations
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=(
    "BUILDER BUG: Miracle is built as an alternative cost usable from hand at "
    "any time, with no reveal-on-first-draw trigger"))
def test_miracle_is_not_a_hand_alternative_cost_at_any_time(card_db):
    board = setup(card_db, {"Hill Giant": keyword("Miracle", "{R}")})
    card = board.hand("Hill Giant")
    give(board, 1)
    assert not offered(board, card, alternative_cost=alternative_index(board, card, "Miracle"))


@pytest.mark.xfail(strict=True, reason=(
    "BUILDER BUG: Retrace is built as an alternative cost (free when the "
    "keyword has no cost) instead of permission to cast from the graveyard "
    "for the mana cost plus discarding a land card"))
def test_retrace_needs_the_mana_cost_and_a_land_to_discard(card_db):
    board = setup(card_db, {"Lava Spike": (
        Ability.spell(GAIN_2), *build(KeywordInstance("Retrace")))})
    card = board.graveyard("Lava Spike")
    assert not offered(board, card), "no mana and no land card in hand"


@pytest.mark.xfail(strict=True, reason=(
    "BUILDER BUG: Splice is built as an additional cost on the splice card "
    "itself; the engine has no way to reveal it while casting another spell"))
def test_splice_is_not_an_additional_cost_of_the_card_itself(card_db):
    board = setup(card_db, {"Glacial Ray": keyword("Splice", "{1}{R}")})
    card = board.hand("Glacial Ray")
    assert not board.chars(card).additional_costs


@pytest.mark.xfail(strict=True, reason=(
    "BUILDER BUG / ENGINE GAP: Bestow is built as a plain alternative cost; "
    "the spell is never an Aura (CR 702.103b)"))
def test_a_bestowed_spell_is_an_aura_spell(card_db):
    board = setup(card_db, {"Nyxborn Rollicker": keyword("Bestow", "{1}{R}")})
    board.play("Grizzly Bears")
    card = board.hand("Nyxborn Rollicker")
    give(board, 2)
    spell = cast_for(board, card, "Bestow")
    assert not board.chars(spell).is_creature
    assert "Aura" in board.chars(spell).type_line.subtypes


_TOKEN_KEYWORD_BUILDER_BUG = pytest.mark.xfail(strict=True, reason=(
    "BUILDER BUG: built as an alternative cost to cast the card from the "
    "graveyard; the rule is an activated ability that exiles the card and "
    "creates token copies"))


# Encore is built inert (MISBUILT_KEYWORDS) until it can be built faithfully,
# so it is already not castable from the graveyard.
@pytest.mark.parametrize(
    "name",
    [
        pytest.param("Embalm", marks=_TOKEN_KEYWORD_BUILDER_BUG),
        pytest.param("Eternalize", marks=_TOKEN_KEYWORD_BUILDER_BUG),
        "Encore",
    ],
)
def test_graveyard_token_keywords_are_activated_not_cast(card_db, name):
    board = setup(card_db, {"Hill Giant": keyword(name, "{1}")})
    card = board.graveyard("Hill Giant")
    give(board, 5)
    assert not offered(board, card), "the card itself is never cast"


def test_a_blitzed_creature_that_loses_its_abilities_draws_nothing(card_db):
    """CR 702.152a: the draw is an ability the permanent has, so it goes
    with the rest when a later effect removes them all (CR 613.1f)."""
    from mtgfish.rules.cr100_game_concepts.actions import destroy

    board = setup(
        card_db,
        {
            "Grizzly Bears": keyword("Blitz", "{R}"),
            "Humility": (
                Ability.static(
                    Effect(
                        EffectKind.REMOVE_ABILITIES,
                        targets=ObjectFilter(types_all=CardType.CREATURE),
                    ),
                    text="All creatures lose all abilities.",
                ),
            ),
        },
    )
    card = board.hand("Grizzly Bears")
    give(board, 1)
    cast_for(board, card, "Blitz")
    board.resolve_stack()
    board.play("Humility")
    board.refresh()
    before = hand_size(board)
    destroy(board.game, permanent(board, "Grizzly Bears"))
    board.settle()
    board.resolve_stack()
    assert hand_size(board) == before


def test_replicate_copies_may_be_given_new_targets(replicate):
    """CR 702.56a says so, and CR 707.10c makes it a stated permission."""
    from mtgfish.rules.cr700_additional_rules.cr707_faces import MAY_CHOOSE_NEW_TARGETS

    card = replicate.hand("Divination")
    give(replicate, 6, Color.BLUE)
    index = additional_index(replicate, card, "Replicate")
    cast_spell(replicate.game, P0, Action(ActionKind.CAST_SPELL, source=card.id,
                                          additional_costs=(index,)))
    (pending,) = [p for p in replicate.game.pending_triggers if p.ability.text == "Replicate"]
    (copy_effect,) = pending.ability.effects
    assert MAY_CHOOSE_NEW_TARGETS in copy_effect.keywords
