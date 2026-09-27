"""Replacement and prevention effects catch the events their words name (CR 614, 615).

Each family of event names its participants differently - damage has a source
and a recipient, counters a kind and a recipient, tokens a kind and a
controller, life a player - and the replacement layer read all of them with
one generic test against "the object of the event". For damage that object is
the permanent being *damaged*, so Fiery Emancipation, which triples the damage
its controller's sources deal, tripled the damage dealt *to* its controller's
creatures and nothing dealt to a player. These tests are on real boards, with
the cards' own parsed text.
"""

from __future__ import annotations

import pytest

from mtgfish.parser import parse_card
from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr600_spells_and_abilities.cr611_durations import expire_at_cleanup
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.ui.sandbox import Sandbox

ME, THEM = PlayerId(0), PlayerId(1)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


def _on(box, name, player=0):
    box.put(name, "battlefield", player)
    return next(
        obj
        for obj in reversed(list(box.game.objects.values()))
        if obj.card is not None
        and obj.card.name == name
        and obj.zone.name == "BATTLEFIELD"
    )


def _resolve(box, name, *, controller=ME, targets=(), source=None, ability=0):
    """Carry out a card's spell text (or one ability's effects) directly."""
    card = box.db.lookup(name)
    parsed = parse_card(card)
    assert parsed.fully_parsed, [f.reason for f in parsed.failures]
    effects = parsed.faces[0].abilities[ability].effects
    execute(
        Resolution(
            game=box.game,
            source=source.id if source is not None else 0,
            controller=controller,
            targets=tuple(targets),
        ),
        effects,
    )


def _life(box, player):
    return box.game.player(player).life


# ---------------------------------------------------------------------------
# Damage: source and recipient are different objects
# ---------------------------------------------------------------------------


def test_a_damage_multiplier_reads_the_source_not_the_recipient(box):
    box.put("Fiery Emancipation", "battlefield", 0)
    mine = _on(box, "Grizzly Bears", 0)
    theirs = _on(box, "Hill Giant", 1)

    before = _life(box, THEM)
    actions.deal_damage(box.game, THEM, 2, source=mine.id, source_controller=ME)
    assert before - _life(box, THEM) == 6

    actions.deal_damage(box.game, mine, 1, source=theirs.id, source_controller=THEM)
    assert mine.damage == 1, "damage dealt to my creature is not mine to triple"


def test_a_damage_doubler_to_you_ignores_damage_to_creatures(box):
    """Goldnight Castigator: "If a source would deal damage to you, it deals
    double that damage to you instead" - not to your creatures."""
    box.put("Goldnight Castigator", "battlefield", 0)
    bear = _on(box, "Grizzly Bears", 0)
    giant = _on(box, "Hill Giant", 1)
    before = _life(box, ME)
    actions.deal_damage(box.game, ME, 2, source=giant.id, source_controller=THEM)
    assert before - _life(box, ME) == 4
    actions.deal_damage(box.game, bear, 1, source=giant.id, source_controller=THEM)
    assert bear.damage == 1


def test_combat_only_doubling_leaves_noncombat_damage_alone(box):
    """Charging Tuskodon doubles its combat damage to a player, nothing else."""
    tusk = _on(box, "Charging Tuskodon", 0)
    before = _life(box, THEM)
    actions.deal_damage(box.game, THEM, 3, source=tusk.id, source_controller=ME)
    assert before - _life(box, THEM) == 3
    actions.deal_damage(box.game, THEM, 3, source=tusk.id, source_controller=ME, combat=True)
    assert before - _life(box, THEM) == 3 + 6


# ---------------------------------------------------------------------------
# Counters: the kind named is the kind caught
# ---------------------------------------------------------------------------


def test_a_named_counter_kind_is_the_only_kind_modified(box):
    box.put("Hardened Scales", "battlefield", 0)
    giant = _on(box, "Hill Giant", 0)
    actions.add_counters(box.game, giant, "-1/-1", 1, source=giant.id)
    assert giant.counter_count("-1/-1") == 1
    actions.add_counters(box.game, giant, "+1/+1", 1, source=giant.id)
    assert giant.counter_count("+1/+1") == 2


