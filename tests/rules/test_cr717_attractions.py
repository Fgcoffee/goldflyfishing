"""Attractions (CR 717): the Attraction deck, opening, visiting and the junkyard.

The card snapshot has no lit numbers (Scryfall's ``attraction_lights`` is not
in it), so every test that needs an Attraction to be visited gives a real
Attraction card its lights with ``dataclasses.replace`` - which is exactly the
field a snapshot that had them would fill in. Nothing here is keyed on a card's
name: the cards are only something real to put on the board.
"""

from __future__ import annotations

import random
from dataclasses import replace

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.data.cards import CardDef
from mtgfish.data.decks import parse_decklist
from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr100_game_concepts.cr103_setup import new_game
from mtgfish.rules.cr500_turn_structure.cr500_turn import TurnOptions, _turn_based_actions
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, TriggerCondition
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.cr700_additional_rules import keywords
from mtgfish.rules.cr700_additional_rules.cr701_keyword_actions import build as build_action
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import build as build_keyword
from mtgfish.rules.cr700_additional_rules.cr717_attractions import (
    ATTRACTION_DECK,
    JUNKYARD,
    attraction_deck,
    junkyard,
    open_attraction,
    roll_to_visit,
    set_up_attraction_deck,
)
from mtgfish.rules.kernel.enums import Phase, Step, Zone
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import PlayerFilter, PlayerScope, Value

#: Commander-legal Attractions, all differently named - enough for a legal
#: constructed Attraction deck (CR 717.2a).
ATTRACTIONS = (
    "Information Booth", "Clown Extruder", "Concession Stand", "Fortune Teller",
    "Kiddie Coaster", "Roller Coaster", "Spinny Ride", "Foam Weapons Kiosk",
    "Merry-Go-Round", "Haunted House", "Bumper Cars", "Trash Bin",
)

P0, P1 = PlayerId(0), PlayerId(1)
YOU = PlayerFilter(PlayerScope.YOU)


class FixedDie(random.Random):
    """An RNG whose every die comes up the same number."""

    def __init__(self, face: int) -> None:
        super().__init__(0)
        self.face = face

    def randint(self, a: int, b: int) -> int:  # noqa: D102 - a stub
        return self.face


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def lit(card_db, name: str, *numbers: int) -> CardDef:
    """A real Attraction card with these numbers lit up (CR 717.1)."""
    return replace(card_db.lookup(name), attraction_lights=tuple(numbers))


def gain_one() -> Effect:
    return Effect(EffectKind.GAIN_LIFE, players=YOU, amount=Value.of(1), text="gain 1 life")


def visit_ability(*effects: Effect) -> Ability:
    (ability,) = build_keyword(KeywordInstance("Visit", text="Visit", effects=effects))
    return ability


def place(board, card: CardDef, controller=P0):
    obj = board.game.create_object(card, controller, Zone.BATTLEFIELD)
    obj.summoning_sick = False
    board.refresh()
    return obj


def resolve_pending(board) -> None:
    board.settle()
    board.resolve_stack()


def run(board, effects, *, controller=P0):
    execute(Resolution(game=board.game, source=0, controller=controller), effects)
    board.refresh()


def rolls_this_turn(game, player=P0) -> int:
    return game.turn_history.get((int(EventKind.ROLLED_TO_VISIT), int(player), "count"), 0)


# ---------------------------------------------------------------------------
# Card data: lit numbers (CR 717.1)
# ---------------------------------------------------------------------------


def test_lit_numbers_are_read_from_scryfall_data():
    data = {"name": "X", "type_line": "Artifact - Attraction", "attraction_lights": [2, 6, 4]}
    assert CardDef.from_scryfall(data).attraction_lights == (2, 4, 6)


def test_a_card_without_lights_has_none(card_db):
    """The control: a card that is not an Attraction has nothing lit."""
    assert CardDef.from_scryfall({"name": "X"}).attraction_lights == ()
    assert card_db.lookup("Lightning Bolt").attraction_lights == ()


def test_the_snapshot_carries_an_attractions_lights(card_db):
    """The snapshot keeps Scryfall's ``attraction_lights``, so a real
    Attraction arrives with its numbers lit."""
    assert card_db.lookup("Information Booth").attraction_lights == (2, 6)


# ---------------------------------------------------------------------------
# Deck construction (CR 717.2, 717.2a)
# ---------------------------------------------------------------------------


def _decklist(attractions) -> str:
    text = "// Commander\n1 Kenrith, the Returned King\n// Deck\n99 Forest\n"
    return text + "// Attractions\n" + "".join(f"1 {name}\n" for name in attractions)


