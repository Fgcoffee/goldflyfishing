"""Triggers that were read but fired at the wrong times (CR 603.2).

Each test reads a trigger condition from oracle text with the real parser,
puts it on a permanent, makes the event happen - and makes a near miss
happen, to show it does *not* fire then.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.parser.tokens import Stream
from mtgfish.parser.triggers import parse_trigger
from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ObjectFilter, Value


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def watch(board, text: str, holder: str = "Grizzly Bears", controller: int = 0):
    """Put a permanent carrying a trigger read from ``text`` on the board."""
    trigger = parse_trigger(Stream.of(text))
    assert trigger is not None, text
    ability = Ability.triggered(trigger, Effect(EffectKind.NOTHING, text=text), text=text)
    board.scripts.add(holder, ability)
    obj = board.play(holder, controller)
    board.game.pending_triggers.clear()
    return obj


def fired(board, text: str) -> int:
    count = sum(1 for p in board.game.pending_triggers if p.ability.text == text)
    board.game.pending_triggers.clear()
    return count


# ---------------------------------------------------------------------------
# CR 603.2b: a step named without an owner begins on every turn
# ---------------------------------------------------------------------------


def test_the_end_step_is_every_players_end_step(board):
    """Ball Lightning's "at the beginning of the end step" was read as your
    end step only, so a Ball Lightning given to an opponent - or one cast
    with flash on their turn - never went away."""
    text = "At the beginning of the end step, sacrifice this creature."
    watch(board, text)
    board.game.emit(Event(EventKind.END_STEP, player=PlayerId(1)))
    assert fired(board, text) == 1
    board.game.emit(Event(EventKind.END_STEP, player=PlayerId(0)))
    assert fired(board, text) == 1


def test_your_end_step_is_still_only_yours(board):
    text = "At the beginning of your end step, draw a card."
    watch(board, text)
    board.game.emit(Event(EventKind.END_STEP, player=PlayerId(1)))
    assert fired(board, text) == 0
    board.game.emit(Event(EventKind.END_STEP, player=PlayerId(0)))
    assert fired(board, text) == 1


def test_combat_on_your_turn_keeps_its_owner():
    trigger = parse_trigger(
        Stream.of("At the beginning of combat on your turn, draw a card.")
    )
    assert trigger.players is not None


@pytest.mark.parametrize(
    "text",
    [
        "At the beginning of the next end step, draw a card.",
        "At the beginning of your next upkeep, draw a card.",
    ],
)
def test_a_next_step_is_not_a_recurring_trigger(text):
    """CR 603.7: "the next" makes a delayed trigger, which fires once. Read
    as a recurring one it would fire every turn."""
    assert parse_trigger(Stream.of(text)) is None


# ---------------------------------------------------------------------------
# CR 122.1, 603.2c: which counters, and how many occurrences
# ---------------------------------------------------------------------------


def test_a_counter_trigger_keeps_its_kind(board):
    text = "Whenever one or more +1/+1 counters are put on this creature, draw a card."
    me = watch(board, text)
    actions.add_counters(board.game, me, "-1/-1", 1)
    assert fired(board, text) == 0
    actions.add_counters(board.game, me, "+1/+1", 3)
    assert fired(board, text) == 1


def test_a_counter_fires_once_for_each_counter(board):
    """Fathom Mage: "whenever a +1/+1 counter is put on this creature" -
    three counters at once are three occurrences."""
    text = "Whenever a +1/+1 counter is put on this creature, draw a card."
    me = watch(board, text)
    actions.add_counters(board.game, me, "+1/+1", 3)
    assert fired(board, text) == 3
    actions.add_counters(board.game, me, "charge", 2)
    assert fired(board, text) == 0


def test_one_or_more_counters_of_any_kind(board):
    text = "Whenever one or more counters are put on this creature, draw a card."
    me = watch(board, text)
    actions.add_counters(board.game, me, "charge", 2)
    assert fired(board, text) == 1


@pytest.mark.parametrize(
    "text",
    [
        "When the twelfth hour counter is put on this artifact, draw a card.",
        "Whenever two +1/+1 counters are put on this creature, draw a card.",
    ],
)
def test_other_counter_counts_are_not_read(text):
    """CR 122.7: "the Nth counter" is a threshold, and "two counters" is
    neither "a counter" nor "one or more" - left unread, not widened."""
    assert parse_trigger(Stream.of(text)) is None


# ---------------------------------------------------------------------------
# CR 701.37b: becoming monstrous is not getting a counter
# ---------------------------------------------------------------------------


def test_becoming_monstrous_fires_once_and_counters_do_not(board):
    text = "When this creature becomes monstrous, draw a card."
    me = watch(board, text)
    actions.add_counters(board.game, me, "+1/+1", 1)
    assert fired(board, text) == 0

    monstrosity = Effect(
        EffectKind.MONSTROSITY,
        targets=ObjectFilter(source_only=True),
        amount=Value.of(2),
    )
    resolution = Resolution(game=board.game, source=me.id, controller=PlayerId(0))
    execute(resolution, (monstrosity,))
    assert fired(board, text) == 1
    # Already monstrous: monstrosity does nothing, and nothing triggers.
    execute(Resolution(game=board.game, source=me.id, controller=PlayerId(0)), (monstrosity,))
    assert fired(board, text) == 0


# ---------------------------------------------------------------------------
# CR 603.2c: "one or more" triggers once for what happened at the same time
# ---------------------------------------------------------------------------


def _lethal(board, *objs):
    for obj in objs:
        obj.damage = 100
    board.game.invalidate_characteristics()
    board.sba()


def test_one_or_more_deaths_at_once_trigger_once(board):
    """Morbid Opportunist, Chainsaw: a board wipe is one occurrence."""
    text = "Whenever one or more other creatures die, draw a card."
    watch(board, text, holder="Llanowar Elves")
    a = board.play("Hill Giant", 1)
    b = board.play("Hill Giant", 1)
    _lethal(board, a, b)
    (pending,) = [p for p in board.game.pending_triggers if p.ability.text == text]
    # "That many" counts the batch.
    assert pending.event.amount == 2
    board.game.pending_triggers.clear()

    c = board.play("Hill Giant", 1)
    _lethal(board, c)
    assert fired(board, text) == 1


def test_a_creature_dying_still_triggers_for_each(board):
    text = "Whenever another creature dies, draw a card."
    watch(board, text, holder="Llanowar Elves")
    a = board.play("Hill Giant", 1)
    b = board.play("Hill Giant", 1)
    _lethal(board, a, b)
    assert fired(board, text) == 2


def test_separate_instructions_are_separate_batches(board):
    """CR 608.2c: "destroy it, then destroy that one" happens twice."""
    text = "Whenever one or more other creatures die, draw a card."
    watch(board, text, holder="Llanowar Elves")
    a = board.play("Hill Giant", 1)
    b = board.play("Hill Giant", 1)
    kill = (
        Effect(EffectKind.DESTROY, targets=ObjectFilter(specific=(a.id,))),
        Effect(EffectKind.DESTROY, targets=ObjectFilter(specific=(b.id,))),
    )
    execute(Resolution(game=board.game, source=a.id, controller=PlayerId(0)), kill)
    assert fired(board, text) == 2


def test_combat_damage_to_two_players_is_two_occurrences(card_db):
    """Professional Face-Breaker: "to a player" is per player hit."""
    board = make_board(card_db, ScriptedAbilities(), players=3)
    text = "Whenever one or more creatures you control deal combat damage to a player, draw a card."
    watch(board, text)
    x = board.play("Hill Giant", 0)
    y = board.play("Hill Giant", 0)
    game = board.game
    game.event_batch += 1
    for attacker, victim in ((x, 1), (y, 1)):
        game.emit(Event(EventKind.COMBAT_DAMAGE_DEALT, player=PlayerId(victim), source=attacker.id, amount=3))
    assert fired(board, text) == 1
    game.event_batch += 1
    for attacker, victim in ((x, 1), (y, 2)):
        game.emit(Event(EventKind.COMBAT_DAMAGE_DEALT, player=PlayerId(victim), source=attacker.id, amount=3))
    assert fired(board, text) == 2


def test_one_or_more_discards_count_the_cards(board):
    text = "Whenever you discard one or more cards, draw a card."
    watch(board, text)
    first = board.hand("Hill Giant", 0)
    second = board.hand("Forest", 0)
    board.game.event_batch += 1
    actions.discard(board.game, first)
    actions.discard(board.game, second)
    (pending,) = board.game.pending_triggers
    assert pending.event.amount == 2


def test_two_or_more_is_not_read_as_one():
    """Argent Dais: a threshold on the batch, which nothing here records."""
    assert parse_trigger(Stream.of("Whenever two or more creatures attack, draw a card.")) is None


# ---------------------------------------------------------------------------
# "This ability triggers only once each turn"
# ---------------------------------------------------------------------------


def test_once_each_turn_is_enforced(board):
    from dataclasses import replace

    text = "Whenever another creature dies, draw a card."
    trigger = replace(parse_trigger(Stream.of(text)), once_each_turn=True)
    ability = Ability.triggered(trigger, Effect(EffectKind.NOTHING, text=text), text=text)
    board.scripts.add("Llanowar Elves", ability)
    board.play("Llanowar Elves", 0)
    a = board.play("Hill Giant", 1)
    b = board.play("Hill Giant", 1)
    _lethal(board, a)
    assert fired(board, text) == 1
    _lethal(board, b)
    assert fired(board, text) == 0
    board.game.triggered_once_this_turn.clear()  # what a new turn does
    c = board.play("Hill Giant", 1)
    _lethal(board, c)
    assert fired(board, text) == 1


# ---------------------------------------------------------------------------
# Filters on a player's action
# ---------------------------------------------------------------------------


def test_a_discard_trigger_keeps_its_card_filter(board):
    """Waste Not's three abilities each fired on every discard."""
    text = "Whenever an opponent discards a creature card, draw a card."
    watch(board, text)
    actions.discard(board.game, board.hand("Forest", 1))
    assert fired(board, text) == 0
    actions.discard(board.game, board.hand("Hill Giant", 1))
    assert fired(board, text) == 1
    actions.discard(board.game, board.hand("Hill Giant", 0))
    assert fired(board, text) == 0


def test_attacking_something_in_particular_is_not_read():
    """Mila: "an opponent attacks one or more planeswalkers you control" is
    not "an opponent attacks" (CR 508.3e)."""
    text = "Whenever an opponent attacks one or more planeswalkers you control, draw a card."
    assert parse_trigger(Stream.of(text)) is None
    assert parse_trigger(Stream.of("Whenever you attack, draw a card.")) is not None