def test_minus_one_is_subtracted(box):
    box.put("Vizier of Remedies", "battlefield", 0)
    giant = _on(box, "Hill Giant", 0)
    actions.add_counters(box.game, giant, "-1/-1", 2, source=giant.id)
    assert giant.counter_count("-1/-1") == 1


def test_an_effect_excludes_counters_put_as_a_cost(box):
    """Doubling Season: "If an *effect* would put one or more counters"."""
    box.put("Doubling Season", "battlefield", 0)
    giant = _on(box, "Hill Giant", 0)
    actions.add_counters(box.game, giant, "+1/+1", 1)  # a cost: no source
    assert giant.counter_count("+1/+1") == 1
    actions.add_counters(box.game, giant, "+1/+1", 1, source=giant.id)
    assert giant.counter_count("+1/+1") == 3


# ---------------------------------------------------------------------------
# Life: gain and loss are different events
# ---------------------------------------------------------------------------


def test_a_life_gain_doubler_does_not_double_loss(box):
    box.put("Rhox Faithmender", "battlefield", 0)
    before = _life(box, ME)
    actions.lose_life(box.game, ME, 2)
    assert before - _life(box, ME) == 2
    actions.gain_life(box.game, ME, 3)
    assert _life(box, ME) - (before - 2) == 6


def test_plus_one_life_is_added(box):
    box.put("Angel of Vitality", "battlefield", 0)
    before = _life(box, ME)
    actions.gain_life(box.game, ME, 2)
    assert _life(box, ME) - before == 3


def test_a_life_loss_doubler_applies_only_during_its_turn(box):
    """Bloodletter of Aclazotz, including the life loss damage causes
    (CR 120.3a) - which changes the life lost, not the damage dealt."""
    box.put("Bloodletter of Aclazotz", "battlefield", 0)
    box.game.active_player = ME
    before = _life(box, THEM)
    actions.lose_life(box.game, THEM, 2)
    assert before - _life(box, THEM) == 4
    box.game.active_player = THEM
    before = _life(box, THEM)
    actions.lose_life(box.game, THEM, 2)
    assert before - _life(box, THEM) == 2


def test_life_gain_can_become_loss(box):
    box.put("Tainted Remedy", "battlefield", 0)
    before = _life(box, THEM)
    actions.gain_life(box.game, THEM, 3)
    assert before - _life(box, THEM) == 3


# ---------------------------------------------------------------------------
# Tokens: whose, and which
# ---------------------------------------------------------------------------


def test_a_creature_token_multiplier_leaves_other_tokens_alone(box):
    from mtgfish.rules.cr100_game_concepts.cr111_tokens import create_tokens
    from mtgfish.rules.cr600_spells_and_abilities.effects import TokenSpec
    from mtgfish.rules.kernel.enums import CardType

    box.put("Ojer Taq, Deepest Foundation // Temple of Civilization", "battlefield", 0)
    soldiers = create_tokens(
        box.game, TokenSpec(name="Soldier", types=CardType.CREATURE, subtypes=("Soldier",)), ME, 1
    )
    treasures = create_tokens(
        box.game, TokenSpec(name="Treasure", types=CardType.ARTIFACT, subtypes=("Treasure",)), ME, 1
    )
    assert (len(soldiers), len(treasures)) == (3, 1)


def test_tokens_for_any_player_when_the_card_says_no_controller(box):
    from mtgfish.rules.cr100_game_concepts.cr111_tokens import create_tokens
    from mtgfish.rules.cr600_spells_and_abilities.effects import TokenSpec
    from mtgfish.rules.kernel.enums import CardType

    box.put("Primal Vigor", "battlefield", 0)
    spec = TokenSpec(name="Soldier", types=CardType.CREATURE, subtypes=("Soldier",))
    assert len(create_tokens(box.game, spec, THEM, 1)) == 2


# ---------------------------------------------------------------------------
# Prevention: static shields, durations, and what a shield protects
# ---------------------------------------------------------------------------


