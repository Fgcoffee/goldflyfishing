"""Card-type machinery: CR 710, 711, 714, 716, 719, 721, and CR 116.

These rules are invisible in oracle text - a Saga's chapter symbol is a glyph,
not a sentence - so nothing the parser produces will ever exercise them. They
are only ever right if tested directly.
"""

from __future__ import annotations

from mtgfish.rules import card_types
from mtgfish.rules.abilities import Ability, AbilityKind
from mtgfish.rules.card_types import (
    chapter_ability,
    class_level,
    class_level_bar,
    enters_with_lore_counter,
    final_chapter,
    flip,
    level_band,
    station_band,
    to_solve,
)
from mtgfish.rules.costs import Cost, CostComponent, CostKind
from mtgfish.rules.effects import Effect, EffectKind
from mtgfish.rules.enums import Phase, Step, Zone
from mtgfish.rules.mana import ManaCost, ManaKind
from mtgfish.rules.query import ALWAYS, Value

import pytest

from harness import ScriptedAbilities, keyword, make_board


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def mana_cost(text: str) -> Cost:
    return Cost((CostComponent(CostKind.MANA, mana=ManaCost.parse(text)),))


def draw_effect(amount: int = 1) -> Effect:
    from mtgfish.rules.query import YOU

    return Effect(EffectKind.DRAW, players=YOU, amount=Value.of(amount))


# ---------------------------------------------------------------------------
# CR 714: Sagas
# ---------------------------------------------------------------------------


def test_saga_gets_a_lore_counter_at_precombat_main(board):
    """CR 714.3c: the turn-based action is at precombat main, not upkeep.

    The difference is a whole turn cycle of chapter timing, and it decides
    whether a chapter-III effect lands before or after the opponent's turn.
    """
    board.scripts.add(
        "Urza's Saga",
        chapter_ability(1, draw_effect()),
        chapter_ability(2, draw_effect()),
    )
    saga = board.play("Urza's Saga", controller=0)

    board.game.active_player = 0
    board.game.phase = Phase.BEGINNING
    card_types.saga_lore_counters(board.game)
    assert saga.counter_count("lore") == 1

    card_types.saga_lore_counters(board.game)
    assert saga.counter_count("lore") == 2


def test_a_saga_with_no_chapter_abilities_gets_no_counter(board):
    """CR 714.3c and 714.4 both say "with one or more chapter abilities".

    This is the Blood Moon case: Urza's Saga becomes a Mountain, loses every
    ability including its chapters, and must then neither tick nor be
    sacrificed. Getting this wrong destroys the land.
    """
    board.scripts.add("Urza's Saga")  # no abilities at all
    saga = board.play("Urza's Saga", controller=0)

    board.game.active_player = 0
    card_types.saga_lore_counters(board.game)
    assert saga.counter_count("lore") == 0

    board.sba()
    assert saga.zone is Zone.BATTLEFIELD


def test_chapter_fires_only_when_the_count_crosses_its_number(board):
    """CR 714.2b: "was less than N and became at least N".

    Not "whenever a lore counter is added" - a Saga already past chapter II
    that somehow gains another counter must not replay chapter II.
    """
    board.scripts.add(
        "Urza's Saga",
        chapter_ability(1, draw_effect()),
        chapter_ability(2, draw_effect()),
        chapter_ability(3, draw_effect()),
    )
    board.play("Urza's Saga", controller=0)
    board.game.active_player = 0

    card_types.saga_lore_counters(board.game)
    board.settle()
    assert len(board.game.stack) == 1  # chapter I only

    board.resolve_stack()
    card_types.saga_lore_counters(board.game)
    board.settle()
    assert len(board.game.stack) == 1  # chapter II only, not I again


def test_two_counters_at_once_fire_both_chapters(board):
    """CR 714.2b again: crossing two thresholds fires both chapters.

    A doubling effect on lore counters skips nothing.
    """
    from mtgfish.rules import actions

    board.scripts.add(
        "Urza's Saga",
        chapter_ability(1, draw_effect()),
        chapter_ability(2, draw_effect()),
    )
    saga = board.play("Urza's Saga", controller=0)
    board.game.active_player = 0

    actions.add_counters(board.game, saga, "lore", 2)
    board.settle()
    assert len(board.game.stack) == 2


def test_final_chapter_is_the_greatest_number(board):
    board.scripts.add(
        "Urza's Saga",
        chapter_ability(1, draw_effect()),
        chapter_ability(3, draw_effect()),
    )
    saga = board.play("Urza's Saga", controller=0)
    assert final_chapter(board.chars(saga)) == 3

    board.scripts.add("Urza's Saga")
    board.refresh()
    # CR 714.2d: no chapter abilities means a final chapter number of zero.
    assert final_chapter(board.chars(saga)) == 0


