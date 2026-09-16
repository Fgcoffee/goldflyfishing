"""Three capabilities the engine had and the grammar could not ask for.

The project has hit this pattern repeatedly: loyalty payment, the enters-tapped
replacement, the mana-restriction protocol, layer 7a for characteristic-defining
abilities - all built, all tested, none reachable from oracle text. A card fails
wholesale and it looks like a missing feature, when the feature is there and
nothing is wired to it.

These three were the same shape, and each one made a deck faster or safer than
the cards allow:

* a searched land arriving untapped, because the executor never looked at how
  the effect said it arrives;
* a Fog stopping a burn spell, because the shield watched both damage events
  whatever the card said;
* a one-sided shield protecting the whole table, because a shield with neither
  subject nor players narrows nothing.

Built from hand-made cards rather than the card database, so they run on a
fresh clone: what is under test is the executor, not any particular printing.
"""

from __future__ import annotations

import pytest

from mtgfish.data.cards import CardDef, FaceDef, Layout
from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.tokens import Stream
from mtgfish.rules.effects import Effect, EffectKind
from mtgfish.rules.enums import Color, Zone
from mtgfish.rules.events import EventKind
from mtgfish.rules.game import Game
from mtgfish.rules.ids import PlayerId
from mtgfish.rules.mana import ManaCost
from mtgfish.rules.player import Player
from mtgfish.rules.query import PlayerFilter, PlayerScope, Value
from mtgfish.rules.resolve import Resolution, _do_prevent_damage, _do_search_library
from mtgfish.rules.typeline import TypeLine


def a_land(name: str = "Forest") -> CardDef:
    """A basic land, printed by hand.

    The card database is a snapshot of 30,000 real cards and these tests need
    one. Building it here keeps them running before the snapshot exists.
    """
    face = FaceDef(
        name=name,
        mana_cost=ManaCost.parse(""),
        has_mana_cost=False,
        type_line=TypeLine.parse(f"Basic Land - {name}"),
        oracle_text="",
    )
    return CardDef(
        oracle_id=name.lower(),
        name=name,
        layout=Layout.NORMAL,
        faces=(face,),
        mana_value=0,
        color_identity=Color.NONE,
        commander_legal=True,
    )


@pytest.fixture
def game() -> Game:
    """One player, an empty board, and nothing else."""
    game = Game()
    game.players.append(Player(PlayerId(0)))
    game.turn_order.append(PlayerId(0))
    return game


def resolution(game: Game) -> Resolution:
    return Resolution(game=game, source=0, controller=PlayerId(0))


def only_effect(text: str) -> Effect:
    """The single effect a sentence parses to, so the test runs the parser's
    real output rather than a hand-built effect that agrees with it."""
    stream = Stream.of(text)
    effects = parse_effects(stream)
    assert effects is not None and stream.done, f"unread: {stream.remaining()}"
    assert len(effects) == 1
    return effects[0]


# ---------------------------------------------------------------------------
# A searched land arrives the way the card says it arrives
# ---------------------------------------------------------------------------


def _search_and_get_land(game: Game, effect: Effect):
    game.create_object(a_land(), PlayerId(0), Zone.LIBRARY)
    _do_search_library(resolution(game), effect)
    landed = [obj for obj in game.objects.values() if obj.zone is Zone.BATTLEFIELD]
    assert len(landed) == 1, "the search should have found exactly one land"
    return landed[0]


def test_a_searched_land_enters_tapped_when_the_card_says_tapped(game):
    """Rampant Growth, Cultivate, Farseek and most of the format's ramp."""
    effect = only_effect(
        "Search your library for a basic land card, put it onto the battlefield "
        "tapped, then shuffle."
    )
    assert _search_and_get_land(game, effect).tapped


def test_a_searched_land_that_does_not_say_tapped_enters_untapped(game):
    """The other half, so the fix cannot be "tap everything a search finds"."""
    effect = only_effect(
        "Search your library for a basic land card, put it onto the battlefield, "
        "then shuffle."
    )
    assert not _search_and_get_land(game, effect).tapped


def test_tapped_applies_only_to_the_battlefield(game):
    """A card put into a hand cannot be tapped, and asking would be a bug
    waiting for the first tutor written in an order nobody expected."""
    effect = Effect(
        EffectKind.SEARCH_LIBRARY,
        players=PlayerFilter(PlayerScope.YOU),
        zone=Zone.HAND,
        amount=Value.of(1),
        keywords=("tapped",),
    )
    game.create_object(a_land(), PlayerId(0), Zone.LIBRARY)
    _do_search_library(resolution(game), effect)
    in_hand = [obj for obj in game.objects.values() if obj.zone is Zone.HAND]
    assert in_hand and not in_hand[0].tapped


