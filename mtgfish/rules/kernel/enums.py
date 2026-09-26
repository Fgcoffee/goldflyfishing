"""Core enumerations for the rules engine.

Everything here is an ``IntEnum`` or ``IntFlag`` so that at runtime these are
plain integers: cheap to compare, cheap to store, trivially portable to a
native implementation later.

Comprehensive Rules references are cited inline. Where the CR and Scryfall's
data model disagree on naming, the CR wins for engine concepts and Scryfall's
spelling is kept only at the data boundary.
"""

from __future__ import annotations

from enum import IntEnum, IntFlag

# ---------------------------------------------------------------------------
# Colors (CR 105)
# ---------------------------------------------------------------------------


class Color(IntFlag):
    """The five colors of Magic (CR 105.1).

    Colorless is the absence of color, represented by an empty flag - it is
    explicitly *not* a color (CR 105.1). A card with no colored mana symbols in
    its cost and no color indicator is colorless.

    Also used to represent color identity (CR 903.4), which is a distinct
    concept from color but shares the same value space.
    """

    NONE = 0
    WHITE = 1
    BLUE = 2
    BLACK = 4
    RED = 8
    GREEN = 16

    def __iter__(self):
        """Iterate constituent colors in WUBRG order (CR 200.1 canonical)."""
        for c in COLOR_ORDER:
            if self & c:
                yield c

    @property
    def count(self) -> int:
        return bin(int(self)).count("1")


COLOR_ORDER: tuple[Color, ...] = (
    Color.WHITE,
    Color.BLUE,
    Color.BLACK,
    Color.RED,
    Color.GREEN,
)

WUBRG = Color.WHITE | Color.BLUE | Color.BLACK | Color.RED | Color.GREEN

COLOR_LETTERS: dict[Color, str] = {
    Color.WHITE: "W",
    Color.BLUE: "U",
    Color.BLACK: "B",
    Color.RED: "R",
    Color.GREEN: "G",
}

LETTER_TO_COLOR: dict[str, Color] = {v: k for k, v in COLOR_LETTERS.items()}


def colors_from_letters(letters) -> Color:
    """Build a Color flag from an iterable of ``"W"``/``"U"``/... letters.

    Accepts Scryfall's ``colors``/``color_identity`` arrays directly. ``"C"``
    (colorless) contributes nothing, which is correct: colorless is the empty
    set, not a sixth color.
    """
    result = Color.NONE
    for ch in letters:
        ch = ch.upper()
        if ch == "C":
            continue
        try:
            result |= LETTER_TO_COLOR[ch]
        except KeyError:
            raise ValueError(f"unknown color letter {ch!r}") from None
    return result


def color_letters(colors: Color) -> str:
    """Render a Color flag in WUBRG order, e.g. ``Color.BLUE|Color.RED`` -> ``"UR"``."""
    return "".join(COLOR_LETTERS[c] for c in COLOR_ORDER if colors & c)


# ---------------------------------------------------------------------------
# Card types (CR 205.2, 300)
# ---------------------------------------------------------------------------


class CardType(IntFlag):
    """Card types (CR 300.1).

    An object can have several simultaneously (e.g. Artifact Creature), which
    is why this is a flag rather than an enum. Type-changing effects operate in
    layer 4 (CR 613.1d) and may add or remove individual bits.
    """

    NONE = 0

    # Permanent types (CR 110.4a)
    ARTIFACT = 1 << 0
    BATTLE = 1 << 1
    CREATURE = 1 << 2
    ENCHANTMENT = 1 << 3
    LAND = 1 << 4
    PLANESWALKER = 1 << 5

    # Non-permanent card types
    INSTANT = 1 << 6
    SORCERY = 1 << 7
    KINDRED = 1 << 8  # Formerly "Tribal"; renamed by WotC in 2024.

    # Types that only appear in casual variants outside Commander's scope.
    # Modelled so that a card carrying them is recognised and rejected rather
    # than silently mis-typed.
    CONSPIRACY = 1 << 9
    DUNGEON = 1 << 10
    PHENOMENON = 1 << 11
    PLANE = 1 << 12
    SCHEME = 1 << 13
    VANGUARD = 1 << 14

    # Objects that are not cards but carry a type line (CR 114 emblems), and
    # the supplemental-product types. Stickers earns its place: 48 of them are
    # Commander-legal.
    EMBLEM = 1 << 15
    STICKERS = 1 << 16
    HERO = 1 << 17
    EVENT = 1 << 18
    BOSS = 1 << 19
    #: Scryfall gives art-series and some token entries the bare type line
    #: "Card". Never gameplay-relevant, but it must parse.
    CARD = 1 << 20

    def __iter__(self):
        for t in CARD_TYPE_ORDER:
            if self & t:
                yield t


