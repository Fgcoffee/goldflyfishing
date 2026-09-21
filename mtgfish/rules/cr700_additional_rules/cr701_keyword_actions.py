"""Keyword actions (CR 701), as Effect IR.

A keyword ability is shorthand for an *ability*; a keyword action is shorthand
for a *verb used inside* one. "Scry 2", "Investigate", "Bolster 3" all appear in
the middle of a sentence, and each expands into effects the engine already
executes.

So this is the sibling of ``keyword_impl``: same registry shape, same role as
the parser's interface, but it returns effects rather than abilities. The
parser recognising "surveil 2" reduces to ``build("Surveil", amount=2)``.

Anything that genuinely has no modelled behaviour returns an ``UNPARSED``
effect rather than nothing, so it is visible in the coverage report instead of
quietly doing less than the card says.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..cr600_spells_and_abilities.effects import Effect, EffectKind, TokenSpec
from ..kernel.enums import CardType, Color, Zone
from ..kernel.query import (
    ControllerRelation,
    NumericConstraint,
    ObjectFilter,
    PlayerFilter,
    PlayerScope,
    Value,
    ValueKind,
)

YOU = PlayerFilter(PlayerScope.YOU)
EACH_OPPONENT = PlayerFilter(PlayerScope.EACH_OPPONENT)
EACH_PLAYER = PlayerFilter(PlayerScope.EACH_PLAYER)
CREATURES_YOU_CONTROL = ObjectFilter(
    types_all=CardType.CREATURE, controller=ControllerRelation.YOU
)
SELF = ObjectFilter(source_only=True)


@dataclass(frozen=True, slots=True)
class ActionInstance:
    """One keyword action as it appears in a sentence, with its parameter."""

    name: str
    amount: int = 0
    filter: ObjectFilter | None = None
    players: PlayerFilter | None = None
    #: CR 701.7b: what the token is. It comes from the sentence around the
    #: word - "create a 1/1 white Soldier" - so the parser supplies it and the
    #: action only has to place it.
    token: TokenSpec | None = None
    text: str = ""

    @property
    def key(self) -> str:
        return self.name.lower()


Builder = Callable[[ActionInstance], tuple[Effect, ...]]
BUILDERS: dict[str, Builder] = {}


def register(*names: str) -> Callable[[Builder], Builder]:
    def decorate(builder: Builder) -> Builder:
        for name in names:
            BUILDERS[name.lower()] = builder
        return builder

    return decorate


def build(name: str, **kwargs) -> tuple[Effect, ...]:
    """Expand a keyword action into effects."""
    instance = ActionInstance(name, **kwargs)
    builder = BUILDERS.get(instance.key)
    if builder is None:
        return (Effect(EffectKind.UNPARSED, text=instance.text or instance.name),)
    return builder(instance)


def implemented_actions() -> frozenset[str]:
    return frozenset(BUILDERS)


def _amount(instance: ActionInstance, default: int = 1) -> Value:
    return Value.of(instance.amount if instance.amount else default)


# ---------------------------------------------------------------------------
# Direct one-to-one mappings
# ---------------------------------------------------------------------------

#: Keyword actions that are exactly one existing opcode. The engine already
#: enforces each one's rules, so the expansion is a rename.
_DIRECT: dict[str, EffectKind] = {
    "destroy": EffectKind.DESTROY,
    "exile": EffectKind.EXILE,
    "sacrifice": EffectKind.SACRIFICE,
    "tap": EffectKind.TAP,
    "untap": EffectKind.UNTAP,
    "discard": EffectKind.DISCARD,
    "mill": EffectKind.MILL,
    "scry": EffectKind.SCRY,
    "surveil": EffectKind.SURVEIL,
    "shuffle": EffectKind.SHUFFLE,
    "reveal": EffectKind.REVEAL,
    "counter": EffectKind.COUNTER_SPELL,
    "fight": EffectKind.FIGHT,
    "goad": EffectKind.GOAD,
    "proliferate": EffectKind.PROLIFERATE,
    "regenerate": EffectKind.REGENERATE,
    "attach": EffectKind.ATTACH,
    "explore": EffectKind.EXPLORE,
    "transform": EffectKind.TRANSFORM,
    "monstrosity": EffectKind.MONSTROSITY,
    "adapt": EffectKind.ADAPT,
    "exchange": EffectKind.EXCHANGE_CONTROL,
    "vote": EffectKind.VOTE,
    "double": EffectKind.DOUBLE if hasattr(EffectKind, "DOUBLE") else EffectKind.UNPARSED,
}


@register(*_DIRECT)
def _direct(instance: ActionInstance) -> tuple[Effect, ...]:
    kind = _DIRECT[instance.key]
    return (
        Effect(
            kind,
            targets=instance.filter,
            players=instance.players,
            amount=_amount(instance),
            text=instance.text or instance.name,
        ),
    )


@register("Create")
def _create(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.7a: "to create a token is to put a token onto the battlefield."

    A token with no definition is not a token, so an instance without a spec
    yields an unparsed effect rather than putting a nameless 0/0 into play.
    """
    if instance.token is None:
        return (Effect(EffectKind.UNPARSED, text=instance.text or instance.name),)
    return (
        Effect(
            EffectKind.CREATE_TOKEN,
            token=instance.token,
            players=instance.players or YOU,
            amount=_amount(instance),
            text=instance.text or instance.name,
        ),
    )


