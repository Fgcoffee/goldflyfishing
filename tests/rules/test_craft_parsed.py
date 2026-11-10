""""Craft with [materials] [cost]" read from the printed text and played.

The rules session's craft tests script the keyword; this one lets the
parser hand it over, and checks the materials and the count it reads.
"""

from __future__ import annotations

from harness import make_board

from mtgfish.parser import parse_card
from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind, _perform
from mtgfish.rules.kernel.enums import CardType, Color, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions

P0 = PlayerId(0)


def _craft(card_db, name):
    (ability,) = [
        a for a in parse_card(card_db.lookup(name)).faces[0].abilities if a.keyword == "Craft"
    ]
    return ability


def test_materials_and_count(card_db):
    sawblades = _craft(card_db, "Spring-Loaded Sawblades")
    (materials,) = [c for c in sawblades.cost.components if c.kind.name == "EXILE_MATERIALS"]
    assert materials.filter.types_all == CardType.ARTIFACT
    assert materials.amount.constant == 1
    visage = _craft(card_db, "Visage of Dread")
    (materials,) = [c for c in visage.cost.components if c.kind.name == "EXILE_MATERIALS"]
    assert materials.filter.types_all == CardType.CREATURE and materials.amount.constant == 2


def test_unsayable_materials_stay_unread(card_db):
    for name in ("Throne of the Grim Captain", "Eye of Ojer Taq"):
        assert not parse_card(card_db.lookup(name)).fully_parsed, name


def test_parsed_craft_returns_the_card_transformed(card_db):
    board = make_board(card_db, OracleAbilities())
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    game.player(P0).mana_pool.add(ManaKind(Color.WHITE), 4)
    full = card_db.lookup("Spring-Loaded Sawblades").name
    blades = board.play(full)
    board.graveyard("Sol Ring")
    abilities = game.characteristics(blades).abilities
    craft_index = next(i for i, a in enumerate(abilities) if a.keyword == "Craft")
    (action,) = [
        a for a in legal_actions(game, P0)
        if a.kind is ActionKind.ACTIVATE_ABILITY and a.source == blades.id
        and a.ability_index == craft_index
    ]
    assert _perform(game, P0, action)
    board.resolve_stack()
    (chariot,) = [
        o for o in game.objects.values()
        if o.is_live and o.card is not None and o.card.name == full
    ]
    assert chariot.zone is Zone.BATTLEFIELD and chariot.face_index == 1
