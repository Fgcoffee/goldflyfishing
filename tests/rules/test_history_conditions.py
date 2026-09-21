"""Conditions the board cannot answer.

Two families that needed engine support rather than grammar, because the
question is not about the current board state:

* **"this turn"** - "if an opponent lost 2 or more life this turn". The engine
  announced every event, acted on it, and forgot it, so there was nothing to
  ask. ``ConditionKind.EVENT_THIS_TURN`` existed as a name with no evaluator.

* **"it"** - "if it's blue", "if it was a creature". Nothing in the game state
  knows what "it" refers to; only the resolution does, so ``holds`` had to be
  told.

The second one has a subtlety worth keeping: "when this dies, if it *was* a
creature" is asked when the object is already in a graveyard and is no longer a
creature, so it is answered from last-known information (CR 608.2g).
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.kernel.conditions import holds
from mtgfish.rules.kernel.enums import CardType, Color
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.query import (
    Condition,
    ConditionKind,
    NumericConstraint,
    ObjectFilter,
    PlayerFilter,
    PlayerScope,
)
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


# ---------------------------------------------------------------------------
# "this turn"
# ---------------------------------------------------------------------------


def _lost_two_life(scope=PlayerScope.EACH_OPPONENT):
    return Condition(
        kind=ConditionKind.EVENT_THIS_TURN,
        players=PlayerFilter(scope),
        constraint=NumericConstraint.at_least(2),
        event_kinds=(int(EventKind.LIFE_LOST),),
    )


def test_nothing_has_happened_yet(box):
    assert not holds(box.game, _lost_two_life(), controller=box.game.player(0).id)


def test_an_amount_accumulates_across_the_turn(box):
    """Two separate one-point losses satisfy "lost 2 or more life this turn"."""
    me = box.game.player(0).id
    opponent = box.game.player(1).id

    actions.lose_life(box.game, opponent, 1)
    assert not holds(box.game, _lost_two_life(), controller=me)
    actions.lose_life(box.game, opponent, 1)
    assert holds(box.game, _lost_two_life(), controller=me)


def test_the_history_clears_at_the_start_of_a_turn(box):
    me = box.game.player(0).id
    actions.lose_life(box.game, box.game.player(1).id, 5)
    assert holds(box.game, _lost_two_life(), controller=me)

    box.next_turn()
    box.next_turn()
    assert not holds(box.game, _lost_two_life(), controller=me)


def test_the_wrong_player_does_not_count(box):
    """"An opponent lost life" is not satisfied by your own life loss."""
    me = box.game.player(0).id
    actions.lose_life(box.game, me, 5)
    assert not holds(box.game, _lost_two_life(), controller=me)


def test_a_count_and_an_amount_are_different_questions(box):
    """Three spells cast is a count; seven damage is an amount. The same event
    kind can be asked either way, so the two must not share a tally."""
    me = box.game.player(0).id
    opponent = box.game.player(1).id
    actions.lose_life(box.game, opponent, 5)

    counted = Condition(
        kind=ConditionKind.EVENT_THIS_TURN,
        players=PlayerFilter(PlayerScope.EACH_OPPONENT),
        constraint=NumericConstraint.at_least(2),
        counter_type="count",
        event_kinds=(int(EventKind.LIFE_LOST),),
    )
    # One event of five life: the amount is 5, the count is 1.
    assert holds(box.game, _lost_two_life(), controller=me)
    assert not holds(box.game, counted, controller=me)


# ---------------------------------------------------------------------------
# "it"
# ---------------------------------------------------------------------------


def _remembered(**fields):
    return Condition(
        kind=ConditionKind.REMEMBERED_MATCHES,
        filter=ObjectFilter(remembered=True, zones=frozenset(), **fields),
    )


def test_a_condition_about_it_needs_to_be_told_what_it_is(box):
    """Without the resolution there is no answer, and guessing "true" would
    fire every such ability unconditionally."""
    box.put("Grizzly Bears", "battlefield", 0)
    condition = _remembered(types_all=CardType.CREATURE)
    assert not holds(box.game, condition, controller=box.game.player(0).id)


def test_it_matches_when_the_description_fits(box):
    box.put("Grizzly Bears", "battlefield", 0)
    bear = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Grizzly Bears"
    )
    assert holds(
        box.game,
        _remembered(types_all=CardType.CREATURE),
        controller=box.game.player(0).id,
        remembered=(bear.id,),
    )


def test_it_does_not_match_when_the_description_does_not_fit(box):
    box.put("Grizzly Bears", "battlefield", 0)
    bear = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Grizzly Bears"
    )
    assert not holds(
        box.game,
        _remembered(types_all=CardType.ARTIFACT),
        controller=box.game.player(0).id,
        remembered=(bear.id,),
    )


def test_it_is_answered_from_last_known_information(box):
    """"When this dies, if it *was* a creature" is asked when the object is
    already in a graveyard and no longer a creature at all (CR 608.2g)."""
    from mtgfish.rules.kernel.enums import Zone

    box.put("Grizzly Bears", "battlefield", 0)
    bear = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Grizzly Bears"
    )
    dead = box.game.move_object(bear, Zone.GRAVEYARD)
    box.game.invalidate_characteristics()

    assert holds(
        box.game,
        _remembered(types_all=CardType.CREATURE),
        controller=box.game.player(0).id,
        remembered=(dead.id,),
    )


def test_a_colour_test_reads_and_evaluates(box):
    box.put("Grizzly Bears", "battlefield", 0)
    bear = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Grizzly Bears"
    )
    me = box.game.player(0).id
    assert holds(box.game, _remembered(colors_any=Color.GREEN), controller=me,
                 remembered=(bear.id,))
    assert not holds(box.game, _remembered(colors_any=Color.BLUE), controller=me,
                     remembered=(bear.id,))


# ---------------------------------------------------------------------------
# The grammar for both
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "an opponent lost 2 or more life this turn",
        "an opponent was dealt 7 or more damage this turn",
        "an opponent cast three or more spells this turn",
        "you attacked with three or more creatures this turn",
        "it's blue",
        "it was a creature",
        "it's a land card",
        "its power is 3 or greater",
    ],
)
def test_the_condition_reads(card_db, text):
    from mtgfish.parser.clauses import parse_condition_text
    from mtgfish.parser.tokens import Stream

    card_db.registry()
    stream = Stream.of(text)
    condition = parse_condition_text(stream)
    assert condition is not None and stream.done