CARD_TYPE_ORDER: tuple[CardType, ...] = (
    CardType.ARTIFACT,
    CardType.BATTLE,
    CardType.BOSS,
    CardType.CARD,
    CardType.CONSPIRACY,
    CardType.CREATURE,
    CardType.DUNGEON,
    CardType.EMBLEM,
    CardType.ENCHANTMENT,
    CardType.EVENT,
    CardType.HERO,
    CardType.INSTANT,
    CardType.KINDRED,
    CardType.LAND,
    CardType.PHENOMENON,
    CardType.PLANE,
    CardType.PLANESWALKER,
    CardType.SCHEME,
    CardType.SORCERY,
    CardType.STICKERS,
    CardType.VANGUARD,
)

#: The six permanent types (CR 110.4a). A spell of any other type never becomes
#: a permanent; it goes to its owner's graveyard as the final step of its
#: resolution (CR 608.2m).
PERMANENT_TYPES = (
    CardType.ARTIFACT
    | CardType.BATTLE
    | CardType.CREATURE
    | CardType.ENCHANTMENT
    | CardType.LAND
    | CardType.PLANESWALKER
)

#: Types that use the stack when played. Lands are played, not cast (CR 305.1),
#: and never use the stack.
SPELL_TYPES = (
    CardType.ARTIFACT
    | CardType.BATTLE
    | CardType.CREATURE
    | CardType.ENCHANTMENT
    | CardType.INSTANT
    | CardType.KINDRED
    | CardType.PLANESWALKER
    | CardType.SORCERY
)

CARD_TYPE_NAMES: dict[str, CardType] = {
    "artifact": CardType.ARTIFACT,
    "battle": CardType.BATTLE,
    "boss": CardType.BOSS,
    "card": CardType.CARD,
    "conspiracy": CardType.CONSPIRACY,
    "creature": CardType.CREATURE,
    "dungeon": CardType.DUNGEON,
    "emblem": CardType.EMBLEM,
    "enchantment": CardType.ENCHANTMENT,
    "event": CardType.EVENT,
    "hero": CardType.HERO,
    "instant": CardType.INSTANT,
    "kindred": CardType.KINDRED,
    "tribal": CardType.KINDRED,  # Pre-2024 printings.
    "land": CardType.LAND,
    "phenomenon": CardType.PHENOMENON,
    "plane": CardType.PLANE,
    "planeswalker": CardType.PLANESWALKER,
    "scheme": CardType.SCHEME,
    "sorcery": CardType.SORCERY,
    "stickers": CardType.STICKERS,
    "summon": CardType.CREATURE,  # Pre-errata Legends wording.
    "vanguard": CardType.VANGUARD,
}

TYPE_DISPLAY_NAMES: dict[CardType, str] = {
    CardType.ARTIFACT: "Artifact",
    CardType.BATTLE: "Battle",
    CardType.BOSS: "Boss",
    CardType.CARD: "Card",
    CardType.CONSPIRACY: "Conspiracy",
    CardType.CREATURE: "Creature",
    CardType.DUNGEON: "Dungeon",
    CardType.EMBLEM: "Emblem",
    CardType.ENCHANTMENT: "Enchantment",
    CardType.EVENT: "Event",
    CardType.HERO: "Hero",
    CardType.INSTANT: "Instant",
    CardType.KINDRED: "Kindred",
    CardType.LAND: "Land",
    CardType.PHENOMENON: "Phenomenon",
    CardType.PLANE: "Plane",
    CardType.PLANESWALKER: "Planeswalker",
    CardType.SCHEME: "Scheme",
    CardType.SORCERY: "Sorcery",
    CardType.STICKERS: "Stickers",
    CardType.VANGUARD: "Vanguard",
}


