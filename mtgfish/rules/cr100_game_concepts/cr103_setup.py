"""Starting a game (CR 103, 903.6, 903.7).

The setup sequence is fixed: determine turn order, everyone shuffles, everyone
draws seven, then mulligans are taken. Commanders begin in the command zone
rather than the library (CR 903.6), which is why a Commander deck is 99 cards
plus one. An Attraction deck, where a player has one, begins there too
(CR 717.2) and is shuffled along with the library (CR 103.3a).

CR 903.2 settles what kind of game this is before any of that: a Free-for-All
(CR 806) with the attack multiple players option and *without* the limited
range of influence option. Both of those are honoured by omission - every
player is every other player's opponent, and nothing anywhere narrows who a
spell may be aimed at - so the only part that needs code is CR 806.3, seating
the players at random.

Everything random here draws from the game's seeded RNG. That includes the
turn-order roll, so a replay reproduces even who went first.
"""

from __future__ import annotations

import random
from typing import Callable, Sequence

from ..cr700_additional_rules.cr717_attractions import (
    set_up_attraction_deck,
    shuffle_attraction_deck,
)
from ..kernel.enums import Phase, Step, Zone
from ..kernel.game import AbilityProvider, Game
from ..kernel.ids import PlayerId
from ..kernel.log import GameLog
from .player import COMMANDER_STARTING_LIFE, STARTING_HAND_SIZE, Player

#: A decision function: given the game, the player, and how many mulligans they
#: have already taken, return True to mulligan again.
MulliganPolicy = Callable[["Game", PlayerId, int], bool]


def new_game(
    decks: Sequence,
    *,
    seed: int = 0,
    log_enabled: bool = False,
    ability_provider: AbilityProvider | None = None,
    randomize_turn_order: bool = True,
) -> Game:
    """Build a game from decks, up to but not including the first turn.

    Libraries are shuffled and opening hands drawn; mulligans are a separate
    step so the caller can supply a policy.
    """
    game = Game(rng=random.Random(seed), log=GameLog(enabled=log_enabled))
    if ability_provider is not None:
        game.ability_provider = ability_provider
    # CR 727.2: every card in the restarted game is in the new one, so the
    # decks have to survive the game that used them.
    game.source_decks = list(decks)

    for index, deck in enumerate(decks):
        player = Player(id=PlayerId(index), name=deck.name or f"Player {index + 1}")
        game.players.append(player)

    for index, deck in enumerate(decks):
        player_id = PlayerId(index)
        player = game.players[player_id]

        for card in deck.library_cards():
            game.create_object(card, player_id, Zone.LIBRARY)

        # CR 903.6: commanders start in the command zone, face up.
        commander_ids = []
        for commander in deck.commanders:
            obj = game.create_object(commander, player_id, Zone.COMMAND)
            obj.is_commander = True
            commander_ids.append(obj.id)
        player.commanders = tuple(commander_ids)
        # CR 903.11a: what the player started with, commanders included, is
        # what a card brought in later may not share a name with.
        player.starting_deck_names = frozenset(
            card.name for card in list(deck.library_cards()) + list(deck.commanders)
        )

        # CR 400.11a: a sideboard stays outside the game, as card definitions
        # rather than objects (CR 400.11c).
        player.outside_game.extend(getattr(deck, "sideboard", ()))
        # CR 103.2b: before the game begins, a player may reveal one companion
        # they own from outside the game. It stays outside until the special
        # action of CR 116.2g brings it in.
        companion = getattr(deck, "companion", None)
        if companion is not None:
            from ..cr400_zones.cr400_outside_game import reveal_companion

            player.outside_game.append(companion)
            reveal_companion(game, player_id, companion)

        # CR 717.2: a player playing with Attractions begins with an
        # Attraction deck in the command zone.
        set_up_attraction_deck(game, player_id, getattr(deck, "attractions", ()))

    # CR 103.1, and CR 806.3 for the seating it implies: turn order is
    # determined at random, then fixed for the game.
    order = [PlayerId(i) for i in range(len(game.players))]
    if randomize_turn_order:
        game.rng.shuffle(order)
    game.turn_order = order
    game.active_player = order[0]
    game.priority_player = order[0]
    game.turn = 0
    game.phase = Phase.BEGINNING
    game.step = Step.UNTAP

    for player_id in order:
        game.shuffle_library(player_id)
        # CR 103.3a: and each supplementary deck is shuffled with it.
        shuffle_attraction_deck(game, player_id)

    # CR 903.7: once the starting player is known, each player sets their life
    # total to 40 and draws seven. Setting it here rather than leaving it to
    # the Player default makes it a step of the setup sequence, in the order
    # the rule gives it - and 40 is a fact about Commander, not about players.
    for player_id in order:
        game.players[player_id].life = COMMANDER_STARTING_LIFE

    for player_id in order:
        game.draw(player_id, STARTING_HAND_SIZE)

    game.log.record(
        game,
        "Turn order: " + ", ".join(game.players[p].name for p in order),
        kind="setup",
    )
    return game


def take_mulligans(
    game: Game,
    policy: MulliganPolicy,
    *,
    maximum: int = STARTING_HAND_SIZE,
) -> dict[PlayerId, int]:
    """Run the London mulligan (CR 103.5).

    Each mulligan shuffles the hand back and draws a fresh seven; after keeping,
    the player puts one card on the bottom of their library for each mulligan
    taken. So the *quality* of the choice improves with each mulligan while the
    size of the kept hand shrinks - which is the whole point of the London
    rule, and why a naive "draw fewer cards" implementation plays very
    differently.

    Returns how many mulligans each player took, for the statistics layer.
    """
    counts: dict[PlayerId, int] = {}

    for player_id in game.turn_order:
        player = game.players[player_id]
        taken = 0

        while taken < maximum and policy(game, player_id, taken):
            for object_id in list(player.hand):
                game.move_object(game.objects[object_id], Zone.LIBRARY, to_player=player_id)
            game.shuffle_library(player_id)
            game.draw(player_id, STARTING_HAND_SIZE)
            taken += 1

        counts[player_id] = taken
        if taken:
            _bottom_cards(game, player_id, taken)
            game.log.record(
                game,
                f"{player.name} mulligans to {STARTING_HAND_SIZE - taken}",
                kind="mulligan",
                player=player_id,
            )

    return counts


def _bottom_cards(game: Game, player_id: PlayerId, count: int) -> None:
    """Put ``count`` cards from hand on the bottom of the library (CR 103.5).

    Chooses the last cards in hand. Which cards to bottom is a real decision
    that the AI will make; until it does, this is deterministic rather than
    random, so replays stay exact.
    """
    player = game.players[player_id]
    for _ in range(min(count, len(player.hand))):
        object_id = player.hand[-1]
        game.move_object(game.objects[object_id], Zone.LIBRARY, to_player=player_id)


def keep_seven(game: Game, player_id: PlayerId, mulligans_taken: int) -> bool:
    """A policy that never mulligans. Useful for tests that want a fixed hand."""
    return False


def simple_land_policy(
    game: Game, player_id: PlayerId, mulligans_taken: int, *, low: int = 2, high: int = 5
) -> bool:
    """Mulligan a hand with too few or too many lands.

    A placeholder until the bots take over the decision. Stops after two
    mulligans regardless, because a five-card hand is nearly always worse than
    a bad seven.
    """
    if mulligans_taken >= 2:
        return False
    player = game.players[player_id]
    lands = 0
    for object_id in player.hand:
        obj = game.objects[object_id]
        if game.printed_characteristics(obj).is_land:
            lands += 1
    return lands < low or lands > high
