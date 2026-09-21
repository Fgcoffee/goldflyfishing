"""Test harness for rules scenarios.

Rules tests need three things the engine deliberately does not provide yet:
a way to hand-write a card's abilities (the parser does not exist), a way to
set up a board directly (without playing eleven turns to get there), and
readable assertions.

Cards are referred to by their real names and pulled from the real card
database, so a test that says Grizzly Bears is a 2/2 is checking against the
actual printed card rather than a fixture someone typed wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mtgfish.data.decks import parse_decklist
from mtgfish.rules.abilities import Ability, AbilityKind
from mtgfish.rules.characteristics import Characteristics
from mtgfish.rules.cr103_setup import new_game
from mtgfish.rules.enums import Zone
from mtgfish.rules.game import Game
from mtgfish.rules.gameobject import GameObject, ObjectKind
from mtgfish.rules.ids import PlayerId


def keyword(name: str) -> Ability:
    """A bare keyword ability, e.g. ``keyword("Flying")``.

    Keywords are represented as abilities carrying a keyword name rather than
    as flags, so that granting and removing them goes through the layer system
    like everything else.
    """
    return Ability(AbilityKind.STATIC, keyword=name, text=name)


class ScriptedAbilities:
    """An AbilityProvider backed by a name -> abilities table.

    Unlisted cards get no abilities at all rather than an unparsed placeholder,
    so a test board contains exactly what the test asked for and nothing else.
    """

    def __init__(self, scripts: dict[str, tuple[Ability, ...]] | None = None) -> None:
        self.scripts: dict[str, tuple[Ability, ...]] = dict(scripts or {})
        #: Bumped whenever the table changes. The engine caches a card's
        #: copiable values and keys the cache on this, because a test that
        #: rewrites a card's abilities mid-game is doing something no real
        #: game does and must still see the new ones.
        self.generation = 0

    def add(self, name: str, *abilities: Ability) -> ScriptedAbilities:
        self.scripts[name] = tuple(abilities)
        self.generation += 1
        return self

    def abilities_for(self, card: object, face_index: int) -> tuple[Ability, ...]:
        try:
            name = card.faces[face_index].name  # type: ignore[attr-defined]
        except (AttributeError, IndexError):
            return ()
        return self.scripts.get(name, ())


class FixedAgent:
    """An agent that makes exactly the declarations a test tells it to.

    Combat legality has to be tested against declarations the engine did not
    choose - including illegal ones, to prove they are rejected - so the test
    supplies them directly rather than going through a bot.
    """

    def __init__(
        self,
        *,
        attackers: dict | None = None,
        blockers: dict | None = None,
        blocker_order: dict | None = None,
    ) -> None:
        self.attackers = attackers or {}
        self.blockers = blockers or {}
        self.blocker_order = blocker_order or {}

    def choose_action(self, game, player, legal):
        from mtgfish.rules.cr117_priority import PASS

        return PASS

    def choose_optional(self, game, player, effect):
        return True

    def choose_discard(self, game, player):
        hand = game.player(player).hand
        return hand[-1] if hand else 0

    def order_triggers(self, game, player, triggers):
        return triggers

    def declare_attackers(self, game, player, candidates):
        return self.attackers

    def declare_blockers(self, game, player, combat, available):
        return self.blockers

    def order_blockers(self, game, player, attacker, blockers):
        return self.blocker_order.get(attacker, blockers)


@dataclass
class Board:
    """A game set up directly, for testing rules rather than gameplay."""

    game: Game
    db: object
    scripts: ScriptedAbilities = field(default_factory=ScriptedAbilities)

    # -- placing cards ------------------------------------------------------

    def play(self, name: str, controller: int = 0, **status) -> GameObject:
        """Put a named card onto the battlefield under a player's control."""
        return self._place(name, controller, Zone.BATTLEFIELD, **status)

    def hand(self, name: str, controller: int = 0) -> GameObject:
        return self._place(name, controller, Zone.HAND)

    def graveyard(self, name: str, controller: int = 0) -> GameObject:
        return self._place(name, controller, Zone.GRAVEYARD)

    def _place(self, name: str, controller: int, zone: Zone, **status) -> GameObject:
        card = self.db.lookup(name)
        if card is None:
            raise LookupError(f"no such card: {name!r}")
        obj = self.game.create_object(card, PlayerId(controller), zone)
        if zone is Zone.BATTLEFIELD:
            obj.summoning_sick = False
            obj.entered_battlefield_turn = self.game.turn
        for key, value in status.items():
            setattr(obj, key, value)
        self.game.invalidate_characteristics()
        return obj

    def token(self, card_name: str, controller: int = 0) -> GameObject:
        card = self.db.lookup(card_name)
        obj = self.game.create_object(
            card, PlayerId(controller), Zone.BATTLEFIELD, kind=ObjectKind.TOKEN
        )
        obj.summoning_sick = False
        self.game.invalidate_characteristics()
        return obj

    # -- inspecting ---------------------------------------------------------

    def chars(self, obj: GameObject) -> Characteristics:
        return self.game.characteristics(obj)

    def pt(self, obj: GameObject) -> tuple[int | None, int | None]:
        """Current power and toughness, after the whole layer system."""
        chars = self.game.characteristics(obj)
        return chars.power, chars.toughness

    def types(self, obj: GameObject) -> str:
        return str(self.game.characteristics(obj).type_line)

    def keywords(self, obj: GameObject) -> set[str]:
        return {k for k in self.game.characteristics(obj).keywords if k}

    def taps_for(self, obj: GameObject) -> set[str]:
        """Which mana colors this permanent's mana abilities can produce.

        Reads the abilities the layer system produced, which is the only way to
        see intrinsic land mana appearing and disappearing with the land type.
        """
        from mtgfish.rules.effects import EffectKind
        from mtgfish.rules.enums import COLOR_LETTERS

        out: set[str] = set()
        for ability in self.game.characteristics(obj).abilities:
            if not ability.is_mana_ability:
                continue
            for effect in ability.effects:
                if effect.kind is EffectKind.ADD_MANA:
                    for color in effect.colors:
                        out.add(COLOR_LETTERS[color])
        return out

    def refresh(self) -> None:
        self.game.invalidate_characteristics()

    # -- driving the game ---------------------------------------------------

    def sba(self) -> bool:
        """Run state-based actions once, as the priority loop would."""
        from mtgfish.rules.cr704_sba import check_state_based_actions

        return check_state_based_actions(self.game)

    def settle(self) -> None:
        """State-based actions plus putting triggers on the stack."""
        from mtgfish.rules.cr117_priority import settle

        settle(self.game)

    def resolve_stack(self) -> None:
        """Resolve everything on the stack, top first."""
        from mtgfish.rules.cr608_stack import resolve_top

        while self.game.stack:
            resolve_top(self.game)
            self.sba()

    def alive(self, controller: int = 0) -> list[str]:
        """Names of the permanents a player still controls, for readable asserts."""
        return sorted(
            self.game.characteristics(o).name for o in self.game.permanents(PlayerId(controller))
        )

    def in_graveyard(self, controller: int = 0) -> list[str]:
        player = self.game.player(PlayerId(controller))
        return sorted(
            self.game.printed_characteristics(self.game.objects[o]).name
            for o in player.graveyard
        )


def make_board(card_db, scripts: ScriptedAbilities | None = None, players: int = 2) -> Board:
    """A game with empty battlefields, ready for cards to be placed."""
    scripts = scripts or ScriptedAbilities()
    decks = [
        parse_decklist(
            "// Commander\n1 Kenrith, the Returned King\n// Deck\n99 Forest\n",
            card_db,
            name=f"P{i}",
        )
        for i in range(players)
    ]
    game = new_game(decks, seed=1, ability_provider=scripts, randomize_turn_order=False)
    game.turn = 1
    return Board(game=game, db=card_db, scripts=scripts)
