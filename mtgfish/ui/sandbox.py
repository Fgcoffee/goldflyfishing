"""A board you can drive by hand, to check the parser and the rules.

This is the debugging surface for the two things that can silently be wrong:
what the parser made of a card, and what the engine does with it. Everything
here is headless and returns plain dictionaries, so the whole thing is
testable without a window - a GUI whose logic cannot be tested is a GUI whose
logic is not tested.

The opponent does nothing on purpose. A sandbox where the other player fights
back is a game, not an instrument: you cannot tell whether your card behaved
oddly or whether the opponent interfered.

Nothing here bypasses the rules of *casting and resolving*. Cards are *put*
into zones directly, which is how you set up a position, but playing them goes
through ``cast_spell`` and ``activate_ability`` like anything else - so if the
engine would refuse, the sandbox refuses, and the reason comes back as text.

What is switched off, by default, is the handful of rules that end the session
rather than tell you anything: the four ways a player loses, the loss for
drawing from a library a hand-built board does not have, mana pools emptying
between steps, maximum hand size, the land drop, and summoning sickness. Each
is an individually named switch (see ``BenchRules``) that can be put back, so
the sandbox can still be used to test the rule itself. They are off by default
because the alternative - which is what this was - is an instrument that kills
you on the first draw step and reports it as a result.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace

from ..data.db import CardDatabase
from ..parser import parse_card
from ..parser.compile import OracleAbilities
from ..parser.explain import explain_ability
from ..parser.verdicts import Verdict, VerdictStore
from ..rules.enums import Phase, Step, Zone
from ..rules.game import Game
from ..rules.gameobject import GameObject
from ..rules.casting import _candidates_for
from ..rules.ids import ObjectId, PlayerId, is_player_target, target_player
from ..rules.log import GameLog
from ..rules.player import DEFAULT_MAX_HAND_SIZE, Player
from ..rules.priority import Action, ActionKind
from ..rules import relaxations
from ..rules.relaxations import STRICT, Relaxations

#: Where a card can be dropped when setting up a position.
PLACEABLE = {
    "battlefield": Zone.BATTLEFIELD,
    "hand": Zone.HAND,
    "graveyard": Zone.GRAVEYARD,
    "exile": Zone.EXILE,
    "library": Zone.LIBRARY,
    "command": Zone.COMMAND,
}


@dataclass(frozen=True, slots=True)
class BenchRules:
    """Which rules the bench has switched off, and what each one costs you.

    Six switches, all on by default, in two groups.

    The first three are *engine* relaxations: the engine itself consults them
    (see ``rules.relaxations``) because nothing outside the engine can stop a
    state-based action. The last three are applied here, on the board, because
    they are nothing more than a player field or a flag - there is no reason
    to teach the rules layer about them.

    All on by default because every one of them exists to stop the instrument
    ending before the thing being tested happens. All of them can be put back,
    because the rule itself is sometimes the thing being tested: to watch a
    player actually deck, switch the empty-library rule back on.
    """

    #: CR 704.5a-c, 903.10.
    players_cannot_lose: bool = True
    #: CR 704.5b.
    draws_from_an_empty_library_do_nothing: bool = True
    #: CR 500.4.
    mana_pools_persist: bool = True
    #: CR 402.2 / 514.1: no discarding down to seven in the cleanup step. A
    #: hand stocked for a test is not a hand that was drawn.
    no_maximum_hand_size: bool = True
    #: CR 305.2: any number of lands a turn. Setting up a mana base one land
    #: drop per turn is not a test of anything.
    unlimited_land_drops: bool = True
    #: CR 302.6: creatures can attack and tap the turn they arrive. ``put``
    #: has always done this for cards placed directly; this extends it to
    #: creatures actually cast on the bench, which is the more honest test.
    no_summoning_sickness: bool = True

    def with_field(self, name: str, value: bool) -> BenchRules:
        if name not in RULE_NAMES:
            raise KeyError(f"no such bench rule: {name!r}")
        return replace(self, **{name: bool(value)})

    def as_dict(self) -> dict[str, bool]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @property
    def relaxations(self) -> Relaxations:
        """The subset the engine has to be told about."""
        out = STRICT
        for name in relaxations.NAMES:
            out = out.with_field(name, getattr(self, name))
        return out


RULE_NAMES: tuple[str, ...] = tuple(f.name for f in fields(BenchRules))

#: Every rule enforced, as in a game. The bench is also the most convenient way
#: to build a board in a test - ``put`` a few cards and go - and a test about a
#: rule this bench suspends has to be able to ask for it back in one line:
#: ``Sandbox(db=..., rules=STRICT_BENCH)``. Without that, a test of the cleanup
#: discard or of a loop that kills would be quietly testing the bench instead.
STRICT_BENCH = BenchRules(**{name: False for name in RULE_NAMES})

# The engine's relaxations are named identically here on purpose, so one switch
# is one name all the way down. Checked rather than assumed: a relaxation added
# to the rules layer and not to this dataclass would be a switch the sandbox
# could never turn off, and the failure would be an AttributeError deep in a
# board refresh rather than here.
assert set(relaxations.NAMES) <= set(RULE_NAMES), (
    f"bench rules are missing an engine relaxation: "
    f"{sorted(set(relaxations.NAMES) - set(RULE_NAMES))}"
)

#: One line each, so a checkbox can say what it does. The engine-level ones
#: describe themselves; the rest are described here.
RULE_DESCRIPTIONS: dict[str, str] = dict(relaxations.DESCRIPTIONS) | {
    "no_maximum_hand_size": (
        "No discarding down to seven in the cleanup step (CR 402.2, 514.1), "
        "so a hand stocked for a test survives the turn"
    ),
    "unlimited_land_drops": (
        "Any number of lands each turn (CR 305.2), so a mana base can be "
        "built in one go"
    ),
    "no_summoning_sickness": (
        "Creatures can attack and tap the turn they arrive (CR 302.6), cast "
        "as well as placed"
    ),
}


class PassiveOpponent:
    """Does nothing, ever.

    Not a weak bot - a deliberately inert one. In a sandbox you are testing
    one card at a time, and an opponent that blocks or removes things makes it
    impossible to tell your card's behaviour from the opponent's.
    """

    def choose_action(self, game, player, legal):
        from ..rules.priority import PASS

        return PASS

    def choose_optional(self, game, player, effect):
        return False

    def choose_discard(self, game, player):
        hand = game.player(player).hand
        return hand[-1] if hand else ObjectId(0)

    def order_triggers(self, game, player, triggers):
        return triggers

    def choose_targets(self, game, player, source, candidates):
        return tuple((group[0],) if group else () for group in candidates)

    def declare_attackers(self, game, player, candidates):
        return {}

    def declare_blockers(self, game, player, combat, available):
        return {}


@dataclass(slots=True)
class Sandbox:
    """One hand-driven game, plus the queries the UI needs to describe it."""

    db: CardDatabase
    game: Game = field(init=False)
    provider: OracleAbilities = field(init=False)
    #: Human judgements on parses. Shared with the engine's ability provider,
    #: so a card marked inert here stops working here *and* in a real run.
    verdicts: VerdictStore | None = None
    #: Which player the operator is driving. The other seat is inert.
    hero: PlayerId = PlayerId(0)
    #: Which rules this bench has switched off. Survives ``reset``: clearing
    #: the board is not a reason to start losing to your own draw step again.
    rules: BenchRules = BenchRules()

    def __post_init__(self) -> None:
        if self.verdicts is None:
            self.verdicts = VerdictStore()
        self.provider = OracleAbilities(verdicts=self.verdicts)
        self.db.registry()
        self.reset()

    # -- lifecycle ----------------------------------------------------------

    def reset(self) -> dict:
        """Start over with two empty boards.

        Built directly rather than through ``new_game`` because there are no
        decks: a sandbox position is assembled card by card, and shuffling a
        library the operator never specified would only add noise.
        """
        import random

        game = Game(rng=random.Random(0), log=GameLog(enabled=True))
        game.ability_provider = self.provider
        for index, name in enumerate(("You", "Opponent")):
            game.players.append(Player(id=PlayerId(index), name=name))
        game.turn_order = [PlayerId(0), PlayerId(1)]
        game.active_player = PlayerId(0)
        game.priority_player = PlayerId(0)
        game.turn = 1
        game.phase = Phase.PRECOMBAT_MAIN
        game.step = Step.MAIN
        game.agents[PlayerId(1)] = PassiveOpponent()

        for player in game.players:
            player.life = 40

        self.game = game
        self._apply_rules()
        return self.state()

    # -- what is switched off -----------------------------------------------

    def set_rule(self, name: str, on: bool) -> dict:
        """Switch one bench rule on or off, by name.

        An unknown name is an error rather than a no-op: the caller is a UI
        sending strings, and a typo that silently did nothing looks exactly
        like a switch that does not work.
        """
        return self.set_rules({name: on})

    def set_rules(self, values: dict) -> dict:
        """Set several at once, which is what a panel of checkboxes sends."""
        unknown = [name for name in values if name not in RULE_NAMES]
        if unknown:
            return self.state(error=f"no such bench rule: {unknown[0]!r}")
        was = self.rules
        rules = self.rules
        for name, on in values.items():
            rules = rules.with_field(name, on)
        self.rules = rules
        self._restore(was)
        self._apply_rules()

        changed = [
            f"{name.replace('_', ' ')} is {'off' if on else 'back on'}"
            for name, on in values.items()
            if bool(on) != getattr(was, name)
        ]
        return self.state(message="; ".join(changed) or "nothing changed")

    def _restore(self, was: BenchRules) -> None:
        """Put back the real value of any rule that has just been switched on.

        Separate from ``_apply_rules`` because that one runs before every read
        and must only ever *relax*. Forcing the strict value on every read
        would fight the engine: an extra land drop granted by Exploration sets
        the same field this does, and would be wiped the next time the board
        was drawn - so the one rule the operator switched back on to test would
        be the one rule they could not test.
        """
        if was.unlimited_land_drops and not self.rules.unlimited_land_drops:
            for player in self.game.players:
                player.max_lands = 1
        if was.no_maximum_hand_size and not self.rules.no_maximum_hand_size:
            for player in self.game.players:
                player.max_hand_size = DEFAULT_MAX_HAND_SIZE

    def _apply_rules(self) -> None:
        """Push the current switches onto the game.

        Called after every change and before every read, because the board-side
        ones live on state the engine resets - ``max_lands`` goes back to one at
        the end of every turn, which is why the old sandbox stopped allowing
        extra land drops after turn one - and because summoning sickness has to
        be cleared on permanents that did not exist when the switch was set.

        Only ever relaxes. Putting a rule back is ``_restore``'s job, once, at
        the moment it is switched.
        """
        game = self.game
        game.relaxations = self.rules.relaxations

        for player in game.players:
            if self.rules.unlimited_land_drops:
                player.max_lands = max(player.max_lands, 99)
            if self.rules.no_maximum_hand_size:
                player.max_hand_size = max(player.max_hand_size, 999)

        if self.rules.no_summoning_sickness:
            # One way only: a creature that has lost its sickness cannot be
            # made sick again by switching this back on, because nothing
            # records when it arrived. Switching it on affects permanents
            # from then on, which is the useful direction.
            woken = False
            for obj in game.permanents():
                if obj.summoning_sick:
                    obj.summoning_sick = False
                    woken = True
            # Only when something changed: this runs before every read, and
            # invalidating unconditionally would throw away the whole board's
            # layer computation on every refresh.
            if woken:
                game.invalidate_characteristics()

    # -- card lookup --------------------------------------------------------

    def search(self, text: str, limit: int = 25) -> list[dict]:
        """Find cards by name."""
        if not text or len(text) < 2:
            return []
        out = []
        # ``search_names``, not ``suggest``: the latter is the "did you mean"
        # fuzzy matcher, and by edit distance "Grizzly" is not close enough to
        # "Grizzly Bears" to be offered - which is a type-ahead that fails on
        # the thing you are typing. The name is matched here and the card is a
        # second lookup, because the operator picks from a list and wants the
        # cost and type line before choosing.
        for name in self.db.search_names(text, limit=limit):
            card = self.db.lookup(name)
            if card is None or not card.faces:
                continue
            face = card.faces[0]
            out.append(
                {
                    "name": card.name,
                    "mana_cost": str(face.mana_cost),
                    "type_line": str(face.type_line),
                }
            )
        return out

    def inspect(self, name: str) -> dict:
        """What the parser made of a card, and what it could not read.

        This is the whole reason the sandbox exists. A card that does nothing
        on the battlefield is either a rules bug or an unread ability, and
        those need completely different fixes - so the answer is shown before
        the card is ever played.
        """
        card = self.db.lookup(name)
        if card is None:
            return {"error": f"no card named {name!r}"}

        parsed = parse_card(card)
        faces = []
        for index, face in enumerate(card.faces):
            result = parsed.faces[index] if index < len(parsed.faces) else None
            faces.append(
                {
                    "name": face.name,
                    "mana_cost": str(face.mana_cost),
                    "type_line": str(face.type_line),
                    "oracle_text": face.oracle_text,
                    "abilities": [
                        {
                            "kind": ability.kind.name,
                            "text": ability.text or str(ability),
                            # What the engine will actually do, rebuilt from
                            # the opcodes. Read this against the oracle text
                            # above it: that comparison is the review.
                            "understood": explain_ability(ability),
                            "unparsed": ability.unparsed,
                            "keyword": ability.keyword,
                            "effects": [
                                node.kind.name
                                for effect in ability.effects
                                for node in effect.walk()
                            ],
                        }
                        for ability in (result.abilities if result else ())
                    ],
                    "failures": [
                        {
                            "reason": failure.reason,
                            "stopped_at": failure.stopped_at,
                            "remaining": " ".join(failure.remaining[:12]),
                            "text": failure.text,
                        }
                        for failure in (result.failures if result else ())
                    ],
                }
            )
        verdict, current = self.verdicts.status(card.name, parsed)
        review = self.verdicts.review_for(card.name)
        return {
            "name": card.name,
            "fully_parsed": parsed.fully_parsed,
            "faces": faces,
            "verdict": verdict.value,
            # False when the grammar has changed this card since the verdict
            # was given, so an old approval never silently vouches for new
            # behaviour.
            "verdict_current": current,
            "note": review.note if review else "",
            "suppressed": self.verdicts.is_suppressed(card.name),
        }

    # -- reviewing ----------------------------------------------------------

    def judge(self, name: str, verdict: str, note: str = "") -> dict:
        """Record what a person made of this card's parse.

        ``inert`` is the one that changes behaviour: the card's abilities stop
        being served to the engine, here and in a real run, exactly as if the
        grammar had failed. A parse that is confidently wrong does more damage
        to a simulation than one that is visibly absent.
        """
        card = self.db.lookup(name)
        if card is None:
            return {"error": f"no card named {name!r}"}
        try:
            choice = Verdict(verdict)
        except ValueError:
            return {"error": f"unknown verdict {verdict!r}"}

        if choice is Verdict.UNREVIEWED:
            self.verdicts.clear(card.name)
        else:
            self.verdicts.record(card.name, choice, parse_card(card), note)

        # The provider caches abilities per card, so a verdict that suppresses
        # one has to evict it or the old abilities keep being served.
        self.provider.clear()
        self.game.invalidate_characteristics()
        return self.inspect(card.name)

    def review_summary(self) -> dict:
        """How much of the pool a person has actually vouched for."""
        return self.verdicts.counts()

    # -- building a position ------------------------------------------------

    #: A library is stocked in bulk, so the cap is high enough for a real deck
    #: and low enough that a stray keystroke cannot build a hundred thousand
    #: objects and take the session with it.
    MAX_PUT = 250

    def put(
        self, name: str, zone: str = "battlefield", player: int = 0, count: int = 1
    ) -> dict:
        """Place a card into a zone, ready to be used.

        A permanent placed on the battlefield is not summoning-sick: the
        operator is setting up a position, not playing a turn, and having to
        pass three turns before a creature can attack makes the instrument
        tedious without making it more truthful.

        ``count`` places several copies at once, which is how a library gets
        stocked. Drawing from an empty library is harmless on the bench, but
        harmless is not the same as useful: a card that says "draw three" has
        nothing to show unless there is something to draw.
        """
        card = self.db.lookup(name)
        if card is None:
            return {"error": f"no card named {name!r}"}
        target = PLACEABLE.get(zone)
        if target is None:
            return {"error": f"cannot place into {zone!r}"}
        count = max(1, min(int(count), self.MAX_PUT))

        for _ in range(count):
            self._put_one(card, target, PlayerId(player))
        self.game.invalidate_characteristics()
        self._apply_rules()
        copies = "" if count == 1 else f" x{count}"
        return self.state(message=f"put {card.name}{copies} into {zone}")

    def _put_one(self, card, target: Zone, player: PlayerId) -> None:
        obj = self.game.create_object(card, player, target)
        if target is Zone.BATTLEFIELD:
            obj.summoning_sick = False
            obj.entered_battlefield_turn = self.game.turn
            # Placing a permanent skips move_object, and with it CR 614.1c.
            # Without this a tapland placed here enters untapped while the
            # same land *played* enters tapped - and an instrument that
            # disagrees with the engine it is meant to be testing is worse
            # than no instrument.
            from ..rules.events import Event, EventKind
            from ..rules.replacement import apply_self_entry_replacements

            apply_self_entry_replacements(self.game, obj)
            # Placing a permanent skips move_object, so nothing announced that
            # it entered and no enters-the-battlefield trigger fired. An
            # instrument that silently drops ETB triggers would report a card
            # as doing nothing when the engine would have worked.
            self.game.emit(
                Event(
                    EventKind.ENTERS_BATTLEFIELD,
                    object_id=obj.id,
                    player=obj.controller,
                )
            )

    def give_mana(self, amount: int = 10, player: int = 0) -> dict:
        """Fill a pool, so casting can be tested without building a mana base.

        One of each colour plus colourless, which covers every cost without
        the operator having to think about it. With ``mana_pools_persist`` on,
        which is the default, it stays there across steps; switch that rule
        back on and CR 500.4 empties it at the end of the step, as in a game.
        """
        from ..rules.enums import Color
        from ..rules.mana import ManaKind

        pool = self.game.player(PlayerId(player)).mana_pool
        for color in (
            Color.NONE,
            Color.WHITE,
            Color.BLUE,
            Color.BLACK,
            Color.RED,
            Color.GREEN,
        ):
            pool.add(ManaKind(color), amount)
        return self.state(message=f"added {amount} of each colour")

    def set_life(self, life: int, player: int = 0) -> dict:
        self.game.player(PlayerId(player)).life = life
        return self.state(message=f"life set to {life}")

    # -- playing ------------------------------------------------------------

    def legal(self, player: int = 0) -> list[dict]:
        """Everything this player could do right now, as the engine sees it.

        Read from ``legality.legal_actions`` rather than assembled here, so
        the sandbox cannot offer something the engine would refuse - and if it
        offers nothing, that is the answer to "why can't I cast this?".
        """
        from ..rules.legality import legal_actions

        self._apply_rules()
        out = []
        for index, action in enumerate(legal_actions(self.game, PlayerId(player))):
            if action.kind is ActionKind.PASS:
                continue
            obj = self.game.objects.get(action.source)
            out.append(
                {
                    "index": index,
                    "kind": action.kind.name,
                    "source": int(action.source),
                    "name": self._name(obj) if obj else "",
                    "ability_index": action.ability_index,
                    "description": self._describe_action(action, obj),
                }
            )
        return out

    def targets_for(self, index: int, player: int = 0) -> list[dict]:
        """What an action could legally target, so the operator can choose.

        Grouped per targeting effect, in announcement order (CR 601.2c), which
        is the order ``perform`` expects them back in. Without this the sandbox
        would pick for you, and picking the first legal candidate means a
        Lightning Bolt kills your own creature - which is a real thing that
        happened the first time this was run.
        """
        from ..rules.legality import legal_actions

        self._apply_rules()
        actions = legal_actions(self.game, PlayerId(player))
        if not 0 <= index < len(actions):
            return []
        action = actions[index]
        obj = self.game.objects.get(action.source)
        if obj is None:
            return []

        groups = []
        for effect in self._targeting_effects(obj, action):
            # The engine's own candidate builder, not a second implementation.
            # A sandbox that computed legal targets differently from the rules
            # would be an instrument that disagrees with the thing it measures.
            candidates = _candidates_for(self.game, obj, effect, PlayerId(player))
            groups.append(
                {
                    "description": effect.text or effect.kind.name.lower(),
                    "optional": bool(effect.targets and effect.targets.up_to),
                    "candidates": [
                        self._candidate(target) for target in candidates
                    ],
                }
            )
        return groups

    def _candidate(self, target: int) -> dict:
        """One choosable target, object or player (CR 115.4)."""
        if is_player_target(target):
            who = self.game.player(target_player(target))
            return {
                "id": int(target),
                "name": f"{who.name} (player)",
                "controller": int(who.id),
                "is_player": True,
            }
        obj = self.game.objects.get(ObjectId(target))
        return {
            "id": int(target),
            "name": self._name(obj),
            "controller": int(obj.controller) if obj else -1,
            "is_player": False,
        }

    def _targeting_effects(self, obj: GameObject, action: Action) -> list:
        from ..rules.abilities import AbilityKind

        chars = self.game.characteristics(obj)
        if action.kind is ActionKind.ACTIVATE_ABILITY or (
            action.kind is ActionKind.ACTIVATE_MANA_ABILITY
        ):
            if not 0 <= action.ability_index < len(chars.abilities):
                return []
            abilities = [chars.abilities[action.ability_index]]
        else:
            abilities = [a for a in chars.abilities if a.kind is AbilityKind.SPELL]

        return [
            node
            for ability in abilities
            for effect in ability.effects
            for node in effect.walk()
            # Player-only targets ("target player draws two cards") carry no
            # object filter; the engine's candidate builder handles both.
            if node.is_targeted and (node.targets is not None or node.players is not None)
        ]

    def perform(
        self, index: int, player: int = 0, targets: list | None = None
    ) -> dict:
        """Take one of the legal actions, by its index in ``legal``.

        ``targets`` is one list of object ids per targeting effect, in the
        order ``targets_for`` returned them. Left out, the engine chooses -
        which is fine for an ability with one legal target and wrong for
        anything else.
        """
        from ..rules.legality import legal_actions
        from ..rules.priority import _perform

        actions = legal_actions(self.game, PlayerId(player))
        if not 0 <= index < len(actions):
            return self.state(error="no such action")

        action = actions[index]
        if targets:
            from dataclasses import replace

            action = replace(
                action,
                targets=tuple(tuple(int(oid) for oid in group) for group in targets),
            )
        before = len(self.game.log.entries)
        ok = _perform(self.game, PlayerId(player), action)
        if not ok:
            reason = self._last_log(before, kinds=("illegal", "rewind"))
            return self.state(error=reason or "the engine refused that action")
        return self.state(message=f"did {action}")

    def resolve_top(self) -> dict:
        """Resolve the top of the stack, as passing priority would."""
        from ..rules.priority import settle
        from ..rules.stack import resolve_top

        if not self.game.stack:
            settle(self.game)
            if not self.game.stack:
                return self.state(message="nothing on the stack")
        resolve_top(self.game)
        settle(self.game)
        return self.state(message="resolved the top of the stack")

    def settle(self) -> dict:
        """Run state-based actions and put waiting triggers on the stack."""
        from ..rules.priority import settle

        settle(self.game)
        return self.state(message="state-based actions and triggers settled")

    def advance(self) -> dict:
        """Move to the next step, running its turn-based actions.

        Steps rather than whole turns, because the interesting bugs live in
        the boundaries - an upkeep trigger that fires twice, a creature that
        untaps when it should not.
        """
        from ..rules.turn import TURN_SEQUENCE, _end_of_step_actions, take_turn

        sequence = list(TURN_SEQUENCE)
        current = (self.game.phase, self.game.step)
        try:
            position = sequence.index(current)
        except ValueError:
            position = len(sequence) - 1

        # Leaving a step ends it, and some things happen only then: combat is
        # over as the end of combat step ends (CR 511.3), not as it begins.
        _end_of_step_actions(self.game, self.game.step)

        if position + 1 >= len(sequence):
            take_turn(self.game)
            self.game.active_player = self.game.next_player(self.game.active_player)
            return self.state(message=f"turn {self.game.turn}")

        phase, step = sequence[position + 1]
        self.game.phase = phase
        self.game.step = step
        from ..rules.priority import settle
        from ..rules.turn import _turn_based_actions, TurnOptions

        _turn_based_actions(self.game, step, TurnOptions())
        settle(self.game)
        return self.state(message=f"{phase.name.lower()} / {step.name.lower()}")

    def next_turn(self) -> dict:
        """Run a whole turn for the active player."""
        from ..rules.turn import take_turn

        take_turn(self.game)
        self.game.active_player = self.game.next_player(self.game.active_player)
        self.game.phase = Phase.PRECOMBAT_MAIN
        self.game.step = Step.MAIN
        return self.state(message=f"turn {self.game.turn} ended")

    # -- describing ---------------------------------------------------------

    def state(self, *, message: str = "", error: str = "") -> dict:
        """The whole visible position, as plain data."""
        self._apply_rules()
        game = self.game
        return {
            "turn": game.turn,
            "phase": game.phase.name,
            "step": game.step.name,
            "active_player": int(game.active_player),
            "message": message,
            "error": error,
            # A bench with ``players_cannot_lose`` on can still be ended by a
            # card that says a player wins, and a board that has stopped
            # responding for that reason has to say so rather than look broken.
            "game_over": game.game_over,
            "winners": [int(p) for p in game.winners],
            "rules": self.rules.as_dict(),
            "rule_descriptions": RULE_DESCRIPTIONS,
            "players": [self._player(player) for player in game.players],
            "stack": [
                self._object(game.objects[object_id])
                for object_id in game.stack
                if object_id in game.objects
            ],
            # The last 200 entries *worth reading*. Windowing the raw log
            # instead showed three lines of use and 197 events: a board that
            # has run a few turns produces tens of thousands of them, and they
            # say the same thing as the actions that caused them.
            "log": self._log_tail(),
        }

    #: How much of the log the board carries back. Enough to cover the last
    #: few turns, short enough that the payload stays small on every refresh.
    LOG_TAIL = 200

    def _log_tail(self) -> list[dict]:
        entries = [e for e in self.game.log.entries if e.kind != "event"]
        return [
            {
                "kind": entry.kind,
                "text": entry.text,
                "turn": entry.turn,
                "step": entry.step.name if entry.step is not None else "",
                "depth": entry.depth,
            }
            for entry in entries[-self.LOG_TAIL :]
        ]

    def _player(self, player: Player) -> dict:
        game = self.game
        return {
            "id": int(player.id),
            "name": player.name,
            "life": player.life,
            "mana": player.mana_pool.total,
            "poison": player.poison,
            "library": len(player.library),
            # With the loss rules switched off a player sits at -7 life
            # indefinitely, which is the point; the board still has to show it,
            # or an effect that is working looks like an effect that is not.
            "has_lost": player.has_lost,
            "loss_reason": player.loss_reason.name if player.loss_reason else "",
            "battlefield": [
                self._object(game.objects[oid])
                for oid in game.battlefield
                if oid in game.objects
                and game.objects[oid].controller == player.id
            ],
            "hand": [
                self._object(game.objects[oid])
                for oid in player.hand
                if oid in game.objects
            ],
            "graveyard": [
                self._object(game.objects[oid])
                for oid in player.graveyard
                if oid in game.objects
            ],
        }

    def _object(self, obj: GameObject) -> dict:
        chars = self.game.characteristics(obj)
        return {
            "id": int(obj.id),
            "name": self._name(obj),
            "type_line": str(chars.type_line),
            "power": chars.power,
            "toughness": chars.toughness,
            "tapped": obj.tapped,
            "damage": obj.damage,
            "counters": dict(obj.counters),
            "summoning_sick": obj.summoning_sick,
            "keywords": sorted(k for k in chars.keywords if k),
            "abilities": [
                {"text": a.text or str(a), "unparsed": a.unparsed}
                for a in chars.abilities
            ],
            "unreadable": any(a.unparsed for a in chars.abilities),
        }

    def _name(self, obj: GameObject | None) -> str:
        if obj is None:
            return ""
        try:
            name = self.game.characteristics(obj).name
        except Exception:  # noqa: BLE001 - a gone object still needs a label
            name = ""
        if name:
            return name
        card = getattr(obj, "card", None)
        return getattr(card, "name", "") or f"#{obj.id}"

    def _describe_action(self, action: Action, obj: GameObject | None) -> str:
        name = self._name(obj)
        if action.kind is ActionKind.PLAY_LAND:
            return f"play {name}"
        if action.kind is ActionKind.CAST_SPELL:
            return f"cast {name}"
        if action.kind is ActionKind.SPECIAL:
            return f"special action on {name}"
        if obj is not None and 0 <= action.ability_index < len(
            self.game.characteristics(obj).abilities
        ):
            ability = self.game.characteristics(obj).abilities[action.ability_index]
            return f"{name}: {ability.text or ability.kind.name}"
        return f"{action.kind.name.lower()} {name}"

    def _last_log(self, since: int, *, kinds: tuple[str, ...]) -> str:
        for entry in reversed(self.game.log.entries[since:]):
            if entry.kind in kinds:
                return entry.text
        return ""
