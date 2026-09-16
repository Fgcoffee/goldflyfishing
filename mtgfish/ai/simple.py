"""A deliberately simple bot.

Not the real AI - that is the pilot/political-matrix work later. This exists so
the rules engine can be exercised by something that actually plays: lands get
played, spells get cast, creatures attack and block, and games end in wins
rather than stall-outs. Every rules test that involves a full game runs against
this, so its behaviour is kept boring and predictable on purpose.

Every decision is deterministic given the game state. Nothing here reads a
clock or a global random source, because replay reconstructs a game from its
seed alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..rules.effects import EffectKind
from ..rules.enums import CardType
from ..rules.ids import ObjectId, PlayerId
from ..rules.priority import PASS, Action, ActionKind

if TYPE_CHECKING:
    from ..rules.combat import Combat
    from ..rules.game import Game
    from ..rules.gameobject import GameObject


from ..rules.loops import LOOP_REPETITIONS, is_continuing

class SimpleAgent:
    """Plays out its hand and attacks when the maths is in its favour."""

    def __init__(self, *, aggressive: bool = True) -> None:
        self.aggressive = aggressive
        #: Actions that failed in the current step, not to be tried again in it.
        self._failed: set[tuple] = set()
        self._failed_window: tuple = ()
        #: (turn, card name) -> times chosen for casting this turn.
        self._casts: dict[tuple[int, str], int] = {}

    # -- learning from failure ------------------------------------------------

    #: How many times in one turn the bot will cast the same card. A loop that
    #: returns a spell to be cast again - flashback plus a recursion engine -
    #: cast Strike It Rich 214 times in one main phase and filled the board
    #: with 212 tokens, which is a game nobody learns anything from.
    MAX_CASTS_PER_TURN = 8

    def action_failed(self, game: Game, player: PlayerId, action: Action) -> None:
        """The engine refused an action this bot chose; do not repeat it this step.

        Failing again is certain unless something changed, and retrying at
        every priority - once per stack resolution - is what turned one
        unpayable ability into 258 attempts. Skipping a play that has since
        become possible, for the rest of one step, is the cheaper mistake.
        """
        self._sync_window(game)
        self._failed.add(self._key(action))

    def _sync_window(self, game: Game) -> None:
        window = (game.turn, int(game.phase), int(game.step))
        if window != self._failed_window:
            self._failed_window = window
            self._failed = set()

    @staticmethod
    def _key(action: Action) -> tuple:
        return (
            int(action.kind),
            int(action.source),
            action.ability_index,
            action.face_index,
            action.alternative_cost,
        )

    def _card_name(self, game: Game, action: Action) -> str:
        obj = game.objects.get(action.source)
        return getattr(getattr(obj, "card", None), "name", "") if obj is not None else ""

    def _cast_too_often(self, game: Game, action: Action) -> bool:
        return self._casts.get((game.turn, self._card_name(game, action)), 0) >= self.MAX_CASTS_PER_TURN

    # -- priority -----------------------------------------------------------

    def choose_action(self, game: Game, player: PlayerId, legal: list[Action]) -> Action:
        """Take the most developing action available, preferring lands.

        Mana abilities are never activated speculatively: casting activates
        exactly what it needs (CR 601.2g), and floating mana that then empties
        at end of step is pure waste.
        """
        self._sync_window(game)
        if self._failed:
            legal = [a for a in legal if self._key(a) not in self._failed]

        lands = [a for a in legal if a.kind is ActionKind.PLAY_LAND]
        if lands:
            return self._best_land(game, lands)

        casts = [
            a
            for a in legal
            if a.kind is ActionKind.CAST_SPELL and not self._cast_too_often(game, a)
        ]
        if casts:
            casts = self._worth_casting_now(game, player, casts)
        if casts:
            chosen = self._best_spell(game, player, casts)
            key = (game.turn, self._card_name(game, chosen))
            self._casts[key] = self._casts.get(key, 0) + 1
            return chosen

        activations = [
            a
            for a in legal
            if a.kind is ActionKind.ACTIVATE_ABILITY
            and not self._is_mana(game, a)
            and not self._repeated_too_often(game, a)
        ]
        if activations:
            return activations[0]

        return PASS

    #: How many times in one turn the bot will activate the same ability.
    #: Real play rarely wants more; an untap engine - Umbral Mantle on a
    #: creature that makes enough mana - is legal and unbounded, and a bot that
    #: always takes the first activation available pumped one creature 3,184
    #: times, a full board recomputation each, for one game of fifty.
    #:
    #: Set above the loop detector's threshold on purpose. The cap used to be
    #: eight, which is exactly how many cycles the detector needs - so a combo
    #: with a single warm-up activation was stopped one cycle short of being
    #: recognised, and never got to win. A loop the detector *does* recognise
    #: is shortcut and then exhausted by the engine, so this cap is only the
    #: backstop for repetition that is not a steady loop.
    MAX_ACTIVATIONS_PER_TURN = LOOP_REPETITIONS + 4

    def _repeated_too_often(self, game: Game, action: Action) -> bool:
        obj = game.objects.get(action.source)
        if obj is None:
            return False
        # A loop that has just killed a player is being re-aimed at the next;
        # stopping it here would leave that shortcut half-finished.
        if is_continuing(game, obj.controller, action):
            return False
        return obj.activations_this_turn.get(action.ability_index, 0) >= self.MAX_ACTIVATIONS_PER_TURN

    def _worth_casting_now(
        self, game: Game, player: PlayerId, casts: list[Action]
    ) -> list[Action]:
        """Filter out spells the bot should be holding mana for.

        The rule the brief asks for, in its simplest honest form: spend on
        your own turn, hold on everyone else's. A bot that casts every instant
        the moment it can is not playing at instant speed, it is playing at
        sorcery speed badly - it taps out in an opponent's upkeep and then
        cannot cast the four-drop it drew.

        The exception is the end step before its own turn. Mana that is about
        to be wasted is worth spending on anything, and holding past that
        point is not patience, it is hoarding.
        """
        from ..rules.enums import Phase, Step

        own_turn = game.active_player == player
        if own_turn and game.phase in (Phase.PRECOMBAT_MAIN, Phase.POSTCOMBAT_MAIN):
            return casts

        # The last window before untapping: use it or lose it.
        last_chance = (
            game.step is Step.END_STEP
            and game.next_player(game.active_player) == player
        )
        if last_chance:
            return casts

        # Otherwise hold, unless the spell is a permanent that can only be
        # cast now anyway - a flash creature is not a trick being wasted.
        return [
            action
            for action in casts
            if self._is_permanent(game, action) and not own_turn
        ]

    def _is_permanent(self, game: Game, action: Action) -> bool:
        obj = game.objects.get(action.source)
        if obj is None:
            return False
        types = game.characteristics(obj).type_line.types
        return not bool(types & (CardType.INSTANT | CardType.SORCERY))

    def _is_mana(self, game: Game, action: Action) -> bool:
        obj = game.objects.get(action.source)
        if obj is None:
            return False
        abilities = game.characteristics(obj).abilities
        if 0 <= action.ability_index < len(abilities):
            return abilities[action.ability_index].is_mana_ability
        return False

    def _best_land(self, game: Game, lands: list[Action]) -> Action:
        """Prefer a land that enters untapped and produces a needed color.

        Ties break on object id so the choice is reproducible.
        """
        def key(action: Action) -> tuple:
            obj = game.objects[action.source]
            chars = game.printed_characteristics(obj)
            produces = sum(1 for a in chars.abilities if a.is_mana_ability)
            return (-produces, obj.id)

        return min(lands, key=key)

    def _best_spell(self, game: Game, player: PlayerId, casts: list[Action]) -> Action:
        """Cast the most expensive thing affordable, preferring creatures.

        Spending mana is almost always better than holding it when the bot has
        no instants worth waiting for, and the biggest castable spell is the
        crudest reasonable proxy for the best one.
        """
        def key(action: Action) -> tuple:
            obj = game.objects[action.source]
            chars = game.characteristics(obj)
            is_creature = chars.has_type(CardType.CREATURE)
            return (-int(is_creature), -chars.mana_value, obj.id)

        return min(casts, key=key)

    # -- resolution choices -------------------------------------------------

    def choose_optional(self, game: Game, player: PlayerId, effect) -> bool:
        return True

    def choose_discard(self, game: Game, player: PlayerId) -> ObjectId:
        """Discard the most expensive card, as the least castable."""
        hand = game.player(player).hand
        if not hand:
            return ObjectId(0)
        return max(
            hand,
            key=lambda oid: (game.printed_characteristics(game.objects[oid]).mana_value, oid),
        )

    def order_triggers(self, game: Game, player: PlayerId, triggers: list) -> list:
        return triggers

    # -- combat -------------------------------------------------------------

    def declare_attackers(
        self, game: Game, player: PlayerId, candidates: list[GameObject]
    ) -> dict[ObjectId, int]:
        """Attack the player closest to losing, with creatures that survive.

        A creature attacks when nothing an opponent controls can profitably
        block it, or when the bot is attacking the weakest player and can
        afford the trade. Crude, but it produces games that end.
        """
        opponents = game.opponents(player)
        if not opponents:
            return {}

        target = self._threat_target(game, player, opponents)
        blockers = [
            obj
            for opponent in opponents
            for obj in game.permanents(opponent)
            if game.characteristics(obj).is_creature and not obj.tapped
        ]
        best_blocker = max(
            (game.characteristics(b).power or 0 for b in blockers), default=0
        )

        out: dict[ObjectId, int] = {}
        for obj in sorted(candidates, key=lambda o: o.id):
            chars = game.characteristics(obj)
            power = chars.power or 0
            toughness = chars.toughness or 0
            if power <= 0:
                continue
            if not self.aggressive and toughness <= best_blocker:
                continue
            out[obj.id] = target
        return out

    def _threat_target(self, game: Game, player: PlayerId, opponents: list) -> PlayerId:
        """Who to attack: whoever is closest to dying, then whoever is scariest.

        The tie-break matters far more than it looks. Breaking ties by player
        id makes every bot at the table pick the *same* opponent on turn one,
        when everybody is on forty life and nothing distinguishes them - and
        the lowest seat then gets attacked by all three opponents in every
        game of every run. A goldfish measuring seat zero read 0% win rate
        across 200 games because of it.

        So an exact tie is broken by turn order *relative to the attacker*:
        each player looks left, which is what a table of humans does when no
        opponent stands out, and which spreads the attacks instead of piling
        them on one seat.
        """
        order = game.turn_order_from(player)
        distance = {opponent: index for index, opponent in enumerate(order)}

        def rank(opponent: PlayerId) -> tuple:
            other = game.player(opponent)
            board = sum(
                game.characteristics(obj).power or 0
                for obj in game.permanents(opponent)
                if game.characteristics(obj).is_creature
            )
            # Lowest life first - a player who can be finished should be. Then
            # the biggest board, because the player most able to kill you is
            # the one worth pressuring. Distance last, and only as a tie-break.
            return (other.life, -board, distance.get(opponent, 99))

        return min(opponents, key=rank)

    def declare_blockers(
        self,
        game: Game,
        player: PlayerId,
        combat: Combat,
        available: list[GameObject],
    ) -> dict[ObjectId, list[ObjectId]]:
        """Block what would kill us, and take free trades.

        Blocks the biggest attackers first with the smallest creature that can
        survive; if life is low enough to matter, chump-blocks instead.
        """
        from ..rules.combat import can_block

        incoming = [
            game.objects[a]
            for a, defending_player in sorted(combat.attacking.items())
            if defending_player == player and a in game.objects
        ]
        if not incoming:
            return {}

        life = game.player(player).life
        total_damage = sum(game.characteristics(a).power or 0 for a in incoming)
        desperate = total_damage >= life

        assignments: dict[ObjectId, list[ObjectId]] = {}
        used: set[ObjectId] = set()

        for attacker in sorted(
            incoming, key=lambda a: -(game.characteristics(a).power or 0)
        ):
            attacker_chars = game.characteristics(attacker)
            attacker_power = attacker_chars.power or 0
            attacker_toughness = attacker_chars.toughness or 0

            best: GameObject | None = None
            for blocker in sorted(available, key=lambda o: o.id):
                if blocker.id in used:
                    continue
                if not can_block(game, blocker, attacker):
                    continue
                blocker_chars = game.characteristics(blocker)
                blocker_power = blocker_chars.power or 0
                blocker_toughness = blocker_chars.toughness or 0

                survives = blocker_toughness > attacker_power
                kills = blocker_power >= attacker_toughness
                if survives or kills or desperate:
                    best = blocker
                    break

            if best is not None:
                assignments[best.id] = [attacker.id]
                used.add(best.id)

        return assignments

    def order_blockers(
        self, game: Game, player: PlayerId, attacker: ObjectId, blockers: list[ObjectId]
    ) -> list[ObjectId]:
        """Kill the biggest blocker first."""
        return sorted(
            blockers,
            key=lambda b: (-(game.characteristics(game.objects[b]).power or 0), b),
        )


#: Effects that are bad for whatever they point at. The bot aims these at
#: opponents; everything else it aims at itself. Crude, but it is the whole
#: difference between a bot that kills its opponents' creatures and one that
#: Murders its own - and the default "first legal candidate" does the latter.
HARMFUL = frozenset(
    {
        EffectKind.DESTROY,
        EffectKind.EXILE,
        EffectKind.DAMAGE,
        EffectKind.COUNTER_SPELL,
        EffectKind.SACRIFICE,
        EffectKind.RETURN_TO_HAND,
        EffectKind.TAP,
        EffectKind.GOAD,
        EffectKind.LOSE_LIFE,
        EffectKind.MILL,
        EffectKind.DISCARD,
        EffectKind.REMOVE_COUNTERS,
        EffectKind.GAIN_CONTROL,
        EffectKind.FIGHT,
    }
)


def _targeted_effects(game: Game, source: ObjectId) -> list:
    """The targeted effects of whatever is being cast or activated.

    Read off the object rather than passed in, because the engine's agent
    protocol hands over candidate ids and nothing else - deliberately, so the
    engine never has to explain itself to a bot.
    """
    obj = game.objects.get(source)
    if obj is None:
        return []
    if obj.ability is not None:
        abilities = [obj.ability]
    else:
        abilities = list(game.characteristics(obj).abilities)
    return [
        node
        for ability in abilities
        for effect in ability.effects
        for node in effect.walk()
        if node.is_targeted
    ]


class _TargetingMixin:
    """Target selection, shared so the pilots inherit it unchanged later."""

    def choose_targets(
        self,
        game: Game,
        player: PlayerId,
        source: ObjectId,
        candidates: list,
    ) -> tuple:
        """Aim harmful effects at opponents and helpful ones at yourself.

        Within each group the biggest creature is picked - the scariest thing
        to remove, and the best thing to pump. A bot that always took the
        first legal candidate would spend its removal on its own board, and
        the resulting win rates would be meaningless.
        """
        effects = _targeted_effects(game, source)
        out = []
        for index, group in enumerate(candidates):
            if not group:
                out.append(())
                continue
            effect = effects[index] if index < len(effects) else None
            harmful = effect is not None and effect.kind in HARMFUL
            out.append((self._best(game, player, group, harmful=harmful),))
        return tuple(out)

    def _best(
        self, game: Game, player: PlayerId, group: list, *, harmful: bool
    ) -> ObjectId:
        mine = [oid for oid in group if self._controlled_by(game, oid, player)]
        theirs = [oid for oid in group if oid not in mine]
        preferred = theirs if harmful else mine
        pool = preferred or group

        from ..rules.ids import is_player_target, target_player

        def size(object_id: ObjectId) -> tuple:
            if is_player_target(object_id):
                # Among players, the lowest life total - the one a drain or a
                # burn spell finishes first. Ties keep the offered order, so
                # the choice is deterministic. A creature still outranks a
                # player in a mixed "any target" group, as it always did.
                return (0, -game.player(target_player(object_id)).life)
            obj = game.objects.get(object_id)
            if obj is None:
                return (0, object_id)
            chars = game.characteristics(obj)
            return ((chars.power or 0) + (chars.toughness or 0), -object_id)

        return max(pool, key=size)

    @staticmethod
    def _controlled_by(game: Game, object_id: ObjectId, player: PlayerId) -> bool:
        from ..rules.ids import is_player_target, target_player

        if is_player_target(object_id):
            # A player target is "mine" when it is me. Without this every
            # player looked like somebody else, so "target player loses 1
            # life" was aimed at the bot itself.
            return target_player(object_id) == player
        obj = game.objects.get(object_id)
        return obj is not None and obj.controller == player


# Grafted onto SimpleAgent rather than left as a subclass, because the
# simulator seats SimpleAgent directly and a bot that mis-aims its removal
# produces win rates that describe nothing.
SimpleAgent.choose_targets = _TargetingMixin.choose_targets
SimpleAgent._best = _TargetingMixin._best
SimpleAgent._controlled_by = staticmethod(_TargetingMixin._controlled_by)