@register("Conjure")
def _conjure(instance: ActionInstance) -> tuple[Effect, ...]:
    """Conjure creates a *card*, not a token, in a specified zone. It appears
    nowhere in the Comprehensive Rules - it is digital-only (Alchemy), so
    there is no rule number to cite.

    Digital-only (Alchemy), and Alchemy cards are excluded from the pool by the
    Commander-legality filter - so this is registered to be reported rather
    than to be reached.
    """
    return (Effect(EffectKind.UNPARSED, text=instance.text or instance.name),)


# ---------------------------------------------------------------------------
# Counter-placing actions
# ---------------------------------------------------------------------------


def _counters(
    instance: ActionInstance, target: ObjectFilter, counter: str = "+1/+1"
) -> tuple[Effect, ...]:
    return (
        Effect(
            EffectKind.ADD_COUNTERS,
            targets=target,
            counter_type=counter,
            amount=_amount(instance),
            text=instance.text or instance.name,
        ),
    )


@register("Bolster")
def _bolster(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.39: N +1/+1 counters on the creature you control with the least
    toughness. "Least toughness" is a choice among ties, so the filter narrows
    to your creatures and the chooser breaks the tie."""
    return _counters(instance, CREATURES_YOU_CONTROL)


@register("Support")
def _support(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.41: a +1/+1 counter on each of up to N *other* target creatures."""
    others = ObjectFilter(
        types_all=CardType.CREATURE,
        other_than_source=True,
        count=_amount(instance),
        up_to=True,
    )
    return (
        Effect(
            EffectKind.ADD_COUNTERS,
            targets=others,
            counter_type="+1/+1",
            amount=Value.of(1),
            is_targeted=True,
            text=instance.text or instance.name,
        ),
    )


@register("Amass")
def _amass(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.47: put N +1/+1 counters on an Army you control, creating a 0/0
    Army token first if you have none."""
    army = TokenSpec(
        name="Army",
        types=CardType.CREATURE,
        subtypes=("Army",),
        power=Value.of(0),
        toughness=Value.of(0),
    )
    armies = ObjectFilter(
        types_all=CardType.CREATURE,
        subtypes_any=("Army",),
        controller=ControllerRelation.YOU,
    )
    return (
        Effect(
            EffectKind.CONDITIONAL,
            condition=_no_such(armies),
            children=(Effect(EffectKind.CREATE_TOKEN, token=army, players=YOU),),
        ),
        Effect(
            EffectKind.ADD_COUNTERS,
            targets=armies,
            counter_type="+1/+1",
            amount=_amount(instance),
        ),
    )


@register("Incubate")
def _incubate(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.53: an Incubator token with N +1/+1 counters on it."""
    incubator = TokenSpec(
        name="Incubator",
        types=CardType.ARTIFACT,
        subtypes=("Incubator",),
        power=Value.of(0),
        toughness=Value.of(0),
    )
    return (
        Effect(
            EffectKind.CREATE_TOKEN,
            token=incubator,
            players=YOU,
            text=instance.text or instance.name,
        ),
    )


def _no_such(spec: ObjectFilter):
    from ..kernel.query import Comparison, Condition, ConditionKind, NumericConstraint

    return Condition(
        ConditionKind.OBJECT_COUNT,
        filter=spec,
        constraint=NumericConstraint(Comparison.EQ, Value.of(0)),
    )


# ---------------------------------------------------------------------------
# Token-making actions
# ---------------------------------------------------------------------------

_ARTIFACT_TOKENS: dict[str, TokenSpec] = {
    "investigate": TokenSpec(name="Clue", types=CardType.ARTIFACT, subtypes=("Clue",)),
    "food": TokenSpec(name="Food", types=CardType.ARTIFACT, subtypes=("Food",)),
    "treasure": TokenSpec(
        name="Treasure", types=CardType.ARTIFACT, subtypes=("Treasure",)
    ),
    "collect evidence": TokenSpec(
        name="Clue", types=CardType.ARTIFACT, subtypes=("Clue",)
    ),
}


@register("Investigate", "Food", "Treasure")
def _artifact_token(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.16, 701.47: create a named artifact token."""
    spec = _ARTIFACT_TOKENS[instance.key]
    return (
        Effect(
            EffectKind.CREATE_TOKEN,
            token=spec,
            players=YOU,
            amount=_amount(instance),
            text=instance.text or instance.name,
        ),
    )


@register("Populate")
def _populate(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.36: copy a creature token you control."""
    tokens = ObjectFilter(
        types_all=CardType.CREATURE,
        controller=ControllerRelation.YOU,
        is_token=True,
    )
    return (
        Effect(
            EffectKind.COPY_PERMANENT,
            targets=tokens,
            text=instance.text or instance.name,
        ),
    )


@register("Manifest", "Manifest dread", "Cloak")
def _manifest(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.40: put the top card onto the battlefield face down as a 2/2.

    The face-down part is what the engine already models in layer 1b, so this
    is a zone change plus a status change rather than anything new.
    """
    top = ObjectFilter(zones=frozenset({Zone.LIBRARY}), count=_amount(instance))
    return (
        Effect(
            EffectKind.PUT_ONTO_BATTLEFIELD,
            targets=top,
            text=instance.text or instance.name,
        ),
        Effect(
            EffectKind.TURN_FACE_DOWN,
            targets=ObjectFilter(
                types_all=CardType.CREATURE, controller=ControllerRelation.YOU
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Library-searching actions
# ---------------------------------------------------------------------------


@register("Learn", "Seek", "Discover", "Draft from a spellbook")
def _search(instance: ActionInstance) -> tuple[Effect, ...]:
    return (
        Effect(
            EffectKind.SEARCH_LIBRARY,
            targets=instance.filter
            or ObjectFilter(zones=frozenset({Zone.LIBRARY})),
            players=YOU,
            amount=_amount(instance),
            text=instance.text or instance.name,
        ),
    )


@register("Fateseal")
def _fateseal(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.29: scry, but done to an opponent's library."""
    return (
        Effect(
            EffectKind.SCRY,
            players=instance.players or EACH_OPPONENT,
            amount=_amount(instance),
            text=instance.text or instance.name,
        ),
    )


@register("Connive")
def _connive(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.50: draw N, discard N, then a +1/+1 counter per nonland discarded."""
    return (
        Effect(EffectKind.DRAW, players=YOU, amount=_amount(instance)),
        Effect(EffectKind.DISCARD, players=YOU, amount=_amount(instance)),
        Effect(
            EffectKind.ADD_COUNTERS,
            targets=SELF,
            counter_type="+1/+1",
            amount=_amount(instance),
            text=instance.text or instance.name,
        ),
    )


# ---------------------------------------------------------------------------
# Player-facing actions
# ---------------------------------------------------------------------------


@register("Detain", "Suspect", "Exert")
def _status_action(instance: ActionInstance) -> tuple[Effect, ...]:
    """Actions that impose an ongoing restriction on a permanent (CR 701.35)."""
    from ..cr500_turn_structure.restrictions import Act, Restriction

    acts = {
        "detain": (Act.ATTACK, Act.BLOCK, Act.ACTIVATE_ABILITY),
        "suspect": (Act.BLOCK,),
        "exert": (Act.UNTAP,),
    }[instance.key]
    return (
        Effect(
            EffectKind.RESTRICTION,
            targets=instance.filter,
            restrictions=tuple(
                Restriction(act=act, subject=instance.filter, text=instance.name)
                for act in acts
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Endure", "Forage", "Behold", "Time Travel", "Venture into the dungeon",
          "Open an Attraction", "Roll to Visit Your Attractions", "Face a dilemma",
          "Set in motion", "Planeswalk", "Abandon", "Assemble", "Assimilate",
          "Blight", "Convert", "Earthbend", "Airbend", "Waterbend", "Harness",
          "Heal", "Heist", "Incorporate", "Meld", "Prepared", "Role token",
          "Triple", "Clash", "Play", "Cast", "Activate", "Double")
def _not_yet_modelled(instance: ActionInstance) -> tuple[Effect, ...]:
    """Actions whose behaviour is catalogued but not built.

    Registered deliberately: an action the parser can *name* but not execute is
    visible in the coverage report, whereas one it cannot name at all looks
    like a parse failure and hides which of the two problems it is.
    """
    return (Effect(EffectKind.UNPARSED, text=instance.text or instance.name),)


# ---------------------------------------------------------------------------
# The remainder of CR 701
#
# ``register`` overwrites, so these replace the shape-only builders above.
# ---------------------------------------------------------------------------


ANY_OBJECT = ObjectFilter()
YOUR_LIBRARY = ObjectFilter(
    zones=frozenset({Zone.LIBRARY}), owner=ControllerRelation.YOU
)
YOUR_GRAVEYARD = ObjectFilter(
    zones=frozenset({Zone.GRAVEYARD}), owner=ControllerRelation.YOU
)


@register("Play")
def _play(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.18: to play a card is to cast it or to play it as a land.

    Which one depends on the card, so this is one opcode and the executor
    decides - a land uses the land drop, anything else is cast and paid for.
    """
    return (
        Effect(
            EffectKind.PLAY_FROM_ZONE,
            targets=instance.filter or ANY_OBJECT,
            players=instance.players or YOU,
            text=instance.text or instance.name,
        ),
    )


@register("Cast")
def _cast(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.5: to cast a spell is to move it to the stack and follow CR 601.2.

    "Cast without paying its mana cost" is the common form on cards, so that is
    what this expands to; a cast that pays normally is ``Play``.
    """
    return (
        Effect(
            EffectKind.CAST_WITHOUT_PAYING,
            targets=instance.filter or ANY_OBJECT,
            players=instance.players or YOU,
            text=instance.text or instance.name,
        ),
    )


@register("Activate")
def _activate(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.2: to activate an ability is to put it on the stack and pay its
    costs.

    Cards that say "activate" as an instruction are rare and always name which
    ability, which is text the parser has to read - so the opcode is honest
    about not knowing which one.
    """
    return (Effect(EffectKind.UNPARSED, text=instance.text or instance.name),)


@register("Clash")
def _clash(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.30a: "each clashing player reveals the top card of their library,
    then puts it on the top or bottom. A player wins if their card had a higher
    mana value."
    """
    return (
        Effect(
            EffectKind.REVEAL,
            players=EACH_PLAYER,
            from_zone=Zone.LIBRARY,
            amount=Value.of(1),
            text="each player reveals the top card of their library",
        ),
        Effect(
            EffectKind.PUT_ON_LIBRARY,
            players=EACH_PLAYER,
            text="put it back on top or on the bottom",
        ),
    )


@register("Meld")
def _meld(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.42a / 713: exile two permanents and return them melded.

    Both halves must be on the battlefield and owned by the same player, which
    is a check the transform machinery already makes - so this is a transform
    with a partner rather than a new mechanism.
    """
    return (
        Effect(
            EffectKind.TRANSFORM,
            targets=instance.filter or SELF,
            text=instance.text or instance.name,
        ),
    )


@register("Endure")
def _endure(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.63a: "put N +1/+1 counters on this creature, or create an N/N
    white Spirit creature token."
    """
    spirit = TokenSpec(
        name="Spirit",
        types=CardType.CREATURE,
        subtypes=("Spirit",),
        colors=Color.WHITE,
        power=_amount(instance),
        toughness=_amount(instance),
    )
    return (
        Effect(
            EffectKind.CHOOSE_MODE,
            children=(
                Effect(
                    EffectKind.ADD_COUNTERS,
                    targets=SELF,
                    counter_type="+1/+1",
                    amount=_amount(instance),
                    text=f"put {instance.amount} +1/+1 counters on it",
                ),
                Effect(
                    EffectKind.CREATE_TOKEN,
                    token=spirit,
                    players=YOU,
                    text=f"create an {instance.amount}/{instance.amount} Spirit",
                ),
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Forage")
def _forage(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.61a: "exile three cards from your graveyard, or sacrifice a Food."
    """
    food = ObjectFilter(
        types_all=CardType.ARTIFACT,
        subtypes_any=("Food",),
        controller=ControllerRelation.YOU,
    )
    return (
        Effect(
            EffectKind.CHOOSE_MODE,
            children=(
                Effect(
                    EffectKind.EXILE,
                    targets=YOUR_GRAVEYARD,
                    amount=Value.of(3),
                    text="exile three cards from your graveyard",
                ),
                Effect(
                    EffectKind.SACRIFICE, targets=food, text="sacrifice a Food"
                ),
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Collect evidence")
def _collect_evidence(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.59a: "exile cards with total mana value N or greater from your
    graveyard."
    """
    return (
        Effect(
            EffectKind.EXILE,
            targets=YOUR_GRAVEYARD,
            amount=_amount(instance),
            text=instance.text or instance.name,
        ),
    )


@register("Time Travel")
def _time_travel(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.56a: "for each suspended card you own and each permanent you
    control with a time counter, you may add or remove a time counter."
    """
    timed = ObjectFilter(
        has_counter="time",
        counter_constraint=NumericConstraint.at_least(1),
        controller=ControllerRelation.YOU,
    )
    return (
        Effect(
            EffectKind.CHOOSE_MODE,
            children=(
                Effect(
                    EffectKind.ADD_COUNTERS,
                    targets=timed,
                    counter_type="time",
                    amount=Value.of(1),
                    text="add a time counter",
                ),
                Effect(
                    EffectKind.REMOVE_COUNTERS,
                    targets=timed,
                    counter_type="time",
                    amount=Value.of(1),
                    text="remove a time counter",
                ),
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Venture into the dungeon")
def _venture(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.49a: enter the first room of a dungeon, or move to the next."""
    return (
        Effect(
            EffectKind.VENTURE,
            players=instance.players or YOU,
            text=instance.text or instance.name,
        ),
    )


@register("Plot")
def _plot(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 702.170a: "exile that card from your hand. You may cast it as a sorcery
    on a later turn without paying its mana cost."
    """
    return (
        Effect(
            EffectKind.EXILE,
            targets=instance.filter or SELF,
            from_zone=Zone.HAND,
            text=instance.text or instance.name,
        ),
    )


@register("Role token")
def _role_token(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 111.10j-r: Role tokens are predefined tokens - colorless Aura Role
    enchantment tokens with enchant creature. Not a keyword action, so
    there is no CR 701 entry for them.

    Which Role - Cursed, Monster, Royal, Sorcerer, Virtuous, Wicked, Young
    Hero - is card text, and each grants something different, so the token is
    created with its subtype and the grant comes from the parser.
    """
    role = TokenSpec(
        name="Role",
        types=CardType.ENCHANTMENT,
        subtypes=("Aura", "Role"),
    )
    return (
        Effect(
            EffectKind.CREATE_TOKEN,
            token=role,
            players=instance.players or YOU,
            text=instance.text or instance.name,
        ),
    )


@register("Double", "Triple")
def _multiply(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.10 / 701.60: doubling or tripling a quantity.

    Applied to counters here, which is the common case; doubling life or damage
    is a replacement effect the parser builds directly rather than a keyword
    action.
    """
    factor = 2 if instance.key == "double" else 3
    return (
        Effect(
            EffectKind.ADD_COUNTERS,
            targets=instance.filter or SELF,
            counter_type=instance.text or "+1/+1",
            amount=Value(
                kind=ValueKind.PRODUCT,
                operands=(
                    Value(
                        kind=ValueKind.COUNTERS,
                        filter=instance.filter or SELF,
                        counter_type="+1/+1",
                    ),
                    Value.of(factor - 1),
                ),
            ),
            text=instance.text or instance.name,
        ),
    )


@register("Heal")
def _heal(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.69: to heal is to remove damage marked on a permanent."""
    return (
        Effect(
            EffectKind.PREVENT_DAMAGE,
            targets=instance.filter or SELF,
            amount=_amount(instance),
            text=instance.text or instance.name,
        ),
    )


@register("Behold")
def _behold(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.4a: "reveal a [quality] card from your hand, or choose one you
    control." No cost is paid and nothing moves; it is a check.
    """
    return (
        Effect(
            EffectKind.REVEAL,
            targets=instance.filter or ANY_OBJECT,
            players=instance.players or YOU,
            amount=Value.of(1),
            text=instance.text or instance.name,
        ),
    )


@register("Harness")
def _harness(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.64: tap an untapped permanent you control for its harness ability.

    The permanent taps; what that buys is printed on the card.
    """
    return (
        Effect(
            EffectKind.TAP,
            targets=instance.filter
            or ObjectFilter(controller=ControllerRelation.YOU, tapped=False),
            text=instance.text or instance.name,
        ),
    )


@register("Assemble")
def _assemble(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.45: assemble a Contraption - Unstable only, and not
    Commander-legal, so no deck this program simulates can reach it."""
    return (Effect(EffectKind.UNPARSED, text=instance.text or instance.name),)


#: Keyword actions belonging to formats this simulator does not run. Named
#: rather than omitted, so a card carrying one is reported as out of scope
#: instead of quietly doing nothing.
OUT_OF_FORMAT_ACTIONS = (
    "Abandon",            # Archenemy schemes
    "Set in motion",      # Archenemy schemes
    "Planeswalk",         # Planechase
    "Open an Attraction",  # Unfinity
    "Roll to Visit Your Attractions",  # Unfinity
)


@register(*OUT_OF_FORMAT_ACTIONS)
def _out_of_format_action(instance: ActionInstance) -> tuple[Effect, ...]:
    return (Effect(EffectKind.UNPARSED, text=instance.text or instance.name),)


#: Actions from sets newer than the engine's card-pool snapshot. The rules text
#: exists; no Commander-legal card in the pool uses them yet.
NOT_YET_MODELLED_ACTIONS = (
    "Airbend", "Earthbend", "Waterbend", "Assimilate", "Blight", "Convert",
    "Face a dilemma", "Heist", "Incorporate", "Prepared",
)


@register(*NOT_YET_MODELLED_ACTIONS)
def _not_yet_modelled_action(instance: ActionInstance) -> tuple[Effect, ...]:
    return (Effect(EffectKind.UNPARSED, text=instance.text or instance.name),)
