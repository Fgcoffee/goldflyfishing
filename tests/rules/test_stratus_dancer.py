"""Stratus Dancer versus Krosan Grip.

The interaction people get wrong at the table, and the one an engine gets wrong
if it treats "turn face up" as an activated ability.

    Krosan Grip - Instant {2}{G}
    Split second (As long as this spell is on the stack, players can't cast
    spells or activate abilities that aren't mana abilities.)
    Destroy target artifact or enchantment.

    Stratus Dancer - Creature - Djinn Monk, 2/1
    Flying
    Megamorph {1}{U}
    When this creature is turned face up, counter target instant or sorcery
    spell.

Split second stops *casting* and *activating*. Turning a face-down creature up
is neither - it is a special action (CR 116.2b), it uses no stack, and CR
702.82a does not touch it. So the Dancer flips up under split second, its
turned-face-up trigger goes on the stack above Krosan Grip, and counters it.

Everything below is the real cards' text, hand-written as abilities because the
parser does not exist yet.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, keyword, make_board

from mtgfish.rules.abilities import Ability, AbilityKind, TriggerCondition
from mtgfish.rules.cr106_mana import ManaCost, ManaKind
from mtgfish.rules.cr116_special_actions import SpecialKind, available, perform
from mtgfish.rules.cr117_priority import ActionKind
from mtgfish.rules.cr118_costs import Cost, CostComponent, CostKind
from mtgfish.rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.effects import Effect, EffectKind
from mtgfish.rules.enums import LETTER_TO_COLOR, CardType, Phase, Step, Zone
from mtgfish.rules.events import EventKind
from mtgfish.rules.query import ObjectFilter
from mtgfish.rules.restrictions import Act, prohibited

#: "counter target instant or sorcery spell"
INSTANT_OR_SORCERY = ObjectFilter(
    types_any=CardType.INSTANT | CardType.SORCERY,
    zones=frozenset({Zone.STACK}),
)

#: "target artifact or enchantment"
ARTIFACT_OR_ENCHANTMENT = ObjectFilter(types_any=CardType.ARTIFACT | CardType.ENCHANTMENT)


def cost(text: str) -> Cost:
    return Cost((CostComponent(CostKind.MANA, mana=ManaCost.parse(text)),))


def stratus_dancer() -> tuple[Ability, ...]:
    """Flying, Megamorph {1}{U}, and the turned-face-up trigger."""
    turned_face_up = Ability(
        AbilityKind.TRIGGERED,
        effects=(
            Effect(
                EffectKind.COUNTER_SPELL,
                targets=INSTANT_OR_SORCERY,
                is_targeted=True,
                text="counter target instant or sorcery spell",
            ),
        ),
        trigger=TriggerCondition(
            event_kinds=frozenset({EventKind.TURNED_FACE_UP}),
            subject=ObjectFilter(source_only=True),
            text="when this creature is turned face up",
        ),
        text="When this creature is turned face up, counter target instant or sorcery spell.",
    )
    return (
        keyword("Flying"),
        *build(KeywordInstance("Megamorph", cost=cost("{1}{U}"))),
        turned_face_up,
    )


def krosan_grip() -> tuple[Ability, ...]:
    """Split second, and the spell ability."""
    return (
        *build(KeywordInstance("Split second")),
        Ability.spell(
            Effect(
                EffectKind.DESTROY,
                targets=ARTIFACT_OR_ENCHANTMENT,
                is_targeted=True,
                text="destroy target artifact or enchantment",
            ),
            text="Destroy target artifact or enchantment.",
        ),
    )


@pytest.fixture
def table(card_db):
    """P0 has a face-down Stratus Dancer and an artifact; P1 holds Krosan Grip."""
    board = make_board(card_db, ScriptedAbilities())
    board.scripts.add("Stratus Dancer", *stratus_dancer())
    board.scripts.add("Krosan Grip", *krosan_grip())

    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    board.game.active_player = 1

    dancer = board.play("Stratus Dancer", controller=0, face_down=True)
    # The placeholder Krosan Grip is aimed at. Sol Ring is an artifact and
    # nothing else about it matters here.
    artifact = board.play("Sol Ring", controller=0)
    grip = board.hand("Krosan Grip", controller=1)

    # P0 needs {1}{U} for megamorph, P1 needs {2}{G} for the Grip.
    board.game.player(0).mana_pool.add(ManaKind(LETTER_TO_COLOR["U"]), 3)
    board.game.player(1).mana_pool.add(ManaKind(LETTER_TO_COLOR["G"]), 3)

    return board, dancer, artifact, grip


def cast_grip(board, grip, artifact):
    """P1 casts Krosan Grip targeting the artifact, and it sits on the stack."""
    from mtgfish.rules.cr117_priority import Action
    from mtgfish.rules.cr601_casting import cast_spell

    return cast_spell(
        board.game,
        1,
        Action(ActionKind.CAST_SPELL, source=grip.id, targets=((artifact.id,),)),
    )


# ---------------------------------------------------------------------------
# The board before anything happens
# ---------------------------------------------------------------------------


def test_the_face_down_dancer_is_a_vanilla_two_two(table):
    """CR 708.2: a face-down permanent is a 2/2 with no name, no types beyond
    creature, no text - so no flying, whatever the card says."""
    board, dancer, _, _ = table
    chars = board.chars(dancer)

    assert board.pt(dancer) == (2, 2)
    assert "Flying" not in board.keywords(dancer)
    assert chars.name != "Stratus Dancer"


# ---------------------------------------------------------------------------
# Split second
# ---------------------------------------------------------------------------


def test_split_second_stops_both_players_casting(table):
    """CR 702.82a. Not just opponents - the Grip's own controller too."""
    board, _, artifact, grip = table
    cast_grip(board, grip, artifact)

    for player in (0, 1):
        assert prohibited(board.game, Act.CAST_SPELL, player=player) is not None