def test_an_attractions_section_is_the_attraction_deck(card_db):
    deck = parse_decklist(_decklist(ATTRACTIONS[:10]), card_db)
    assert [c.name for c in deck.attractions] == list(ATTRACTIONS[:10])
    # CR 717.2: not in the deck and not counted toward its size.
    assert deck.library_size == 99
    assert not [i for i in deck.issues if i.code.startswith("attraction")]
    assert "// Attractions" in deck.as_decklist()


def test_an_attraction_deck_needs_ten_cards(card_db):
    deck = parse_decklist(_decklist(ATTRACTIONS[:9]), card_db)
    assert "attraction-deck-size" in {i.code for i in deck.issues}


def test_an_attraction_deck_needs_different_names(card_db):
    deck = parse_decklist(_decklist(ATTRACTIONS[:9] + ATTRACTIONS[:1]), card_db)
    assert "attraction-duplicate" in {i.code for i in deck.issues}


def test_only_attractions_go_in_the_attraction_deck(card_db):
    deck = parse_decklist(_decklist(ATTRACTIONS[:10] + ("Sol Ring",)), card_db)
    assert "not-an-attraction" in {i.code for i in deck.issues}


def test_an_attraction_in_the_library_is_reported(card_db):
    text = "// Commander\n1 Kenrith, the Returned King\n// Deck\n98 Forest\n1 Information Booth\n"
    deck = parse_decklist(text, card_db)
    assert "attraction-in-deck" in {i.code for i in deck.issues}


# ---------------------------------------------------------------------------
# Setup (CR 717.2, 103.3a)
# ---------------------------------------------------------------------------


def test_the_attraction_deck_begins_in_the_command_zone(card_db):
    decks = [parse_decklist(_decklist(ATTRACTIONS[:10]), card_db, name=f"P{i}") for i in range(2)]
    game = new_game(decks, seed=3, randomize_turn_order=False)
    deck = attraction_deck(game, P0)
    assert sorted(game.printed_characteristics(o).name for o in deck) == sorted(ATTRACTIONS[:10])
    assert all(o.zone is Zone.COMMAND and o.command_pile == ATTRACTION_DECK for o in deck)
    # None of it is in the library or the opening hand.
    library_and_hand = game.player(P0).library + game.player(P0).hand
    assert not {o.id for o in deck} & set(library_and_hand)
    assert len(attraction_deck(game, P1)) == 10


def test_the_attraction_deck_is_shuffled(card_db):
    """CR 103.3a: across a handful of seeds the deck does not stay in list order."""
    orders = set()
    for seed in range(5):
        decks = [parse_decklist(_decklist(ATTRACTIONS[:10]), card_db)]
        game = new_game(decks, seed=seed, randomize_turn_order=False)
        orders.add(tuple(game.printed_characteristics(o).name for o in attraction_deck(game, P0)))
    assert len(orders) > 1


def test_a_deck_without_attractions_has_no_attraction_deck(board):
    """The control: the harness's decks have no Attractions section."""
    assert attraction_deck(board.game, P0) == []


# ---------------------------------------------------------------------------
# CR 701.51: open an Attraction
# ---------------------------------------------------------------------------


def test_opening_takes_the_top_card_then_the_next(board, card_db):
    cards = [card_db.lookup(n) for n in ATTRACTIONS[:3]]
    set_up_attraction_deck(board.game, P0, cards)
    run(board, build_action("Open an Attraction", amount=2))
    names = [board.game.printed_characteristics(o).name for o in board.game.permanents(P0)]
    assert names == list(ATTRACTIONS[:2])
    assert [board.game.printed_characteristics(o).name for o in attraction_deck(board.game, P0)] == [
        ATTRACTIONS[2]
    ]


def test_opening_an_attraction_triggers_whenever_you_open_one(board, card_db):
    """CR 701.51c: the opening is an event abilities can see."""
    board.scripts.add(
        "Grizzly Bears",
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.ATTRACTION_OPENED}), players=YOU
            ),
            gain_one(),
        ),
    )
    board.play("Grizzly Bears")
    set_up_attraction_deck(board.game, P0, [card_db.lookup(ATTRACTIONS[0])])
    life = board.game.player(P0).life
    open_attraction(board.game, P0)
    resolve_pending(board)
    assert board.game.player(P0).life == life + 1