# ---------------------------------------------------------------------------
# Supertypes (CR 205.4)
# ---------------------------------------------------------------------------


class Supertype(IntFlag):
    """Supertypes (CR 205.4a)."""

    NONE = 0
    BASIC = 1 << 0
    LEGENDARY = 1 << 1
    ONGOING = 1 << 2  # Archenemy schemes only.
    SNOW = 1 << 3
    WORLD = 1 << 4
    #: Scryfall marks token entries with a Token supertype. Not a real
    #: supertype in the CR - tokens are distinguished by being tokens
    #: (CR 111) - but it must parse.
    TOKEN = 1 << 5
    # Un-set supertypes. Present so their type lines parse rather than throw;
    # these cards are filtered out of the Commander-legal pool anyway.
    HOST = 1 << 6
    ELITE = 1 << 7

    def __iter__(self):
        for t in SUPERTYPE_ORDER:
            if self & t:
                yield t


SUPERTYPE_ORDER: tuple[Supertype, ...] = (
    Supertype.BASIC,
    Supertype.ELITE,
    Supertype.LEGENDARY,
    Supertype.ONGOING,
    Supertype.SNOW,
    Supertype.TOKEN,
    Supertype.WORLD,
    Supertype.HOST,
)

SUPERTYPE_NAMES: dict[str, Supertype] = {
    "basic": Supertype.BASIC,
    "elite": Supertype.ELITE,
    "legendary": Supertype.LEGENDARY,
    "ongoing": Supertype.ONGOING,
    "snow": Supertype.SNOW,
    "token": Supertype.TOKEN,
    "world": Supertype.WORLD,
    "host": Supertype.HOST,
}

SUPERTYPE_DISPLAY_NAMES: dict[Supertype, str] = {
    Supertype.BASIC: "Basic",
    Supertype.ELITE: "Elite",
    Supertype.LEGENDARY: "Legendary",
    Supertype.ONGOING: "Ongoing",
    Supertype.SNOW: "Snow",
    Supertype.TOKEN: "Token",
    Supertype.WORLD: "World",
    Supertype.HOST: "Host",
}


# ---------------------------------------------------------------------------
# Zones (CR 400)
# ---------------------------------------------------------------------------


class Zone(IntEnum):
    """The seven zones (CR 400.1).

    Library, hand, graveyard, and exile are private-ish per player; battlefield,
    stack, and command are shared. Whether a zone is ordered matters: library,
    graveyard, and the stack are ordered (CR 400.3); hand, battlefield, exile,
    and command are not.
    """

    LIBRARY = 0
    HAND = 1
    BATTLEFIELD = 2
    GRAVEYARD = 3
    STACK = 4
    EXILE = 5
    COMMAND = 6


#: Zones whose contents have a meaningful order (CR 400.3). The graveyard is
#: ordered even though players may look through it freely, because some effects
#: care about the order cards were put there.
ORDERED_ZONES = frozenset({Zone.LIBRARY, Zone.GRAVEYARD, Zone.STACK})

#: Zones owned by an individual player. The battlefield, stack, and command
#: zone are shared by all players (CR 403.1, 405.1, 408.1).
PLAYER_ZONES = frozenset({Zone.LIBRARY, Zone.HAND, Zone.GRAVEYARD})

#: Zones in which a face-up object is visible to every player.
PUBLIC_ZONES = frozenset({Zone.BATTLEFIELD, Zone.STACK, Zone.GRAVEYARD, Zone.COMMAND})


# ---------------------------------------------------------------------------
# Turn structure (CR 500-514)
# ---------------------------------------------------------------------------


class Phase(IntEnum):
    """The five phases of a turn (CR 500.1)."""

    BEGINNING = 0
    PRECOMBAT_MAIN = 1
    COMBAT = 2
    POSTCOMBAT_MAIN = 3
    ENDING = 4


