"""Combat (CR 506-511).

The step where most engines quietly diverge from the rules, in four places:

**Restrictions and requirements (CR 508.1d, 509.1c).** A declaration must obey
every restriction, *and* among the declarations that do, must satisfy the
greatest possible number of requirements. "Must attack if able" plus "can't
attack unless you pay {2}" is not a contradiction - it is a constraint problem,
and the answer is that the creature does not attack.

**Damage division (CR 510.1c, 510.1d).** An attacker blocked by several
creatures divides its damage among them however its controller chooses. There
is no damage assignment order and no lethal-first requirement - both were
removed from the rules, and an engine that still enforces them is rejecting
legal assignments. The one place lethal damage still matters is trample
(CR 702.19b), which may only spill over once every blocker has been assigned
lethal damage; deathtouch makes 1 damage lethal for that check (CR 702.2b).
Banding (CR 702.22j) moves the choice to the defending player.

**First strike creates a second damage step (CR 510.4)**, and it only exists if
someone has first or double strike - checked at that moment, so a creature that
gains first strike after blockers are declared still gets it.

**Blocked stays blocked (CR 509.1h).** A creature whose blockers all leave is
still a blocked creature and deals no damage to the player.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..cr100_game_concepts import actions
from ..kernel.enums import CardType, Step
from ..kernel.events import Event, EventKind
from ..kernel.gameobject import GameObject
from ..kernel.ids import NO_OBJECT, ObjectId, PlayerId

if TYPE_CHECKING:
    from ..kernel.game import Game


@dataclass(slots=True)
class Combat:
    """The state of one combat phase."""

    #: attacker -> the player who is defending against it. When a planeswalker
    #: or battle is being attacked this is its controller or protector.
    #:
    #: Deliberately *not* "a player id or an object id in one field". Player
    #: ids and object ids are both small integers, so a combined field silently
    #: resolves "attacking player 1" into "attacking whatever object 1 is".
    attacking: dict[ObjectId, PlayerId] = field(default_factory=dict)
    #: attacker -> the planeswalker or battle it is attacking, when it is
    #: attacking one rather than a player directly (CR 508.1a).
    attacking_permanent: dict[ObjectId, ObjectId] = field(default_factory=dict)
    #: attacker -> the creatures blocking it, in the order they were declared.
    #:
    #: Not a damage assignment order. There is no such thing any more: the
    #: rule that made the attacker order its blockers was deleted, CR 509.2 is
    #: now just "the active player gets priority", and CR 510.1c lets the
    #: controller divide the damage however they like. The order is kept only
    #: so that the engine's default division (``_lethal_first``) is the same
    #: one on every replay.
    blockers: dict[ObjectId, list[ObjectId]] = field(default_factory=dict)
    #: blocker -> the attackers it is blocking.
    blocking: dict[ObjectId, list[ObjectId]] = field(default_factory=dict)
    #: Attackers that were blocked, even if every blocker has since gone
    #: (CR 509.1h).
    was_blocked: set[ObjectId] = field(default_factory=set)
    #: CR 509.1h: attackers become blocked or unblocked when blockers are
    #: declared, and not before. Until then an attacker is neither, so "an
    #: unblocked attacking creature" - ninjutsu's cost - describes nothing.
    blockers_declared: bool = False
    #: Who controlled each permanent in combat at the moment it joined -
    #: attackers, blockers, and the planeswalkers and battles being attacked.
    #: CR 506.4 takes a permanent out of combat when its controller changes,
    #: and the only way to notice a change is to have recorded what it was.
    controllers: dict[ObjectId, PlayerId] = field(default_factory=dict)

    def is_attacking(self, object_id: ObjectId) -> bool:
        return object_id in self.attacking

    def is_blocking(self, object_id: ObjectId) -> bool:
        return bool(self.blocking.get(object_id))

    def is_blocked(self, object_id: ObjectId) -> bool:
        return object_id in self.was_blocked

    def remove(self, object_id: ObjectId) -> None:
        """CR 506.4: take a permanent out of combat entirely."""
        self.stop_attacking_or_blocking(object_id)
        self.stop_being_attacked(object_id)
        self.controllers.pop(object_id, None)

    def stop_attacking_or_blocking(self, object_id: ObjectId) -> None:
        """CR 506.4: it stops being an attacking, blocking, blocked and/or
        unblocked creature.

        Split out from ``remove`` because a permanent can be in combat twice
        over - CR 506.4d's blocking creature that is also a planeswalker
        being attacked - and losing one of those card types ends only the
        role that card type gave it.
        """
        self.attacking.pop(object_id, None)
        self.attacking_permanent.pop(object_id, None)
        self.was_blocked.discard(object_id)
        for blockers in self.blockers.values():
            if object_id in blockers:
                blockers.remove(object_id)
        self.blockers.pop(object_id, None)
        for attackers in self.blocking.values():
            if object_id in attackers:
                attackers.remove(object_id)
        self.blocking.pop(object_id, None)

    def stop_being_attacked(self, object_id: ObjectId) -> None:
        """CR 506.4: a planeswalker or battle removed from combat stops being
        attacked.

        CR 506.4c: its attackers are *not* removed with it. They go on being
        attacking creatures that attack nothing, which is why the entry is
        blanked rather than dropped - a missing entry would read as "attacking
        the defending player" and send the damage through to them.
        """
        for attacker_id, attacked in self.attacking_permanent.items():
            if attacked == object_id:
                self.attacking_permanent[attacker_id] = NO_OBJECT

    def clear(self) -> None:
        self.attacking.clear()
        self.attacking_permanent.clear()
        self.blockers.clear()
        self.blocking.clear()
        self.was_blocked.clear()
        self.controllers.clear()
        self.blockers_declared = False


# ---------------------------------------------------------------------------
# Declaring attackers (CR 508)
# ---------------------------------------------------------------------------


def declare_attackers(game: Game) -> None:
    """CR 508.1: the active player declares attackers, as a turn-based action."""
    combat = _combat(game)
    combat.clear()

    active = game.active_player
    candidates = [obj for obj in game.permanents(active) if can_attack(game, obj)]
    if not candidates:
        game.emit(Event(EventKind.ATTACKERS_DECLARED, player=active))
        return

    agent = game.agent_for(active)
    proposal: dict[ObjectId, int] = {}
    if agent is not None and hasattr(agent, "declare_attackers"):
        proposal = dict(agent.declare_attackers(game, active, candidates) or {})

    proposal = _enforce_attack_requirements(game, active, candidates, proposal)

    for attacker_id, defender in sorted(proposal.items()):
        obj = game.objects.get(attacker_id)
        if obj is None:
            continue
        player, permanent = _resolve_defender(game, defender)
        combat.attacking[attacker_id] = player
        # CR 506.4 watches for a change of controller, so note who both the
        # attacker and whatever it is attacking belong to right now.
        combat.controllers[attacker_id] = obj.controller
        if permanent != NO_OBJECT:
            combat.attacking_permanent[attacker_id] = permanent
            attacked = game.objects.get(permanent)
            if attacked is not None:
                combat.controllers[permanent] = attacked.controller
        obj.attacked_this_turn = True
        # CR 508.1f: attacking taps the creature, unless it has vigilance.
        if not game.characteristics(obj).has_keyword("Vigilance"):
            obj.tapped = True

    game.invalidate_characteristics()
    for attacker_id in sorted(combat.attacking):
        game.emit(
            Event(
                EventKind.ATTACKS,
                object_id=attacker_id,
                player=active,
                amount=combat.attacking[attacker_id],
            )
        )
    game.emit(Event(EventKind.ATTACKERS_DECLARED, player=active))


def can_attack(game: Game, obj: GameObject) -> bool:
    """CR 508.1a: the basic legality of one creature attacking."""
    chars = game.characteristics(obj)
    if not chars.is_creature:
        return False
    if obj.tapped:
        return False
    # CR 302.6: summoning sickness, unless it has haste.
    if obj.summoning_sick and not chars.has_keyword("Haste"):
        return False
    # CR 702.3b: a creature with defender can't attack.
    if chars.has_keyword("Defender"):
        return False
    # CR 101.2: any "can't attack" effect beats every permission to attack.
    from .restrictions import Act, prohibited

    if prohibited(game, Act.ATTACK, obj=obj) is not None:
        return False
    return True


def _enforce_attack_requirements(
    game: Game,
    active: PlayerId,
    candidates: list[GameObject],
    proposal: dict[ObjectId, int],
) -> dict[ObjectId, int]:
    """Apply CR 508.1d-e to a proposed declaration.

    Restrictions are absolute: a declaration that violates one is illegal, so
    offending attackers are dropped. Requirements must then be maximised, so
    any creature that must attack and legally can is added if the agent left it
    out.

    Requirements that conflict with restrictions lose - CR 508.1d puts
    restrictions first, which is why a goaded creature behind a Propaganda it
    cannot pay for simply stays home.
    """
    result = {
        attacker_id: defender
        for attacker_id, defender in proposal.items()
        if _attack_is_permitted(game, game.objects[attacker_id], defender)
    }

    for obj in candidates:
        if obj.id in result:
            continue
        if not _must_attack(game, obj):
            continue
        defender = _forced_defender(game, obj, active)
        if defender is None:
            continue
        if _attack_is_permitted(game, obj, defender):
            result[obj.id] = defender
            game.log.record(
                game, f"{obj} must attack and is forced into combat", kind="combat"
            )
    return result


def _must_attack(game: Game, obj: GameObject) -> bool:
    """Requirements that force an attack (CR 508.1e).

    Goad (CR 701.38) is the one that matters most in Commander: a goaded
    creature attacks if able, and must attack someone other than the player who
    goaded it.
    """
    if obj.goaded_by:
        return True
    return game.characteristics(obj).has_keyword("Attacks each combat if able")


def _forced_defender(game: Game, obj: GameObject, active: PlayerId) -> int | None:
    """Who a forced attacker must attack.

    A goaded creature must attack a player who did not goad it if it can
    (CR 701.38b).
    """
    opponents = [p for p in game.opponents(active)]
    if not opponents:
        return None
    if obj.goaded_by:
        preferred = [p for p in opponents if p not in obj.goaded_by]
        if preferred:
            return preferred[0]
    return opponents[0]


def _attack_is_permitted(game: Game, obj: GameObject, defender: int) -> bool:
    """Restrictions on attacking (CR 508.1d)."""
    if not can_attack(game, obj):
        return False
    if not _may_be_attacked(game, obj, defender):
        return False
    chars = game.characteristics(obj)
    if chars.has_keyword("Can't attack"):
        return False
    # A goaded creature cannot attack a player who goaded it if another choice
    # exists; the caller has already picked, so just reject the illegal pick.
    if obj.goaded_by and defender in obj.goaded_by:
        others = [p for p in game.opponents(obj.controller) if p not in obj.goaded_by]
        if others:
            return False
    return True


def _may_be_attacked(game: Game, obj: GameObject, defender) -> bool:
    """Whether the declared defender is something this creature may attack.

    CR 506.2 names what is open to attack: the defending player, planeswalkers
    they control, and battles they protect. CR 802.1 is what makes "they" a
    choice rather than the only other player - this game uses the attack
    multiple players option (CR 903.2), so any opponent will do.

    What none of that permits is attacking yourself, a teammate, or a player
    who has already left the game (CR 800.4). Nothing stopped an agent naming
    one, so nothing did.
    """
    player, permanent = _resolve_defender(game, defender)
    if permanent != NO_OBJECT:
        attacked = game.objects.get(permanent)
        if attacked is None or not attacked.is_permanent:
            return False
        # CR 506.2: only a planeswalker or a battle is attackable in its own
        # right. Any other permanent is simply not a legal thing to attack.
        chars = game.characteristics(attacked)
        if not (chars.has_type(CardType.PLANESWALKER) or chars.has_type(CardType.BATTLE)):
            return False
    return player in game.opponents(obj.controller)


def _resolve_defender(game: Game, defender) -> tuple[PlayerId, ObjectId]:
    """Work out who is defending, and what permanent (if any) is under attack.

    Agents may name either a player id or an ``AttackPermanent`` wrapper. A
    bare integer always means a player - never an object - because guessing
    from the number is exactly the ambiguity this avoids.
    """
    if isinstance(defender, AttackPermanent):
        obj = game.objects.get(defender.permanent)
        if obj is not None and obj.is_permanent:
            return obj.controller, obj.id
        return PlayerId(0), NO_OBJECT
    return PlayerId(int(defender)), NO_OBJECT


@dataclass(frozen=True, slots=True)
class AttackPermanent:
    """Declare an attack against a planeswalker or battle rather than a player."""

    permanent: ObjectId


def enter_attacking(
    game: Game, permanent: GameObject, *, like: tuple[ObjectId, ...] = ()
) -> bool:
    """Make a permanent that was just put onto the battlefield an attacker.

    CR 508.4: it was never declared as an attacker, so nothing that triggers on
    a creature attacking sees it, and what it attacks is chosen as it enters
    unless the effect says. Ninjutsu says (CR 702.49c): the same player,
    planeswalker or battle as the creature its cost returned. So the target is
    taken from the first of ``like`` that was attacking - a returned creature's
    old object keeps its place in the combat record, which is last-known
    information about what it attacked - and otherwise it is the first opponent
    still in the game.

    CR 506.3a-c: a noncreature, a creature under anyone but the attacking
    player, or a creature headed for a player or permanent that is gone still
    enters, but is never an attacking creature. Outside the combat phase there
    is no attack to join.

    Nothing marks it unblocked. Put in after blockers are declared, no blocker
    was ever declared for it, which is what unblocked means (CR 506.3d,
    508.4d). Returns whether it is now attacking.
    """
    from ..kernel.enums import Phase

    if game.phase is not Phase.COMBAT or not permanent.is_permanent:
        return False
    if not game.characteristics(permanent).is_creature:
        return False
    if permanent.controller != game.active_player:
        return False

    combat = _combat(game)
    defender: PlayerId | None = None
    attacked = NO_OBJECT
    for object_id in like:
        if object_id in combat.attacking:
            defender = combat.attacking[object_id]
            attacked = combat.attacking_permanent.get(object_id, NO_OBJECT)
            break
    if defender is None:
        opponents = game.opponents(permanent.controller)
        if not opponents:
            return False
        defender = opponents[0]

    if attacked != NO_OBJECT:
        target = game.objects.get(attacked)
        if target is None or not target.is_permanent:
            return False
        target_chars = game.characteristics(target)
        if not (
            target_chars.has_type(CardType.PLANESWALKER)
            or target_chars.has_type(CardType.BATTLE)
        ):
            return False
        # CR 508.4a: and it must still be a defending player's to attack.
        if target.controller == permanent.controller:
            return False
        defender = target.controller
    elif game.player(defender).has_lost:
        return False

    combat.attacking[permanent.id] = defender
    combat.controllers[permanent.id] = permanent.controller
    if attacked != NO_OBJECT:
        combat.attacking_permanent[permanent.id] = attacked
        combat.controllers[attacked] = target.controller
    game.invalidate_characteristics()
    return True


# ---------------------------------------------------------------------------
# Declaring blockers (CR 509)
# ---------------------------------------------------------------------------


def declare_blockers(game: Game) -> None:
    """CR 509.1: each defending player declares blockers, all at once."""
    from .restrictions import enforce_block_requirements

    combat = _combat(game)
    # CR 506.4: anything that stopped belonging in combat while attackers were
    # being declared is out of it before anyone chooses a block, so a creature
    # that is no longer attacking cannot be blocked.
    check_removal_from_combat(game)
    # Whether or not anyone blocks, every attacker is now either blocked or
    # unblocked (CR 509.1h).
    combat.blockers_declared = True
    if not combat.attacking:
        game.emit(Event(EventKind.BLOCKERS_DECLARED, player=game.active_player))
        return

    defenders = sorted(set(combat.attacking.values()))

    for defender_id in defenders:
        agent = game.agent_for(defender_id)
        if agent is None or not hasattr(agent, "declare_blockers"):
            continue
        available = [
            obj
            for obj in game.permanents(defender_id)
            if can_block_at_all(game, obj)
        ]
        proposal = agent.declare_blockers(game, defender_id, combat, available) or {}
        # CR 509.1c: the declaration must obey the greatest possible number of
        # blocking requirements. The defending player's own choices survive
        # wherever they are compatible with that, since the rule constrains
        # how many requirements are obeyed and nothing else.
        proposal = enforce_block_requirements(
            game, combat, defender_id, available, proposal
        )
        for blocker_id, attacker_ids in sorted(proposal.items()):
            blocker = game.objects.get(blocker_id)
            if blocker is None or not can_block_at_all(game, blocker):
                continue
            legal = [
                attacker_id
                for attacker_id in attacker_ids
                if attacker_id in combat.attacking
                and can_block(game, blocker, game.objects[attacker_id])
            ]
            if not legal:
                continue
            combat.blocking[blocker_id] = legal
            # CR 506.4 again: a blocker that changes controller leaves combat.
            combat.controllers[blocker_id] = blocker.controller
            for attacker_id in legal:
                combat.blockers.setdefault(attacker_id, []).append(blocker_id)

    _enforce_menace(game, combat)

    for attacker_id, blockers in combat.blockers.items():
        if blockers:
            combat.was_blocked.add(attacker_id)
            attacker = game.objects.get(attacker_id)
            if attacker is not None:
                attacker.blocked_this_turn = True
            game.emit(
                Event(EventKind.BECOMES_BLOCKED, object_id=attacker_id, amount=len(blockers))
            )
    for blocker_id in sorted(combat.blocking):
        game.emit(Event(EventKind.BLOCKS, object_id=blocker_id))

    game.emit(Event(EventKind.BLOCKERS_DECLARED, player=game.active_player))


def can_block_at_all(game: Game, obj: GameObject) -> bool:
    """CR 509.1a: an untapped creature can block. Summoning sickness does not
    stop blocking - only attacking and {T} abilities."""
    chars = game.characteristics(obj)
    return chars.is_creature and not obj.tapped


def can_block(game: Game, blocker: GameObject, attacker: GameObject) -> bool:
    """Whether this blocker may block this attacker (CR 509.1b).

    Evasion lives here. Every one of these is a restriction on the *blocker's*
    declaration, not something checked later.
    """
    attacker_chars = game.characteristics(attacker)
    blocker_chars = game.characteristics(blocker)

    # CR 101.2 again: "can't block" and "can't be blocked" both win outright.
    from .restrictions import Act, prohibited

    if prohibited(game, Act.BLOCK, obj=blocker, counterpart=attacker) is not None:
        return False
    if prohibited(game, Act.BE_BLOCKED, obj=attacker, counterpart=blocker) is not None:
        return False

    if attacker_chars.has_keyword("Unblockable"):
        return False

    # CR 702.9b: flying can only be blocked by flying or reach.
    if attacker_chars.has_keyword("Flying"):
        if not (blocker_chars.has_keyword("Flying") or blocker_chars.has_keyword("Reach")):
            return False

    # CR 702.20b: shadow can only be blocked by shadow, and blocks only shadow.
    if attacker_chars.has_keyword("Shadow") != blocker_chars.has_keyword("Shadow"):
        if attacker_chars.has_keyword("Shadow") or blocker_chars.has_keyword("Shadow"):
            return False

    # CR 702.5b: horsemanship, the same shape as shadow.
    if attacker_chars.has_keyword("Horsemanship") and not blocker_chars.has_keyword(
        "Horsemanship"
    ):
        return False

    # CR 702.16d: protection stops blocks by creatures with the stated quality.
    from ..cr100_game_concepts.actions import protected_from

    if protected_from(game, attacker, blocker):
        return False

    # CR 702.15b: landwalk. The attacker cannot be blocked at all while the
    # defending player controls a land of the named type - it is a property of
    # the *defender's* board, not of the blocker.
    if _has_unblockable_landwalk(game, attacker, blocker.controller):
        return False

    # CR 702.118b: skulk - can't be blocked by creatures with greater power.
    if attacker_chars.has_keyword("Skulk"):
        attacker_power = attacker_chars.power or 0
        if (blocker_chars.power or 0) > attacker_power:
            return False

    # CR 702.17b: fear - blockable only by artifact and/or black creatures.
    if attacker_chars.has_keyword("Fear"):
        from ..kernel.enums import Color

        if not (
            blocker_chars.has_type(CardType.ARTIFACT) or (blocker_chars.colors & Color.BLACK)
        ):
            return False

    # CR 702.13b: intimidate - artifact creatures, or creatures sharing a color.
    if attacker_chars.has_keyword("Intimidate"):
        if not (
            blocker_chars.has_type(CardType.ARTIFACT)
            or (blocker_chars.colors & attacker_chars.colors)
        ):
            return False

    return True


def _has_unblockable_landwalk(game: Game, attacker: GameObject, defender: PlayerId) -> bool:
    """CR 702.15b: does the defending player control a land of the walked type?"""
    from ..kernel.matching import matches

    chars = game.characteristics(attacker)
    for ability in chars.abilities:
        if not ability.keyword.lower().endswith("walk"):
            continue
        quality = ability.quality
        if quality is None:
            continue
        for permanent in game.permanents(defender):
            if matches(game, permanent, quality):
                return True
    return False


def _enforce_menace(game: Game, combat: Combat) -> None:
    """CR 702.111b: a creature with menace can't be blocked except by two or more.

    A single blocker declared against it is an illegal declaration, so the
    block is removed entirely rather than being allowed through.
    """
    for attacker_id, blockers in list(combat.blockers.items()):
        attacker = game.objects.get(attacker_id)
        if attacker is None:
            continue
        if not game.characteristics(attacker).has_keyword("Menace"):
            continue
        if 0 < len(blockers) < 2:
            for blocker_id in blockers:
                attackers = combat.blocking.get(blocker_id, [])
                if attacker_id in attackers:
                    attackers.remove(attacker_id)
                if not attackers:
                    combat.blocking.pop(blocker_id, None)
            combat.blockers[attacker_id] = []


def _has_banding(game: Game, object_id: ObjectId) -> bool:
    """CR 702.22b: "bands with other" is a special form of banding, and losing
    banding loses those too - so both names answer this question."""
    obj = game.objects.get(object_id)
    if obj is None:
        return False
    chars = game.characteristics(obj)
    return chars.has_keyword("Banding") or any(
        ability.keyword.lower().startswith("bands with other")
        for ability in chars.abilities
    )


def _defending_player(game: Game, combat: Combat, attacker_id: ObjectId) -> PlayerId:
    """Who this creature is attacking, for the banding damage-order swap."""
    defender = combat.attacking.get(attacker_id)
    if defender is not None:
        return defender
    # Attacking a planeswalker or battle instead: the defending player is
    # whoever controls it (CR 506.2b).
    target = combat.attacking_permanent.get(attacker_id)
    if target is not None:
        obj = game.objects.get(target)
        if obj is not None:
            return obj.controller
    return game.active_player


# ---------------------------------------------------------------------------
# Removal from combat (CR 506.4)
# ---------------------------------------------------------------------------


def check_removal_from_combat(game: Game) -> list[ObjectId]:
    """CR 506.4: take out of combat everything that no longer belongs there.

    The conditions are not state-based actions - a permanent leaves combat the
    instant one of them is met, not the next time anyone would get priority -
    but the engine only ever *reads* the combat record at a handful of moments,
    so sweeping at each of them is indistinguishable from removing it as it
    happens. Attackers and blockers are checked against the creature
    conditions; the planeswalkers and battles being attacked are checked
    against their own, because they can hold both roles at once (CR 506.4d).

    Returns the permanents that left combat, for logging and for tests.
    """
    combat = getattr(game, "combat", None)
    if combat is None or not (combat.attacking or combat.blocking):
        return []

    fighters = list(combat.attacking) + list(combat.blocking)
    attacked = [i for i in combat.attacking_permanent.values() if i != NO_OBJECT]
    removed: list[ObjectId] = []

    for object_id in dict.fromkeys(fighters + attacked):
        obj = game.objects.get(object_id)
        if obj is None:
            combat.remove(object_id)
            removed.append(object_id)
            continue
        if _has_left_the_game_state(combat, obj):
            actions.remove_from_combat(game, obj)
            removed.append(object_id)
            continue

        chars = game.characteristics(obj)
        left = False
        # An attacking or blocking creature that stops being a creature, or
        # becomes a battle, is removed from combat.
        if object_id in fighters and not (
            chars.is_creature and not chars.has_type(CardType.BATTLE)
        ):
            combat.stop_attacking_or_blocking(object_id)
            left = True
        # A planeswalker or battle that stops being one stops being attacked.
        if object_id in attacked and not (
            chars.has_type(CardType.PLANESWALKER) or chars.has_type(CardType.BATTLE)
        ):
            combat.stop_being_attacked(object_id)
            left = True
        if left:
            combat.controllers.pop(object_id, None)
            removed.append(object_id)
            game.log.record(game, f"{obj} is removed from combat", kind="combat")
            game.invalidate_characteristics()

    return removed


def _has_left_the_game_state(combat: Combat, obj: GameObject) -> bool:
    """The CR 506.4 conditions that apply to everything in combat at once.

    Leaving the battlefield, phasing out (CR 702.26b treats a phased-out
    permanent as though it did not exist) and changing controller take a
    permanent out of combat whichever role it was filling.
    """
    if not obj.is_permanent or obj.phased_out:
        return True
    was = combat.controllers.get(obj.id)
    return was is not None and was != obj.controller


# ---------------------------------------------------------------------------
# Combat damage (CR 510)
# ---------------------------------------------------------------------------


def deal_combat_damage(game: Game, *, first_strike_step: bool = False) -> None:
    """Assign and deal combat damage (CR 510.1-2).

    All of it is dealt simultaneously, which is why two creatures that kill
    each other both die.
    """
    combat = _combat(game)
    check_removal_from_combat(game)  # CR 506.4
    if not combat.attacking:
        return

    if not first_strike_step and _needs_first_strike_step(game, combat):
        # CR 510.4: an extra damage step, taken before this one.
        previous = game.step
        game.step = Step.FIRST_STRIKE_COMBAT_DAMAGE
        _damage_round(game, combat, first_strike=True)
        game.step = previous
        from ..cr100_game_concepts.cr117_priority import run_priority

        run_priority(game)
        if game.game_over:
            return

    _damage_round(game, combat, first_strike=False)


def _needs_first_strike_step(game: Game, combat: Combat) -> bool:
    for object_id in list(combat.attacking) + list(combat.blocking):
        obj = game.objects.get(object_id)
        if obj is None:
            continue
        chars = game.characteristics(obj)
        if chars.has_keyword("First strike") or chars.has_keyword("Double strike"):
            return True
    return False


def _deals_damage_now(game: Game, obj: GameObject, first_strike: bool) -> bool:
    """Which creatures deal damage in which of the two damage steps."""
    chars = game.characteristics(obj)
    has_first = chars.has_keyword("First strike")
    has_double = chars.has_keyword("Double strike")
    if first_strike:
        return has_first or has_double
    # In the normal step, first strikers have already dealt theirs; double
    # strikers deal damage again (CR 702.4b).
    return has_double or not has_first


def _damage_round(game: Game, combat: Combat, *, first_strike: bool) -> None:
    """One damage step: work out every assignment, then deal it all at once."""
    # CR 506.4 once more: the first-strike step and the priority round after it
    # kill creatures, steal them and turn them into noncreatures, and none of
    # that is allowed to still be assigning damage in the second step.
    check_removal_from_combat(game)
    assignments: list[tuple[GameObject, object, int, bool, bool]] = []
    # CR 702.19b: lethal damage is checked against what other creatures are
    # assigning in this same step, so the running total is carried along.
    marked: dict[ObjectId, int] = {}

    def record(
        made: list[tuple[GameObject, object, int, bool, bool]]
    ) -> list[tuple[GameObject, object, int, bool, bool]]:
        for _, who, amount, _, _ in made:
            if isinstance(who, GameObject):
                marked[who.id] = marked.get(who.id, 0) + amount
        return made

    for attacker_id in sorted(combat.attacking):
        attacker = game.objects.get(attacker_id)
        if attacker is None or not attacker.is_permanent:
            continue
        if not _deals_damage_now(game, attacker, first_strike):
            continue
        assignments.extend(record(_attacker_assignment(game, combat, attacker, marked)))

    for blocker_id in sorted(combat.blocking):
        blocker = game.objects.get(blocker_id)
        if blocker is None or not blocker.is_permanent:
            continue
        if not _deals_damage_now(game, blocker, first_strike):
            continue
        assignments.extend(record(_blocker_assignment(game, combat, blocker, marked)))

    from ..cr700_additional_rules.cr725_designations import (
        combat_damage_to_initiative_holder,
        combat_damage_to_monarch,
    )

    for source, target, amount, deathtouch, lifelink in assignments:
        if isinstance(target, int) and amount > 0:
            # CR 725.4 / 726.4: combat damage to the holder passes it on. Done
            # before the damage so it is not skipped when the damage kills them.
            combat_damage_to_monarch(game, PlayerId(target), source.controller)
            combat_damage_to_initiative_holder(game, PlayerId(target), source.controller)
        actions.deal_damage(
            game,
            target,
            amount,
            source=source.id,
            source_controller=source.controller,
            deathtouch=deathtouch,
            lifelink=lifelink,
            combat=True,
            is_commander_source=source.is_commander,
        )


def _attacker_assignment(
    game: Game,
    combat: Combat,
    attacker: GameObject,
    marked: dict[ObjectId, int] | None = None,
) -> list[tuple[GameObject, object, int, bool, bool]]:
    """How one attacking creature divides its combat damage (CR 510.1b-c)."""
    chars = game.characteristics(attacker)
    power = chars.power or 0
    # CR 510.1a: a creature that would assign 0 or less assigns no damage.
    if power <= 0:
        return []

    deathtouch = chars.has_keyword("Deathtouch")
    lifelink = chars.has_keyword("Lifelink")
    trample = chars.has_keyword("Trample")

    blockers = [
        game.objects[b]
        for b in combat.blockers.get(attacker.id, [])
        if b in game.objects and game.objects[b].is_permanent
    ]

    if not blockers:
        # CR 509.1h: a creature that was blocked stays blocked, and assigns no
        # damage at all once its blockers are gone - trample is the exception,
        # since it had damage to spill over in the first place.
        if combat.is_blocked(attacker.id) and not trample:
            return []
        target = _damage_recipient(game, combat, attacker)
        if target is None:
            return []
        return [(attacker, target, power, deathtouch, lifelink)]

    # CR 702.19b: trample lets the excess reach what the creature is attacking,
    # so that is one more thing the damage may be divided among. It always
    # comes last in ``recipients``, which is what ``spill`` indexes.
    recipients: list[object] = list(blockers)
    spill = -1
    if trample:
        target = _damage_recipient(game, combat, attacker)
        if target is not None:
            spill = len(recipients)
            recipients.append(target)

    # CR 510.1c: the attacking creature's controller divides the damage.
    # CR 702.22j: a blocker with banding hands that choice to the defender.
    chooser = attacker.controller
    if any(_has_banding(game, blocker.id) for blocker in blockers):
        chooser = _defending_player(game, combat, attacker.id)

    lethal = [
        lethal_damage(game, blocker, deathtouch=deathtouch, marked=marked)
        for blocker in blockers
    ]
    division = _divide(
        game,
        chooser,
        attacker,
        recipients=recipients,
        total=power,
        default=_lethal_first(power, lethal, spill),
        legal=lambda choice: _attacker_division_is_legal(
            choice, len(recipients), power, lethal, spill
        ),
    )
    return _as_assignments(attacker, recipients, division, deathtouch, lifelink)


def _lethal_first(total: int, lethal: list[int], spill: int) -> dict[int, int]:
    """Lethal to each creature in turn, then the rest onward.

    Any division is legal (CR 510.1c/d); this one is merely the deterministic
    default a replay needs, and it is the division that satisfies CR 702.19b
    when the attacking creature has trample.
    """
    division: dict[int, int] = {}
    remaining = total
    for index, needed in enumerate(lethal):
        if remaining <= 0:
            break
        assigned = min(remaining, needed)
        if assigned > 0:
            division[index] = assigned
            remaining -= assigned
    if remaining <= 0:
        return division
    last = spill if spill >= 0 else len(lethal) - 1
    if last >= 0:
        division[last] = division.get(last, 0) + remaining
    return division


def _attacker_division_is_legal(
    division: dict[int, int],
    count: int,
    total: int,
    lethal: list[int],
    spill: int,
) -> bool:
    """CR 510.1c and, when the creature tramples, CR 702.19b."""
    if not _division_covers(division, count, total):
        return False
    # CR 702.19b: nothing reaches the player, planeswalker, or battle until
    # every blocking creature has been assigned lethal damage.
    if spill >= 0 and division.get(spill, 0) > 0:
        return all(
            division.get(index, 0) >= needed for index, needed in enumerate(lethal)
        )
    return True


def _division_covers(division: dict[int, int], count: int, total: int) -> bool:
    """Every point goes somewhere it is allowed to go (CR 510.1c, 510.1d)."""
    if any(not isinstance(k, int) or not 0 <= k < count for k in division):
        return False
    if any(not isinstance(v, int) or v < 0 for v in division.values()):
        return False
    return sum(division.values()) == total


def lethal_damage(
    game: Game,
    creature: GameObject,
    *,
    deathtouch: bool = False,
    marked: dict[ObjectId, int] | None = None,
) -> int:
    """What counts as lethal damage when checking an assignment (CR 702.19b).

    Damage already marked on the creature counts, and so does damage other
    creatures are assigning during this same combat damage step - which is why
    two attackers can between them get a blocker to lethal and both still
    trample over it. Abilities and effects that would change how much damage is
    actually dealt are deliberately not considered.

    CR 702.2b: deathtouch makes any nonzero amount lethal.
    """
    if deathtouch:
        return 1
    chars = game.characteristics(creature)
    already = creature.damage + (marked or {}).get(creature.id, 0)
    return max(1, (chars.toughness or 0) - already)


def _blocker_assignment(
    game: Game,
    combat: Combat,
    blocker: GameObject,
    marked: dict[ObjectId, int] | None = None,
) -> list[tuple[GameObject, object, int, bool, bool]]:
    """How one blocking creature divides its combat damage (CR 510.1d)."""
    chars = game.characteristics(blocker)
    power = chars.power or 0
    # CR 510.1a.
    if power <= 0:
        return []
    deathtouch = chars.has_keyword("Deathtouch")
    lifelink = chars.has_keyword("Lifelink")

    attackers = [
        game.objects[a]
        for a in combat.blocking.get(blocker.id, [])
        if a in game.objects and game.objects[a].is_permanent
    ]
    # CR 510.1d: blocking nothing any more means assigning nothing.
    if not attackers:
        return []
    if len(attackers) == 1:
        return [(blocker, attackers[0], power, deathtouch, lifelink)]

    # CR 510.1d: divided among the creatures it is blocking, as its controller
    # chooses. There is no lethal-first requirement here.
    lethal = [
        lethal_damage(game, attacker, deathtouch=deathtouch, marked=marked)
        for attacker in attackers
    ]
    division = _divide(
        game,
        blocker.controller,
        blocker,
        recipients=list(attackers),
        total=power,
        default=_lethal_first(power, lethal, -1),
        legal=lambda choice: _division_covers(choice, len(attackers), power),
    )
    return _as_assignments(blocker, attackers, division, deathtouch, lifelink)


def _divide(
    game: Game,
    chooser: PlayerId,
    source: GameObject,
    *,
    recipients: list,
    total: int,
    default: dict[int, int],
    legal,
) -> dict[int, int]:
    """Ask whoever is dividing, and hold them to the rules.

    The division comes back keyed by position in ``recipients``, since a
    recipient may be a creature, a player, a planeswalker, or a battle.

    CR 510.1e: an assignment that does not comply is illegal, and the game
    rewinds to before the player began making it. An engine cannot rewind a
    bot's intent, so an illegal division is refused and the legal default
    stands in its place.
    """
    agent = game.agent_for(chooser)
    if agent is None or not hasattr(agent, "assign_combat_damage"):
        return default
    choice = agent.assign_combat_damage(
        game, chooser, source.id, list(recipients), total
    )
    if not isinstance(choice, dict) or not legal(choice):
        return default
    return choice


def _as_assignments(
    source: GameObject,
    recipients: list,
    division: dict[int, int],
    deathtouch: bool,
    lifelink: bool,
) -> list[tuple[GameObject, object, int, bool, bool]]:
    return [
        (source, recipients[index], amount, deathtouch, lifelink)
        for index, amount in sorted(division.items())
        if amount > 0
    ]


def _damage_recipient(game: Game, combat: Combat, attacker: GameObject) -> object:
    """What an unblocked attacker damages: a player, planeswalker, or battle."""
    permanent_id = combat.attacking_permanent.get(attacker.id)
    if permanent_id is not None:
        if permanent_id == NO_OBJECT:
            # CR 506.4c: what it was attacking left combat. It is still an
            # attacking creature, but it attacks nothing, so an unblocked one
            # deals no damage - it is not redirected to the defending player.
            return None
        obj = game.objects.get(permanent_id)
        if obj is not None and obj.is_permanent:
            return obj
        # CR 508.4: if the planeswalker is gone, the attacker is removed from
        # combat and deals no damage at all - not redirected to the player.
        return None

    player_id = combat.attacking.get(attacker.id)
    if player_id is None or game.player(player_id).has_lost:
        return None
    return player_id


# ---------------------------------------------------------------------------
# End of combat (CR 511)
# ---------------------------------------------------------------------------


def end_combat(game: Game) -> None:
    """CR 511.3: everything stops being an attacker or blocker."""
    combat = _combat(game)
    combat.clear()
    game.emit(Event(EventKind.REMOVED_FROM_COMBAT, player=game.active_player))


def _combat(game: Game) -> Combat:
    existing = getattr(game, "combat", None)
    if existing is None:
        existing = Combat()
        game.combat = existing
    return existing
