"""The combat keywords that change power and toughness as blocks are declared.

Each is built by ``cr702_keyword_impl`` from its CR 702 definition and played
out on a real board: the declaration fires the trigger, the stack resolves
it, and the test reads the creatures' power and toughness afterwards - so a
keyword that triggers on the wrong thing, or does more than its rule says,
shows up as a wrong number.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.rules.cr500_turn_structure.cr506_combat import (
    declare_attackers,
    declare_blockers,
)
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import (
    KeywordInstance,
    build,
)
from mtgfish.rules.kernel.ids import PlayerId, player_target


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def kw(name: str, **kwargs) -> tuple:
    return build(KeywordInstance(name, **kwargs))


def _combat(board, attackers: dict, blockers: dict) -> None:
    """Declare attackers and blockers, then resolve whatever triggered."""
    game = board.game
    game.active_player = PlayerId(0)
    game.agents[PlayerId(0)] = FixedAgent(attackers=attackers)
    game.agents[PlayerId(1)] = FixedAgent(blockers=blockers)
    declare_attackers(game)
    board.settle()
    board.resolve_stack()
    declare_blockers(game)
    board.settle()
    board.resolve_stack()
    board.refresh()


# ---------------------------------------------------------------------------
# Bushido (CR 702.45a)
# ---------------------------------------------------------------------------


def test_bushido_pumps_an_attacker_that_becomes_blocked(board):
    board.scripts.add("Grizzly Bears", *kw("Bushido", amount=2))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=1)
    _combat(board, {bears.id: 1}, {giant.id: [bears.id]})
    assert board.pt(bears) == (4, 4)
    assert board.pt(giant) == (3, 3)


def test_bushido_pumps_a_blocker(board):
    board.scripts.add("Hill Giant", *kw("Bushido", amount=1))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=1)
    _combat(board, {bears.id: 1}, {giant.id: [bears.id]})
    assert board.pt(giant) == (4, 4)
    assert board.pt(bears) == (2, 2)


def test_bushido_does_nothing_for_an_unblocked_attacker(board):
    board.scripts.add("Grizzly Bears", *kw("Bushido", amount=2))
    bears = board.play("Grizzly Bears", controller=0)
    _combat(board, {bears.id: 1}, {})
    assert board.pt(bears) == (2, 2)


# ---------------------------------------------------------------------------
# Rampage (CR 702.23a-b)
# ---------------------------------------------------------------------------


def test_rampage_counts_blockers_beyond_the_first(board):
    board.scripts.add("Hill Giant", *kw("Rampage", amount=2))
    giant = board.play("Hill Giant", controller=0)
    first = board.play("Grizzly Bears", controller=1)
    second = board.play("Grizzly Bears", controller=1)
    third = board.play("Grizzly Bears", controller=1)
    _combat(
        board,
        {giant.id: 1},
        {first.id: [giant.id], second.id: [giant.id], third.id: [giant.id]},
    )
    # Three blockers: two beyond the first, +2/+2 for each.
    assert board.pt(giant) == (7, 7)


def test_rampage_with_a_single_blocker_gives_nothing(board):
    board.scripts.add("Hill Giant", *kw("Rampage", amount=2))
    giant = board.play("Hill Giant", controller=0)
    bears = board.play("Grizzly Bears", controller=1)
    _combat(board, {giant.id: 1}, {bears.id: [giant.id]})
    assert board.pt(giant) == (3, 3)


def test_rampage_counts_the_blockers_left_when_it_resolves(board):
    """CR 702.23b: calculated once, as the ability resolves."""
    board.scripts.add("Hill Giant", *kw("Rampage", amount=1))
    giant = board.play("Hill Giant", controller=0)
    first = board.play("Grizzly Bears", controller=1)
    second = board.play("Grizzly Bears", controller=1)
    game = board.game
    game.active_player = PlayerId(0)
    game.agents[PlayerId(0)] = FixedAgent(attackers={giant.id: 1})
    game.agents[PlayerId(1)] = FixedAgent(
        blockers={first.id: [giant.id], second.id: [giant.id]}
    )
    declare_attackers(game)
    declare_blockers(game)
    board.settle()
    # One blocker leaves combat with the trigger still on the stack.
    game.combat.remove(second.id)
    board.resolve_stack()
    board.refresh()
    assert board.pt(giant) == (3, 3)


# ---------------------------------------------------------------------------
# Flanking (CR 702.25a)
# ---------------------------------------------------------------------------


def test_flanking_shrinks_each_blocker_without_flanking(board):
    board.scripts.add("Hill Giant", *kw("Flanking"))
    giant = board.play("Hill Giant", controller=0)
    first = board.play("Grizzly Bears", controller=1)
    second = board.play("Grizzly Bears", controller=1)
    _combat(board, {giant.id: 1}, {first.id: [giant.id], second.id: [giant.id]})
    assert board.pt(first) == (1, 1)
    assert board.pt(second) == (1, 1)
    # The flanker itself is untouched.
    assert board.pt(giant) == (3, 3)


def test_flanking_ignores_a_blocker_with_flanking(board):
    board.scripts.add("Hill Giant", *kw("Flanking"))
    board.scripts.add("Grizzly Bears", *kw("Flanking"))
    giant = board.play("Hill Giant", controller=0)
    bears = board.play("Grizzly Bears", controller=1)
    _combat(board, {giant.id: 1}, {bears.id: [giant.id]})
    assert board.pt(bears) == (2, 2)


def test_flanking_does_nothing_on_a_blocker(board):
    """It triggers on *this* creature becoming blocked, not on it blocking."""
    board.scripts.add("Hill Giant", *kw("Flanking"))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=1)
    _combat(board, {bears.id: 1}, {giant.id: [bears.id]})
    assert board.pt(bears) == (2, 2)
    assert board.pt(giant) == (3, 3)


# ---------------------------------------------------------------------------
# Mobilize (CR 702.181a)
# ---------------------------------------------------------------------------


def _warriors(board, controller: int = 0):
    return [
        obj
        for obj in board.game.permanents(PlayerId(controller))
        if board.chars(obj).name != "Grizzly Bears"
    ]


def _attack_in_combat(board, attackers: dict) -> None:
    from mtgfish.rules.kernel.enums import Phase

    game = board.game
    game.active_player = PlayerId(0)
    game.phase = Phase.COMBAT
    game.agents[PlayerId(0)] = FixedAgent(attackers=attackers)
    declare_attackers(game)
    board.settle()
    board.resolve_stack()
    board.refresh()


def test_mobilize_makes_tapped_attacking_warriors(board):
    board.scripts.add("Grizzly Bears", *kw("Mobilize", amount=2))
    bears = board.play("Grizzly Bears", controller=0)
    _attack_in_combat(board, {bears.id: 1})

    warriors = _warriors(board)
    assert len(warriors) == 2
    combat = board.game.combat
    for token in warriors:
        chars = board.chars(token)
        assert "Warrior" in chars.subtypes
        assert board.pt(token) == (1, 1)
        assert token.tapped
        assert combat.is_attacking(token.id)


def test_mobilize_sacrifices_the_tokens_at_the_next_end_step(board):
    from mtgfish.rules.kernel.events import Event, EventKind

    board.scripts.add("Grizzly Bears", *kw("Mobilize", amount=1))
    bears = board.play("Grizzly Bears", controller=0)
    board.play("Llanowar Elves", controller=0)
    _attack_in_combat(board, {bears.id: 1})
    assert len(_warriors(board)) == 2  # the Warrior and the Elves

    board.game.emit(Event(EventKind.END_STEP, player=PlayerId(0)))
    board.settle()
    board.resolve_stack()
    # Only the token goes: "them" is what the mobilize trigger created.
    assert board.alive(0) == ["Grizzly Bears", "Llanowar Elves"]


# ---------------------------------------------------------------------------
# Firebending (CR 702.189a)
# ---------------------------------------------------------------------------


def test_firebending_adds_red_mana_when_it_attacks(board):
    board.scripts.add("Grizzly Bears", *kw("Firebending", amount=2))
    bears = board.play("Grizzly Bears", controller=0)
    _attack_in_combat(board, {bears.id: 1})
    pool = board.game.player(PlayerId(0)).mana_pool
    assert pool.total == 2
    from mtgfish.rules.kernel.enums import Color

    assert pool.amount_of(Color.RED) == 2


def test_firebending_mana_lasts_until_end_of_combat_and_no_longer(board):
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.cr500_turn_structure.cr500_turn import _empty_mana_pools
    from mtgfish.rules.kernel.enums import Color, Step

    board.scripts.add("Grizzly Bears", *kw("Firebending", amount=1))
    bears = board.play("Grizzly Bears", controller=0)
    _attack_in_combat(board, {bears.id: 1})
    pool = board.game.player(PlayerId(0)).mana_pool
    # Ordinary mana made in the same step is lost as usual (CR 500.5).
    pool.add(ManaKind(Color.GREEN), 1)

    _empty_mana_pools(board.game, Step.DECLARE_ATTACKERS)
    assert pool.amount_of(Color.RED) == 1
    assert pool.amount_of(Color.GREEN) == 0
    _empty_mana_pools(board.game, Step.COMBAT_DAMAGE)
    assert pool.amount_of(Color.RED) == 1
    _empty_mana_pools(board.game, Step.END_OF_COMBAT)
    assert pool.total == 0


def test_firebending_mana_spends_like_red_mana(board):
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaCost, can_pay
    from mtgfish.rules.kernel.enums import Color

    board.scripts.add("Grizzly Bears", *kw("Firebending", amount=1))
    bears = board.play("Grizzly Bears", controller=0)
    _attack_in_combat(board, {bears.id: 1})
    pool = board.game.player(PlayerId(0)).mana_pool
    (kind,) = pool.buckets
    assert kind.color == Color.RED and kind.until_end_of_combat
    assert can_pay(pool, ManaCost.parse("{R}"))
    assert not can_pay(pool, ManaCost.parse("{G}"))


# ---------------------------------------------------------------------------
# Renown (CR 702.112)
# ---------------------------------------------------------------------------


def _fight(board, attackers: dict, blockers: dict | None = None) -> None:
    """Attack, block, deal combat damage, and resolve what triggered."""
    from mtgfish.rules.cr500_turn_structure.cr506_combat import deal_combat_damage

    _combat(board, attackers, blockers or {})
    deal_combat_damage(board.game)
    board.settle()
    board.resolve_stack()
    board.refresh()


def test_renown_adds_counters_and_makes_it_renowned(board):
    board.scripts.add("Grizzly Bears", *kw("Renown", amount=2))
    bears = board.play("Grizzly Bears", controller=0)
    _fight(board, {bears.id: 1})
    assert bears.counter_count("+1/+1") == 2
    assert bears.renowned
    assert board.pt(bears) == (4, 4)


def test_renown_does_nothing_once_renowned(board):
    """The intervening if: a renowned creature gets no more counters."""
    board.scripts.add("Grizzly Bears", *kw("Renown", amount=1))
    bears = board.play("Grizzly Bears", controller=0)
    bears.renowned = True
    _fight(board, {bears.id: 1})
    assert bears.counter_count("+1/+1") == 0


def test_two_renown_instances_only_the_first_resolves(board):
    """CR 702.112c: both trigger; the first to resolve makes it renowned and
    the second then does nothing - so 1 or 2 counters, never 3."""
    board.scripts.add(
        "Grizzly Bears", *kw("Renown", amount=1), *kw("Renown", amount=2)
    )
    bears = board.play("Grizzly Bears", controller=0)
    _fight(board, {bears.id: 1})
    assert bears.counter_count("+1/+1") in (1, 2)
    assert bears.renowned


def test_renown_needs_combat_damage_to_a_player(board):
    """Blocked, it deals its combat damage to a creature: no renown."""
    board.scripts.add("Grizzly Bears", *kw("Renown", amount=1))
    bears = board.play("Grizzly Bears", controller=0)
    wall = board.play("Wall of Stone", controller=1)
    _fight(board, {bears.id: 1}, {wall.id: [bears.id]})
    assert bears.counter_count("+1/+1") == 0
    assert not bears.renowned


def test_renowned_is_a_designation_not_a_counter(board):
    """CR 702.112b: nothing that counts counters sees it."""
    board.scripts.add("Grizzly Bears", *kw("Renown", amount=1))
    bears = board.play("Grizzly Bears", controller=0)
    _fight(board, {bears.id: 1})
    assert {k for k, n in bears.counters.items() if n} == {"+1/+1"}


def test_becoming_renowned_triggers_when_this_becomes_renowned(board):
    """Relic Seeker's "When this creature becomes renowned" watches the
    designation, which the renown ability sets."""
    from mtgfish.parser.tokens import Stream
    from mtgfish.parser.triggers import parse_trigger
    from mtgfish.rules.cr600_spells_and_abilities.cr603_triggers import condition_met
    from mtgfish.rules.kernel.events import Event, EventKind

    trigger = parse_trigger(
        Stream.of("When this creature becomes renowned, draw a card.")
    )
    bears = board.play("Grizzly Bears", controller=0)
    event = Event(EventKind.BECAME_RENOWNED, object_id=bears.id, player=bears.controller)
    assert condition_met(board.game, bears, trigger, event)
    counters = Event(EventKind.COUNTER_ADDED, object_id=bears.id, player=bears.controller)
    assert not condition_met(board.game, bears, trigger, counters)


# ---------------------------------------------------------------------------
# Frenzy (CR 702.68)
# ---------------------------------------------------------------------------


def test_frenzy_pumps_power_of_an_unblocked_attacker(board):
    """CR 702.68a: +N/+0 - power only."""
    board.scripts.add("Grizzly Bears", *kw("Frenzy", amount=2))
    bears = board.play("Grizzly Bears", controller=0)
    _combat(board, {bears.id: 1}, {})
    assert board.pt(bears) == (4, 2)


def test_frenzy_does_nothing_when_blocked(board):
    board.scripts.add("Grizzly Bears", *kw("Frenzy", amount=2))
    bears = board.play("Grizzly Bears", controller=0)
    wall = board.play("Wall of Stone", controller=1)
    _combat(board, {bears.id: 1}, {wall.id: [bears.id]})
    assert board.pt(bears) == (2, 2)


def test_frenzy_does_not_trigger_on_attacking_alone(board):
    """It waits for blockers: declaring the attack is not enough."""
    board.scripts.add("Grizzly Bears", *kw("Frenzy", amount=2))
    bears = board.play("Grizzly Bears", controller=0)
    _attack_in_combat(board, {bears.id: 1})
    assert board.pt(bears) == (2, 2)


def test_frenzy_instances_trigger_separately(board):
    """CR 702.68b."""
    board.scripts.add("Grizzly Bears", *kw("Frenzy", amount=1), *kw("Frenzy", amount=2))
    bears = board.play("Grizzly Bears", controller=0)
    _combat(board, {bears.id: 1}, {})
    assert board.pt(bears) == (5, 2)


def test_frenzy_only_pumps_its_own_creature(board):
    board.scripts.add("Grizzly Bears", *kw("Frenzy", amount=2))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=0)
    _combat(board, {bears.id: 1, giant.id: 1}, {})
    assert board.pt(giant) == (3, 3)


# ---------------------------------------------------------------------------
# Increment (CR 702.191)
# ---------------------------------------------------------------------------


def _cast(board, name: str, controller: int = 0, targets=()) -> None:
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
    from mtgfish.rules.kernel.enums import Color

    card = board.hand(name, controller=controller)
    player = PlayerId(controller)
    board.game.player(player).mana_pool.add(ManaKind(Color.BLUE), 3)
    board.game.player(player).mana_pool.add(ManaKind(Color.RED), 1)
    cast_spell(
        board.game, player, Action(ActionKind.CAST_SPELL, source=card.id, targets=targets)
    )
    board.settle()
    board.resolve_stack()
    board.refresh()


def test_increment_counts_a_spell_costing_more_than_its_power_and_toughness(board):
    board.scripts.add("Grizzly Bears", *kw("Increment"))
    bears = board.play("Grizzly Bears", controller=0)
    _cast(board, "Divination")
    assert bears.counter_count("+1/+1") == 1


def test_increment_ignores_a_cheap_spell(board):
    board.scripts.add("Grizzly Bears", *kw("Increment"))
    bears = board.play("Grizzly Bears", controller=0)
    _cast(board, "Lightning Bolt", targets=((player_target(PlayerId(1)),),))
    assert bears.counter_count("+1/+1") == 0


def test_increment_needs_only_power_or_toughness_to_be_exceeded(board):
    """Wall of Stone is 0/8: one mana is more than its power."""
    board.scripts.add("Wall of Stone", *kw("Increment"))
    wall = board.play("Wall of Stone", controller=0)
    _cast(board, "Lightning Bolt", targets=((player_target(PlayerId(1)),),))
    assert wall.counter_count("+1/+1") == 1


def test_increment_ignores_an_opponents_spell(board):
    board.scripts.add("Grizzly Bears", *kw("Increment"))
    bears = board.play("Grizzly Bears", controller=0)
    board.game.active_player = PlayerId(1)
    _cast(board, "Divination", controller=1)
    assert bears.counter_count("+1/+1") == 0


def test_increment_rechecks_as_it_resolves(board):
    """CR 603.4: two instances both trigger on a three-mana spell, but after
    the first a 3/3 is no longer smaller than three - the second does
    nothing."""
    board.scripts.add("Grizzly Bears", *kw("Increment"), *kw("Increment"))
    bears = board.play("Grizzly Bears", controller=0)
    _cast(board, "Divination")
    assert bears.counter_count("+1/+1") == 1


# ---------------------------------------------------------------------------
# Provoke (CR 702.39)
# ---------------------------------------------------------------------------


class _Declines(FixedAgent):
    def choose_optional(self, game, player, effect):
        return False


def test_provoke_untaps_the_target_and_makes_it_block(board):
    """CR 702.39a: the defending player declares no blocks, but the provoked
    creature - tapped until the trigger untapped it - must block."""
    board.scripts.add("Grizzly Bears", *kw("Provoke"))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=1, tapped=True)
    _combat(board, {bears.id: 1}, {})
    assert not giant.tapped
    assert board.game.combat.blocking.get(giant.id) == [bears.id]


def test_provoke_makes_it_block_this_creature_not_another(board):
    board.scripts.add("Grizzly Bears", *kw("Provoke"))
    bears = board.play("Grizzly Bears", controller=0)
    other = board.play("Hill Giant", controller=0)
    giant = board.play("Hill Giant", controller=1)
    _combat(board, {bears.id: 1, other.id: 1}, {giant.id: [other.id]})
    assert board.game.combat.blocking.get(giant.id) == [bears.id]


def test_provoke_declined_does_nothing(board):
    board.scripts.add("Grizzly Bears", *kw("Provoke"))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=1, tapped=True)
    game = board.game
    game.active_player = PlayerId(0)
    game.agents[PlayerId(0)] = _Declines(attackers={bears.id: 1})
    game.agents[PlayerId(1)] = FixedAgent()
    declare_attackers(game)
    board.settle()
    board.resolve_stack()
    assert giant.tapped
    assert not game.standing_requirements


def test_provoke_lasts_only_this_combat(board):
    from mtgfish.rules.cr600_spells_and_abilities.cr611_durations import (
        expire_at_end_of_combat,
    )

    board.scripts.add("Grizzly Bears", *kw("Provoke"))
    bears = board.play("Grizzly Bears", controller=0)
    board.play("Hill Giant", controller=1)
    _combat(board, {bears.id: 1}, {})
    assert board.game.standing_requirements
    expire_at_end_of_combat(board.game)
    assert not board.game.standing_requirements


def test_provoke_targets_only_the_defending_players_creatures(card_db):
    """"Target creature defending player controls": with two opponents, a
    creature of the one not being attacked is not a legal target."""
    from mtgfish.rules.kernel.matching import matches
    from mtgfish.rules.kernel.query import ControllerRelation, ObjectFilter

    board = make_board(card_db, ScriptedAbilities(), players=3)
    bears = board.play("Grizzly Bears", controller=0)
    attacked = board.play("Hill Giant", controller=1)
    bystander = board.play("Hill Giant", controller=2)
    game = board.game
    game.active_player = PlayerId(0)
    game.agents[PlayerId(0)] = FixedAgent(attackers={bears.id: 1})
    declare_attackers(game)
    spec = ObjectFilter(controller=ControllerRelation.DEFENDING_PLAYER)
    kwargs = dict(source=bears.id, controller=PlayerId(0))
    assert matches(game, attacked, spec, **kwargs)
    assert not matches(game, bystander, spec, **kwargs)


# ---------------------------------------------------------------------------
# Enlist (CR 702.154)
# ---------------------------------------------------------------------------


class _Enlister(FixedAgent):
    """Enlists ``choice`` and records what it was offered."""

    def __init__(self, choice=None, **kwargs):
        super().__init__(**kwargs)
        self.choice = choice
        self.offered: list = []

    def choose_enlist(self, game, player, attacker, eligible):
        self.offered.append(sorted(obj.id for obj in eligible))
        return self.choice


def _enlist_attack(board, agent, *, resolve: bool = True) -> None:
    game = board.game
    game.active_player = PlayerId(0)
    game.agents[PlayerId(0)] = agent
    declare_attackers(game)
    board.settle()
    if resolve:
        board.resolve_stack()
        board.refresh()


def test_enlist_taps_a_creature_and_adds_its_power(board):
    """CR 702.154a: +X/+0, X the tapped creature's power."""
    board.scripts.add("Grizzly Bears", *kw("Enlist"))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=0)
    agent = _Enlister(giant.id, attackers={bears.id: 1})
    _enlist_attack(board, agent)
    assert giant.tapped
    assert giant.id not in board.game.combat.attacking
    assert board.pt(bears) == (5, 2)
    assert board.pt(giant) == (3, 3)


