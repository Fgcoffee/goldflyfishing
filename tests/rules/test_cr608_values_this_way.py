"""Values that only a resolution can answer (CR 608.2c, 608.2h).

"You gain life equal to the life lost this way" is an *amount* the resolution
itself produced; "for each creature destroyed this way" counts what it acted
on. Both were read as "the number of whatever was just referred to" and then
evaluated against the whole battlefield, so Gray Merchant gained a life per
permanent in play and Fumigate a life per creature that survived.

A resolving effect's numbers are also fixed when it resolves (CR 608.2h):
a pump that counts Forests stays the size it was.
"""

from __future__ import annotations

from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.enums import CardType, Duration
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import (
    ControllerRelation,
    ObjectFilter,
    PlayerFilter,
    PlayerScope,
    Value,
    ValueKind,
)

ME = PlayerId(0)
LIFE_LOST_THIS_WAY = Value(
    kind=ValueKind.AMOUNT_THIS_WAY, event_kinds=(int(EventKind.LIFE_LOST),)
)


def _run(board, *effects):
    execute(Resolution(game=board.game, source=0, controller=ME), tuple(effects))


def test_life_lost_this_way_is_the_amount_lost(card_db):
    board = make_board(card_db, ScriptedAbilities())
    for _ in range(5):
        board.play("Forest", controller=0)
    me, them = board.game.player(0), board.game.player(1)
    start_me, start_them = me.life, them.life
    _run(
        board,
        Effect(
            EffectKind.LOSE_LIFE,
            players=PlayerFilter(PlayerScope.EACH_OPPONENT),
            amount=Value.of(2),
        ),
        Effect(EffectKind.GAIN_LIFE, players=PlayerFilter(PlayerScope.YOU), amount=LIFE_LOST_THIS_WAY),
    )
    assert them.life == start_them - 2
    assert me.life == start_me + 2


def test_damage_dealt_this_way_counts_damage(card_db):
    board = make_board(card_db, ScriptedAbilities())
    me = board.game.player(0)
    start = me.life
    _run(
        board,
        Effect(
            EffectKind.DAMAGE,
            players=PlayerFilter(PlayerScope.EACH_OPPONENT),
            amount=Value.of(3),
        ),
        Effect(
            EffectKind.GAIN_LIFE,
            players=PlayerFilter(PlayerScope.YOU),
            amount=Value(
                kind=ValueKind.AMOUNT_THIS_WAY,
                event_kinds=(int(EventKind.DAMAGE_DEALT), int(EventKind.COMBAT_DAMAGE_DEALT)),
            ),
        ),
    )
    assert me.life == start + 3


def test_nothing_done_this_way_is_zero(card_db):
    board = make_board(card_db, ScriptedAbilities())
    me = board.game.player(0)
    start = me.life
    _run(board, Effect(EffectKind.GAIN_LIFE, players=PlayerFilter(PlayerScope.YOU), amount=LIFE_LOST_THIS_WAY))
    assert me.life == start


def test_destroyed_this_way_counts_only_what_was_destroyed(card_db):
    """Fumigate: one life per creature it destroyed, not per creature left."""
    board = make_board(card_db, ScriptedAbilities())
    for _ in range(3):
        board.play("Grizzly Bears", controller=1)
    for _ in range(4):
        board.play("Forest", controller=0)
    me = board.game.player(0)
    start = me.life
    creatures = ObjectFilter(types_all=CardType.CREATURE)
    _run(
        board,
        Effect(EffectKind.DESTROY, targets=creatures),
        Effect(
            EffectKind.GAIN_LIFE,
            players=PlayerFilter(PlayerScope.YOU),
            amount=Value(kind=ValueKind.COUNT, filter=ObjectFilter(types_all=CardType.CREATURE, remembered=True)),
        ),
    )
    assert me.life == start + 3


def test_a_remembered_count_outside_a_resolution_is_zero(card_db):
    from mtgfish.rules.kernel.values import evaluate

    board = make_board(card_db, ScriptedAbilities())
    board.play("Grizzly Bears", controller=0)
    value = Value(kind=ValueKind.COUNT, filter=ObjectFilter(remembered=True))
    assert evaluate(board.game, value, controller=ME) == 0


def test_life_gained_this_turn_is_an_amount_not_a_life_total(card_db):
    from mtgfish.rules.kernel.values import evaluate

    board = make_board(card_db, ScriptedAbilities())
    gained = Value(
        kind=ValueKind.EVENT_AMOUNT_THIS_TURN,
        players=PlayerFilter(PlayerScope.YOU),
        event_kinds=(int(EventKind.LIFE_GAINED),),
    )
    assert evaluate(board.game, gained, controller=ME) == 0
    _run(board, Effect(EffectKind.GAIN_LIFE, players=PlayerFilter(PlayerScope.YOU), amount=Value.of(2)))
    _run(board, Effect(EffectKind.GAIN_LIFE, players=PlayerFilter(PlayerScope.YOU), amount=Value.of(3)))
    assert evaluate(board.game, gained, controller=ME) == 5


def test_a_resolving_pump_is_fixed_when_it_resolves(card_db):
    """CR 608.2h: "+X/+X where X is the number of Forests you control"."""
    board = make_board(card_db, ScriptedAbilities())
    bear = board.play("Grizzly Bears", controller=0)
    board.play("Forest", controller=0)
    forests = Value(
        kind=ValueKind.COUNT,
        filter=ObjectFilter(subtypes_all=("Forest",), controller=ControllerRelation.YOU),
    )
    _run(
        board,
        Effect(
            EffectKind.MODIFY_PT,
            targets=ObjectFilter(types_all=CardType.CREATURE, controller=ControllerRelation.YOU),
            amount=forests,
            amount2=forests,
            duration=Duration.END_OF_TURN,
        ),
    )
    board.refresh()
    assert board.pt(bear) == (3, 3)
    board.play("Forest", controller=0)
    board.refresh()
    assert board.pt(bear) == (3, 3)
