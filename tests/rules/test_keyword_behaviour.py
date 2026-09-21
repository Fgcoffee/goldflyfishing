"""Keywords doing what they say, not merely existing.

The registry can only see whether a builder produced something. These check
that what it produced reaches the board - which is the difference the split
second bug turned on.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, keyword, make_board

from mtgfish.rules import keywords
from mtgfish.rules.abilities import AbilityKind
from mtgfish.rules.cr106_mana import ManaCost
from mtgfish.rules.cr118_costs import Cost, CostComponent, CostKind
from mtgfish.rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.effects import EffectKind
from mtgfish.rules.enums import CardType, Color, Zone
from mtgfish.rules.query import ObjectFilter


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def cost(text: str) -> Cost:
    return Cost((CostComponent(CostKind.MANA, mana=ManaCost.parse(text)),))


# ---------------------------------------------------------------------------
# Characteristic-defining abilities
# ---------------------------------------------------------------------------


def test_changeling_is_every_creature_type(board):
    """CR 702.73a, and it is a CDA - so it applies in every zone."""
    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Changeling")))
    bear = board.play("Grizzly Bears", controller=0)

    subtypes = set(board.chars(bear).subtypes)
    for expected in ("Goblin", "Elf", "Bear", "Sliver", "Zombie"):
        assert expected in subtypes, expected


def test_devoid_makes_a_coloured_card_colourless(board):
    """CR 702.114a. Grizzly Bears is green; devoid makes it colourless despite
    the {G} still sitting in its mana cost."""
    bear = board.play("Grizzly Bears", controller=0)
    assert board.chars(bear).colors & Color.GREEN

    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Devoid")))
    board.refresh()
    assert board.chars(bear).colors == Color.NONE


def test_living_metal_is_a_creature_only_on_your_turn(board):
    """CR 702.163a: the Vehicle is an artifact creature only while it's your
    turn, so it cannot be killed by creature removal on anyone else's."""
    board.scripts.add("Sol Ring", *build(KeywordInstance("Living metal")))
    vehicle = board.play("Sol Ring", controller=0)

    board.game.active_player = 0
    board.refresh()
    assert board.chars(vehicle).is_creature

    board.game.active_player = 1
    board.refresh()
    assert not board.chars(vehicle).is_creature


# ---------------------------------------------------------------------------
# Combat
# ---------------------------------------------------------------------------


def test_skulk_stops_bigger_creatures_blocking(board):
    """CR 702.118b: can't be blocked by creatures with greater power."""
    from mtgfish.rules.cr506_combat import can_block

    board.scripts.add("Grizzly Bears", keyword("Skulk"))
    attacker = board.play("Grizzly Bears", controller=0)  # 2/2 with skulk
    small = board.play("Llanowar Elves", controller=1)  # 1/1
    big = board.play("Serra Angel", controller=1)  # 4/4

    assert can_block(board.game, small, attacker)
    assert not can_block(board.game, big, attacker)


def test_skulk_allows_an_equal_sized_blocker(board):
    """"Greater power", not "greater or equal" - a 2/2 blocks a 2/2 skulker."""
    from mtgfish.rules.cr506_combat import can_block

    board.scripts.add("Grizzly Bears", keyword("Skulk"))
    attacker = board.play("Grizzly Bears", controller=0)
    equal = board.play("Runeclaw Bear", controller=1)  # also 2/2

    assert can_block(board.game, equal, attacker)


def test_banding_hands_the_damage_division_to_the_defender(board):
    """CR 702.22j, the reason banding was ever played.

    Normally the attacking player divides a blocked creature's damage among
    its blockers (CR 510.1c). With a banding blocker, the defending player
    divides it instead, and the attacker loses the ability to aim it.
    """
    from mtgfish.rules.cr506_combat import _attacker_assignment, _combat

    board.scripts.add("Grizzly Bears", keyword("Banding"))
    attacker = board.play("Serra Angel", controller=0)
    bander = board.play("Grizzly Bears", controller=1)
    other = board.play("Runeclaw Bear", controller=1)

    combat = _combat(board.game)
    combat.attacking[attacker.id] = 1
    combat.blockers[attacker.id] = [bander.id, other.id]

    asked: list[int] = []

    class Recorder:
        def assign_combat_damage(self, game, player, source, recipients, total):
            asked.append(player)
            return {}

    board.game.agents[0] = Recorder()
    board.game.agents[1] = Recorder()
    board.game.active_player = 0

    _attacker_assignment(board.game, combat, attacker)
    assert asked == [1], "the defending player should have been asked"


def test_without_banding_the_attacker_divides(board):
    """The control: no banding, and the attacking player divides as usual."""
    from mtgfish.rules.cr506_combat import _attacker_assignment, _combat

    attacker = board.play("Serra Angel", controller=0)
    one = board.play("Grizzly Bears", controller=1)
    two = board.play("Runeclaw Bear", controller=1)

    combat = _combat(board.game)
    combat.attacking[attacker.id] = 1
    combat.blockers[attacker.id] = [one.id, two.id]

    asked: list[int] = []

    class Recorder:
        def assign_combat_damage(self, game, player, source, recipients, total):
            asked.append(player)
            return {}

    board.game.agents[0] = Recorder()
    board.game.agents[1] = Recorder()
    board.game.active_player = 0

    _attacker_assignment(board.game, combat, attacker)
    assert asked == [0]


