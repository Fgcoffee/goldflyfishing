"""What a player may legally do right now (CR 117.1, 116, 307, 602.5).

The AI asks this and only this. It never decides legality for itself, because a
second opinion about the rules is a second set of rules, and the two would
drift apart the moment either changed.

Timing is the bulk of it. Sorcery speed (CR 307.1) means: your turn, a main
phase, an empty stack, and you have priority. Everything else is instant speed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..cr100_game_concepts.cr117_priority import PASS, Action, ActionKind
from ..cr600_spells_and_abilities.abilities import AbilityKind
from .enums import CardType, Phase, Timing, Zone
from .gameobject import GameObject
from .ids import NO_PLAYER, ObjectId, PlayerId

if TYPE_CHECKING:
    from .game import Game


def has_sorcery_speed(game: Game, player_id: PlayerId) -> bool:
    """CR 307.1: your main phase, stack empty, and you have priority."""
    return (
        game.active_player == player_id
        and game.phase in (Phase.PRECOMBAT_MAIN, Phase.POSTCOMBAT_MAIN)
        and not game.stack
    )


def legal_actions(game: Game, player_id: PlayerId) -> list[Action]:
    """Every action this player could take, plus passing.

    Ordered deterministically: replay depends on the same legal list being
    produced in the same order given the same state.
    """
    out: list[Action] = [PASS]
    sorcery_speed = has_sorcery_speed(game, player_id)

    out.extend(_land_plays(game, player_id, sorcery_speed))
    out.extend(_castable(game, player_id, sorcery_speed))
    out.extend(_activatable(game, player_id, sorcery_speed))
    # CR 116: special actions need no stack and no timing permission beyond
    # priority, so they are offered in every window a player gets one.
    from ..cr100_game_concepts.cr116_special_actions import available as special_available

    out.extend(action.as_action() for action in special_available(game, player_id))
    return out


# ---------------------------------------------------------------------------
# Lands (CR 305.1, 116.2a)
# ---------------------------------------------------------------------------


def _land_plays(game: Game, player_id: PlayerId, sorcery_speed: bool) -> list[Action]:
    from ..cr100_game_concepts.cr101_rule_overrides import Rule

    player = game.player(player_id)
    if not sorcery_speed:
        return []
    # CR 305.2 and CR 505.6b: one land per turn, unless a card has switched
    # the limit off (CR 101.1). A card that merely *raises* it sets
    # ``Player.max_lands`` instead - this is for the ones that remove it.
    if player.lands_played >= player.max_lands and (
        game.rule_is_suspended(Rule.ONE_LAND_PER_TURN, player=player_id) is None
    ):
        return []

    from ..cr500_turn_structure.restrictions import Act, prohibited

    if prohibited(game, Act.PLAY_LAND, player=player_id) is not None:
        return []

    from ..cr700_additional_rules.cr707_faces import playable_land_face_indices

    out: list[Action] = []
    for object_id in player.hand:
        obj = game.objects[object_id]
        # CR 712.12: a modal double-faced card with a land on the back is a
        # land drop for that face, and it enters with that face up.
        for face_index in playable_land_face_indices(game, obj):
            out.append(
                Action(ActionKind.PLAY_LAND, source=object_id, face_index=face_index)
            )
    return out


# ---------------------------------------------------------------------------
# Casting (CR 601.3)
# ---------------------------------------------------------------------------


def _face_characteristics(game: Game, obj: GameObject, face_index: int):
    """Printed characteristics of one particular face.

    Casting a split card's second half means the spell *is* that half, so
    legality has to be judged on that face's cost and type rather than on the
    card's front face.
    """
    from ..cr200_parts_of_a_card.characteristics import from_face
    from ..cr600_spells_and_abilities.cr613_layers import intrinsic_abilities

    # CR 718.3a: a prototyped spell is judged on its prototype face, which
    # the card data does not print as a face of its own.
    from ..cr300_card_types.cr300_card_types import card_face

    face, ability_face = card_face(obj.card, face_index)
    if face is None:
        return game.printed_characteristics(obj)
    abilities = game.ability_provider.abilities_for(obj.card, ability_face)
    return from_face(face, abilities + intrinsic_abilities(face.type_line))


def _castable(game: Game, player_id: PlayerId, sorcery_speed: bool) -> list[Action]:
    from ..cr500_turn_structure.restrictions import Act, permitted, prohibited
    from ..cr700_additional_rules.cr707_faces import castable_face_indices

    out: list[Action] = []
    player = game.player(player_id)

    sources: list[tuple[ObjectId, Zone]] = [(oid, Zone.HAND) for oid in player.hand]
    # CR 903.8: a player may cast a commander *they own* from the command zone.
    # That permission belongs to commanders alone. The command zone also holds
    # emblems (CR 114.2) and whatever else an effect puts there, and none of it
    # may be cast - offering it would put a card on the stack that no rule ever
    # let a player cast.
    sources += [
        (oid, Zone.COMMAND)
        for oid in game.command
        if game.objects[oid].owner == player_id and game.objects[oid].is_commander
    ]
    # An alternative cost can allow casting from somewhere else entirely -
    # Flashback from a graveyard being the obvious one - so those zones are
    # candidates too, filtered per alternative below.
    sources += [(oid, Zone.GRAVEYARD) for oid in player.graveyard]
    sources += [
        (oid, Zone.EXILE) for oid in game.exile if game.objects[oid].owner == player_id
    ]

    for object_id, zone in sources:
        obj = game.objects.get(object_id)
        if obj is None:
            continue
        # CR 601.3: a player may begin to cast a spell only if no rule or
        # effect prohibits it.
        # CR 601.4: some cards may only be cast by a particular player -
        # "only you may cast this", "an opponent may cast this". The check is
        # the same prohibition mechanism, asked of this player specifically.
        if prohibited(game, Act.CAST_SPELL, obj=obj, player=player_id) is not None:
            continue

        # CR 709.4 / 712.2: a split card or modal double-faced card offers a
        # real choice of face, and each face has its own cost, type and timing.
        for face_index in castable_face_indices(game, obj):
            # CR 715.3d: the permission covers one face. A card exiled by its
            # own Adventure may be cast as the creature and not as the
            # Adventure again, which would otherwise loop for ever.
            if (
                obj.playable_from_here_by != NO_PLAYER
                and face_index != obj.playable_face
            ):
                continue
            chars = _face_characteristics(game, obj, face_index)
            if chars.is_land:
                continue  # Lands are played, not cast.

            timing = _spell_timing(chars)
            if timing is Timing.SORCERY and not sorcery_speed:
                # CR 113.6: an effect may grant the timing the card lacks.
                # Vedalken Orrery, Leyline of Anticipation, and every "as
                # though it had flash" grant live here. Without this the
                # rules stay maximally restrictive and those cards do
                # nothing at all.
                if (
                    permitted(
                        game, Act.CAST_AS_THOUGH_FLASH, obj=obj, player=player_id
                    )
                    is None
                ):
                    continue
            if not _targets_available(game, obj, player_id):
                continue

            # CR 118.6: no mana cost means an unpayable cost, not a free spell.
            #
            # The zones are the ones a card may be cast from for its ordinary
            # cost: the hand, the command zone (CR 903.8), and wherever a
            # standing permission has put it (CR 715.3d). Casting from
            # anywhere else needs an alternative cost, which is the branch
            # below.
            may_cast_from_here = zone in (Zone.HAND, Zone.COMMAND) or (
                obj.playable_from_here_by == player_id
            )
            if may_cast_from_here and chars.has_mana_cost:
                if _affordable(game, player_id, obj, zone, chars):
                    out.append(
                        Action(
                            ActionKind.CAST_SPELL,
                            source=object_id,
                            face_index=face_index,
                        )
                    )

        chars = game.printed_characteristics(obj)
        if chars.is_land:
            continue

        # CR 118.9: each alternative cost is a separate option, and it is what
        # makes a card in a graveyard castable at all.
        for index, alternative in enumerate(chars.alternative_costs):
            if alternative.from_zone is not None and alternative.from_zone is not zone:
                continue
            if alternative.from_zone is None and zone not in (Zone.HAND, Zone.COMMAND):
                continue
            if not _alternative_available_now(
                game, player_id, alternative, obj, chars, sorcery_speed
            ):
                continue
            if not _affordable_alternative(game, player_id, alternative, obj, zone):
                continue
            out.append(
                Action(ActionKind.CAST_SPELL, source=object_id, alternative_cost=index)
            )

    return out


def _alternative_available_now(
    game: Game,
    player_id: PlayerId,
    alternative,
    obj: GameObject,
    chars,
    sorcery_speed: bool,
) -> bool:
    """Whether this alternative cost may be chosen at this moment.

    CR 601.2b chooses the alternative cost while proposing the spell, and the
    spell's timing (CR 307.1) still applies to a spell cast that way:
    flashback on a sorcery is sorcery-speed. An alternative cost may carry its
    own window (CR 702.190a sneak, "during your declare blockers step") and
    may lift the spell's timing to instant speed within it.
    """
    from ..cr500_turn_structure.restrictions import Act, permitted
    from .conditions import holds

    if alternative.condition is not None and not holds(
        game, alternative.condition, source=obj.id, controller=player_id
    ):
        return False
    if sorcery_speed or alternative.instant_speed:
        return True
    from ..cr600_spells_and_abilities.cr601_casting import FACE_DOWN_CAST_KEYWORDS

    if alternative.keyword in FACE_DOWN_CAST_KEYWORDS:
        # CR 708.4: cast face down, it is judged as the 2/2 it will be - which
        # has no flash, whatever the card underneath has.
        from ..cr700_additional_rules.cr708_face_down import face_down_characteristics

        chars = face_down_characteristics(obj)
    if _spell_timing(chars) is not Timing.SORCERY:
        return True
    # CR 113.6: a granted "as though it had flash" covers this cast too.
    return permitted(game, Act.CAST_AS_THOUGH_FLASH, obj=obj, player=player_id) is not None


def _affordable_alternative(
    game: Game, player_id: PlayerId, alternative, obj: GameObject, zone: Zone
) -> bool:
    """Whether an alternative cost could plausibly be paid.

    The mana is an upper bound, like ``_affordable``: over-reporting costs a
    rewound cast, under-reporting hides a legal play. Everything else is asked
    of the dry run payment itself uses. Only the mana was asked about, so an
    escape was offered with nothing in the graveyard to exile.
    """
    from ..cr100_game_concepts.cr106_mana import find_payment
    from ..cr600_spells_and_abilities.cr601_casting import CastError, _check_payable

    if alternative.cost.is_unparsed:
        return False
    for component in alternative.cost.non_mana_components:
        try:
            # The card is still where it is cast from. It will be on the
            # stack when this is paid, so it cannot pay for itself.
            _check_payable(game, player_id, component, obj, spell=obj)
        except CastError:
            return False

    player = game.player(player_id)
    cost = alternative.cost.mana_component
    if zone is Zone.COMMAND and obj.is_commander:
        # CR 903.8 with CR 118.9d: the tax is a cost *increase*, and an
        # alternative cost replaces the mana cost rather than the increases
        # applied on top of it. ``compute_total_cost`` charges it either way,
        # so leaving it out here offers a commander for one mana and then
        # refuses the payment.
        cost = cost.increased_by(
            player.commander_tax(game.commander_identity(obj.id))
        )
    if find_payment(player.mana_pool, cost, life_available=player.life - 1):
        return True

    available = player.mana_pool.total
    for permanent in game.permanents(player_id):
        if permanent.tapped:
            continue
        chars = game.characteristics(permanent)
        if chars.is_creature and permanent.summoning_sick:
            continue
        if any(a.is_mana_ability and not a.unparsed for a in chars.abilities):
            available += 1
    return available >= cost.mana_value


def _spell_timing(chars) -> Timing:
    """CR 307.1: only instants, and things with flash, can be cast any time.

    Granted timing is *not* handled here, because this reads the card's own
    characteristics and a grant is a property of the game state. The caller
    asks ``permitted`` separately.
    """
    if chars.has_type(CardType.INSTANT):
        return Timing.INSTANT
    if chars.has_keyword("Flash"):
        return Timing.INSTANT
    return Timing.SORCERY


def can_afford(game: Game, player_id: PlayerId, cost) -> bool:
    """Whether a player could plausibly pay a bare mana cost.

    The same upper bound as ``_affordable``, minus the spell - special actions
    (CR 116) pay a flat cost with no cost-modification effects applying to
    them, so there is nothing to increase or reduce.
    """
    from ..cr100_game_concepts.cr106_mana import find_payment

    player = game.player(player_id)
    if cost is None or cost.mana_value == 0:
        return True
    if find_payment(player.mana_pool, cost, life_available=player.life - 1):
        return True

    sources = 0
    for permanent in game.permanents(player_id):
        if permanent.tapped:
            continue
        chars = game.characteristics(permanent)
        if chars.is_creature and permanent.summoning_sick:
            continue
        if any(a.is_mana_ability and not a.unparsed for a in chars.abilities):
            sources += 1
    if sources == 0:
        return False
    # No spell here to test a restriction against - a special action pays a
    # flat cost - so the whole pool counts.
    return player.mana_pool.total + sources >= cost.mana_value


def _affordable(
    game: Game, player_id: PlayerId, obj: GameObject, zone: Zone, chars=None
) -> bool:
    """Whether the player could plausibly pay for this spell.

    An upper bound on purpose: it counts what is available rather than solving
    the exact tap plan. Saying yes when the colors do not work out costs a
    rewound cast; saying no when they do would hide a legal play entirely.
    """
    from ..cr100_game_concepts.cr106_mana import find_payment
    from ..cr600_spells_and_abilities.cr601_casting import cost_increases, cost_reductions

    player = game.player(player_id)
    chars = chars if chars is not None else game.characteristics(obj)
    cost = chars.mana_cost

    increase = sum(cost_increases(game, obj, player_id))
    if zone is Zone.COMMAND and obj.is_commander:
        increase += player.commander_tax(game.commander_identity(obj.id))
    reduction = sum(cost_reductions(game, obj, player_id))
    cost = cost.increased_by(increase).reduced_by(reduction)

    # ``context`` is the spell itself, which is what "spend this mana only to
    # cast creature spells" is a question about. Without it the restricted
    # mana counts toward everything, and the spell is offered as legal only to
    # be abandoned mid-cast - a bot then wastes the turn on a play the card
    # never allowed.
    if find_payment(
        player.mana_pool, cost, life_available=player.life - 1, context=obj
    ):
        return True

    # CR 702.51a and friends: convoke, improvise and delve pay part of a cost
    # with something other than mana, so a spell that is unaffordable in mana
    # alone may still be perfectly castable.
    from ..cr600_spells_and_abilities.cr601_casting import helper_capacity

    helpers = helper_capacity(game, obj, player_id)
    if helpers and player.mana_pool.usable_for(obj) + helpers >= cost.mana_value:
        return True

    # The count-based fallback is an upper bound used when untapped sources
    # could still produce something. With no sources at all the pool check
    # above was exact, so trusting the count here would offer a spell whose
    # colours plainly cannot be paid.
    sources = 0
    for permanent in game.permanents(player_id):
        if permanent.tapped:
            continue
        permanent_chars = game.characteristics(permanent)
        if permanent_chars.is_creature and permanent.summoning_sick:
            continue
        if any(a.is_mana_ability and not a.unparsed for a in permanent_chars.abilities):
            sources += 1
    if sources == 0:
        return False
    # Only mana that could legally pay for *this* spell counts toward the
    # bound; restricted mana would otherwise make every spell look affordable.
    return player.mana_pool.usable_for(obj) + sources >= cost.mana_value


def _targets_available(game: Game, obj: GameObject, player_id: PlayerId) -> bool:
    """CR 601.2c: a spell needing a target cannot be cast without a legal one.

    A modal spell is the awkward case, because which targets it needs depends
    on a mode nobody has chosen yet - the card is still in hand. CR 700.2a
    settles it: a mode whose targets cannot be supplied cannot be chosen, so
    the spell is castable while *any* mode is. Requiring every mode's targets
    made a charm uncastable whenever a single one of its options had nothing
    to point at.
    """
    from ..cr600_spells_and_abilities.cr601_casting import (
        aura_target_effect,
        legal_modes,
        modal_effect,
        spell_effects,
        targeted_nodes,
        targeting_effects,
    )
    from .matching import find

    modal = modal_effect(spell_effects(game, obj))
    if modal is None:
        # The engine's own list, rather than a third walk of the same effects.
        # An Aura's target comes from its enchant ability instead of a
        # targeting effect (CR 303.4a), so a walk that only looked at effects
        # offered an Aura as castable with no creature to enchant.
        nodes = targeting_effects(game, obj)
    else:
        if not legal_modes(game, obj, modal, player_id):
            # CR 700.2b: no mode can be chosen, so there is nothing to cast.
            return False
        # The modes have been judged above, on their own terms. What is left
        # to check is the targets outside the modal instruction, which an
        # empty ``chosen_modes`` asks for.
        nodes = targeted_nodes(spell_effects(game, obj), ())
        enchant = aura_target_effect(game.characteristics(obj))
        if enchant is not None:
            nodes.insert(0, enchant)

    for node in nodes:
        if node.targets is None:
            continue
        # "Up to N" can legally be cast with none, so an empty board
        # is no obstacle.
        if node.targets.up_to:
            continue
        # CR 115.4: a spell that can target a player always has one,
        # so an empty board never makes it uncastable. Without this,
        # burn is uncastable on turn one and can never win a game.
        if node.targets.includes_players:
            continue
        if not find(game, node.targets, source=obj.id, controller=player_id):
            return False
    return True


# ---------------------------------------------------------------------------
# Activated abilities (CR 602.5)
# ---------------------------------------------------------------------------


def _activation_sources(game: Game, player_id: PlayerId) -> list[GameObject]:
    """Objects whose activated abilities this player might activate.

    The permanents they control, the cards in their hand, the cards they own
    in the command zone, and the cards in their graveyard. Cycling, Channel
    and Forecast are activated from hand, Unearth, Scavenge and "Return this
    card from your graveyard to your hand" from a graveyard (CR 113.6), and
    commander ninjutsu from the command zone (CR 702.49d); looking at
    permanents alone never offered any of them. Only this player's own hand
    and graveyard are searched: a card there has no controller, and its owner
    is who activates it (CR 602.2, 108.4a).

    Every ability is still asked whether it functions where its object is, so
    a played cycling land is a source whose Cycling is not offered, and a
    creature card in a graveyard offers none of its battlefield abilities.

    Exile is not searched: no activated ability the engine builds functions
    there.
    """
    permanents = sorted(game.permanents(player_id), key=lambda o: o.id)
    hand = sorted(
        (game.objects[object_id] for object_id in game.player(player_id).hand),
        key=lambda o: o.id,
    )
    command = sorted(
        (
            game.objects[object_id]
            for object_id in game.command
            if game.objects[object_id].owner == player_id
        ),
        key=lambda o: o.id,
    )
    graveyard = sorted(
        (game.objects[object_id] for object_id in game.player(player_id).graveyard),
        key=lambda o: o.id,
    )
    return permanents + hand + command + graveyard


def _activatable(game: Game, player_id: PlayerId, sorcery_speed: bool) -> list[Action]:
    from ..cr500_turn_structure.restrictions import Act, prohibited
    from ..cr600_spells_and_abilities.cr601_casting import (
        _can_pay_activation,
        ability_targets_available,
        activation_limit_reached,
        activation_refusal,
    )

    out: list[Action] = []
    for obj in _activation_sources(game, player_id):
        chars = game.characteristics(obj)
        for index, ability in enumerate(chars.abilities):
            if ability.kind is not AbilityKind.ACTIVATED or ability.unparsed:
                continue
            if activation_refusal(game, player_id, obj, ability) is not None:
                continue
            if ability.timing is Timing.SORCERY and not sorcery_speed:
                continue
            # CR 209.2 states the rule and CR 606.3 repeats it: a loyalty
            # ability is sorcery-speed, and only one of a permanent's loyalty
            # abilities may be activated per turn.
            if ability.is_loyalty_ability and not sorcery_speed:
                continue
            if activation_limit_reached(obj, ability, index):
                continue
            if not _can_pay_activation(game, obj, ability):
                continue
            if ability.is_targeted and not ability_targets_available(game, obj, ability, player_id):
                continue
            if ability.is_loyalty_ability:
                act = Act.ACTIVATE_LOYALTY_ABILITY
            elif ability.is_mana_ability:
                # CR 605: a mana ability is still an activated ability, but
                # every effect that stops "activated abilities" while carving
                # out mana abilities - split second above all - needs the two
                # to be distinguishable acts.
                act = Act.ACTIVATE_MANA_ABILITY
            else:
                act = Act.ACTIVATE_ABILITY
            if prohibited(game, act, obj=obj, player=player_id) is not None:
                continue

            kind = (
                ActionKind.ACTIVATE_MANA_ABILITY
                if ability.is_mana_ability
                else ActionKind.ACTIVATE_ABILITY
            )
            out.append(Action(kind, source=obj.id, ability_index=index))
    return out
