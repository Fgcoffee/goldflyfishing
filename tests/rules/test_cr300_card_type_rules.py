"""What each card type brings with it (CR 301-310).

Section 3 is mostly not a subsystem. Nearly every rule in it is a sentence
about a mechanism that lives somewhere else - the casting timing is the
legality check, the resolution is the stack, "damage removes loyalty counters"
is the damage action, and half a dozen more are state-based actions. The
package index in ``mtgfish/rules/cr300_card_types/__init__.py`` says where each
one went; this says whether it is actually there.

So these tests deliberately look at the engine from outside rather than at one
module: the question each asks is the one the rule asks, and the answer has to
come out of whatever code happens to produce it.

The battle protector (CR 310.9 and CR 310.11-310.12b) is *not* here; it lives
in ``test_cr310_battle_protector.py``, where the designation, the choice as a
battle enters, the CR 704.5x state-based action and the defending-player
lookup are covered together. CR 310.12b, the Siege's intrinsic ability, is
still missing and is a strict xfail there.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind
from mtgfish.rules.cr500_turn_structure.cr500_turn import _clear_damage_and_expire_effects
from mtgfish.rules.cr500_turn_structure.cr506_combat import (
    AttackPermanent,
    _attack_is_permitted,
    can_attack,
    can_block_at_all,
)
from mtgfish.rules.cr500_turn_structure.restrictions import Act, Restriction, register_standing
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.cr608_stack import resolve_top
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import CardType, Color, Phase, Zone
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.rules.kernel.query import PlayerFilter, PlayerScope, Value

#: One card of each type the section covers, chosen for having nothing
#: interesting about it beyond its type line.
ARTIFACT = "Sol Ring"
CREATURE = "Grizzly Bears"
ENCHANTMENT = "Wild Growth"  # Aura, so the Aura path gets exercised too
INSTANT = "Lightning Bolt"
SORCERY = "Rampant Growth"
PLANESWALKER = "Chandra, Torch of Defiance"  # printed loyalty 4
SIEGE = "Invasion of Gobakhan"  # Battle - Siege, printed defense 3
BATTLE = "Occupation of Kulrath"  # Battle - Control Point, printed defense 2

#: The five types whose spells become permanents, and the rules that say so.
PERMANENT_SPELLS = [
    pytest.param(ARTIFACT, id="artifact-301"),
    pytest.param(CREATURE, id="creature-302"),
    pytest.param(ENCHANTMENT, id="enchantment-303"),
    pytest.param(PLANESWALKER, id="planeswalker-306"),
    pytest.param(SIEGE, id="battle-310"),
]


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def enters(board, name, controller=0):
    """Put a card onto the battlefield the way a zone change would.

    ``Board.play`` builds the object in place, which skips ``move_object`` and
    with it every "as this enters" rule - loyalty and defense counters above
    all (CR 306.5b, 310.4b). Anything that cares about counters has to arrive
    this way instead.
    """
    card = board.hand(name, controller=controller)
    return board.game.move_object(card, Zone.BATTLEFIELD, to_player=controller)


def put_on_the_stack(board, name, controller=0):
    """A spell on the stack, controlled by the player who cast it.

    ``base_controller`` is set alongside ``controller`` because layer 2
    recomputes the second from the first on every board evaluation (CR 613.1b):
    setting only ``controller`` describes a spell whose control has been
    stolen and is about to be handed back, which is not what any of this is
    testing.
    """
    card = board.hand(name, controller=controller)
    spell = board.game.move_object(card, Zone.STACK, to_player=controller)
    spell.controller = controller
    spell.base_controller = controller
    board.game.stack.append(spell.id)
    return spell


def castable(board, player=0):
    """The names of the cards this player is currently offered as spells."""
    return {
        board.game.printed_characteristics(board.game.objects[action.source]).name
        for action in legal_actions(board.game, player)
        if action.kind is ActionKind.CAST_SPELL
    }


def with_a_full_hand(board):
    """Every type in hand, and enough lands to pay for any of it."""
    for name in (ARTIFACT, CREATURE, ENCHANTMENT, INSTANT, SORCERY, PLANESWALKER, SIEGE):
        board.hand(name, controller=0)
    # Every colour, because affordability is solved exactly now: twenty Forests
    # were "enough lands" only while the check counted lands, and Chandra
    # still needs {R}{R}.
    for _ in range(4):
        for basic in ("Plains", "Island", "Swamp", "Mountain", "Forest"):
            board.play(basic, controller=0)
    board.game.phase = Phase.PRECOMBAT_MAIN
    return board


# ---------------------------------------------------------------------------
# Casting timing: CR 301.1, 302.1, 303.1, 304.1, 306.1, 310.1
# ---------------------------------------------------------------------------


def test_every_permanent_type_is_castable_at_sorcery_speed(board):
    """CR 301.1, 302.1, 303.1, 306.1, 310.1: a main phase of your turn with
    the stack empty. All five rules are one sentence repeated five times."""
    with_a_full_hand(board)

    assert {ARTIFACT, CREATURE, ENCHANTMENT, PLANESWALKER, SIEGE} <= castable(board)


def test_no_permanent_type_is_castable_outside_a_main_phase(board):
    """The other half of the same five rules."""
    with_a_full_hand(board)
    board.game.phase = Phase.BEGINNING

    assert not {ARTIFACT, CREATURE, ENCHANTMENT, PLANESWALKER, SIEGE} & castable(board)


def test_a_sorcery_is_not_castable_outside_a_main_phase(board):
    """CR 307.1, the rule the other five defer to."""
    with_a_full_hand(board)
    board.game.phase = Phase.BEGINNING

    assert SORCERY not in castable(board)


def test_an_instant_is_castable_outside_a_main_phase(board):
    """CR 304.1: no phase, no turn and no empty stack in the permission - the
    control that shows the five rules above are about the card type and not
    about the window being closed for everyone."""
    with_a_full_hand(board)
    board.game.phase = Phase.BEGINNING

    assert INSTANT in castable(board)


def test_an_instant_is_castable_on_an_opponents_turn(board):
    """CR 304.1 again, from the other side: the turn is not part of it."""
    with_a_full_hand(board)
    board.game.active_player = 1

    assert castable(board) & {INSTANT} == {INSTANT}
    assert not {ARTIFACT, CREATURE, ENCHANTMENT, PLANESWALKER, SIEGE} & castable(board)


def test_a_permanent_spell_is_not_castable_with_a_spell_on_the_stack(board):
    """"When the stack is empty" is the third clause of all five rules."""
    with_a_full_hand(board)
    put_on_the_stack(board, INSTANT, controller=1)

    assert not {ARTIFACT, CREATURE, ENCHANTMENT, PLANESWALKER, SIEGE} & castable(board)


# ---------------------------------------------------------------------------
# Resolution: CR 301.2, 302.2, 303.2, 306.2, 310.2, and CR 304.2, 307.2
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", PERMANENT_SPELLS)
def test_a_permanent_spell_resolves_onto_the_battlefield(board, name):
    """CR 301.2, 302.2, 303.2, 306.2, 310.2: its controller puts it onto the
    battlefield under their control."""
    put_on_the_stack(board, name, controller=0)
    resolve_top(board.game)

    assert name in board.alive(0)


@pytest.mark.parametrize("name", PERMANENT_SPELLS)
def test_a_permanent_spell_does_not_go_to_the_graveyard(board, name):
    """The same five rules, stated as what does *not* happen: a permanent
    spell is not an instant that leaves something behind."""
    put_on_the_stack(board, name, controller=0)
    resolve_top(board.game)

    assert name not in board.in_graveyard(0)


def test_a_resolving_permanent_arrives_under_its_controllers_control(board):
    """"Under their control" - the controller of the spell, who need not be
    its owner."""
    spell = put_on_the_stack(board, CREATURE, controller=0)
    spell.controller = spell.base_controller = 1
    resolve_top(board.game)

    assert CREATURE in board.alive(1)
    assert CREATURE not in board.alive(0)


@pytest.mark.parametrize("name", [INSTANT, SORCERY])
def test_an_instant_or_sorcery_goes_to_its_owners_graveyard(board, name):
    """CR 304.2 and CR 307.2: the actions are followed, then it is put into
    its owner's graveyard. The control for the five tests above."""
    put_on_the_stack(board, name, controller=0)
    resolve_top(board.game)

    assert name in board.in_graveyard(0)
    assert name not in board.alive(0)


