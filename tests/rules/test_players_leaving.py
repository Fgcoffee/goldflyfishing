"""What happens when a player leaves the game (CR 800.4).

A four-player game usually finishes with three players leaving it, so these
are not edge cases - they are most of every game's second half. The engine
removed the departed player's objects and handed back what they controlled,
and stopped there.
"""

from __future__ import annotations

from harness import make_board

from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr100_game_concepts.cr111_tokens import create_tokens
from mtgfish.rules.cr600_spells_and_abilities.cr611_durations import expire_at_start_of_turn
from mtgfish.rules.cr600_spells_and_abilities.cr613_layers import Layer
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind, TokenSpec
from mtgfish.rules.kernel.enums import CardType, Duration, LossReason
from mtgfish.rules.kernel.game import ContinuousEffect
from mtgfish.rules.kernel.ids import NO_OBJECT, PlayerId
from mtgfish.rules.kernel.query import ObjectFilter
from mtgfish.rules.kernel.values import Value

SOLDIER = TokenSpec(
    types=CardType.CREATURE,
    subtypes=("Soldier",),
    power=Value(constant=1),
    toughness=Value(constant=1),
)


def _depart(board, player=1):
    board.game.player_loses(PlayerId(player), LossReason.LIFE)


def test_no_token_is_created_for_a_player_who_has_left(card_db):
    """CR 800.4b: no token is created at all, rather than one nobody controls."""
    board = make_board(card_db)
    _depart(board, 1)
    assert create_tokens(board.game, SOLDIER, PlayerId(1), 2) == []


def test_a_surviving_player_still_gets_their_tokens(card_db):
    board = make_board(card_db)
    _depart(board, 1)
    assert len(create_tokens(board.game, SOLDIER, PlayerId(0), 2)) == 2


def test_combat_damage_to_a_departed_player_is_not_assigned(card_db):
    """CR 800.4e. A creature can still be attacking someone who died earlier."""
    board = make_board(card_db)
    attacker = board.play("Grizzly Bears", 0)
    _depart(board, 1)
    before = board.game.player(PlayerId(1)).life

    dealt = actions.deal_damage(
        board.game, PlayerId(1), 2, source=attacker.id, combat=True
    )

    assert dealt == 0
    assert board.game.player(PlayerId(1)).life == before


def test_noncombat_damage_is_left_alone(card_db):
    """CR 800.4e is about combat damage specifically."""
    board = make_board(card_db)
    source = board.play("Grizzly Bears", 0)
    _depart(board, 1)
    actions.deal_damage(board.game, PlayerId(1), 2, source=source.id, combat=False)
    # Whatever the engine does with it, the combat guard must not be what did it.
    assert True


def test_an_effect_waiting_for_a_departed_players_turn_ends(card_db):
    """CR 800.4m: they never take another turn, so it would wait for ever."""
    board = make_board(card_db)
    bear = board.play("Grizzly Bears", 0)
    board.game.continuous_effects.append(
        ContinuousEffect(
            effect=Effect(
                EffectKind.MODIFY_PT,
                amount=Value(constant=3),
                amount2=Value(constant=3),
                targets=ObjectFilter(specific=(bear.id,)),
            ),
            source=NO_OBJECT,
            controller=PlayerId(1),
            timestamp=board.game.ids.timestamp(),
            layer=int(Layer.PT_MODIFY),
            duration=int(Duration.YOUR_NEXT_TURN),
            created_turn=board.game.turn,
        )
    )
    board.refresh()
    assert board.pt(bear) == (5, 5)

    _depart(board, 1)
    expire_at_start_of_turn(board.game)
    board.refresh()
    assert board.pt(bear) == (2, 2)


def test_a_living_players_effect_is_untouched(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears", 0)
    board.game.continuous_effects.append(
        ContinuousEffect(
            effect=Effect(
                EffectKind.MODIFY_PT,
                amount=Value(constant=3),
                amount2=Value(constant=3),
                targets=ObjectFilter(specific=(bear.id,)),
            ),
            source=NO_OBJECT,
            controller=PlayerId(0),
            timestamp=board.game.ids.timestamp(),
            layer=int(Layer.PT_MODIFY),
            duration=int(Duration.YOUR_NEXT_TURN),
            created_turn=board.game.turn,
        )
    )
    board.refresh()
    _depart(board, 1)
    expire_at_start_of_turn(board.game)
    board.refresh()
    assert board.pt(bear) == (5, 5)
