"""The keyword actions of CR 701.65 - 701.68, plus convert and Attractions.

Each of these used to build a correctly named effect list holding one
``UNPARSED`` node, which the registry reported as PARTIAL and the engine
executed as nothing at all. The tests here are about what the expansions
*do* - the land really becomes a creature, the counters really land on one
creature - so that a builder cannot go back to naming the action without
performing it.

No card is named for its behaviour. Real cards are placed on the board because
the harness needs something to put there, and every effect under test is built
by ``cr701_keyword_actions.build`` exactly as the parser would build it.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.cr700_additional_rules.cr701_keyword_actions import build
from mtgfish.rules.kernel.enums import CardType, Zone
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ObjectFilter


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def run(board, effects, *, controller: int = 0, source: int = 0, targets=()):
    """Resolve a keyword action's effects, as a spell or ability would."""
    resolution = Resolution(
        game=board.game,
        source=source,
        controller=PlayerId(controller),
        targets=targets,
    )
    execute(resolution, effects)
    board.refresh()
    return resolution


def unparsed_nodes(effects) -> list:
    return [node for e in effects for node in e.walk() if node.kind is EffectKind.UNPARSED]


# ---------------------------------------------------------------------------
# Control: an action with no rule behind it still refuses to invent one
# ---------------------------------------------------------------------------


def test_an_action_the_rules_decline_to_define_stays_unparsed():
    """The control for every test below.

    CR 701.45a says the Comprehensive Rules do not cover the set assembling a
    Contraption comes from. There is no rule to implement, so the builder must
    still report a gap - and this passes both before and after the bending
    actions were built, which is what makes it a control rather than a
    restatement of the change.
    """
    assert unparsed_nodes(build("Assemble", amount=1))


def test_an_action_nobody_has_printed_is_still_unparsed():
    """A second control: the registry's failure mode is unchanged."""
    assert unparsed_nodes(build("Heist", amount=1))


# ---------------------------------------------------------------------------
# CR 701.66 - Earthbend
# ---------------------------------------------------------------------------


def test_earthbend_expands_to_effects_rather_than_a_gap():
    assert not unparsed_nodes(build("Earthbend", amount=2))


def test_earthbend_animates_the_targeted_land(board):
    """CR 701.66a: it becomes a 0/0 land creature with haste and takes N
    counters - so an earthbend 3 leaves a 3/3 that can attack at once."""
    land = board.play("Forest", controller=0)
    run(board, build("Earthbend", amount=3), targets=((land.id,),))

    chars = board.chars(land)
    assert chars.type_line.has_type(CardType.CREATURE)
    assert chars.type_line.has_type(CardType.LAND), "it is still a land"
    assert board.pt(land) == (3, 3)
    assert "Haste" in board.keywords(land)
    assert land.counter_count("+1/+1") == 3


def test_earthbend_leaves_your_other_lands_alone(board):
    """The set of affected objects is the one target, not every land you
    control - which is the failure an untargeted filter would produce."""
    chosen = board.play("Forest", controller=0)
    bystander = board.play("Forest", controller=0)
    run(board, build("Earthbend", amount=2), targets=((chosen.id,),))

    assert board.chars(chosen).type_line.has_type(CardType.CREATURE)
    assert not board.chars(bystander).type_line.has_type(CardType.CREATURE)
    assert bystander.counter_count("+1/+1") == 0


def test_earthbend_targets_only_lands_you_control(board):
    """CR 701.66a says "target land you control", so an opponent's land is not
    a legal target and the effect skips it (CR 608.2b)."""
    theirs = board.play("Forest", controller=1)
    run(board, build("Earthbend", amount=2), targets=((theirs.id,),))

    assert theirs.counter_count("+1/+1") == 0
    assert not board.chars(theirs).type_line.has_type(CardType.CREATURE)


def test_earthbend_sets_up_the_return_when_that_land_leaves(board):
    """CR 701.66a's last sentence is a delayed triggered ability (CR 603.7),
    created as the earthbend resolves and waiting on two events."""
    land = board.play("Forest", controller=0)
    run(board, build("Earthbend", amount=1), targets=((land.id,),))

    assert len(board.game.delayed_triggers) == 1
    delayed = board.game.delayed_triggers[0]
    assert delayed.trigger.event_kinds == frozenset({EventKind.DIES, EventKind.EXILED})
    returning = delayed.effects[0]
    assert returning.kind is EffectKind.PUT_ONTO_BATTLEFIELD
    assert "tapped" in returning.keywords


def test_the_return_fires_when_the_earthbent_land_dies(board):
    """The delayed ability is waiting on the right event, and the right event
    alone: destroying the land puts it on the stack."""
    from mtgfish.rules.cr100_game_concepts import actions

    land = board.play("Forest", controller=0)
    run(board, build("Earthbend", amount=1), targets=((land.id,),))
    actions.destroy(board.game, land, source=0)
    board.settle()
    assert len(board.game.stack) == 1