# ---------------------------------------------------------------------------
# Subtypes: CR 301.3, 302.3, 303.3, 304.3, 306.3, 307.3, 310.3
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("Bone Saw", ("Equipment",), id="artifact-301.3"),
        pytest.param("Grizzly Bears", ("Bear",), id="creature-302.3"),
        pytest.param("Wild Growth", ("Aura",), id="enchantment-303.3"),
        pytest.param("Chandra, Torch of Defiance", ("Chandra",), id="planeswalker-306.3"),
        pytest.param("Invasion of Gobakhan", ("Siege",), id="battle-310.3"),
    ],
)
def test_subtypes_are_read_off_the_type_line(board, name, expected):
    """CR 301.3, 302.3, 303.3, 306.3, 310.3: everything after the long dash.
    One registry answers for every card type, which is why these are one
    test - the rules differ only in which names are legal for the type."""
    card = board.hand(name, controller=0)

    assert board.game.printed_characteristics(card).type_line.subtypes == expected


def test_a_creature_may_have_several_subtypes(board):
    """CR 302.3: "Creature - Human Knight" is two creature types, not one."""
    card = board.hand("Benalish Marshal", controller=0)

    assert board.game.printed_characteristics(card).type_line.subtypes == (
        "Human",
        "Knight",
    )


