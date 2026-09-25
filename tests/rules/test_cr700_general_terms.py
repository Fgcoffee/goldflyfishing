"""CR 700 terms that need tracking: party, modified, activated this turn,
descended, outlaw, worthy, crime and expend."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr600_spells_and_abilities.abilities import TriggerCondition
from mtgfish.rules.cr600_spells_and_abilities.cr603_triggers import condition_met
from mtgfish.rules.cr700_additional_rules.cr700_general import (
    outlaw_filter,
    party_size,
    worthy_filter,
)
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.ids import PlayerId, player_target
from mtgfish.rules.kernel.matching import find, matches
from mtgfish.rules.kernel.query import ObjectFilter, YOU

P0, P1 = PlayerId(0), PlayerId(1)


@pytest.fixture
def board(card_db):
    # A scripted board gives unlisted cards no abilities, so the changeling
    # is given its keyword explicitly.
    from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import (
        KeywordInstance,
        build,
    )

    board = make_board(
        card_db,
        ScriptedAbilities({"Changeling Outcast": build(KeywordInstance("Changeling"))}),
    )
    board.seen = []
    board.game.observer = lambda _g, e: board.seen.append(e)
    return board


def events(board, kind) -> list:
    return [e for e in board.seen if e.kind is kind]


# -- 700.8 party ---------------------------------------------------------


def test_party_counts_one_creature_per_role(board):
    # A Cleric, and a second Cleric that cannot fill another role.
    board.play("Acolyte of Xathrid")  # Human Cleric
    board.play("Acolyte of Xathrid")
    assert party_size(board.game, P0) == 1


def test_a_changeling_fills_whichever_role_is_left(board):
    board.play("Acolyte of Xathrid")  # Cleric
    board.play("Changeling Outcast")  # every creature type
    assert party_size(board.game, P0) == 2


def test_no_party_without_the_types(board):
    board.play("Grizzly Bears")
    assert party_size(board.game, P0) == 0


# -- 700.9 modified ------------------------------------------------------


def test_a_counter_makes_a_permanent_modified(board):
    bears = board.play("Grizzly Bears")
    spec = ObjectFilter(modified=True)
    assert not matches(board.game, bears, spec)
    bears.add_counters("+1/+1", 1)
    board.refresh()
    assert matches(board.game, bears, spec)


# -- 700.10 activated this turn -----------------------------------------


def test_a_permanent_whose_ability_was_activated_this_turn(board):
    from mtgfish.rules.cr500_turn_structure.cr500_turn import clear_turn_activations

    rock = board.play("Sol Ring")
    spec = ObjectFilter(activated_this_turn=True)
    assert not matches(board.game, rock, spec)
    rock.activations_this_turn[0] = 1
    assert matches(board.game, rock, spec)
    clear_turn_activations(board.game)
    assert not matches(board.game, rock, spec)


# -- 700.11 descended ----------------------------------------------------


def test_a_permanent_card_into_the_graveyard_is_descending(board):
    from mtgfish.rules.cr100_game_concepts.actions import destroy

    destroy(board.game, board.play("Grizzly Bears"))
    assert [e.player for e in events(board, EventKind.DESCENDED)] == [P0]


def test_an_instant_or_a_token_is_not(board):
    from mtgfish.rules.cr100_game_concepts.actions import destroy
    from mtgfish.rules.kernel.enums import Zone

    bolt = board.hand("Lightning Bolt")
    board.game.move_object(bolt, Zone.GRAVEYARD)
    destroy(board.game, board.token("Grizzly Bears"))
    assert not events(board, EventKind.DESCENDED)


# -- 700.12 / 700.16 filters ---------------------------------------------


def test_outlaws_and_worthy_creatures(board):
    rogue = board.play("Changeling Outcast")
    bears = board.play("Grizzly Bears")
    assert [o.id for o in find(board.game, outlaw_filter())] == [rogue.id]
    assert bears not in find(board.game, worthy_filter())


# -- 700.13 crime --------------------------------------------------------


def _cast_targeting(board, target) -> None:
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
    from mtgfish.rules.kernel.enums import Color

    bolt = board.hand("Lightning Bolt")
    board.game.player(P0).mana_pool.add(ManaKind(Color.RED), 1)
    cast_spell(
        board.game, P0, Action(ActionKind.CAST_SPELL, source=bolt.id, targets=((target,),))
    )


def test_targeting_an_opponent_is_a_crime(board):
    _cast_targeting(board, player_target(P1))
    assert [e.player for e in events(board, EventKind.CRIME_COMMITTED)] == [P0]


def test_targeting_an_opponents_creature_is_a_crime(board):
    _cast_targeting(board, board.play("Grizzly Bears", controller=1).id)
    assert events(board, EventKind.CRIME_COMMITTED)


def test_targeting_your_own_creature_is_not(board):
    _cast_targeting(board, board.play("Grizzly Bears", controller=0).id)
    assert not events(board, EventKind.CRIME_COMMITTED)


# -- 700.14 expend -------------------------------------------------------


def test_expend_counts_each_total_crossed(board):
    """A three-mana spell after one mana spent this turn expends 2, 3 and 4."""
    board.game.player(P0).mana_spent_on_spells_this_turn = 1
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
    from mtgfish.rules.kernel.enums import Color

    card = board.hand("Divination")
    board.game.player(P0).mana_pool.add(ManaKind(Color.BLUE), 3)
    cast_spell(board.game, P0, Action(ActionKind.CAST_SPELL, source=card.id))
    assert [e.amount for e in events(board, EventKind.EXPENDED)] == [2, 3, 4]


def test_whenever_you_expend_four_fires_on_four_only(board):
    trigger = TriggerCondition(event_kinds=frozenset({EventKind.EXPENDED}), players=YOU, expend=4)
    source = board.play("Grizzly Bears")
    assert condition_met(board.game, source, trigger, Event(EventKind.EXPENDED, player=P0, amount=4))
    assert not condition_met(
        board.game, source, trigger, Event(EventKind.EXPENDED, player=P0, amount=3)
    )
