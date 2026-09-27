"""MTG Arena's digital-only mechanics on a real board: perpetually, conjure, seek.

None of these is in the Comprehensive Rules; the rules they follow are Arena's
own (see ``cr700_additional_rules/digital_mechanics.py``). Each test pins one
thing the mechanic is defined by.
"""

from __future__ import annotations

from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.enums import CardType, Zone
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ObjectFilter, PlayerFilter, PlayerScope, Value

from mtgfish.rules.cr700_additional_rules.digital_mechanics import named

from harness import make_board

SELF = ObjectFilter(source_only=True)
REMEMBERED = ObjectFilter(remembered=True)


def _run(board, source, *effects, remembered=()):
    resolution = Resolution(
        game=board.game,
        source=source.id,
        controller=PlayerId(0),
        remembered=list(remembered),
    )
    execute(resolution, tuple(effects))
    return resolution


def _pump(power, toughness):
    return Effect(EffectKind.MODIFY_PT, amount=Value.of(power), amount2=Value.of(toughness))


def _perpetually(targets, *changes, keywords=()):
    return Effect(
        EffectKind.PERPETUALLY, targets=targets, children=changes, keywords=keywords
    )


# ---------------------------------------------------------------------------
# Perpetually
# ---------------------------------------------------------------------------


def test_a_perpetual_pump_follows_the_card_from_zone_to_zone(card_db):
    """The one thing perpetually adds to a continuous effect: it survives the
    zone changes that CR 400.7 would otherwise wipe it out with."""
    board = make_board(card_db)
    bears = board.play("Grizzly Bears")
    _run(board, bears, _perpetually(SELF, _pump(1, 1)))
    assert board.pt(bears) == (3, 3)

    in_hand = board.game.move_object(bears, Zone.HAND)
    assert board.pt(in_hand) == (3, 3)
    back = board.game.move_object(in_hand, Zone.BATTLEFIELD)
    assert board.pt(back) == (3, 3)


def test_a_perpetual_change_reaches_cards_in_hand_only_where_the_filter_says(card_db):
    board = make_board(card_db)
    source = board.play("Grizzly Bears")
    mine = board.hand("Grizzly Bears", 0)
    theirs = board.hand("Grizzly Bears", 1)
    from mtgfish.rules.kernel.query import ControllerRelation

    cards_in_your_hand = ObjectFilter(
        types_all=CardType.CREATURE,
        zones=frozenset({Zone.HAND}),
        owner=ControllerRelation.YOU,
    )
    _run(board, source, _perpetually(cards_in_your_hand, _pump(1, 1)))
    assert board.pt(mine) == (3, 3)
    assert board.pt(theirs) == (2, 2)
    assert board.pt(source) == (2, 2)


def test_perpetual_amounts_are_fixed_when_the_change_is_made(card_db):
    """"Gets +X/+X, where X is the number of creatures you control" does not
    grow when more creatures arrive later."""
    from mtgfish.rules.kernel.query import ValueKind

    board = make_board(card_db)
    bears = board.play("Grizzly Bears")
    creatures = Value(kind=ValueKind.COUNT, filter=ObjectFilter(types_all=CardType.CREATURE))
    _run(
        board,
        bears,
        _perpetually(
            SELF,
            Effect(EffectKind.MODIFY_PT, amount=creatures, amount2=creatures),
        ),
    )
    assert board.pt(bears) == (3, 3)
    board.play("Grizzly Bears")
    board.play("Grizzly Bears")
    assert board.pt(bears) == (3, 3)


def test_a_perpetually_granted_keyword_is_there_in_every_zone(card_db):
    board = make_board(card_db)
    bears = board.hand("Grizzly Bears")
    source = board.play("Llanowar Elves")
    flying = Ability(AbilityKind.STATIC, keyword="Flying", text="Flying")
    _run(
        board,
        source,
        _perpetually(
            REMEMBERED, Effect(EffectKind.GRANT_ABILITY, granted_abilities=(flying,))
        ),
        remembered=[bears.id],
    )
    assert "Flying" in board.keywords(bears)
    graveyard = board.game.move_object(bears, Zone.GRAVEYARD)
    assert "Flying" in board.keywords(graveyard)


def test_a_random_perpetual_pick_takes_one_matching_card(card_db):
    from mtgfish.rules.kernel.query import ControllerRelation

    board = make_board(card_db)
    source = board.play("Llanowar Elves")
    cards = [board.hand("Grizzly Bears") for _ in range(3)]
    spec = ObjectFilter(
        types_all=CardType.CREATURE,
        zones=frozenset({Zone.HAND}),
        owner=ControllerRelation.YOU,
        count=Value.of(1),
    )
    resolution = _run(
        board, source, _perpetually(spec, _pump(1, 1), keywords=("at random",))
    )
    pumped = [card for card in cards if board.pt(card) == (3, 3)]
    assert len(pumped) == 1
    assert resolution.remembered == [pumped[0].id]


