"""The six structural gaps from docs/parser-gaps.md, pinned.

Each of these was a whole *category* of card rather than a single template, so
each test names the category and one card that proves it. The point of pinning
them is that grammar work tends to trade one template for another, and a
category regressing is much harder to notice than a card regressing.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.nouns import parse_object_filter, parse_value, singular
from mtgfish.parser.tokens import Stream
from mtgfish.parser.triggers import parse_trigger
from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind
from mtgfish.rules.kernel.enums import CardType
from mtgfish.rules.kernel.query import ValueKind


def _reads(text, parser=parse_effects):
    """Whether the grammar reads the whole of ``text`` and nothing less."""
    stream = Stream.of(text)
    result = parser(stream)
    return result is not None and stream.done


# ---------------------------------------------------------------------------
# 1. Noun phrases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "artifact, enchantment, or planeswalker",
        "artifact, creature, or land",
        "instant or sorcery spell",
        "creature or planeswalker you control",
    ],
)
def test_a_comma_separated_type_list_is_a_disjunction(card_db, phrase):
    """"or" is written once, before the last item only. Requiring a
    conjunction at every step stopped dead at the first comma."""
    card_db.registry()
    stream = Stream.of(phrase)
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done
    assert spec.types_any, "a list of types must be ANY, never ALL"
    assert not spec.types_all, "read as ALL it would match almost nothing"


@pytest.mark.parametrize(
    ("plural", "expected"),
    [("Goblins", "Goblin"), ("Elves", "Elf"), ("Ninjas", "Ninja"), ("Allies", "Ally")],
)
def test_plural_creature_types_are_understood(card_db, plural, expected):
    """The registry holds singulars and oracle text pluralises freely, so
    every tribal card in the format failed on its own creature type."""
    card_db.registry()
    assert singular(plural) == expected


def test_a_leading_state_adjective_reads(card_db):
    card_db.registry()
    stream = Stream.of("Attacking Ninjas you control")
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done
    assert spec.attacking is True
    assert spec.subtypes_all == ("Ninja",)


def test_a_subtype_disjunction_is_not_a_conjunction(card_db):
    card_db.registry()
    stream = Stream.of("Elf or Goblin creature")
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done
    assert set(spec.subtypes_any) == {"Elf", "Goblin"}
    assert not spec.subtypes_all, "nothing is both an Elf and a Goblin"


def test_the_top_of_a_library_is_a_position_not_just_a_zone(card_db):
    """CR 401.2: a library is ordered. An effect on "the top card" must not be
    free to pick any card in the library."""
    card_db.registry()
    stream = Stream.of("the top card of your library")
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done
    assert spec.from_top == 1

    stream = Stream.of("the top three cards of their library")
    spec = parse_object_filter(stream)
    assert spec is not None and stream.done
    assert spec.from_top == 3


def test_a_superlative_reads_over_a_filter(card_db):
    card_db.registry()
    stream = Stream.of("the greatest mana value among permanents you control")
    value = parse_value(stream)
    assert value is not None and stream.done
    assert value.kind is ValueKind.GREATEST_AMONG
    assert value.filter is not None
    assert value.operands and value.operands[0].kind is ValueKind.MANA_VALUE


# ---------------------------------------------------------------------------
# 2. Conditions
# ---------------------------------------------------------------------------


def test_unless_modifies_the_effect_before_it(card_db):
    """The dangerous failure, not the loud one.

    Read as a sibling clause, "draw a card unless that player pays {1}" became
    "draw a card" *and* something else entirely - Rhystic Study parsed into a
    cost increase, which is a completely different card that still ran.
    """
    card_db.registry()
    stream = Stream.of("you may draw a card unless that player pays {1}.")
    effects = parse_effects(stream)
    assert effects is not None and stream.done
    assert len(effects) == 1, "the unless clause is not a second effect"

    kinds = {node.kind for effect in effects for node in effect.walk()}
    assert EffectKind.UNLESS_PAYS in kinds
    assert EffectKind.MODIFY_COST not in kinds, "this is not a cost increase"


def test_unless_carries_the_cost_that_stops_it(card_db):
    card_db.registry()
    effects = parse_effects(
        Stream.of("you may draw a card unless that player pays {1}.")
    )
    escape = next(
        node
        for effect in effects
        for node in effect.walk()
        if node.kind is EffectKind.UNLESS_PAYS
    )
    assert escape.pay_cost is not None
    assert escape.children, "the effect it guards must survive"


@pytest.mark.parametrize(
    "text",
    [
        "This creature gets +1/+1 as long as you control an artifact.",
        "As long as you control an artifact, this creature gets +1/+1.",
        "This creature has flying as long as you control an artifact.",
    ],
)
def test_as_long_as_reads_on_either_side(card_db, text):
    """Oracle text puts the condition before or after the effect and means the
    same thing. Only the leading form was read, and the trailing one is the
    commoner of the two."""
    card_db.registry()
    assert _reads(text)


# ---------------------------------------------------------------------------
# 3. Trigger conditions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Whenever this creature or another creature you control dies, draw a card",
        "Whenever this creature is dealt damage, you gain 2 life",
        "Whenever this creature becomes the target of a spell, draw a card",
        "Whenever a creature you control attacks or blocks, draw a card",
        "Whenever this creature becomes untapped, draw a card",
    ],
)
def test_trigger_conditions_that_used_to_give_up_immediately(card_db, text):
    card_db.registry()
    stream = Stream.of(text)
    assert parse_trigger(stream) is not None


def test_a_disjunctive_subject_includes_the_source(card_db):
    """"this creature or another creature you control" is every creature you
    control - the union of the source and the others."""
    card_db.registry()
    trigger = parse_trigger(
        Stream.of(
            "Whenever this creature or another creature you control dies, draw a card"
        )
    )
    assert trigger is not None
    assert trigger.subject is not None
    assert not trigger.subject.other_than_source


# ---------------------------------------------------------------------------
# 4. Characteristic-defining abilities
# ---------------------------------------------------------------------------


def test_a_defined_pt_is_marked_characteristic_defining(card_db):
    """CR 604.3 puts these in layer 7a, before every other P/T effect. Read as
    an ordinary setting effect it would land in 7b and overwrite counters."""
    from mtgfish.parser import parse_card

    card = card_db.lookup("Nightmare")
    assert card is not None
    abilities = [a for face in parse_card(card).faces for a in face.abilities]
    defined = [a for a in abilities if a.is_characteristic_defining]
    assert defined, [a.text for a in abilities]

    effect = defined[0].effects[0]
    assert effect.kind is EffectKind.SET_PT
    assert effect.amount.kind is ValueKind.COUNT


def test_a_cda_functions_outside_the_battlefield(card_db):
    """CR 604.3: a CDA applies in every zone."""
    from mtgfish.parser import parse_card
    from mtgfish.rules.kernel.enums import Zone

    card = card_db.lookup("Nightmare")
    defined = [
        a
        for face in parse_card(card).faces
        for a in face.abilities
        if a.is_characteristic_defining
    ]
    assert Zone.GRAVEYARD in defined[0].functions_in


# ---------------------------------------------------------------------------
# 5. Prevention
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Prevent all combat damage that would be dealt this turn.",
        "Prevent all combat damage that would be dealt to this creature.",
        "Prevent the next 3 damage that would be dealt to target creature this turn.",
        "Prevent all damage that would be dealt to you this turn.",
    ],
)
def test_prevention_shapes(card_db, text):
    card_db.registry()
    assert _reads(text)


# ---------------------------------------------------------------------------
# 6. Mana restrictions
# ---------------------------------------------------------------------------


def test_restricted_mana_cannot_pay_for_the_wrong_spell(card_db, tmp_path):
    """The whole point of reading the rider.

    Mana that is supposed to be narrow being spendable on anything is a
    straightforward overstatement of how fast a deck is - and it would show up
    in a run as nothing but good draws.
    """
    from mtgfish.parser.verdicts import VerdictStore
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind, SpendOnlyOn
    from mtgfish.rules.kernel.enums import Color, Zone
    from mtgfish.rules.kernel.query import ObjectFilter
    from mtgfish.ui.sandbox import Sandbox

    box = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    only_creatures = SpendOnlyOn(
        key="creature spells",
        filter=ObjectFilter(
            types_all=CardType.CREATURE, zones=frozenset({Zone.STACK})
        ),
    )
    pool = box.game.player(0).mana_pool
    for color in (Color.NONE, Color.RED, Color.GREEN):
        pool.add(ManaKind(color, restriction=only_creatures), 4)

    box.put("Grizzly Bears", "hand", 0)
    box.put("Lightning Bolt", "hand", 0)

    offered = [action["description"] for action in box.legal()]
    assert any("Grizzly Bears" in text for text in offered)
    assert not any("Lightning Bolt" in text for text in offered), (
        "restricted mana must not make a noncreature spell look castable"
    )


@pytest.mark.parametrize(
    "name, in_graveyard",
    [
        # CR 113.6m: the effect moves this card out of the graveyard.
        ("Bloodghast", True),
        ("Skyfire Phoenix", True),
        # About what it enchants: triggers on the battlefield, looking back.
        ("Screams from Within", False),
        ("Genju of the Realm", False),
    ],
)
def test_a_trigger_that_returns_this_card_from_the_graveyard_works_there(card_db, name, in_graveyard):
    from mtgfish.parser.compile import parse_card
    from mtgfish.rules.kernel.enums import Zone

    triggers = [
        a.trigger
        for face in parse_card(card_db.lookup(name)).faces
        for a in face.abilities
        if a.trigger is not None and "graveyard" in a.text
    ]
    assert triggers, name
    assert all((t.functions_in == frozenset({Zone.GRAVEYARD})) is in_graveyard for t in triggers)


def test_your_commander_from_the_command_zone(card_db):
    from mtgfish.parser.compile import parse_card
    from mtgfish.rules.kernel.enums import Zone
    from mtgfish.rules.kernel.query import ControllerRelation

    face = parse_card(card_db.lookup("Command Beacon")).faces[0]
    move = face.abilities[-1].effects[0]
    assert move.kind is EffectKind.MOVE_ZONE and move.zone is Zone.HAND
    assert move.targets.is_commander and move.targets.owner is ControllerRelation.YOU
    assert move.targets.zones == frozenset({Zone.COMMAND})


def test_discarding_at_random_is_not_the_players_choice(card_db):
    from mtgfish.parser.compile import parse_card

    effect = parse_card(card_db.lookup("Hymn to Tourach")).faces[0].abilities[0].effects[0]
    assert effect.kind is EffectKind.DISCARD and effect.amount.constant == 2
    assert "at random" in effect.keywords
    plain = parse_effects(Stream.of("Target player discards two cards."))[0]
    assert "at random" not in plain.keywords


def test_unless_any_player_pays_is_left_unread(card_db):
    """The first player who pays stops it; the IR can only ask each player in
    turn, so the card stays unread rather than misread."""
    from mtgfish.parser.compile import parse_card

    assert not parse_card(card_db.lookup("Icy Prison")).fully_parsed
    # A condition about "a player" still reads.
    assert parse_card(card_db.lookup("Abandoned Campground")).fully_parsed


def test_a_basic_land_type_is_any_of_the_five():
    """CR 305.6: "a land card with a basic land type" (Boseiju)."""
    spec = parse_object_filter(Stream.of("a land card with a basic land type"))
    assert spec is not None and set(spec.subtypes_any) == {"Plains", "Island", "Swamp", "Mountain", "Forest"}
    assert spec.types_all & CardType.LAND


@pytest.mark.parametrize("text, targeted", [
    ("The owner of target permanent shuffles it into their library.", True),
    ("Target creature's owner shuffles it into their library.", True),
])
def test_the_owner_of_a_target_shuffles_that_target_in(text, targeted):
    """Chaos Warp: the target is what moves, and it's shuffled in."""
    effect = parse_effects(Stream.of(text))[0]
    assert effect.kind is EffectKind.MOVE_ZONE and effect.is_targeted is targeted
    assert not effect.targets.remembered and "shuffle" in effect.keywords


