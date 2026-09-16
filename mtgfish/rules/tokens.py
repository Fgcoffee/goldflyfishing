"""Tokens (CR 111) and emblems (CR 114).

Both are objects that exist without a card behind them, and the engine
addresses characteristics through a card definition, so both need a synthetic
one built on the fly.

CR 111.4 is the rule that is easy to miss: a token's name defaults to its
subtypes plus the word "Token" if the effect creating it does not say
otherwise. That is why "create a 1/1 white Soldier creature token" makes a
token *named* "Soldier Token", which matters the moment anything counts tokens
by name.

CR 111.5 is the other one: if a rule or effect says a permanent with one of the
token's characteristics can't enter the battlefield, the token is simply never
created - not created and then removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .abilities import Ability, AbilityKind
from .effects import TokenSpec
from .enums import CardType, Color, Zone
from .events import Event, EventKind
from .ids import NO_OBJECT, ObjectId, PlayerId
from .mana import ZERO_COST, ManaCost
from .typeline import TypeLine

if TYPE_CHECKING:
    from .game import Game
    from .gameobject import GameObject


@dataclass(frozen=True, slots=True)
class _SyntheticFace:
    """A card face conjured for a token or emblem.

    Shaped to match ``data.cards.FaceDef`` closely enough for
    ``characteristics.from_face``, without importing the data layer into the
    rules engine.
    """

    name: str
    mana_cost: ManaCost
    has_mana_cost: bool
    type_line: TypeLine
    oracle_text: str
    power: str | None = None
    toughness: str | None = None
    loyalty: str | None = None
    defense: str | None = None
    colors: Color = Color.NONE
    color_indicator: Color | None = None
    keywords: tuple[str, ...] = ()
    produced_mana: tuple[str, ...] = ()

    @property
    def power_value(self) -> int | None:
        return _as_int(self.power)

    @property
    def toughness_value(self) -> int | None:
        return _as_int(self.toughness)

    @property
    def loyalty_value(self) -> int | None:
        return _as_int(self.loyalty)

    @property
    def defense_value(self) -> int | None:
        return _as_int(self.defense)


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class SyntheticCard:
    """A stand-in card definition for an object that has no card."""

    name: str
    faces: tuple[_SyntheticFace, ...]
    abilities: tuple[Ability, ...] = ()
    mana_value: int = 0
    color_identity: Color = Color.NONE
    commander_legal: bool = False
    oracle_id: str = ""

    def __str__(self) -> str:
        return self.name


def token_name(spec: TokenSpec) -> str:
    """CR 111.4: subtypes plus "Token" unless the effect named it."""
    if spec.name:
        return spec.name
    if spec.subtypes:
        return " ".join(spec.subtypes) + " Token"
    return "Token"


def build_token_card(game: Game, spec: TokenSpec, source: ObjectId) -> SyntheticCard:
    """Turn a TokenSpec into something the characteristics layer can read."""
    from .values import evaluate

    power = evaluate(game, spec.power, source=source)
    toughness = evaluate(game, spec.toughness, source=source)
    name = token_name(spec)

    abilities = tuple(spec.abilities) + tuple(
        Ability(AbilityKind.STATIC, keyword=word, text=word) for word in spec.keywords
    )

    face = _SyntheticFace(
        name=name,
        mana_cost=ZERO_COST,
        has_mana_cost=False,
        type_line=TypeLine(types=spec.types, subtypes=tuple(spec.subtypes)),
        oracle_text="",
        power=str(power) if spec.types & CardType.CREATURE else None,
        toughness=str(toughness) if spec.types & CardType.CREATURE else None,
        colors=spec.colors,
        keywords=spec.keywords,
    )
    return SyntheticCard(name=name, faces=(face,), abilities=abilities)


def _replaced_count(
    game: Game, controller: PlayerId, count: int, source: ObjectId
) -> int:
    """How many tokens are actually created, after replacement (CR 614.1c).

    Doubling Season, Parallel Lives and Anointed Procession all replace the
    *token-creation event* - "if an effect would create one or more tokens,
    it creates twice that many instead" - so the count has to be run past the
    replacement system before any token exists. The engine parsed these into
    MODIFY_TOKENS replacements and registered them correctly; nothing ever
    asked, because the only TOKEN_CREATED event was emitted once per token
    *after* it was made, when changing the number no longer means anything.

    Two doublers give four times as many rather than three, because each
    replacement applies once (CR 614.5) and each doubles what the last one
    left.
    """
    prospective = game.replace(
        Event(
            EventKind.TOKEN_CREATED,
            player=controller,
            source=source,
            amount=count,
        )
    )
    # Replaced out of existence entirely: the tokens are simply never created.
    if prospective is None:
        return 0
    return prospective.amount


def create_tokens(
    game: Game,
    spec: TokenSpec,
    controller: PlayerId,
    count: int = 1,
    *,
    source: ObjectId = NO_OBJECT,
) -> list[GameObject]:
    """Create tokens on the battlefield (CR 111.1).

    Returns the tokens actually created, which may be fewer than asked for -
    CR 111.5 means a prohibition on entering stops the token existing at all,
    and CR 101.3 means the rest of the instruction simply carries on.
    """
    from .gameobject import ObjectKind
    from .restrictions import Act, prohibited

    if count <= 0:
        return []

    # Replacements apply to the number asked for, before any token exists.
    # A doubler raises it; "creates no tokens instead" takes it to zero, and
    # then there is nothing left to build a card for.
    count = _replaced_count(game, controller, count, source)
    if count <= 0:
        return []

    card = build_token_card(game, spec, source)
    created: list[GameObject] = []

    for _ in range(count):
        obj = game.create_object(
            card, controller, Zone.BATTLEFIELD, kind=ObjectKind.TOKEN
        )
        obj.tapped = spec.enters_tapped
        obj.entered_battlefield_turn = game.turn
        obj.summoning_sick = True
        game.invalidate_characteristics()

        # CR 111.5: check *after* building it, because the prohibition is
        # phrased in terms of the token's characteristics.
        blocked = prohibited(game, Act.ENTER_BATTLEFIELD, obj=obj)
        if blocked is not None:
            game.log.record(
                game, f"{card.name} is not created ({blocked})", kind="restriction"
            )
            game._remove_from_zone(obj)
            game.objects.pop(obj.id, None)
            game.invalidate_characteristics()
            continue

        created.append(obj)
        game.emit(
            Event(EventKind.TOKEN_CREATED, object_id=obj.id, player=controller, source=source)
        )
        game.emit(
            Event(EventKind.ENTERS_BATTLEFIELD, object_id=obj.id, player=controller)
        )

    return created


# ---------------------------------------------------------------------------
# Emblems (CR 114)
# ---------------------------------------------------------------------------


def create_emblem(
    game: Game, controller: PlayerId, abilities: tuple[Ability, ...], *, text: str = ""
) -> GameObject:
    """Put an emblem into the command zone (CR 114.2).

    CR 114.3: an emblem has no characteristics at all beyond its abilities - no
    types, no mana cost, no color, usually no name. CR 114.4: its abilities
    function from the command zone, which is why they are marked as doing so
    rather than defaulting to the battlefield.
    """
    from .gameobject import ObjectKind

    command_zone_abilities = tuple(
        Ability(
            ability.kind,
            effects=ability.effects,
            text=ability.text,
            cost=ability.cost,
            trigger=ability.trigger,
            static_condition=ability.static_condition,
            functions_in=frozenset({Zone.COMMAND}),
            keyword=ability.keyword,
        )
        for ability in abilities
    )

    face = _SyntheticFace(
        name="",
        mana_cost=ZERO_COST,
        has_mana_cost=False,
        type_line=TypeLine(types=CardType.EMBLEM),
        oracle_text=text,
    )
    card = SyntheticCard(name="Emblem", faces=(face,), abilities=command_zone_abilities)

    obj = game.create_object(card, controller, Zone.COMMAND, kind=ObjectKind.EMBLEM)
    game.invalidate_characteristics()
    game.log.record(game, f"{game.player(controller).name} gets an emblem", kind="emblem")
    return obj