class Step(IntEnum):
    """Steps within phases (CR 500.1).

    Main phases have no steps; ``Step.MAIN`` is a synthetic value so that the
    engine can address every point in the turn uniformly as a (phase, step)
    pair.

    ``FIRST_STRIKE_COMBAT_DAMAGE`` exists only when a creature with first or
    double strike is in combat (CR 510.4); the engine inserts it dynamically.
    """

    UNTAP = 0
    UPKEEP = 1
    DRAW = 2
    MAIN = 3
    BEGINNING_OF_COMBAT = 4
    DECLARE_ATTACKERS = 5
    DECLARE_BLOCKERS = 6
    FIRST_STRIKE_COMBAT_DAMAGE = 7
    COMBAT_DAMAGE = 8
    END_OF_COMBAT = 9
    END_STEP = 10
    CLEANUP = 11


#: Steps in which no player receives priority (CR 502.4, 514.3). Note that the
#: cleanup step normally grants no priority, but does if a state-based action is
#: performed or a triggered ability is waiting - handled in turn.py.
NO_PRIORITY_STEPS = frozenset({Step.UNTAP, Step.CLEANUP})

#: The combat steps (CR 506.1).
COMBAT_STEPS = frozenset(
    {
        Step.BEGINNING_OF_COMBAT,
        Step.DECLARE_ATTACKERS,
        Step.DECLARE_BLOCKERS,
        Step.FIRST_STRIKE_COMBAT_DAMAGE,
        Step.COMBAT_DAMAGE,
        Step.END_OF_COMBAT,
    }
)


class Timing(IntEnum):
    """When a player may take an action.

    ``SORCERY`` means the CR 307.1 timing restriction: only during a main phase
    of your own turn, when the stack is empty and you have priority.
    """

    SORCERY = 0
    INSTANT = 1


# ---------------------------------------------------------------------------
# Continuous effects (CR 611, 613)
# ---------------------------------------------------------------------------


class Layer(IntEnum):
    """Layers and sublayers of the continuous-effect system (CR 613.1, 613.4).

    Values are spaced so that plain integer ordering is the CR's application
    order. Sublayers of layer 1 (CR 613.2) and layer 7 (CR 613.4) get their own
    members because effects genuinely apply at those granularities.

    Within a layer, effects apply in timestamp order (CR 613.7), except where a
    dependency reorders them (CR 613.8).
    """

    COPY = 10  # 613.2a - copy effects
    FACE_DOWN = 11  # 613.2b - face-down spells and permanents
    CONTROL = 20  # 613.1b
    TEXT = 30  # 613.1c
    TYPE = 40  # 613.1d
    COLOR = 50  # 613.1e
    ABILITY = 60  # 613.1f
    PT_CDA = 70  # 613.4a - characteristic-defining abilities
    PT_SET = 71  # 613.4b - effects that set power/toughness
    PT_MODIFY = 72  # 613.4c - effects that modify but don't set
    PT_COUNTERS = 73  # 613.4d - +1/+1 and -1/-1 counters
    PT_SWITCH = 74  # 613.4e - effects that switch power and toughness


class Duration(IntEnum):
    """How long a continuous effect from a resolved spell or ability lasts.

    ``PERMANENT`` covers effects with no stated duration (CR 611.2b: they last
    indefinitely and do not expire even if their source leaves).
    ``WHILE_SOURCE_PERSISTS`` covers static abilities of permanents, which stop
    applying the moment the source stops existing or stops having the ability.
    """

    PERMANENT = 0
    END_OF_TURN = 1
    END_OF_COMBAT = 2
    YOUR_NEXT_TURN = 3
    END_OF_YOUR_NEXT_TURN = 4
    WHILE_SOURCE_PERSISTS = 5
    CUSTOM = 6


# ---------------------------------------------------------------------------
# Game outcomes
# ---------------------------------------------------------------------------