def test_shuffle_this_into_its_owners_library():
    """The Zenith and Beacon cycles: the card itself, shuffled in."""
    effect = parse_effects(Stream.of("Shuffle this into its owner's library."))[0]
    assert effect.kind is EffectKind.MOVE_ZONE and effect.targets.source_only
    assert "shuffle" in effect.keywords


def test_if_instead_replaces_the_effect_before_it():
    """Field Research: the kicked draw replaces the plain one, not both."""
    effects = parse_effects(Stream.of("Draw two cards. If this spell was kicked, draw three cards instead."))
    assert len(effects) == 1
    only = effects[0]
    assert only.kind is EffectKind.CONDITIONAL
    assert only.children[0].amount.constant == 3 and only.otherwise[0].amount.constant == 2


@pytest.mark.parametrize("text", [
    # "that creature" is the first effect's target: unread, not misread.
    "Target creature gets +2/+2 until end of turn. If this spell was kicked, "
    "that creature gets +5/+5 until end of turn instead.",
    # A different target in the replacement.
    "Exile target artifact or enchantment. If this spell was kicked, exile target nonland permanent instead.",
    # Different kinds of effect: what "instead" replaces isn't plain.
    "Draw a card. If you control a Forest, create a 1/1 white Spirit creature token instead.",
])
def test_if_instead_is_left_unread_when_it_refers_back(text):
    assert parse_effects(Stream.of(text)) is None


