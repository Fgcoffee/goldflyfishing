"""The Game: the single owner of all mutable state.

Everything the rules touch lives here or hangs off here. Two design choices
carry most of the weight:

**One object table.** ``Game.objects`` maps every ``ObjectId`` to its
``GameObject``; zones hold ids, not objects. A card therefore cannot end up in
two zones because two lists disagreed, which is a bug class this design simply
does not have.

**Determinism by construction.** Every shuffle and every random choice draws
from ``Game.rng``, seeded per game. Nothing iterates an unordered set, and
nothing depends on string hashing. This is not tidiness - replay reconstructs a
game from its seed alone, so any ambient randomness would make the replay show
a different game from the one the statistics came from.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterator, Protocol

from .abilities import Ability
from .characteristics import Characteristics, from_face
from .effects import Effect
from .enums import LossReason, Phase, Step, Zone
from .events import Event, EventKind
from .gameobject import GameObject, ObjectKind
from .ids import NO_OBJECT, NO_PLAYER, IdAllocator, ObjectId, PlayerId
from .log import GameLog
from .player import Player
from .relaxations import STRICT, Relaxations


class AbilityProvider(Protocol):
    """Supplies the abilities of a card face.

    The seam between the rules engine and the parser. The engine never reads
    oracle text; it asks for abilities. Tests inject hand-written ones, and the
    parser plugs in here once it exists, without the engine changing at all.
    """

    def abilities_for(self, card: object, face_index: int) -> tuple[Ability, ...]: ...


class UnparsedAbilityProvider:
    """The honest default: every face with rules text has one unparsed ability.

    Not an empty tuple. A card whose text we cannot read is a card whose
    behaviour is missing, and the coverage report needs to be able to say so.
    Returning nothing would make a Wrath of God look like a blank piece of
    cardboard that the engine was perfectly happy with.
    """

    def abilities_for(self, card: object, face_index: int) -> tuple[Ability, ...]:
        try:
            face = card.faces[face_index]  # type: ignore[attr-defined]
        except (AttributeError, IndexError):
            return ()
        text = (face.oracle_text or "").strip()
        if not text:
            return ()
        return (Ability.unreadable(text),)


@dataclass(slots=True)
class ContinuousEffect:
    """One active continuous effect, awaiting application by the layer system.

    Created by a resolved spell (CR 611.2) or generated from a static ability
    (CR 611.3). Ordered by ``timestamp`` within its layer (CR 613.7).
    """

    effect: Effect
    source: ObjectId
    controller: PlayerId
    timestamp: int
    layer: int
    duration: int
    #: The turn this started applying on. "Until your next turn" and "until
    #: the end of your next turn" cannot be resolved into a turn number when
    #: the effect is created - an extra turn taken in between changes which
    #: turn that is - so the turn it began on is recorded and the expiry check
    #: asks whether the controller has since begun a later one.
    created_turn: int = 0
    #: The static ability that generated this, when one did. Kept so the layer
    #: system can ask whether that ability still exists after earlier effects in
    #: the same layer have been applied - which is what lets Blood Moon shut
    #: Urborg off rather than merely reorder it.
    ability: object | None = None
    #: For effects from static abilities: they stop applying the instant the
    #: source stops existing or stops having the ability, so they are
    #: regenerated rather than persisted.
    from_static_ability: bool = False
    #: CR 613.8a's third clause: a characteristic-defining ability and an
    #: ordinary effect never depend on one another.
    is_cda: bool = False
    expired: bool = False


@dataclass(slots=True)
class Game:
    """A game in progress."""

    players: list[Player] = field(default_factory=list)
    objects: dict[ObjectId, GameObject] = field(default_factory=dict)

    # -- shared zones (CR 403, 405, 406, 408) -------------------------------
    battlefield: list[ObjectId] = field(default_factory=list)
    #: Index -1 is the top of the stack: the next thing to resolve.
    stack: list[ObjectId] = field(default_factory=list)
    exile: list[ObjectId] = field(default_factory=list)
    command: list[ObjectId] = field(default_factory=list)

    ids: IdAllocator = field(default_factory=IdAllocator)
    rng: random.Random = field(default_factory=random.Random)
    log: GameLog = field(default_factory=GameLog)
    ability_provider: AbilityProvider = field(default_factory=UnparsedAbilityProvider)
    #: Rules suspended for this game (see ``rules.relaxations``). Only the
    #: sandbox ever sets anything here; a simulated game is always ``STRICT``,
    #: and the default is shared rather than built per game because it is
    #: frozen and every game's copy would be identical.
    relaxations: Relaxations = STRICT

    # -- turn state ---------------------------------------------------------
    turn: int = 0
    #: Turn order, fixed at the start and never reordered. Players who leave
    #: stay in the list so historical references still resolve (CR 800.4).
    turn_order: list[PlayerId] = field(default_factory=list)
    active_player: PlayerId = NO_PLAYER
    priority_player: PlayerId = NO_PLAYER
    phase: Phase = Phase.BEGINNING
    step: Step = Step.UNTAP

    # -- continuous effects and triggers ------------------------------------
    continuous_effects: list[ContinuousEffect] = field(default_factory=list)
    #: Triggered abilities that have triggered but not yet been put on the
    #: stack (CR 603.3): they wait for a player to receive priority. Each is a
    #: ``triggers.PendingTrigger``, which carries the controller read when the
    #: ability triggered - by the time it reaches the stack its source may be
    #: gone, and CR 603.3d still puts it under whoever controlled it then.
    pending_triggers: list = field(default_factory=list)
    delayed_triggers: list = field(default_factory=list)
    #: State triggers (CR 603.8) whose condition is currently true and which
    #: have already fired for it. They re-arm only once the condition goes
    #: false, or a permanently-true condition would trigger endlessly.
    armed_state_triggers: set = field(default_factory=set)

    #: Bumped whenever anything could change a derived characteristic, which
    #: invalidates every cached ``Characteristics`` at once.
    epoch: int = 0
    #: The whole-board layer computation, cached against ``epoch``. CR 613.6
    #: makes each layer depend on the previous one across every object, so the
    #: board is computed together and memoised together.
    _board: dict | None = None
    _board_epoch: int = -1
    #: Set while the layer system is mid-computation. Filter evaluation reads
    #: partially-applied characteristics from here instead of recursing back
    #: into the computation that is asking the question.
    board_in_progress: dict | None = None

    # -- game outcome -------------------------------------------------------
    game_over: bool = False
    winners: tuple[PlayerId, ...] = ()

    #: Maps every object a commander has ever been to the identity it started
    #: with. A commander changing zones becomes a new object (CR 400.7), but
    #: CR 903.10 tracks damage per commander across all of them, so the chain
    #: has to be followed.
    commander_origin: dict[ObjectId, ObjectId] = field(default_factory=dict)

    #: Active replacement and prevention effects (CR 614, 615).
    replacement_effects: list = field(default_factory=list)
    #: Which abilities watch which event kind, so an event does not have to
    #: walk the board to find out that nothing cares. Keyed by epoch and by
    #: how many objects are in the watched zones - see ``triggers._watchers``.
    _trigger_index: dict | None = None
    _trigger_index_key: tuple = ()
    #: How many actions have been taken this game, and what the last one was.
    #: See ``rules.turn.ACTION_BUDGET``: a game that spends its budget is
    #: stopped and reported rather than left to run, because the alternative
    #: is a simulator that hangs.
    actions_taken: int = 0
    runaway: str = ""
    #: The steps and repetition runs of the current priority window, for
    #: noticing a loop. See rules.loops.
    loop_window: object | None = None
    #: Actions belonging to a loop already shortcut this turn, which the
    #: player does not take again - the result has been applied.
    exhausted_loop_steps: set = field(default_factory=set)
    #: Actions of a loop that killed a player and may carry on against the
    #: next one, which the bot's repetition cap must not cut short.
    continuing_loop_steps: set = field(default_factory=set)
    #: What happened to every loop this game, for the report.
    loops: list = field(default_factory=list)
    #: CR 104.4b: the game ended as a draw on a loop nothing could stop.
    loop_draw: bool = False
    #: Set while a shortcut is applied, so its lump sum does not fire the
    #: cycle's own triggers a second time.
    suppress_triggers: bool = False
    #: The turn a loop shortcut last ran. A game won by a drain loop that
    #: combat damage set off happens on a turn with combat damage in it, and
    #: without this it was reported as won by combat.
    last_loop_turn: int = -1
    #: What happened this turn, as (event kind, player) -> total amount, and
    #: (event kind, player, "count") -> how many times. Cleared at the start
    #: of every turn. A counter rather than a list of events: the cards that
    #: ask about this want "how many" or "did it happen", and a 100-turn
    #: stall-out would otherwise accumulate a lot of nothing.
    turn_history: dict = field(default_factory=dict)
    #: Permissions (CR 113.6) - the mirror of restrictions. Cached the same
    #: way and invalidated by the same epoch, because both are derived from
    #: the same static abilities.
    standing_permissions: list = field(default_factory=list)
    permissions_cache: list | None = None
    permissions_epoch: int = -1
    #: Replacement effects derived from permanents' static abilities, cached
    #: the same way and invalidated by the same epoch.
    static_replacements_cache: list | None = None
    static_replacements_epoch: int = -1
    #: Prohibitions from resolved spells, which outlive their source
    #: (CR 611.2b). Those from static abilities are rebuilt each epoch instead.
    standing_restrictions: list = field(default_factory=list)
    restrictions_cache: list | None = None
    restrictions_epoch: int = -1
    #: CR 607: cards exiled by an ability, keyed by the object that exiled
    #: them, so a linked ability can find "the exiled cards" later.
    exiled_with: dict = field(default_factory=dict)
    #: The current combat, if any (CR 506). Recreated each combat phase.
    combat: object | None = None
    #: Commanders that have arrived in a graveyard or exile since state-based
    #: actions were last checked. CR 903.9a's option applies only to those, and
    #: only until the next check - a commander that has sat in a graveyard
    #: through a check stays there.
    commanders_awaiting_zone_choice: list[ObjectId] = field(default_factory=list)
    #: CR 731.1: True for day, False for night, None for neither. "Neither" is
    #: a real state - the game has no day/night designation until something
    #: creates one - so this is deliberately three-valued.
    day_night: bool | None = None
    #: Spells cast during the current turn and the previous one, which is what
    #: CR 731.2's day/night flip reads.
    spells_cast_this_turn: int = 0
    spells_cast_last_turn: int = 0
    #: CR 724: set when an effect ends the turn. The turn loop skips straight
    #: to cleanup rather than finishing the remaining steps.
    end_turn_requested: bool = False
    #: Steps to skip, by Step value, cleared as each turn ends.
    skipped_steps: set = field(default_factory=set)
    #: Extra turns waiting to be taken (CR 500.7), in the order created.
    extra_turns: list[PlayerId] = field(default_factory=list)
    #: Decision-makers, one per player. Absent means "take the default", which
    #: keeps the rules engine runnable and testable without any AI at all.
    #: CR 716.2b and 719.3b: designations that live beside the object rather
    #: than on it. Both are explicitly non-copiable and both end when the
    #: permanent leaves the battlefield, so they are keyed by object id and
    #: dropped on zone change - a new object has never been solved.
    #: CR 500.8: extra phases and steps, taken after the current one finishes.
    extra_phases: list = field(default_factory=list)
    extra_steps: list = field(default_factory=list)
    #: CR 701.34: the last vote's tally, so a later sentence can ask who won.
    last_vote: dict = field(default_factory=dict)

    #: CR 723.1: who is making a player's decisions, when it is not them.
    #: Keyed by the controlled player; CR 723.1a says the latest such effect
    #: wins, which a dict gives for free.
    controlled_players: dict = field(default_factory=dict)
    #: CR 727.1: set when an effect restarts the game. The turn loop sees it,
    #: ends this game without a winner, and the runner starts a new one.
    restart_requested: bool = False
    #: CR 727.1a: who goes first in the restarted game.
    restart_starting_player: object = None
    #: CR 722.3b: permanents currently carrying the prepared designation.
    prepared_permanents: set = field(default_factory=set)
    #: The decks this game was built from, kept so CR 727 can rebuild it.
    source_decks: list = field(default_factory=list)
    #: Where the simulator watches from. ``None`` in ordinary play and in the
    #: rules tests, so nothing pays for statistics it does not want.
    observer: object = None

    class_levels: dict[ObjectId, int] = field(default_factory=dict)
    solved_permanents: set[ObjectId] = field(default_factory=set)

    agents: dict[PlayerId, object] = field(default_factory=dict)

    def is_controlled(self, player_id: PlayerId) -> bool:
        """CR 723.1: whether someone else is making this player's decisions."""
        controller = self.controlled_players.get(player_id)
        return controller is not None and controller != player_id

    def concede(self, player_id: PlayerId) -> bool:
        """CR 104.3a: a player may concede at any time.

        CR 723.6 is the reason this is a method rather than a loss reason the
        agent can return: a player being controlled may still concede, but the
        player controlling them may *not* concede on their behalf. Conceding is
        the one decision control never transfers, so it is taken by the player
        directly and never routed through ``agent_for``.
        """
        from .enums import LossReason

        self.player_loses(player_id, LossReason.CONCEDE)
        return True

    def agent_for(self, player_id: PlayerId) -> object | None:
        """Whoever is making this player's decisions right now.

        CR 723.5: while a player is controlled, the controlling player makes
        every choice the controlled player would make. CR 723.3 is the part
        that is easy to get wrong - only *control of the player* changes.
        Their permanents are still theirs, they are still the active player on
        their own turn, and CR 723.5a says their resources pay their costs.
        So this redirects the decisions and nothing else.
        """
        controller = self.controlled_players.get(player_id)
        if controller is not None and controller != player_id:
            return self.agents.get(controller)
        return self.agents.get(player_id)

    def control_player(self, controlled: PlayerId, controller: PlayerId) -> None:
        """CR 723.1: take control of another player."""
        self.controlled_players[controlled] = controller
        self.log.record(
            self,
            f"{self.player(controller).name} controls {self.player(controlled).name}",
            kind="control-player",
            player=controller,
        )

    def release_player(self, controlled: PlayerId) -> None:
        """The controlling effect ends (CR 723.1)."""
        if self.controlled_players.pop(controlled, None) is not None:
            self.log.record(
                self,
                f"{self.player(controlled).name} is no longer controlled",
                kind="control-player",
                player=controlled,
            )

    # ---------------------------------------------------------------------
    # Players
    # ---------------------------------------------------------------------

    def player(self, player_id: PlayerId) -> Player:
        return self.players[player_id]

    @property
    def living_players(self) -> list[Player]:
        return [p for p in self.players if not p.has_lost]

    def opponents(self, player_id: PlayerId) -> list[PlayerId]:
        return [p.id for p in self.players if p.id != player_id and not p.has_lost]

    def turn_order_from(self, start: PlayerId) -> list[PlayerId]:
        """Living players in turn order, beginning with ``start``.

        Players who have left the game are skipped but keep their seat, so the
        order of the survivors never changes (CR 800.4).
        """
        if not self.turn_order:
            return []
        try:
            offset = self.turn_order.index(start)
        except ValueError:
            offset = 0
        rotated = self.turn_order[offset:] + self.turn_order[:offset]
        return [p for p in rotated if not self.players[p].has_lost]

    def apnap_order(self) -> list[PlayerId]:
        """Active Player, Non-Active Player order (CR 101.4).

        The order in which simultaneous choices are made and simultaneous
        triggers are put on the stack. Used constantly, and getting it wrong
        changes which trigger resolves first.
        """
        return self.turn_order_from(self.active_player)

    def next_player(self, after: PlayerId) -> PlayerId:
        order = self.turn_order_from(after)
        return order[1] if len(order) > 1 else after

    # ---------------------------------------------------------------------
    # Zones
    # ---------------------------------------------------------------------

    def zone_list(self, zone: Zone, player: PlayerId = NO_PLAYER) -> list[ObjectId]:
        """The list backing a zone. Shared zones ignore ``player``."""
        if zone is Zone.BATTLEFIELD:
            return self.battlefield
        if zone is Zone.STACK:
            return self.stack
        if zone is Zone.EXILE:
            return self.exile
        if zone is Zone.COMMAND:
            return self.command
        if player == NO_PLAYER:
            raise ValueError(f"{zone.name} is player-owned; a player is required")
        return self.players[player].zone(zone)

    def objects_in(self, zone: Zone, player: PlayerId = NO_PLAYER) -> Iterator[GameObject]:
        for object_id in self.zone_list(zone, player):
            yield self.objects[object_id]

    def permanents(self, controller: PlayerId | None = None) -> Iterator[GameObject]:
        """Every permanent, optionally filtered by controller.

        Phased-out permanents are excluded: CR 702.26a says they are treated as
        though they do not exist.
        """
        for object_id in self.battlefield:
            obj = self.objects[object_id]
            if obj.phased_out:
                continue
            if controller is None or obj.controller == controller:
                yield obj

    # ---------------------------------------------------------------------
    # Object lifecycle
    # ---------------------------------------------------------------------

    def create_object(
        self,
        card: object,
        owner: PlayerId,
        zone: Zone,
        *,
        kind: ObjectKind = ObjectKind.CARD,
        face_index: int = 0,
        to_top: bool = False,
    ) -> GameObject:
        """Put a new object into a zone."""
        obj = GameObject(
            id=self.ids.object_id(),
            kind=kind,
            owner=owner,
            controller=owner,
            base_controller=owner,
            zone=zone,
            card=card,
            face_index=face_index,
            timestamp=self.ids.timestamp(),
        )
        self.objects[obj.id] = obj
        target = self.zone_list(zone, owner)
        if to_top:
            target.insert(0, obj.id)
        else:
            target.append(obj.id)
        return obj

    def move_object(
        self,
        obj: GameObject,
        to_zone: Zone,
        *,
        to_player: PlayerId | None = None,
        to_top: bool = False,
        position: int | None = None,
    ) -> GameObject:
        """Move an object to another zone, creating a new object (CR 400.7).

        The new object remembers nothing: counters, damage, attachments, and
        continuous effects targeting the old object are all gone. This is the
        rule that makes flicker effects work and that stops "destroy target
        creature" from following a card into exile and back.

        Returns the *new* object. Callers must not keep using the old one.
        """
        from_zone = obj.zone
        owner = obj.owner

        # CR 614: replacement effects apply *before* the move, so nothing has
        # to be undone. This is where a commander goes to the command zone
        # instead (CR 903.9), and where "if it would die, exile it instead"
        # takes hold - in both cases the object never reaches the graveyard at
        # all, which is exactly the observable difference from a trigger.
        from .cr614_replacement import apply_replacements, commander_zone_replacement

        prospective = Event(
            EventKind.ZONE_CHANGE,
            object_id=obj.id,
            player=owner,
            from_zone=from_zone,
            to_zone=to_zone,
        )
        prospective = commander_zone_replacement(self, prospective)
        if (
            from_zone is Zone.BATTLEFIELD
            and prospective.to_zone is Zone.GRAVEYARD
            and obj.counter_count("finality")
        ):
            # CR 122.1: a permanent with a finality counter that would go to a
            # graveyard from the battlefield is exiled instead. The counter was
            # put on and meant nothing, so "return it with a finality counter"
            # was a plain reanimation that could be repeated.
            prospective = prospective.replaced(to_zone=Zone.EXILE)
        if self.has_replacements:
            replaced = apply_replacements(self, prospective)
            if replaced is None:
                return obj  # The move was prevented; nothing happened.
            prospective = replaced
        to_zone = prospective.to_zone or to_zone
        if to_zone is Zone.COMMAND:
            to_player = owner

        destination_player = to_player if to_player is not None else owner

        # CR 716.2b, 719.3b: level and the solved designation last only while
        # the permanent is on the battlefield. The new object gets neither.
        from .cr300_card_types import forget_designations

        forget_designations(self, obj.id)

        self._detach_all(obj)
        self._remove_from_zone(obj)

        # CR 111.7: a token that leaves the battlefield ceases to exist. It
        # still "leaves", so leave-the-battlefield triggers see it, but it is
        # never actually placed in the destination zone.
        if obj.kind is ObjectKind.TOKEN and to_zone is not Zone.BATTLEFIELD:
            # The events go out while the token still reads as where it was,
            # which is what the card path's pre-move object gives a trigger
            # (CR 603.6e, 603.10a). Moving the zone first made every "whenever
            # a creature you control dies" ask about a token already in the
            # graveyard - not a creature on the battlefield - and answer no, so
            # no aristocrat ever triggered off a token. The zone moves once the
            # events are out, which is what CR 704.5d's check reads.
            self.invalidate_characteristics()
            self.emit(
                Event(
                    EventKind.ZONE_CHANGE,
                    object_id=obj.id,
                    player=owner,
                    from_zone=from_zone,
                    to_zone=to_zone,
                    data=(obj.id,),
                )
            )
            self._emit_zone_specific(obj, from_zone, to_zone, previous=obj)
            obj.zone = to_zone
            self.invalidate_characteristics()
            self.emit(Event(EventKind.CEASED_TO_EXIST, object_id=obj.id, player=owner))
            return obj

        new_obj = GameObject(
            id=self.ids.object_id(),
            kind=obj.kind,
            owner=owner,
            controller=destination_player if to_zone is Zone.BATTLEFIELD else owner,
            base_controller=destination_player if to_zone is Zone.BATTLEFIELD else owner,
            zone=to_zone,
            card=obj.card,
            face_index=0 if to_zone is not Zone.BATTLEFIELD else obj.face_index,
            timestamp=self.ids.timestamp(),
            is_commander=obj.is_commander,
        )
        if to_zone is Zone.BATTLEFIELD:
            new_obj.entered_battlefield_turn = self.turn
            new_obj.summoning_sick = True

        # The old object is kept, and deliberately keeps its old zone: that is
        # last-known information (CR 603.6e, 608.2g), and a dies-trigger asking
        # "was it a creature you controlled?" has to be able to see the answer
        # as it was on the battlefield. ``superseded_by`` is what marks it as no
        # longer the live object - checking the zone would give the wrong answer
        # in one direction or the other.
        obj.superseded_by = new_obj.id
        new_obj.previous_id = obj.id

        self.objects[new_obj.id] = new_obj
        if new_obj.is_commander:
            self.commander_origin[new_obj.id] = self.commander_identity(obj.id)
            # CR 903.9a: note it for the state-based action. It really is in the
            # graveyard right now - it died, and dies-triggers are about to see
            # it - and its owner gets the choice at the next check.
            if to_zone in (Zone.GRAVEYARD, Zone.EXILE):
                self.commanders_awaiting_zone_choice.append(new_obj.id)
        target = self.zone_list(to_zone, destination_player)
        if position is not None:
            target.insert(position, new_obj.id)
        elif to_top:
            target.insert(0, new_obj.id)
        else:
            target.append(new_obj.id)

        self.invalidate_characteristics()
        self.emit(
            Event(
                EventKind.ZONE_CHANGE,
                object_id=new_obj.id,
                player=owner,
                from_zone=from_zone,
                to_zone=to_zone,
                data=(obj.id,),
            )
        )
        self._emit_zone_specific(new_obj, from_zone, to_zone, previous=obj)
        return new_obj

    def _emit_zone_specific(
        self,
        obj: GameObject,
        from_zone: Zone,
        to_zone: Zone,
        previous: GameObject | None = None,
    ) -> None:
        """Emit the specific events a zone change implies.

        "Dies" is battlefield-to-graveyard specifically (CR 700.4), not any
        trip to the graveyard - a countered spell does not die.
        """
        if to_zone is Zone.BATTLEFIELD:
            # CR 614.1c: before anything observes the permanent, apply its own
            # "as this enters" effects. Doing it after the event would let a
            # "whenever a land enters untapped" trigger see a tapland wrong.
            from .cr614_replacement import apply_self_entry_replacements

            apply_self_entry_replacements(self, obj)
            self.emit(
                Event(
                    EventKind.ENTERS_BATTLEFIELD,
                    object_id=obj.id,
                    player=obj.controller,
                    from_zone=from_zone,
                )
            )
            return

        if from_zone is Zone.BATTLEFIELD:
            self.emit(
                Event(
                    EventKind.LEAVES_BATTLEFIELD,
                    object_id=obj.id,
                    player=obj.owner,
                    to_zone=to_zone,
                    data=(previous.id,) if previous else (),
                )
            )
            if to_zone is Zone.GRAVEYARD:
                self.emit(
                    Event(
                        EventKind.DIES,
                        object_id=obj.id,
                        player=obj.owner,
                        data=(previous.id,) if previous else (),
                    )
                )

    def _remove_from_zone(self, obj: GameObject) -> None:
        contents = self.zone_list(obj.zone, obj.owner)
        try:
            contents.remove(obj.id)
        except ValueError:
            pass

    def _detach_all(self, obj: GameObject) -> None:
        """Unattach anything attached to this object, and detach it from its host."""
        if obj.attached_to != NO_OBJECT:
            host = self.objects.get(obj.attached_to)
            if host is not None and obj.id in host.attachments:
                host.attachments.remove(obj.id)
            obj.attached_to = NO_OBJECT
        for attachment_id in list(obj.attachments):
            attachment = self.objects.get(attachment_id)
            if attachment is not None:
                attachment.attached_to = NO_OBJECT
        obj.attachments.clear()

    # ---------------------------------------------------------------------
    # Characteristics
    # ---------------------------------------------------------------------

    def invalidate_characteristics(self) -> None:
        """Mark every cached characteristic set stale.

        A single epoch counter beats per-object invalidation: continuous
        effects are global, so almost anything that changes one object's
        characteristics can change another's.
        """
        self.epoch += 1

    def board(self, requesting: GameObject | None = None) -> dict:
        """The whole-board layer computation, memoised against ``epoch``."""
        if self.board_in_progress is not None:
            return self.board_in_progress
        if self._board is not None and self._board_epoch == self.epoch:
            return self._board
        from .cr613_layers import compute_board

        self._board = compute_board(self)
        self._board_epoch = self.epoch
        return self._board

    def characteristics(self, obj: GameObject) -> Characteristics:
        """An object's current characteristics, after continuous effects.

        The layer system (CR 613) does the real work; this is the memoising
        front door everything else uses.
        """
        if self.board_in_progress is not None:
            found = self.board_in_progress.get(obj.id)
            if found is not None:
                return found
            return self.printed_characteristics(obj)

        if obj._characteristics is not None and obj._characteristics_epoch == self.epoch:
            return obj._characteristics

        from .cr613_layers import compute_characteristics

        result = compute_characteristics(self, obj)
        obj._characteristics = result
        obj._characteristics_epoch = self.epoch
        return result

    def printed_characteristics(self, obj: GameObject) -> Characteristics:
        """Characteristics before any continuous effect - the copiable values.

        CR 613.2: this is what a copy effect copies, and the base the layer
        system starts from.
        """
        # The provider's generation is part of the key. Nothing changes a
        # card's abilities mid-game in play - the parser reads them once - but
        # a test that rewrites the ability table must not be served a stale
        # copiable base, and a cache that is only correct in production is not
        # a cache anyone can reason about.
        key = (
            obj.face_index,
            obj.flipped,
            obj.face_down,
            getattr(self.ability_provider, "generation", 0),
        )
        if obj._printed is not None and obj._printed_key == key:
            return obj._printed

        printed = self._build_printed(obj)
        obj._printed = printed
        obj._printed_key = key
        return printed

    def _build_printed(self, obj: GameObject) -> Characteristics:
        card = obj.card
        if card is None:
            return Characteristics(name="", abilities=())
        # CR 710.2: a flipped permanent reads its bottom half instead. The
        # flip is not a face change everywhere else in the engine - a flip
        # card in any other zone, and an unflipped one on the battlefield, is
        # only ever its top half - so it is resolved here rather than by
        # rewriting face_index and having every other caller see it.
        face_index = 1 if obj.flipped and obj.face_index == 0 else obj.face_index
        try:
            face = card.faces[face_index]  # type: ignore[attr-defined]
        except (AttributeError, IndexError):
            return Characteristics(name=getattr(card, "name", ""))
        # Tokens and emblems have no card behind them, so they carry their
        # abilities directly rather than going through the provider - the
        # provider is a lookup by card face name, and a token's face name is
        # something it invented for itself.
        own = getattr(card, "abilities", None)
        if own is not None:
            abilities = tuple(own)
        else:
            abilities = self.ability_provider.abilities_for(card, face_index)
        # CR 305.6: a land's basic land types carry mana abilities that are not
        # printed in its text box. A Swamp taps for {B} whether or not anything
        # says so, so they belong in the printed characteristics.
        from .cr613_layers import intrinsic_land_abilities

        printed = from_face(face, abilities + intrinsic_land_abilities(face.type_line))
        if face_index != obj.face_index:
            # CR 710.1c: flipping changes name, text, types and P/T, but not
            # colour and not mana cost. Kamigawa's flip creatures stay the
            # colour they were cast as, which is what makes them still hit by
            # colour-based removal after flipping.
            top = card.faces[obj.face_index]  # type: ignore[attr-defined]
            printed = printed.replace(
                mana_cost=top.mana_cost,
                has_mana_cost=top.has_mana_cost,
                colors=(
                    top.color_indicator
                    if top.color_indicator is not None
                    else top.colors
                ),
            )
        return printed

    # ---------------------------------------------------------------------
    # Events
    # ---------------------------------------------------------------------

    def emit(self, event: Event) -> None:
        """Announce that something happened.

        Triggered abilities that watch this event are collected into
        ``pending_triggers``; they go on the stack the next time a player would
        receive priority (CR 603.3b), not now.

        ``observer`` is how the simulator gathers statistics. It is a plain
        callback rather than a subscriber list because there is exactly one
        consumer and the hot loop runs this millions of times a run - a list
        walk and a virtual call per event is real money at 10,000 games.
        """
        self.log.record(self, str(event), kind="event", player=event.player)
        if self.observer is not None:
            self.observer(self, event)

        # DIVERGENCE. CR 702.179d makes the speed increase an inherent
        # triggered ability. It has no source, but it is a triggered ability
        # and belongs on the stack; applying it inline here skips that.
        self._record_this_turn(event)

        if event.kind is EventKind.LIFE_LOST:
            self._advance_speed(event)

        from .cr603_triggers import collect_triggers

        collect_triggers(self, event)

    def _record_this_turn(self, event: Event) -> None:
        """Tally an event so "did this happen this turn" can be answered."""
        key = (int(event.kind), int(event.player))
        history = self.turn_history
        history[key] = history.get(key, 0) + max(1, int(event.amount or 0))
        counted = (int(event.kind), int(event.player), "count")
        history[counted] = history.get(counted, 0) + 1

    def _advance_speed(self, event: Event) -> None:
        """CR 702.179d: each player whose speed is 1-3 and who is the active
        player gains speed when an *opponent* loses life on their turn.

        At most once each turn, which the player object tracks.
        """
        loser = event.player
        if loser == NO_PLAYER:
            return
        active = self.player(self.active_player)
        if active.id == loser or active.speed == 0:
            return
        if active.increase_speed():
            self.emit_raw(
                Event(EventKind.SPEED_INCREASED, player=active.id, amount=active.speed)
            )

    #: Emit an event that has already been through replacement, or that is
    #: itself the *result* of one. Same thing today; named separately so the
    #: replacement layer never accidentally re-enters replacement.
    emit_raw = emit

    @property
    def has_replacements(self) -> bool:
        """Whether any replacement effect could apply to anything.

        The fast path every caller uses. It has to consider replacements
        derived from permanents' static abilities as well as those a
        resolution registered - testing the registry list alone meant
        Doubling Season and every card like it was skipped before it was ever
        asked, which looks exactly like the card not working.
        """
        if self.replacement_effects:
            return True
        from .cr614_replacement import static_replacements

        return bool(static_replacements(self))

    def replace(self, event: Event) -> Event | None:
        """Run replacement effects over an event that is about to happen."""
        if not self.has_replacements:
            return event
        from .cr614_replacement import apply_replacements

        return apply_replacements(self, event)

    # ---------------------------------------------------------------------
    # Library operations
    # ---------------------------------------------------------------------

    def shuffle_library(self, player_id: PlayerId) -> None:
        """CR 701.20. Uses the game's seeded RNG so replays match exactly."""
        self.rng.shuffle(self.players[player_id].library)
        self.emit(Event(EventKind.SHUFFLED, player=player_id))

    def draw(self, player_id: PlayerId, count: int = 1) -> list[GameObject]:
        """Draw cards (CR 121).

        Drawing from an empty library does not lose the game immediately: it
        sets a flag, and the loss happens as a state-based action the next time
        one would be checked (CR 704.5b). That delay is observable - a player
        can win before it is checked.

        On a bench with no library behind the board, the flag is not even set
        - see ``rules.relaxations``.
        """
        player = self.players[player_id]
        drawn: list[GameObject] = []
        for _ in range(count):
            if not player.library:
                if not self.relaxations.draws_from_an_empty_library_do_nothing:
                    player.attempted_draw_from_empty_library = True
                break
            obj = self.objects[player.library[0]]
            new_obj = self.move_object(obj, Zone.HAND, to_player=player_id)
            player.cards_drawn_this_turn += 1
            drawn.append(new_obj)
            self.emit(
                Event(EventKind.DREW_CARD, object_id=new_obj.id, player=player_id, amount=1)
            )
        return drawn

    # ---------------------------------------------------------------------
    # Losing the game
    # ---------------------------------------------------------------------

    def player_loses(self, player_id: PlayerId, reason: LossReason) -> None:
        """Remove a player from the game (CR 104.3, 800.4).

        Everything they own leaves with them, and any control-change effect
        that gave them control of someone else's permanent ends - which can
        hand permanents back mid-game.
        """
        player = self.players[player_id]
        if player.has_lost:
            return
        if player.cannot_lose and reason is not LossReason.CONCEDE:
            return

        player.has_lost = True
        player.loss_reason = reason
        player.left_on_turn = self.turn
        self.log.record(
            self, f"{player.name or f'P{player_id}'} loses ({reason.name})", kind="loss",
            player=player_id,
        )

        # CR 800.4a: objects owned by the departing player leave the game.
        for object_id in list(self.objects):
            obj = self.objects.get(object_id)
            if obj is None or obj.owner != player_id:
                continue
            self._remove_from_zone(obj)
            del self.objects[object_id]

        # CR 800.4a: any effect giving them control of an object ends.
        for obj in list(self.permanents()):
            if obj.controller == player_id and obj.owner != player_id:
                obj.controller = obj.owner
                # The control-changing effect ends with the player, so the
                # *base* moves too - otherwise layer 2 hands the permanent
                # straight back to someone who is no longer in the game.
                obj.base_controller = obj.owner
                obj.summoning_sick = True

        self.invalidate_characteristics()
        # CR 725.5: if the monarch leaves, the crown passes rather than vanishing.
        if player.is_monarch:
            player.is_monarch = False
            from .cr725_designations import monarch_left_the_game

            monarch_left_the_game(self)
        self.emit(Event(EventKind.PLAYER_LEFT_GAME, player=player_id))
        self._check_game_over()

    def commander_identity(self, object_id: ObjectId) -> ObjectId:
        """The original identity of a commander object (CR 903.10).

        A commander that dies and is recast is a different object every time,
        but the 21-damage clock follows the commander, not the object, so all
        of its incarnations resolve back to one id.
        """
        return self.commander_origin.get(object_id, object_id)

    def _check_game_over(self) -> None:
        living = self.living_players
        if len(living) <= 1:
            self.game_over = True
            self.winners = tuple(p.id for p in living)

    def player_wins(self, player_id: PlayerId) -> None:
        """CR 104.2b: a spell or ability says a player wins.

        Everyone else loses simultaneously, which is what actually ends the
        game.
        """
        player = self.players[player_id]
        if player.cannot_win:
            return
        player.has_won = True
        for other in self.players:
            if other.id != player_id:
                self.player_loses(other.id, LossReason.EFFECT)
        self.game_over = True
        self.winners = (player_id,)

    # ---------------------------------------------------------------------

    def describe_board(self) -> str:
        lines = [f"Turn {self.turn} - {self.step.name} - active P{self.active_player}"]
        for player in self.players:
            lines.append(f"  {player}  hand={player.hand_size} library={player.library_size}")
            controlled = [str(o) for o in self.permanents(player.id)]
            if controlled:
                lines.append("    " + ", ".join(controlled))
        if self.stack:
            lines.append("  Stack: " + ", ".join(str(self.objects[i]) for i in reversed(self.stack)))
        return "\n".join(lines)
