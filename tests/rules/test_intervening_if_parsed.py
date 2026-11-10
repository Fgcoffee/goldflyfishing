"""A leading "if" after a trigger is the trigger's intervening-if (CR 603.4).

"When you cast this spell, if it was kicked, exile target land": left to the
effect reader, the "if" wrapped the effect, so the unkicked trigger still went
on the stack and asked for a target (auditor's F52). It is now the trigger's
own condition, checked as the event happens and again on resolution.
"""

from __future__ import annotations

from harness import FixedAgent, make_board

from mtgfish.parser import parse_card
from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.kernel.enums import Phase, Step
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


def test_the_if_is_on_the_trigger(card_db):
    (ability,) = [
        a for a in parse_card(card_db.lookup("Sowing Mycospawn")).faces[0].abilities
        if "kicked" in a.text
    ]
    assert not ability.trigger.intervening_if.is_always
    assert [e.kind.name for e in ability.effects] == ["EXILE"]


def _board(card_db):
    board = make_board(card_db, OracleAbilities())
    for player in board.game.players:
        board.game.agents[player.id] = FixedAgent()
    game = board.game
    game.active_player = P0
    game.phase = Phase.COMBAT
    game.step = Step.DECLARE_ATTACKERS
    return board


def _drawn_on_attack(board, creature) -> int:
    before = len(board.game.player(P0).hand)
    board.game.emit(Event(EventKind.ATTACKS, object_id=creature.id, player=P0))
    board.settle()
    board.resolve_stack()
    return len(board.game.player(P0).hand) - before


def test_a_condition_on_the_source_still_decides_the_outcome(card_db):
    """Angelic Sell-Sword (4/4): "Whenever this creature attacks, if its power
    is 6 or greater, draw a card." "Its" is the object the trigger refers to,
    which nothing has referred to yet as the event happens, so this one stays
    checked on resolution rather than on the trigger: it draws at 6 power,
    not at 4."""
    board = _board(card_db)
    assert _drawn_on_attack(board, board.play("Angelic Sell-Sword")) == 0

    board = _board(card_db)
    sellsword = board.play("Angelic Sell-Sword")
    sellsword.add_counters("+1/+1", 2)
    assert _drawn_on_attack(board, sellsword) == 1


def test_if_you_cast_it_still_fires_its_enters_trigger(card_db):
    """Geological Appraiser: "When this creature enters, if you cast it,
    discover 3." The cast mark is on the permanent as it enters, so the
    condition is the trigger's."""
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
    from mtgfish.rules.kernel.enums import Color

    board = _board(card_db)
    game = board.game
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    card = board.hand("Geological Appraiser")
    game.player(P0).mana_pool.add(ManaKind(Color.RED), 4)
    cast_spell(game, P0, Action(ActionKind.CAST_SPELL, source=card.id))
    board.resolve_stack()
    board.settle()
    assert len(game.stack) == 1


def test_if_you_cast_it_is_on_the_trigger(card_db):
    (ability,) = [
        a for a in parse_card(card_db.lookup("Geological Appraiser")).faces[0].abilities
        if a.trigger is not None
    ]
    assert ability.trigger.intervening_if.kind.name == "WAS_CAST"
    assert [e.kind.name for e in ability.effects] == ["DISCOVER"]


def test_an_appraiser_put_onto_the_battlefield_does_not_trigger(card_db):
    board = _board(card_db)
    appraiser = board.play("Geological Appraiser")
    board.game.emit(Event(EventKind.ENTERS_BATTLEFIELD, object_id=appraiser.id, player=P0))
    board.settle()
    assert len(board.game.stack) == 0


def test_if_you_cast_it_about_another_creature_is_not_read(card_db):
    """"Whenever another creature you control enters, if you cast it": "it"
    is the entering creature, and the cast check asks about the source."""
    for name in ("Prototype X-8", "The Sibsig Ceremony"):
        assert not parse_card(card_db.lookup(name)).fully_parsed, name
