"""Keyword expansion (CR 702).

The builder registry is the interface the parser will call: recognise a keyword
name, read its parameter, hand over a ``KeywordInstance``. So these tests check
two things - that the expansion produces the right *shape* of ability, and that
the ability actually does the right thing when the engine runs it.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.rules.abilities import AbilityKind
from mtgfish.rules.actions import destroy
from mtgfish.rules.cr118_costs import Cost
from mtgfish.rules.cr702_keyword_impl import KeywordInstance, build, implemented_keywords
from mtgfish.rules.enums import CardType, Zone
from mtgfish.rules.ids import PlayerId


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def kw(name: str, **kwargs) -> tuple:
    return build(KeywordInstance(name, **kwargs))


# ---------------------------------------------------------------------------
# The registry itself
# ---------------------------------------------------------------------------


def test_every_builder_names_a_real_keyword(card_db):
    """A builder for a keyword that does not exist is a typo that would make
    the coverage number wrong."""
    from mtgfish.rules import keywords

    invented = sorted(k for k in implemented_keywords() if not keywords.is_known(k))
    assert not invented, f"builders for non-existent keywords: {invented}"


def test_an_unknown_keyword_is_marked_unparsed_not_dropped(board):
    """It must show up in the coverage report rather than silently doing less."""
    abilities = kw("Zibbleflorp")
    assert len(abilities) == 1
    assert abilities[0].unparsed


def test_the_registry_covers_a_useful_number_of_keywords():
    assert len(implemented_keywords()) >= 60


# ---------------------------------------------------------------------------
# Plain statics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["Flying", "Trample", "Deathtouch", "Vigilance", "Indestructible"]
)
def test_plain_statics_produce_one_static_ability(name):
    abilities = kw(name)
    assert len(abilities) == 1
    assert abilities[0].kind is AbilityKind.STATIC
    assert abilities[0].keyword == name


def test_a_built_keyword_works_in_the_engine(board):
    """The expansion has to be the same thing the engine already understands."""
    board.scripts.add("Serra Angel", *kw("Flying"), *kw("Vigilance"))
    angel = board.play("Serra Angel")
    assert board.keywords(angel) == {"Flying", "Vigilance"}


def test_ward_is_a_trigger_not_a_targeting_ban(board):
    """CR 702.21a.

    Ward taxes a spell that targets; it does not stop the spell being cast.
    Modelling it as a targeting restriction would make the opponent unable to
    even try, and would make an unpaid ward do nothing.
    """
    abilities = kw("Ward", cost=Cost.mana("{2}"))
    assert abilities[0].kind is AbilityKind.TRIGGERED
    assert abilities[0].keyword == "Ward"
    assert str(abilities[0].cost.mana_component) == "{2}", "the cost travels with it"


def test_protection_carries_its_quality(board):
    """CR 702.16a: "from red" is the whole point - a blanket ban over-protects."""
    from mtgfish.rules.enums import Color
    from mtgfish.rules.query import ObjectFilter

    red = ObjectFilter(colors_any=Color.RED)
    ability = kw("Protection", filter=red)[0]
    assert ability.keyword == "Protection"
    assert ability.quality is red


def test_landwalk_carries_the_land_type_it_walks(board):
    ability = kw("Islandwalk")[0]
    assert ability.quality is not None
    assert "Island" in ability.quality.subtypes_any


# ---------------------------------------------------------------------------
# Combat triggers
# ---------------------------------------------------------------------------


def test_exalted_triggers_on_a_lone_attacker(board):
    """CR 702.90a, with "alone" as an intervening-if (CR 603.4)."""
    from mtgfish.rules.cr506_combat import declare_attackers

    game = board.game
    game.active_player = PlayerId(0)
    board.scripts.add("Grizzly Bears", *kw("Exalted"))
    attacker = board.play("Grizzly Bears", controller=0)
    game.agents[PlayerId(0)] = FixedAgent(attackers={attacker.id: 1})

    declare_attackers(game)
    assert game.pending_triggers


def test_exalted_does_not_trigger_with_two_attackers(board):
    game = board.game
    game.active_player = PlayerId(0)
    board.scripts.add("Grizzly Bears", *kw("Exalted"))
    first = board.play("Grizzly Bears", controller=0)
    second = board.play("Grizzly Bears", controller=0)
    game.agents[PlayerId(0)] = FixedAgent(attackers={first.id: 1, second.id: 1})

    declare_attackers = __import__(
        "mtgfish.rules.combat", fromlist=["declare_attackers"]
    ).declare_attackers
    declare_attackers(game)
    assert not game.pending_triggers


def test_annihilator_carries_its_amount(board):
    abilities = kw("Annihilator", amount=2)
    assert abilities[0].kind is AbilityKind.TRIGGERED
    effect = abilities[0].effects[0]
    assert effect.amount.constant == 2


def test_afflict_triggers_on_becoming_blocked(board):
    from mtgfish.rules.cr506_combat import declare_attackers, declare_blockers

    game = board.game
    game.active_player = PlayerId(0)
    board.scripts.add("Grizzly Bears", *kw("Afflict", amount=3))
    attacker = board.play("Grizzly Bears", controller=0)
    blocker = board.play("Hill Giant", controller=1)
    game.agents[PlayerId(0)] = FixedAgent(attackers={attacker.id: 1})
    game.agents[PlayerId(1)] = FixedAgent(blockers={blocker.id: [attacker.id]})

    declare_attackers(game)
    game.pending_triggers.clear()
    declare_blockers(game)
    assert game.pending_triggers


# ---------------------------------------------------------------------------
# Death triggers
# ---------------------------------------------------------------------------


def test_persist_triggers_on_death(board):
    game = board.game
    board.scripts.add("Grizzly Bears", *kw("Persist"))
    bears = board.play("Grizzly Bears", controller=0)

    destroy(game, bears)
    assert game.pending_triggers


def test_undying_triggers_on_death(board):
    game = board.game
    board.scripts.add("Grizzly Bears", *kw("Undying"))
    bears = board.play("Grizzly Bears", controller=0)

    destroy(game, bears)
    assert game.pending_triggers


def test_afterlife_makes_the_right_number_of_tokens(board):
    abilities = kw("Afterlife", amount=2)
    effect = abilities[0].effects[0]
    assert effect.token is not None
    assert effect.token.subtypes == ("Spirit",)
    assert "Flying" in effect.token.keywords
    assert effect.amount.constant == 2


# ---------------------------------------------------------------------------
# Enters-with-counters keywords
# ---------------------------------------------------------------------------


def test_modular_is_two_abilities(board):
    """One keyword, one enters-with-counters and one dies trigger."""
    abilities = kw("Modular", amount=3)
    assert len(abilities) == 2
    assert {a.kind for a in abilities} == {AbilityKind.STATIC, AbilityKind.TRIGGERED}


def test_graft_enters_with_counters(board):
    abilities = kw("Graft", amount=2)
    effect = abilities[0].effects[0]
    assert effect.counter_type == "+1/+1"
    assert effect.amount.constant == 2


def test_vanishing_is_counters_plus_an_upkeep_trigger(board):
    abilities = kw("Vanishing", amount=3)
    assert len(abilities) == 2
    assert abilities[0].effects[0].counter_type == "time"
    assert abilities[1].kind is AbilityKind.TRIGGERED


# ---------------------------------------------------------------------------
# Cost keywords
# ---------------------------------------------------------------------------


def test_kicker_is_an_optional_additional_cost(board):
    abilities = kw("Kicker", cost=Cost.mana("{2}"))
    assert abilities[0].additional_cost is not None
    assert abilities[0].additional_cost.optional
    assert str(abilities[0].additional_cost.cost.mana_component) == "{2}"


def test_flashback_is_a_graveyard_alternative_cost(board):
    abilities = kw("Flashback", cost=Cost.mana("{3}{R}"))
    alternative = abilities[0].alternative_cost
    assert alternative is not None
    assert alternative.from_zone is Zone.GRAVEYARD
    assert str(alternative.cost.mana_component) == "{3}{R}"


def test_flashback_actually_makes_a_graveyard_card_castable(board):
    """End to end: the keyword expansion feeds the legality rules from CR 118.9."""
    from mtgfish.rules.cr106_mana import ManaKind
    from mtgfish.rules.cr117_priority import ActionKind
    from mtgfish.rules.enums import LETTER_TO_COLOR, Phase, Step
    from mtgfish.rules.legality import legal_actions

    game = board.game
    game.active_player = PlayerId(0)
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN

    board.scripts.add("Lightning Bolt", *kw("Flashback", cost=Cost.mana("{R}")))
    card = board.graveyard("Lightning Bolt", controller=0)
    game.player(PlayerId(0)).mana_pool.add(ManaKind(LETTER_TO_COLOR["R"]), 3)

    casts = [
        a
        for a in legal_actions(game, PlayerId(0))
        if a.kind is ActionKind.CAST_SPELL and a.source == card.id
    ]
    assert casts and casts[0].alternative_cost == 0


def test_dash_is_a_hand_alternative_cost(board):
    alternative = kw("Dash", cost=Cost.mana("{1}{R}"))[0].alternative_cost
    assert alternative is not None
    assert alternative.from_zone is None


# ---------------------------------------------------------------------------
# Activated-ability keywords
# ---------------------------------------------------------------------------


def test_equip_is_a_sorcery_speed_activated_ability(board):
    from mtgfish.rules.enums import Timing

    ability = kw("Equip", cost=Cost.mana("{2}"))[0]
    assert ability.kind is AbilityKind.ACTIVATED
    assert ability.timing is Timing.SORCERY
    assert str(ability.cost.mana_component) == "{2}"


def test_cycling_functions_from_hand_and_discards_itself(board):
    from mtgfish.rules.cr118_costs import CostKind

    ability = kw("Cycling", cost=Cost.mana("{2}"))[0]
    assert Zone.HAND in ability.functions_in
    assert any(c.kind is CostKind.DISCARD for c in ability.cost.components)


def test_unearth_functions_from_the_graveyard(board):
    ability = kw("Unearth", cost=Cost.mana("{1}{B}"))[0]
    assert Zone.GRAVEYARD in ability.functions_in


def test_crew_animates_the_vehicle(board):
    from mtgfish.rules.effects import EffectKind

    ability = kw("Crew", amount=3)[0]
    assert ability.effects[0].kind is EffectKind.ADD_TYPE
    assert ability.effects[0].types & CardType.CREATURE


# ---------------------------------------------------------------------------
# Spell-cast triggers
# ---------------------------------------------------------------------------


def test_prowess_triggers_on_a_noncreature_spell(board):
    from mtgfish.rules.cr106_mana import ManaKind
    from mtgfish.rules.cr117_priority import Action, ActionKind
    from mtgfish.rules.cr601_casting import cast_spell
    from mtgfish.rules.enums import LETTER_TO_COLOR, Phase, Step

    game = board.game
    game.active_player = PlayerId(0)
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN

    board.scripts.add("Monastery Swiftspear", *kw("Prowess"))
    board.play("Monastery Swiftspear", controller=0)
    card = board.hand("Lightning Bolt", controller=0)
    game.player(PlayerId(0)).mana_pool.add(ManaKind(LETTER_TO_COLOR["R"]), 3)
    game.pending_triggers.clear()

    cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    assert game.pending_triggers


def test_prowess_does_not_trigger_on_a_creature_spell(board):
    from mtgfish.rules.cr106_mana import ManaKind
    from mtgfish.rules.cr117_priority import Action, ActionKind
    from mtgfish.rules.cr601_casting import cast_spell
    from mtgfish.rules.enums import LETTER_TO_COLOR, Phase, Step

    game = board.game
    game.active_player = PlayerId(0)
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN

    board.scripts.add("Monastery Swiftspear", *kw("Prowess"))
    board.play("Monastery Swiftspear", controller=0)
    card = board.hand("Grizzly Bears", controller=0)
    game.player(PlayerId(0)).mana_pool.add(ManaKind(LETTER_TO_COLOR["G"]), 3)
    game.pending_triggers.clear()

    cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    assert not game.pending_triggers


def test_evolve_triggers_on_a_creature_entering(board):
    from mtgfish.rules.events import Event, EventKind

    game = board.game
    board.scripts.add("Experiment One", *kw("Evolve"))
    board.play("Experiment One", controller=0)
    game.pending_triggers.clear()

    newcomer = board.play("Grizzly Bears", controller=0)
    game.emit(
        Event(EventKind.ENTERS_BATTLEFIELD, object_id=newcomer.id, player=PlayerId(0))
    )
    assert game.pending_triggers
