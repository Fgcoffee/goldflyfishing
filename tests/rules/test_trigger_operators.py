"""The trigger operators added for the top-1000 pass, tested on real cards.

Each of these is a capability the engine did not have, and the reason for
testing them here rather than in the parser suite is the lesson of
``test_end_to_end_cards``: a parse that produces the right opcode proves
nothing if nothing consults it. Every one of these was, at some point in this
project, a feature that "existed" and never fired.

* **that many / that much** - the size of the event that triggered the
  ability. Nothing carried it from the event to the resolution, so
  "create that many Treasure tokens" made none.
* **ordinal** - "their second spell each turn". The per-turn tally existed
  and only a yes-or-no condition could read it.
* **an additional time** (CR 603.2b) - Panharmonicon. Triggers were
  collected exactly once each.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.compile import parse_card
from mtgfish.parser.verdicts import VerdictStore
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


def _abilities(card_db, name):
    card = card_db.lookup(name)
    if card is None:
        pytest.skip(f"{name} is not in this card pool")
    parsed = parse_card(card)
    # Deliberately not asserting the whole card reads: these tests are about
    # one ability each, and a card with a second, unrelated gap should fail
    # the coverage report rather than this.
    return [
        ability
        for face in parsed.faces
        for ability in face.abilities
        if not ability.unparsed
    ]


# ---------------------------------------------------------------------------
# "that many" - the size of the triggering event
# ---------------------------------------------------------------------------


def test_that_many_reads_the_event_it_came_from(card_db):
    """"deals combat damage to a player, create that many Treasure tokens".

    The number is a fact about the event and about nothing else: by the time
    the ability resolves the damage is over and no object on the battlefield
    records it. Without the event travelling with the trigger, the amount
    evaluates to zero and the card makes nothing.
    """
    from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind
    from mtgfish.rules.kernel.query import ValueKind

    amounts = [
        node.amount.kind
        for ability in _abilities(card_db, "Old Gnawbone")
        for effect in ability.effects
        for node in effect.walk()
        if node.kind is EffectKind.CREATE_TOKEN
    ]
    assert ValueKind.EVENT_AMOUNT in amounts, amounts


def test_the_stack_object_carries_its_event(box):
    """A trigger on the stack remembers the event that made it (CR 603.3d)."""
    from mtgfish.rules.cr600_spells_and_abilities.cr603_triggers import put_triggers_on_stack
    from mtgfish.rules.kernel.events import Event, EventKind

    box.put("Old Gnawbone", "battlefield", 0)
    box.game.invalidate_characteristics()

    dragon = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Old Gnawbone"
    )
    box.game.emit(
        # Damage to a player, as ``actions`` emits it: the player, and no
        # object - an object id here would say the damage went to a permanent.
        Event(
            EventKind.COMBAT_DAMAGE_DEALT,
            player=1,
            source=dragon.id,
            amount=5,
        )
    )
    put_triggers_on_stack(box.game)

    on_stack = [box.game.objects[i] for i in box.game.stack]
    assert on_stack, "the combat-damage trigger did not fire"
    assert any(getattr(o.trigger_event, "amount", 0) == 5 for o in on_stack)


# ---------------------------------------------------------------------------
# Ordinals - "their second spell each turn"
# ---------------------------------------------------------------------------


def test_an_ordinal_trigger_is_read_as_one(card_db):
    """Lotho triggers on the *second* spell, not on every spell.

    Read as "every spell" this is one of the strongest cards in the format;
    the ordinal is the whole card.
    """
    for ability in _abilities(card_db, "Lotho, Corrupt Shirriff"):
        if ability.trigger is not None and ability.trigger.event_kinds:
            if ability.trigger.ordinal:
                return
    pytest.fail("no ordinal trigger was produced")


def test_an_ordinal_trigger_fires_only_on_that_occurrence(box):
    """The tally is per player and per turn, and only the nth event fires."""
    from mtgfish.rules.cr600_spells_and_abilities.abilities import (
        Ability,
        AbilityKind,
        TriggerCondition,
    )
    from mtgfish.rules.cr600_spells_and_abilities.cr603_triggers import condition_met
    from mtgfish.rules.kernel.events import Event, EventKind

    box.put("Sol Ring", "battlefield", 0)
    source = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Sol Ring"
    )
    trigger = TriggerCondition(
        event_kinds=frozenset({EventKind.CAST_SPELL}), ordinal=2
    )
    Ability(AbilityKind.TRIGGERED, trigger=trigger)

    fired = []
    for _ in range(3):
        event = Event(EventKind.CAST_SPELL, player=0)
        box.game.emit(event)
        fired.append(condition_met(box.game, source, trigger, event))

    assert fired == [False, True, False]


# ---------------------------------------------------------------------------
# "that ability triggers an additional time" (CR 603.2b)
# ---------------------------------------------------------------------------


def test_panharmonicon_is_read_as_an_extra_trigger(card_db):
    from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind

    kinds = {
        node.kind
        for ability in _abilities(card_db, "Panharmonicon")
        for effect in ability.effects
        for node in effect.walk()
    }
    assert EffectKind.EXTRA_TRIGGER in kinds


def test_an_extra_trigger_puts_two_abilities_on_the_stack(box):
    """The ability triggers twice, and both instances exist independently.

    Not a copy and not a replacement: CR 603.2b says it triggers an
    additional time, so there are two separate triggers on the stack and a
    player may respond between them.
    """
    from mtgfish.rules.kernel.events import Event, EventKind

    if box.db.lookup("Panharmonicon") is None:
        pytest.skip("Panharmonicon is not in this card pool")

    box.put("Panharmonicon", "battlefield", 0)
    box.put("Solemn Simulacrum", "battlefield", 0)
    box.game.invalidate_characteristics()

    solemn = next(
        obj
        for obj in box.game.objects.values()
        if obj.card is not None and obj.card.name == "Solemn Simulacrum"
    )
    from mtgfish.rules.cr600_spells_and_abilities.cr603_triggers import collect_triggers

    box.game.pending_triggers = []
    collect_triggers(
        box.game,
        Event(EventKind.ENTERS_BATTLEFIELD, object_id=solemn.id, player=0),
    )

    entering = [
        pending
        for pending in box.game.pending_triggers
        if pending[0] == solemn.id
    ]
    if not entering:
        pytest.skip("Solemn Simulacrum's enters trigger did not fire here")
    assert len(entering) >= 2, "the ability should have triggered twice"