def test_a_static_prevention_ability_prevents(box):
    """Glacial Chasm's "Prevent all damage that would be dealt to you" was
    parsed, counted as read, and consulted by nothing."""
    box.put("Glacial Chasm", "battlefield", 0)
    bear = _on(box, "Grizzly Bears", 0)
    giant = _on(box, "Hill Giant", 1)
    before = _life(box, ME)
    actions.deal_damage(box.game, ME, 3, source=giant.id, source_controller=THEM)
    assert _life(box, ME) == before
    actions.deal_damage(box.game, bear, 1, source=giant.id, source_controller=THEM)
    assert bear.damage == 1, "you, not your creatures"


def test_a_fog_ends_with_the_turn(box):
    giant = _on(box, "Hill Giant", 1)
    _resolve(box, "Fog")
    before = _life(box, ME)
    actions.deal_damage(box.game, ME, 3, source=giant.id, source_controller=THEM, combat=True)
    assert _life(box, ME) == before
    actions.deal_damage(box.game, ME, 1, source=giant.id, source_controller=THEM)
    assert _life(box, ME) == before - 1, "combat damage only"
    expire_at_cleanup(box.game)
    actions.deal_damage(box.game, ME, 3, source=giant.id, source_controller=THEM, combat=True)
    assert _life(box, ME) == before - 4


def test_a_targeted_shield_protects_only_its_target(box):
    one = _on(box, "Grizzly Bears", 0)
    two = _on(box, "Grizzly Bears", 0)
    giant = _on(box, "Hill Giant", 1)
    _resolve(box, "Shielded Passage", targets=((one.id,),))
    actions.deal_damage(box.game, one, 2, source=giant.id, source_controller=THEM)
    actions.deal_damage(box.game, two, 2, source=giant.id, source_controller=THEM)
    assert (one.damage, two.damage) == (0, 2)


def test_a_sized_shield_is_spent_across_events(box):
    bear = _on(box, "Grizzly Bears", 0)
    giant = _on(box, "Hill Giant", 1)
    _resolve(box, "Anoint", targets=((bear.id,),), ability=-1)  # the next 3 damage
    actions.deal_damage(box.game, bear, 2, source=giant.id, source_controller=THEM)
    actions.deal_damage(box.game, bear, 2, source=giant.id, source_controller=THEM)
    assert bear.damage == 1


def test_dealt_to_and_dealt_by_runs_both_ways(box):
    """Deftblade Elite's shield (Maze of Ith's wording) stops its own damage
    as well as the damage dealt to it."""
    elite = _on(box, "Deftblade Elite", 1)
    blocker = _on(box, "Grizzly Bears", 0)
    _resolve(box, "Deftblade Elite", controller=THEM, source=elite, ability=-1)
    before = _life(box, ME)
    actions.deal_damage(box.game, ME, 1, source=elite.id, source_controller=THEM, combat=True)
    assert _life(box, ME) == before
    actions.deal_damage(box.game, elite, 2, source=blocker.id, source_controller=ME, combat=True)
    assert elite.damage == 0
    actions.deal_damage(box.game, blocker, 1, source=elite.id, source_controller=THEM)
    assert blocker.damage == 1, "combat damage only"


def test_any_target_prevention_can_protect_a_player(box):
    giant = _on(box, "Hill Giant", 1)
    _resolve(box, "Hold at Bay", targets=((-1 - int(ME),),))
    before = _life(box, ME)
    actions.deal_damage(box.game, ME, 9, source=giant.id, source_controller=THEM)
    assert before - _life(box, ME) == 2


# ---------------------------------------------------------------------------
# Replacements made by a resolving ability
# ---------------------------------------------------------------------------


def test_a_resolving_replacement_is_registered_and_expires(box):
    """Kaya, Geist Hunter's -2: "Until end of turn, if one or more tokens
    would be created under your control, twice that many ... instead"."""
    from mtgfish.rules.cr100_game_concepts.cr111_tokens import create_tokens
    from mtgfish.rules.cr600_spells_and_abilities.effects import TokenSpec
    from mtgfish.rules.kernel.enums import CardType

    kaya = _on(box, "Kaya, Geist Hunter", 0)
    _resolve(box, "Kaya, Geist Hunter", source=kaya, ability=1)
    spec = TokenSpec(name="Spirit", types=CardType.CREATURE, subtypes=("Spirit",))
    assert len(create_tokens(box.game, spec, ME, 1)) == 2
    expire_at_cleanup(box.game)
    assert len(create_tokens(box.game, spec, ME, 1)) == 1
