"""The keyword registry (CR 701, 702) and its coverage status.

Every keyword ability, keyword action, and ability word that exists is listed
here with an honest implementation status. Two reasons, and neither is
paperwork:

**Nothing is silently ignored.** An unregistered keyword is a hole in the
engine. ``tests/rules/test_keywords.py`` checks this registry against
Scryfall's own catalogs, so a new set introducing a new keyword fails the test
suite by name rather than producing a card that quietly does less than it says.

**The coverage report is honest.** A deck full of ``DECLARED`` keywords will
simulate, but its numbers are worth less than one full of ``IMPLEMENTED`` ones,
and the user is entitled to know which they have.

``IMPLEMENTED`` means the engine enforces the rule today. ``PARTIAL`` means it
enforces a simplified version, described in the note. ``DECLARED`` means the
keyword is recognised and catalogued but has no behaviour yet - a card with it
still exists, still has the right cost and types, and simply does not do the
keyword's thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Status(IntEnum):
    #: The engine enforces this rule.
    IMPLEMENTED = 0
    #: A simplified version is enforced; see the note.
    PARTIAL = 1
    #: Recognised and catalogued, but with no behaviour yet.
    DECLARED = 2


class Category(IntEnum):
    EVASION = 0  # Restricts who can block
    COMBAT = 1  # Changes how combat damage works
    STATIC = 2  # A continuous effect on the permanent itself
    TRIGGERED = 3  # Shorthand for a triggered ability
    ACTIVATED = 4  # Shorthand for an activated ability
    ALTERNATIVE_COST = 5  # CR 118.9 - cast it a different way
    ADDITIONAL_COST = 6  # CR 601.2b - pay more alongside the mana cost
    COST_MODIFIER = 7  # Changes what the spell costs
    REPLACEMENT = 8  # CR 614 - modifies an event before it happens
    SPELL_MODIFIER = 9  # Changes how the spell itself works
    ZONE = 10  # Functions from somewhere other than the battlefield
    LANDWALK = 11  # CR 702.15
    STATE = 12  # Ongoing game state (phasing, day/night)
    DECK_BUILDING = 13  # Affects deck legality rather than play
    OTHER = 14


@dataclass(frozen=True, slots=True)
class KeywordSpec:
    name: str
    category: Category
    status: Status
    rule: str = ""
    note: str = ""

    @property
    def key(self) -> str:
        return self.name.lower()


def _build(entries: dict[tuple[Category, Status, str], list[str]]) -> dict[str, KeywordSpec]:
    out: dict[str, KeywordSpec] = {}
    for (category, status, note), names in entries.items():
        for name in names:
            out[name.lower()] = KeywordSpec(name, category, status, note=note)
    return out


# ---------------------------------------------------------------------------
# Keyword abilities (CR 702)
# ---------------------------------------------------------------------------

KEYWORD_ABILITIES: dict[str, KeywordSpec] = _build(
    {
        (Category.EVASION, Status.IMPLEMENTED, "enforced in combat.can_block"): [
            "Flying",
            "Fear",
            "Horsemanship",
            "Intimidate",
            "Menace",
            "Shadow",
        ],
        (Category.EVASION, Status.DECLARED, ""): [
            "Skulk", "Banding",
            "Sneak",
            "Web-slinging",
        ],
        (Category.EVASION, Status.IMPLEMENTED, "lets a blocker stop fliers"): ["Reach"],
        (Category.COMBAT, Status.IMPLEMENTED, "enforced in combat damage"): [
            "Deathtouch",
            "Double strike",
            "First strike",
            "Trample",
            "Lifelink",
            "Vigilance",
        ],
        (Category.COMBAT, Status.IMPLEMENTED, "cannot attack"): ["Defender"],
        (Category.COMBAT, Status.DECLARED, ""): [
            "Banding",
            "Bushido",
            "Flanking",
            "Rampage",
            "Melee",
            "Mentor",
            "Battle Cry",
            "Exalted",
            "Annihilator",
            "Afflict",
            "Dethrone",
            "Frenzy",
            "Provoke",
            "Training",
            "Undaunted",
            "Myriad",
            "Double team",
            "Toxic",
            "Poisonous",
            "Infect",
            "Wither",
            "Renown",
            "Enlist",
            "Mobilize",
            "Teamwork",
            "Firebending",
            "Increment",
            "Intensity",
        ],
        (Category.STATIC, Status.IMPLEMENTED, "cannot be destroyed"): ["Indestructible"],
        (Category.STATIC, Status.IMPLEMENTED, "cannot be attacked with the turn it lands"): [
            "Haste"
        ],
        (Category.STATIC, Status.IMPLEMENTED, "targeting restriction"): [
            "Hexproof",
            "Shroud",
        ],
        (
            Category.STATIC,
            Status.IMPLEMENTED,
            "quality-aware: the filter is consulted for targeting, blocking, "
            "damage and enchanting, so protection from red does not stop white",
        ): ["Protection", "Hexproof from"],
        (Category.STATIC, Status.IMPLEMENTED, "instant-speed casting"): ["Flash"],
        (Category.STATIC, Status.DECLARED, ""): [
            "Changeling",
            "Devoid",
            "Split second",
            "Living metal",
            "Living weapon",
            "Umbra armor",
            "Decayed",
            "Compleated",
            "Solved",
            "Paradigm",
            "Max speed",
            "Power-up",
            "Epic",
        ],
        (Category.STATIC, Status.DECLARED, "ward is a cost, not a targeting ban"): [
            "Ward"
        ],
        (Category.LANDWALK, Status.DECLARED, ""): [
            "Landwalk",
            "Plainswalk",
            "Islandwalk",
            "Swampwalk",
            "Mountainwalk",
            "Forestwalk",
            "Desertwalk",
            "Legendary landwalk",
            "Nonbasic landwalk",
        ],
        (Category.ACTIVATED, Status.DECLARED, ""): [
            "Equip",
            "Fortify",
            "Cycling",
            "Basic landcycling",
            "Landcycling",
            "Typecycling",
            "Plainscycling",
            "Islandcycling",
            "Swampcycling",
            "Mountaincycling",
            "Forestcycling",
            "Slivercycling",
            "Wizardcycling",
            "Level Up",
            "Crew",
            "Reconfigure",
            "Outlast",
            "Ninjutsu",
            "Commander ninjutsu",
            "Transmute",
            "Transfigure",
            "Unearth",
            "Scavenge",
            "Reinforce",
            "Morph",
            "Megamorph",
            "Disguise",
            "Boast",
            "Channel",
            "Forecast",
            "Aura Swap",
            "Specialize",
            "Saddle",
            "Station",
            "Exhaust",
            "Start your engines!",
            "Harmonize",
            "Craft",
            "Job select",
        ],
        (Category.ALTERNATIVE_COST, Status.DECLARED, ""): [
            "Flashback",
            "Madness",
            "Evoke",
            "Overload",
            "Escape",
            "Foretell",
            "Dash",
            "Blitz",
            "Prowl",
            "Surge",
            "Emerge",
            "Freerunning",
            "Jump-start",
            "Retrace",
            "Aftermath",
            "Disturb",
            "Embalm",
            "Eternalize",
            "Encore",
            "Spectacle",
            "Mayhem",
            "Bestow",
            "Mutate",
            "Prototype",
            "Miracle",
            "Warp",
            "Impending",
            "Offering",
            "Assist",
            "Cleave",
            "Gift",
        ],
        (Category.ADDITIONAL_COST, Status.DECLARED, ""): [
            "Kicker",
            "Multikicker",
            "Casualty",
            "Buyback",
            "Entwine",
            "Escalate",
            "Replicate",
            "Conspire",
            "Splice",
            "Squad",
            "Spree",
            "Tiered",
            "Bargain",
            "Offspring",
        ],
        (Category.COST_MODIFIER, Status.DECLARED, ""): [
            "Affinity",
            "Convoke",
            "Delve",
            "Improvise",
        ],
        (Category.TRIGGERED, Status.DECLARED, ""): [
            "Cascade",
            "Storm",
            "Gravestorm",
            "Prowess",
            "Evolve",
            "Extort",
            "Exploit",
            "Haunt",
            "Persist",
            "Undying",
            "Soulbond",
            "Soulshift",
            "Champion",
            "Ripple",
            "Recover",
            "Rebound",
            "Tribute",
            "Unleash",
            "Riot",
            "Afterlife",
            "Demonstrate",
            "Read Ahead",
            "Ravenous",
            "Backup",
            "For Mirrodin!",
            "Double agenda",
            "Hidden agenda",
            "Hideaway",
            "Ingest",
            "Absorb",
            "Awaken",
            "Cipher",
            "Fuse",
            "More Than Meets the Eye",
            "Augment",
        ],
        (Category.REPLACEMENT, Status.DECLARED, ""): [
            "Amplify",
            "Bloodthirst",
            "Devour",
            "Fabricate",
            "Graft",
            "Modular",
            "Sunburst",
            "Vanishing",
            "Fading",
            "Suspend",
            "Echo",
            "Cumulative upkeep",
            "Dredge",
        ],
        (Category.STATE, Status.DECLARED, ""): [
            "Phasing",
            "Daybound",
            "Nightbound",
            "Ascend",
        ],
        (Category.STATIC, Status.IMPLEMENTED, "an Aura's attachment restriction"): [
            "Enchant"
        ],
        (
            Category.DECK_BUILDING,
            Status.IMPLEMENTED,
            "validated during deck construction",
        ): [
            "Partner",
            "Partner with",
            "Friends forever",
            "Choose a background",
            "Doctor's companion",
        ],
        (Category.DECK_BUILDING, Status.DECLARED, ""): ["Companion"],
    }
)


# ---------------------------------------------------------------------------
# Keyword actions (CR 701)
# ---------------------------------------------------------------------------

KEYWORD_ACTIONS: dict[str, KeywordSpec] = _build(
    {
        (Category.OTHER, Status.IMPLEMENTED, "implemented in rules/cr100_game_concepts/actions.py"): [
            "Attach",
            "Cast",
            "Counter",
            "Create",
            "Destroy",
            "Discard",
            "Exile",
            "Fight",
            "Mill",
            "Play",
            "Sacrifice",
            "Shuffle",
            "Tap",
            "Untap",
            "Activate",
            "Goad",
            "Proliferate",
            "Regenerate",
        ],
        (Category.OTHER, Status.DECLARED, ""): [
            "Abandon",
            "Adapt",
            "Airbend",
            "Amass",
            "Assemble",
            "Assimilate",
            "Behold",
            "Blight",
            "Bolster",
            "Clash",
            "Cloak",
            "Collect evidence",
            "Conjure",
            "Connive",
            "Convert",
            "Detain",
            "Discover",
            "Double",
            "Draft from a spellbook",
            "Earthbend",
            "Endure",
            "Exchange",
            "Exert",
            "Explore",
            "Face a dilemma",
            "Fateseal",
            "Food",
            "Forage",
            "Harness",
            "Heal",
            "Heist",
            "Incorporate",
            "Incubate",
            "Investigate",
            "Learn",
            "Manifest",
            "Manifest dread",
            "Meld",
            "Monstrosity",
            "Open an Attraction",
            "Planeswalk",
            "Plot",
            "Populate",
            "Prepared",
            "Reveal",
            "Role token",
            "Roll to Visit Your Attractions",
            "Scry",
            "Seek",
            "Set in motion",
            "Support",
            "Surveil",
            "Suspect",
            "Time Travel",
            "Transform",
            "Treasure",
            "Triple",
            "Venture into the dungeon",
            "Vote",
            "Waterbend",
        ],
    }
)


# ---------------------------------------------------------------------------
# Ability words (CR 207.2c)
# ---------------------------------------------------------------------------

#: Ability words have no rules meaning at all - they are italic flavour that
#: groups similar abilities. They are listed so the parser can strip them
#: without treating them as unknown text, and never need implementing.
ABILITY_WORDS: frozenset[str] = frozenset(
    w.lower()
    for w in [
        "Adamant", "Addendum", "Alliance", "Battalion", "Bloodrush", "Celebration",
        "Channel", "Chroma", "Cohort", "Constellation", "Converge", "Corrupted",
        "Council's dilemma", "Coven", "Covercast", "Delirium", "Descend", "Disappear",
        "Domain", "Eerie", "Eminence", "Enrage", "Fateful hour", "Fathomless descent",
        "Ferocious", "Flurry", "Formidable", "Grandeur", "Hellbent", "Hero's Reward",
        "Heroic", "Imprint", "Infusion", "Inspired", "Join forces", "Kinfall",
        "Kinship", "Landfall", "Landship", "Legacy", "Lieutenant", "Magecraft",
        "Metalcraft", "Morbid", "Opus", "Pack tactics", "Paradox", "Parley",
        "Radiance", "Raid", "Rally", "Renew", "Repartee", "Revolt", "Secret council",
        "Spell mastery", "Strive", "Survival", "Sweep",
        "Tempting offer", "Threshold", "Underdog", "Undergrowth", "Valiant", "Vivid",
        "Void", "Will of the Planeswalkers", "Will of the council",
    ]
)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


#: Keywords whose entire behaviour is the engine reading the name. Flying is
#: not "unimplemented" for having no effects attached - ``combat.py`` asks
#: ``has_keyword("Flying")`` and that *is* the rule. But split second was a
#: bare name that nothing read, and it scored as implemented for months.
#:
#: So the list is explicit, and ``test_keywords`` proves every entry actually
#: appears in engine source outside the keyword modules. A name added here
#: without the code to read it fails that test.
ENGINE_READS = frozenset(
    {
        # -- evasion and combat, read by combat.py -------------------------
        "Flying", "Reach", "Menace", "Fear", "Intimidate", "Shadow",
        "Horsemanship", "Defender", "Vigilance", "Protection", "Landwalk",
        "Skulk", "Banding",
        # -- damage --------------------------------------------------------
        "Deathtouch", "Lifelink", "Trample", "First strike", "Double strike",
        "Infect", "Wither",
        # -- state, read by SBAs, zone changes and targeting ---------------
        "Indestructible", "Hexproof", "Shroud", "Ward", "Phasing",
        # -- casting and timing, read by casting.py and legality.py --------
        "Flash", "Split second", "Delve", "Haste",
        "Morph", "Megamorph", "Disguise", "Suspend", "Foretell", "Plot",
        # -- characteristics, read by the layer system ---------------------
        "Changeling", "Read Ahead",
    }
)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------




#: Lower-cased for lookup; the readable set above is the one to edit.
_ENGINE_READS_KEYS = frozenset(name.lower() for name in ENGINE_READS)


def _probe_instance(name: str):
    """A representative instance of a keyword, for grading its builder."""
    from ..cr100_game_concepts.cr106_mana import ManaCost
    from ..cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind
    from ..cr600_spells_and_abilities.effects import Effect, EffectKind
    from ..kernel.query import CardType, ObjectFilter, PlayerFilter, PlayerScope, Value
    from .cr702_keyword_impl import KeywordInstance

    return KeywordInstance(
        name,
        amount=1,
        cost=Cost((CostComponent(CostKind.MANA, mana=ManaCost.parse("{2}")),)),
        filter=ObjectFilter(types_all=CardType.CREATURE),
        # A keyword that wraps arbitrary card text is graded on whether it
        # builds the right shape *around* that text, not on whether it can
        # invent the text - which is the parser's job, not the keyword's.
        effects=(
            Effect(
                EffectKind.DRAW,
                players=PlayerFilter(PlayerScope.YOU),
                amount=Value.of(1),
                text="a stand-in body",
            ),
        ),
    )


def _builder_status(name: str) -> Status | None:
    """Whether ``keyword_impl`` can expand this keyword.

    The registry's status is derived from the code rather than hand-maintained
    alongside it. A keyword with a builder is implemented by definition, and one
    without cannot be - which removes the possibility of the table and the
    engine disagreeing.
    """
    from .cr701_keyword_actions import BUILDERS as ACTION_BUILDERS
    from .cr701_keyword_actions import build as build_action
    from .cr702_keyword_impl import BUILDERS, build

    key = name.lower()

    # Keyword *actions* expand to effects rather than abilities, so they are
    # judged the same way but through their own registry.
    if key in ACTION_BUILDERS:
        effects = build_action(name, amount=1)
        if any(node.is_unparsed for e in effects for node in e.walk()):
            return Status.PARTIAL
        return Status.IMPLEMENTED

    if key not in BUILDERS:
        # No builder means the parser cannot produce this keyword at all - it
        # emits an unreadable ability instead, and the keyword never reaches
        # the characteristics. Returning ``None`` here let the hand-written
        # table's claim stand unchallenged, which is how Haste sat marked
        # "implemented" while every hasty creature in the pool was summoning
        # sick.
        return Status.DECLARED

    # A builder existing is not the same as the keyword working. Several
    # builders produce an ability of the right shape - the right trigger, the
    # right cost, the right zone - wrapped around an UNPARSED effect, because
    # the *shape* is known and the behaviour is not. Those are PARTIAL, and
    # detecting it here rather than trusting a hand-maintained flag is what
    # stops the registry drifting into flattery.
    # The probe supplies every parameter the parser could, because a builder is
    # being judged on what it does with a real instance. Protection with no
    # quality and equip with no cost are meaningless inputs, and grading a
    # builder on those would report a gap that does not exist.
    try:
        abilities = build(_probe_instance(name))
    except Exception:  # noqa: BLE001 - a builder that throws is not implemented
        return Status.DECLARED
    if any(a.unparsed for a in abilities):
        return Status.DECLARED
    if any(
        node.is_unparsed
        for ability in abilities
        for effect in ability.effects
        for node in effect.walk()
    ):
        return Status.PARTIAL

    # A builder that produces a bare name is only implemented if the engine
    # reads that name. Otherwise the keyword is inert: the card carries a word
    # that changes nothing, which is the failure split second demonstrated.
    substantive = any(
        ability.effects
        or ability.trigger
        or ability.alternative_cost
        or ability.additional_cost
        or ability.is_characteristic_defining
        # A quality is substance: landwalk, protection and "hexproof from"
        # carry an ObjectFilter that combat and targeting genuinely consult.
        or ability.quality is not None
        or (ability.cost is not None and not ability.cost.is_free)
        for ability in abilities
    )
    if not substantive and key not in _ENGINE_READS_KEYS:
        return Status.PARTIAL
    return Status.IMPLEMENTED


def lookup(name: str) -> KeywordSpec | None:
    """Find a keyword ability or keyword action by name, case-insensitively."""
    key = name.lower()
    spec = KEYWORD_ABILITIES.get(key) or KEYWORD_ACTIONS.get(key)
    if spec is None:
        return None
    # A builder is proof of implementation; the hand-written status is only
    # consulted where there is no builder, or where it says PARTIAL and means it.
    derived = _builder_status(key)
    if derived is None:
        return spec
    if spec.status is Status.PARTIAL and derived is Status.IMPLEMENTED:
        return spec  # A hand-written PARTIAL note knows something the builder does not.
    note = spec.note
    if derived is Status.PARTIAL and not note:
        note = "ability shape is built; the effect itself is not modelled yet"
    return KeywordSpec(spec.name, spec.category, derived, spec.rule, note)


def is_implemented(name: str) -> bool:
    spec = lookup(name)
    return spec is not None and spec.status is not Status.DECLARED


def is_known(name: str) -> bool:
    key = name.lower()
    return key in KEYWORD_ABILITIES or key in KEYWORD_ACTIONS or key in ABILITY_WORDS


def unregistered(names) -> list[str]:
    """Names from a catalog that this registry does not know about.

    The whole point of the module: if Scryfall lists a keyword and we do not,
    that is a gap, and it should be named rather than discovered later.
    """
    return sorted(n for n in names if not is_known(n))


def coverage(registry: dict[str, KeywordSpec] | None = None) -> dict[Status, list[str]]:
    """Group a registry's entries by implementation status."""
    registry = registry if registry is not None else KEYWORD_ABILITIES
    out: dict[Status, list[str]] = {status: [] for status in Status}
    for spec in registry.values():
        resolved = lookup(spec.name) or spec
        out[resolved.status].append(spec.name)
    for names in out.values():
        names.sort()
    return out


def summary() -> str:
    """A short human-readable coverage summary."""
    lines = []
    for label, registry in (
        ("Keyword abilities", KEYWORD_ABILITIES),
        ("Keyword actions", KEYWORD_ACTIONS),
    ):
        grouped = coverage(registry)
        total = sum(len(v) for v in grouped.values())
        done = len(grouped[Status.IMPLEMENTED])
        partial = len(grouped[Status.PARTIAL])
        lines.append(
            f"{label}: {done} implemented, {partial} partial, "
            f"{len(grouped[Status.DECLARED])} declared, {total} total"
        )
    lines.append(f"Ability words: {len(ABILITY_WORDS)} (no rules meaning)")
    return "\n".join(lines)