def test_a_multiword_subtype_is_one_subtype(board):
    """The reason CR 301.3's "always a single word" cannot be taken at face
    value for creatures: CR 302.3 says "usually", and Time Lord is why."""
    card = board.hand("The Fourth Doctor", controller=0)

    assert "Time Lord" in board.game.printed_characteristics(card).type_line.subtypes


@pytest.mark.parametrize("name", ["Lightning Bolt", "Rampant Growth"])
def test_an_instant_or_sorcery_with_no_subtypes_has_none(board, name):
    """CR 304.3 and CR 307.3 share one list of spell types, and most spells
    are on neither. The control: an absent subtype is absent, not invented."""
    card = board.hand(name, controller=0)

    assert board.game.printed_characteristics(card).type_line.subtypes == ()


def test_arcane_is_a_spell_type_on_both_instants_and_sorceries(board):
    """CR 304.3 / 307.3: "the set of instant subtypes is the same as the set
    of sorcery subtypes"."""
    instant = board.hand("Reach Through Mists", controller=0)  # Instant - Arcane
    sorcery = board.hand("Kodama's Reach", controller=0)  # Sorcery - Arcane

    assert board.game.printed_characteristics(instant).type_line.subtypes == ("Arcane",)
    assert board.game.printed_characteristics(sorcery).type_line.subtypes == ("Arcane",)


# ---------------------------------------------------------------------------
# CR 301.4: artifacts have no characteristics of their own
# ---------------------------------------------------------------------------


def test_an_artifact_may_be_coloured(board):
    """CR 301.4: "there is no correlation between being colorless and being an
    artifact". Baleful Strix is an artifact creature and is blue-black."""
    card = board.hand("Baleful Strix", controller=0)
    chars = board.game.printed_characteristics(card)

    assert chars.has_type(CardType.ARTIFACT)
    assert chars.colors == Color.BLUE | Color.BLACK