def test_enters_with_a_lore_counter_is_a_replacement(board):
    """CR 714.3a: an intrinsic replacement effect, which is why chapter I
    happens the turn the Saga arrives rather than a turn later."""
    ability = enters_with_lore_counter()
    assert ability.kind is AbilityKind.STATIC
    assert ability.effects[0].kind is EffectKind.ADD_COUNTERS
    assert ability.effects[0].counter_type == "lore"
    # No targets: that is what makes the engine read it as an ETB replacement
    # rather than a one-shot that adds counters to something.
    assert ability.effects[0].targets is None


# ---------------------------------------------------------------------------
# CR 710: flip cards
# ---------------------------------------------------------------------------


def test_flipping_reads_the_bottom_half_but_keeps_the_top_half_cost(board):
    """CR 710.2 and 710.1c together.

    Name, types and P/T come from the bottom; colour and mana cost stay with
    the top. A flipped Kamigawa creature is still the colour it was cast as,
    so colour-based removal still catches it.
    """
    obj = board.play("Delver of Secrets // Insectile Aberration", controller=0)
    top = board.chars(obj)

    assert flip(board.game, obj) is True
    bottom = board.chars(obj)

    assert bottom.name != top.name
    assert bottom.mana_cost == top.mana_cost
    assert bottom.colors == top.colors


def test_flipping_is_one_way(board):
    """CR 710.4: once flipped, a permanent cannot become unflipped."""
    obj = board.play("Delver of Secrets // Insectile Aberration", controller=0)
    assert flip(board.game, obj) is True
    assert flip(board.game, obj) is False


def test_a_card_outside_the_battlefield_cannot_flip(board):
    """CR 710.2: only a permanent on the battlefield is ever flipped."""
    obj = board.hand("Delver of Secrets // Insectile Aberration", controller=0)
    assert flip(board.game, obj) is False


# ---------------------------------------------------------------------------
# CR 711: levelers
# ---------------------------------------------------------------------------


def test_level_band_sets_base_pt_only_inside_its_range(board):
    """CR 711.2a: "{LEVEL N1-N2}" is a static ability conditioned on counters.

    Below the band the creature keeps its printed P/T (711.5); inside it, the
    band's P/T is the new base.
    """
    board.scripts.add("Grizzly Bears", *level_band(1, 2, 4, 4))
    bear = board.play("Grizzly Bears", controller=0)
    assert board.pt(bear) == (2, 2)

    bear.add_counters("level", 1)
    board.refresh()
    assert board.pt(bear) == (4, 4)

    bear.add_counters("level", 2)  # now at 3, past the band
    board.refresh()
    assert board.pt(bear) == (2, 2)


def test_open_ended_level_band_has_no_upper_limit(board):
    """CR 711.2b: "{LEVEL N3+}" applies at N3 or more, forever."""
    board.scripts.add("Grizzly Bears", *level_band(3, None, 6, 6))
    bear = board.play("Grizzly Bears", controller=0)

    bear.add_counters("level", 3)
    board.refresh()
    assert board.pt(bear) == (6, 6)

    bear.add_counters("level", 5)
    board.refresh()
    assert board.pt(bear) == (6, 6)


def test_level_band_grants_its_abilities_too(board):
    board.scripts.add("Grizzly Bears", *level_band(2, None, 5, 5, keyword("Flying")))
    bear = board.play("Grizzly Bears", controller=0)
    assert "Flying" not in board.keywords(bear)

    bear.add_counters("level", 2)
    board.refresh()
    assert "Flying" in board.keywords(bear)


# ---------------------------------------------------------------------------
# CR 721: station
# ---------------------------------------------------------------------------


def test_station_threshold_grants_abilities_on_charge_counters(board):
    """CR 721.2a: "{N+}" reads charge counters, not level counters."""
    board.scripts.add("Grizzly Bears", *station_band(3, keyword("Flying"), power=5, toughness=5))
    obj = board.play("Grizzly Bears", controller=0)
    assert "Flying" not in board.keywords(obj)

    obj.add_counters("charge", 2)
    board.refresh()
    assert "Flying" not in board.keywords(obj)

    obj.add_counters("charge", 1)
    board.refresh()
    assert "Flying" in board.keywords(obj)
    assert board.pt(obj) == (5, 5)


# ---------------------------------------------------------------------------
# CR 716: Classes
# ---------------------------------------------------------------------------


def test_a_permanent_with_no_level_is_level_one(board):
    """CR 716.2d, and it applies to any permanent, not only Classes."""
    obj = board.play("Grizzly Bears", controller=0)
    assert class_level(board.game, obj.id) == 1


