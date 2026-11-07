"""Separating objects into two piles (CR 700.3): the separator splits, the
chooser picks, and each pile gets its own instruction."""

from __future__ import annotations

from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import Color, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import (
    YOU,
    ObjectFilter,
    PlayerFilter,
    PlayerScope,
    Value,
)

P0, P1 = PlayerId(0), PlayerId(1)
IT = ObjectFilter(remembered=True)

FACT_OR_FICTION = Ability.spell(
    Effect(
        EffectKind.LOOK_AT_TOP,
        players=YOU,
        amount=Value.of(5),
        text="reveal the top five cards of your library",
    ),
    Effect(
        EffectKind.SEPARATE_INTO_PILES,
        actor=PlayerFilter(PlayerScope.OPPONENT),
        players=YOU,
        targets=ObjectFilter(from_pile=True),
        children=(Effect(EffectKind.RETURN_TO_HAND, targets=IT, text="put one pile into your hand"),),
        otherwise=(Effect(EffectKind.MOVE_ZONE, targets=IT, zone=Zone.GRAVEYARD, text="and the other into your graveyard"),),
        text="an opponent separates them into two piles",
    ),
)


class Splitter(FixedAgent):
    def __init__(self, first_count, pick, **kw):
        super().__init__(**kw)
        self.first_count = first_count
        self.pick = pick

    def separate_piles(self, game, player, objects):
        return objects[: self.first_count]

    def choose_pile(self, game, player, first, second):
        return self.pick


def setup(card_db, agent0, agent1):
    board = make_board(card_db, ScriptedAbilities({"Divination": (FACT_OR_FICTION,)}))
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    game.agents[P0] = agent0
    game.agents[P1] = agent1
    for oid in list(game.player(P0).hand):
        game.move_object(game.objects[oid], Zone.LIBRARY)
    game.player(P0).mana_pool.add(ManaKind(Color.BLUE), 3)
    return board


def cast_and_resolve(board):
    card = board.hand("Divination")
    cast_spell(board.game, P0, Action(ActionKind.CAST_SPELL, source=card.id))
    board.resolve_stack()


def test_the_chooser_takes_the_pile_they_pick_and_the_other_goes_to_the_graveyard(card_db):
    board = setup(card_db, Splitter(0, 1), Splitter(1, 0))  # opponent: 1 and 4
    cast_and_resolve(board)
    game = board.game
    # The chooser (P0) picked pile 1 - the four cards - into hand.
    assert len(game.player(P0).hand) == 4
    assert sum(1 for oid in game.player(P0).graveyard
               if game.objects[oid].card.name != "Divination") == 1


def test_choosing_the_other_pile(card_db):
    board = setup(card_db, Splitter(0, 0), Splitter(1, 0))
    cast_and_resolve(board)
    game = board.game
    assert len(game.player(P0).hand) == 1
    assert sum(1 for oid in game.player(P0).graveyard
               if game.objects[oid].card.name != "Divination") == 4


def test_an_empty_pile_is_a_pile(card_db):
    """CR 700.3a: all five in one pile, and the chooser may take the empty one."""
    board = setup(card_db, Splitter(0, 1), Splitter(5, 0))
    cast_and_resolve(board)
    game = board.game
    assert len(game.player(P0).hand) == 0