def test_a_colourless_card_need_not_be_an_artifact(board):
    """The other direction of the same sentence."""
    card = board.hand("Kozilek, Butcher of Truth", controller=0)
    chars = board.game.printed_characteristics(card)

    assert not chars.has_type(CardType.ARTIFACT)
    assert chars.colors == Color.NONE


def test_an_artifact_gets_no_power_or_toughness_for_being_an_artifact(board):
    """CR 301.4: no characteristics specific to the card type. Power and
    toughness would be the obvious one to hand out, and CR 208.3 does not."""
    ring = board.play(ARTIFACT, controller=0)

    assert board.pt(ring) == (None, None)


# ---------------------------------------------------------------------------
# CR 302.5: creatures attack and block
# ---------------------------------------------------------------------------


def test_a_creature_can_attack(board):
    """CR 302.5, and CR 508: being a creature is the whole requirement."""
    bear = board.play(CREATURE, controller=0)

    assert can_attack(board.game, bear)


def test_a_creature_can_block(board):
    """CR 302.5, and CR 509."""
    bear = board.play(CREATURE, controller=0)

    assert can_block_at_all(board.game, bear)


def test_a_noncreature_permanent_can_neither_attack_nor_block(board):
    """The control: CR 302.5 is a creature rule, and an artifact is not
    offered the ability by being a permanent."""
    ring = board.play(ARTIFACT, controller=0)

    assert not can_attack(board.game, ring)
    assert not can_block_at_all(board.game, ring)


# ---------------------------------------------------------------------------
# CR 302.7: damage marked on a creature
# ---------------------------------------------------------------------------


def test_damage_to_a_creature_is_marked_on_it(board):
    """CR 302.7: marked, not subtracted from toughness."""
    bear = board.play(CREATURE, controller=0)
    source = board.play(CREATURE, controller=1)
    actions.deal_damage(board.game, bear, 1, source=source.id)

    assert bear.damage == 1
    assert board.pt(bear) == (2, 2)


def test_a_creature_survives_damage_less_than_its_toughness(board):
    """The control: marked damage is not itself lethal."""
    bear = board.play(CREATURE, controller=0)
    source = board.play(CREATURE, controller=1)
    actions.deal_damage(board.game, bear, 1, source=source.id)
    board.sba()

    assert CREATURE in board.alive(0)


def test_marked_damage_equal_to_toughness_is_lethal(board):
    """CR 302.7: destroyed as a state-based action (CR 704.5g)."""
    bear = board.play(CREATURE, controller=0)
    source = board.play(CREATURE, controller=1)
    actions.deal_damage(board.game, bear, 2, source=source.id)
    board.sba()

    assert CREATURE not in board.alive(0)
    assert CREATURE in board.in_graveyard(0)


def test_marked_damage_is_removed_during_the_cleanup_step(board):
    """CR 302.7 with CR 514.2 - which is why it is marked rather than
    applied, and why a creature damaged on two turns is not cumulatively
    weaker."""
    bear = board.play(CREATURE, controller=0)
    source = board.play(CREATURE, controller=1)
    actions.deal_damage(board.game, bear, 1, source=source.id)
    _clear_damage_and_expire_effects(board.game)

    assert bear.damage == 0


# ---------------------------------------------------------------------------
# CR 304.5: "any time they could cast an instant" means "has priority"
# ---------------------------------------------------------------------------


def a_player_who_cannot_cast_spells(board):
    register_standing(
        board.game,
        Restriction(
            act=Act.CAST_SPELL,
            players=PlayerFilter(scope=PlayerScope.EACH_PLAYER),
            text="players can't cast spells",
        ),
    )


