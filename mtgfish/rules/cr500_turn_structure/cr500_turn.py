"""Turn structure and turn-based actions (CR 500-514).

A turn is five phases; some phases have steps. Turn-based actions happen
automatically at the start of certain steps, do not use the stack, and cannot
be responded to (CR 703.2) - untapping, drawing for turn, and declaring
attackers are all turn-based actions rather than things a player chooses to do
with priority.

Two steps grant no priority at all (CR 502.4, 514.3): untap, and cleanup. The
cleanup exception is real, though - if a state-based action happens or a
triggered ability is waiting, players *do* get priority and another cleanup
step follows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..cr100_game_concepts import actions
from ..cr100_game_concepts.cr117_priority import run_priority, settle
from ..kernel.enums import Phase, Step
from ..kernel.events import Event, EventKind
from ..kernel.ids import PlayerId

if TYPE_CHECKING:
    from ..kernel.game import Game


#: The phase each step belongs to, in order (CR 500.1). The first-strike damage
#: step is absent because it only exists when combat calls for it (CR 510.4).
#:
#: CR 501.1: the beginning phase is untap, upkeep, draw.
#: CR 505.1: the two main phases are separated by the combat phase.
#: CR 506.1: the combat phase has five steps.
#: CR 512.1: the ending phase is the end step and the cleanup step.
TURN_SEQUENCE: tuple[tuple[Phase, Step], ...] = (
    (Phase.BEGINNING, Step.UNTAP),
    (Phase.BEGINNING, Step.UPKEEP),
    (Phase.BEGINNING, Step.DRAW),
    (Phase.PRECOMBAT_MAIN, Step.MAIN),
    (Phase.COMBAT, Step.BEGINNING_OF_COMBAT),
    (Phase.COMBAT, Step.DECLARE_ATTACKERS),
    (Phase.COMBAT, Step.DECLARE_BLOCKERS),
    (Phase.COMBAT, Step.COMBAT_DAMAGE),
    (Phase.COMBAT, Step.END_OF_COMBAT),
    (Phase.POSTCOMBAT_MAIN, Step.MAIN),
    (Phase.ENDING, Step.END_STEP),
    (Phase.ENDING, Step.CLEANUP),
)


@dataclass(slots=True)
class TurnOptions:
    """Rules that vary by format or by house rule."""

    #: CR 103.8a makes the player who goes first skip their first draw step -
    #: but that rule is written for *two-player* games. A multiplayer game,
    #: which Commander is, has no such rule and the starting player draws.
    #: Exposed because plenty of playgroups believe otherwise, and it changes
    #: goldfishing numbers noticeably.
    first_player_skips_first_draw: bool = False
    #: The simulation stops after this many turns around the table.
    max_rounds: int = 25


def run_game(game: Game, options: TurnOptions | None = None) -> Game:
    """Play a game to its end, or until the turn cap.

    Returns the game that was actually finished, which is normally the one
    passed in - but CR 727 lets a game restart, and a restarted game is a
    different Game object. Returning it means the caller records the game that
    produced the result rather than the one that was abandoned.
    """
    options = options or TurnOptions()
    limit = options.max_rounds * max(1, len(game.players))

    while not game.game_over and game.turn < limit:
        take_turn(game, options)
        if game.restart_requested:
            # CR 727.1: the game ends immediately and nobody wins, loses or
            # draws it. Starting the replacement game is the runner's job -
            # this loop only stops, because a new game is a new Game.
            game.log.record(
                game, "Game restarted; no player won or lost", kind="restart"
            )
            game.game_over = True
            game.winners = []
            break
        if game.game_over:
            break
        game.active_player = _next_active_player(game)

    if game.restart_requested:
        return _restart(game, options)

    if game.runaway:
        game.log.record(
            game,
            f"Game stopped as a runaway: {game.runaway}. This is a bug in a "
            "card, not a property of the deck - the ability had no cost the "
            "engine could charge.",
            kind="runaway",
        )
    elif not game.game_over:
        game.log.record(game, "Turn cap reached; game is a stall-out", kind="stall")
    return game


#: CR 727 games nest only in the sense that a restart can restart. A cap keeps
#: a pathological loop from running forever; two Karns in a row is already
#: beyond anything a goldfishing run needs to model faithfully.
MAX_RESTARTS = 4


def _restart(game: Game, options: TurnOptions, depth: int = 0) -> Game:
    """CR 727.1: build the replacement game and play it.

    Every card that was in the old game is in the new one (727.2), the player
    who controlled the restarting effect goes first (727.1a), and the seed is
    derived from the old game's so the whole thing stays reproducible from one
    run seed.
    """
    if depth >= MAX_RESTARTS:
        game.log.record(game, "Restart cap reached", kind="cap")
        game.game_over = True
        return game

    decks = getattr(game, "source_decks", None)
    if not decks:
        # Nothing to rebuild from. The game is over and nobody won, which is
        # what CR 727.1 says happens to the restarted game either way.
        game.log.record(
            game, "Restart requested but the decks are unknown", kind="unsupported"
        )
        return game

    from ..cr100_game_concepts.cr103_setup import new_game

    starting = game.restart_starting_player or 0
    fresh = new_game(
        decks,
        seed=game.rng.randrange(1 << 30),
        log_enabled=game.log.enabled,
        ability_provider=game.ability_provider,
        randomize_turn_order=False,
    )
    fresh.source_decks = decks
    # CR 727.1a: the player who restarted the game goes first.
    fresh.active_player = starting
    fresh.log.record(fresh, "Game restarted (CR 727)", kind="restart")
    return run_game(fresh, options)


def _next_active_player(game: Game) -> PlayerId:
    """Whose turn is next, honouring extra turns (CR 500.7).

    CR 500.7: the most recently created extra turn is taken first, so the
    queue is drained from the end rather than the front. It makes no
    difference while one player is stacking up turns for themselves, and all
    the difference once two effects hand turns to different players - the
    engine took them in the order they were created, which is the reverse of
    what the rule says.
    """
    if game.extra_turns:
        return game.extra_turns.pop()
    return game.next_player(game.active_player)


@dataclass(slots=True)
class _TurnProgress:
    """What the turn has got through so far.

    CR 505.1b counts main phases within the current turn, and CR 505.1a reads
    that count to decide which of them is the precombat main phase. Neither
    can be answered from ``game.phase`` alone, and the count belongs to one
    turn rather than to the game, so it is carried down the call chain instead
    of being stored on the Game.
    """

    #: How many main phases have begun this turn (CR 505.1b).
    main_phases: int = 0


def clear_turn_activations(game: Game) -> None:
    """CR 602.5b / 606.3: "only once each turn" is counted per turn.

    Without this the count only ever grew, and a planeswalker's abilities
    could each be activated once in the whole game.
    """
    for obj in game.objects.values():
        if obj.activations_this_turn:
            obj.activations_this_turn.clear()


def take_turn(game: Game, options: TurnOptions | None = None) -> None:
    """Run one complete turn."""
    options = options or TurnOptions()
    game.turn += 1
    player = game.player(game.active_player)
    # "This turn" means the current turn for everyone, not just the active
    # player - an opponent who draws during your turn has drawn a card this
    # turn. Resetting only the active player left the opening hand counted as
    # cards drawn on turn one.
    # "This turn" starts again here, for everyone. Cleared with the players'
    # own per-turn state so the two can never disagree about which turn it is.
    game.turn_history.clear()
    # A loop shortcut last turn may be run again this turn - the untap step
    # has given it back its fuel.
    game.exhausted_loop_steps.clear()
    game.continuing_loop_steps.clear()
    for each in game.players:
        each.begin_turn()
    clear_turn_activations(game)

    # CR 502.2: the day/night check is the untap step's second turn-based
    # action. It is taken here rather than inside ``_untap_step`` only because
    # it reads the previous turn's spell count and that count is rolled over
    # below; nothing in the untap step can observe the difference, since no
    # player receives priority in it (CR 502.4).
    # CR 731.2: the day/night flip reads the *previous* turn's spell count, so
    # it is checked before this turn's counter is reset.
    from ..cr700_additional_rules.cr725_designations import check_day_night_transition

    check_day_night_transition(game, game.active_player)
    game.spells_cast_last_turn = game.spells_cast_this_turn
    game.spells_cast_this_turn = 0

    # "Until your next turn" ends as that turn begins, before any of it
    # happens - so a creature lent out until your next turn comes back before
    # you untap, not after.
    from ..cr600_spells_and_abilities.cr611_durations import expire_at_start_of_turn

    expire_at_start_of_turn(game)

    game.log.record(
        game, f"--- Turn {game.turn}: {player.name} ---", kind="turn", player=player.id
    )
    game.emit(Event(EventKind.TURN_BEGAN, player=game.active_player))

    progress = _TurnProgress()
    for phase, step in TURN_SEQUENCE:
        if game.game_over:
            return
        # CR 724.1b: "end the turn" skips every remaining step except cleanup.
        if game.end_turn_requested and step is not Step.CLEANUP:
            continue
        if int(step) in game.skipped_steps:
            continue
        _run_step(game, phase, step, options, progress)
        _run_extras(game, options, progress)

    game.emit(Event(EventKind.TURN_ENDED, player=game.active_player))
    # CR 723.1: "control that player during that player's next turn" - the
    # effect covers the whole turn and ends as it does.
    game.release_player(game.active_player)
    game.end_turn_requested = False
    game.skipped_steps.clear()
    _end_of_turn_cleanup(game)


def _run_extras(
    game: Game, options: TurnOptions, progress: _TurnProgress | None = None
) -> None:
    """CR 500.8, 500.9: extra phases and steps happen after the one that made them.

    Drained in a loop rather than a single pass, because an extra phase can
    itself create another - which is how the "take an extra turn" style of
    combo works one rung down.

    CR 500.8 and CR 500.9 both say the most recently created one goes first,
    so each queue is drained from the end. Drained from the front, "an
    additional combat phase followed by an additional main phase" came out in
    the order the two were created rather than the order the card prints.
    """
    guard = 0
    while (game.extra_steps or game.extra_phases) and not game.game_over:
        guard += 1
        if guard > 100:
            game.log.record(game, "Extra phase/step loop capped", kind="cap")
            game.extra_steps.clear()
            game.extra_phases.clear()
            return
        if game.extra_steps:
            step = game.extra_steps.pop()
            _run_step(game, game.phase, step, options, progress)
            continue
        phase = game.extra_phases.pop()
        for sequence_phase, step in TURN_SEQUENCE:
            if sequence_phase is phase:
                _run_step(game, phase, step, options, progress)


#: CR 500.1: the step each phase starts with. The main phases have no steps
#: at all (CR 505.2), and are modelled as a single MAIN step.
_FIRST_STEP_OF_PHASE = frozenset(
    {Step.UNTAP, Step.MAIN, Step.BEGINNING_OF_COMBAT, Step.END_STEP}
)


def _run_step(
    game: Game,
    phase: Phase,
    step: Step,
    options: TurnOptions,
    progress: _TurnProgress | None = None,
) -> None:
    if _skipped_for_want_of_attackers(game, step):
        return
    phase = _main_phase_kind(phase, step, progress)
    game.phase = phase
    game.step = step
    # CR 500.6: "at the beginning of" this step or phase triggers now, and
    # waits for the next time a player would receive priority. A phase begins
    # with its first step (CR 500.1), and nothing announced it, so every "at
    # the beginning of combat" and "of your precombat main phase" trigger
    # waited for an event that never came.
    if step in _FIRST_STEP_OF_PHASE:
        game.emit(
            Event(EventKind.PHASE_BEGAN, player=game.active_player, amount=int(phase))
        )
    game.emit(Event(EventKind.STEP_BEGAN, player=game.active_player, amount=int(step)))

    _turn_based_actions(game, step, options)

    if step is Step.CLEANUP:
        _cleanup_step(game)
        return

    if step in (Step.UNTAP,):
        # CR 502.4: no player receives priority during the untap step.
        return

    # Every step but untap and cleanup gives the active player priority once
    # its turn-based actions are done, and then runs until all players pass in
    # succession with the stack empty (CR 500.2). One rule says so per step,
    # and they say the same thing: CR 503.1 for the upkeep step, CR 504.2 for
    # the draw step, CR 505.6 for a main phase (which has no steps at all, so
    # CR 505.2 ends the phase itself), CR 507.2 for the beginning of combat,
    # CR 511.1 for the end of combat, and CR 513.1 for the end step.
    run_priority(game)
    _end_of_step_actions(game, step)

    # CR 500.5: unspent mana empties as the step or phase ends.
    _empty_mana_pools(game)
    game.emit(Event(EventKind.STEP_ENDED, player=game.active_player, amount=int(step)))


def _main_phase_kind(
    phase: Phase, step: Step, progress: _TurnProgress | None
) -> Phase:
    """Which main phase this is (CR 505.1a).

    CR 505.1a: only the turn's *first* main phase is the precombat main phase;
    every later one is a postcombat main phase, including an extra main phase
    an effect created after combat and the second main phase of a turn whose
    combat phase was skipped.

    This is not bookkeeping. The precombat main phase is where CR 505.4 puts a
    lore counter on every Saga and CR 728 runs the rad-counter procedure, and
    an extra main phase asked for as ``Phase.PRECOMBAT_MAIN`` - which is what
    every "additional main phase" effect asks for - ran both of them a second
    time in the same turn.
    """
    if step is not Step.MAIN:
        return phase
    if progress is None:
        return phase
    progress.main_phases += 1
    return Phase.PRECOMBAT_MAIN if progress.main_phases == 1 else Phase.POSTCOMBAT_MAIN


def _skipped_for_want_of_attackers(game: Game, step: Step) -> bool:
    """CR 508.8: an empty combat has no declare blockers or damage step.

    Not a cosmetic tidy-up. A step that happens is a step in which every player
    receives priority (CR 117.3), so running these two anyway hands out two
    rounds of priority the rules say do not exist - two more windows to cast
    instants, two more chances for an "at the beginning of" ability, and two
    more step boundaries for "until end of combat" to be measured against.

    Asked here rather than in the turn sequence because extra combat phases
    reach the steps through this same function, and an extra combat with
    nobody attacking is the commonest way to get an empty one.

    Checked when the step would begin rather than when attackers were
    declared, because CR 508.8 covers creatures put onto the battlefield
    attacking too, and those arrive after the declaration.
    """
    if step is not Step.DECLARE_BLOCKERS and step is not Step.COMBAT_DAMAGE:
        return False
    combat = getattr(game, "combat", None)
    return combat is None or not combat.attacking


def _end_of_step_actions(game: Game, step: Step) -> None:
    """What happens as a step ends, rather than as it begins.

    CR 511.3: creatures are removed from combat as soon as the end of combat
    step *ends*. Clearing combat as the step began emptied it before anyone
    had priority in it, so nothing was still attacking during the step - and
    ninjutsu, which needs an unblocked attacker to return (CR 508.4d, 509.1h),
    could not be used there at all.
    """
    if step is Step.END_OF_COMBAT:
        _end_of_combat(game)


def _empty_mana_pools(game: Game) -> None:
    # CR 500.5, unless a bench has suspended it - see rules.kernel.relaxations.
    # (Not CR 500.4, which is now the rule about effects that last until a step
    # or phase expiring as it begins.)
    if game.relaxations.mana_pools_persist:
        return
    for player in game.players:
        lost = player.mana_pool.clear()
        if lost:
            game.emit(Event(EventKind.MANA_EMPTIED, player=player.id, amount=lost))


# ---------------------------------------------------------------------------
# Turn-based actions (CR 703.4)
# ---------------------------------------------------------------------------


def _turn_based_actions(game: Game, step: Step, options: TurnOptions) -> None:
    """The actions a step or phase takes automatically (CR 703.1, 703.3).

    CR 703.3: they are taken as the step or phase begins, before any player
    receives priority, and they do not use the stack. CR 703.1a draws the line
    this function sits on: an ability that merely *watches* for a step to
    begin is a triggered ability and is not here - which is why the upkeep and
    end step emit an event instead of doing anything.
    """
    if step is Step.UNTAP:
        _untap_step(game)
    elif step is Step.MAIN and game.phase is Phase.PRECOMBAT_MAIN:
        # The only turn-based actions of a main phase, and all three are
        # limited to the precombat one - which CR 505.1a makes the turn's
        # first main phase and no other. CR 505.3's Archenemy scheme is
        # deliberately absent: this is a Commander game.
        # CR 728.1: the rad-counter procedure is a turn-based action at the
        # start of the precombat main phase.
        # CR 505.4: a lore counter on each Saga the active player controls.
        # CR 505.5, and CR 703.4g for its place straight after the lore
        # counters: if the active player controls an Attraction, they roll to
        # visit their Attractions. The visit abilities that triggers wait for
        # priority like any others.
        from ..cr300_card_types.cr300_card_types import saga_lore_counters
        from ..cr700_additional_rules.cr717_attractions import (
            controls_an_attraction,
            roll_to_visit,
        )
        from ..cr700_additional_rules.cr725_designations import rad_counter_milling

        rad_counter_milling(game, game.active_player)
        saga_lore_counters(game)
        if controls_an_attraction(game, game.active_player):
            roll_to_visit(game, game.active_player)
    elif step is Step.UPKEEP:
        # CR 503.1: the upkeep step has no turn-based actions of its own; this
        # event exists only so that abilities can trigger off the step
        # beginning (CR 500.6), and the priority that follows is the whole of
        # the step.
        # CR 503.1a: abilities that trigger "at the beginning of the upkeep"
        # trigger now. Nothing announced the step, so no upkeep trigger - echo,
        # cumulative upkeep, rebound's "at the beginning of your next upkeep" -
        # ever fired in a game; only a test that emitted the event by hand saw
        # one.
        game.emit(Event(EventKind.UPKEEP, player=game.active_player))
        # CR 726.2: the initiative's holder ventures into Undercity.
        from ..cr700_additional_rules.cr725_designations import initiative_upkeep

        initiative_upkeep(game)
    elif step is Step.END_STEP:
        # CR 513.1: the end step has no turn-based actions either.
        # CR 513.1a: the same for "at the beginning of the end step", which is
        # when unearth's delayed "exile it" and every "sacrifice it at the
        # beginning of the next end step" are waiting to fire.
        #
        # CR 513.2 comes out of emitting it once, here, as the step begins: a
        # permanent that arrives later in the step, or a delayed ability
        # created later in it, has missed the event and waits for the next
        # turn's end step. The step does not back up for them.
        game.emit(Event(EventKind.END_STEP, player=game.active_player))
        # CR 725.2: the monarch draws at the beginning of their end step.
        from ..cr700_additional_rules.cr725_designations import monarch_end_step_draw

        monarch_end_step_draw(game)
    elif step is Step.DRAW:
        _draw_step(game, options)
    elif step is Step.DECLARE_ATTACKERS:
        _declare_attackers(game)
    elif step is Step.DECLARE_BLOCKERS:
        _declare_blockers(game)
    elif step is Step.COMBAT_DAMAGE:
        _combat_damage(game)
    # The beginning of combat step has nothing to do as it begins either.
    # CR 507.1 would have the active player choose one opponent to be *the*
    # defending player - but only in a multiplayer game where the opponents do
    # not all become defending players automatically. Commander uses the
    # attack multiple players option (CR 903.2, 802.1), under which they all
    # do, so the turn-based action does not happen and every opponent stays
    # attackable; cr506_combat._may_be_attacked settles it per attacker.
    #
    # The end of combat step has nothing to do as it begins. Combat is cleared
    # as it ends (CR 511.3), by ``_end_of_step_actions``.


def _untap_step(game: Game) -> None:
    """CR 502: phasing, then untapping. No priority, no triggers resolve here."""
    active = game.active_player

    # CR 702.179d: the once-each-turn speed increase resets. Without this a
    # player's speed would rise at most once per game rather than once per
    # turn, and no deck would ever reach max speed.
    for player in game.players:
        player.speed_increased_this_turn = False

    # CR 502.1: phasing happens first, before untapping. A permanent with
    # phasing alternates every one of its controller's untap steps, so both
    # directions are handled here and nowhere else.
    for object_id in list(game.battlefield):
        obj = game.objects.get(object_id)
        if obj is None or obj.controller != active:
            continue
        if obj.phased_out:
            obj.phased_out = False
            obj.phasing_scheduled = False
            game.emit(Event(EventKind.PHASED_IN, object_id=obj.id, player=active))
        elif game.characteristics(obj).has_keyword("Phasing"):
            obj.phased_out = True
            obj.phasing_scheduled = True
            game.emit(Event(EventKind.PHASED_OUT, object_id=obj.id, player=active))
    game.invalidate_characteristics()

    # CR 502.3: the active player untaps their permanents, all at once - but
    # only the ones allowed to untap. Untapping everything unconditionally is
    # why "doesn't untap during its controller's untap step" did nothing at
    # all: the parser read the prohibition, the restriction system held it,
    # and the one place that had to ask never did.
    #
    # Both acts are consulted. UNTAP_DURING_UNTAP_STEP is the wording on
    # Sleep and the tap-down auras; a plain UNTAP prohibition is how exert and
    # "this creature can't untap" are modelled, and neither should let a
    # permanent untap here.
    from .restrictions import Act, prohibited

    for obj in list(game.permanents(active)):
        if not obj.tapped:
            continue
        blocked = prohibited(game, Act.UNTAP_DURING_UNTAP_STEP, obj=obj) or prohibited(
            game, Act.UNTAP, obj=obj
        )
        if blocked is not None:
            game.log.record(
                game,
                f"{game.characteristics(obj).name} does not untap ({blocked})",
                kind="restriction",
            )
            continue
        obj.tapped = False
    game.emit(Event(EventKind.UNTAP_STEP, player=active))

    # CR 302.6: everything the active player controls has now been controlled
    # since their turn began, so summoning sickness ends here.
    for obj in game.permanents(active):
        obj.summoning_sick = False

    game.invalidate_characteristics()


def _draw_step(game: Game, options: TurnOptions) -> None:
    """CR 504.1: the active player draws a card. This is a turn-based action.

    CR 504.2 is the rest of the step: the active player then gets priority,
    which ``_run_step`` does for every step that has one.
    """
    if options.first_player_skips_first_draw:
        if game.turn == 1 and game.active_player == game.turn_order[0]:
            game.log.record(game, "First player skips their first draw step")
            return
    game.draw(game.active_player, 1)
    game.emit(Event(EventKind.DRAW_STEP, player=game.active_player))


def _declare_attackers(game: Game) -> None:
    from .cr506_combat import declare_attackers

    declare_attackers(game)


def _declare_blockers(game: Game) -> None:
    from .cr506_combat import declare_blockers

    declare_blockers(game)


def _combat_damage(game: Game) -> None:
    from .cr506_combat import deal_combat_damage

    deal_combat_damage(game)


def _end_of_combat(game: Game) -> None:
    from ..cr600_spells_and_abilities.cr611_durations import expire_at_end_of_combat
    from .cr506_combat import end_combat

    end_combat(game)
    # CR 511.2: "Effects that last 'until end of combat' expire at the end
    # of the combat phase" - which CR 511.3 places at the moment this step
    # ends, the same moment creatures are removed from combat.
    expire_at_end_of_combat(game)


# ---------------------------------------------------------------------------
# Cleanup (CR 514)
# ---------------------------------------------------------------------------


#: Guard against an effect that keeps triggering during cleanup for ever.
MAX_CLEANUP_ROUNDS = 20


def _cleanup_step(game: Game) -> None:
    """CR 514.

    Discard to hand size and clear damage happen simultaneously and without
    priority. But if that causes a state-based action or a triggered ability,
    players *do* get priority, and then another cleanup step happens
    (CR 514.3a) - which is why this loops.
    """
    for _ in range(MAX_CLEANUP_ROUNDS):
        _discard_to_hand_size(game)
        _clear_damage_and_expire_effects(game)
        game.emit(Event(EventKind.CLEANUP, player=game.active_player))

        needs_priority = bool(game.pending_triggers)
        from ..cr700_additional_rules.cr704_sba import check_state_based_actions

        if check_state_based_actions(game):
            needs_priority = True
        if not needs_priority:
            _empty_mana_pools(game)
            return

        settle(game)
        run_priority(game)
        _empty_mana_pools(game)
        if game.game_over:
            return


def _discard_to_hand_size(game: Game) -> None:
    """CR 514.1: the active player discards down to their maximum hand size."""
    from .restrictions import Act, permitted

    player = game.player(game.active_player)
    # CR 402.2: "You have no maximum hand size" is a permission to skip this
    # entirely. Reliquary Tower and Thought Vessel are among the most played
    # cards in the format and did nothing at all without this.
    if permitted(game, Act.DISCARD_TO_HAND_SIZE, player=player.id) is not None:
        return
    agent = game.agent_for(player.id)
    while len(player.hand) > player.max_hand_size:
        choice = agent.choose_discard(game, player.id) if agent is not None else player.hand[-1]
        obj = game.objects.get(choice) or game.objects[player.hand[-1]]
        actions.discard(game, obj)


def _clear_damage_and_expire_effects(game: Game) -> None:
    """CR 514.2: damage wears off and until-end-of-turn effects end, together."""
    from ..cr600_spells_and_abilities.cr611_durations import expire_at_cleanup

    for obj in game.permanents():
        obj.damage = 0
        obj.dealt_deathtouch_damage = False
        obj.regeneration_shields = 0

    expire_at_cleanup(game)
    game.invalidate_characteristics()


def _end_of_turn_cleanup(game: Game) -> None:
    """Reset per-turn state that is not part of the cleanup step itself."""
    for player in game.players:
        player.max_lands = 1
    for obj in game.permanents():
        obj.attacked_this_turn = False
        obj.blocked_this_turn = False
