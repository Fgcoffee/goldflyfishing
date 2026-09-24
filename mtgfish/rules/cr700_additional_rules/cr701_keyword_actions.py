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

from ..cr100_game_concepts.cr106_mana import ManaCost
from ..cr100_game_concepts.cr118_costs import (
    AlternativeCost,
    Cost,
    CostComponent,
    CostKind,
)
from ..cr600_spells_and_abilities.abilities import Ability, AbilityKind, TriggerCondition
from ..cr600_spells_and_abilities.effects import Effect, EffectKind, TokenSpec
from ..kernel.enums import CardType, Color, Duration, Zone
from ..kernel.events import EventKind
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


@register("Discover")
def _discover(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.57a: exile until a nonland card with mana value N or less; cast
    it free or put it into your hand; the rest to the bottom at random. It
    is not a search - the cards are exiled, and in order."""
    return (
        Effect(
            EffectKind.DISCOVER,
            players=YOU,
            amount=_amount(instance),
            text=instance.text or instance.name,
        ),
    )


@register("Learn", "Seek", "Draft from a spellbook")
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
    """CR 701.45a: assembling a Contraption is an Unstable mechanic, and the
    Comprehensive Rules decline to define it - they say only that Unstable is
    out of their scope and point at that set's FAQ.

    So there is no rule here to implement. One Commander-legal card refers to
    assembling a Contraption, and it stays unparsed rather than being given
    invented behaviour."""
    return (Effect(EffectKind.UNPARSED, text=instance.text or instance.name),)


#: Keyword actions belonging to formats this simulator does not run. Named
#: rather than omitted, so a card carrying one is reported as out of scope
#: instead of quietly doing nothing.
OUT_OF_FORMAT_ACTIONS = (
    "Abandon",            # Archenemy schemes
    "Set in motion",      # Archenemy schemes
    "Planeswalk",         # Planechase
    # CR 505.5: the roll happens in the precombat main phase and needs a die
    # plus every Attraction's lit numbers, neither of which is modelled.
    "Roll to Visit Your Attractions",
)


@register(*OUT_OF_FORMAT_ACTIONS)
def _out_of_format_action(instance: ActionInstance) -> tuple[Effect, ...]:
    return (Effect(EffectKind.UNPARSED, text=instance.text or instance.name),)


#: Actions this registry can name and nothing else can reach. They appear
#: nowhere in the Comprehensive Rules and on no Commander-legal card - there
#: is no rule to implement, only a word.
NOT_YET_MODELLED_ACTIONS = (
    "Assimilate", "Face a dilemma", "Heist", "Incorporate",
)


@register(*NOT_YET_MODELLED_ACTIONS)
def _not_yet_modelled_action(instance: ActionInstance) -> tuple[Effect, ...]:
    return (Effect(EffectKind.UNPARSED, text=instance.text or instance.name),)


# ---------------------------------------------------------------------------
# The bending actions (CR 701.65 - 701.68), and the rest of CR 701
#
# ``register`` overwrites, so these replace the shape-only builders above.
# ---------------------------------------------------------------------------

#: "it", "that land", "the blighted creature" - whichever object the sentence
#: before this one acted on (CR 608.2). Only the opcodes in ``resolve``'s
#: remembering set record it, which is why every expansion below puts the one
#: that does first.
IT = ObjectFilter(remembered=True)

ONE_LAND_YOU_CONTROL = ObjectFilter(
    types_all=CardType.LAND,
    controller=ControllerRelation.YOU,
    count=Value.of(1),
)
ONE_CREATURE_YOU_CONTROL = ObjectFilter(
    types_all=CardType.CREATURE,
    controller=ControllerRelation.YOU,
    count=Value.of(1),
)


@register("Earthbend")
def _earthbend(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.66a: a target land you control becomes a 0/0 land creature with
    haste, takes N +1/+1 counters, and a delayed ability (CR 603.7) brings it
    back tapped if it dies or is exiled.

    "0/0 land creature" with no creature type, unlike awaken's Elemental - so
    the animation is a type addition (layer 4) with nothing in ``keywords``,
    a base P/T (layer 7b), and the counters arriving in layer 7d on top. A land
    earthbent for 3 is a 3/3.

    The counters are placed first although the rule says it second. Nothing in
    the game can tell - state-based actions are not checked mid-resolution
    (CR 704.3), so the land is never a 0/0 with no counters on it - and it buys
    the reference every later clause needs: ``ADD_COUNTERS`` is the opcode that
    records what it acted on, so "it" means the land that was targeted rather
    than every land its controller has.

    CR 701.66b's "whenever a player earthbends" trigger fires when the delayed
    ability is created, which is this effect list running to its end.
    """
    return (
        Effect(
            EffectKind.ADD_COUNTERS,
            targets=ONE_LAND_YOU_CONTROL,
            is_targeted=True,
            counter_type="+1/+1",
            amount=_amount(instance),
            text=f"put {instance.amount} +1/+1 counters on target land you control",
        ),
        Effect(
            EffectKind.ADD_TYPE,
            targets=IT,
            types=CardType.CREATURE,
            duration=int(Duration.PERMANENT),
            text="it becomes a creature in addition to its other types",
        ),
        Effect(
            EffectKind.SET_PT,
            targets=IT,
            amount=Value.of(0),
            amount2=Value.of(0),
            duration=int(Duration.PERMANENT),
            text="its base power and toughness are 0/0",
        ),
        Effect(
            EffectKind.GRANT_ABILITY,
            targets=IT,
            keywords=("Haste",),
            duration=int(Duration.PERMANENT),
            text="it has haste",
        ),
        Effect(
            EffectKind.DELAYED_TRIGGER,
            trigger=TriggerCondition(
                event_kinds=frozenset({EventKind.DIES, EventKind.EXILED}),
                # The object the event is about has already left, so it is
                # matched against last known information (CR 603.6e) and the
                # zone constraint is dropped.
                subject=ObjectFilter(
                    remembered=True,
                    types_all=CardType.LAND,
                    zones=frozenset(),
                ),
                functions_in=frozenset(Zone),
                uses_last_known_information=True,
                text="when that land dies or is put into exile",
            ),
            children=(
                Effect(
                    EffectKind.PUT_ONTO_BATTLEFIELD,
                    targets=ObjectFilter(
                        remembered=True,
                        zones=frozenset({Zone.GRAVEYARD, Zone.EXILE}),
                    ),
                    keywords=("tapped",),
                    text="return it to the battlefield tapped under your control",
                ),
            ),
            text="when that land dies or is put into exile, return it tapped",
        ),
    )


@register("Blight")
def _blight(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.68a: put N -1/-1 counters on a creature you control.

    CR 701.68c calls that creature "the blighted creature", and later sentences
    on the same card refer back to it. Nothing extra is needed for that:
    ``ADD_COUNTERS`` records what it acted on, which is exactly what "the
    blighted creature" resolves against.

    DIVERGENCE. The creature is *chosen*, not targeted (CR 701.68a names no
    target), and the engine's only way to settle on exactly one object is
    target selection - an untargeted filter is matched in full, which would put
    the counters on every creature its controller has. Choosing one by
    targeting gets the set right; the difference shows only against something
    that cares about being targeted, such as ward.
    """
    return (
        Effect(
            EffectKind.ADD_COUNTERS,
            targets=instance.filter or ONE_CREATURE_YOU_CONTROL,
            is_targeted=True,
            counter_type="-1/-1",
            amount=_amount(instance),
            text=instance.text or f"blight {instance.amount}",
        ),
    )


@register("Convert")
def _convert(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.28a: turn the permanent so that its other face is up, following
    the transform rules in every respect.

    So it is the transform opcode and not a second mechanism. CR 701.28b keeps
    the two apart for triggers - converting is not turning face up - which is a
    question for whatever watches the event, not for the expansion; and
    CR 701.28c's "nothing happens" for a permanent that has no other face is
    already what the executor does with one it cannot turn over.
    """
    return (
        Effect(
            EffectKind.TRANSFORM,
            targets=instance.filter or SELF,
            text=instance.text or instance.name,
        ),
    )


@register("Airbend")
def _airbend(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.65a: exile the named objects; each *card* exiled this way may be
    cast by its owner for {2} instead of its mana cost, for as long as it stays
    exiled.

    The permission is an alternative cost (CR 118.9) on a static ability that
    functions from exile, granted to the cards that were just exiled - the
    same shape flashback and foretell use, because it is the same rule.

    Two constraints come straight from the wording. "For each card" excludes
    tokens and copies, which cease to exist in exile and have no mana cost to
    replace. "For as long as it remains exiled" needs no duration: a card that
    leaves exile is a new object (CR 400.7), and a continuous effect settled on
    the old one stops reaching it.
    """
    for_two = Ability(
        AbilityKind.STATIC,
        keyword=instance.name,
        alternative_cost=AlternativeCost(
            cost=Cost.mana(ManaCost.generic(2)),
            from_zone=Zone.EXILE,
            keyword=instance.name,
            text="you may cast this from exile by paying {2}",
        ),
        functions_in=frozenset({Zone.EXILE}),
        text="its owner may cast it by paying {2} rather than its mana cost",
    )
    return (
        Effect(
            EffectKind.EXILE,
            targets=instance.filter,
            players=instance.players,
            text=instance.text or instance.name,
        ),
        Effect(
            EffectKind.GRANT_ABILITY,
            targets=ObjectFilter(
                remembered=True,
                is_token=False,
                zones=frozenset({Zone.EXILE}),
            ),
            granted_abilities=(for_two,),
            duration=int(Duration.PERMANENT),
            text="its owner may cast it for {2} while it remains exiled",
        ),
    )


@register("Waterbend")
def _waterbend(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.67a: pay the cost, and for each generic mana in it you may tap an
    untapped artifact or creature you control instead of paying that mana.

    One payment per generic mana rather than one for the whole cost, because
    the choice is made once per mana: waterbend {3} may tap two permanents and
    pay {1}. CR 701.67b is why the cost is built here at all - the swap is
    confined to the waterbend cost, so it cannot be folded into the spell's
    total cost and left to the generic payment step.
    """
    helper = ObjectFilter(
        types_any=CardType.ARTIFACT | CardType.CREATURE,
        controller=ControllerRelation.YOU,
        tapped=False,
        count=Value.of(1),
    )
    either = Cost(
        choices=(
            Cost.mana(ManaCost.generic(1)),
            Cost(
                (
                    CostComponent(
                        CostKind.TAP_OTHER,
                        amount=Value.of(1),
                        filter=helper,
                        text="tap an untapped artifact or creature you control",
                    ),
                )
            ),
        )
    )
    return (
        Effect(
            EffectKind.REPEAT,
            amount=_amount(instance),
            children=(
                Effect(
                    EffectKind.PAY_COST,
                    pay_cost=either,
                    players=instance.players or YOU,
                    text="pay {1}, or tap an untapped artifact or creature you control",
                ),
            ),
            text=instance.text or f"waterbend {{{instance.amount}}}",
        ),
    )


#: CR 717.2: an Attraction deck is a supplementary deck that lives in the
#: command zone, so its cards are found there rather than in a library.
YOUR_ATTRACTION_DECK = ObjectFilter(
    subtypes_any=("Attraction",),
    # Yours by both readings: CR 717.2 makes the deck the player's own, and a
    # card sitting in the command zone is controlled by whoever owns it, so
    # the control relation is the one the matcher can actually answer.
    controller=ControllerRelation.YOU,
    owner=ControllerRelation.YOU,
    zones=frozenset({Zone.COMMAND}),
    count=Value.of(1),
)


@register("The Ring tempts you")
def _the_ring_tempts_you(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.54: the emblem, the Ring-bearer and the count, all in
    ``cr701_ring``."""
    return (
        Effect(
            EffectKind.RING_TEMPTS,
            players=instance.players or YOU,
            text=instance.text or "the Ring tempts you",
        ),
    )


@register("Recruit")
def _recruit(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.70a: draw a card, then discard a card; if the discarded card was
    a nonland card, create a 1/1 white Human Soldier creature token."""
    from ..cr600_spells_and_abilities.effects import TokenSpec
    from ..kernel.query import Condition, ConditionKind, ObjectFilter

    soldier = TokenSpec(
        types=CardType.CREATURE,
        subtypes=("Human", "Soldier"),
        colors=Color.WHITE,
        power=Value.of(1),
        toughness=Value.of(1),
    )
    return (
        Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1), text="draw a card"),
        Effect(EffectKind.DISCARD, players=YOU, amount=Value.of(1), text="discard a card"),
        Effect(
            EffectKind.CONDITIONAL,
            condition=Condition(
                kind=ConditionKind.REMEMBERED_MATCHES,
                # The discarded card, as it was in hand - anywhere, since it
                # is in the graveyard by the time this is asked.
                filter=ObjectFilter(types_none=CardType.LAND, zones=frozenset()),
                text="if you discarded a nonland card this way",
            ),
            children=(
                Effect(
                    EffectKind.CREATE_TOKEN,
                    token=soldier,
                    amount=Value.of(1),
                    players=YOU,
                    text="create a 1/1 white Human Soldier creature token",
                ),
            ),
        ),
    )


@register("Prepared")
def _prepared(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 722.3a: "becomes prepared" / "enters prepared".

    Prepared is a designation rather than a verb, so the whole behaviour lives
    in ``cr722_preparation``: the copy of the prepare spell in exile, its
    exception to CR 704.5e, and the permanent unpreparing as the copy is cast.
    The expansion is the effect that grants it - to this permanent unless the
    sentence names another. "Enters prepared" is the same effect read as a
    self-entry replacement (CR 614.1c), which ``cr614_replacement`` applies.
    """
    return (
        Effect(
            EffectKind.BECOME_PREPARED,
            targets=instance.filter,
            text=instance.text or "becomes prepared",
        ),
    )


@register("Open an Attraction")
def _open_an_attraction(instance: ActionInstance) -> tuple[Effect, ...]:
    """CR 701.51b: take the top card of your Attraction deck, turn it face up,
    and put it onto the battlefield under your control.

    A zone change out of the command zone, which is where CR 717.2 keeps the
    Attraction deck. Face up is the default for anything entering, so nothing
    has to say it.

    CR 701.51a: a player may only do this in a game where they are playing with
    an Attraction deck. No deck this engine builds has one, so the filter finds
    nothing and the action does nothing - which is the outcome the rule
    prescribes rather than a gap this expansion papers over.
    """
    return (
        Effect(
            EffectKind.PUT_ONTO_BATTLEFIELD,
            targets=YOUR_ATTRACTION_DECK,
            from_zone=Zone.COMMAND,
            players=instance.players or YOU,
            text=instance.text or instance.name,
        ),
    )
