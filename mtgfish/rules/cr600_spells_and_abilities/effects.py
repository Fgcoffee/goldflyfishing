"""Effects: what a spell or ability actually does, as data.

This is the instruction set. The parser's whole job is to turn oracle text into
these opcodes, and it is only allowed to emit opcodes that appear here and have
an executor in the engine. That constraint is the project's central safety
property: a mis-parse can produce a wrong effect, but never an effect the rules
do not know how to police.

An ``Effect`` is a node in a small tree. ``EffectKind.SEQUENCE`` chains them,
``CONDITIONAL`` branches, and ``REPEAT`` loops, so multi-sentence cards compose
out of the same primitives as one-liners.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from ..kernel.enums import CardType, Color, Supertype, Zone
from ..kernel.query import ALWAYS, ZERO, Condition, ObjectFilter, PlayerFilter, Value


class EffectKind(IntEnum):
    """One-shot and continuous effect opcodes.

    Numbered in blocks by area so new members slot in without renumbering.

    Every number must be **unique**. Two members sharing one are not two
    opcodes: ``IntEnum`` folds the second into an alias of the first, so
    ``EffectKind.BECOME_SOLVED is EffectKind.EXTRA_TRIGGER`` came out true,
    the executor table silently kept one entry for both, and a parsed
    "As this enters, choose a creature type" was dispatched to the reflexive
    trigger executor - the parser emitting one opcode and the engine running
    another, which is the one thing the opcode contract exists to prevent.
    ``test_no_two_opcodes_share_a_number`` guards it.
    """

    # -- control flow -------------------------------------------------------
    SEQUENCE = 0
    CONDITIONAL = 1  # "if X, do Y. Otherwise Z."
    REPEAT = 2  # "repeat this process N times"
    CHOOSE_MODE = 3  # CR 700.2 modal spells
    OPTIONAL = 4  # "you may ..."
    NOTHING = 5
    #: "... unless that player pays {1}." A named player may pay a cost; if
    #: they decline or cannot, ``children`` happen. Its own opcode rather than
    #: a CONDITIONAL because the condition is a *choice made during
    #: resolution* by someone who is usually not the controller - there is no
    #: game state to test before asking.
    UNLESS_PAYS = 6

    # -- cards and zones ----------------------------------------------------
    DRAW = 20
    DISCARD = 21
    MILL = 22
    SEARCH_LIBRARY = 23
    SHUFFLE = 24
    REVEAL = 25
    SCRY = 26
    SURVEIL = 27
    MOVE_ZONE = 28  # The general case; the ones below are the common shapes.
    DESTROY = 29
    EXILE = 30
    SACRIFICE = 31
    RETURN_TO_HAND = 32
    PUT_ONTO_BATTLEFIELD = 33
    PUT_ON_LIBRARY = 34
    CREATE_TOKEN = 35
    EXPLORE = 36

    # -- life and damage ----------------------------------------------------
    DAMAGE = 50
    GAIN_LIFE = 51
    LOSE_LIFE = 52
    SET_LIFE = 53
    EXCHANGE_LIFE = 54
    PREVENT_DAMAGE = 55
    REDIRECT_DAMAGE = 56
    ADD_POISON = 57
    FIGHT = 58

    # -- permanents ---------------------------------------------------------
    TAP = 70
    UNTAP = 71
    ADD_COUNTERS = 72
    REMOVE_COUNTERS = 73
    PROLIFERATE = 74
    ATTACH = 75
    UNATTACH = 76
    GAIN_CONTROL = 77
    EXCHANGE_CONTROL = 78
    TRANSFORM = 79
    TURN_FACE_UP = 80
    TURN_FACE_DOWN = 81
    PHASE_OUT = 82
    REGENERATE = 83
    COPY_PERMANENT = 84
    GOAD = 85
    MONSTROSITY = 86
    ADAPT = 87

    # -- the stack ----------------------------------------------------------
    COUNTER_SPELL = 100
    COPY_SPELL = 101
    CHANGE_TARGETS = 102
    CAST_WITHOUT_PAYING = 103
    PLAY_FROM_ZONE = 104

    # -- mana ---------------------------------------------------------------
    ADD_MANA = 120

    # -- continuous effects (CR 611) ----------------------------------------
    #: These create a continuous effect that the layer system applies. They
    #: carry a Duration, and the layer they apply in is implied by the opcode.
    MODIFY_PT = 140  # layer 7c
    SET_PT = 141  # layer 7b
    SWITCH_PT = 142  # layer 7e
    GRANT_ABILITY = 143  # layer 6
    REMOVE_ABILITIES = 144  # layer 6
    SET_COLOR = 145  # layer 5
    ADD_TYPE = 146  # layer 4
    REMOVE_TYPE = 147  # layer 4
    SET_TYPE = 148  # layer 4
    TEXT_CHANGE = 149  # layer 3
    #: Prohibitions and permissions: "can't attack", "can't be countered",
    #: "may cast from your graveyard".
    RESTRICTION = 150
    PERMISSION = 151
    #: CR 101.1: switch a rule off outright - "creatures don't suffer
    #: summoning sickness". Not a prohibition and not a permission: it
    #: suspends a rule of the game rather than constraining an act. See
    #: ``cr100_game_concepts/cr101_rule_overrides.py`` for the list of rules
    #: that can be named, and how to add one.
    SUSPEND_RULE = 154
    #: Cost modification, e.g. "spells cost {1} more to cast".
    MODIFY_COST = 152
    #: Replacement effects created by a resolved spell, e.g. "if a creature
    #: would die this turn, exile it instead".
    REPLACEMENT = 153

    # -- turn structure -----------------------------------------------------
    EXTRA_TURN = 170
    EXTRA_PHASE = 171
    EXTRA_STEP = 172
    SKIP_STEP = 173
    END_TURN = 174
    UNTAP_STEP_SKIP = 175
    EXTRA_LAND_DROP = 176

    # -- players ------------------------------------------------------------
    PLAYER_WINS = 190
    PLAYER_LOSES = 191
    BECOME_MONARCH = 192
    TAKE_INITIATIVE = 193
    ADD_ENERGY = 194
    ADD_EXPERIENCE = 195
    VOTE = 196
    VENTURE = 197
    RING_TEMPTS = 198
    #: CR 702.179a: "Start your engines!" - if the player has no speed, it
    #: becomes 1.
    START_ENGINES = 199
    #: "You may pay {2}." inside a resolution - an action taken, not a price
    #: of activation. Distinct from MODIFY_COST, which changes what *other*
    #: spells cost: reading a payment as a cost modification produces a card
    #: that taxes the table instead of one that spends two mana.
    PAY_COST = 200
    #: "If you don't, ..." - the negative twin of a reflexive trigger
    #: (CR 603.9). Its own opcode rather than an inverted condition, because
    #: "did the player decline?" is a fact about this resolution and not a
    #: question about the game state, and encoding it as a condition would
    #: mean every condition evaluator had to learn about resolutions.
    IF_YOU_DONT = 201
    #: CR 706: roll one or more dice, then act on the results. One opcode for
    #: the whole of it - the dice, the modifiers, the results table and
    #: "ignore the lowest" are all one ability (CR 706.3b), so splitting them
    #: would mean an ability that could half-happen.
    ROLL_DICE = 202

    # -- card-type designations (CR 716, 719) -------------------------------
    #: A Class's level (CR 716.2a). A designation on the permanent, not a
    #: characteristic, so it is set rather than layered.
    SET_CLASS_LEVEL = 210
    #: CR 603.2b: a static ability making some other ability trigger an extra
    #: time. Not a replacement effect and not a copy - the ability genuinely
    #: triggers twice, and both instances go on the stack independently.
    #: ``targets`` says whose abilities, ``trigger_cause`` what has to have
    #: caused the event, and ``amount`` how many extra times.
    EXTRA_TRIGGER = 211
    #: CR 614.1b: "As this enters, choose a creature type." The choice is
    #: recorded on the permanent; ``keywords[0]`` says what kind of thing is
    #: being chosen. It was previously a CHOOSE_MODE with no children, which
    #: recorded nothing at all, so no later sentence could refer back to it.
    CHOOSE_QUALITY = 214
    #: A Case becomes solved (CR 719.3b).
    BECOME_SOLVED = 218
    #: CR 710: flip a permanent to its bottom half.
    FLIP_PERMANENT = 212
    #: CR 603.7: set up an ability that fires later. The condition lives in
    #: ``trigger``; what it does lives in ``children``.
    DELAYED_TRIGGER = 213
    #: CR 603.9: "when you do, ..." - a reflexive trigger, created and
    #: triggered during a resolution rather than watching the event stream.
    REFLEXIVE_TRIGGER = 219
    #: CR 723.1: control another player's next turn (Mindslaver).
    CONTROL_PLAYER = 215
    #: CR 727.1: restart the game (Karn Liberated).
    RESTART_GAME = 216
    #: CR 722.3a: give a permanent with a prepare spell the prepared
    #: designation, which puts a copy of its prepare spell into exile.
    BECOME_PREPARED = 217
    #: "The blocking creature's controller sacrifices it at end of combat" -
    #: each creature blocking the triggering attacker, at the beginning of
    #: the end of combat step (CR 511.2). The Ring's third ability.
    SACRIFICE_BLOCKERS_AT_END_OF_COMBAT = 220
    #: CR 702.85a: exile from the top until a nonland card with lesser mana
    #: value than this spell; you may cast it free; the rest go to the bottom
    #: in a random order.
    CASCADE = 221
    #: CR 701.57a: the same, up to mana value ``amount``, and a card not cast
    #: goes to its owner's hand.
    DISCOVER = 222
    #: CR 701.55: each player in ``players`` chooses one of ``children`` and
    #: performs all of it; "that player" inside it is the chooser.
    VILLAINOUS_CHOICE = 223
    #: CR 701.64a: "harness [this permanent]" - it becomes harnessed.
    HARNESS = 224

    # -- fallback -----------------------------------------------------------
    #: The parser could not read this. It never executes; it exists so the
    #: coverage report can name exactly what was lost, and so an ability that
    #: is partly understood is not half-executed.
    UNPARSED = 999


#: Effects that create a continuous effect rather than changing the game state
#: once. These need a Duration and go through the layer system (CR 613).
#: CR 610.1: every other opcode is a one-shot effect - it does its thing once
#: and has no duration - which is why the set is listed rather than a flag on
#: each opcode. Damage, destruction, token creation and zone changes are all
#: outside it.
CONTINUOUS_KINDS = frozenset(
    {
        EffectKind.MODIFY_PT,
        EffectKind.SET_PT,
        EffectKind.SWITCH_PT,
        EffectKind.GRANT_ABILITY,
        EffectKind.REMOVE_ABILITIES,
        EffectKind.SET_COLOR,
        EffectKind.ADD_TYPE,
        EffectKind.REMOVE_TYPE,
        EffectKind.SET_TYPE,
        EffectKind.TEXT_CHANGE,
        EffectKind.RESTRICTION,
        EffectKind.PERMISSION,
        EffectKind.SUSPEND_RULE,
        EffectKind.MODIFY_COST,
        EffectKind.REPLACEMENT,
    }
)


@dataclass(frozen=True, slots=True)
class TokenSpec:
    """What a CREATE_TOKEN effect makes (CR 111.2, 111.4)."""

    name: str = ""
    types: CardType = CardType.CREATURE
    subtypes: tuple[str, ...] = ()
    colors: Color = Color.NONE
    power: Value = ZERO
    toughness: Value = ZERO
    keywords: tuple[str, ...] = ()
    abilities: tuple = ()
    enters_tapped: bool = False
    enters_attacking: bool = False
    #: A token copy of an existing object rather than a fresh definition.
    copy_of: ObjectFilter | None = None

    def __str__(self) -> str:
        """What this token is, for the round-trip explainer.

        Power and toughness only when it is a creature: a Treasure rendered as
        "0/0 Treasure" reads like a creature token that dies on arrival, which
        is the bug this spelling used to be hiding rather than reporting. The
        keywords are named for the same reason - a token created with flying
        and one created without look identical without them.
        """
        parts = []
        if self.types & CardType.CREATURE:
            parts.append(f"{self.power}/{self.toughness}")
        parts.append(" ".join(self.subtypes) or self.name)
        if self.keywords:
            parts.append("with " + ", ".join(self.keywords))
        if self.abilities:
            parts.append(f"with {len(self.abilities)} written ability/ies")
        return " ".join(part for part in parts if part).strip() or self.name


@dataclass(frozen=True, slots=True)
class DiceOutcome:
    """One striation of a results table (CR 706.3a).

    "1-3", "4+" and "6" are all the same shape: a range and an effect. A range
    with a single endpoint ("N+") is written with ``high`` left at zero, which
    reads as no upper bound - the alternative was a sentinel large number that
    someone would eventually compare against.
    """

    low: int
    high: int = 0
    effects: tuple = ()
    text: str = ""

    def covers(self, result: int) -> bool:
        """CR 706.3a: whether a result falls in this striation."""
        if result < self.low:
            return False
        return self.high <= 0 or result <= self.high


@dataclass(frozen=True, slots=True)
class Effect:
    """One instruction.

    Most fields are unused by most opcodes; that is the price of a flat struct,
    and it buys dispatch on an integer plus a representation that serialises
    without ceremony.
    """

    kind: EffectKind = EffectKind.NOTHING

    #: What the effect acts on. ``None`` means the effect's own source, or
    #: nothing at all for player-scoped effects.
    targets: ObjectFilter | None = None
    players: PlayerFilter | None = None

    amount: Value = ZERO
    #: Second quantity, for effects that need two (e.g. MODIFY_PT).
    amount2: Value = ZERO

    #: Whether ``targets`` is chosen at announcement (CR 115.1) or merely
    #: described at resolution. This distinction decides whether the spell can
    #: be cast with no legal object, and whether it fizzles (CR 608.2b).
    is_targeted: bool = False

    duration: int = 0  # rules.kernel.enums.Duration; int to avoid a circular import
    zone: Zone | None = None
    from_zone: Zone | None = None
    counter_type: str = ""
    #: For UNLESS_PAYS: what the player may pay to stop the effect.
    pay_cost: object | None = None
    #: For REPLACEMENT: which ReplacementKind this creates, as an int to keep
    #: this module from importing the replacement layer. Zero means the parser
    #: read a replacement whose shape the engine has no kind for, and such an
    #: effect is never registered.
    replacement_kind: int = 0
    #: For REPLACEMENT: multiply the replaced event's amount. Doubling Season
    #: doubles; Hardened Scales adds. Both exist, so both are expressible, and
    #: the multiplier applies before the addition.
    multiplier: int = 1
    #: CR 610.3c: an object returned to the battlefield by the second one-shot
    #: effect of an "until" exile returns under its owner's control, not under
    #: the control of whoever is returning it.
    under_owners_control: bool = False
    #: For ADD_MANA: a "spend this mana only on ..." rider (CR 106.6).
    mana_restriction: object | None = None
    #: For ADD_MANA: the exact symbols produced, e.g. ``("{G}", "{U}")``.
    #: A count plus a set of colors cannot express this - "Add {G}{U}" and
    #: "Add two mana of any one of green or blue" have the same count and the
    #: same color set and are different abilities. Storing the symbols keeps
    #: a dual land making two mana instead of one per color.
    mana_produced: tuple[str, ...] = ()
    token: TokenSpec | None = None
    keywords: tuple[str, ...] = ()
    types: CardType = CardType.NONE
    colors: Color = Color.NONE

    #: Guard for CONDITIONAL and for "if" clauses inside an effect.
    condition: Condition = ALWAYS
    #: CR 603.7: for DELAYED_TRIGGER, when the delayed ability fires. Typed
    #: loosely to keep effects.py from importing abilities.py, which imports
    #: effects.py.
    trigger: object | None = None
    #: CR 603.7b: a delayed trigger fires once, unless the effect that made it
    #: says otherwise - "at the beginning of each of your upkeeps". Its own
    #: flag because the duration cannot say it: ``Duration.PERMANENT`` is 0,
    #: the same as an unset duration, so reading it made *every* delayed
    #: trigger repeat and "at the beginning of the next end step, draw a card"
    #: drew every end step for the rest of the game.
    repeats: bool = False
    #: Sub-effects for SEQUENCE, CONDITIONAL, REPEAT, OPTIONAL, CHOOSE_MODE.
    children: tuple[Effect, ...] = ()
    #: CR 700.2i: what each mode of a CHOOSE_MODE costs against ``amount``,
    #: one entry per child. Empty means every mode costs one, which is the
    #: ordinary bulleted "choose one" - so a pawprint spell is not a second
    #: mechanism, just this one with weights that are not all 1. A card
    #: printing "{P}{P} -" gives that mode a 2.
    mode_weights: tuple[int, ...] = ()
    #: CR 700.2d: normally a mode may not be chosen twice, and some cards say
    #: otherwise in so many words ("You may choose the same mode more than
    #: once"). Every pawprint spell printed so far does.
    modes_may_repeat: bool = False
    #: CR 700.2i: "choose *up to* N worth of modes" - the budget is a
    #: ceiling, not a requirement. Plain "choose one" is not up-to: a mode has
    #: to be chosen where a legal one exists.
    modes_up_to: bool = False
    #: The "otherwise" branch of a CONDITIONAL.
    otherwise: tuple[Effect, ...] = ()

    #: Whole abilities granted by GRANT_ABILITY, for the cases where a keyword
    #: name is not enough ("gains '{T}: draw a card'").
    granted_abilities: tuple = ()
    #: Prohibitions created by a RESTRICTION effect (CR 101.2). Data rather
    #: than code, so a new "can't" is a new entry and not a new special case.
    restrictions: tuple = ()
    #: For ROLL_DICE: how many faces each die has (CR 706.1). ``amount`` is
    #: how many dice.
    dice_sides: int = 0
    #: CR 706.2: added to the natural result to give the result. Modifiers
    #: from other sources are continuous effects and do not live here.
    dice_modifier: int = 0
    #: CR 706.6: how many of the lowest rolls to ignore, as "ignore the lowest
    #: roll" asks. An ignored roll never happened, so nothing triggers on it.
    dice_ignore_lowest: int = 0
    #: CR 706.3a: the results table, if the ability has one. Each entry is a
    #: range and what happens for a result inside it. Empty means CR 706.4 -
    #: no table, and the result is read by whatever comes after.
    outcomes: tuple = ()
    #: For CAST_WITHOUT_PAYING: which face to cast. CR 310.12b's "cast it
    #: transformed" is the only thing that needs it - everything else casts
    #: the front face, which is index 0 and the default.
    face_index: int = 0
    #: For CAST_WITHOUT_PAYING: cast a *copy* of the object rather than the
    #: object (CR 707.12), created in ``zone`` - or where the object is - and
    #: cast from there. Paradigm's "create a copy of this object in exile.
    #: You may cast the copy".
    cast_a_copy: bool = False
    #: For ADD_TYPE: supertypes to add - "is legendary in addition to its
    #: other types" (CR 205.4a).
    supertypes: Supertype = Supertype.NONE
    #: For SUSPEND_RULE: which rule is switched off, as its CR number - a
    #: member of ``cr101_rule_overrides.Rule``. ``targets`` and ``players``
    #: then say for whom, exactly as they do for a prohibition.
    rule: str = ""
    #: For COPY_PERMANENT: which object's copiable values to take (CR 613.2).
    copy_source: int = 0
    #: CR 601.2d: the amount is *divided* among the targets rather than
    #: applied to each of them. Two counters among two creatures is one each,
    #: not two each - which is a different card.
    divided: bool = False
    #: For ADD_TYPE: add the creature type this permanent recorded, rather
    #: than one named on the card.
    of_chosen_type: bool = False
    #: For ADD_MANA: produce the colour this permanent's controller chose,
    #: rather than a colour fixed when the card was parsed.
    colors_chosen: bool = False
    #: For ADD_MANA: "of any color in your commander's color identity" - the
    #: colours on offer are narrowed to that identity (CR 903.4), and there
    #: are none without a commander (CR 903.4f).
    colors_in_commander_identity: bool = False
    #: For ATTACH: what is being attached, when it is not the ability's own
    #: source. "Attach *that Equipment* to target creature" names a different
    #: permanent, and attaching the source instead would move the wrong one.
    attached_to: ObjectFilter | None = None
    #: For EXTRA_TRIGGER: what must have caused the event, when the card says
    #: so ("if a *land* entering causes..."). None means any cause at all.
    trigger_cause: ObjectFilter | None = None
    #: For VENTURE: CR 701.49d's "venture into [quality]" - the quality a
    #: dungeon entered this way must have, e.g. "Undercity". Empty for the
    #: plain "venture into the dungeon".
    dungeon_quality: str = ""

    #: The oracle text this came from, kept for the replay log and for the
    #: coverage report.
    text: str = ""

    @property
    def is_unparsed(self) -> bool:
        if self.kind is EffectKind.UNPARSED:
            return True
        return any(child.is_unparsed for child in self.children + self.otherwise)

    @property
    def is_continuous(self) -> bool:
        return self.kind in CONTINUOUS_KINDS

    def walk(self):
        """Yield this effect and every descendant."""
        yield self
        for child in self.children + self.otherwise:
            yield from child.walk()

    def __str__(self) -> str:
        if self.text:
            return self.text
        parts = [self.kind.name.lower()]
        if not self.amount.is_constant or self.amount.constant:
            parts.append(str(self.amount))
        if self.targets is not None:
            parts.append(str(self.targets))
        if self.players is not None:
            parts.append(str(self.players))
        return " ".join(parts)


def unparsed(text: str) -> Effect:
    """An effect the parser could not read.

    Deliberately constructible so that a partly-understood ability records what
    it failed on instead of silently producing a shorter effect list.
    """
    return Effect(EffectKind.UNPARSED, text=text)


NO_EFFECT = Effect(EffectKind.NOTHING)
