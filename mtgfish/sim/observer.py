"""Watching a game and writing down what happened.

The observer is attached to a ``Game`` and reads its event stream. It never
asks the engine to do anything differently and never inspects anything the
engine would not otherwise compute - a run with statistics on has to play the
same game as a run with them off, or the numbers describe a different game
from the one the replay will show.

Two things are collected that the engine does not track for itself:

**Removal attribution.** When one of your objects leaves the battlefield
because of somebody else's effect, the pair (victim, source) is recorded. The
engine knows both; nothing else needs them, so nothing else stores them.

**Per-turn series.** Sampled at end of turn, when every player is at a
comparable point in their own cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..rules.enums import CardType, LossReason, Zone
from ..rules.events import Event, EventKind
from .records import CardEvent, GameRecord, RemovalEvent, TurnSnapshot, WinReason

if TYPE_CHECKING:
    from ..rules.game import Game

#: How a player lost, mapped to how the *winner* won. A player at zero life
#: died to whatever brought them there, and the engine records the mechanism
#: rather than the culprit - which is why combat and noncombat damage share a
#: loss reason and are separated by what the last damage event was.
_LOSS_TO_WIN = {
    LossReason.LIFE: WinReason.COMBAT_DAMAGE,
    LossReason.EMPTY_LIBRARY: WinReason.MILL,
    LossReason.POISON: WinReason.POISON,
    LossReason.COMMANDER_DAMAGE: WinReason.COMMANDER_DAMAGE,
    LossReason.EFFECT: WinReason.ALTERNATE,
    LossReason.CONCEDE: WinReason.CONCEDED,
}

#: Zone changes that mean somebody took your permanent away, and the word for
#: it that a report can show a human.
_REMOVAL_WORDS = {
    EventKind.DESTROYED: "destroyed",
    EventKind.EXILED: "exiled",
    EventKind.SACRIFICED: "sacrificed",
    EventKind.RETURNED_TO_HAND: "bounced",
    EventKind.COUNTERED: "countered",
    EventKind.DIES: "died",
}


class Observer:
    """Collects one game's record.

    Attached as ``game.observer``; the engine calls it for every event.
    """

    __slots__ = (
        "record",
        "hero",
        "_last_combat_damage_turn",
        "_seen_names",
        "_counted",
        "_own_turns",
    )

    def __init__(self, record: GameRecord, hero: int) -> None:
        self.record = record
        #: Objects whose departure has already been attributed. One permanent
        #: leaving produces several events - a destroyed creature emits both
        #: DESTROYED and DIES - and counting each of them would double every
        #: creature killed by removal while leaving artifacts counted once.
        self._counted: set[int] = set()
        #: How many turns each player has taken, so a series is indexed by a
        #: player's own turn rather than the global counter.
        self._own_turns: dict[int, int] = {}
        #: Which player the run is about. Everything card-level is recorded
        #: for this player only - a 10,000-game run that stored every card
        #: event for four players would be four times the size and three
        #: quarters irrelevant.
        self.hero = hero
        self._last_combat_damage_turn = -1
        self._seen_names: dict[int, str] = {}

    def __call__(self, game: Game, event: Event) -> None:
        kind = event.kind

        if kind is EventKind.DREW_CARD:
            self._drew(game, event)
        elif kind is EventKind.CAST_SPELL:
            self._cast(game, event)
        elif kind is EventKind.COMBAT_DAMAGE_DEALT:
            self._last_combat_damage_turn = game.turn
        elif kind in _REMOVAL_WORDS:
            self._removal(game, event)
        elif kind is EventKind.TURN_ENDED:
            self._end_of_turn(game)
        elif kind is EventKind.PLAYER_LEFT_GAME:
            self._eliminated(game, event)

    # -- individual events --------------------------------------------------

    def _drew(self, game: Game, event: Event) -> None:
        if event.player != self.hero:
            return
        player = game.player(self.hero)
        if not player.hand:
            return
        obj = game.objects.get(player.hand[-1])
        if obj is not None:
            self.record.drawn.add(self._name(game, obj))

    def _cast(self, game: Game, event: Event) -> None:
        if event.player != self.hero:
            return
        obj = game.objects.get(event.object_id)
        if obj is None:
            return
        name = self._name(game, obj)
        self.record.cast.append(
            CardEvent(turn=game.turn, name=name, controller=self.hero, kind="cast")
        )
        # Recorded as the caster's *own* turn number, not the global
        # player-turn counter. Globally, four identical decks cast their
        # commanders on turns five through eight - which is seat position,
        # not deck behaviour, and it makes the histogram describe the seating
        # rather than the deck.
        if obj.is_commander and self.record.commander_landed[self.hero] is None:
            self.record.commander_landed[self.hero] = self._own_turns.get(
                self.hero, 0
            ) + 1

    def _removal(self, game: Game, event: Event) -> None:
        """Somebody's permanent left, possibly because of somebody else."""
        victim = game.objects.get(event.object_id)
        if victim is None:
            return
        if event.object_id in self._counted:
            return
        owner = victim.controller
        source = game.objects.get(event.source) if event.source else None
        source_controller = (
            source.controller if source is not None else event.source_controller
        )

        # Only losses to *other* players are attribution-worthy. A creature
        # you sacrificed to your own outlet is a cost you chose to pay, not
        # presence somebody took from you.
        if source_controller == owner or source_controller < 0:
            return
        if owner != self.hero:
            return

        self._counted.add(event.object_id)
        self.record.removal.append(
            RemovalEvent(
                turn=game.turn,
                victim=self._name(game, victim),
                victim_controller=owner,
                source=self._name(game, source) if source is not None else "unknown",
                source_controller=source_controller,
                how=_REMOVAL_WORDS[event.kind],
            )
        )

    def _eliminated(self, game: Game, event: Event) -> None:
        # CR 800.4: the engine's event for leaving is PLAYER_LEFT_GAME. There
        # is a PLAYER_LOST kind too, and watching it instead records nothing
        # at all - which is exactly what happened until a run reported zero
        # eliminations in games that plainly had a winner.
        player = game.player(event.player)
        reason = player.loss_reason
        self.record.eliminations.append(
            (game.turn, int(event.player), reason.name if reason else "unknown")
        )

    def _end_of_turn(self, game: Game) -> None:
        """Sample the active player only, at the end of their own turn.

        Sampling everyone at everyone's turn-end catches each player at a
        different point in their own cycle, and averaging that over games
        where the hero sat in different seats turns a clean curve into noise.
        """
        player = game.player(game.active_player)
        if player.has_lost:
            return
        self._own_turns[player.id] = self._own_turns.get(player.id, 0) + 1
        self.record.turns_series.append(
            self._snapshot(game, player, self._own_turns[player.id])
        )

    # -- sampling -----------------------------------------------------------

    def _snapshot(self, game: Game, player, own_turn: int) -> TurnSnapshot:
        lands = 0
        creatures = 0
        power = 0
        permanents = 0
        mana = 0

        for obj in game.permanents(player.id):
            permanents += 1
            chars = game.characteristics(obj)
            if chars.is_land:
                lands += 1
            if chars.is_creature:
                creatures += 1
                power += chars.power or 0
            # Every mana source, tapped or not: what this player can produce
            # on their turn once they untap. Counting only untapped ones at
            # end of turn measures what was left over after casting, which
            # falls as a deck develops and reads as the mana base going
            # backwards.
            if any(
                ability.is_mana_ability and not ability.unparsed
                for ability in chars.abilities
            ):
                mana += 1

        return TurnSnapshot(
            turn=own_turn,
            player=int(player.id),
            life=player.life,
            lands=lands,
            mana_available=mana,
            cards_in_hand=len(player.hand),
            permanents=permanents,
            board_power=power,
            creatures=creatures,
        )

    def _name(self, game: Game, obj) -> str:
        """A card's name, cached because the same object is asked about often.

        Falls back to last-known information: an object that has already left
        may no longer have computable characteristics, and "unknown" in a
        removal report is worse than useless.
        """
        cached = self._seen_names.get(obj.id)
        if cached is not None:
            return cached
        name = ""
        try:
            name = game.characteristics(obj).name
        except Exception:  # noqa: BLE001 - a gone object still needs a name
            name = ""
        if not name:
            card = getattr(obj, "card", None)
            name = getattr(card, "name", "") or "unknown"
        self._seen_names[obj.id] = name
        return name