def test_opening_with_an_empty_deck_does_nothing_and_triggers_nothing(board):
    """The control for the trigger above (CR 701.51a, 701.51c)."""
    board.scripts.add(
        "Grizzly Bears",
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.ATTRACTION_OPENED}), players=YOU
            ),
            gain_one(),
        ),
    )
    board.play("Grizzly Bears")
    life = board.game.player(P0).life
    assert open_attraction(board.game, P0) is None
    resolve_pending(board)
    assert board.game.player(P0).life == life


def test_an_opening_that_never_reaches_the_battlefield_triggers_nothing(board, card_db):
    """CR 701.51c: if the card is stopped from entering, nothing was opened.

    A card that cannot become a permanent stays where it is (CR 304.4); no
    real Attraction deck holds one, which is what makes it a clean way to
    stop the move without another card's replacement effect.
    """
    board.scripts.add(
        "Grizzly Bears",
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.ATTRACTION_OPENED}), players=YOU
            ),
            gain_one(),
        ),
    )
    board.play("Grizzly Bears")
    set_up_attraction_deck(board.game, P0, [card_db.lookup("Lightning Bolt")])
    life = board.game.player(P0).life
    assert open_attraction(board.game, P0) is None
    resolve_pending(board)
    assert board.game.player(P0).life == life


# ---------------------------------------------------------------------------
# CR 717.6: the junkyard
# ---------------------------------------------------------------------------


def test_a_destroyed_attraction_goes_to_the_junkyard(board, card_db):
    attraction = place(board, card_db.lookup(ATTRACTIONS[0]))
    actions.destroy(board.game, attraction)
    assert board.game.player(P0).graveyard == []
    (junked,) = junkyard(board.game, P0)
    assert junked.zone is Zone.COMMAND and junked.command_pile == JUNKYARD
    # CR 717.6a: a pile of its own, not the Attraction deck.
    assert attraction_deck(board.game, P0) == []


def test_an_attraction_bound_for_a_hand_goes_to_the_junkyard(board, card_db):
    attraction = place(board, card_db.lookup(ATTRACTIONS[0]))
    hand = list(board.game.player(P0).hand)
    actions.bounce(board.game, attraction)
    assert board.game.player(P0).hand == hand
    assert len(junkyard(board.game, P0)) == 1


def test_an_exiled_attraction_stays_in_exile(board, card_db):
    """CR 717.6 leaves exile alone."""
    attraction = place(board, card_db.lookup(ATTRACTIONS[0]))
    actions.exile(board.game, attraction)
    assert len(board.game.exile) == 1
    assert junkyard(board.game, P0) == []


def test_another_artifact_still_goes_to_the_graveyard(board):
    """The control: only a card with an Astrotorium back is redirected."""
    ring = board.play("Sol Ring")
    actions.destroy(board.game, ring)
    assert board.in_graveyard(0) == ["Sol Ring"]
    assert junkyard(board.game, P0) == []


# ---------------------------------------------------------------------------
# CR 701.52 and 702.159: rolling to visit, and visit abilities
# ---------------------------------------------------------------------------


def test_a_lit_attraction_is_visited_and_its_visit_ability_triggers(board, card_db):
    card = lit(card_db, ATTRACTIONS[0], 4)
    board.scripts.add(card.name, visit_ability(gain_one()))
    place(board, card)
    board.game.rng = FixedDie(4)
    life = board.game.player(P0).life
    assert roll_to_visit(board.game, P0) == 4
    resolve_pending(board)
    assert board.game.player(P0).life == life + 1


def test_an_unlit_number_visits_nothing(board, card_db):
    """The control: the same Attraction, a roll it does not have lit up."""
    card = lit(card_db, ATTRACTIONS[0], 4)
    board.scripts.add(card.name, visit_ability(gain_one()))
    place(board, card)
    board.game.rng = FixedDie(3)
    life = board.game.player(P0).life
    roll_to_visit(board.game, P0)
    resolve_pending(board)
    assert board.game.player(P0).life == life


def test_an_attraction_without_lights_is_never_visited(board, card_db):
    card = lit(card_db, ATTRACTIONS[0])
    board.scripts.add(card.name, visit_ability(gain_one()))
    place(board, card)
    life = board.game.player(P0).life
    for face in range(1, 7):
        board.game.rng = FixedDie(face)
        roll_to_visit(board.game, P0)
        resolve_pending(board)
    assert board.game.player(P0).life == life