@pytest.mark.parametrize("word, subtype", [("equipped", "Equipment"), ("enchanted", "Aura")])
def test_this_is_equipped_or_enchanted(word, subtype):
    """"As long as this creature is equipped": something of that kind is attached."""
    from mtgfish.parser.clauses import parse_condition_text

    condition = parse_condition_text(Stream.of(f"this creature is {word}"))
    assert condition is not None
    attached = condition.filter.has_attached
    assert attached is not None and attached.subtypes_any == (subtype,)


@pytest.mark.parametrize("name, subject", [
    ("Kor Duelist", "source"),            # "this creature is equipped, it has ..."
    ("Clout of the Dominus", "attached"), # "enchanted creature is blue, it gets ..."
    ("Narcolepsy", "attached"),           # a step trigger: "tap it"
])
def test_it_in_a_static_ability_is_the_conditions_object(card_db, name, subject):
    """readings_queue.md: "it" here once meant whatever was just referred to,
    which in a static ability is nothing, so the bonus reached every permanent."""
    from mtgfish.parser.compile import parse_card

    nodes = [n for face in parse_card(card_db.lookup(name)).faces for a in face.abilities
             for e in a.effects for n in e.walk() if n.targets is not None]
    assert nodes and not any(n.targets.remembered for n in nodes)
    if subject == "source":
        assert any(n.targets.source_only for n in nodes)
    else:
        assert any(n.targets.has_attached is not None and n.targets.has_attached.source_only for n in nodes)