def finish(game: Game, record: GameRecord) -> GameRecord:
    """Fill in everything only knowable once the game is over."""
    record.turns = game.turn
    record.digest = game.log.digest()
    record.commander_casts = [
        sum(player.commander_casts.values()) for player in game.players
    ]
    # Carried onto the record, not just the log: the log is off unless a
    # replay asked for it, and a game that ran away is the one thing a person
    # most needs told about.
    record.runaway = getattr(game, "runaway", "")
    record.loops = list(getattr(game, "loops", ()))
    record.loop_draw = bool(getattr(game, "loop_draw", False))


    living = [player for player in game.players if not player.has_lost]
    if game.winners:
        record.winner = int(game.winners[0])
    elif len(living) == 1:
        record.winner = int(living[0].id)
    else:
        record.winner = None

    record.win_reason = _win_reason(game, record)
    return record


def _win_reason(game: Game, record: GameRecord) -> WinReason:
    """How the winner won, inferred from how everyone else lost.

    The engine records why each player *lost*, which is the fact it can know
    for certain. Turning that into how somebody won needs one extra
    distinction the loss reason does not carry: whether the damage that killed
    the last opponent came from combat.
    """
    if record.winner is None:
        return WinReason.LOOP_DRAW if record.loop_draw else WinReason.STALL_OUT
    if not record.eliminations:
        return WinReason.LAST_STANDING

    _, loser, reason_name = record.eliminations[-1]
    player = game.player(loser)
    reason = player.loss_reason
    if reason is None:
        return WinReason.LAST_STANDING

    win = _LOSS_TO_WIN.get(reason, WinReason.LAST_STANDING)
    if win is WinReason.COMBAT_DAMAGE and player.left_on_turn is not None:
        # Life loss with no combat damage that turn was burn, drain or a
        # sacrifice effect, and lumping those in with beatdown would make
        # every control deck look aggressive.
        if player.left_on_turn != _last_combat_turn(game):
            return WinReason.NONCOMBAT_DAMAGE
        # A drain loop is usually set off *by* combat damage - Exquisite Blood
        # sees the hit - so the kill lands on a combat turn. The loop did the
        # killing, and calling it a combat win hid every combo kill in the
        # beatdown column.
        if player.left_on_turn == getattr(game, "last_loop_turn", -1):
            return WinReason.NONCOMBAT_DAMAGE
    return win


def _last_combat_turn(game: Game) -> int:
    observer = game.observer
    return getattr(observer, "_last_combat_damage_turn", -1)


def new_record(index: int, seed: int, players: int) -> GameRecord:
    record = GameRecord(index=index, seed=seed, turns=0)
    record.commander_landed = [None] * players
    record.commander_casts = [0] * players
    record.mulligans = [0] * players
    return record


__all__ = ["Observer", "finish", "new_record", "CardType", "Zone"]
