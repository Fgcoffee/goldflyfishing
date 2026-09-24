"""A triggered ability outliving whatever created it (CR 603.3d, 800.4a).

A trigger does not go on the stack when it triggers. It is noted, and reaches
the stack the next time a player would receive priority - and in between, the
thing that triggered it can be gone. Two different disappearances, with two
different answers, and the engine used to give the same wrong one to both:

* **The source is gone.** A token that ceased to exist (CR 111.7), a creature
  that died. The ability is independent of its source once it has triggered
  (CR 603.3d) and still resolves, under whoever controlled the source when it
  triggered.
* **The controller is gone.** CR 800.4a: everything a departed player owns
  leaves the game, and the abilities they control cease to exist. This one
  must *not* resolve.

Both were handled by reading the controller off the source at the moment the
ability reached the stack, and calling it ``NO_PLAYER`` when the source could
not be found. That gave the first case no controller, so a "create a token"
trigger made a token owned by nobody - which crashed the whole game the moment
it left the battlefield, with ``GRAVEYARD is player-owned; a player is
required``, a long way from the trigger that caused it.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from harness import make_board

from mtgfish.rules.cr600_spells_and_abilities.abilities import (
    Ability,
    AbilityKind,
    TriggerCondition,
)
from mtgfish.rules.cr600_spells_and_abilities.cr603_triggers import (
    PendingTrigger,
    put_triggers_on_stack,
)
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind, TokenSpec
from mtgfish.rules.kernel.enums import CardType, LossReason, Zone
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.ids import NO_PLAYER, PlayerId
from mtgfish.rules.kernel.query import ZERO

ONE = replace(ZERO, constant=1)

#: "When this dies, create a 1/1 Soldier." A token-making trigger is the one
#: that turned a missing controller into a crash rather than a quiet nothing.
MAKES_A_TOKEN = Ability(
    AbilityKind.TRIGGERED,
    trigger=TriggerCondition(event_kinds=frozenset({EventKind.DIES})),
    effects=(
        Effect(
            kind=EffectKind.CREATE_TOKEN,
            token=TokenSpec(
                name="Soldier",
                types=CardType.CREATURE,
                subtypes=("Soldier",),
                power=ONE,
                toughness=ONE,
            ),
        ),
    ),
    text="When this dies, create a 1/1 Soldier.",
)


@pytest.fixture
def table(card_db):
    return make_board(card_db, players=4)


def queue_trigger(board, source) -> None:
    """Note the ability, exactly as ``collect_triggers`` would."""
    board.game.pending_triggers.append(
        PendingTrigger(
            source.id,
            MAKES_A_TOKEN,
            Event(EventKind.DIES, object_id=source.id),
            source.controller,
        )
    )


def tokens_of(board, player: int) -> list:
    return [
        obj
        for obj in board.game.objects.values()
        if obj.is_token and obj.owner == PlayerId(player)
    ]


# ---------------------------------------------------------------------------
# CR 603.3d: the source is gone, the ability is not
# ---------------------------------------------------------------------------


def test_a_trigger_outlives_a_source_that_ceased_to_exist(table):
    """A token's own dies-trigger is exactly this case."""
    source = table.token("Grizzly Bears", controller=2)
    queue_trigger(table, source)

    table.game._remove_from_zone(source)
    del table.game.objects[source.id]

    put_triggers_on_stack(table.game)
    table.resolve_stack()

    made = tokens_of(table, 2)
    assert len(made) == 1, "the ability exists independently of its source"
    assert made[0].controller == PlayerId(2)


def test_what_it_makes_belongs_to_somebody(table):
    """The bug itself. A token owned by ``NO_PLAYER`` is in no player's zones,
    so the first thing that moved it raised out of the priority loop and took
    the whole game with it."""
    source = table.token("Grizzly Bears", controller=2)
    queue_trigger(table, source)
    table.game._remove_from_zone(source)
    del table.game.objects[source.id]

    put_triggers_on_stack(table.game)
    table.resolve_stack()

    assert all(obj.owner != NO_PLAYER for obj in table.game.objects.values())


