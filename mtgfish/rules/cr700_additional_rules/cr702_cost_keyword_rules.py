"""Engine rules behind cost keywords that outlive the payment (CR 702).

``cr702_paid_costs`` applies what a paid cost does to the spell or the
permanent. This module holds the pieces the rest of the engine reads while a
keyword plays out: how many times a cost was paid and what repeats per payment
(multikicker, squad), the payment record a permanent inherits as it enters,
the token copy a squad or offspring ability creates, what combat damage was
dealt by this turn (prowl, freerunning), and the opponent a gift was promised
to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..kernel.enums import CardType
from ..kernel.events import Event, EventKind
from ..kernel.ids import NO_PLAYER, PlayerId

if TYPE_CHECKING:
    from ..cr600_spells_and_abilities.effects import Effect, TokenSpec
    from ..cr600_spells_and_abilities.resolve import Resolution
    from ..kernel.game import Game
    from ..kernel.gameobject import GameObject


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


def carry_payment_record(spell: GameObject, permanent: GameObject) -> None:
    """Give the permanent a spell becomes the record of how it was paid for.

    Done as it enters, before anything sees it arrive: "when this enters, if
    it was kicked" is checked as the ability triggers (CR 603.4), and an
    "enters with" replacement as the permanent enters (CR 614.1c).
    """
    # CR 702.33e, 702.157b, 702.175b: "if it was kicked", "if its squad cost
    # was paid", "if its offspring cost was paid" ask the permanent about the
    # spell it was.
    permanent.additional_costs_paid = spell.additional_costs_paid
    permanent.alternative_cost_paid = spell.alternative_cost_paid
    permanent.mana_spent = spell.mana_spent
    permanent.mana_spent_colors = spell.mana_spent_colors
    permanent.phyrexian_paid_with_life = spell.phyrexian_paid_with_life
    # CR 702.174a: the opponent a gift was promised to.
    permanent.chosen_player = spell.chosen_player


def times_paid_for(game: Game, resolution: Resolution, keyword: str) -> int:
    """How many times the resolving object's ``keyword`` cost was paid.

    The spell resolving, or the permanent whose ability this is - as it
    last existed if it has left (CR 608.2h).
    """
    from .cr702_paid_costs import times_paid

    obj = resolution.stack_object
    if obj is None or obj.ability is not None:
        obj = game.objects.get(resolution.source)
    if obj is None:
        return 0
    return times_paid(game, obj, keyword)


def do_repeat_per_payment(resolution: Resolution, effect: Effect) -> None:
    """CR 702.33d, 702.157a: "for each time it was kicked" - the children
    once for each payment of the cost named by ``keywords[0]``."""
    from ..cr600_spells_and_abilities.resolve import execute

    if not effect.keywords:
        return
    for _ in range(times_paid_for(resolution.game, resolution, effect.keywords[0])):
        execute(resolution, effect.children)


def chosen_cost(game: Game, player_id: PlayerId, cost, spell: GameObject):
    """The cost actually paid when a cost is written as a choice of costs.

    CR 702.166a: bargain is "sacrifice an artifact, enchantment, or token",
    which no single filter can say, so it is written as a choice. With
    nothing to ask, the first choice whose components can all be paid is
    taken; when none can, the first, whose payment then fails (CR 601.2h).
    """
    if not cost.choices:
        return cost
    from ..cr600_spells_and_abilities.cr601_casting import CastError, _check_payable

    for choice in cost.choices:
        try:
            for component in choice.non_mana_components:
                _check_payable(game, player_id, component, spell, spell=spell)
        except CastError:
            continue
        return choice
    return cost.choices[0]


def chosen_mode_costs(game: Game, spell: GameObject) -> list:
    """CR 702.172a: the costs of the spree modes chosen for this spell."""
    from ..cr600_spells_and_abilities.cr601_casting import modal_effect, spell_effects

    modal = modal_effect(spell_effects(game, spell))
    if modal is None or not modal.mode_costs:
        return []
    costs = modal.mode_costs
    return [costs[index] for index in spell.chosen_modes if 0 <= index < len(costs)]


def cheapest_mode_cost(game: Game, obj: GameObject):
    """The least a spree spell can cost on top of its mana cost - its
    cheapest mode, since one is always chosen - or None for any other."""
    from ..cr600_spells_and_abilities.cr601_casting import modal_effect, spell_effects

    modal = modal_effect(spell_effects(game, obj))
    if modal is None or not modal.mode_costs:
        return None
    return min(modal.mode_costs, key=lambda cost: cost.mana_component.mana_value)


#: CR 702.162a: alternative costs that cast the card converted.
CONVERTED_CAST_KEYWORDS = frozenset({"more than meets the eye"})


def cast_face_index(spell: GameObject, alternative, face_index: int) -> int:
    """The face a spell is cast with: its back face when an alternative cost
    casts it converted (CR 712.8c), whichever face was asked for."""
    if alternative is None or alternative.keyword.lower() not in CONVERTED_CAST_KEYWORDS:
        return face_index
    if len(getattr(spell.card, "faces", ())) < 2:
        return face_index
    return 1


# ---------------------------------------------------------------------------
# Token copies (CR 707.2, 707.9)
# ---------------------------------------------------------------------------


def copy_token_card(game: Game, original: GameObject, spec: TokenSpec):
    """The card a token copy of ``original`` is built from.

    CR 707.2: the copiable values, fixed as the token is created. CR 707.9b:
    a power and toughness on the spec are the copy's exception ("except it's
    1/1") and are part of those values.
    """
    from ..cr100_game_concepts.cr111_tokens import SyntheticCard, _SyntheticFace
    from ..cr600_spells_and_abilities.cr613_layers import intrinsic_abilities
    from .cr707_faces import copiable_characteristics

    chars = copiable_characteristics(game, original)
    power, toughness = chars.power, chars.toughness
    if not (spec.power.is_constant and spec.power.constant == 0) or not (
        spec.toughness.is_constant and spec.toughness.constant == 0
    ):
        from ..kernel.values import evaluate

        power = evaluate(game, spec.power, source=original.id)
        toughness = evaluate(game, spec.toughness, source=original.id)
    # The type line's own abilities come back with the type line (CR 305.6).
    intrinsic = intrinsic_abilities(chars.type_line)
    abilities = tuple(a for a in chars.abilities if a not in intrinsic)

    def text(value):
        return None if value is None else str(value)

    face = _SyntheticFace(
        name=chars.name,
        mana_cost=chars.mana_cost,
        has_mana_cost=chars.has_mana_cost,
        type_line=chars.type_line,
        oracle_text=chars.text,
        power=text(power),
        toughness=text(toughness),
        loyalty=text(chars.loyalty),
        defense=text(chars.defense),
        colors=chars.colors,
        color_indicator=chars.colors,
    )
    return SyntheticCard(
        name=chars.name,
        faces=(face,),
        abilities=abilities,
        mana_value=chars.mana_cost.mana_value,
    )


def copy_originals(resolution: Resolution, effect: Effect) -> list[GameObject]:
    """The objects a "token that's a copy of ..." copies."""
    from dataclasses import replace

    from ..cr600_spells_and_abilities.resolve import _objects

    spec = effect.token.copy_of
    game = resolution.game
    if spec.source_only:
        # CR 608.2h: "a copy of it" after it has left uses the object as it
        # last existed.
        obj = game.objects.get(resolution.source)
        return [obj] if obj is not None else []
    return _objects(resolution, replace(effect, targets=spec))


# ---------------------------------------------------------------------------
# Combat damage dealt by (CR 702.76a prowl, 702.173a freerunning)
# ---------------------------------------------------------------------------

#: The turn history key for combat damage dealt to a player, followed by the
#: source's controller, whether it was a commander, and its creature types.
COMBAT_DAMAGE_BY = "combat damage by"


def record_combat_damage_source(
    game: Game, source_id, source_controller: PlayerId
) -> None:
    """Remember what dealt combat damage to a player, as it was then.

    Prowl and freerunning ask what the source was "at the time it dealt that
    damage", so the answer is taken now: a Rogue that stops being a Rogue
    afterwards has still dealt damage as one.
    """
    obj = game.objects.get(source_id)
    if obj is None or source_controller == NO_PLAYER:
        return
    chars = game.characteristics(obj)
    creature_types = tuple(sorted(chars.type_line.creature_types()))
    key = (
        COMBAT_DAMAGE_BY,
        int(source_controller),
        bool(obj.is_commander),
        bool(chars.type_line.types & CardType.CREATURE),
        creature_types,
    )
    game.turn_history[key] = game.turn_history.get(key, 0) + 1


def _damage_records(game: Game, controller: PlayerId):
    for key, count in game.turn_history.items():
        if (
            count
            and isinstance(key, tuple)
            and key
            and key[0] == COMBAT_DAMAGE_BY
            and key[1] == int(controller)
        ):
            yield key[2], key[3], key[4]


def combat_damage_dealt_by(game: Game, condition, controller: PlayerId) -> bool:
    """COMBAT_DAMAGE_DEALT_BY: a player was dealt combat damage this turn by
    a source you controlled that matched the condition's filter then.

    The filter is asked about the recorded source: its creature types
    (``subtypes_any``), whether it was a commander (``is_commander``) and
    whether it was a creature. Several conditions joined by OR say "an
    Assassin or a commander".
    """
    spec = condition.filter
    for was_commander, was_creature, creature_types in _damage_records(game, controller):
        if spec is None:
            return True
        if spec.is_commander is not None and spec.is_commander != was_commander:
            continue
        if spec.types_all & CardType.CREATURE and not was_creature:
            continue
        wanted = {name.lower() for name in spec.subtypes_any}
        if wanted and not wanted & {name.lower() for name in creature_types}:
            continue
        return True
    return False


def combat_damage_by_shared_type(game: Game, source_id, controller: PlayerId) -> bool:
    """COMBAT_DAMAGE_BY_SHARED_CREATURE_TYPE: CR 702.76a - by a source you
    controlled that had any of this object's creature types then."""
    obj = game.objects.get(source_id)
    if obj is None:
        return False
    mine = {name.lower() for name in game.characteristics(obj).type_line.creature_types()}
    if not mine:
        return False
    return any(
        mine & {name.lower() for name in creature_types}
        for _, _, creature_types in _damage_records(game, controller)
    )


