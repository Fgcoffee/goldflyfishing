"""Card-type machinery: CR 710, 711, 714, 716, 718-721.

These are the card types whose rules are *structural* rather than expressed in
oracle text. A Saga's chapter symbol, a leveler's level bar, a Class's level
bar, a station symbol - each is a printed glyph that the Comprehensive Rules
define as shorthand for an ability the card doesn't spell out. The parser will
never see the words, so the shorthand has to live here, next to the engine that
executes it.

Each function in this module is the CR's own expansion, written once:

    {rN}: [Effect]      -> a triggered ability that fires when lore counters
                           cross N (714.2b)
    {LEVEL N1-N2}       -> a static ability conditioned on level counters,
                           setting base P/T and granting abilities (711.2a)
    [Cost]: Level N     -> an activated ability plus a static ability (716.2a)
    {N+}                -> a static ability conditioned on charge counters
                           (721.2a)
    To solve - [Cond]   -> an end-step trigger that sets a designation (719.3a)

The alternative-characteristics types - Adventure (715), Prototype (718), Omen
(720), and flip (710) - are all the same idea seen from a different angle: one
card, two sets of characteristics, and a rule saying which set applies in which
zone. They share ``CastMode`` so the casting code has one thing to ask about
rather than four.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import IntEnum
from functools import lru_cache
from typing import TYPE_CHECKING

from ..cr100_game_concepts import actions
from ..cr600_spells_and_abilities.abilities import Ability, AbilityKind, TriggerCondition
from ..cr600_spells_and_abilities.effects import Effect, EffectKind
from ..kernel.enums import Zone
from ..kernel.events import EventKind
from ..kernel.ids import NO_OBJECT, ObjectId
from ..kernel.query import (
    Comparison,
    Condition,
    ConditionKind,
    NumericConstraint,
    ObjectFilter,
    Value,
)

if TYPE_CHECKING:
    from ..kernel.game import Game
    from ..kernel.gameobject import GameObject


SELF = ObjectFilter(source_only=True)


def _restamp(game: Game, obj: GameObject) -> None:
    """Give a permanent a new timestamp and drop cached characteristics.

    CR 613.7d: gaining or losing an ability, or a designation that abilities
    key off, changes what continuous effects apply to it, and the layer cache
    has to be told.
    """
    obj.timestamp = game.ids.timestamp()
    obj.invalidate()
    obj.invalidate_printed()
    game.invalidate_characteristics()


# ---------------------------------------------------------------------------
# CR 715/718/720/710: alternative characteristics
# ---------------------------------------------------------------------------


class CastMode(IntEnum):
    """Which set of characteristics an object is using.

    CR 715.4, 718.4 and 720.4 all say the same thing in different words: the
    alternative characteristics apply only in specific zones, and only if the
    player chose them when playing the card. Because the choice is made as the
    card is played (715.3, 718.3, 720.3) and then persists, it is state on the
    object rather than something recomputed.
    """

    NORMAL = 0
    ADVENTURE = 1  # CR 715
    PROTOTYPE = 2  # CR 718
    OMEN = 3  # CR 720
    FLIPPED = 4  # CR 710 - reached by flipping, not by choosing


#: How a spell cast in each mode leaves the stack on resolution. CR 715.3d
#: exiles an Adventure (and lets its controller play the card later); CR 720.3d
#: shuffles an Omen into its owner's library; everything else goes to the
#: graveyard the ordinary way.
RESOLUTION_ZONE: dict[CastMode, Zone | None] = {
    CastMode.NORMAL: None,
    CastMode.ADVENTURE: Zone.EXILE,
    CastMode.PROTOTYPE: None,  # It becomes a permanent; it never "resolves away".
    CastMode.OMEN: Zone.LIBRARY,
    CastMode.FLIPPED: None,
}

#: The modes whose alternative characteristics survive onto the battlefield.
#: CR 718.3b keeps a prototyped creature's smaller P/T and cost; CR 710.2 keeps
#: a flipped permanent's bottom half. An Adventure or an Omen never becomes a
#: permanent in its alternative mode at all, so its characteristics stop
#: mattering the moment it leaves the stack.
PERSISTS_ON_BATTLEFIELD = frozenset({CastMode.PROTOTYPE, CastMode.FLIPPED})


def face_index_for_mode(mode: CastMode) -> int:
    """Which printed face a cast mode reads.

    Adventure, Omen, prototype and flip all print their alternative
    characteristics as a second face in the card data, so the engine addresses
    them the same way it addresses the back of a transforming card.
    """
    return 0 if mode is CastMode.NORMAL else 1


def cast_mode_for_face(obj: GameObject, face_index: int) -> CastMode:
    """Which mode playing this face of this card means.

    The face and the mode are two views of one choice: an Adventure prints
    its alternative characteristics as a second face, and choosing that face
    *is* choosing to cast the Adventure (CR 715.3). Which mode it is depends
    on the card's layout, because the second face of a split card is simply
    the other half and carries no alternative characteristics at all.
    """
    from ..kernel.enums import Layout

    if face_index == 0:
        return CastMode.NORMAL
    # CR 718.3: the prototype characteristics are not printed as a face of
    # their own in the card data, so the layout is asked before the faces.
    if getattr(obj.card, "layout", Layout.NORMAL) is Layout.PROTOTYPE:
        face, _ = card_face(obj.card, face_index)
        return CastMode.PROTOTYPE if face is not None else CastMode.NORMAL
    faces = getattr(obj.card, "faces", ())
    if face_index >= len(faces):
        return CastMode.NORMAL

    # Read the subtype rather than the layout. CR 715.1 and CR 720.1 define an
    # adventurer card and an omen card by the subtype on that half, and the
    # card data gives both the same layout - so a layout test would call every
    # Omen an Adventure and send it to exile instead of the library.
    subtypes = faces[face_index].type_line.subtypes
    if "Adventure" in subtypes:
        return CastMode.ADVENTURE
    if "Omen" in subtypes:
        return CastMode.OMEN
    # A split card's other half is simply the other half: no alternative
    # characteristics, so nothing special happens when it resolves.
    return CastMode.NORMAL


def resolution_zone(mode: CastMode) -> Zone | None:
    return RESOLUTION_ZONE.get(mode)


#: CR 702.160a: "Prototype [mana cost] - [power]/[toughness]", as the card
#: data prints it at the start of a line of the card's own text.
_PROTOTYPE_LINE = re.compile(
    r"^Prototype\s+((?:\{[^}]+\})+)\s*[\u2014-]\s*(\d+)/(\d+)", re.MULTILINE
)


@lru_cache(maxsize=None)
def prototype_face(face):
    """CR 718.2: the prototype card's alternative set of characteristics.

    The inset frame is a second mana cost, power and toughness, and nothing
    else - CR 718.5 keeps the name, text, types and abilities. The card data
    prints no second face for it, only the keyword line, so the face is built
    from that line: the front face with those three values swapped in, and
    CR 718.3b's colour taken from the new cost. None when the face has no
    prototype line to read.
    """
    match = _PROTOTYPE_LINE.search(getattr(face, "oracle_text", "") or "")
    if match is None:
        return None
    from ..cr100_game_concepts.cr106_mana import ManaCost

    cost = ManaCost.parse(match.group(1))
    return replace(
        face,
        mana_cost=cost,
        has_mana_cost=True,
        power=match.group(2),
        toughness=match.group(3),
        colors=cost.colors,
        color_indicator=None,
    )


def card_face(card, face_index: int):
    """The face a face index names, and the face whose abilities it has.

    An ordinary face is its own answer. The one exception is a prototype
    card's index 1, which is its prototype characteristics (CR 718.2) - a
    face the card data never prints, whose abilities are the front face's
    (CR 718.5). ``(None, face_index)`` when the index names nothing.
    """
    from ..kernel.enums import Layout

    faces = getattr(card, "faces", ())
    if face_index < len(faces):
        return faces[face_index], face_index
    if (
        face_index == 1
        and faces
        and getattr(card, "layout", Layout.NORMAL) is Layout.PROTOTYPE
    ):
        proto = prototype_face(faces[0])
        if proto is not None:
            return proto, 0
    return None, face_index


def flip(game: Game, obj: GameObject) -> bool:
    """CR 710: flip a permanent, one way and forever.

    710.4 makes this irreversible while the permanent is on the battlefield,
    and 710.2 makes the bottom half's name, text, types and P/T take over. The
    colour and mana cost are deliberately untouched (710.1c) - they come from
    the top half no matter what, which is why this sets a flag rather than
    swapping faces wholesale.
    """
    if obj.flipped or obj.zone is not Zone.BATTLEFIELD:
        return False
    obj.flipped = True
    _restamp(game, obj)
    game.log.record(game, f"{obj} flips", kind="flip", player=obj.controller)
    return True


# ---------------------------------------------------------------------------
# CR 714: Sagas
#
# CR 303.5: Saga is an enchantment subtype, and the enchantment rules say
# nothing more about it than "see rule 714" - so this is all of it.
# ---------------------------------------------------------------------------


def chapter_ability(chapter: int, *effects: Effect, text: str = "") -> Ability:
    """CR 714.2b: expand a chapter symbol into its triggered ability.

    "{rN} - [Effect]" is "When one or more lore counters are put onto this
    Saga, if the number of lore counters on it was less than N and became at
    least N, [effect]." The crossing test lives in the trigger machinery
    because it needs the event's amount, not just the current count.
    """
    return Ability(
        kind=AbilityKind.TRIGGERED,
        effects=effects,
        chapter=chapter,
        text=text or f"{{r{chapter}}} - " + "; ".join(str(e) for e in effects),
        trigger=TriggerCondition(
            event_kinds=frozenset({EventKind.COUNTER_ADDED}),
            subject=SELF,
            chapter=chapter,
            text=f"lore counters reach {chapter}",
        ),
    )


def enters_with_lore_counter() -> Ability:
    """CR 714.3a: the intrinsic "enters with a lore counter on it".

    A replacement effect, not a trigger - which is why a Saga's first chapter
    happens on the turn it enters and why a Saga entering with counters already
    on it (Read Ahead) skips the chapters it passes.
    """
    return Ability.static(
        Effect(
            EffectKind.ADD_COUNTERS,
            counter_type="lore",
            amount=Value.of(1),
            text="enters with a lore counter",
        ),
        text="This Saga enters with a lore counter on it.",
    )


def chapter_numbers(chars) -> tuple[int, ...]:
    """Every chapter number printed on this object, in order."""
    return tuple(
        sorted(
            {
                ability.chapter
                for ability in chars.abilities
                if ability.chapter and ability.kind is AbilityKind.TRIGGERED
            }
        )
    )


def final_chapter(chars) -> int:
    """CR 714.2d: the greatest chapter number, or zero if there are none."""
    numbers = chapter_numbers(chars)
    return numbers[-1] if numbers else 0


def saga_lore_counters(game: Game) -> None:
    """CR 714.3c: as a player's precombat main phase begins, a lore counter on
    each Saga they control that has chapter abilities.

    A turn-based action, so it does not use the stack and cannot be responded
    to before it happens - the chapter ability it triggers can be. The "with
    one or more chapter abilities" clause is what stops an Urza's Saga under a
    Blood Moon from ticking: it is still an Enchantment Land - Saga, but Blood
    Moon has removed the abilities, so it gets no counter and rule 714.4 will
    not sacrifice it either.
    """
    for obj in list(game.permanents(game.active_player)):
        chars = game.characteristics(obj)
        if not chars.has_subtype("Saga"):
            continue
        if not chapter_numbers(chars):
            continue
        actions.add_counters(game, obj, "lore", 1)


# ---------------------------------------------------------------------------
# CR 711: levelers
# ---------------------------------------------------------------------------


def _counter_band(counter: str, low: int, high: int | None) -> Condition:
    """A condition true while this permanent's counter count is in [low, high].

    Asked of the permanent directly rather than by searching the battlefield
    for one that qualifies: "as long as this creature has at least N level
    counters" is a question about one object, and a search would also be
    satisfied by some *other* levelled creature.
    """
    lower = Condition(
        kind=ConditionKind.COUNTER_COUNT,
        filter=SELF,
        counter_type=counter,
        constraint=NumericConstraint.at_least(low),
        text=f"{low} or more {counter} counters",
    )
    if high is None:
        return lower
    upper = Condition(
        kind=ConditionKind.COUNTER_COUNT,
        filter=SELF,
        counter_type=counter,
        constraint=NumericConstraint.at_most(high),
        text=f"no more than {high} {counter} counters",
    )
    return Condition(
        kind=ConditionKind.AND,
        operands=(lower, upper),
        text=f"between {low} and {high} {counter} counters",
    )


def level_band(
    low: int,
    high: int | None,
    power: int,
    toughness: int,
    *granted: Ability,
) -> tuple[Ability, ...]:
    """CR 711.2a/b: expand a {LEVEL N1-N2} or {LEVEL N3+} symbol.

    Both forms mean the same shape of thing - a static ability, conditioned on
    the number of level counters, that sets base power and toughness and grants
    the abilities printed in that striation. Setting base P/T puts it in layer
    7b, so a level band overwrites a characteristic-defining P/T but is itself
    overwritten by later timestamps and by counters in 7d.
    """
    condition = _counter_band("level", low, high)
    label = f"{{LEVEL {low}-{high}}}" if high is not None else f"{{LEVEL {low}+}}"
    abilities: list[Ability] = [
        Ability.static(
            Effect(
                EffectKind.SET_PT,
                targets=SELF,
                amount=Value.of(power),
                amount2=Value.of(toughness),
            ),
            condition=condition,
            text=f"{label} {power}/{toughness}",
        )
    ]
    if granted:
        abilities.append(
            Ability.static(
                Effect(
                    EffectKind.GRANT_ABILITY,
                    targets=SELF,
                    granted_abilities=tuple(granted),
                ),
                condition=condition,
                text=f"{label} grants " + "; ".join(a.text for a in granted),
            )
        )
    return tuple(abilities)


# ---------------------------------------------------------------------------
# CR 721: station
# ---------------------------------------------------------------------------


def station_band(threshold: int, *granted: Ability, power: int = 0, toughness: int = 0):
    """CR 721.2a: "{N+}[abilities]" is "as long as this permanent has N or more
    charge counters on it, it has [abilities]" - plus the P/T box printed in
    the same striation, if there is one.
    """
    condition = _counter_band("charge", threshold, None)
    label = f"{{{threshold}+}}"
    abilities: list[Ability] = []
    if power or toughness:
        abilities.append(
            Ability.static(
                Effect(
                    EffectKind.SET_PT,
                    targets=SELF,
                    amount=Value.of(power),
                    amount2=Value.of(toughness),
                ),
                condition=condition,
                text=f"{label} {power}/{toughness}",
            )
        )
    if granted:
        abilities.append(
            Ability.static(
                Effect(
                    EffectKind.GRANT_ABILITY,
                    targets=SELF,
                    granted_abilities=tuple(granted),
                ),
                condition=condition,
                text=f"{label} grants " + "; ".join(a.text for a in granted),
            )
        )
    return tuple(abilities)


# ---------------------------------------------------------------------------
# CR 716: Classes
#
# CR 303.6: Class is an enchantment subtype, and like Saga it is defined
# entirely in section 700.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClassLevel:
    """One class level bar: an activated ability and a static ability."""

    level: int
    activated: Ability
    static: tuple[Ability, ...]


def class_level_bar(level: int, cost, *granted: Ability) -> ClassLevel:
    """CR 716.2a: "[Cost]: Level N - [Abilities]" is two abilities.

    The activated one is sorcery-speed and can only be used to step up by
    exactly one level, which is what stops a player buying level 3 directly.
    The static one applies at that level *or greater*, so a Class at level 3
    still has everything level 2 gave it.
    """
    from ..kernel.enums import Timing

    activated = Ability(
        AbilityKind.ACTIVATED,
        effects=(
            Effect(EffectKind.SET_CLASS_LEVEL, targets=SELF, amount=Value.of(level)),
        ),
        cost=cost,
        timing=Timing.SORCERY,
        activation_condition=Condition(
            kind=ConditionKind.CLASS_LEVEL,
            filter=SELF,
            constraint=NumericConstraint(Comparison.EQ, Value.of(level - 1)),
            text=f"this Class is level {level - 1}",
        ),
        text=f"{cost}: Level {level}",
    )
    condition = Condition(
        kind=ConditionKind.CLASS_LEVEL,
        filter=SELF,
        constraint=NumericConstraint(Comparison.GE, Value.of(level)),
        text=f"this Class is level {level} or greater",
    )
    statics = tuple(
        Ability.static(
            Effect(
                EffectKind.GRANT_ABILITY, targets=SELF, granted_abilities=(ability,)
            ),
            condition=condition,
            text=f"Level {level}: {ability.text}",
        )
        for ability in granted
    )
    return ClassLevel(level=level, activated=activated, static=statics)


def class_level(game: Game, object_id: ObjectId) -> int:
    """CR 716.2b/d: a permanent's level, defaulting to 1.

    A designation rather than a characteristic - it survives the permanent
    ceasing to be a Class, and it is explicitly not copiable (716.2b), so it
    lives on the game beside the object rather than on the object's
    characteristics.
    """
    return game.class_levels.get(object_id, 1)


def set_class_level(game: Game, object_id: ObjectId, level: int) -> None:
    obj = game.objects.get(object_id)
    game.class_levels[object_id] = level
    if obj is not None:
        _restamp(game, obj)


# ---------------------------------------------------------------------------
# CR 719: Cases
# ---------------------------------------------------------------------------


def to_solve(condition: Condition, text: str = "") -> Ability:
    """CR 719.3a: "To solve - [Condition]" is a delayed check at your end step.

    Written as a triggered ability with an intervening-if, because that is
    exactly what the rule expands to: it triggers at the beginning of your end
    step, checks the condition then and again on resolution, and only then does
    the Case become solved.
    """
    not_yet = Condition(
        kind=ConditionKind.NOT,
        operands=(
            Condition(
                kind=ConditionKind.IS_SOLVED,
                filter=SELF,
                text="this Case is solved",
            ),
        ),
        text="this Case is not solved",
    )
    return Ability(
        kind=AbilityKind.TRIGGERED,
        effects=(Effect(EffectKind.BECOME_SOLVED, targets=SELF),),
        text=text or f"To solve - {condition}",
        trigger=TriggerCondition(
            event_kinds=frozenset({EventKind.END_STEP}),
            intervening_if=Condition(
                kind=ConditionKind.AND,
                operands=(condition, not_yet),
                text=f"{condition} and this Case is not solved",
            ),
            text="at the beginning of your end step",
        ),
    )


def is_solved(game: Game, object_id: ObjectId) -> bool:
    """CR 719.3b: solved is a designation, not an ability and not copiable.

    It lasts until the permanent leaves the battlefield, which is handled by
    dropping the id when the object changes zones - a new object is a new
    thing (CR 400.7) and has never been solved.
    """
    return object_id in game.solved_permanents


def become_solved(game: Game, object_id: ObjectId) -> bool:
    if object_id in game.solved_permanents:
        return False
    game.solved_permanents.add(object_id)
    obj = game.objects.get(object_id)
    if obj is not None:
        _restamp(game, obj)
        game.log.record(game, f"{obj} becomes solved", kind="solved")
    return True


def solved_ability(*granted: Ability) -> tuple[Ability, ...]:
    """CR 719.3c / 702.169: "Solved - [Ability]" functions only once solved."""
    condition = Condition(
        kind=ConditionKind.IS_SOLVED, filter=SELF, text="this Case is solved"
    )
    return tuple(
        Ability.static(
            Effect(
                EffectKind.GRANT_ABILITY, targets=SELF, granted_abilities=(ability,)
            ),
            condition=condition,
            text=f"Solved - {ability.text}",
        )
        for ability in granted
    )


# ---------------------------------------------------------------------------
# Zone-change bookkeeping
# ---------------------------------------------------------------------------


def forget_designations(game: Game, object_id: ObjectId) -> None:
    """CR 400.7: a permanent that leaves the battlefield loses its designations.

    Solved and class level both explicitly last only while the permanent is on
    the battlefield (719.3b, 716.2b), so both are dropped here rather than in
    seven separate places that move objects around.
    """
    if object_id == NO_OBJECT:
        return
    game.solved_permanents.discard(object_id)
    game.class_levels.pop(object_id, None)
    # CR 722.3c: prepared lasts only while the permanent is on the battlefield.
    game.prepared_permanents.discard(object_id)


__all__ = [
    "CastMode",
    "ClassLevel",
    "card_face",
    "become_solved",
    "chapter_ability",
    "chapter_numbers",
    "class_level",
    "class_level_bar",
    "enters_with_lore_counter",
    "face_index_for_mode",
    "final_chapter",
    "flip",
    "forget_designations",
    "is_solved",
    "level_band",
    "prototype_face",
    "resolution_zone",
    "saga_lore_counters",
    "set_class_level",
    "solved_ability",
    "station_band",
    "to_solve",
]