# ---------------------------------------------------------------------------
# A prevention shield is as narrow as the card that made it
# ---------------------------------------------------------------------------


def _shield(game: Game, text: str):
    _do_prevent_damage(resolution(game), only_effect(text))
    assert len(game.replacement_effects) == 1
    return game.replacement_effects[0]


def test_a_fog_watches_combat_damage_only(game):
    """"Prevent all combat damage that would be dealt this turn." A shield
    registered against both damage events also blanks a Lightning Bolt."""
    shield = _shield(game, "Prevent all combat damage that would be dealt this turn.")
    assert shield.event_kinds == frozenset({EventKind.COMBAT_DAMAGE_DEALT})


def test_an_unqualified_shield_still_watches_both(game):
    shield = _shield(
        game, "Prevent all damage that would be dealt to target creature this turn."
    )
    assert EventKind.DAMAGE_DEALT in shield.event_kinds
    assert EventKind.COMBAT_DAMAGE_DEALT in shield.event_kinds


def test_a_shield_over_a_player_is_not_a_shield_over_everyone(game):
    """With no subject and no players, ``_applies`` narrows nothing at all, so
    "damage that would be dealt to you" protected the opponents too."""
    shield = _shield(game, "Prevent all damage that would be dealt to you this turn.")
    assert shield.players is not None


# ---------------------------------------------------------------------------
# A conditional static ability applies at all, and only when it should
# ---------------------------------------------------------------------------


def a_permanent(name: str, text: str, type_line: str, power=None, toughness=None):
    face = FaceDef(
        name=name,
        mana_cost=ManaCost.parse("{2}"),
        has_mana_cost=True,
        type_line=TypeLine.parse(type_line),
        oracle_text=text,
        power=power,
        toughness=toughness,
    )
    return CardDef(
        oracle_id=name.lower(),
        name=name,
        layout=Layout.NORMAL,
        faces=(face,),
        mana_value=2,
        color_identity=Color.NONE,
        commander_legal=True,
    )


@pytest.fixture
def two_players() -> Game:
    """A game with two players and the parser supplying abilities."""
    from mtgfish.parser.compile import OracleAbilities

    game = Game()
    game.players.extend([Player(PlayerId(0)), Player(PlayerId(1))])
    game.turn_order.extend([PlayerId(0), PlayerId(1)])
    game.ability_provider = OracleAbilities()
    game.active_player = PlayerId(0)
    return game


def test_an_anthem_gated_on_your_turn_applies_on_your_turn(two_players):
    """The layer system gathers continuous effects by walking SEQUENCEs and
    never looks inside a CONDITIONAL, so an anthem wrapped in one was dropped
    entirely - "as long as" and "during your turn" alike did nothing at all.

    Lifting the condition onto ``Ability.static_condition`` puts it where
    ``layers._static_condition_holds`` already checks it.
    """
    game = two_players
    game.create_object(
        a_permanent(
            "Anthem", "Creatures you control get +1/+1 during your turn.", "Enchantment"
        ),
        PlayerId(0),
        Zone.BATTLEFIELD,
    )
    bear = game.create_object(
        a_permanent("Bear", "", "Creature - Bear", "2", "2"),
        PlayerId(0),
        Zone.BATTLEFIELD,
    )
    game.invalidate_characteristics()

    chars = game.characteristics(bear)
    assert (chars.power, chars.toughness) == (3, 3)


def test_the_same_anthem_stops_applying_off_your_turn(two_players):
    """The other half. A condition that is never checked and one that is
    always true look identical from a single board."""
    game = two_players
    game.create_object(
        a_permanent(
            "Anthem", "Creatures you control get +1/+1 during your turn.", "Enchantment"
        ),
        PlayerId(0),
        Zone.BATTLEFIELD,
    )
    bear = game.create_object(
        a_permanent("Bear", "", "Creature - Bear", "2", "2"),
        PlayerId(0),
        Zone.BATTLEFIELD,
    )
    game.active_player = PlayerId(1)
    game.invalidate_characteristics()

    chars = game.characteristics(bear)
    assert (chars.power, chars.toughness) == (2, 2)


def test_an_unconditional_anthem_is_untouched(two_players):
    game = two_players
    game.create_object(
        a_permanent("Anthem", "Creatures you control get +1/+1.", "Enchantment"),
        PlayerId(0),
        Zone.BATTLEFIELD,
    )
    bear = game.create_object(
        a_permanent("Bear", "", "Creature - Bear", "2", "2"),
        PlayerId(0),
        Zone.BATTLEFIELD,
    )
    game.active_player = PlayerId(1)
    game.invalidate_characteristics()

    chars = game.characteristics(bear)
    assert (chars.power, chars.toughness) == (3, 3)
