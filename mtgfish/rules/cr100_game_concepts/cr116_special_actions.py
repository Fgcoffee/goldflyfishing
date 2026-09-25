"""Special actions (CR 116).

A special action is something a player does with priority that never uses the
stack and therefore cannot be responded to. That last part is the whole point:
unmorphing in response to a removal spell resolves before the removal spell
does, and no window opens in between.

There are twelve (CR 116.2a-m). Playing a land already lives in ``casting.py``
because it needs the whole land-drop apparatus; the rest live here. Two are for
formats out of scope - rolling the planar die (116.2i) and unlocking a
conspiracy (116.2j) - and are registered as unsupported rather than omitted, so
a card that reaches for them is reported instead of silently doing nothing.

Each action is enumerated by ``available``, taken by ``perform``, and the two
must agree: the enumerator is the legality model, and there is exactly one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from ..kernel.enums import Zone
from ..kernel.events import Event, EventKind
from ..kernel.ids import NO_OBJECT, ObjectId, PlayerId
from .cr106_mana import ManaCost
from .cr117_priority import Action, ActionKind

if TYPE_CHECKING:
    from ..kernel.game import Game


class SpecialKind(IntEnum):
    """Which special action, by its CR 116.2 letter."""

    PLAY_LAND = 0  # 116.2a - handled in casting.py, listed for completeness
    TURN_FACE_UP = 1  # 116.2b
    END_CONTINUOUS_EFFECT = 2  # 116.2c
    IGNORE_STATIC = 3  # 116.2d
    DISCARD_SELF = 4  # 116.2e
    SUSPEND = 5  # 116.2f
    COMPANION = 6  # 116.2g
    FORETELL = 7  # 116.2h
    ROLL_PLANAR_DIE = 8  # 116.2i - Planechase, out of scope
    UNLOCK_CONSPIRACY = 9  # 116.2j - Conspiracy Draft, out of scope
    PLOT = 10  # 116.2k
    UNLOCK_ROOM = 11  # 116.2m


#: CR 116.2i and 116.2j belong to formats this simulator does not run. They are
#: named so that ``unsupported`` is a fact about the format rather than a gap.
OUT_OF_SCOPE = frozenset({SpecialKind.ROLL_PLANAR_DIE, SpecialKind.UNLOCK_CONSPIRACY})


@dataclass(frozen=True, slots=True)
class SpecialAction:
    """One available special action, with everything needed to take it."""

    kind: SpecialKind
    source: ObjectId = NO_OBJECT
    cost: ManaCost | None = None
    #: For turning a face-down permanent up: which keyword permits it, because
    #: megamorph adds a +1/+1 counter and morph does not.
    keyword: str = ""
    #: For turning a face-down permanent up: which of its ways of turning up
    #: this is, by position in ``face_up_options``. A manifested card with
    #: morph has two (CR 701.40c), and the player chooses which cost to pay.
    option: int = -1

    def as_action(self) -> Action:
        # The option rides in ``alternative_cost``: choosing which cost to pay
        # for turning a permanent up is exactly what that field records for
        # a spell, and a special action uses it for nothing else.
        return Action(
            ActionKind.SPECIAL,
            source=self.source,
            ability_index=int(self.kind),
            alternative_cost=self.option,
        )


def available(game: Game, player_id: PlayerId) -> list[SpecialAction]:
    """Every special action this player may take right now.

    Land drops are deliberately absent: ``legality.legal_actions`` already
    enumerates them as ``PLAY_LAND``, and having one action appear twice under
    two names would make an agent's choice space wrong.
    """
    found: list[SpecialAction] = []
    found.extend(_turn_face_up(game, player_id))
    found.extend(_from_hand(game, player_id))
    return found


def perform(game: Game, player_id: PlayerId, action: Action) -> bool:
    """Take a special action. Returns whether anything happened."""
    kind = SpecialKind(action.ability_index)
    obj = game.objects.get(action.source)

    if kind is SpecialKind.TURN_FACE_UP:
        return obj is not None and _pay_and_turn_face_up(
            game, player_id, obj, action.alternative_cost
        )
    if kind in (SpecialKind.SUSPEND, SpecialKind.FORETELL, SpecialKind.PLOT):
        return obj is not None and _exile_from_hand(game, player_id, obj, kind)
    if kind is SpecialKind.DISCARD_SELF:
        return obj is not None and _discard_self(game, player_id, obj)
    if kind in OUT_OF_SCOPE:
        game.log.record(
            game,
            f"{kind.name} is not supported in this format",
            kind="unsupported",
            player=player_id,
        )
        return False
    # 116.2c/d/g/m: these exist only where a card creates them, and a card that
    # creates one registers its own handler. Nothing generic to do.
    return False


# ---------------------------------------------------------------------------
# CR 116.2b: turning a face-down permanent face up
# ---------------------------------------------------------------------------

#: The keywords that let a face-down permanent be turned up, and whether doing
#: so leaves a +1/+1 counter behind (CR 702.37b megamorph, 702.168b disguise
#: leaves none but enters with ward).
FACE_UP_KEYWORDS: dict[str, int] = {
    "Morph": 0,
    "Megamorph": 1,
    "Disguise": 0,
}


def _turn_face_up(game: Game, player_id: PlayerId):
    """CR 702.37e: any time you have priority, for the printed morph cost.

    Note what is *not* checked: timing beyond having priority, and whether the
    permanent is a creature right now. Turning face up is not casting and not
    activating, so nothing about sorcery speed or the stack being empty
    applies.
    """
    from ..kernel.legality import can_afford

    for object_id in game.battlefield:
        obj = game.objects.get(object_id)
        if obj is None or obj.controller != player_id or not obj.face_down:
            continue
        for option, (keyword, cost) in enumerate(face_up_options(game, obj)):
            if can_afford(game, player_id, cost):
                yield SpecialAction(
                    SpecialKind.TURN_FACE_UP,
                    source=object_id,
                    cost=cost,
                    keyword=keyword,
                    option=option,
                )


def face_up_options(game: Game, obj) -> list[tuple[str, ManaCost]]:
    """Every way this face-down permanent may be turned face up, with its cost.

    The morph-family costs come first, then CR 701.40b / 701.58b's mana cost
    for a manifested or cloaked creature card - both, for a manifested card
    with morph (CR 701.40c, 701.58c). CR 701.40g: an instant or sorcery card
    offers nothing, because it cannot be turned face up at all.
    """
    from ..cr700_additional_rules.cr708_face_down import (
        cannot_turn_face_up,
        mana_cost_to_turn_up,
    )

    if obj.card is None or cannot_turn_face_up(game, obj):
        return []
    options = list(_face_up_costs(game, obj))
    mana_cost = mana_cost_to_turn_up(game, obj)
    if mana_cost is not None:
        options.append((obj.face_down_by, mana_cost))
    return options


def _face_up_costs(game: Game, obj):
    """The morph-family keywords on the *face-down permanent's real card*.

    CR 708.4: a face-down permanent has no abilities at all while it is face
    down, but the ability that lets it be turned face up still functions - it
    is part of the card, and it is checked against the card, not against the
    2/2 with no text that the layer system is showing everyone.
    """
    if obj.card is None:
        return
    printed = game.printed_characteristics(obj)
    for ability in printed.abilities:
        if ability.keyword not in FACE_UP_KEYWORDS:
            continue
        # A morph keyword builds two abilities: the alternative cost that got
        # the card cast face down, and the permission to turn it up. Only the
        # second one is a special action, and it is the one that functions on
        # the battlefield.
        if ability.alternative_cost is not None:
            continue
        if Zone.BATTLEFIELD not in ability.functions_in:
            continue
        yield ability.keyword, ability.cost.mana_component


def turn_face_up(game: Game, obj, keyword: str | None = None) -> bool:
    """Turn a face-down permanent face up (CR 708.8).

    Its characteristics come back all at once, and because it was already on
    the battlefield this is not an enters-the-battlefield event - "when this
    creature is turned face up" triggers fire, "when this creature enters"
    triggers do not.

    ``keyword`` is the cost that was paid. CR 702.37b: only a megamorph cost
    leaves a +1/+1 counter, so a manifested megamorph card turned up for its
    mana cost gets none. With no keyword the permanent is taken to be turned
    up by its own morph-family ability.
    """
    from ..cr700_additional_rules.cr708_face_down import turn_face_up as turn_up

    if keyword is None:
        megamorph = game.printed_characteristics(obj).has_keyword("Megamorph")
    else:
        megamorph = keyword == "Megamorph"
    return turn_up(game, obj, megamorph=megamorph)


def _pay_and_turn_face_up(game: Game, player_id: PlayerId, obj, option: int) -> bool:
    """CR 702.37e, 702.168d, 701.40b: show the cost, pay it, turn it up.

    The cost is paid - it is what turning a permanent up is for - and an
    unpayable one leaves the permanent face down with nothing spent.
    """
    from ..cr600_spells_and_abilities.cr601_casting import CastError, _pay
    from .cr118_costs import TotalCost

    if obj.controller != player_id or not obj.face_down:
        return False
    options = face_up_options(game, obj)
    if not options:
        return False
    if not 0 <= option < len(options):
        option = 0
    keyword, cost = options[option]
    try:
        _pay(game, player_id, TotalCost(base=cost), obj)
    except CastError as exc:
        game.log.record(
            game, f"cannot turn {obj} face up: {exc}", kind="illegal", player=player_id
        )
        return False
    return turn_face_up(game, obj, keyword)


# ---------------------------------------------------------------------------
# CR 116.2e/f/h/k: actions taken on a card in hand
# ---------------------------------------------------------------------------

#: What each hand-based special action costs and when it may be taken.
#: ``own_turn`` and ``empty_stack`` come straight from the rule text, and they
#: differ per action for no reason other than history - foretell is any time
#: during your turn, plot needs an empty stack, suspend needs neither.
_HAND_ACTIONS: dict[str, tuple[SpecialKind, str, bool, bool]] = {
    "Suspend": (SpecialKind.SUSPEND, "", False, False),
    "Foretell": (SpecialKind.FORETELL, "{2}", True, False),
    "Plot": (SpecialKind.PLOT, "", True, True),
}


def _from_hand(game: Game, player_id: PlayerId):
    from ..kernel.legality import can_afford

    player = game.player(player_id)
    is_own_turn = game.active_player == player_id
    stack_empty = not game.stack

    for object_id in list(player.zone(Zone.HAND)):
        obj = game.objects.get(object_id)
        if obj is None:
            continue
        chars = game.printed_characteristics(obj)
        for keyword, (kind, cost_text, own_turn, empty_stack) in _HAND_ACTIONS.items():
            if not chars.has_keyword(keyword):
                continue
            if own_turn and not is_own_turn:
                continue
            if empty_stack and not stack_empty:
                continue
            cost = ManaCost.parse(cost_text) if cost_text else None
            if not can_afford(game, player_id, cost):
                continue
            yield SpecialAction(kind, source=object_id, cost=cost, keyword=keyword)

        # CR 116.2e: Circling Vultures, the one card with a discard-self
        # special action. Named rather than pattern-matched because it is
        # genuinely one card and a pattern would catch things it should not.
        if chars.name == "Circling Vultures":
            yield SpecialAction(SpecialKind.DISCARD_SELF, source=object_id)


def _exile_from_hand(game: Game, player_id: PlayerId, obj, kind: SpecialKind) -> bool:
    """Suspend, foretell, or plot a card from hand.

    All three exile the card and record what let it happen, because what the
    player may later do with it - cast it when the last time counter is
    removed, cast it for its foretell cost, cast it on a later turn - depends
    on which one it was. CR 607: that is a linked ability, so the link is
    stored rather than re-derived.
    """
    face_down = kind is not SpecialKind.SUSPEND
    exiled = game.move_object(obj, Zone.EXILE)
    exiled.face_down = face_down
    game.exiled_with.setdefault(obj.id, [])
    game.log.record(
        game,
        f"{obj} exiled ({kind.name.lower()})",
        kind="special-action",
        player=player_id,
    )
    if kind is SpecialKind.SUSPEND:
        # CR 702.62a: exiled with N time counters, one removed at each of the
        # controller's upkeeps, cast for free when the last one goes.
        amount = _suspend_amount(game, obj)
        exiled.add_counters("time", amount)
        game.emit(
            Event(
                EventKind.COUNTER_ADDED,
                object_id=exiled.id,
                player=player_id,
                amount=amount,
                data=("time",),
            )
        )
    return True


def _suspend_amount(game: Game, obj) -> int:
    for ability in game.printed_characteristics(obj).abilities:
        if ability.keyword == "Suspend":
            return max(1, getattr(ability.quality, "amount", 0) or 1)
    return 1


def _discard_self(game: Game, player_id: PlayerId, obj) -> bool:
    """CR 116.2e."""
    game.move_object(obj, Zone.GRAVEYARD)
    game.emit(Event(EventKind.DISCARDED, object_id=obj.id, player=player_id))
    return True


def can_take_special_actions(game: Game, player_id: PlayerId) -> bool:
    """Whether the player has any special action available.

    Used by the auto-pass shortcut (CR 724): a player with nothing to do and no
    special action available can pass without being asked.
    """
    for _ in available(game, player_id):
        return True
    return False


__all__ = [
    "OUT_OF_SCOPE",
    "SpecialAction",
    "SpecialKind",
    "available",
    "can_take_special_actions",
    "face_up_options",
    "perform",
    "turn_face_up",
]