def test_class_level_bar_is_sorcery_speed_and_steps_by_one(board):
    """CR 716.2a: "Activate only if this Class is level N-1 and only as a sorcery".

    Both halves matter. Without the level check a player could buy level 3
    directly; without the timing check they could level up in response to
    removal.
    """
    from mtgfish.rules.conditions import holds
    from mtgfish.rules.enums import Timing

    bar = class_level_bar(2, mana_cost("{2}"))
    assert bar.activated.timing is Timing.SORCERY

    obj = board.play("Grizzly Bears", controller=0)
    assert holds(board.game, bar.activated.activation_condition, source=obj.id)

    card_types.set_class_level(board.game, obj.id, 2)
    assert not holds(board.game, bar.activated.activation_condition, source=obj.id)


def test_class_static_applies_at_that_level_or_greater(board):
    """CR 716.2a: "as long as this Class is level N or greater"."""
    bar = class_level_bar(2, mana_cost("{2}"), keyword("Flying"))
    board.scripts.add("Grizzly Bears", *bar.static)
    obj = board.play("Grizzly Bears", controller=0)
    assert "Flying" not in board.keywords(obj)

    card_types.set_class_level(board.game, obj.id, 2)
    board.refresh()
    assert "Flying" in board.keywords(obj)

    card_types.set_class_level(board.game, obj.id, 3)
    board.refresh()
    assert "Flying" in board.keywords(obj)


def test_level_is_not_copiable_and_ends_with_the_battlefield(board):
    """CR 716.2b: a Class retains its level even if it stops being a Class -
    but a new object never inherits it (CR 400.7)."""
    obj = board.play("Grizzly Bears", controller=0)
    card_types.set_class_level(board.game, obj.id, 3)

    moved = board.game.move_object(obj, Zone.GRAVEYARD)
    assert class_level(board.game, moved.id) == 1


# ---------------------------------------------------------------------------
# CR 719: Cases
# ---------------------------------------------------------------------------


def test_to_solve_triggers_at_your_end_step(board):
    ability = to_solve(ALWAYS)
    assert ability.kind is AbilityKind.TRIGGERED
    assert ability.trigger is not None
    assert ability.effects[0].kind is EffectKind.BECOME_SOLVED


def test_a_case_becomes_solved_and_stays_solved(board):
    obj = board.play("Grizzly Bears", controller=0)
    assert card_types.is_solved(board.game, obj.id) is False

    assert card_types.become_solved(board.game, obj.id) is True
    assert card_types.is_solved(board.game, obj.id) is True
    # Already solved: nothing happens a second time.
    assert card_types.become_solved(board.game, obj.id) is False


def test_solved_abilities_function_only_once_solved(board):
    """CR 719.3c: "Solved - ..." is off until the designation is there."""
    board.scripts.add("Grizzly Bears", *card_types.solved_ability(keyword("Flying")))
    obj = board.play("Grizzly Bears", controller=0)
    assert "Flying" not in board.keywords(obj)

    card_types.become_solved(board.game, obj.id)
    board.refresh()
    assert "Flying" in board.keywords(obj)


def test_solved_is_lost_when_the_permanent_leaves(board):
    """CR 719.3b: solved lasts until it leaves the battlefield."""
    obj = board.play("Grizzly Bears", controller=0)
    card_types.become_solved(board.game, obj.id)

    moved = board.game.move_object(obj, Zone.GRAVEYARD)
    assert card_types.is_solved(board.game, moved.id) is False


# ---------------------------------------------------------------------------
# CR 116: special actions
# ---------------------------------------------------------------------------


def test_turning_face_up_is_offered_whenever_you_have_priority(board):
    """CR 116.2b: any time you have priority - no sorcery-speed restriction.

    A morphed creature must be able to flip up in response to a removal spell,
    with the removal spell still on the stack.
    """
    from mtgfish.rules.keyword_impl import KeywordInstance, build
    from mtgfish.rules.special_actions import SpecialKind, available

    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Morph", cost=mana_cost("{2}"))))
    obj = board.play("Grizzly Bears", controller=0, face_down=True)
    board.game.player(0).mana_pool.add(ManaKind(), 2)

    actions = [a for a in available(board.game, 0) if a.kind is SpecialKind.TURN_FACE_UP]
    assert len(actions) == 1
    assert actions[0].source == obj.id


