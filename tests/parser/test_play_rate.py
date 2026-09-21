"""Coverage measured over the cards people actually play.

The pool percentage answers the wrong question: 31,830 commander-legal cards
are mostly cards nobody has ever put in a deck, so a single number weights a
format staple and a draft common equally. These tests guard the *weighted*
number and pin the specific staples that were failing, because a regression on
Sol Ring matters and a regression on an unplayed uncommon does not.
"""

from __future__ import annotations

import pytest

from mtgfish.parser import parse_card
from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.costs import parse_cost
from mtgfish.parser.tokens import Stream
from mtgfish.tools.play_rate_report import coverage


@pytest.fixture(autouse=True)
def _registry(card_db):
    card_db.registry()


def _reads(text):
    stream = Stream.of(text)
    return parse_effects(stream) is not None and stream.done


# ---------------------------------------------------------------------------
# The weighted number
# ---------------------------------------------------------------------------


def test_play_rate_ordering_puts_staples_first(card_db):
    """Sol Ring is the most played card in the format; if it is not near the
    front, the ordering is broken and every number below it is meaningless."""
    top = [card.name for card in card_db.iter_by_play_rate(limit=25)]
    assert "Sol Ring" in top
    assert "Command Tower" in top


def test_the_most_played_cards_are_read_better_than_the_pool(card_db):
    """A floor, not a target.

    Deliberately well below the current figure: this exists to catch a
    collapse, not to be adjusted upward every time the grammar improves.
    """
    measured = dict((depth, read / total) for depth, read, total in coverage(card_db))
    assert measured[100] >= 0.60, measured
    assert measured[1000] >= 0.45, measured


# ---------------------------------------------------------------------------
# Specific staples that were failing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Sol Ring",
        "Command Tower",
        "Arcane Signet",
        "Reliquary Tower",
        "Swords to Plowshares",
        "Path to Exile",
        "Beast Within",
        "Rhystic Study",
        "Lightning Greaves",
        "Swiftfoot Boots",
    ],
)
def test_a_format_staple_is_read_completely(card_db, name):
    card = card_db.lookup(name)
    if card is None:
        pytest.skip(f"{name} is not in this pool")
    parsed = parse_card(card)
    assert parsed.fully_parsed, [f.reason for f in parsed.failures]


def test_command_tower_reads_its_only_line(card_db):
    """The second most played card in the format, failing on a phrase about
    commander colour identity."""
    assert _reads("Add one mana of any color in your commander's color identity.")


def test_a_shock_land_reads(card_db):
    """"As this land enters, you may pay 2 life. If you don't, it enters
    tapped." - a choice and its consequence in one sentence."""
    assert _reads(
        "As this land enters, you may pay 2 life. If you don't, it enters tapped."
    )


def test_a_check_land_reads(card_db):
    """Each alternative carries its own determiner: "a Swamp or *a* Mountain"."""
    assert _reads("This land enters tapped unless you control a Swamp or a Mountain.")


def test_a_battlebond_land_reads(card_db):
    """A question about the table rather than the board."""
    assert _reads("This land enters tapped unless you have two or more opponents.")


def test_a_token_can_be_made_by_someone_else(card_db):
    """"Its controller creates a 3/3 green Beast" hands the token to the
    victim. Reading it as your own token turns a drawback into an upside."""
    from mtgfish.rules.effects import EffectKind
    from mtgfish.rules.query import PlayerScope

    stream = Stream.of(
        "Destroy target permanent. Its controller creates a 3/3 green Beast creature token."
    )
    effects = parse_effects(stream)
    assert effects is not None and stream.done

    token = next(
        node
        for effect in effects
        for node in effect.walk()
        if node.kind is EffectKind.CREATE_TOKEN
    )
    assert token.players is not None
    assert token.players.scope is PlayerScope.CONTROLLER_OF


def test_no_maximum_hand_size_is_a_permission(card_db):
    """Reliquary Tower and Thought Vessel are top-20 cards that did nothing."""
    from mtgfish.rules.effects import EffectKind

    stream = Stream.of("You have no maximum hand size.")
    effects = parse_effects(stream)
    assert effects is not None and stream.done
    assert effects[0].kind is EffectKind.PERMISSION


def test_reliquary_tower_actually_skips_the_cleanup_discard(card_db, tmp_path):
    """The parse is only half of it - the cleanup step has to honour it."""
    from mtgfish.parser.verdicts import VerdictStore
    from mtgfish.rules.cr500_turn import _discard_to_hand_size
    from mtgfish.ui.sandbox import Sandbox

    box = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    for _ in range(10):
        box.put("Grizzly Bears", "hand", 0)
    assert len(box.game.player(0).hand) == 10

    box.put("Reliquary Tower", "battlefield", 0)
    box.game.invalidate_characteristics()
    _discard_to_hand_size(box.game)
    assert len(box.game.player(0).hand) == 10, "no maximum hand size"


def test_without_the_tower_the_discard_still_happens(card_db, tmp_path):
    from mtgfish.parser.verdicts import VerdictStore
    from mtgfish.rules.cr500_turn import _discard_to_hand_size
    from mtgfish.ui.sandbox import Sandbox

    box = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    # The bench suspends the maximum hand size, because a hand stocked for a
    # test is not a hand that was drawn. This test is about the rule itself, so
    # it asks for it back.
    box.set_rule("no_maximum_hand_size", False)
    for _ in range(10):
        box.put("Grizzly Bears", "hand", 0)
    _discard_to_hand_size(box.game)
    assert len(box.game.player(0).hand) == 7


# ---------------------------------------------------------------------------
# Clause selection
# ---------------------------------------------------------------------------


def test_a_specific_clause_beats_a_general_one_that_starts_the_same(card_db):
    """The disease behind a whole class of failures.

    "You may cast spells as though they had flash" begins exactly like "You
    may <do something>". Under first-match-wins the general clause ate the
    prefix and stranded the rest. Selection is now longest-match.
    """
    assert _reads("You may cast spells as though they had flash.")
    assert _reads("You may play an additional land on each of your turns.")
    assert _reads("You may draw a card.")


def test_mana_lists_read_like_type_lists(card_db):
    assert _reads("Add {W}, {U}, or {B}.")
    assert _reads("Add {R} or {G}.")
    assert _reads("Add {C}{C}.")


@pytest.mark.parametrize(
    "text",
    [
        "this land deals 2 damage to any target.",
        "this artifact deals 2 damage to any target.",
        "this enchantment deals 3 damage to target creature.",
        "this creature deals 2 damage to any target.",
    ],
)
def test_a_card_type_is_not_a_reason_to_stop_reading(card_db, text):
    """Three modules each kept their own list of nouns that may follow
    "this", and they drifted. There is one list now."""
    assert _reads(text)


def test_exert_is_charged_as_a_cost(card_db):
    cost, reason = parse_cost("{R}, {T}, Exert this land")
    assert cost is not None, reason