@pytest.mark.xfail(
    reason="a delayed trigger does not carry what the effect that created it "
    "remembered (resolve.create_delayed_trigger never fills "
    "DelayedTrigger.remembered), so 'return it' has no 'it' to return",
)
def test_the_earthbent_land_comes_back_tapped(board):
    from mtgfish.rules.cr100_game_concepts import actions

    land = board.play("Forest", controller=0)
    run(board, build("Earthbend", amount=1), targets=((land.id,),))
    actions.destroy(board.game, land, source=0)
    board.settle()
    board.resolve_stack()
    assert board.alive(0) == ["Forest"]


def test_earthbend_does_not_animate_before_it_resolves(board):
    """A control: the board is untouched until the effects are executed."""
    land = board.play("Forest", controller=0)
    build("Earthbend", amount=3)
    assert not board.chars(land).type_line.has_type(CardType.CREATURE)


# ---------------------------------------------------------------------------
# CR 701.68 - Blight
# ---------------------------------------------------------------------------


def test_blight_expands_to_effects_rather_than_a_gap():
    assert not unparsed_nodes(build("Blight", amount=1))


def test_blight_puts_minus_counters_on_one_creature_you_control(board):
    """CR 701.68a: N -1/-1 counters on *a* creature you control."""
    chosen = board.play("Grizzly Bears", controller=0)
    bystander = board.play("Grizzly Bears", controller=0)
    run(board, build("Blight", amount=1), targets=((chosen.id,),))

    assert chosen.counter_count("-1/-1") == 1
    assert bystander.counter_count("-1/-1") == 0
    assert board.pt(chosen) == (1, 1)


def test_blight_can_finish_a_creature_off(board):
    """The counters are real counters, so CR 704.5f applies to the result."""
    bears = board.play("Grizzly Bears", controller=0)
    run(board, build("Blight", amount=2), targets=((bears.id,),))
    assert board.pt(bears) == (0, 0)
    board.sba()
    assert "Grizzly Bears" not in board.alive(0)


def test_blight_is_not_pointed_at_a_creature_you_do_not_control(board):
    """CR 701.68a is "a creature you control"; an opponent's is not one."""
    theirs = board.play("Grizzly Bears", controller=1)
    run(board, build("Blight", amount=2), targets=((theirs.id,),))
    assert theirs.counter_count("-1/-1") == 0


def test_the_blighted_creature_is_what_the_next_sentence_means(board):
    """CR 701.68c: a card that says "the blighted creature" means the one that
    was chosen, and the resolution has to still know which that was."""
    chosen = board.play("Grizzly Bears", controller=0)
    board.play("Grizzly Bears", controller=0)
    resolution = run(board, build("Blight", amount=1), targets=((chosen.id,),))
    assert list(resolution.remembered) == [chosen.id]


# ---------------------------------------------------------------------------
# CR 701.28 - Convert
# ---------------------------------------------------------------------------


def test_convert_expands_to_effects_rather_than_a_gap():
    assert not unparsed_nodes(build("Convert", amount=1))


def test_convert_turns_a_double_faced_permanent_over(board):
    """CR 701.28a: converting follows the transform rules, so it is the same
    act on the same object - a new face, not a new permanent."""
    obj = board.play("Ulvenwald Captive", controller=0)
    front = board.chars(obj).name
    run(
        board,
        build("Convert", filter=ObjectFilter(specific=(obj.id,))),
        source=obj.id,
    )
    assert board.chars(obj).name != front
    assert obj.id in board.game.battlefield


def test_convert_does_nothing_to_a_single_faced_permanent(board):
    """CR 701.28c: a permanent with no other face is left alone."""
    obj = board.play("Grizzly Bears", controller=0)
    run(
        board,
        build("Convert", filter=ObjectFilter(specific=(obj.id,))),
        source=obj.id,
    )
    assert board.chars(obj).name == "Grizzly Bears"


# ---------------------------------------------------------------------------
# CR 701.65 - Airbend
# ---------------------------------------------------------------------------


def test_airbend_expands_to_effects_rather_than_a_gap():
    assert not unparsed_nodes(build("Airbend", amount=1))


def test_airbend_exiles_what_it_names(board):
    bears = board.play("Grizzly Bears", controller=0)
    run(board, build("Airbend", filter=ObjectFilter(specific=(bears.id,))))
    assert bears.id not in board.game.battlefield


def test_airbend_grants_the_exiled_card_a_two_mana_alternative_cost():
    """CR 701.65a: while it stays exiled, its owner may cast it by paying {2}
    instead of its mana cost - an alternative cost (CR 118.9) that works from
    exile (CR 604.6)."""
    grant = build("Airbend")[1]
    assert grant.kind is EffectKind.GRANT_ABILITY
    granted = grant.granted_abilities[0]
    assert str(granted.alternative_cost.cost) == "{2}"
    assert granted.alternative_cost.from_zone is Zone.EXILE
    assert granted.functions_in == frozenset({Zone.EXILE})