def test_a_token_made_by_such_a_trigger_can_die_without_crashing(table):
    """The crash, end to end: it happened when the token left the battlefield,
    several steps after the trigger that caused it."""
    from mtgfish.rules.cr100_game_concepts.actions import put_into_graveyard

    source = table.token("Grizzly Bears", controller=2)
    queue_trigger(table, source)
    table.game._remove_from_zone(source)
    del table.game.objects[source.id]

    put_triggers_on_stack(table.game)
    table.resolve_stack()

    # Every token, whoever it belongs to: the crash needed one belonging to
    # nobody, so a test that only looked at P2's would miss it entirely.
    made = [obj for obj in table.game.objects.values() if obj.is_token]
    assert made
    for token in made:
        put_into_graveyard(table.game, token)

    # CR 704.5d finds a token off the battlefield and removes it. With an
    # owner of NO_PLAYER this raised ``GRAVEYARD is player-owned; a player is
    # required`` straight out of the priority loop, ending the whole game.
    table.sba()
    assert not [obj for obj in table.game.objects.values() if obj.is_token]


# ---------------------------------------------------------------------------
# CR 800.4a: the controller is gone, and so is the ability
# ---------------------------------------------------------------------------


def test_a_trigger_whose_controller_left_the_game_ceases_to_exist(table):
    source = table.play("Grizzly Bears", controller=1)
    queue_trigger(table, source)

    table.game.player_loses(PlayerId(1), LossReason.LIFE)
    put_triggers_on_stack(table.game)

    assert not table.game.stack, "CR 800.4a: the ability ceases to exist"
    table.resolve_stack()
    assert not tokens_of(table, 1)


def test_it_is_dropped_rather_than_left_waiting(table):
    """Left in the queue it would be retried at every priority check for the
    rest of the game."""
    source = table.play("Grizzly Bears", controller=1)
    queue_trigger(table, source)

    table.game.player_loses(PlayerId(1), LossReason.LIFE)
    put_triggers_on_stack(table.game)
    assert not table.game.pending_triggers


def test_the_log_says_why_it_never_resolved(table):
    """Silently dropping a trigger is how a card gets reported as doing
    nothing when the rules were working."""
    source = table.play("Grizzly Bears", controller=1)
    queue_trigger(table, source)
    table.game.log.enabled = True

    table.game.player_loses(PlayerId(1), LossReason.LIFE)
    put_triggers_on_stack(table.game)

    assert any(
        "left the game" in entry.text for entry in table.game.log.entries
    ), [e.text for e in table.game.log.entries]


def test_another_player_s_trigger_is_untouched(table):
    """One player leaving must not take everyone else's triggers with it."""
    mine = table.play("Grizzly Bears", controller=2)
    theirs = table.play("Grizzly Bears", controller=1)
    queue_trigger(table, mine)
    queue_trigger(table, theirs)

    table.game.player_loses(PlayerId(1), LossReason.LIFE)
    put_triggers_on_stack(table.game)
    table.resolve_stack()

    assert len(tokens_of(table, 2)) == 1
    assert not tokens_of(table, 1)


# ---------------------------------------------------------------------------
# The controller is read when it triggers, not when it reaches the stack
# ---------------------------------------------------------------------------


def test_the_controller_is_captured_when_the_ability_triggers(table):
    """CR 603.3d. Reading it late is what made both cases above wrong, and it
    is also wrong on its own terms: control of the source can change in
    between, and the ability stays with whoever had it when it triggered."""
    source = table.play("Grizzly Bears", controller=2)
    queue_trigger(table, source)

    source.controller = PlayerId(3)
    source.base_controller = PlayerId(3)
    table.refresh()

    put_triggers_on_stack(table.game)
    table.resolve_stack()

    assert len(tokens_of(table, 2)) == 1, "it stays with the player who had it"
    assert not tokens_of(table, 3)