def an_instant_speed_ability(board):
    """A free activated ability on a permanent, with no timing of its own.

    ``Timing.INSTANT`` is the default (CR 602.2), so this is exactly the
    "activate any time you have priority" case CR 304.5 is about.
    """
    board.scripts.add(
        ARTIFACT,
        Ability(
            AbilityKind.ACTIVATED,
            effects=(Effect(EffectKind.DRAW, amount=Value.of(1)),),
            text="draw a card",
        ),
    )
    obj = board.play(ARTIFACT, controller=0)
    board.refresh()
    return obj


def action_kinds(board, player=0):
    return {action.kind for action in legal_actions(board.game, player)}


def test_an_effect_that_stops_casting_does_not_stop_activating(board):
    """CR 304.5: an effect that would preclude a player from casting an
    instant does not affect what they may do "any time they could cast an
    instant". The engine gets this by asking about casting a spell as its own
    act, separate from activating an ability."""
    with_a_full_hand(board)
    an_instant_speed_ability(board)
    a_player_who_cannot_cast_spells(board)

    assert ActionKind.ACTIVATE_ABILITY in action_kinds(board)


def test_the_prohibition_really_did_stop_casting(board):
    """The control that keeps the test above from passing vacuously."""
    with_a_full_hand(board)
    an_instant_speed_ability(board)

    assert ActionKind.CAST_SPELL in action_kinds(board)
    a_player_who_cannot_cast_spells(board)
    assert ActionKind.CAST_SPELL not in action_kinds(board)


# ---------------------------------------------------------------------------
# Planeswalkers: CR 306.4, 306.6, 306.7, 306.8, 306.9
# ---------------------------------------------------------------------------


def test_two_planeswalkers_of_the_same_type_coexist(board):
    """CR 306.4: the planeswalker uniqueness rule is gone. Two Chandras, one
    player, and nothing removes either."""
    enters(board, "Chandra, Torch of Defiance", controller=0)
    enters(board, "Chandra, Awakened Inferno", controller=0)
    board.sba()

    assert board.alive(0) == ["Chandra, Awakened Inferno", "Chandra, Torch of Defiance"]


def test_two_planeswalkers_of_the_same_name_do_not(board):
    """CR 306.4's second half: the errata made them legendary, so the legend
    rule (CR 704.5j) is what applies now. The control that shows the test
    above is not simply "the engine checks nothing"."""
    enters(board, PLANESWALKER, controller=0)
    enters(board, PLANESWALKER, controller=0)
    board.sba()

    assert board.alive(0) == [PLANESWALKER]
    assert board.in_graveyard(0) == [PLANESWALKER]


def test_a_planeswalker_can_be_attacked(board):
    """CR 306.6."""
    walker = enters(board, PLANESWALKER, controller=1)
    bear = board.play(CREATURE, controller=0)

    assert _attack_is_permitted(board.game, bear, AttackPermanent(walker.id))


def test_noncombat_damage_to_a_player_is_not_redirected(board):
    """CR 306.7: the redirection effect was removed. Damage dealt to a player
    who controls a planeswalker is dealt to the player."""
    walker = enters(board, PLANESWALKER, controller=1)
    source = board.play(CREATURE, controller=0)
    before = board.game.player(1).life
    actions.deal_damage(board.game, 1, 3, source=source.id)

    assert board.game.player(1).life == before - 3
    assert walker.counter_count("loyalty") == 4


def test_damage_to_a_planeswalker_removes_loyalty_counters(board):
    """CR 306.8: that many loyalty counters, and nothing marked."""
    walker = enters(board, PLANESWALKER, controller=1)
    source = board.play(CREATURE, controller=0)
    actions.deal_damage(board.game, walker, 3, source=source.id)

    assert walker.counter_count("loyalty") == 1
    assert walker.damage == 0


