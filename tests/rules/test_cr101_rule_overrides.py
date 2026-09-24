"""Cards that beat the rules (CR 101.1).

The golden rule says a card's text wins where it contradicts the rules. An
engine cannot implement that in general - card text here compiles into a fixed
vocabulary, so a card can only beat a rule the engine has left a seam for.

This is the seam for the rules that are about the *game* rather than about an
object. "Creatures you control have haste" is a continuous effect and belongs
to CR 611; "creatures don't suffer summoning sickness" belongs here, because
it switches CR 302.6 off rather than granting anything.

No card is named anywhere below. The abilities are built by hand from
``EffectKind.SUSPEND_RULE``, which is exactly what a parser will emit.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr101_rule_overrides import (
    Rule,
    RuleOverride,
    register,
    suspended,
)
from mtgfish.rules.cr500_turn_structure.cr506_combat import can_attack
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr700_additional_rules.cr704_sba import check_state_based_actions
from mtgfish.rules.kernel.enums import CardType
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ControllerRelation, ObjectFilter

CREATURES = ObjectFilter(types_all=CardType.CREATURE)


def suspends(rule: Rule, *, subject=None, players=None) -> Ability:
    """The static ability a parser would build for "rule X does not apply"."""
    return Ability.static(
        Effect(
            EffectKind.SUSPEND_RULE,
            rule=rule,
            targets=subject,
            players=players,
            text=f"CR {rule.value} does not apply",
        ),
        text=f"CR {rule.value} does not apply",
    )


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


# ---------------------------------------------------------------------------
# CR 302.6, the summoning sickness example
# ---------------------------------------------------------------------------


def test_a_summoning_sick_creature_cannot_attack(board):
    """The control: CR 302.6 applies when nothing has switched it off."""
    bear = board.play("Grizzly Bears", controller=0)
    bear.summoning_sick = True

    assert not can_attack(board.game, bear)


def test_a_card_can_switch_summoning_sickness_off(board):
    """CR 101.1: the card beats CR 302.6 while it is on the battlefield."""
    board.scripts.add("Sol Ring", suspends(Rule.SUMMONING_SICKNESS, subject=CREATURES))
    board.play("Sol Ring", controller=0)
    bear = board.play("Grizzly Bears", controller=0)
    bear.summoning_sick = True

    assert can_attack(board.game, bear)


def test_the_rule_comes_back_when_the_source_leaves(board):
    """CR 611.3: a suspension from a static ability is rebuilt each time, so
    it stops the moment its permanent does."""
    board.scripts.add("Sol Ring", suspends(Rule.SUMMONING_SICKNESS, subject=CREATURES))
    ring = board.play("Sol Ring", controller=0)
    bear = board.play("Grizzly Bears", controller=0)
    bear.summoning_sick = True
    assert can_attack(board.game, bear)

    board.game._remove_from_zone(ring)
    board.game.invalidate_characteristics()

    assert not can_attack(board.game, bear)


def test_a_suspension_can_be_scoped_to_some_objects_only(board):
    """``targets`` says for whom, exactly as it does for a prohibition."""
    mine = ObjectFilter(
        types_all=CardType.CREATURE, controller=ControllerRelation.YOU
    )
    board.scripts.add("Sol Ring", suspends(Rule.SUMMONING_SICKNESS, subject=mine))
    board.play("Sol Ring", controller=0)
    ours = board.play("Grizzly Bears", controller=0)
    theirs = board.play("Runeclaw Bear", controller=1)
    ours.summoning_sick = theirs.summoning_sick = True

    assert can_attack(board.game, ours)
    assert not can_attack(board.game, theirs)


# ---------------------------------------------------------------------------
# CR 704.5j, a rule about the game rather than about one object
# ---------------------------------------------------------------------------


def test_the_legend_rule_applies_by_default(board):
    """The control."""
    board.play("Sol Ring", controller=0)
    first = board.play("Kenrith, the Returned King", controller=0)
    second = board.play("Kenrith, the Returned King", controller=0)
    check_state_based_actions(board.game)

    alive = [o for o in (first, second) if o.id in board.game.battlefield]
    assert len(alive) == 1


def test_a_card_can_switch_the_legend_rule_off(board):
    """CR 101.1 again, at a state-based action rather than in combat."""
    board.scripts.add("Sol Ring", suspends(Rule.LEGEND_RULE))
    board.play("Sol Ring", controller=0)
    first = board.play("Kenrith, the Returned King", controller=0)
    second = board.play("Kenrith, the Returned King", controller=0)
    check_state_based_actions(board.game)

    assert first.id in board.game.battlefield
    assert second.id in board.game.battlefield


# ---------------------------------------------------------------------------
# The mechanism itself
# ---------------------------------------------------------------------------


def test_an_unsuspended_rule_reports_nothing(board):
    assert suspended(board.game, Rule.ONE_LAND_PER_TURN, player=PlayerId(0)) is None


def test_a_standing_suspension_says_which_card_did_it(board):
    """``suspended`` returns the override, not a bool, so a log can explain a
    surprising game rather than merely record it."""
    ring = board.play("Sol Ring", controller=0)
    register(
        board.game,
        RuleOverride(
            rule=Rule.ONE_LAND_PER_TURN,
            source=ring.id,
            text="you may play any number of lands",
        ),
    )
    found = suspended(board.game, Rule.ONE_LAND_PER_TURN, player=PlayerId(0))

    assert found is not None
    assert found.source == ring.id
    assert str(found) == "you may play any number of lands"


def test_a_suspension_names_its_rule_by_cr_number(board):
    """The value *is* the rule number, so a parser can emit one from the text
    it read and a log can print it."""
    assert Rule.SUMMONING_SICKNESS.value == "302.6"
    assert Rule("704.5j") is Rule.LEGEND_RULE


def test_an_unknown_rule_is_refused_rather_than_ignored(board):
    """The list is closed on purpose: a rule the engine has no name for is one
    it cannot suspend, and silence would be a card that claims to switch a
    rule off and does not."""
    with pytest.raises(ValueError):
        Rule("999.9")
