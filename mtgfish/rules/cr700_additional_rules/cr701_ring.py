"""CR 701.54: the Ring tempts you.

Each time the Ring tempts a player, they get the emblem named The Ring if they
have none (CR 701.54c), choose a creature they control to be their Ring-bearer
(CR 701.54a), and the count of temptations - which decides how many of the
Ring's abilities it has - goes up by one.

The Ring's abilities are defined by the rule itself, so they are built here
from the same pieces any card's abilities are: a layer-4 effect, a combat
restriction read by ``cr506_combat``, and three triggered abilities gated on
how often the Ring has tempted its owner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..cr600_spells_and_abilities.abilities import Ability, AbilityKind, TriggerCondition
from ..cr600_spells_and_abilities.effects import Effect, EffectKind
from ..kernel.enums import Step, Supertype, Zone
from ..kernel.events import Event, EventKind
from ..kernel.gameobject import GameObject
from ..kernel.ids import NO_OBJECT, PlayerId
from ..kernel.query import (
    EACH_OPPONENT,
    YOU,
    Comparison,
    Condition,
    ConditionKind,
    NumericConstraint,
    ObjectFilter,
    Value,
)

if TYPE_CHECKING:
    from ..kernel.game import Game

#: CR 701.54e: "your Ring-bearer" - read against the controller of the
#: ability asking, which for the Ring's own abilities is the emblem's owner.
YOUR_RING_BEARER = ObjectFilter(ring_bearer=True, zones=frozenset({Zone.BATTLEFIELD}))

_COMMAND = frozenset({Zone.COMMAND})


def _tempted_at_least(times: int) -> Condition:
    """CR 701.54c: "as long as the Ring has tempted that player N or more
    times". The count never falls, so gating the trigger on it is the same as
    the ability appearing when the count is reached."""
    return Condition(
        kind=ConditionKind.RING_TEMPTED_TIMES,
        constraint=NumericConstraint(Comparison.GE, Value.of(times)),
        text=f"the Ring has tempted you {times} or more times",
    )


def ring_abilities() -> tuple[Ability, ...]:
    """The abilities of the emblem named The Ring (CR 701.54c)."""
    legendary = Ability(
        AbilityKind.STATIC,
        effects=(
            Effect(
                EffectKind.ADD_TYPE,
                targets=YOUR_RING_BEARER,
                supertypes=Supertype.LEGENDARY,
                text="your Ring-bearer is legendary",
            ),
        ),
        functions_in=_COMMAND,
        text="Your Ring-bearer is legendary and can't be blocked by creatures "
        "with greater power.",
    )
    loot = Ability(
        AbilityKind.TRIGGERED,
        effects=(
            Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1), text="draw a card"),
            Effect(EffectKind.DISCARD, players=YOU, amount=Value.of(1), text="discard a card"),
        ),
        trigger=TriggerCondition(
            event_kinds=frozenset({EventKind.ATTACKS}),
            subject=YOUR_RING_BEARER,
            intervening_if=_tempted_at_least(2),
            functions_in=_COMMAND,
            text="whenever your Ring-bearer attacks",
        ),
        functions_in=_COMMAND,
        text="Whenever your Ring-bearer attacks, draw a card, then discard a card.",
    )
    blockers = Ability(
        AbilityKind.TRIGGERED,
        effects=(
            Effect(
                EffectKind.SACRIFICE_BLOCKERS_AT_END_OF_COMBAT,
                text="the blocking creature's controller sacrifices it at end of combat",
            ),
        ),
        trigger=TriggerCondition(
            event_kinds=frozenset({EventKind.BECOMES_BLOCKED}),
            subject=YOUR_RING_BEARER,
            intervening_if=_tempted_at_least(3),
            functions_in=_COMMAND,
            text="whenever your Ring-bearer becomes blocked by a creature",
        ),
        functions_in=_COMMAND,
        text="Whenever your Ring-bearer becomes blocked by a creature, the "
        "blocking creature's controller sacrifices it at end of combat.",
    )
    drain = Ability(
        AbilityKind.TRIGGERED,
        effects=(
            Effect(
                EffectKind.LOSE_LIFE,
                players=EACH_OPPONENT,
                amount=Value.of(3),
                text="each opponent loses 3 life",
            ),
        ),
        trigger=TriggerCondition(
            event_kinds=frozenset({EventKind.COMBAT_DAMAGE_DEALT}),
            source=YOUR_RING_BEARER,
            to_player=True,
            intervening_if=_tempted_at_least(4),
            functions_in=_COMMAND,
            text="whenever your Ring-bearer deals combat damage to a player",
        ),
        functions_in=_COMMAND,
        text="Whenever your Ring-bearer deals combat damage to a player, each "
        "opponent loses 3 life.",
    )
    return (legendary, loot, blockers, drain)


def ring_bearer(game: Game, player_id: PlayerId) -> GameObject | None:
    """CR 701.54a/e: this player's Ring-bearer, if they have one.

    The designation ends when another player gains control of the creature,
    and it is not given back if control returns, so it is cleared the moment
    that is seen rather than merely ignored.
    """
    player = game.player(player_id)
    obj = game.objects.get(player.ring_bearer)
    if obj is None:
        return None
    if not obj.is_live or obj.zone is not Zone.BATTLEFIELD or obj.controller != player_id:
        player.ring_bearer = NO_OBJECT
        return None
    return obj


def is_ring_bearer_of(game: Game, obj: GameObject, player_id: PlayerId) -> bool:
    bearer = ring_bearer(game, player_id)
    return bearer is not None and bearer.id == obj.id


def is_a_ring_bearer(game: Game, obj: GameObject) -> bool:
    """Whether the object is any player's Ring-bearer."""
    return is_ring_bearer_of(game, obj, obj.controller)


