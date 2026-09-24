"""Cards that beat the rules (CR 101.1).

CR 101.1 is the golden rule: when a card's text contradicts these rules, the
card wins. It is the one rule an engine cannot implement in general, because
card text here compiles into a fixed vocabulary of effects - a card can only
beat a rule the engine has left a seam for, and a card whose text contradicts
a rule in a way the opcode set cannot express does not beat the rule, it fails
to parse.

The engine already has three such seams:

- **CR 614, replacement effects.** "If a creature would die, exile it
  instead." The event is rewritten before it happens.
- **CR 101.2, prohibitions.** ``restrictions.py``'s ``Act`` list: "creatures
  can't attack you unless their controller pays {2}". Also the permissions
  written alongside them, for the handful of rules whose override reads more
  naturally as "you may" than as "can't".
- **CR 611, continuous effects.** Anything that changes what an object *is* -
  its characteristics, its abilities, who controls it.

This module is the fourth, for the rules none of those reach: the ones the
engine applies to the game itself rather than to an object. "Creatures you
control have haste" is a continuous effect and belongs in CR 611; "creatures
don't suffer summoning sickness" is this, because it suspends CR 302.6 rather
than granting anything.

Writing a card against it
-------------------------

A parser emits a ``SUSPEND_RULE`` effect naming the rule and who it applies
to, and nothing else. There is no per-card code::

    Effect(
        EffectKind.SUSPEND_RULE,
        rule=Rule.SUMMONING_SICKNESS,
        targets=ObjectFilter(types_all=CardType.CREATURE, controller=YOU),
        text="creatures you control have no summoning sickness",
    )

From a static ability on a permanent it applies while that permanent is on the
battlefield and stops when it leaves, like any other continuous effect. From a
resolving spell it takes a duration (CR 611.2).

``targets`` says which objects the suspension covers and ``players`` which
players; leave both unset for "everyone and everything". A rule about a player
(the maximum hand size) reads ``players``; a rule about an object (summoning
sickness) reads ``targets``. Passing neither means the rule is simply off.

Adding a rule to this list
--------------------------

The list is deliberately closed. A rule the engine has no name for is one it
cannot suspend, and the failure mode of an open registry is silence - a card
that claims to switch off a rule nothing ever consults. Adding one is two
steps and no more:

1. Add a member here, named for what it switches off and carrying its CR
   number, with the call site in its comment.
2. At that call site, ask ``game.rule_is_suspended(...)`` before enforcing.

If a rule is missing, that is a gap to fill, not a reason to special-case a
card.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..kernel.ids import NO_OBJECT, NO_PLAYER, ObjectId, PlayerId
from ..kernel.query import ALWAYS, Condition, ObjectFilter, PlayerFilter


class Rule(StrEnum):
    """A rule a card may switch off, named by its CR number.

    The value *is* the rule number, so a log can print it and the citation
    checker can see it. Every member names where it is enforced, because a
    member with no call site is a promise the engine does not keep.
    """

    #: CR 302.6. Enforced in ``cr506_combat.can_attack`` and in the three
    #: places ``cr601_casting`` refuses to activate a {T} ability.
    SUMMONING_SICKNESS = "302.6"
    #: CR 704.5j, the legend rule. Enforced in ``cr704_sba._check_legend_rule``.
    LEGEND_RULE = "704.5j"
    #: CR 305.2, one land per turn. Enforced in ``legality._land_plays``.
    #: Note this is the *limit*, not the permission: a card that raises the
    #: limit to two sets ``Player.max_lands`` instead.
    ONE_LAND_PER_TURN = "305.2"
    #: CR 508.1f: attacking taps the creature. Vigilance (CR 702.21) is the
    #: per-creature version and stays a keyword; this is the blanket form.
    #: Enforced in ``cr506_combat.declare_attackers``.
    ATTACKING_TAPS = "508.1f"


@dataclass(frozen=True, slots=True)
class RuleOverride:
    """One rule switched off, and for whom.

    Shaped like ``Restriction`` on purpose: the two are the same idea from
    opposite directions - that one adds a "can't" the rules do not have, this
    one removes one they do - and a reader who knows the first should not have
    to learn a second vocabulary.
    """

    rule: Rule
    #: Which objects the suspension covers. ``None`` means every object.
    subject: ObjectFilter | None = None
    #: Which players it covers. ``None`` means every player.
    players: PlayerFilter | None = None
    source: ObjectId = NO_OBJECT
    controller: PlayerId = NO_PLAYER
    #: For the "unless" shape, the same way a prohibition is gated.
    condition: Condition = ALWAYS
    text: str = ""
    #: ``kernel.enums.Duration``, for a suspension registered by a resolved
    #: spell rather than regenerated from a static ability. An override
    #: rebuilt from a permanent ends when the permanent does and needs none.
    duration: int = 0
    #: The turn it started on; see ``ContinuousEffect``.
    created_turn: int = 0

    def __str__(self) -> str:
        return self.text or f"CR {self.rule.value} does not apply"


#: What ``Rule`` members mean, for a log or a UI that has to explain one.
DESCRIPTIONS: dict[Rule, str] = {
    Rule.SUMMONING_SICKNESS: (
        "creatures can attack and use {T} abilities the turn they arrive "
        "(CR 302.6)"
    ),
    Rule.LEGEND_RULE: (
        "a player may control two or more legendary permanents with the same "
        "name (CR 704.5j)"
    ),
    Rule.ONE_LAND_PER_TURN: (
        "lands may be played without the one-per-turn limit (CR 305.2)"
    ),
    Rule.ATTACKING_TAPS: "attacking does not cause a creature to tap (CR 508.1f)",
}


# ---------------------------------------------------------------------------
# Asking, and registering
# ---------------------------------------------------------------------------


def suspended(
    game,
    rule: Rule,
    *,
    obj=None,
    player: PlayerId = NO_PLAYER,
) -> RuleOverride | None:
    """Whether ``rule`` is switched off right now, and by what.

    Returns the override rather than a bool for the same reason ``prohibited``
    does: when a simulated game does something surprising, the useful question
    is not whether a rule applied but which card said it should not.

    ``obj`` is the object the rule is about, ``player`` the player. An
    override that names neither is unconditional.
    """
    from ..kernel.conditions import holds
    from ..kernel.matching import matches, resolve_players

    for override in active_overrides(game):
        if override.rule is not rule:
            continue

        if override.subject is not None and (
            obj is None
            or not matches(
                game,
                obj,
                override.subject,
                source=override.source,
                controller=override.controller,
            )
        ):
            continue

        if override.players is not None:
            who = player
            if who == NO_PLAYER and obj is not None:
                who = obj.controller
            if who == NO_PLAYER:
                continue
            if who not in resolve_players(
                game, override.players, controller=override.controller
            ):
                continue

        if not override.condition.is_always and not holds(
            game,
            override.condition,
            source=override.source,
            controller=override.controller,
        ):
            continue

        return override
    return None


def active_overrides(game) -> list[RuleOverride]:
    """Every suspension in force, rebuilt from live static abilities.

    Regenerated rather than persisted, for the same reason prohibitions and
    continuous effects are (CR 611.3): a rule switched off by a permanent
    that has left the battlefield is a rule that applies again.

    The standing list holds only what a resolved spell registered, which
    outlives its source and ends with its duration instead.
    """
    from ..cr600_spells_and_abilities.abilities import AbilityKind
    from ..cr600_spells_and_abilities.effects import EffectKind

    out: list[RuleOverride] = list(getattr(game, "rule_overrides", ()))
    # CR 604.3: a static ability can function outside the battlefield, so the
    # stack is in scope too - the same scan prohibitions use.
    for object_id in list(game.battlefield) + list(game.stack):
        obj = game.objects.get(object_id)
        if obj is None or obj.phased_out:
            continue
        for ability in game.characteristics(obj).abilities:
            if ability.kind is not AbilityKind.STATIC or ability.unparsed:
                continue
            if not ability.functions_in_zone(obj.zone):
                continue
            for effect in ability.effects:
                if effect.kind is not EffectKind.SUSPEND_RULE or not effect.rule:
                    continue
                try:
                    rule = Rule(effect.rule)
                except ValueError:
                    continue
                out.append(
                    RuleOverride(
                        rule=rule,
                        subject=effect.targets,
                        players=effect.players,
                        source=obj.id,
                        controller=obj.controller,
                        condition=ability.static_condition,
                        text=effect.text,
                    )
                )
    return out


def register(game, override: RuleOverride) -> RuleOverride:
    """Add a suspension that is not rebuilt from a permanent's static ability.

    Used by a resolved spell with a duration. One that comes from a static
    ability is regenerated with the rest of the continuous effects and must
    not be registered here, or it would outlive its source.
    """
    from dataclasses import replace

    if not override.created_turn:
        override = replace(override, created_turn=getattr(game, "turn", 0))
    if not hasattr(game, "rule_overrides"):
        game.rule_overrides = []
    game.rule_overrides.append(override)
    return override
