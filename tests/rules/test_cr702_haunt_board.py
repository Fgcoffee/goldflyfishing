"""Haunt (CR 702.55a-c): a haunt card is exiled haunting a target creature -
a creature when it dies, an instant or sorcery as it resolves - and its
abilities that name "the creature it haunts" trigger from exile.

The keyword half is played from the parsed text of Absolver Thrull and Cry
of Contrition. The parser does not yet read "the creature it haunts dies"
(that sentence is unparsed on every haunt card), so the exile half is
played with that trigger written out as the IR the parser should emit."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.actions import destroy
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, TriggerCondition
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.kernel.enums import CardType, Zone
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.ids import NO_OBJECT, PlayerId, player_target
from mtgfish.rules.kernel.query import ObjectFilter
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0, P1 = PlayerId(0), PlayerId(1)
THRULL = "Absolver Thrull"
CRY = "Cry of Contrition"
BEARS = "Grizzly Bears"
ENCHANTMENT = "Glorious Anthem"


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    for name in (THRULL, CRY, BEARS, "Counterspell", "Island"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def named(game, name):
    return [
        o for o in game.objects.values()
        if o.is_live and o.card is not None and o.card.name == name
    ]


def on_battlefield(game, name):
    return [o for o in named(game, name) if o.zone is Zone.BATTLEFIELD]


def resolve_all(box):
    box.settle()
    for _ in range(10):
        if not box.game.stack:
            return
        box.resolve_top()


def test_a_dying_haunt_creature_is_exiled_haunting_a_creature(box):
    box.put(THRULL, "battlefield", 0)
    box.put(BEARS, "battlefield", 1)
    (bears,) = named(box.game, BEARS)
    (thrull,) = named(box.game, THRULL)
    destroy(box.game, thrull)
    resolve_all(box)
    (thrull,) = named(box.game, THRULL)
    assert thrull.zone is Zone.EXILE
    assert thrull.haunting == bears.id


def test_a_creature_without_haunt_stays_in_the_graveyard(box):
    """Control: the exile is the keyword's, not every creature's."""
    box.put(BEARS, "battlefield", 0)
    box.put(BEARS, "battlefield", 1)
    mine = next(o for o in named(box.game, BEARS) if o.controller == P0)
    destroy(box.game, mine)
    resolve_all(box)
    assert box.game.objects[mine.superseded_by].zone is Zone.GRAVEYARD


def cast_cry(box):
    box.put("Island", "hand", 1)
    box.put(CRY, "hand", 0)
    box.give_mana(5)
    (action,) = [a for a in box.legal(0) if a["name"] == CRY and a["kind"].startswith("CAST")]
    state = box.perform(action["index"], targets=[[player_target(P1)]])
    assert not state.get("error"), state.get("error")
    return box.game.objects[box.game.stack[-1]]


def test_a_haunt_spell_is_exiled_haunting_a_creature_as_it_resolves(box):
    box.put(BEARS, "battlefield", 1)
    (bears,) = named(box.game, BEARS)
    cast_cry(box)
    resolve_all(box)
    (cry,) = named(box.game, CRY)
    assert cry.zone is Zone.EXILE
    assert cry.haunting == bears.id
    assert len(box.game.player(P1).hand) == 0  # the spell itself did resolve


def test_a_countered_haunt_spell_stays_in_the_graveyard(box):
    """Control: CR 702.55a is "during its resolution", which a countered
    spell never has."""
    box.put(BEARS, "battlefield", 1)
    box.put("Counterspell", "hand", 1)
    spell = cast_cry(box)
    box.give_mana(5, player=1)
    (counter,) = [
        a for a in box.legal(1) if a["name"] == "Counterspell" and a["kind"].startswith("CAST")
    ]
    assert not box.perform(counter["index"], player=1, targets=[[spell.id]]).get("error")
    resolve_all(box)
    (cry,) = named(box.game, CRY)
    assert cry.zone is Zone.GRAVEYARD
    assert cry.haunting == NO_OBJECT


def _when_the_creature_it_haunts_dies(*effects):
    """The IR for Absolver Thrull's second paragraph, "When this creature
    enters or the creature it haunts dies, destroy target enchantment" -
    what the parser should emit for it (CR 113.6k, 702.55c)."""
    enters = TriggerCondition(
        event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
        subject=ObjectFilter(source_only=True),
        text="when this creature enters",
    )
    haunted_dies = TriggerCondition(
        event_kinds=frozenset({EventKind.DIES}),
        subject=ObjectFilter(types_all=CardType.CREATURE, haunted_by_source=True),
        functions_in=frozenset({Zone.EXILE}),
        uses_last_known_information=True,
        text="when the creature it haunts dies",
    )
    return Ability.triggered(
        TriggerCondition(
            alternatives=(enters, haunted_dies),
            functions_in=frozenset({Zone.BATTLEFIELD, Zone.EXILE}),
            text="when this creature enters or the creature it haunts dies",
        ),
        *effects,
        text="When this creature enters or the creature it haunts dies, "
        "destroy target enchantment.",
    )


@pytest.fixture
def scripted(card_db):
    for name in (THRULL, BEARS, "Hill Giant", ENCHANTMENT):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    destroy_enchantment = Effect(
        EffectKind.DESTROY,
        targets=ObjectFilter(types_all=CardType.ENCHANTMENT),
        is_targeted=True,
        text="destroy target enchantment",
    )
    scripts = ScriptedAbilities({
        THRULL: (
            *build(KeywordInstance("Haunt")),
            _when_the_creature_it_haunts_dies(destroy_enchantment),
        ),
        ENCHANTMENT: (),
    })
    board = make_board(card_db, scripts)
    for player in (0, 1):
        board.game.agents[player] = PassiveOpponent()
    return board


def test_the_haunted_creature_dying_triggers_the_card_in_exile(scripted):
    board = scripted
    game = board.game
    thrull = board.play(THRULL, 0)
    bears = board.play(BEARS, 1)
    board.play(ENCHANTMENT, 1)
    destroy(game, thrull)
    board.settle()
    board.resolve_stack()
    (exiled,) = named(game, THRULL)
    assert exiled.zone is Zone.EXILE and exiled.haunting == bears.id
    assert on_battlefield(game, ENCHANTMENT)
    destroy(game, bears)
    board.settle()
    board.resolve_stack()
    assert not on_battlefield(game, ENCHANTMENT)


def test_another_creature_dying_does_not(scripted):
    """Control: only the creature it haunts."""
    board = scripted
    game = board.game
    thrull = board.play(THRULL, 0)
    bears = board.play(BEARS, 1)
    giant = board.play("Hill Giant", 1)
    board.play(ENCHANTMENT, 1)
    destroy(game, thrull)
    board.settle()
    board.resolve_stack()
    (exiled,) = named(game, THRULL)
    haunted = bears if exiled.haunting == bears.id else giant
    other = giant if haunted is bears else bears
    destroy(game, other)
    board.settle()
    board.resolve_stack()
    assert on_battlefield(game, ENCHANTMENT)
    destroy(game, haunted)
    board.settle()
    board.resolve_stack()
    assert not on_battlefield(game, ENCHANTMENT)
