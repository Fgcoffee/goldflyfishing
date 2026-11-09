"""A leading "Until end of turn, ..." reaches every effect it covers.

The duration was stamped onto the sentence's top-level effect only, and a
"becomes" is a SEQUENCE of several, so Turn to Frog's changes were permanent
(auditor's F33): the Angel was still a 1/1 Frog with no abilities the next
turn. The engine reads a duration off each effect it runs.
"""

from __future__ import annotations

from harness import FixedAgent, make_board

from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.cr600_spells_and_abilities.cr611_durations import expire_at_cleanup
from mtgfish.rules.kernel.enums import Color, Phase, Step
from mtgfish.rules.kernel.ids import PlayerId

P0 = PlayerId(0)


def test_turn_to_frog_wears_off_at_end_of_turn(card_db):
    board = make_board(card_db, OracleAbilities())
    for player in board.game.players:
        board.game.agents[player.id] = FixedAgent()
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    angel = board.play("Serra Angel", controller=1)
    frog = board.hand("Turn to Frog")
    game.player(P0).mana_pool.add(ManaKind(Color.BLUE), 2)
    cast_spell(game, P0, Action(ActionKind.CAST_SPELL, source=frog.id, targets=((angel.id,),)))
    board.resolve_stack()
    assert board.pt(angel) == (1, 1)
    assert "Flying" not in board.keywords(angel)

    expire_at_cleanup(game)
    board.refresh()
    assert board.pt(angel) == (4, 4)
    assert {"Flying", "Vigilance"} <= board.keywords(angel)
    assert "Frog" not in board.chars(angel).type_line.subtypes


def _kinds_with_durations(card_db, name, face=0):
    from mtgfish.parser import parse_card

    out = {}
    for ability in parse_card(card_db.lookup(name)).faces[face].abilities:
        for effect in ability.effects:
            for node in effect.walk():
                out.setdefault(node.kind.name, set()).add(int(node.duration))
    return out


def test_a_leading_duration_covers_only_its_own_sentence(card_db):
    """Auditor's F44: "Until end of turn, ... Goad it." - the goad and the
    untap are separate sentences, not until end of turn."""
    besmirch = _kinds_with_durations(card_db, "Besmirch")
    assert besmirch["GAIN_CONTROL"] == {1}
    assert besmirch["GOAD"] == {0} and besmirch["UNTAP"] == {0}
    teferi = _kinds_with_durations(card_db, "Teferi's Protection")
    assert teferi["PHASE_OUT"] == {0}


def test_an_if_sentence_after_a_leading_duration_stays_its_own(card_db):
    """Venat's back face: "Until your next turn, it gains indestructible. If
    that creature is legendary, draw a card." - the "if" governs the draw,
    not the indestructible."""
    from mtgfish.parser import parse_card

    card = card_db.lookup("Venat, Heart of Hydaelyn // Hydaelyn, the Mothercrystal")
    (ability,) = [a for a in parse_card(card).faces[1].abilities if "legendary" in a.text]
    conditionals = [
        n for e in ability.effects for n in e.walk() if n.kind.name == "CONDITIONAL" and n.text == "if"
    ]
    (gate,) = conditionals
    assert [c.kind.name for c in gate.children] == ["DRAW"]
