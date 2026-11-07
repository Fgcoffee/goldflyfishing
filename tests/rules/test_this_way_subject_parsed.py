""""If an artifact is destroyed this way, ...": the subject is read.

It was skipped, every word up to "this way", so the condition became "if
anything happened": Smashing Success made a Treasure for a destroyed land,
Oviya put counters on any creature, and "If you didn't put a card into your
hand this way" read as its opposite. A subject the reader cannot say is
left unread.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, make_board

from mtgfish.parser import parse_card
from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.kernel.enums import Color, Phase, Step
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


def _treasures_after_smashing(card_db, target_name):
    board = make_board(card_db, OracleAbilities())
    for player in board.game.players:
        board.game.agents[player.id] = FixedAgent()
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    target = board.play(target_name, controller=1)
    card = board.hand("Smashing Success")
    game.player(P0).mana_pool.add(ManaKind(Color.RED), 4)
    cast_spell(game, P0, Action(ActionKind.CAST_SPELL, source=card.id, targets=((target.id,),)))
    board.resolve_stack()
    board.settle()
    board.resolve_stack()
    return sum(
        1
        for obj in game.objects.values()
        if obj.zone.name == "BATTLEFIELD"
        and obj.controller == P0
        and "Treasure" in game.characteristics(obj).subtypes
    )


def test_smashing_success_pays_only_for_an_artifact(card_db):
    assert _treasures_after_smashing(card_db, "Sol Ring") == 1
    assert _treasures_after_smashing(card_db, "Forest") == 0


def _condition_filters(card_db, name):
    out = []
    for ability in parse_card(card_db.lookup(name)).faces[0].abilities:
        for effect in ability.effects:
            for node in effect.walk():
                if node.kind.name == "REFLEXIVE_TRIGGER":
                    out += [
                        child.condition.filter
                        for child in node.children
                        if child.kind.name == "CONDITIONAL"
                    ]
    return out


def test_the_subject_becomes_the_condition(card_db):
    from mtgfish.rules.kernel.enums import CardType

    (oviya,) = _condition_filters(card_db, "Oviya, Automech Artisan")
    assert oviya.types_all == CardType.ARTIFACT
    (deathgorge,) = _condition_filters(card_db, "Deathgorge Scavenger")[:1]
    assert deathgorge.types_all == CardType.CREATURE


@pytest.mark.parametrize(
    "name",
    [
        # "If you didn't put a card into your hand this way" - a negation.
        "Adventure Awaits",
        # "If damage is prevented this way" - not an object.
        "Acolyte's Reward",
        # "If a Saproling was sacrificed this way" - the cost's, not the
        # resolution's.
        "Thallid Omnivore",
    ],
)
def test_a_subject_not_understood_is_not_read(card_db, name):
    assert not parse_card(card_db.lookup(name)).fully_parsed