def test_that_creature_with_nothing_to_name_is_left_unread(card_db):
    """Deadly Wanderings: "that creature" is the one creature you control,
    which the IR can't name; unread rather than every creature."""
    from mtgfish.parser.compile import parse_card

    assert not parse_card(card_db.lookup("Deadly Wanderings")).fully_parsed


@pytest.mark.parametrize("name", ["Dovin's Veto", "Carnage Tyrant"])
def test_cant_be_countered_works_on_the_stack(card_db, name):
    """readings_queue.md: on an instant it was a spell effect (runs on
    resolution, too late); on a permanent, it worked on the battlefield."""
    from mtgfish.parser.compile import parse_card
    from mtgfish.rules.cr600_spells_and_abilities.abilities import AbilityKind
    from mtgfish.rules.kernel.enums import Zone

    face = parse_card(card_db.lookup(name)).faces[0]
    line = next(a for a in face.abilities if a.text.lower().startswith("this spell can't be countered"))
    assert line.kind is AbilityKind.STATIC and line.functions_in == frozenset({Zone.STACK})


def test_every_basic_land_type_is_all_five(card_db):
    """Dryad of the Ilysian Grove (readings_queue.md): read as no types."""
    from mtgfish.parser.compile import parse_card

    face = parse_card(card_db.lookup("Dryad of the Ilysian Grove")).faces[0]
    added = [n for a in face.abilities for e in a.effects for n in e.walk() if n.kind is EffectKind.ADD_TYPE]
    assert added and set(added[0].keywords) == {"Plains", "Island", "Swamp", "Mountain", "Forest"}