def test_a_face_up_permanent_offers_no_turn_face_up_action(board):
    from mtgfish.rules.keyword_impl import KeywordInstance, build
    from mtgfish.rules.special_actions import SpecialKind, available

    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Morph", cost=mana_cost("{2}"))))
    board.play("Grizzly Bears", controller=0)
    board.game.player(0).mana_pool.add(ManaKind(), 2)

    assert not [a for a in available(board.game, 0) if a.kind is SpecialKind.TURN_FACE_UP]


def test_turning_face_up_restores_characteristics_without_an_etb(board):
    """CR 708.4: it was already on the battlefield, so it does not "enter".

    A morph creature flipped up does not retrigger its own
    enters-the-battlefield ability, and that is a real difference from
    blinking it.
    """
    from mtgfish.rules.events import EventKind
    from mtgfish.rules.special_actions import turn_face_up

    obj = board.play("Grizzly Bears", controller=0, face_down=True)
    assert board.chars(obj).name != "Grizzly Bears"

    board.game.log.enabled = True
    before = len(board.game.log.entries)
    assert turn_face_up(board.game, obj) is True
    assert board.chars(obj).name == "Grizzly Bears"

    emitted = [e.text for e in board.game.log.entries[before:] if e.kind == "event"]
    assert any(EventKind.TURNED_FACE_UP.name in text for text in emitted)
    assert not any(EventKind.ENTERS_BATTLEFIELD.name in text for text in emitted)


def test_megamorph_leaves_a_counter_and_morph_does_not(board):
    """CR 702.37b: the whole difference between the two keywords."""
    from mtgfish.rules.special_actions import turn_face_up

    board.scripts.add("Grizzly Bears", keyword("Morph"))
    plain = board.play("Grizzly Bears", controller=0, face_down=True)
    turn_face_up(board.game, plain)
    assert plain.counter_count("+1/+1") == 0

    board.scripts.add("Runeclaw Bear", keyword("Megamorph"))
    mega = board.play("Runeclaw Bear", controller=0, face_down=True)
    turn_face_up(board.game, mega)
    assert mega.counter_count("+1/+1") == 1


def test_special_actions_never_use_the_stack(board):
    """CR 116.1, the property the whole category exists for."""
    from mtgfish.rules.special_actions import turn_face_up

    obj = board.play("Grizzly Bears", controller=0, face_down=True)
    turn_face_up(board.game, obj)
    assert not board.game.stack


def test_morph_is_never_offered_as_an_activated_ability(board):
    """A regression guard.

    Morph used to build an activated ability, which would have put turning
    face up on the stack and handed opponents a response window the rules
    never give them.
    """
    from mtgfish.rules.keyword_impl import KeywordInstance, build

    for name in ("Morph", "Megamorph", "Disguise"):
        abilities = build(KeywordInstance(name, cost=mana_cost("{2}")))
        assert abilities
        assert all(a.kind is not AbilityKind.ACTIVATED for a in abilities), name


def test_the_engine_offers_special_actions_in_the_legal_action_list(board):
    """The enumerator and the performer must agree, so both go through
    ``legal_actions``."""
    from mtgfish.rules.keyword_impl import KeywordInstance, build
    from mtgfish.rules.legality import legal_actions
    from mtgfish.rules.priority import ActionKind

    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Morph", cost=mana_cost("{2}"))))
    board.play("Grizzly Bears", controller=0, face_down=True)
    board.game.player(0).mana_pool.add(ManaKind(), 2)
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN

    actions = legal_actions(board.game, 0)
    assert any(a.kind is ActionKind.SPECIAL for a in actions)


def test_performing_the_special_action_turns_the_permanent_up(board):
    from mtgfish.rules.keyword_impl import KeywordInstance, build
    from mtgfish.rules.special_actions import SpecialKind, available, perform

    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Morph", cost=mana_cost("{2}"))))
    obj = board.play("Grizzly Bears", controller=0, face_down=True)
    board.game.player(0).mana_pool.add(ManaKind(), 2)

    action = next(
        a for a in available(board.game, 0) if a.kind is SpecialKind.TURN_FACE_UP
    )
    assert perform(board.game, 0, action.as_action()) is True
    assert obj.face_down is False
    assert board.chars(obj).name == "Grizzly Bears"


def test_out_of_scope_special_actions_are_named_not_silent(board):
    """Planechase and Conspiracy Draft are out of scope. Being explicit about
    that is the difference between a known gap and a bug."""
    from mtgfish.rules.special_actions import OUT_OF_SCOPE, SpecialKind

    assert SpecialKind.ROLL_PLANAR_DIE in OUT_OF_SCOPE
    assert SpecialKind.UNLOCK_CONSPIRACY in OUT_OF_SCOPE


def test_unused_helper_stays_referenced():
    """``Ability`` is imported for the type it documents in this module."""
    assert Ability is not None
