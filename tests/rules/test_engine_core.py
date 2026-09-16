"""Engine core: objects, zones, players, and determinism.

The determinism tests are the load-bearing ones. Replays are not stored - they
are reconstructed by simulating a seed again - so if the same seed can produce
two different games, every statistic the UI shows is disconnected from the
replay it links to.
"""

from __future__ import annotations

import pytest

from mtgfish.data.decks import parse_decklist
from mtgfish.rules.enums import LossReason, Zone
from mtgfish.rules.gameobject import ObjectKind
from mtgfish.rules.player import COMMANDER_DAMAGE_THRESHOLD
from mtgfish.rules.setup import new_game, take_mulligans


def build_deck(card_db, commander="Kenrith, the Returned King", name="Test"):
    return parse_decklist(
        f"// Commander\n1 {commander}\n// Deck\n40 Forest\n59 Mountain\n", card_db, name=name
    )


@pytest.fixture
def game(card_db):
    decks = [build_deck(card_db, name=f"Deck {i}") for i in range(4)]
    return new_game(decks, seed=1234)


# ---------------------------------------------------------------------------
# Setup (CR 103, 903.6, 903.7)
# ---------------------------------------------------------------------------


def test_four_players_start_at_forty_life(game):
    assert len(game.players) == 4
    assert all(p.life == 40 for p in game.players)


def test_commander_starts_in_the_command_zone(game):
    """CR 903.6, and the reason a Commander deck is 99 + 1."""
    assert len(game.command) == 4
    for player in game.players:
        assert len(player.commanders) == 1
        commander = game.objects[player.commanders[0]]
        assert commander.zone is Zone.COMMAND
        assert commander.is_commander


def test_opening_hands_and_library_sizes(game):
    for player in game.players:
        assert player.hand_size == 7
        assert player.library_size == 92
        assert player.hand_size + player.library_size == 99


def test_turn_order_covers_every_player(game):
    assert sorted(game.turn_order) == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_same_seed_produces_an_identical_game(card_db):
    def run():
        decks = [build_deck(card_db, name=f"Deck {i}") for i in range(4)]
        g = new_game(decks, seed=99)
        return g.log.digest(), list(g.turn_order), [list(p.hand) for p in g.players]

    assert run() == run()


def test_different_seeds_produce_different_games(card_db):
    def run(seed):
        decks = [build_deck(card_db, name=f"Deck {i}") for i in range(4)]
        return new_game(decks, seed=seed).log.digest()

    assert run(1) != run(2)


def test_logging_does_not_change_the_digest(card_db):
    """The hash must be maintained whether or not full logging is on.

    Otherwise a replay - which runs with logging enabled - could not be checked
    against the run that produced it.
    """

    def run(enabled):
        decks = [build_deck(card_db, name=f"Deck {i}") for i in range(4)]
        return new_game(decks, seed=7, log_enabled=enabled).log.digest()

    assert run(True) == run(False)


# ---------------------------------------------------------------------------
# Zone changes (CR 400.7)
# ---------------------------------------------------------------------------


def test_zone_change_creates_a_new_object(game):
    """CR 400.7: the object that arrives is not the object that left."""
    player = game.players[0]
    old = game.objects[player.hand[0]]
    old_id = old.id

    new = game.move_object(old, Zone.BATTLEFIELD)

    assert new.id != old_id
    assert new.zone is Zone.BATTLEFIELD
    assert new.id in game.battlefield
    assert old_id not in player.hand


def test_zone_change_forgets_counters_and_damage(game):
    """The whole point of CR 400.7, and what makes blink effects work."""
    player = game.players[0]
    obj = game.move_object(game.objects[player.hand[0]], Zone.BATTLEFIELD)
    obj.add_counters("+1/+1", 3)
    obj.damage = 2

    returned = game.move_object(obj, Zone.HAND, to_player=player.id)
    battlefield_again = game.move_object(returned, Zone.BATTLEFIELD)

    assert battlefield_again.counters == {}
    assert battlefield_again.damage == 0


def test_entering_the_battlefield_grants_summoning_sickness(game):
    obj = game.move_object(game.objects[game.players[0].hand[0]], Zone.BATTLEFIELD)
    assert obj.summoning_sick
    assert obj.entered_battlefield_turn == game.turn


def test_token_leaving_the_battlefield_ceases_to_exist(game):
    """CR 111.7: it leaves - so triggers see it - but never lands anywhere."""
    card = game.objects[game.players[0].hand[0]].card
    token = game.create_object(card, game.players[0].id, Zone.BATTLEFIELD, kind=ObjectKind.TOKEN)

    result = game.move_object(token, Zone.GRAVEYARD)

    assert result.id == token.id  # No new object was made.
    assert token.id not in game.battlefield
    assert token.id not in game.players[0].graveyard