def test_airbend_grants_it_to_cards_and_not_to_tokens():
    """CR 701.65a says "for each card exiled this way". A token ceases to
    exist in exile and has no mana cost to pay {2} instead of."""
    grant = build("Airbend")[1]
    assert grant.targets.is_token is False
    assert grant.targets.remembered, "it is the objects just exiled"


@pytest.mark.xfail(
    reason="continuous effects are not computed outside the battlefield and "
    "the stack (cr613_layers.compute_characteristics), and casting legality "
    "reads printed alternative costs (kernel/legality), so a grant made to a "
    "card in exile cannot reach it yet",
)
def test_airbend_makes_the_exiled_card_castable_for_two(board):
    bears = board.play("Grizzly Bears", controller=0)
    run(board, build("Airbend", filter=ObjectFilter(specific=(bears.id,))))

    exiled = board.game.objects[bears.id]
    while exiled.superseded_by:
        exiled = board.game.objects[exiled.superseded_by]
    assert exiled.zone is Zone.EXILE
    assert [
        ability
        for ability in board.chars(exiled).abilities
        if ability.alternative_cost is not None
    ]


def test_airbend_leaves_the_rest_of_the_board_alone(board):
    """A control: only what the instruction named is exiled."""
    bears = board.play("Grizzly Bears", controller=0)
    bystander = board.play("Grizzly Bears", controller=0)
    run(board, build("Airbend", filter=ObjectFilter(specific=(bears.id,))))
    assert bystander.id in board.game.battlefield


# ---------------------------------------------------------------------------
# CR 701.67 - Waterbend
# ---------------------------------------------------------------------------


def test_waterbend_expands_to_effects_rather_than_a_gap():
    assert not unparsed_nodes(build("Waterbend", amount=2))


def test_waterbend_charges_one_payment_per_generic_mana(board):
    """CR 701.67a: the choice is made once per generic mana in the cost, not
    once for the cost, so waterbend {3} is three payments."""
    effects = build("Waterbend", amount=3)
    assert len(effects) == 1
    assert effects[0].kind is EffectKind.REPEAT
    assert effects[0].amount.constant == 3
    payment = effects[0].children[0]
    assert payment.kind is EffectKind.PAY_COST
    assert len(payment.pay_cost.choices) == 2


def test_waterbend_spends_mana_when_that_is_the_way_it_is_paid(board):
    board.game.player(PlayerId(0)).mana_pool.add(ManaKind(), 5)
    run(board, build("Waterbend", amount=3))
    assert board.game.player(PlayerId(0)).mana_pool.total == 2


def test_waterbend_offers_tapping_instead_of_paying(board):
    """The other half of CR 701.67a: an untapped artifact or creature you
    control may stand in for one generic mana."""
    payment = build("Waterbend", amount=1)[0].children[0]
    tapping = payment.pay_cost.choices[1].components[0]
    assert tapping.filter.tapped is False
    assert tapping.filter.types_any == CardType.ARTIFACT | CardType.CREATURE


# ---------------------------------------------------------------------------
# CR 701.51 - Open an Attraction
# ---------------------------------------------------------------------------


def test_open_an_attraction_expands_to_effects_rather_than_a_gap():
    assert not unparsed_nodes(build("Open an Attraction"))


def test_open_an_attraction_puts_one_onto_the_battlefield(board):
    """CR 701.51b: off the Attraction deck and onto the battlefield under your
    control. CR 717.2 keeps that deck in the command zone, which is where this
    one is placed."""
    card = board.db.lookup("Push Your Luck")
    attraction = board.game.create_object(card, PlayerId(0), Zone.COMMAND)
    board.refresh()

    run(board, build("Open an Attraction"))
    # CR 400.7: it is a new object on the battlefield, so it is followed
    # rather than looked up by the id it had in the command zone.
    opened = attraction
    while opened.superseded_by:
        opened = board.game.objects[opened.superseded_by]
    assert opened.zone is Zone.BATTLEFIELD
    assert opened.controller == PlayerId(0)
    assert attraction.id not in board.game.zone_list(Zone.COMMAND)


def test_open_an_attraction_does_nothing_without_an_attraction_deck(board):
    """CR 701.51a: a player may only open one in a game where they are playing
    with an Attraction deck."""
    before = list(board.game.battlefield)
    run(board, build("Open an Attraction"))
    assert list(board.game.battlefield) == before


def test_open_an_attraction_does_not_take_someone_elses(board):
    """A control on the ownership half of CR 701.51b: it is *your* deck."""
    card = board.db.lookup("Push Your Luck")
    theirs = board.game.create_object(card, PlayerId(1), Zone.COMMAND)
    board.refresh()

    run(board, build("Open an Attraction"), controller=0)
    assert theirs.zone is Zone.COMMAND
    assert not theirs.superseded_by