def test_unleash_stops_blocking_only_while_the_counter_is_there(board):
    """CR 702.101a: the drawback keys off the counter, not off the choice."""
    from mtgfish.rules.cr506_combat import can_block

    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Unleash")))
    blocker = board.play("Grizzly Bears", controller=1)
    attacker = board.play("Serra Angel", controller=0)

    assert can_block(board.game, blocker, attacker)

    blocker.add_counters("+1/+1", 1)
    board.refresh()
    assert not can_block(board.game, blocker, attacker)

    blocker.remove_counters("+1/+1", 1)
    board.refresh()
    assert can_block(board.game, blocker, attacker)


def test_decayed_cannot_block(board):
    """CR 702.148a, the first half."""
    from mtgfish.rules.cr506_combat import can_block

    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Decayed")))
    blocker = board.play("Grizzly Bears", controller=1)
    attacker = board.play("Serra Angel", controller=0)

    assert not can_block(board.game, blocker, attacker)


# ---------------------------------------------------------------------------
# Shapes the registry grades
# ---------------------------------------------------------------------------


def test_morph_family_and_split_second_are_not_activated_abilities():
    """Both were bugs of exactly this shape: a thing that must not use the
    stack, modelled as something that does."""
    for name in ("Morph", "Megamorph", "Disguise", "Split second"):
        for ability in build(KeywordInstance(name, cost=cost("{2}"))):
            assert ability.kind is not AbilityKind.ACTIVATED, name


def test_suspend_functions_from_exile():
    """CR 702.62a: the time counters tick while the card is in exile, so an
    ability that only worked on the battlefield would never fire."""
    abilities = build(KeywordInstance("Suspend", amount=3, cost=cost("{1}")))
    ticking = [a for a in abilities if Zone.EXILE in a.functions_in]
    assert len(ticking) >= 2


def test_dredge_functions_from_the_graveyard():
    """CR 702.52a: it replaces a draw while sitting in the graveyard."""
    abilities = build(KeywordInstance("Dredge", amount=3))
    assert all(Zone.GRAVEYARD in a.functions_in for a in abilities)
    assert abilities[0].effects[0].kind is EffectKind.REPLACEMENT


def test_cascade_casts_without_paying():
    """CR 702.85a. The free cast is the whole keyword; an expansion that only
    exiled cards would look right and do nothing."""
    abilities = build(KeywordInstance("Cascade"))
    kinds = {effect.kind for ability in abilities for effect in ability.effects}
    assert EffectKind.CAST_WITHOUT_PAYING in kinds


def test_living_weapon_makes_a_germ_and_attaches(board):
    """CR 702.91a: both halves, in that order - the token has to exist before
    the Equipment can attach to it."""
    abilities = build(KeywordInstance("Living weapon"))
    kinds = [effect.kind for ability in abilities for effect in ability.effects]
    assert kinds == [EffectKind.CREATE_TOKEN, EffectKind.ATTACH]

    token = abilities[0].effects[0].token
    assert token.name == "Germ"
    assert token.power.constant == 0 and token.toughness.constant == 0


def test_convoke_and_improvise_carry_the_right_helper(board):
    """CR 702.51a and 702.126a differ only in what may be tapped."""
    convoke = build(KeywordInstance("Convoke"))[0]
    improvise = build(KeywordInstance("Improvise"))[0]

    assert convoke.quality.types_all == CardType.CREATURE
    assert improvise.quality.types_all == CardType.ARTIFACT


def test_out_of_format_keywords_are_named_not_silent():
    """Augment and the Conspiracy agendas cannot appear in a Commander deck.

    They stay in the registry as DECLARED so a card carrying one is reported,
    rather than being dropped and looking like full coverage.
    """
    from mtgfish.rules.keywords import Status

    for name in ("Augment", "Double agenda", "Hidden agenda"):
        spec = keywords.lookup(name)
        assert spec is not None, name
        assert spec.status is Status.DECLARED, name


def test_create_needs_a_token_to_create():
    """CR 701.6a: a token with no definition is not a token.

    An expansion that put a nameless 0/0 onto the battlefield would be worse
    than doing nothing, because the board would look plausible.
    """
    from mtgfish.rules.cr701_keyword_actions import build as build_action
    from mtgfish.rules.effects import TokenSpec
    from mtgfish.rules.query import Value

    assert build_action("Create")[0].kind is EffectKind.UNPARSED

    soldier = TokenSpec(
        name="Soldier",
        types=CardType.CREATURE,
        subtypes=("Soldier",),
        power=Value.of(1),
        toughness=Value.of(1),
    )
    created = build_action("Create", token=soldier)
    assert created[0].kind is EffectKind.CREATE_TOKEN
    assert created[0].token.name == "Soldier"


def test_every_opcode_has_an_executor():
    """The Effect IR's ceiling.

    The parser may only emit opcodes the engine executes, so an opcode without
    an executor is a promise the engine cannot keep - it would log and do
    nothing, which is the quiet failure this project exists to avoid.
    """
    from mtgfish.rules.resolve import EXECUTORS

    missing = sorted(
        kind.name
        for kind in EffectKind
        if kind is not EffectKind.UNPARSED and kind not in EXECUTORS
    )
    assert not missing, f"opcodes with no executor: {missing}"


def test_unparsed_deliberately_has_no_executor():
    """The one opcode that must never run."""
    from mtgfish.rules.resolve import EXECUTORS

    assert EffectKind.UNPARSED not in EXECUTORS


def test_protection_quality_is_carried_through(board):
    """CR 702.16b end to end: protection from red does not stop a green
    creature blocking."""
    from mtgfish.rules.cr506_combat import can_block

    red = ObjectFilter(colors_any=Color.RED)
    board.scripts.add("Serra Angel", *build(KeywordInstance("Protection", filter=red)))
    attacker = board.play("Serra Angel", controller=0)
    green_blocker = board.play("Grizzly Bears", controller=1)

    assert can_block(board.game, green_blocker, attacker)