def test_attachments_are_severed_on_zone_change(game):
    player = game.players[0]
    host = game.move_object(game.objects[player.hand[0]], Zone.BATTLEFIELD)
    aura = game.move_object(game.objects[player.hand[0]], Zone.BATTLEFIELD)
    aura.attached_to = host.id
    host.attachments.append(aura.id)

    game.move_object(host, Zone.GRAVEYARD)

    assert aura.attached_to == 0  # NO_OBJECT
    assert not host.attachments


# ---------------------------------------------------------------------------
# Drawing (CR 121, 704.5b)
# ---------------------------------------------------------------------------


def test_drawing_moves_cards_from_library_to_hand(game):
    player = game.players[0]
    before = player.library_size
    drawn = game.draw(player.id, 3)
    assert len(drawn) == 3
    assert player.library_size == before - 3
    assert player.hand_size == 10


def test_drawing_from_an_empty_library_does_not_lose_immediately(game):
    """CR 704.5b: the loss is a state-based action, checked later.

    The delay is observable - a player can win before it is checked - so the
    engine must not shortcut it.
    """
    player = game.players[0]
    player.library.clear()

    game.draw(player.id, 1)

    assert player.attempted_draw_from_empty_library
    assert not player.has_lost


# ---------------------------------------------------------------------------
# Players leaving (CR 800.4)
# ---------------------------------------------------------------------------


def test_losing_removes_the_players_objects(game):
    """CR 800.4a: everything they own leaves the game with them."""
    victim = game.players[1]
    owned_before = sum(1 for o in game.objects.values() if o.owner == victim.id)
    assert owned_before > 0

    game.player_loses(victim.id, LossReason.LIFE)

    assert victim.has_lost
    assert victim.loss_reason is LossReason.LIFE
    assert not any(o.owner == victim.id for o in game.objects.values())


def test_a_departed_player_is_skipped_but_keeps_their_seat(game):
    original = list(game.turn_order)
    game.player_loses(game.turn_order[1], LossReason.CONCEDE)

    assert game.turn_order == original  # Seats never shuffle.
    assert len(game.turn_order_from(game.turn_order[0])) == 3


def test_game_ends_when_one_player_remains(game):
    for player_id in game.turn_order[1:]:
        game.player_loses(player_id, LossReason.LIFE)
    assert game.game_over
    assert game.winners == (game.turn_order[0],)


def test_cannot_lose_prevents_the_loss(game):
    player = game.players[0]
    player.cannot_lose = True
    game.player_loses(player.id, LossReason.LIFE)
    assert not player.has_lost


# ---------------------------------------------------------------------------
# APNAP (CR 101.4)
# ---------------------------------------------------------------------------


def test_apnap_starts_with_the_active_player(game):
    order = game.apnap_order()
    assert order[0] == game.active_player
    assert len(order) == 4


def test_apnap_skips_departed_players(game):
    game.player_loses(game.turn_order[2], LossReason.LIFE)
    order = game.apnap_order()
    assert game.turn_order[2] not in order
    assert len(order) == 3


# ---------------------------------------------------------------------------
# Commander rules (CR 903.8, 903.10)
# ---------------------------------------------------------------------------


def test_commander_tax_is_two_per_previous_cast(game):
    player = game.players[0]
    commander = player.commanders[0]

    assert player.commander_tax(commander) == 0
    player.record_commander_cast(commander)
    assert player.commander_tax(commander) == 2
    player.record_commander_cast(commander)
    assert player.commander_tax(commander) == 4


def test_commander_damage_is_tracked_per_commander(game):
    """CR 903.10: 21 from a *single* commander, not 21 in total."""
    victim = game.players[0]
    a, b = game.players[1].commanders[0], game.players[2].commanders[0]

    victim.take_commander_damage(a, 20)
    victim.take_commander_damage(b, 20)
    assert victim.lethal_commander_damage == 0  # NO_OBJECT: neither reached 21

    victim.take_commander_damage(a, 1)
    assert victim.commander_damage[a] == COMMANDER_DAMAGE_THRESHOLD
    assert victim.lethal_commander_damage == a


# ---------------------------------------------------------------------------
# Mulligans (CR 103.5)
# ---------------------------------------------------------------------------


def test_london_mulligan_keeps_seven_then_bottoms(card_db):
    """The hand is drawn at seven every time; cards go to the bottom on keep."""
    decks = [build_deck(card_db, name=f"Deck {i}") for i in range(4)]
    g = new_game(decks, seed=5)

    calls: dict[int, int] = {}

    def mulligan_twice(game, player_id, taken):
        calls[player_id] = taken
        return taken < 2

    counts = take_mulligans(g, mulligan_twice)

    assert all(count == 2 for count in counts.values())
    for player in g.players:
        assert player.hand_size == 5
        assert player.hand_size + player.library_size == 99


def test_no_mulligan_leaves_seven(card_db):
    decks = [build_deck(card_db, name=f"Deck {i}") for i in range(4)]
    g = new_game(decks, seed=5)
    counts = take_mulligans(g, lambda game, pid, taken: False)
    assert all(c == 0 for c in counts.values())
    assert all(p.hand_size == 7 for p in g.players)
