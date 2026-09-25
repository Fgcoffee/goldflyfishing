"""State-based actions (CR 704).

The game's housekeeping. Whenever a player would receive priority, the game
checks every state-based action, performs all that apply *simultaneously*, and
then checks again - repeating until none apply (CR 704.3). Only then does the
player actually get priority.

Two properties matter and are easy to get wrong:

**Simultaneity.** Two creatures that would each kill the other both die. If
they were processed one at a time, the first to die might stop being lethal to
the second. So the whole set is decided before any of it is carried out.

**They are not triggers.** State-based actions do not use the stack and cannot
be responded to (CR 704.2). A creature with lethal damage dies before anyone
gets a chance to save it - the window was earlier.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..cr100_game_concepts.actions import (
    destroy,
    detach,
    is_indestructible,
    put_into_graveyard,
    sacrifice,
)
from ..kernel.enums import CardType, LossReason, Supertype, Zone
from ..kernel.events import Event, EventKind
from ..kernel.gameobject import GameObject, ObjectKind
from ..kernel.ids import NO_OBJECT, NO_PLAYER, PlayerId
from .cr722_preparation import keeps_its_prepare_copy

if TYPE_CHECKING:
    from ..kernel.game import Game

#: Guard against an effect loop that keeps generating work forever.
MAX_ITERATIONS = 100


def check_state_based_actions(game: Game) -> bool:
    """Run state-based actions to a fixed point (CR 704.3).

    Returns whether anything was done, which the priority loop uses to decide
    whether to re-check triggers before handing out priority.
    """
    from ..cr500_turn_structure.cr506_combat import check_removal_from_combat

    did_anything = False
    for _ in range(MAX_ITERATIONS):
        # CR 506.4 is not a state-based action - a permanent leaves combat the
        # instant it dies, changes controller, phases out or stops being a
        # creature, not when anyone next checks. But this is the moment the
        # game next looks at the board, and it runs before any player gets
        # priority, so nothing can observe the combat record in between.
        check_removal_from_combat(game)
        if not _one_pass(game):
            break
        did_anything = True
        if game.game_over:
            break
    return did_anything


def _one_pass(game: Game) -> bool:
    """One simultaneous round of state-based actions.

    Everything is *decided* before anything is *done*, because CR 704.3 makes
    them simultaneous.
    """
    losers: list[tuple[PlayerId, LossReason]] = []
    to_graveyard: list[GameObject] = []
    to_destroy: list[GameObject] = []
    to_cease: list[GameObject] = []
    to_unattach: list[GameObject] = []
    counter_annihilation: list[GameObject] = []
    legend_choices: list[tuple[PlayerId, str, list[GameObject]]] = []
    to_sacrifice: list[GameObject] = []
    speed_starts: list[PlayerId] = []
    finished_dungeons: list[GameObject] = []

    commander_moves = _check_commander_zone_choice(game)

    _grant_enduring_stories(game)
    _check_players(game, losers)
    _check_permanents(
        game,
        to_graveyard,
        to_destroy,
        to_unattach,
        counter_annihilation,
        to_sacrifice,
    )
    _check_ceased(game, to_cease)
    _check_speed(game, speed_starts)
    _check_legend_rule(game, legend_choices)
    _check_world_rule(game, to_graveyard)
    _check_role_rule(game, to_graveyard)
    _check_dungeons(game, finished_dungeons)

    if not any(
        (
            losers,
            to_graveyard,
            to_destroy,
            to_cease,
            to_unattach,
            counter_annihilation,
            legend_choices,
            to_sacrifice,
            speed_starts,
            commander_moves,
            finished_dungeons,
        )
    ):
        return False

    for player_id in speed_starts:
        if game.player(player_id).start_engines():
            game.log.record(
                game, f"{game.player(player_id).name} starts their engines", kind="sba"
            )

    for obj in commander_moves:
        _move_commander_home(game, obj)

    # -- perform, all at once ------------------------------------------------
    # CR 704.8: a permanent leaving the battlefield in this same round of
    # state-based actions has its last known information read from the state
    # *before* any of them happened. Annihilating its counters first rewrote
    # the information every later reader gets: a Young Wolf with one +1/+1 and
    # three -1/-1 counters cancelled a pair, reached the graveyard carrying no
    # +1/+1 counter, and came back under undying - which the rule's own
    # example exists to forbid. Its counters are left alone; they cannot
    # matter to anything except as the record of what it was.
    leaving = {obj.id for obj in to_graveyard}
    leaving.update(obj.id for obj in to_destroy)
    leaving.update(obj.id for obj in to_sacrifice)
    leaving.update(obj.id for obj in to_cease)

    for obj in counter_annihilation:
        if obj.id in leaving:
            continue
        # CR 704.5q: +1/+1 and -1/-1 counters cancel in pairs.
        pairs = min(obj.counters.get("+1/+1", 0), obj.counters.get("-1/-1", 0))
        if pairs:
            obj.remove_counters("+1/+1", pairs)
            obj.remove_counters("-1/-1", pairs)
            game.invalidate_characteristics()

    for obj in to_unattach:
        detach(game, obj)

    for obj in to_cease:
        game.log.record(game, f"{obj} ceases to exist", kind="sba")
        game._remove_from_zone(obj)
        if obj.kind is ObjectKind.TOKEN and any(
            pending.source == obj.id for pending in game.pending_triggers
        ):
            # Its own death trigger is still waiting to go on the stack, and
            # needs the token's controller and last-known characteristics
            # (CR 603.3a, 603.10a). Popped, the trigger went on the stack with
            # no controller and did nothing. Kept exactly like a card's
            # pre-move object instead: not live, and reading the zone it left -
            # which is also what stops this check finding it again next pass.
            obj.superseded_by = obj.id
            obj.zone = Zone.BATTLEFIELD
        else:
            game.objects.pop(obj.id, None)
        game.invalidate_characteristics()

    for obj in to_graveyard:
        if obj.zone is Zone.BATTLEFIELD:
            put_into_graveyard(game, obj)

    for obj in to_destroy:
        destroy(game, obj)

    for obj in to_sacrifice:
        sacrifice(game, obj)

    for controller, name, group in legend_choices:
        _resolve_legend_rule(game, controller, name, group)

    if finished_dungeons:
        from ..cr300_card_types.cr309_dungeons import complete_dungeon

        for obj in finished_dungeons:
            complete_dungeon(game, obj)

    for player_id, reason in losers:
        game.player_loses(player_id, reason)

    return True


# ---------------------------------------------------------------------------
# Players (CR 704.5a-c, 903.10)
# ---------------------------------------------------------------------------


def _check_commander_zone_choice(game: Game) -> list[GameObject]:
    """CR 903.9a: a commander newly in a graveyard or exile may go home.

    A state-based action, not a replacement. The commander genuinely went to
    the graveyard - it died, and every dies-trigger on the board has already
    seen it - and only now does its owner get the option.

    The window is exactly one state-based action check. A commander that has
    already sat through a check stays where it is, so a card put into a
    graveyard by an earlier effect cannot be scooped up later.
    """
    pending = game.commanders_awaiting_zone_choice
    if not pending:
        return []
    game.commanders_awaiting_zone_choice = []

    from ..cr600_spells_and_abilities.cr614_replacement import wants_command_zone

    moves: list[GameObject] = []
    for object_id in pending:
        obj = game.objects.get(object_id)
        if obj is None or not obj.is_live:
            continue
        if obj.zone not in (Zone.GRAVEYARD, Zone.EXILE):
            continue  # Something moved it on before the check.
        if wants_command_zone(game, obj, obj.zone):
            moves.append(obj)
    return moves


def _move_commander_home(game: Game, obj: GameObject) -> None:
    game.log.record(
        game,
        f"{obj} moves from {obj.zone.name.lower()} to the command zone (CR 903.9a)",
        kind="commander",
        player=obj.owner,
    )
    moved = game.move_object(obj, Zone.COMMAND, to_player=obj.owner)
    game.emit(
        Event(
            EventKind.COMMANDER_MOVED_TO_COMMAND_ZONE,
            object_id=moved.id,
            player=obj.owner,
            from_zone=obj.zone,
        )
    )


def _check_players(game: Game, losers: list[tuple[PlayerId, LossReason]]) -> None:
    # The sandbox switches these four off together (see rules.relaxations).
    # Returning before the loop, rather than letting each player be collected
    # and then spared, matters: a spared loser is still a *pending* loser, so
    # the fixed-point loop above would find work to do on every pass and spin
    # to its iteration cap at every priority check.
    if game.relaxations.players_cannot_lose:
        return

    for player in game.players:
        if player.has_lost:
            continue

        # CR 704.5a
        if player.life <= 0:
            losers.append((player.id, LossReason.LIFE))
            continue

        # CR 704.5b: the loss happens now, not at the moment of the draw.
        if player.attempted_draw_from_empty_library:
            losers.append((player.id, LossReason.EMPTY_LIBRARY))
            continue

        # CR 704.5c
        if player.poison >= 10:
            losers.append((player.id, LossReason.POISON))
            continue

        # CR 704.6c, which restates CR 903.10a: 21 or more combat damage
        # from a single commander. This is the state-based action itself.
        if player.lethal_commander_damage:
            losers.append((player.id, LossReason.COMMANDER_DAMAGE))


# ---------------------------------------------------------------------------
# Permanents (CR 704.5f-r)
# ---------------------------------------------------------------------------


def _check_permanents(
    game: Game,
    to_graveyard: list[GameObject],
    to_destroy: list[GameObject],
    to_unattach: list[GameObject],
    counter_annihilation: list[GameObject],
    to_sacrifice: list[GameObject],
) -> None:
    for obj in list(game.permanents()):
        chars = game.characteristics(obj)

        # CR 704.5q - checked before lethal damage, since removing counters can
        # change a creature's toughness.
        if obj.counters.get("+1/+1", 0) and obj.counters.get("-1/-1", 0):
            counter_annihilation.append(obj)

        if chars.is_creature:
            toughness = chars.toughness or 0
            # CR 704.5f: toughness 0 or less. This is not destruction, so
            # indestructible does not save it and regeneration cannot replace it.
            if toughness <= 0:
                to_graveyard.append(obj)
                continue
            # CR 704.5g and 704.5h: lethal damage, or any damage from a
            # deathtouch source. Both are destruction.
            if not is_indestructible(game, obj):
                if obj.damage >= toughness and obj.damage > 0:
                    to_destroy.append(obj)
                    continue
                if obj.dealt_deathtouch_damage and obj.damage > 0:
                    to_destroy.append(obj)
                    continue

        # CR 704.5i
        if chars.has_type(CardType.PLANESWALKER):
            if obj.counter_count("loyalty") <= 0:
                to_graveyard.append(obj)
                continue

        # CR 704.5w: a battle with defense 0 goes to its owner's graveyard.
        # CR 704.5v says the same for a Siege battle but spares one that is
        # still the source of a triggered ability on the stack - the same
        # shape of exception the Saga check below honours, and for the same
        # reason: the permanent has to stay around long enough for its own
        # last trigger to resolve.
        if chars.has_type(CardType.BATTLE):
            if obj.counter_count("defense") <= 0 and not (
                chars.has_subtype("Siege") and _is_source_on_the_stack(game, obj)
            ):
                to_graveyard.append(obj)
                continue
            # CR 704.5x and 704.5y: a battle with nobody protecting it, or
            # one whose protector is no longer eligible, gets a new protector
            # chosen by its controller - and goes to its owner's graveyard if
            # nobody can be. 704.5x waits while the battle is under attack,
            # because a protector chosen mid-combat would change who the
            # defending player is (CR 310.9d).
            if not _has_eligible_protector(game, obj) and not _is_being_attacked(
                game, obj
            ):
                if not game.choose_protector(obj):
                    to_graveyard.append(obj)
                    continue

        # CR 704.5m: an Aura attached to something illegal, or to nothing, is
        # put into its owner's graveyard.
        if chars.has_subtype("Aura"):
            if not _attachment_is_legal(game, obj, require_host=True):
                to_graveyard.append(obj)
                continue

        # CR 704.5n / 704.5p: everything else merely falls off.
        #
        # Equipment and Fortifications fall off a host they may no longer be
        # on (CR 704.5n). CR 704.5p is broader and was missing entirely: a
        # battle or a creature that is attached to anything, and any other
        # permanent that is not an Aura, an Equipment or a Fortification,
        # becomes unattached whatever it is attached to - there is no legality
        # question, because no rule ever let it be attached in the first
        # place. That happens when a permanent stops being an Aura while it is
        # on something, or an effect turns an Equipment into a creature.
        if chars.has_subtype("Equipment") or chars.has_subtype("Fortification"):
            if obj.attached_to and not _attachment_is_legal(game, obj, require_host=False):
                to_unattach.append(obj)
        elif obj.attached_to and not chars.has_subtype("Aura"):
            to_unattach.append(obj)

        # CR 704.5s: a Saga with no lore counters left to add and no chapter
        # ability on the stack is sacrificed.
        if chars.has_subtype("Saga") and _saga_is_finished(game, obj, chars):
            to_sacrifice.append(obj)


def _attachment_is_legal(game: Game, obj: GameObject, *, require_host: bool) -> bool:
    """Whether an attachment is on something it may legally be on.

    CR 303.4c for an Aura, CR 301.5 for Equipment, CR 301.6 for a
    Fortification. The restriction was already parsed and already on the
    object - the Enchant keyword carries its filter as ``quality`` - and this
    check ignored it, so nothing ever fell off: an Aura stayed attached to a
    creature that had stopped being a creature, and Equipment stayed on a
    permanent that was no longer one.

    An attachment whose restriction the parser could not read keeps the old
    behaviour, which is to stay put. Destroying it on a restriction the engine
    cannot see would be worse than leaving it.
    """
    if not obj.attached_to:
        return not require_host
    host = game.objects.get(obj.attached_to)
    if host is None or host.zone is not Zone.BATTLEFIELD:
        return False

    from ..kernel.matching import matches

    chars = game.characteristics(obj)
    for ability in chars.abilities:
        if ability.keyword.lower() not in ("enchant", "equip", "fortify"):
            continue
        if ability.quality is None:
            continue
        if not matches(
            game, host, ability.quality, source=obj.id, controller=obj.controller
        ):
            return False

    # CR 301.5: Equipment goes on a creature, whatever else its equip ability
    # says. Fortifications go on lands (CR 301.6). Both are part of the type,
    # not of the ability's own filter, so neither comes from ``quality``.
    host_chars = game.characteristics(host)
    if chars.has_subtype("Equipment") and not host_chars.is_creature:
        return False
    if chars.has_subtype("Fortification") and not host_chars.has_type(CardType.LAND):
        return False
    return True


def _has_eligible_protector(game: Game, obj: GameObject) -> bool:
    """CR 704.5x, 704.5y: whether this battle's protector still stands.

    Both halves at once, because they ask the same question from opposite
    sides - 704.5x is nobody designated, 704.5y is somebody who may no longer
    be it - and the answer to both is that the controller chooses again.
    """
    if obj.protector == NO_PLAYER:
        return False
    return obj.protector in game.eligible_protectors(obj)


def _is_being_attacked(game: Game, obj: GameObject) -> bool:
    """CR 704.5x only acts on a battle no creature is attacking."""
    combat = getattr(game, "combat", None)
    if combat is None:
        return False
    return obj.id in combat.attacking_permanent.values()


def _is_source_on_the_stack(game: Game, obj: GameObject) -> bool:
    """Whether an ability of this permanent has triggered and not yet left.

    CR 704.5v and CR 714.4 both spare a permanent whose own triggered ability
    "has triggered but has not yet left the stack".

    That covers one waiting to be *put* on the stack as well as one already
    there. A triggered ability goes on the stack only the next time a player
    would receive priority (CR 603.3), and state-based actions are checked
    first - so a Siege whose last defense counter has just come off would be
    in its owner's graveyard before its own CR 310.12b ability ever reached
    the stack, and the back face would never be seen.
    """
    for object_id in game.stack:
        stack_object = game.objects.get(object_id)
        if stack_object is not None and stack_object.source == obj.id:
            return True
    return any(
        getattr(pending, "source", NO_OBJECT) == obj.id
        for pending in getattr(game, "pending_triggers", ())
    )


def _check_dungeons(game: Game, finished: list[GameObject]) -> None:
    """CR 704.5t: a dungeon whose last room has been reached, and whose last
    room ability has left the stack, is removed and completed (CR 309.6)."""
    if not any(player.dungeon for player in game.players):
        return
    from ..cr300_card_types.cr309_dungeons import finished_dungeons

    finished.extend(finished_dungeons(game))


def _saga_is_finished(game: Game, obj: GameObject, chars) -> bool:
    """CR 714.4: final chapter reached, and no chapter ability waiting."""
    final = _final_chapter(chars)
    if final is None:
        return False
    if obj.counter_count("lore") < final:
        return False
    return not _is_source_on_the_stack(game, obj)


def _final_chapter(chars) -> int | None:
    chapters = [a.chapter for a in chars.abilities if getattr(a, "chapter", 0)]
    return max(chapters) if chapters else None


# ---------------------------------------------------------------------------
# Objects that cease to exist (CR 704.5d, 704.5e)
# ---------------------------------------------------------------------------


def _check_ceased(game: Game, to_cease: list[GameObject]) -> None:
    for obj in list(game.objects.values()):
        # CR 704.5d: a token that has left the battlefield.
        # CR 704.5e: a copy of a card outside the stack and the battlefield -
        # save a prepared permanent's copy in exile (CR 722.3c).
        if obj.kind is ObjectKind.TOKEN and obj.zone is not Zone.BATTLEFIELD:
            to_cease.append(obj)
        elif (
            obj.kind is ObjectKind.COPY
            and obj.zone not in (Zone.STACK, Zone.BATTLEFIELD)
            and not keeps_its_prepare_copy(game, obj)
        ):
            to_cease.append(obj)


# ---------------------------------------------------------------------------
# Legend and world rules (CR 704.5j, 704.5k)
# ---------------------------------------------------------------------------


def _check_speed(game: Game, speed_starts: list[PlayerId]) -> None:
    """CR 704.5aa: a player controlling a start your engines! permanent has speed.

    "If a player controls a permanent with start your engines! and that player
    has no speed, that player's speed becomes 1."

    A state-based action rather than a trigger, so it never uses the stack and
    applies however the permanent arrived.
    """
    for obj in game.permanents():
        if obj.controller in speed_starts:
            continue
        if game.player(obj.controller).speed:
            continue
        if game.characteristics(obj).has_keyword("Start your engines!"):
            speed_starts.append(obj.controller)


def _grant_enduring_stories(game: Game) -> None:
    """CR 702.195a: storied - "any time you control three or more permanents
    that are artifacts, Sagas, and/or legendary", you have an enduring story
    for the rest of the game.

    Not a state-based action, but checked "any time" in the same way and
    with the same effect: nothing uses the stack, and it applies however the
    permanents arrived. It is asked here, where the game already looks at
    the whole board whenever a player would receive priority. CR 702.195c
    has continuous effects reapplied before triggers are checked, which the
    characteristics invalidation below gives.
    """
    from ..kernel.enums import Supertype

    for player in game.players:
        if player.has_enduring_story:
            continue
        permanents = game.permanents(player.id)
        if not any(game.characteristics(o).has_keyword("Storied") for o in permanents):
            continue
        storied = 0
        for obj in permanents:
            chars = game.characteristics(obj)
            if (
                chars.has_type(CardType.ARTIFACT)
                or chars.has_supertype(Supertype.LEGENDARY)
                or "Saga" in chars.type_line.subtypes
            ):
                storied += 1
        if storied >= 3:
            player.has_enduring_story = True
            game.invalidate_characteristics()
            game.log.record(game, f"{player.name} has an enduring story", kind="designation")


def _check_legend_rule(
    game: Game, choices: list[tuple[PlayerId, str, list[GameObject]]]
) -> None:
    """CR 704.5j: one legendary permanent of each name per player.

    Per *player*, not per game - two opponents may each have their own Sol
    Ring... their own Karn. The keeper is that player's choice.
    """
    from ..cr100_game_concepts.cr101_rule_overrides import Rule

    grouped: dict[tuple[PlayerId, str], list[GameObject]] = {}
    for obj in game.permanents():
        chars = game.characteristics(obj)
        if not chars.has_supertype(Supertype.LEGENDARY):
            continue
        if not chars.name:
            continue
        # CR 101.1: a card may switch the legend rule off, for its controller
        # or for everyone.
        if game.rule_is_suspended(Rule.LEGEND_RULE, obj=obj) is not None:
            continue
        grouped.setdefault((obj.controller, chars.name), []).append(obj)

    for (controller, name), group in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if len(group) > 1:
            choices.append((controller, name, group))


def _resolve_legend_rule(
    game: Game, controller: PlayerId, name: str, group: list[GameObject]
) -> None:
    """Keep one, bin the rest.

    Which one to keep is the controller's choice. Until the AI makes it, the
    most recently gained one is kept - deterministically, so replays match.
    """
    survivors = sorted(group, key=lambda o: o.timestamp)
    keep = survivors[-1]
    for obj in survivors:
        if obj is not keep and obj.zone is Zone.BATTLEFIELD:
            game.log.record(game, f"Legend rule: {name} put into graveyard", kind="sba")
            put_into_graveyard(game, obj)


def _check_role_rule(game: Game, to_graveyard: list[GameObject]) -> None:
    """CR 704.5z: one Role per permanent per player, keeping the newest.

    Per *player*, like the legend rule and unlike the world rule - two
    opponents can each have a Role on the same creature, and both stay. This
    was missing entirely, and the engine creates Role tokens (CR 701.54), so
    a second Role from the same player stacked its grant on top of the first
    instead of replacing it.
    """
    for obj in game.permanents():
        if len(obj.attachments) < 2:
            continue
        by_controller: dict[PlayerId, list[GameObject]] = {}
        for attached_id in obj.attachments:
            role = game.objects.get(attached_id)
            if role is None or role.zone is not Zone.BATTLEFIELD:
                continue
            if not game.characteristics(role).has_subtype("Role"):
                continue
            by_controller.setdefault(role.controller, []).append(role)

        for group in by_controller.values():
            if len(group) < 2:
                continue
            ordered = sorted(group, key=lambda o: o.timestamp)
            for older in ordered[:-1]:
                if older not in to_graveyard:
                    game.log.record(
                        game, "Role rule: older Role put into graveyard", kind="sba"
                    )
                    to_graveyard.append(older)


def _check_world_rule(game: Game, to_graveyard: list[GameObject]) -> None:
    """CR 704.5k: only the most recent World permanent survives."""
    worlds = [
        obj
        for obj in game.permanents()
        if game.characteristics(obj).has_supertype(Supertype.WORLD)
    ]
    if len(worlds) <= 1:
        return
    newest = max(obj.timestamp for obj in worlds)
    for obj in worlds:
        if obj.timestamp != newest:
            to_graveyard.append(obj)