class LossReason(IntEnum):
    """Why a player left the game (CR 104.3, 704.5a-c, 903.10).

    Numbered from 1 so no member is falsy. A zero-valued member reads as "no
    reason at all" in any ``if reason:`` test, and the most common reason of
    all would have been the one to silently vanish from the statistics.
    """

    LIFE = 1  # 704.5a - life total 0 or less
    EMPTY_LIBRARY = 2  # 704.5b - tried to draw from an empty library
    POISON = 3  # 704.5c - ten or more poison counters
    COMMANDER_DAMAGE = 4  # 903.10 - 21+ from a single commander
    EFFECT = 5  # 104.3b - a spell or ability says the player loses
    CONCEDE = 6  # 104.3a
    DRAW_AGREED = 7


class WinReason(IntEnum):
    """How a game ended, for the statistics layer. Numbered from 1; see above."""

    LAST_PLAYER_STANDING = 1
    ALTERNATE_WIN_CONDITION = 2  # 104.2b - a spell or ability says you win
    STALL_OUT = 3  # Hit the simulation turn cap with nobody dead
    DRAW = 4


# ---------------------------------------------------------------------------
# Card layouts (Scryfall vocabulary, CR 700s semantics)
# ---------------------------------------------------------------------------


class Layout(IntEnum):
    """Physical card layouts.

    The engine addresses *faces*, not cards, so layout mainly determines how
    many faces exist, which one is the default for casting, and what happens on
    the battlefield (CR 709-719).
    """

    NORMAL = 0
    SPLIT = 1  # CR 709 - two halves, either castable
    FLIP = 2  # CR 710 - rotates 180 degrees on the battlefield
    TRANSFORM = 3  # CR 712 - double-faced, transforms in place
    MODAL_DFC = 4  # CR 712.2 - either face may be played from hand
    MELD = 5  # CR 713
    LEVELER = 6  # CR 711
    CLASS = 7  # CR 717
    SAGA = 8  # CR 716
    ADVENTURE = 9  # CR 715
    MUTATE = 10  # CR 722
    PROTOTYPE = 11  # CR 718
    CASE = 12
    ROOM = 13
    BATTLE = 14
    TOKEN = 15
    EMBLEM = 16
    REVERSIBLE = 17  # Purely cosmetic; treat as NORMAL functionally.
    AUGMENT = 18  # Un-sets.
    HOST = 19  # Un-sets.
    ART_SERIES = 20
    DOUBLE_FACED_TOKEN = 21
    PLANAR = 22
    SCHEME = 23
    VANGUARD = 24
    PREPARE = 25  # CR 722 - a prepare spell in an inset frame


LAYOUT_NAMES: dict[str, Layout] = {
    "normal": Layout.NORMAL,
    "split": Layout.SPLIT,
    "flip": Layout.FLIP,
    "transform": Layout.TRANSFORM,
    "modal_dfc": Layout.MODAL_DFC,
    "meld": Layout.MELD,
    "leveler": Layout.LEVELER,
    "class": Layout.CLASS,
    "saga": Layout.SAGA,
    "adventure": Layout.ADVENTURE,
    "prepare": Layout.PREPARE,
    "mutate": Layout.MUTATE,
    "prototype": Layout.PROTOTYPE,
    "case": Layout.CASE,
    "room": Layout.ROOM,
    "battle": Layout.BATTLE,
    "token": Layout.TOKEN,
    "emblem": Layout.EMBLEM,
    "reversible_card": Layout.REVERSIBLE,
    "augment": Layout.AUGMENT,
    "host": Layout.HOST,
    "art_series": Layout.ART_SERIES,
    "double_faced_token": Layout.DOUBLE_FACED_TOKEN,
    "planar": Layout.PLANAR,
    "scheme": Layout.SCHEME,
    "vanguard": Layout.VANGUARD,
}

#: Layouts with two independently-castable faces from hand. For these the
#: engine must offer both faces as separate play options (CR 709.4, 712.2).
DUAL_CASTABLE_LAYOUTS = frozenset({Layout.SPLIT, Layout.MODAL_DFC, Layout.ADVENTURE})

#: Layouts where the back face exists but is never cast directly - it is only
#: reached by transforming or melding (CR 712.7, 713.3).
BACK_FACE_ONLY_LAYOUTS = frozenset({Layout.TRANSFORM, Layout.MELD, Layout.FLIP})