def test_split_second_does_not_stop_mana_abilities(table):
    """CR 702.82a carves them out explicitly, and CR 605 makes them a distinct
    act so the carve-out is expressible rather than special-cased."""
    board, _, artifact, grip = table
    cast_grip(board, grip, artifact)

    assert prohibited(board.game, Act.ACTIVATE_MANA_ABILITY, player=0) is None
    assert prohibited(board.game, Act.ACTIVATE_ABILITY, player=0) is not None


def test_split_second_ends_when_the_spell_leaves_the_stack(table):
    """CR 611.3: the prohibition is regenerated from live abilities, so it
    disappears the moment the spell is no longer on the stack. A cached
    restriction would lock the game up."""
    board, _, artifact, grip = table
    spell = cast_grip(board, grip, artifact)
    assert prohibited(board.game, Act.CAST_SPELL, player=0) is not None

    board.game.move_object(spell, Zone.GRAVEYARD)
    assert prohibited(board.game, Act.CAST_SPELL, player=0) is None


# ---------------------------------------------------------------------------
# The interaction
# ---------------------------------------------------------------------------


def test_unmorphing_is_still_legal_under_split_second(table):
    """The point of the whole exercise.

    Turning face up is a special action (CR 116.2b), not casting and not
    activating, so CR 702.82a has nothing to say about it. An engine that
    modelled morph as an activated ability would wrongly forbid this.
    """
    board, dancer, artifact, grip = table
    cast_grip(board, grip, artifact)

    actions = [a for a in available(board.game, 0) if a.kind is SpecialKind.TURN_FACE_UP]
    assert len(actions) == 1
    assert actions[0].source == dancer.id


def test_the_engine_offers_it_in_the_legal_action_list_too(table):
    """Enumeration and permission must agree - one legality model, not two."""
    from mtgfish.rules.legality import legal_actions

    board, _, artifact, grip = table
    cast_grip(board, grip, artifact)

    actions = legal_actions(board.game, 0)
    assert any(a.kind is ActionKind.SPECIAL for a in actions)
    # ...and casting really is gone, so this is not just a permissive list.
    assert not any(a.kind is ActionKind.CAST_SPELL for a in actions)


def test_megamorph_turns_it_up_with_a_counter_and_its_real_text(table):
    """CR 702.37b: megamorph adds the +1/+1 counter; CR 708.4 restores
    everything the face-down state was hiding."""
    board, dancer, artifact, grip = table
    cast_grip(board, grip, artifact)

    action = next(
        a for a in available(board.game, 0) if a.kind is SpecialKind.TURN_FACE_UP
    )
    assert perform(board.game, 0, action.as_action()) is True

    assert board.chars(dancer).name == "Stratus Dancer"
    assert "Flying" in board.keywords(dancer)
    assert dancer.counter_count("+1/+1") == 1
    # 2/1 printed, plus the counter.
    assert board.pt(dancer) == (3, 2)


def test_the_trigger_goes_on_the_stack_above_krosan_grip(table):
    """CR 603.3b: it is put on the stack the next time a player would receive
    priority, which is after the special action - so it lands on top."""
    board, dancer, artifact, grip = table
    spell = cast_grip(board, grip, artifact)

    action = next(
        a for a in available(board.game, 0) if a.kind is SpecialKind.TURN_FACE_UP
    )
    perform(board.game, 0, action.as_action())
    board.settle()

    assert len(board.game.stack) == 2
    assert board.game.stack[-1] != spell.id  # the trigger is on top
    assert board.game.stack[0] == spell.id


def test_the_dancer_counters_the_grip_and_the_artifact_survives(table):
    """The whole interaction, end to end."""
    board, dancer, artifact, grip = table
    spell = cast_grip(board, grip, artifact)

    action = next(
        a for a in available(board.game, 0) if a.kind is SpecialKind.TURN_FACE_UP
    )
    perform(board.game, 0, action.as_action())
    board.settle()
    board.resolve_stack()

    # CR 400.7: the countered spell is a *new* object in the graveyard, so the
    # stack object it used to be is superseded rather than moved.
    countered = board.game.objects[spell.id]
    assert not countered.is_live
    assert board.game.objects[countered.superseded_by].zone is Zone.GRAVEYARD

    assert artifact.zone is Zone.BATTLEFIELD
    assert board.game.objects[artifact.id].is_live


def test_without_the_dancer_the_grip_resolves_and_the_artifact_dies(table):
    """The control. If the artifact survived either way the test above would
    be proving nothing."""
    board, dancer, artifact, grip = table
    board.game.move_object(dancer, Zone.HAND)

    cast_grip(board, grip, artifact)
    board.settle()
    board.resolve_stack()

    assert not board.game.objects[artifact.id].is_live


def test_split_second_does_not_stop_the_trigger_itself(table):
    """CR 702.82a stops casting and activating. A triggered ability is
    neither - it triggers and goes on the stack regardless."""
    board, dancer, artifact, grip = table
    cast_grip(board, grip, artifact)

    action = next(
        a for a in available(board.game, 0) if a.kind is SpecialKind.TURN_FACE_UP
    )
    perform(board.game, 0, action.as_action())

    assert board.game.pending_triggers