# ---------------------------------------------------------------------------
# Gift (CR 702.174)
# ---------------------------------------------------------------------------


def promise_gift(game: Game, spell: GameObject, player_id: PlayerId) -> None:
    """CR 702.174a: promising a gift is choosing an opponent, as the spell is
    cast. The choice is the spell's, and the permanent's it becomes."""
    from .cr702_paid_costs import paid_additional_keywords

    if "gift" not in paid_additional_keywords(game, spell):
        return
    opponents = [
        p.id for p in game.living_players if p.id != player_id
    ]
    if not opponents:
        return
    agent = game.agent_for(player_id)
    chooser = getattr(agent, "choose_gift_recipient", None)
    chosen = chooser(game, player_id, opponents) if chooser is not None else None
    spell.chosen_player = chosen if chosen in opponents else opponents[0]


def do_give_gift(resolution: Resolution, effect: Effect) -> None:
    """CR 702.174b, 702.174d-i: the promised gift, given to the chosen opponent.

    The children are carried out with that opponent as "that player"
    (``PlayerScope.THAT_PLAYER``). Nothing happens when no gift was promised.
    """
    from ..cr600_spells_and_abilities.resolve import execute

    game = resolution.game
    obj = resolution.stack_object
    if obj is None or obj.ability is not None:
        obj = game.objects.get(resolution.source)
    if obj is None or obj.chosen_player == NO_PLAYER:
        return
    recipient = obj.chosen_player
    if game.player(recipient).has_lost:
        return
    before = resolution.that_player
    resolution.that_player = recipient
    try:
        execute(resolution, effect.children)
    finally:
        resolution.that_player = before
    # CR 702.174c: "whenever you give a gift".
    game.emit(
        Event(
            EventKind.GIFT_GIVEN,
            object_id=obj.id,
            player=resolution.controller,
            data=(int(recipient),),
        )
    )