# ---------------------------------------------------------------------------
# Conjure
# ---------------------------------------------------------------------------


def test_conjure_by_name_needs_the_catalogue_and_does_nothing_without_it(card_db):
    board = make_board(card_db)
    source = board.play("Llanowar Elves")
    before = len(board.game.player(0).hand)
    board.game.card_catalog = None
    _run(board, source, Effect(EffectKind.CONJURE, keywords=(named("Lightning Bolt"),), zone=Zone.HAND))
    assert len(board.game.player(0).hand) == before


def test_conjure_puts_a_new_card_owned_by_the_conjurer_into_the_named_zone(card_db):
    board = make_board(card_db)
    board.game.card_catalog = card_db
    source = board.play("Llanowar Elves")
    before = len(board.game.player(0).hand)
    resolution = _run(
        board,
        source,
        Effect(
            EffectKind.CONJURE,
            keywords=(named("Lightning Bolt"),),
            zone=Zone.HAND,
            amount=Value.of(2),
        ),
    )
    hand = board.game.player(0).hand
    assert len(hand) == before + 2
    made = [board.game.objects[i] for i in resolution.remembered]
    assert [board.chars(obj).name for obj in made] == ["Lightning Bolt"] * 2
    assert all(obj.owner == 0 and obj.id in hand for obj in made)


def test_a_card_conjured_onto_the_battlefield_enters_and_triggers(card_db):
    """It enters the battlefield like any card: ETB events fire, and a
    planeswalker brings its loyalty (CR 306.5b)."""
    board = make_board(card_db)
    board.game.card_catalog = card_db
    source = board.play("Llanowar Elves")
    entered = []
    board.game.observer = lambda game, event: (
        entered.append(event.object_id) if event.kind is EventKind.ENTERS_BATTLEFIELD else None
    )
    resolution = _run(
        board,
        source,
        Effect(
            EffectKind.CONJURE,
            zone=Zone.BATTLEFIELD,
            keywords=(named("Jace Beleren"), "tapped"),
        ),
    )
    (jace_id,) = resolution.remembered
    jace = board.game.objects[jace_id]
    assert jace.zone is Zone.BATTLEFIELD and jace.tapped
    assert jace.counters.get("loyalty") == 3
    assert jace_id in entered


def test_conjure_into_the_library_at_a_position(card_db):
    board = make_board(card_db)
    board.game.card_catalog = card_db
    source = board.play("Llanowar Elves")
    resolution = _run(
        board,
        source,
        Effect(
            EffectKind.CONJURE,
            keywords=(named("Lightning Bolt"),),
            zone=Zone.LIBRARY,
            amount2=Value.of(3),
        ),
    )
    library = board.game.player(0).library
    assert library.index(resolution.remembered[0]) == 2


def test_a_duplicate_keeps_perpetual_changes_and_a_same_named_card_does_not(card_db):
    board = make_board(card_db)
    board.game.card_catalog = card_db
    bears = board.play("Grizzly Bears")
    _run(board, bears, _perpetually(SELF, _pump(2, 2)))

    duplicate = _run(
        board,
        bears,
        Effect(EffectKind.CONJURE, targets=SELF, keywords=("duplicate",), zone=Zone.HAND),
    ).remembered
    named = _run(board, bears, Effect(EffectKind.CONJURE, targets=SELF, zone=Zone.HAND)).remembered
    assert board.pt(board.game.objects[duplicate[0]]) == (4, 4)
    assert board.pt(board.game.objects[named[0]]) == (2, 2)


# ---------------------------------------------------------------------------
# Seek
# ---------------------------------------------------------------------------


def test_seek_takes_a_matching_card_from_the_library_without_shuffling(card_db):
    board = make_board(card_db)
    game = board.game
    source = board.play("Llanowar Elves")
    bolt = game.create_object(card_db.lookup("Lightning Bolt"), PlayerId(0), Zone.LIBRARY)
    order_before = [i for i in game.player(0).library if i != bolt.id]
    instants = ObjectFilter(types_all=CardType.INSTANT, zones=frozenset({Zone.LIBRARY}))
    resolution = _run(
        board,
        source,
        Effect(EffectKind.SEEK, targets=instants, amount=Value.of(1), players=PlayerFilter(PlayerScope.YOU)),
    )
    (found,) = resolution.remembered
    assert game.objects[found].zone is Zone.HAND
    assert board.chars(game.objects[found]).name == "Lightning Bolt"
    assert game.player(0).library == order_before


def test_seek_with_nothing_to_find_remembers_nothing(card_db):
    board = make_board(card_db)
    source = board.play("Llanowar Elves")
    instants = ObjectFilter(types_all=CardType.INSTANT, zones=frozenset({Zone.LIBRARY}))
    resolution = _run(
        board,
        source,
        Effect(EffectKind.SEEK, targets=instants, amount=Value.of(1)),
        remembered=[source.id],
    )
    assert resolution.remembered == []
