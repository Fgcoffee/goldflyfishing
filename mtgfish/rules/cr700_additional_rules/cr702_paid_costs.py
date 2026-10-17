"""What a cost keyword does once its cost has been paid (CR 702).

A cost keyword is more than a price. "Dash [cost]" is three abilities, and
only the first is the alternative cost; the other two give the permanent
haste and return it to hand at the next end step. The keyword builders wire
the price, and the cast records which keyword was paid for
(``alternative_cost_paid``, ``additional_costs_paid``) - the record that the
spell, and the permanent it becomes, carry for the rest of their lives. The
rest of each keyword is keyed on that record here, in one place, the way
flashback's exile is keyed on it as the spell leaves the stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..cr600_spells_and_abilities.abilities import Ability, AbilityKind, TriggerCondition
from ..cr600_spells_and_abilities.effects import Effect, EffectKind
from ..kernel.enums import Duration, Zone
from ..kernel.events import EventKind
from ..kernel.query import ObjectFilter, PlayerFilter, PlayerScope, Value

if TYPE_CHECKING:
    from ..kernel.game import Game
    from ..kernel.gameobject import GameObject

#: CR 603.7c: "it" in a delayed ability is the permanent the spell became, and
#: only that object - one that left and came back is a new object (CR 400.7).
_IT = ObjectFilter(remembered=True)
_SELF = ObjectFilter(source_only=True)
_YOU = PlayerFilter(PlayerScope.YOU)

#: The marker an exile effect carries for CR 702.185a's permission: the
#: owner may cast the card after this turn, for as long as it stays exiled.
WARPED = "warped"


def paid_additional_keywords(game: Game, obj: GameObject) -> list[str]:
    """The keywords of the optional additional costs paid for this spell, one
    entry per payment, lower-cased.

    ``additional_costs_paid`` holds indexes into the spell's additional costs,
    and an index appears once for each time that cost was paid (CR 702.56a
    replicate, 702.33c multikicker).
    """
    costs = game.characteristics(obj).additional_costs
    return [
        costs[index].keyword.lower()
        for index in obj.additional_costs_paid
        if 0 <= index < len(costs)
    ]


def times_paid(game: Game, obj: GameObject, keyword: str) -> int:
    return paid_additional_keywords(game, obj).count(keyword.lower())


def _at_the_next_end_step(*effects: Effect, text: str) -> Effect:
    return Effect(
        EffectKind.DELAYED_TRIGGER,
        trigger=TriggerCondition(
            event_kinds=frozenset({EventKind.END_STEP}),
            text="at the beginning of the next end step",
        ),
        children=effects,
        text=text,
    )


def _haste(text: str) -> Effect:
    # No duration: it lasts while this object is on the battlefield, and the
    # effect ends with the object (CR 400.7, 611.2c).
    return Effect(
        EffectKind.GRANT_ABILITY,
        targets=_SELF,
        granted_abilities=(Ability(AbilityKind.STATIC, keyword="Haste", text="Haste"),),
        duration=int(Duration.PERMANENT),
        text=text,
    )


def _when_it_dies_draw_a_card() -> Effect:
    """CR 702.152a: blitz gives the permanent "when this permanent is put into
    a graveyard from the battlefield, draw a card" - an ability it has, so
    one it loses with the rest of its abilities."""
    return Effect(
        EffectKind.GRANT_ABILITY,
        targets=_SELF,
        granted_abilities=(
            Ability.triggered(
                TriggerCondition(
                    event_kinds=frozenset({EventKind.DIES}),
                    subject=ObjectFilter(source_only=True, zones=frozenset()),
                    uses_last_known_information=True,
                    text="when this permanent is put into a graveyard from the battlefield",
                ),
                Effect(EffectKind.DRAW, players=_YOU, amount=Value.of(1), text="draw a card"),
                text="when this dies, draw a card",
            ),
        ),
        duration=int(Duration.PERMANENT),
        text="as long as its blitz cost was paid, it has \"when this dies, draw a card\"",
    )


def _riders(keyword: str) -> tuple[Effect, ...]:
    if keyword == "dash":
        # CR 702.109a: haste, and back to its owner's hand at the next end step.
        return (
            _haste("as long as its dash cost was paid, it has haste"),
            _at_the_next_end_step(
                Effect(EffectKind.RETURN_TO_HAND, targets=_IT, text="return it to hand"),
                text="return it to its owner's hand at the beginning of the next end step",
            ),
        )
    if keyword == "blitz":
        # CR 702.152a: haste and a draw when it dies, and sacrificed at the
        # next end step.
        return (
            _haste("as long as its blitz cost was paid, it has haste"),
            _when_it_dies_draw_a_card(),
            _at_the_next_end_step(
                Effect(EffectKind.SACRIFICE, targets=_IT, text="sacrifice it"),
                text="sacrifice it at the beginning of the next end step",
            ),
        )
    if keyword == "warp":
        # CR 702.185a: exiled at the next end step, and castable from exile
        # once this turn is over.
        return (
            _at_the_next_end_step(
                Effect(EffectKind.EXILE, targets=_IT, keywords=(WARPED,), text="exile it"),
                text="exile it at the beginning of the next end step",
            ),
        )
    return ()


def on_permanent_spell_resolved(game: Game, permanent: GameObject) -> None:
    """Apply the parts of a paid alternative cost that outlast the spell.

    Run as the permanent spell becomes a permanent (CR 608.3), with the
    permanent as the source, so its haste is its own and its delayed ability
    remembers exactly this object.
    """
    keyword = permanent.alternative_cost_paid.lower()
    effects = _riders(keyword)
    if not effects:
        return
    from ..cr600_spells_and_abilities.resolve import Resolution, execute

    resolution = Resolution(
        game=game,
        source=permanent.id,
        controller=permanent.controller,
        remembered=[permanent.id],
    )
    execute(resolution, effects)
    game.invalidate_characteristics()


def resolution_destination(game: Game, spell: GameObject) -> Zone | None:
    """Where an instant or sorcery goes as it resolves, if a paid cost says.

    CR 702.27a: a spell whose buyback cost was paid goes to its owner's hand
    rather than the graveyard. Only as it resolves - a countered one still
    goes to the graveyard.
    """
    if "buyback" in paid_additional_keywords(game, spell):
        return Zone.HAND
    return None


def entwined_modes(game: Game, spell: GameObject, modal: Effect | None) -> tuple[int, ...]:
    """CR 702.42a-b: every mode, in printed order, when entwine was paid."""
    if modal is None or "entwine" not in paid_additional_keywords(game, spell):
        return ()
    return tuple(range(len(modal.children)))


#: CR 702.56a replicate, 702.78a conspire: "when you cast this spell, if
#: [its cost] was paid, copy it" - once for each payment.
_COPIED_AS_CAST = {
    "replicate": "copy it for each time its replicate cost was paid",
    "conspire": "copy it",
}


def put_cast_triggers(game: Game, spell: GameObject) -> None:
    """CR 702.56a, 702.78a: the "when you cast this spell" copy triggers.

    Replicate copies the spell once for each time its cost was paid, and
    each paid conspire once (CR 702.78b). They trigger on the cast, so they
    go on the stack above the spell and can be responded to.
    """
    for keyword, text in _COPIED_AS_CAST.items():
        _put_copy_trigger(game, spell, keyword, text)


def _put_copy_trigger(game: Game, spell: GameObject, keyword: str, text: str) -> None:
    count = times_paid(game, spell, keyword)
    if not count:
        return
    from ..cr600_spells_and_abilities.cr603_triggers import PendingTrigger
    from ..kernel.events import Event

    ability = Ability.triggered(
        TriggerCondition(
            event_kinds=frozenset({EventKind.CAST_SPELL}),
            functions_in=frozenset({Zone.STACK}),
            text=f"when you cast this spell, if a {keyword} cost was paid for it",
        ),
        Effect(
            EffectKind.COPY_SPELL,
            targets=ObjectFilter(specific=(spell.id,), zones=frozenset({Zone.STACK})),
            amount=Value.of(count),
            text=text,
        ),
        text=keyword.capitalize(),
    )
    game.pending_triggers.append(
        PendingTrigger(
            spell.id,
            ability,
            Event(EventKind.CAST_SPELL, object_id=spell.id, player=spell.controller),
            spell.controller,
        )
    )


def _emerge_sacrifice(total) -> int:
    """The index of the sacrifice an emerge cost carries, or -1."""
    from ..cr100_game_concepts.cr118_costs import CostKind

    for index, component in enumerate(total.additional):
        if component.kind is CostKind.SACRIFICE and component.filter is not None:
            return index
    return -1


def apply_emerge(game: Game, spell: GameObject, player_id, total) -> None:
    """CR 702.119a, 702.119c: the creature to sacrifice is chosen as the
    emerge cost is chosen, and the total cost is reduced by its mana value.

    The reduction has to be known before the mana is paid (CR 601.2f), so the
    choice is made here and the sacrifice is pinned to that one permanent -
    paying the cost sacrifices exactly the creature the discount came from.
    """
    from dataclasses import replace

    from ..cr600_spells_and_abilities.cr601_casting import _choose_permanents

    index = _emerge_sacrifice(total)
    if index < 0:
        return
    component = total.additional[index]
    (victim,) = _choose_permanents(game, player_id, component, 1, spell)
    total.additional = (
        *total.additional[:index],
        replace(component, filter=ObjectFilter(specific=(victim.id,))),
        *total.additional[index + 1:],
    )
    total.reduction += game.characteristics(victim).mana_value


def emerge_discount(game: Game, player_id, alternative) -> int:
    """The most an emerge cost could be reduced by right now, for legality's
    upper bound on what a cast needs."""
    from ..kernel.matching import find

    if alternative.keyword.lower() != "emerge":
        return 0
    from ..cr100_game_concepts.cr118_costs import CostKind

    best = 0
    for component in alternative.cost.non_mana_components:
        if component.kind is not CostKind.SACRIFICE or component.filter is None:
            continue
        for obj in find(game, component.filter, controller=player_id):
            best = max(best, game.characteristics(obj).mana_value)
    return best