def test_a_planeswalker_at_zero_loyalty_is_put_into_its_owners_graveyard(board):
    """CR 306.9, a state-based action (CR 704.5i)."""
    walker = enters(board, PLANESWALKER, controller=1)
    source = board.play(CREATURE, controller=0)
    actions.deal_damage(board.game, walker, 4, source=source.id)
    board.sba()

    assert board.alive(1) == []
    assert board.in_graveyard(1) == [PLANESWALKER]


def test_a_planeswalker_with_loyalty_left_stays(board):
    """The control for CR 306.9: the rule is "0", not "damaged"."""
    walker = enters(board, PLANESWALKER, controller=1)
    source = board.play(CREATURE, controller=0)
    actions.deal_damage(board.game, walker, 3, source=source.id)
    board.sba()

    assert board.alive(1) == [PLANESWALKER]


# ---------------------------------------------------------------------------
# Battles: CR 310.5, 310.6, 310.8, 310.10
# ---------------------------------------------------------------------------


def test_a_battle_can_be_attacked(board):
    """CR 310.5: a battle is a thing creatures can attack.

    Attacked by the creatures of its own controller, because CR 310.12a makes
    a Siege's protector an opponent of its controller and CR 310.9b lets it be
    attacked by anyone the protector defends against. *Which* players those
    are is CR 310.9b's, and lives in ``test_cr310_battle_protector.py``.
    """
    battle = enters(board, SIEGE, controller=0)
    bear = board.play(CREATURE, controller=0)

    assert _attack_is_permitted(board.game, bear, AttackPermanent(battle.id))


def test_an_ordinary_permanent_cannot_be_attacked(board):
    """The control for CR 306.6 and CR 310.5: planeswalkers and battles are
    attackable because those two rules say so, not because permanents are."""
    ring = board.play(ARTIFACT, controller=1)
    bear = board.play(CREATURE, controller=0)

    assert not _attack_is_permitted(board.game, bear, AttackPermanent(ring.id))


def test_damage_to_a_battle_removes_defense_counters(board):
    """CR 310.6, and CR 310.4b for the three it arrived with."""
    battle = enters(board, SIEGE, controller=1)
    source = board.play(CREATURE, controller=0)
    assert battle.counter_count("defense") == 3
    actions.deal_damage(board.game, battle, 2, source=source.id)

    assert battle.counter_count("defense") == 1
    assert battle.damage == 0


def test_a_non_siege_battle_at_zero_defense_goes_to_the_graveyard(board):
    """CR 310.8. CR 310.7 is the Siege version, with its own exception, and is
    already covered in ``test_cr704_attachment_and_battles.py``."""
    battle = enters(board, BATTLE, controller=1)
    source = board.play(CREATURE, controller=0)
    actions.deal_damage(board.game, battle, battle.counter_count("defense"), source=source.id)
    board.sba()

    assert board.in_graveyard(1) == [BATTLE]


def test_a_battle_with_defense_left_stays(board):
    """The control for CR 310.8."""
    battle = enters(board, BATTLE, controller=1)
    source = board.play(CREATURE, controller=0)
    actions.deal_damage(board.game, battle, 1, source=source.id)
    board.sba()

    assert board.alive(1) == [BATTLE]


def test_a_battle_attached_to_a_permanent_becomes_unattached(board):
    """CR 310.10, a state-based action (CR 704.5p). Nothing legitimately
    attaches a battle, which is exactly why the rule exists: an effect that
    made one an Aura would otherwise leave it stuck to a host."""
    battle = enters(board, SIEGE, controller=1)
    host = board.play(CREATURE, controller=1)
    actions.attach(board.game, battle, host)
    assert battle.attached_to == host.id
    board.sba()

    assert not battle.attached_to


def test_an_unattached_battle_is_not_destroyed_for_it(board):
    """CR 310.10 says it becomes unattached, and stops there."""
    battle = enters(board, SIEGE, controller=1)
    host = board.play(CREATURE, controller=1)
    actions.attach(board.game, battle, host)
    board.sba()

    assert SIEGE in board.alive(1)