def test_only_the_rollers_attractions_are_visited(board, card_db):
    """CR 701.52a: "Attractions you control" - an opponent's is not visited."""
    card = lit(card_db, ATTRACTIONS[0], 1, 2, 3, 4, 5, 6)
    board.scripts.add(card.name, visit_ability(gain_one()))
    place(board, card, controller=P1)
    life = board.game.player(P1).life
    roll_to_visit(board.game, P0)
    resolve_pending(board)
    assert board.game.player(P1).life == life


def test_the_roll_to_visit_action_expands_to_the_roll(board, card_db):
    card = lit(card_db, ATTRACTIONS[0], 1, 2, 3, 4, 5, 6)
    board.scripts.add(card.name, visit_ability(gain_one()))
    place(board, card)
    life = board.game.player(P0).life
    run(board, build_action("Roll to Visit Your Attractions"))
    resolve_pending(board)
    assert board.game.player(P0).life == life + 1


def test_visit_is_an_implemented_keyword():
    spec = keywords.lookup("Visit")
    assert spec is not None and spec.status is keywords.Status.IMPLEMENTED
    for action in ("Open an Attraction", "Roll to Visit Your Attractions"):
        assert keywords.lookup(action).status is keywords.Status.IMPLEMENTED


# ---------------------------------------------------------------------------
# CR 505.5: the precombat main phase roll
# ---------------------------------------------------------------------------


def _main_phase(board, phase: Phase) -> None:
    board.game.phase = phase
    board.game.step = Step.MAIN
    _turn_based_actions(board.game, Step.MAIN, TurnOptions())


def test_the_active_player_rolls_to_visit_in_their_precombat_main_phase(board, card_db):
    place(board, card_db.lookup(ATTRACTIONS[0]))
    _main_phase(board, Phase.PRECOMBAT_MAIN)
    assert rolls_this_turn(board.game) == 1


def test_no_roll_without_an_attraction(board):
    """The control: CR 505.5 asks for one or more Attractions."""
    board.play("Sol Ring")
    _main_phase(board, Phase.PRECOMBAT_MAIN)
    assert rolls_this_turn(board.game) == 0


def test_no_roll_in_the_postcombat_main_phase(board, card_db):
    """The other control: only the precombat main phase rolls."""
    place(board, card_db.lookup(ATTRACTIONS[0]))
    _main_phase(board, Phase.POSTCOMBAT_MAIN)
    assert rolls_this_turn(board.game) == 0


def test_an_opponents_attraction_does_not_make_you_roll(board, card_db):
    place(board, card_db.lookup(ATTRACTIONS[0]), controller=P1)
    _main_phase(board, Phase.PRECOMBAT_MAIN)
    assert rolls_this_turn(board.game) == 0


# ---------------------------------------------------------------------------
# The parser reaches all of it
# ---------------------------------------------------------------------------


def _parsed(card):
    from mtgfish.parser.compile import parse_face

    return parse_face(card).abilities


def test_a_visit_line_parses_to_a_visit_trigger(card_db):
    (ability,) = _parsed(card_db.lookup("Information Booth"))
    assert not ability.unparsed
    assert ability.trigger.event_kinds == frozenset({EventKind.ATTRACTION_VISITED})
    assert [e.kind for e in ability.effects] == [EffectKind.DRAW]


def test_open_two_attractions_parses_with_its_count(card_db):
    (ability,) = _parsed(card_db.lookup("Step Right Up"))
    (effect,) = ability.effects
    assert effect.kind is EffectKind.OPEN_ATTRACTION
    assert effect.amount.constant == 2


def test_roll_to_visit_and_attraction_triggers_parse(card_db):
    kinds = {
        e.kind for a in _parsed(card_db.lookup("Line Cutter")) for e in a.effects
    }
    assert EffectKind.ROLL_TO_VISIT in kinds
    watched = {
        k
        for name in ("The Most Dangerous Gamer", "Motion Sickness")
        for a in _parsed(card_db.lookup(name))
        if a.trigger is not None
        for k in a.trigger.event_kinds
    }
    assert {EventKind.ATTRACTION_OPENED, EventKind.ATTRACTION_VISITED} <= watched


def test_a_parsed_attraction_draws_when_visited(board, card_db):
    """End to end: oracle text, an Attraction deck, opening, and a visit."""
    from mtgfish.parser.compile import OracleAbilities

    board.game.ability_provider = OracleAbilities()
    set_up_attraction_deck(board.game, P0, [lit(card_db, "Information Booth", 5)])
    open_attraction(board.game, P0)
    board.game.rng = FixedDie(5)
    hand = len(board.game.player(P0).hand)
    roll_to_visit(board.game, P0)
    resolve_pending(board)
    assert len(board.game.player(P0).hand) == hand + 1
