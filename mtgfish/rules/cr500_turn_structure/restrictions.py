"""Prohibitions and permissions (CR 101.2, 101.3, 601.3).

CR 101.2 is one sentence and it governs the whole game: "When a rule or effect
allows or directs something to happen, and another effect states that it can't
happen, the 'can't' effect takes precedence."

That has to be a general mechanism rather than a check bolted onto each
individual effect. If "can't attack" is a special case inside combat and
"can't be countered" is a special case inside the stack, then the twentieth
prohibition gets forgotten and nobody notices, because a missing prohibition
silently *allows* something rather than crashing.

So every "may I?" question in the engine routes through ``prohibited`` here,
and adding a new prohibition is data rather than code.

CR 101.3 is the mirror image: an instruction that is impossible is ignored,
with no effect unless the card says otherwise. That is why every action
primitive returns what it actually managed to do rather than assuming it
worked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

from ..kernel.enums import CardType
from ..kernel.ids import NO_OBJECT, NO_PLAYER, ObjectId, PlayerId
from ..kernel.query import ALWAYS, Condition, ObjectFilter, PlayerFilter

if TYPE_CHECKING:
    from ..kernel.game import Game
    from ..kernel.gameobject import GameObject


class Act(IntEnum):
    """Things a rule or effect can forbid.

    Deliberately exhaustive rather than minimal: a prohibition the engine has
    no name for is one it cannot enforce, and the failure mode is permissive.
    """

    # -- playing and casting ------------------------------------------------
    CAST_SPELL = 0
    PLAY_LAND = 1
    #: CR 307.1 timing, granted rather than forbidden: "you may cast sorcery
    #: spells as though they had flash". Its own act because a permission to
    #: *ignore timing* is not a permission to cast - the spell still has to be
    #: castable for every other reason.
    CAST_AS_THOUGH_FLASH = 5
    ACTIVATE_ABILITY = 2
    ACTIVATE_MANA_ABILITY = 3
    ACTIVATE_LOYALTY_ABILITY = 4

    # -- combat -------------------------------------------------------------
    ATTACK = 20
    ATTACK_PLAYER = 21
    ATTACK_PLANESWALKER = 22
    BLOCK = 23
    BE_BLOCKED = 24

    # -- the stack ----------------------------------------------------------
    BE_COUNTERED = 40
    BE_TARGETED = 41
    COPY = 42

    # -- permanents ---------------------------------------------------------
    UNTAP = 60
    TAP = 61
    BE_DESTROYED = 62
    BE_SACRIFICED = 63
    BE_REGENERATED = 64
    ENTER_BATTLEFIELD = 65
    LEAVE_BATTLEFIELD = 66
    HAVE_COUNTERS_PLACED = 67
    BE_EXILED = 68
    CHANGE_CONTROL = 69
    PHASE_OUT = 70

    # -- players ------------------------------------------------------------
    DRAW_CARD = 90
    GAIN_LIFE = 91
    LOSE_LIFE = 92
    SEARCH_LIBRARY = 93
    WIN_GAME = 94
    LOSE_GAME = 95
    BE_DEALT_DAMAGE = 96
    SHUFFLE = 97
    CAST_MORE_THAN_ONE_SPELL = 98
    UNTAP_DURING_UNTAP_STEP = 99
    #: "Your life total can't change" - not a ban on gaining and a ban on
    #: losing but one prohibition on the total moving at all, which is why it
    #: is a single act rather than a pair.
    LIFE_TOTAL_CHANGE = 101
    #: "Play with the top card of your library revealed" / "you may look at
    #: the top card of your library any time". A permission to see, which the
    #: play-from-the-top permissions are written alongside but separate from.
    LOOK_AT_TOP_CARD = 102
    #: CR 402.2 / 514.1: discarding down to the maximum hand size in cleanup.
    #: Expressed as something a player may be *permitted* to skip, because
    #: "you have no maximum hand size" is a permission, not a prohibition.
    DISCARD_TO_HAND_SIZE = 100


@dataclass(frozen=True, slots=True)
class Restriction:
    """One "can't" in force.

    ``subject`` and ``players`` say who or what it applies to; ``None`` means
    the restriction's own source. ``condition`` gates it, for the "can't attack
    unless..." shape.
    """

    act: Act
    subject: ObjectFilter | None = None
    players: PlayerFilter | None = None
    source: ObjectId = NO_OBJECT
    controller: PlayerId = NO_PLAYER
    condition: Condition = ALWAYS
    #: A restriction that names something specific: "can't be blocked by
    #: creatures with power 2 or less" constrains the *other* participant.
    counterpart: ObjectFilter | None = None
    text: str = ""
    #: ``rules.kernel.enums.Duration``, for a prohibition registered by a resolved
    #: spell rather than regenerated from a static ability. Held as an int to
    #: keep this module from importing the enum, and only consulted for
    #: standing entries - one rebuilt from a permanent ends when the permanent
    #: does and needs no duration of its own.
    duration: int = 0
    #: The turn the prohibition started on; see ``ContinuousEffect``.
    created_turn: int = 0

    def __str__(self) -> str:
        return self.text or f"can't {self.act.name.lower().replace('_', ' ')}"

    @property
    def act_phrase(self) -> str:
        """The act alone, with any leading negation stripped.

        The same Restriction is used for a prohibition and for a permission,
        so a caller composing "X can't ..." or "X may ..." needs the verb
        without a "can't" already attached - otherwise the explainer says
        "can't can't be regenerated".
        """
        text = self.text or self.act.name.lower().replace("_", " ")
        # "doesn't untap during its controller's untap step" is written as a
        # negation too, and left alone it came back out as "can't doesn't
        # untap ..." - noise in the one place a reviewer is reading closely.
        for prefix in ("can't ", "cannot ", "may not ", "doesn't ", "does not "):
            if text.lower().startswith(prefix):
                return text[len(prefix) :]
        return text


@dataclass(slots=True)
class RestrictionSet:
    """Every prohibition currently in force.

    Rebuilt from static abilities the same way continuous effects are, so a
    prohibition ends the instant its source stops existing.
    """

    entries: list[Restriction] = field(default_factory=list)

    def add(self, restriction: Restriction) -> None:
        self.entries.append(restriction)

    def clear(self) -> None:
        self.entries.clear()


def prohibited(
    game: Game,
    act: Act,
    *,
    obj: GameObject | None = None,
    player: PlayerId = NO_PLAYER,
    counterpart: GameObject | None = None,
) -> Restriction | None:
    """Whether ``act`` is forbidden right now, and by what.

    Returns the restriction so the caller can log *why* something was not
    allowed, which matters enormously when a simulated game does something
    surprising.
    """
    from ..kernel.conditions import holds
    from ..kernel.matching import matches, resolve_players

    for restriction in _active(game):
        if restriction.act is not act:
            continue

        if restriction.subject is not None:
            if obj is None:
                continue
            if not matches(
                game,
                obj,
                restriction.subject,
                source=restriction.source,
                controller=restriction.controller,
            ):
                continue
        elif restriction.source != NO_OBJECT and obj is not None:
            if obj.id != restriction.source:
                continue

        if restriction.players is not None:
            target_player = player
            if target_player == NO_PLAYER and obj is not None:
                target_player = obj.controller
            if target_player == NO_PLAYER:
                continue
            allowed = resolve_players(
                game, restriction.players, controller=restriction.controller
            )
            if target_player not in allowed:
                continue

        if restriction.counterpart is not None:
            if counterpart is None:
                continue
            if not matches(
                game,
                counterpart,
                restriction.counterpart,
                source=restriction.source,
                controller=restriction.controller,
            ):
                continue

        if not restriction.condition.is_always:
            if not holds(
                game,
                restriction.condition,
                source=restriction.source,
                controller=restriction.controller,
            ):
                continue

        return restriction

    return None


def allowed(game: Game, act: Act, **kwargs) -> bool:
    """Convenience inverse of ``prohibited``."""
    return prohibited(game, act, **kwargs) is None


def _active(game: Game) -> list[Restriction]:
    """Restrictions in force, rebuilt from live static abilities.

    Regenerated rather than persisted, for the same reason continuous effects
    are (CR 611.3): a prohibition from a permanent that has left the
    battlefield is not a prohibition any more.
    """
    cached = game.restrictions_cache
    if cached is not None and game.restrictions_epoch == game.epoch:
        return cached

    from ..cr600_spells_and_abilities.abilities import AbilityKind
    from ..cr600_spells_and_abilities.effects import EffectKind


    out: list[Restriction] = list(game.standing_restrictions)
    # CR 604.3: a static ability can function from a zone other than the
    # battlefield. Split second is the reason this loop includes the stack -
    # its prohibition applies precisely while the spell is *on* the stack, and
    # a battlefield-only scan would never see it.
    scope = list(game.battlefield) + list(game.stack)
    for object_id in scope:
        obj = game.objects.get(object_id)
        if obj is None or obj.phased_out:
            continue
        for ability in game.characteristics(obj).abilities:
            if ability.kind is not AbilityKind.STATIC or ability.unparsed:
                continue
            if not ability.functions_in_zone(obj.zone):
                continue
            for effect in ability.effects:
                if effect.kind is not EffectKind.RESTRICTION:
                    continue
                for restriction in effect.restrictions:
                    out.append(
                        Restriction(
                            act=restriction.act,
                            subject=restriction.subject or effect.targets,
                            players=restriction.players or effect.players,
                            source=obj.id,
                            controller=obj.controller,
                            condition=ability.static_condition,
                            counterpart=restriction.counterpart,
                            text=restriction.text or effect.text,
                        )
                    )

    game.restrictions_cache = out
    game.restrictions_epoch = game.epoch
    return out


def permitted(
    game: Game,
    act: Act,
    *,
    obj: GameObject | None = None,
    player: PlayerId = NO_PLAYER,
) -> Restriction | None:
    """Whether an effect *permits* ``act`` that the rules would otherwise deny.

    The mirror image of ``prohibited``, and the half that was missing. CR 113.6
    permissions - "you may cast sorceries as though they had flash", "you may
    cast this from your graveyard" - are not prohibitions with a "not" in
    front; they are their own layer, and without them the rules stay maximally
    restrictive and Vedalken Orrery does nothing at all.

    Returns the permission so the caller can log *why* something unusual was
    allowed, for the same reason ``prohibited`` returns the restriction.
    """
    from ..kernel.conditions import holds
    from ..kernel.matching import matches, resolve_players

    for permission in _active_permissions(game):
        if permission.act is not act:
            continue

        if permission.subject is not None:
            if obj is None:
                continue
            if not matches(
                game,
                obj,
                permission.subject,
                source=permission.source,
                controller=permission.controller,
            ):
                continue

        if permission.players is not None and player != NO_PLAYER:
            allowed = resolve_players(
                game, permission.players, controller=permission.controller
            )
            if player not in allowed:
                continue

        if not holds(
            game,
            permission.condition,
            source=permission.source,
            controller=permission.controller,
        ):
            continue
        return permission
    return None


def _active_permissions(game: Game) -> list[Restriction]:
    """Permissions in force, rebuilt from live static abilities.

    Rebuilt rather than persisted for the same reason prohibitions are
    (CR 611.3): a permission from a permanent that has left the battlefield is
    not a permission any more.
    """
    cached = game.permissions_cache
    if cached is not None and game.permissions_epoch == game.epoch:
        return cached

    from ..cr600_spells_and_abilities.abilities import AbilityKind
    from ..cr600_spells_and_abilities.effects import EffectKind

    out: list[Restriction] = list(game.standing_permissions)
    for object_id in list(game.battlefield) + list(game.stack):
        obj = game.objects.get(object_id)
        if obj is None or obj.phased_out:
            continue
        for ability in game.characteristics(obj).abilities:
            if ability.kind is not AbilityKind.STATIC or ability.unparsed:
                continue
            if not ability.functions_in_zone(obj.zone):
                continue
            for effect in ability.effects:
                if effect.kind is not EffectKind.PERMISSION:
                    continue
                for permission in effect.restrictions:
                    out.append(
                        Restriction(
                            act=permission.act,
                            subject=permission.subject or effect.targets,
                            players=permission.players or effect.players,
                            source=obj.id,
                            controller=obj.controller,
                            condition=ability.static_condition,
                            counterpart=permission.counterpart,
                            text=permission.text or effect.text,
                        )
                    )

    game.permissions_cache = out
    game.permissions_epoch = game.epoch
    return out


def register_standing_permission(game: Game, permission: Restriction) -> Restriction:
    """Add a permission not tied to a permanent's static ability.

    Used by resolved spells with a duration - "you may cast spells this turn
    as though they had flash" - which persist independently of any source.
    """
    from dataclasses import replace

    if not permission.created_turn:
        permission = replace(permission, created_turn=game.turn)
    game.standing_permissions.append(permission)
    game.permissions_epoch = -1
    return permission


def register_standing(game: Game, restriction: Restriction) -> Restriction:
    """Add a prohibition that is not tied to a permanent's static ability.

    Used by resolved spells with a duration ("creatures can't attack this
    turn"), which persist independently of any source (CR 611.2b).
    """
    from dataclasses import replace

    if not restriction.created_turn:
        restriction = replace(restriction, created_turn=game.turn)
    game.standing_restrictions.append(restriction)
    game.restrictions_cache = None
    return restriction


# ---------------------------------------------------------------------------
# Requirements (CR 508.1e, 509.1c)
# ---------------------------------------------------------------------------
#
# A requirement is not a prohibition with the sign flipped, and it must not be
# modelled as one. A prohibition is answered one act at a time - "may this
# creature block that one?" - and CR 101.2 makes the answer absolute. A
# requirement is answered about the declaration *as a whole*: CR 509.1c makes a
# block legal only if no other legal block would have obeyed more requirements,
# so "must this creature block?" has no answer until every other creature's
# choice is on the table too.
#
# That is why these live beside the prohibitions rather than inside them, and
# why the enforcement below is a search rather than a loop. Everything else
# follows the module's habit: requirements are data, so a new "must block"
# is a new entry and not a new special case.


#: The engine's spellings of the blocking requirements, the same way
#: ``"Attacks each combat if able"`` is the engine's spelling of the attacking
#: one (CR 508.1e). Keyword strings rather than ``Act`` values, because a
#: requirement is a different mechanism from a prohibition and sharing the
#: vocabulary is how the two got confused in the first place.
BLOCKS_IF_ABLE = "Blocks each combat if able"
MUST_BE_BLOCKED = "Must be blocked if able"
BLOCKED_BY_ALL_ABLE = "All creatures able to block it do so"

#: "This creature" - the requirement's own source. Used for the *other*
#: participant, where ``None`` already means "anything".
SOURCE_OBJECT = ObjectFilter(source_only=True)

#: No effect in the rules lets one creature block an unbounded number of
#: attackers, but nothing in CR 509.1 caps the number of blockers an attacker
#: may have either, so the upper bound is only ever the board.
_UNBOUNDED = 1 << 30

#: How many partial declarations the CR 509.1c search will look at before it
#: settles for the best it has found. A board where this runs out is one with
#: a dozen possible blockers and several attackers; the answer it returns is
#: still a legal declaration obeying at least as many requirements as the
#: defending player asked for, it is simply no longer provably maximal.
_SEARCH_BUDGET = 200_000


@dataclass(frozen=True, slots=True)
class Requirement:
    """One "must" in force (CR 508.1e, 509.1c).

    ``act`` is what has to happen, reusing the vocabulary of ``Act`` so that
    "must block" and "can't block" name the same act. ``subject`` is who is
    under the obligation - the blocker for ``Act.BLOCK``, the attacker for
    ``Act.BE_BLOCKED`` - and ``None`` means the requirement's own source, the
    same convention prohibitions use.

    ``counterpart`` constrains the other participant: for ``Act.BLOCK`` which
    attackers would satisfy it, for ``Act.BE_BLOCKED`` which creatures would.
    ``None`` means any, and ``SOURCE_OBJECT`` means the source itself, which is
    what "all creatures able to block *this creature* do so" needs.

    One requirement is generated per object matching ``subject``. That is not
    an implementation convenience - it is how the rules count. Lure's
    requirement is a separate requirement for each creature able to block, so
    a declaration in which one of them stays home obeys strictly fewer.
    """

    act: Act
    subject: ObjectFilter | None = None
    counterpart: ObjectFilter | None = None
    source: ObjectId = NO_OBJECT
    controller: PlayerId = NO_PLAYER
    condition: Condition = ALWAYS
    text: str = ""

    def __str__(self) -> str:
        return self.text or f"must {self.act.name.lower().replace('_', ' ')}"


#: Keyword ability -> the requirement it creates. The table is the mechanism:
#: a card that says something new about blocking adds a row, and the search
#: below never learns its name.
_BLOCK_REQUIREMENT_KEYWORDS: dict[str, Requirement] = {
    BLOCKS_IF_ABLE.lower(): Requirement(
        act=Act.BLOCK, text="blocks each combat if able"
    ),
    MUST_BE_BLOCKED.lower(): Requirement(
        act=Act.BE_BLOCKED, text="must be blocked if able"
    ),
    BLOCKED_BY_ALL_ABLE.lower(): Requirement(
        act=Act.BLOCK,
        subject=ObjectFilter(types_all=CardType.CREATURE),
        counterpart=SOURCE_OBJECT,
        text="all creatures able to block it do so",
    ),
}


@dataclass(frozen=True, slots=True)
class BlockObligation:
    """One requirement, resolved against the creatures actually in combat.

    ``blockers`` and ``attackers`` are the pairs that would obey it; an empty
    tuple means "any". All three shapes the rules use collapse into this:

    ==================================== ============== =================
    requirement                          blockers       attackers
    ==================================== ============== =================
    "this creature blocks if able"       (that creature) ()
    "this creature must be blocked"      ()              (that attacker)
    "all creatures able to block it do"  (one of them)   (that attacker)
    ==================================== ============== =================

    Collapsing them is what lets the search count requirements without knowing
    which kind of effect produced any of them.
    """

    blockers: tuple[ObjectId, ...] = ()
    attackers: tuple[ObjectId, ...] = ()
    text: str = ""

    def satisfied(self, assignment: dict[ObjectId, ObjectId]) -> bool:
        """Whether a blocker -> attacker declaration obeys this."""
        for blocker, attacker in assignment.items():
            if self.blockers and blocker not in self.blockers:
                continue
            if self.attackers and attacker not in self.attackers:
                continue
            return True
        return False

    def reachable(self, blockers: frozenset[ObjectId], options: dict) -> bool:
        """Whether any of ``blockers`` could still obey it.

        The search's only pruning: a requirement no remaining creature could
        possibly obey cannot raise the count, so a branch that has already
        fallen behind the best declaration found is abandoned.
        """
        for blocker in blockers:
            if self.blockers and blocker not in self.blockers:
                continue
            for attacker in options.get(blocker, ()):
                if not self.attackers or attacker in self.attackers:
                    return True
        return False


def requirements(game: Game) -> list[Requirement]:
    """Every "must" in force, rebuilt from live permanents.

    Regenerated rather than persisted for the reason ``_active`` is: a
    requirement from a permanent that has left the battlefield is not a
    requirement any more (CR 611.3).

    ``game.standing_requirements`` is read defensively so that requirements
    from a resolved spell rather than a permanent - "creatures block this turn
    if able" - need only that one field to start working.
    """
    from dataclasses import replace

    out: list[Requirement] = list(getattr(game, "standing_requirements", ()))
    for object_id in list(game.battlefield):
        obj = game.objects.get(object_id)
        if obj is None or obj.phased_out:
            continue
        for ability in game.characteristics(obj).abilities:
            if not ability.keyword:
                continue
            template = _BLOCK_REQUIREMENT_KEYWORDS.get(ability.keyword.strip().lower())
            if template is None:
                continue
            out.append(
                replace(
                    template,
                    source=obj.id,
                    controller=obj.controller,
                    condition=ability.static_condition,
                )
            )
    return out


def _named(
    game: Game,
    pool: list[GameObject],
    spec: ObjectFilter | None,
    requirement: Requirement,
) -> tuple[ObjectId, ...] | None:
    """Which of ``pool`` a requirement's filter names, or ``None`` for "unsaid".

    ``None`` is not an empty answer: an unset filter means "the source itself"
    for a subject and "anything" for a counterpart, and the two are decided by
    the caller. An *empty tuple* means the filter was set and matched nothing.
    """
    if spec is None:
        return None
    from ..kernel.matching import matches

    return tuple(
        obj.id
        for obj in pool
        if matches(
            game,
            obj,
            spec,
            source=requirement.source,
            controller=requirement.controller,
        )
    )


def block_obligations(
    game: Game,
    combat,
    defender: PlayerId,
    candidates: list[GameObject],
) -> list[BlockObligation]:
    """The requirements CR 509.1c will count, for one defending player.

    A requirement whose counterpart names creatures that are not in this
    combat is dropped rather than recorded as unsatisfiable: no legal
    declaration obeys it, so it cannot change which declaration obeys the most.
    """
    from ..kernel.conditions import holds

    attackers = [
        game.objects[attacker_id]
        for attacker_id in sorted(combat.attacking)
        if combat.attacking[attacker_id] == defender and attacker_id in game.objects
    ]
    if not attackers or not candidates:
        return []

    out: list[BlockObligation] = []
    for requirement in requirements(game):
        if requirement.act not in (Act.BLOCK, Act.BE_BLOCKED):
            continue
        if not requirement.condition.is_always and not holds(
            game,
            requirement.condition,
            source=requirement.source,
            controller=requirement.controller,
        ):
            continue

        # Whoever is under the obligation, and whoever would satisfy it. For a
        # "must block" the subject is the blocker and the counterpart picks the
        # attackers; for a "must be blocked" the two swap round.
        if requirement.act is Act.BLOCK:
            subject_pool, other_pool = candidates, attackers
        else:
            subject_pool, other_pool = attackers, candidates

        subjects = _named(game, subject_pool, requirement.subject, requirement)
        if subjects is None:
            subjects = tuple(
                obj.id for obj in subject_pool if obj.id == requirement.source
            )
        others = _named(game, other_pool, requirement.counterpart, requirement)
        # A counterpart that names nothing in this combat can never be
        # obeyed, so it is not a requirement any declaration could obey.
        if others is not None and not others:
            continue

        for subject_id in subjects:
            if requirement.act is Act.BLOCK:
                obligation = BlockObligation(
                    blockers=(subject_id,),
                    attackers=others or (),
                    text=requirement.text,
                )
            else:
                obligation = BlockObligation(
                    blockers=others or (),
                    attackers=(subject_id,),
                    text=requirement.text,
                )
            out.append(obligation)
    return out


def enforce_block_requirements(
    game: Game,
    combat,
    defender: PlayerId,
    candidates: list[GameObject],
    proposal: dict[ObjectId, list[ObjectId]] | dict,
) -> dict[ObjectId, list[ObjectId]]:
    """Apply CR 509.1b-c to a proposed declaration of blockers.

    Returns a declaration that obeys every restriction and the greatest number
    of requirements any legal declaration could obey. The rules say an
    inadequate declaration is simply illegal and the game rewinds (CR 509.1,
    733); an engine with no player to ask again has to produce the legal
    declaration itself, which is exactly what ``_enforce_attack_requirements``
    does on the attacking side.

    The defending player's own choices are honoured wherever they are
    compatible with obeying the maximum - both in which creatures block and in
    which attacker each one blocks - because CR 509.1c constrains how *many*
    requirements are obeyed and nothing else.

    A board with no requirements on it returns the proposal untouched, which
    is the overwhelmingly common case and costs one list scan.

    CR 509.1c's cost clause is deliberately not implemented: a defending player
    is never required to pay a cost to block, so a creature that can't block
    unless a cost is paid is treated as one that does not block.
    """
    from .cr506_combat import can_block, can_block_at_all

    blockers = [
        obj
        for obj in candidates
        if obj.controller == defender and can_block_at_all(game, obj)
    ]
    obligations = block_obligations(game, combat, defender, blockers)
    if not obligations:
        return proposal

    attackers = [
        game.objects[attacker_id]
        for attacker_id in sorted(combat.attacking)
        if combat.attacking[attacker_id] == defender and attacker_id in game.objects
    ]
    # CR 509.1b: every pair the declaration may use, restrictions applied.
    options: dict[ObjectId, tuple[ObjectId, ...]] = {}
    for blocker in blockers:
        legal = tuple(
            attacker.id for attacker in attackers if can_block(game, blocker, attacker)
        )
        if legal:
            options[blocker.id] = legal
    bounds = {attacker.id: _block_count_bounds(game, attacker) for attacker in attackers}

    preferred: dict[ObjectId, ObjectId | None] = {}
    for blocker_id, legal in options.items():
        chosen = None
        for attacker_id in proposal.get(blocker_id, ()) or ():
            if attacker_id in legal:
                chosen = attacker_id
                break
        preferred[blocker_id] = chosen

    assignment = _maximise_blocks(obligations, options, bounds, preferred)

    result: dict[ObjectId, list[ObjectId]] = {}
    for blocker_id, attacker_id in sorted(assignment.items()):
        declared = list(proposal.get(blocker_id, ()) or ())
        if attacker_id in declared and len(declared) > 1:
            # A multiple block the defending player declared through some
            # effect that allows it. CR 509.1c has nothing to say about it, so
            # it is left exactly as declared.
            result[blocker_id] = declared
        else:
            result[blocker_id] = [attacker_id]
    return result


def _block_count_bounds(game: Game, attacker: GameObject) -> tuple[int, int]:
    """How many creatures may block this attacker at once.

    The declaration-wide half of CR 509.1b: menace (CR 702.111b) forbids a
    block by exactly one creature, which no pairwise "may this creature block
    that one?" question can express - each blocker is individually fine and the
    declaration is still illegal.
    """
    minimum = 2 if game.characteristics(attacker).has_keyword("Menace") else 1
    return minimum, _UNBOUNDED


def _blocks_are_legal(assignment: dict[ObjectId, ObjectId], bounds: dict) -> bool:
    """CR 509.1b, applied to a whole declaration."""
    counts: dict[ObjectId, int] = {}
    for attacker_id in assignment.values():
        counts[attacker_id] = counts.get(attacker_id, 0) + 1
    for attacker_id, count in counts.items():
        low, high = bounds.get(attacker_id, (1, _UNBOUNDED))
        if not low <= count <= high:
            return False
    return True


def _maximise_blocks(
    obligations: list[BlockObligation],
    options: dict[ObjectId, tuple[ObjectId, ...]],
    bounds: dict[ObjectId, tuple[int, int]],
    preferred: dict[ObjectId, ObjectId | None],
) -> dict[ObjectId, ObjectId]:
    """The CR 509.1c maximisation: the legal block obeying the most requirements.

    A search rather than a rule of thumb, because every rule of thumb is wrong
    on the example the rule itself prints. Blocking with the creature that must
    block can be *illegal* (menace), and blocking with a creature under no
    requirement at all can be what makes the requirement obeyable - so no
    decision here can be made one creature at a time.

    Creatures are tried in the order the defending player's own declaration
    suggests, so the first declaration reaching the maximum is the one closest
    to what they asked for. Branches that could no longer beat the best
    declaration found are abandoned, and the whole search is capped: a board
    wide enough to exhaust the cap gets the best declaration found so far,
    which is still legal.
    """
    order = sorted(
        options, key=lambda blocker_id: (preferred.get(blocker_id) is None, blocker_id)
    )
    total = len(obligations)

    best: dict[ObjectId, ObjectId] = {}
    best_count = 0
    if _blocks_are_legal({}, bounds):
        best_count = _count_obeyed(obligations, {})

    assignment: dict[ObjectId, ObjectId] = {}
    remaining = [frozenset(order[index:]) for index in range(len(order) + 1)]
    budget = _SEARCH_BUDGET

    def walk(index: int) -> None:
        nonlocal best, best_count, budget
        if budget <= 0 or best_count >= total:
            return
        budget -= 1

        if index == len(order):
            if not _blocks_are_legal(assignment, bounds):
                return
            obeyed = _count_obeyed(obligations, assignment)
            if obeyed > best_count:
                best_count = obeyed
                best = dict(assignment)
            return

        # CR 509.1c counts requirements, so a branch whose best conceivable
        # count cannot beat what is already in hand is not worth walking.
        ceiling = _count_obeyed(obligations, assignment)
        free = remaining[index]
        for obligation in obligations:
            if obligation.satisfied(assignment):
                continue
            if obligation.reachable(free, options):
                ceiling += 1
        if ceiling <= best_count:
            return

        blocker_id = order[index]
        choices: list[ObjectId | None] = []
        chosen = preferred.get(blocker_id)
        if chosen is not None:
            choices.append(chosen)
        choices.append(None)
        for attacker_id in options[blocker_id]:
            if attacker_id != chosen:
                choices.append(attacker_id)

        for choice in choices:
            if choice is None:
                assignment.pop(blocker_id, None)
            else:
                assignment[blocker_id] = choice
            walk(index + 1)
            assignment.pop(blocker_id, None)

    walk(0)
    return best


def _count_obeyed(
    obligations: list[BlockObligation], assignment: dict[ObjectId, ObjectId]
) -> int:
    return sum(1 for obligation in obligations if obligation.satisfied(assignment))


def register_standing_requirement(game: Game, requirement: Requirement) -> Requirement:
    """Add a requirement that is not tied to a permanent's static ability.

    The mirror of ``register_standing``, for a resolved spell whose requirement
    outlives its source - "creatures block this turn if able". It needs a
    ``standing_requirements`` list on ``Game`` to hold them; until that field
    exists the call is refused rather than silently dropping the requirement,
    because a requirement that quietly does nothing is the failure mode this
    whole module exists to avoid.
    """
    store = getattr(game, "standing_requirements", None)
    if store is None:
        raise NotImplementedError(
            "Game has no standing_requirements list; requirements from resolved "
            "spells need one before they can be registered"
        )
    store.append(requirement)
    return requirement
