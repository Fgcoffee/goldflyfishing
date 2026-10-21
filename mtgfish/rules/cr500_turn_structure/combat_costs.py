"""Costs to attack and to block (CR 508.1c-d, 508.1h-j, 509.1b-f).

"Creatures can't attack you unless their controller pays {2} for each creature
they control that's attacking you" is not a prohibition and not a resolving
"unless that player pays". It is a restriction that a cost lifts (CR 508.1c:
"effects that say a creature can't attack ... unless some condition is
met"), and the cost is part of declaring attackers: the active player
determines the total cost to attack (508.1h), may activate mana abilities
(508.1i), and pays it all at once with no partial payment (508.1j). Blocking
is the same with the defending player (509.1d-f).

Read by the parser as an ``UNLESS_PAYS`` whose children are ``RESTRICTION``
effects on attacking or blocking, inside a static ability - optionally behind
an "as long as" gate. Nothing else gathers these: ``restrictions._active``
looks only at top-level ``RESTRICTION`` effects, so a tax is never mistaken
for an absolute "can't".

Three shapes of per-creature amount are understood:

* a flat cost - "Enchanted creature can't attack unless its controller pays
  {3}", "this creature can't attack or block unless you pay {2}";
* a cost "for each of those creatures" / "for each creature they control
  that's attacking you" - which is the flat cost charged once per creature
  the restriction covers, and nothing more: charging each attacker the
  scaled total would square it;
* a cost "for each" of something that does not depend on the combat -
  "for each card in your hand", "for each +1/+1 counter on it" - evaluated
  for each creature the restriction covers, as the cost is determined.

``tax_refusal`` names every other shape so the parser refuses the card rather
than having it read and never charged.

CR 508.1d / 509.1c: a player is never required to pay a cost to attack or
block, so a requirement ("attacks each combat if able", goad) never forces a
creature that would have to pay into combat.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from ..cr100_game_concepts.cr106_mana import ManaCost
from ..kernel.ids import NO_OBJECT, ObjectId, PlayerId
from ..kernel.query import ALWAYS, Condition, ConditionKind, ControllerRelation, Value
from .restrictions import Act, Restriction, restriction_applies

if TYPE_CHECKING:
    from ..cr600_spells_and_abilities.abilities import Ability
    from ..cr600_spells_and_abilities.effects import Effect
    from ..kernel.game import Game
    from ..kernel.gameobject import GameObject


#: The acts a combat tax can lift.
ATTACK_ACTS = frozenset({Act.ATTACK, Act.ATTACK_PLAYER, Act.ATTACK_PLANESWALKER})
COMBAT_ACTS = ATTACK_ACTS | {Act.BLOCK}


@dataclass(frozen=True, slots=True)
class CombatTax:
    """One "can't attack/block unless [cost]" in force."""

    #: The restrictions the payment lifts, with source, controller and gate
    #: filled in - matched exactly as a prohibition would be.
    restrictions: tuple[Restriction, ...]
    #: The cost for one creature the restriction covers, before any scale.
    mana: ManaCost
    #: A multiplier that is not the creatures themselves ("for each card in
    #: your hand"), evaluated as the cost is determined. ``None`` means the
    #: cost is charged once per creature covered.
    scale: Value | None
    source: ObjectId
    controller: PlayerId
    text: str


# ---------------------------------------------------------------------------
# What the engine can charge
# ---------------------------------------------------------------------------


def _is_combat_tax(effect: Effect) -> bool:
    from ..cr600_spells_and_abilities.effects import EffectKind

    return (
        effect.kind is EffectKind.UNLESS_PAYS
        and bool(effect.children)
        and any(child.kind is EffectKind.RESTRICTION for child in effect.children)
    )


def _gated_taxes(effects, gate: Condition):
    """Each combat tax in a static ability, with the "as long as" gating it."""
    from ..cr600_spells_and_abilities.effects import EffectKind

    for effect in effects:
        if _is_combat_tax(effect):
            yield effect, gate
        elif effect.kind is EffectKind.CONDITIONAL and not effect.otherwise:
            inner = effect.condition
            if not gate.is_always:
                inner = Condition(kind=ConditionKind.AND, operands=(gate, inner), text="and")
            yield from _gated_taxes(effect.children, inner)


def tax_refusal(ability: Ability) -> str | None:
    """Why the engine could not charge a combat tax in ``ability``, or None.

    The parser asks this of every ability it reads: a "can't attack unless
    ... pays" the engine does not charge is a card read as something it is
    not, and has to be refused instead.
    """
    from ..cr600_spells_and_abilities.abilities import AbilityKind

    taxes = [node for effect in ability.effects for node in effect.walk() if _is_combat_tax(node)]
    if not taxes:
        return None
    if ability.kind is not AbilityKind.STATIC:
        # "This turn, creatures can't attack unless ..." from a resolving
        # spell would be asked once at resolution, which is not the card.
        return "a cost to attack or block outside a static ability"
    reachable = {id(effect) for effect, _ in _gated_taxes(ability.effects, ALWAYS)}
    for node in taxes:
        if id(node) not in reachable:
            return "a cost to attack or block nested where it is not continuous"
        reason = _shape_refusal(node)
        if reason:
            return reason
    return None


def _shape_refusal(effect: Effect) -> str | None:
    from ..cr100_game_concepts.cr118_costs import CostKind
    from ..cr600_spells_and_abilities.effects import EffectKind
    from ..kernel.query import PlayerScope

    if not all(child.kind is EffectKind.RESTRICTION for child in effect.children):
        return "a cost to attack or block guarding something else as well"
    restrictions = [
        (restriction, child) for child in effect.children for restriction in child.restrictions
    ]
    if not restrictions or any(r.act not in COMBAT_ACTS for r, _ in restrictions):
        return "a cost lifting a restriction other than attacking or blocking"
    cost = effect.pay_cost
    if cost is None or cost.choices or len(cost.components) != 1:
        return "a combat cost the engine cannot charge"
    component = cost.components[0]
    if component.kind is not CostKind.MANA or not component.mana:
        return "a combat cost other than mana"
    if component.mana.variable_count:
        return "a combat cost with an unbound {X}"
    payer = effect.players
    if payer is None:
        return "a combat cost with no payer"
    if payer.scope is PlayerScope.YOU:
        # "This creature can't attack unless you pay {2}": the payer is the
        # creature's controller, who declares it. "Creatures can't attack
        # unless you pay" would be someone else paying for an attack.
        if any(not _subject(r, child).source_only for r, child in restrictions):
            return "a combat cost paid by someone other than the declaring player"
    elif not (payer.scope is PlayerScope.CONTROLLER_OF and not payer.reference):
        return "a combat cost paid by someone other than the declaring player"
    if component.scale is not None and _scale_kind(component.scale, restrictions) is None:
        return "a combat cost scaled by something the engine cannot count"
    return None


def _subject(restriction, child):
    from ..kernel.query import ObjectFilter

    return restriction.subject or child.targets or ObjectFilter(source_only=True)


_PER_CREATURE = "per creature"
_INDEPENDENT = "independent"
#: A product of one per-creature factor and independent ones.
_PRODUCT = "per creature, times"


def _independent_part(scale: Value, restrictions) -> Value | None:
    """The multiplier left once the per-creature factor is charged by
    counting creatures: ``None`` for a plain per-creature count."""
    kind = _scale_kind(scale, restrictions)
    if kind == _PER_CREATURE:
        return None
    if kind == _INDEPENDENT:
        return scale
    rest = tuple(
        operand
        for operand in scale.operands
        if _scale_kind(operand, restrictions) != _PER_CREATURE
    )
    return rest[0] if len(rest) == 1 else replace(scale, operands=rest)


def _scale_kind(scale: Value, restrictions) -> str | None:
    """How a "for each" on a combat cost multiplies it.

    ``_PER_CREATURE`` when it counts the very creatures the restriction
    covers ("for each of those creatures", "for each creature they control
    that's attacking you" on "creatures can't attack you"), ``_INDEPENDENT``
    when it counts something that has nothing to do with this combat, and
    ``None`` otherwise - "nonblack creatures can't attack you unless their
    controller pays {2} for each creature they control that's attacking you"
    counts black attackers too, and neither reading is charged.
    """
    from ..kernel.query import ValueKind

    if scale.kind is ValueKind.PRODUCT and scale.constant in (0, 1):
        # "{X} for each of those creatures, where X is ...": one factor per
        # creature covered, the rest independent of the combat.
        kinds = [_scale_kind(operand, restrictions) for operand in scale.operands]
        if None in kinds or kinds.count(_PER_CREATURE) > 1:
            return None
        return _PRODUCT if _PER_CREATURE in kinds else _INDEPENDENT
    filt = scale.filter
    if scale.kind is ValueKind.COUNT and filt is not None and not scale.operands:
        if filt.remembered:
            stripped = replace(filt, remembered=False)
            if all(stripped == _subject(r, c) for r, c in restrictions):
                return _PER_CREATURE
            return None
        acts = {r.act for r, _ in restrictions}
        in_combat = (
            filt.attacking if acts <= ATTACK_ACTS else filt.blocking if acts == {Act.BLOCK} else None
        )
        if in_combat:
            stripped = replace(
                filt,
                attacking=None,
                blocking=None,
                controller=ControllerRelation.ANY,
                controller_specific=None,
            )
            if all(stripped == _subject(r, c) for r, c in restrictions):
                return _PER_CREATURE
            return None
    if _mentions_combat(scale):
        return None
    return _INDEPENDENT


def _mentions_combat(value: Value) -> bool:
    filt = value.filter
    if filt is not None and (
        filt.remembered
        or filt.attacking is not None
        or filt.blocking is not None
        or filt.blocked is not None
    ):
        return True
    return any(_mentions_combat(operand) for operand in value.operands)


# ---------------------------------------------------------------------------
# The taxes in force
# ---------------------------------------------------------------------------


def combat_taxes(game: Game) -> list[CombatTax]:
    """Every combat tax in force, rebuilt from live static abilities (CR 611.3)."""
    from ..cr600_spells_and_abilities.abilities import AbilityKind

    out: list[CombatTax] = []
    for object_id in list(game.battlefield):
        obj = game.objects.get(object_id)
        if obj is None or obj.phased_out:
            continue
        for ability in game.characteristics(obj).abilities:
            if ability.kind is not AbilityKind.STATIC or ability.unparsed:
                continue
            if not ability.functions_in_zone(obj.zone):
                continue
            for effect, gate in _gated_taxes(ability.effects, ability.static_condition):
                if _shape_refusal(effect) is not None:
                    # The parser refuses these; a hand-built ability that
                    # slips through is not charged as something else.
                    continue
                component = effect.pay_cost.components[0]
                restrictions = [
                    (restriction, child)
                    for child in effect.children
                    for restriction in child.restrictions
                ]
                scale = component.scale
                if scale is not None:
                    scale = _independent_part(scale, restrictions)
                out.append(
                    CombatTax(
                        restrictions=tuple(
                            Restriction(
                                act=restriction.act,
                                subject=restriction.subject or child.targets,
                                players=restriction.players or child.players,
                                source=obj.id,
                                controller=obj.controller,
                                condition=gate,
                                counterpart=restriction.counterpart,
                                text=restriction.text or child.text,
                            )
                            for restriction, child in restrictions
                        ),
                        mana=component.mana,
                        scale=scale,
                        source=obj.id,
                        controller=obj.controller,
                        text=ability.text,
                    )
                )
    return out


def _times(mana: ManaCost, count: int) -> ManaCost:
    return ManaCost(mana.symbols * max(0, count))


def _charge(game: Game, tax: CombatTax) -> ManaCost:
    if tax.scale is None:
        return tax.mana
    from ..kernel.values import evaluate

    return _times(tax.mana, evaluate(game, tax.scale, source=tax.source, controller=tax.controller))


def attack_cost(
    game: Game, obj: GameObject, player: PlayerId, permanent: ObjectId = NO_OBJECT
) -> ManaCost:
    """What attacking ``player`` (or the ``permanent`` they defend) with
    ``obj`` costs (CR 508.1h). Empty when it is free."""
    total = ManaCost(())
    attacked = game.objects.get(permanent) if permanent != NO_OBJECT else None
    for tax in combat_taxes(game):
        for restriction in tax.restrictions:
            if restriction.act is Act.ATTACK:
                hit = restriction_applies(game, restriction, Act.ATTACK, obj=obj)
            elif restriction.act is Act.ATTACK_PLAYER:
                hit = attacked is None and restriction_applies(
                    game, restriction, Act.ATTACK_PLAYER, obj=obj, player=player
                )
            elif restriction.act is Act.ATTACK_PLANESWALKER:
                hit = attacked is not None and restriction_applies(
                    game, restriction, Act.ATTACK_PLANESWALKER, obj=obj, counterpart=attacked
                )
            else:
                hit = False
            if hit:
                # One restriction lifted is one cost: "you or planeswalkers
                # you control" is one tax, and a creature attacks one thing.
                total = total.plus(_charge(game, tax))
                break
    return total


def block_cost(game: Game, obj: GameObject) -> ManaCost:
    """What blocking with ``obj`` costs (CR 509.1d). Empty when it is free.

    Charged once per blocking creature, however many attackers it blocks:
    the restriction is on the creature blocking, not on each block.
    """
    total = ManaCost(())
    for tax in combat_taxes(game):
        if any(
            restriction.act is Act.BLOCK
            and restriction_applies(game, restriction, Act.BLOCK, obj=obj)
            for restriction in tax.restrictions
        ):
            total = total.plus(_charge(game, tax))
    return total


# ---------------------------------------------------------------------------
# Paying
# ---------------------------------------------------------------------------


def _as_cost(mana: ManaCost):
    from ..cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind

    return Cost((CostComponent(CostKind.MANA, mana=mana),))


def affordable(
    game: Game,
    player: PlayerId,
    costs: dict[ObjectId, ManaCost],
    *,
    tapped: tuple[ObjectId, ...] = (),
) -> dict[ObjectId, ManaCost]:
    """The taxed creatures the player will pay for: ``costs`` with the most
    expensive dropped until the total can be paid.

    ``tapped`` are creatures that will be tapped by the declaration itself
    (CR 508.1f taps attackers before costs are paid), so their mana
    abilities are not counted on. The agent may decline any of it
    (``choose_combat_costs``); a player is never required to pay (CR 508.1d,
    509.1c).
    """
    from ..cr600_spells_and_abilities.cr601_casting import can_pay_cost

    chosen = {object_id: mana for object_id, mana in costs.items() if mana}
    agent = game.agent_for(player)
    chooser = getattr(agent, "choose_combat_costs", None) if agent is not None else None
    if chooser is not None and chosen:
        willing = set(chooser(game, player, dict(chosen)) or ())
        chosen = {k: v for k, v in chosen.items() if k in willing}

    flipped = []
    for object_id in tapped:
        obj = game.objects.get(object_id)
        if obj is not None and not obj.tapped:
            obj.tapped = True
            flipped.append(obj)
    try:
        while chosen:
            total = _total(chosen)
            if can_pay_cost(game, player, _as_cost(total)):
                break
            dearest = max(chosen, key=lambda k: (chosen[k].mana_value, k))
            del chosen[dearest]
    finally:
        for obj in flipped:
            obj.tapped = False
    return chosen


def _total(costs: dict[ObjectId, ManaCost]) -> ManaCost:
    total = ManaCost(())
    for object_id in sorted(costs):
        total = total.plus(costs[object_id])
    return total


def pay(game: Game, player: PlayerId, costs: dict[ObjectId, ManaCost], *, what: str) -> bool:
    """CR 508.1i-j / 509.1e-f: pay the locked-in total, all or nothing."""
    from ..cr600_spells_and_abilities.cr601_casting import pay_cost

    total = _total(costs)
    if not total:
        return True
    if not pay_cost(game, player, _as_cost(total)):
        return False
    game.log.record(game, f"player {player} pays {total} to {what}", kind="cost")
    return True


__all__ = [
    "ATTACK_ACTS",
    "COMBAT_ACTS",
    "CombatTax",
    "affordable",
    "attack_cost",
    "block_cost",
    "combat_taxes",
    "pay",
    "tax_refusal",
]