def tempt(game: Game, player_id: PlayerId) -> None:
    """CR 701.54a/c/d: the Ring tempts this player."""
    from ..cr100_game_concepts.cr111_tokens import create_emblem

    player = game.player(player_id)
    emblem = game.objects.get(player.ring_emblem)
    if emblem is None or not emblem.is_live or emblem.zone is not Zone.COMMAND:
        emblem = create_emblem(game, player_id, ring_abilities(), text="The Ring")
        player.ring_emblem = emblem.id

    creatures = [
        obj for obj in game.permanents(player_id) if game.characteristics(obj).is_creature
    ]
    chosen = _choose_ring_bearer(game, player_id, creatures)
    if chosen is not None:
        player.ring_bearer = chosen.id
        game.log.record(
            game, f"{chosen} becomes {player.name}'s Ring-bearer", kind="ring", player=player_id
        )
    player.ring_tempted_count += 1
    game.invalidate_characteristics()
    # CR 701.54d: the Ring has tempted the player even if there was no
    # creature to choose.
    game.emit(Event(EventKind.RING_TEMPTED, player=player_id, amount=player.ring_tempted_count))


def _choose_ring_bearer(
    game: Game, player_id: PlayerId, creatures: list[GameObject]
) -> GameObject | None:
    """The controller chooses (CR 701.54a). With no agent to ask, the creature
    with the greatest power - the Ring's evasion is against greater power -
    and then the lowest id, so a replay chooses the same way."""
    if not creatures:
        return None
    agent = game.agent_for(player_id)
    chooser = getattr(agent, "choose_ring_bearer", None)
    if chooser is not None:
        picked = chooser(game, player_id, list(creatures))
        if picked in creatures:
            return picked
    return max(creatures, key=lambda o: (game.characteristics(o).power or 0, -o.id))


def sacrifice_blockers_at_end_of_combat(game: Game, attacker_id, controller: PlayerId) -> None:
    """The Ring's third ability: each creature blocking the attacker is
    sacrificed by its controller at end of combat - the beginning of the end
    of combat step (CR 511.2)."""
    from ..cr500_turn_structure.cr506_combat import _combat
    from ..cr600_spells_and_abilities.abilities import DelayedTrigger

    blockers = tuple(_combat(game).blockers.get(attacker_id, ()))
    if not blockers:
        return
    game.delayed_triggers.append(
        DelayedTrigger(
            trigger=TriggerCondition(
                event_kinds=frozenset({EventKind.STEP_BEGAN}),
                steps=frozenset({int(Step.END_OF_COMBAT)}),
                text="at end of combat",
            ),
            effects=(
                Effect(
                    EffectKind.SACRIFICE,
                    targets=ObjectFilter(specific=blockers),
                    text="its controller sacrifices the blocking creature",
                ),
            ),
            controller=controller,
            source=NO_OBJECT,
        )
    )


__all__ = [
    "YOUR_RING_BEARER",
    "is_a_ring_bearer",
    "is_ring_bearer_of",
    "ring_abilities",
    "ring_bearer",
    "sacrifice_blockers_at_end_of_combat",
    "tempt",
]