def test_enlist_is_optional(board):
    board.scripts.add("Grizzly Bears", *kw("Enlist"))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=0)
    _enlist_attack(board, _Enlister(None, attackers={bears.id: 1}))
    assert not giant.tapped
    assert board.pt(bears) == (2, 2)


def test_enlist_offers_only_untapped_non_attackers_that_could_have_attacked(board):
    """Not an attacker, not tapped, and not summoning sick unless it has
    haste (CR 702.154a)."""
    from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind

    board.scripts.add("Grizzly Bears", *kw("Enlist"))
    board.scripts.add("Raging Goblin", Ability(AbilityKind.STATIC, keyword="Haste"))
    bears = board.play("Grizzly Bears", controller=0)
    other_attacker = board.play("Hill Giant", controller=0)
    home = board.play("Hill Giant", controller=0)
    board.play("Hill Giant", controller=0, tapped=True)
    board.play("Hill Giant", controller=0, summoning_sick=True)
    hasty = board.play("Raging Goblin", controller=0, summoning_sick=True)
    board.play("Hill Giant", controller=1)
    agent = _Enlister(None, attackers={bears.id: 1, other_attacker.id: 1})
    _enlist_attack(board, agent)
    assert agent.offered == [sorted([home.id, hasty.id])]


def test_enlist_uses_the_power_as_the_trigger_resolves(board):
    """CR 608.2h: X is read on resolution, not when the cost is paid."""
    board.scripts.add("Grizzly Bears", *kw("Enlist"))
    bears = board.play("Grizzly Bears", controller=0)
    giant = board.play("Hill Giant", controller=0)
    _enlist_attack(board, _Enlister(giant.id, attackers={bears.id: 1}), resolve=False)
    giant.add_counters("+1/+1", 2)
    board.refresh()
    board.resolve_stack()
    board.refresh()
    assert board.pt(bears) == (7, 2)


def test_enlist_without_an_enlist_ability_is_never_offered(board):
    bears = board.play("Grizzly Bears", controller=0)
    board.play("Hill Giant", controller=0)
    agent = _Enlister(None, attackers={bears.id: 1})
    _enlist_attack(board, agent)
    assert agent.offered == []