@pytest.mark.parametrize("name", ["The Great Henge", "Ghalta, Primal Hunger", "Drag to the Underworld"])
def test_costs_x_less_keeps_its_definition(card_db, name):
    """readings_queue.md: "costs {X} less ..., where X is ..." read as "costs
    0 less" ({X} has mana value 0). X is now the trailing definition."""
    from mtgfish.parser.compile import parse_card
    from mtgfish.rules.kernel.query import ValueKind

    face = parse_card(card_db.lookup(name)).faces[0]
    change = next(n for a in face.abilities for e in a.effects for n in e.walk() if n.kind is EffectKind.MODIFY_COST)
    assert change.amount.kind is ValueKind.PRODUCT
    sign, x = change.amount.operands
    assert sign.constant == -1 and x.kind not in (ValueKind.X, ValueKind.CONSTANT)


@pytest.mark.parametrize("name, tapped", [
    ("Rom and Leeta, Unlikely Pair", True),  # "create two tapped Treasure tokens"
    ("Fallaji Excavation", True),  # "Create three tapped Powerstone tokens"
    ("Smothering Tithe", False),  # "create a Treasure token"
])
def test_a_tapped_token_enters_tapped(card_db, name, tapped):
    """The noun reader took "tapped" as a filter constraint and nothing carried
    it to the token, so every "create a tapped ..." token entered untapped."""
    from mtgfish.parser.compile import parse_card

    face = parse_card(card_db.lookup(name)).faces[0]
    made = [n for a in face.abilities for e in a.effects for n in e.walk() if n.kind is EffectKind.CREATE_TOKEN]
    assert made and all(n.token.enters_tapped is tapped for n in made)


@pytest.mark.parametrize("name, events", [
    ("Teysa Karlov", {"DIES"}),
    ("Panharmonicon", {"ENTERS_BATTLEFIELD"}),
    ("Gandalf the White", {"ENTERS_BATTLEFIELD", "LEAVES_BATTLEFIELD"}),
    ("Wulfgar of Icewind Dale", {"ATTACKS"}),
])
def test_an_extra_trigger_keeps_its_causing_event(card_db, name, events):
    """"If a creature *dying* causes a triggered ability ... to trigger": the
    participle was skipped, so Teysa doubled a creature's entering triggers."""
    from mtgfish.parser.compile import parse_card

    face = parse_card(card_db.lookup(name)).faces[0]
    extra = next(e for a in face.abilities for e in a.effects if e.kind is EffectKind.EXTRA_TRIGGER)
    assert {k.name for k in extra.trigger.event_kinds} == events


def test_an_extra_trigger_with_an_unknown_event_is_unread():
    from mtgfish.parser.clauses import _causing_event

    assert _causing_event("drawing a card") is None
