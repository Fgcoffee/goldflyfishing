"""Game objects (CR 109).

An object is "an ability on the stack, a card, a copy of a card, a token, a
spell, a permanent, or an emblem". One class covers all of them, matching the
CR rather than inventing a hierarchy the rules do not have - and dispatching on
an integer discriminator instead of isinstance keeps the priority loop fast.

The single most important rule modelled here is CR 400.7: an object that moves
from one zone to another becomes a *new object* with no memory of the old one.
Counters fall off, auras fall off, damage clears, and any effect that referred
to the old object loses track of it. That is why zone changes allocate a fresh
``ObjectId`` rather than mutating in place, and it is what makes "blink it in
response" behave correctly without special-casing.

Status (tapped, flipped, face-down, phased-out) lives here rather than in
Characteristics, because CR 110.5 makes it a separate concept: status survives
effects that change characteristics, and changes independently of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from ..cr200_parts_of_a_card.characteristics import Characteristics
from ..cr600_spells_and_abilities.abilities import Ability
from .enums import Zone
from .ids import NO_OBJECT, NO_PLAYER, ObjectId, PlayerId, Timestamp


class ObjectKind(IntEnum):
    """What sort of object this is.

    ``CARD`` covers a card anywhere; it becomes a ``SPELL`` while on the stack
    and a permanent while on the battlefield, but those are the same underlying
    object, so the distinction the engine needs is only "was this ever a card".
    """

    CARD = 0
    TOKEN = 1  # CR 111
    ABILITY = 2  # An ability on the stack (CR 113.7)
    EMBLEM = 3  # CR 114
    COPY = 4  # A copy of a spell (CR 707.10)


@dataclass(slots=True)
class GameObject:
    """One object in one zone."""

    id: ObjectId
    kind: ObjectKind = ObjectKind.CARD
    owner: PlayerId = NO_PLAYER
    controller: PlayerId = NO_PLAYER
    #: CR 109.4: who would control this absent any control-changing effect -
    #: the player who put it onto the battlefield, which is not always the
    #: owner. Layer 2 recomputes ``controller`` from this every time the board
    #: is evaluated, so control *returns* when Act of Treason wears off
    #: instead of the theft being permanent.
    base_controller: PlayerId = NO_PLAYER
    zone: Zone = Zone.LIBRARY

    #: The printed card this object came from, and which face is up. Tokens and
    #: emblems have no card, and carry their characteristics directly.
    card: object | None = None
    face_index: int = 0
    #: Objects tapped, sacrificed or otherwise consumed to pay this object's
    #: cost. Station needs it - "put charge counters equal to *that
    #: creature's* power" is a question about the creature the cost tapped,
    #: which nothing else in the resolution knows about.
    cost_paid_objects: list = field(default_factory=list)

    #: CR 613.7: orders continuous effects. Refreshed when a permanent enters
    #: the battlefield, and again whenever an Aura or Equipment attaches to it.
    timestamp: Timestamp = Timestamp(0)

    # -- status (CR 110.5) --------------------------------------------------
    tapped: bool = False
    flipped: bool = False
    face_down: bool = False
    #: CR 708.2, 708.6: what made this object face down - "Morph", "Disguise",
    #: "Manifest", "Cloak" and so on, or empty for an effect that listed no
    #: characteristics. It decides what the face-down object is (disguise and
    #: cloak add ward {2}) and how it may be turned face up again (CR 701.40b
    #: only for a manifested or cloaked permanent).
    face_down_by: str = ""
    phased_out: bool = False

    # -- battlefield state --------------------------------------------------
    #: Damage marked on a creature. Cleared during cleanup (CR 514.2), which is
    #: why it is tracked separately from toughness.
    damage: int = 0
    #: Whether any of the marked damage was from a deathtouch source, which
    #: makes it lethal regardless of amount (CR 704.5h).
    dealt_deathtouch_damage: bool = False
    counters: dict[str, int] = field(default_factory=dict)
    #: CR 301.5, 303.4: what this Equipment or Aura is attached to.
    attached_to: ObjectId = NO_OBJECT
    attachments: list[ObjectId] = field(default_factory=list)
    #: Damage prevention shields and regeneration shields pending this turn.
    regeneration_shields: int = 0
    #: CR 701.38: players this creature has been goaded by. A goaded creature
    #: must attack, and must attack someone other than a player who goaded it.
    goaded_by: set = field(default_factory=set)
    #: CR 310.9: the player designated as this battle's protector. A
    #: designation rather than a characteristic - it is not copiable, and a
    #: zone change drops it because CR 400.7 builds a fresh object that takes
    #: the default. CR 310.9d: while the battle is attacked, every rule that
    #: says "defending player" means this player, not the controller.
    protector: PlayerId = NO_PLAYER
    #: CR 702.26: a permanent with phasing phases out during its controller's
    #: untap step and back in the following one.
    phasing_scheduled: bool = False
    #: For "attacked this turn" and "was blocked this turn" queries, which
    #: outlive the combat they happened in.
    attacked_this_turn: bool = False
    blocked_this_turn: bool = False
    #: CR 602.5b: activations of once-per-turn abilities, keyed by ability
    #: index. Cleared as each turn begins, and reset when the object changes
    #: zones, since it becomes new (CR 400.7).
    activations_this_turn: dict = field(default_factory=dict)
    #: CR 602.5b: "activate only once" is a limit over the object's whole
    #: existence, not a turn, so it is counted separately and never cleared.
    activations_ever: dict = field(default_factory=dict)

    #: The object this one replaced, when it arrived via a zone change. The
    #: mirror of ``superseded_by``. CR 400.7 makes them different objects, but a
    #: dies-trigger's source is the graveyard object while its subject is the
    #: battlefield one it replaced, so "this ability's own source" has to be
    #: able to span the change.
    previous_id: ObjectId = NO_OBJECT
    #: Set when a zone change replaced this object with a new one (CR 400.7).
    #: The old object survives so last-known information and already-emitted
    #: events can still resolve, but it is no longer the live object.
    superseded_by: ObjectId = NO_OBJECT
    #: CR 302.6: set when the permanent enters the battlefield or changes
    #: control, and cleared for the active player's permanents at the start of
    #: their turn. That is exactly the rule - "controlled continuously since
    #: their most recent turn began" - without having to reconstruct when each
    #: player's last turn was.
    summoning_sick: bool = True
    #: For "entered the battlefield this turn" queries, which is a different
    #: question from summoning sickness.
    entered_battlefield_turn: int = -1

    # -- stack objects ------------------------------------------------------
    #: For ABILITY objects: the ability, and the object it came from.
    ability: Ability | None = None
    source: ObjectId = NO_OBJECT
    #: Targets chosen at announcement (CR 601.2c). One tuple per targeting
    #: effect, in the order the effects appear.
    targets: tuple = ()
    #: For a triggered ability on the stack: the event that triggered it.
    #: CR 603.3d - the ability exists independently of its source once it has
    #: triggered, so anything it needs to know about the event has to travel
    #: with it. "Create that many Treasures" is unanswerable otherwise, since
    #: by the time it resolves the damage is long over.
    trigger_event: object | None = None
    #: What this permanent's controller chose for it - a creature type, a
    #: card type, or a colour (CR 614.1b). Kept on the object because that is
    #: how long the choice lasts: it is made as the permanent enters and the
    #: permanent is what every "of the chosen type" sentence asks.
    #: Whether this permanent arrived by being cast, rather than by being
    #: put onto the battlefield. "When this enters, *if you cast it*" turns
    #: on the difference and nothing recorded it.
    was_cast: bool = False
    chosen_type: str = ""
    chosen_color: int = 0
    #: CR 201.4: the card name this object's controller chose. A *name*, not a
    #: card - the chosen name may match nothing in the game, which is the
    #: whole point of naming a card your opponent has not played yet.
    chosen_name: str = ""
    #: CR 715.3, 718.3, 720.3: which set of characteristics this object was
    #: played with. Chosen as the card is played and then fixed, which is why
    #: it is state rather than something recomputed - and it decides where an
    #: Adventure or an Omen goes when it resolves (CR 715.3d, 720.3d).
    #: A ``cr300_card_types.CastMode``, held as an int to keep this module
    #: from importing it.
    cast_mode: int = 0
    #: CR 715.3d and the "exile it, you may play it" effects generally: while
    #: this card stays in this zone, the named player may play it from there.
    #: NO_PLAYER means nobody, which is the ordinary case. A zone change
    #: builds a fresh object (CR 400.7) and so clears it, which is exactly the
    #: rule's "for as long as that card remains exiled".
    playable_from_here_by: PlayerId = NO_PLAYER
    #: Which face that permission covers. CR 715.3d lets the *creature* be
    #: cast, not the Adventure again, so a permission that offered every face
    #: would let a player loop the Adventure half for ever.
    playable_face: int = 0
    #: Modes chosen at announcement (CR 700.2).
    chosen_modes: tuple[int, ...] = ()
    #: The value chosen for X (CR 601.2b).
    x_value: int = 0
    #: True when this spell was cast without paying its mana cost, or by an
    #: alternative cost - some abilities care.
    cast_without_paying: bool = False
    #: CR 601.2b: which optional additional costs were chosen, by index. Read
    #: by kicker and anything else that asks how this spell was cast.
    additional_costs_paid: tuple[int, ...] = ()
    #: CR 601.2b / 118.9: the keyword of the alternative cost this spell was
    #: cast for, if any - what "if its sneak cost was paid" asks. Empty for a
    #: spell cast for its mana cost.
    alternative_cost_paid: str = ""
    #: CR 722.3c: for the copy of a prepare spell waiting in exile, the
    #: prepared permanent it belongs to.
    prepare_copy_of: ObjectId = NO_OBJECT
    #: CR 701.64b: the harnessed designation. It lasts until the permanent
    #: leaves the battlefield, which CR 400.7 gives by making a new object.
    harnessed: bool = False
    #: CR 702.190b: a spell whose alternative cost puts it onto the battlefield
    #: tapped and attacking, and the objects paid for it, whose attack it
    #: joins (CR 506.3a).
    enters_tapped_and_attacking: bool = False
    enters_attacking_like: tuple = ()
    #: How much mana was actually spent casting this spell (CR 601.2g). Zero
    #: for a free cast, which is what Lavinia and friends key off.
    mana_spent: int = 0

    # -- Commander (CR 903.3) -----------------------------------------------
    is_commander: bool = False

    # -- caching ------------------------------------------------------------
    #: Characteristics after the layer system has run. Invalidated whenever the
    #: continuous-effect set or any timestamp changes; never written directly.
    _characteristics: Characteristics | None = None
    _characteristics_epoch: int = -1
    #: CR 613.2: the copiable values, which depend only on the card, the face
    #: and whether it is flipped - never on the board. Cached per object and
    #: keyed by that triple, because it was being rebuilt on every layer
    #: recomputation and the layer system recomputes constantly.
    _printed: object = None
    _printed_key: tuple = ()

    # -- queries ------------------------------------------------------------

    @property
    def is_live(self) -> bool:
        """Whether this is still the current object, not a superseded husk.

        A superseded object keeps its old zone on purpose so last-known
        information survives, so "is it on the battlefield?" must be asked as
        ``is_live and zone is BATTLEFIELD`` rather than by the zone alone.
        """
        return self.superseded_by == NO_OBJECT

    @property
    def is_permanent(self) -> bool:
        """CR 110.1: a permanent is an object on the battlefield."""
        return self.is_live and self.zone is Zone.BATTLEFIELD

    @property
    def is_spell(self) -> bool:
        """CR 112.1: a card on the stack, as opposed to an ability. CR 112.1a
        makes a copy of a spell a spell too, even with no card behind it."""
        return self.zone is Zone.STACK and self.kind is not ObjectKind.ABILITY

    @property
    def is_token(self) -> bool:
        return self.kind is ObjectKind.TOKEN

    @property
    def is_ability_on_stack(self) -> bool:
        return self.kind is ObjectKind.ABILITY

    #: The kind that means "any kind at all", for "a creature with a counter
    #: on it" and for Modified (CR 701.48), neither of which names one.
    ANY_COUNTER = "*"

    def counter_count(self, kind: str) -> int:
        if kind == self.ANY_COUNTER:
            return sum(self.counters.values())
        return self.counters.get(kind, 0)

    def add_counters(self, kind: str, amount: int) -> int:
        """Add counters, returning how many were actually added."""
        if amount <= 0:
            return 0
        self.counters[kind] = self.counters.get(kind, 0) + amount
        return amount

    def remove_counters(self, kind: str, amount: int) -> int:
        """Remove up to ``amount`` counters, returning how many came off."""
        have = self.counters.get(kind, 0)
        taken = min(have, max(0, amount))
        if taken:
            remaining = have - taken
            if remaining:
                self.counters[kind] = remaining
            else:
                del self.counters[kind]
        return taken

    def entered_this_turn(self, current_turn: int) -> bool:
        return self.entered_battlefield_turn == current_turn

    def invalidate_printed(self) -> None:
        """Drop the copiable-values cache.

        Only a face change or a flip can make it stale; everything else that
        changes an object is a continuous effect, which is layered on top of
        this rather than part of it.
        """
        self._printed = None
        self._printed_key = ()

    def invalidate(self) -> None:
        """Drop cached characteristics. Called when anything could change them."""
        self._characteristics = None
        self._characteristics_epoch = -1

    def __str__(self) -> str:
        chars = self._characteristics
        name = chars.name if chars is not None else _printed_name(self)
        bits = [f"#{self.id}", name or "(object)"]
        if self.tapped:
            bits.append("(tapped)")
        if self.face_down:
            bits.append("(face down)")
        if self.phased_out:
            bits.append("(phased out)")
        if self.damage:
            bits.append(f"({self.damage} damage)")
        if self.counters:
            bits.append("(" + ", ".join(f"{n} {k}" for k, n in sorted(self.counters.items())) + ")")
        return " ".join(bits)


def _printed_name(obj: GameObject) -> str:
    card = obj.card
    if card is None:
        return ""
    try:
        return card.faces[obj.face_index].name
    except (AttributeError, IndexError):
        return getattr(card, "name", "")
