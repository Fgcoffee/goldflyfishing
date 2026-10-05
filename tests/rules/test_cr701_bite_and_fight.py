"""A target that acts: "target creature you control deals damage equal to its
power to ..." and "... fights ...".

The first target is the damage's source (CR 120.3) and the owner of "its
power"; it is chosen as a target of its own (CR 115.1) by a DESIGNATE
instruction, and the damage or the fight names what was remembered of it.
CR 608.2b: when either target has become illegal, the spell still resolves
if the other is legal, but nothing can be determined about the illegal one -
no damage is dealt by it, and none is dealt to it. CR 701.14b: if either
creature told to fight is an illegal target, neither deals damage.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mtgfish.parser.compile import parse_card
from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import targeted_nodes
from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind
from mtgfish.rules.kernel.enums import Color, Zone
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def _need(box, *names):
    for name in names:
        if box.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")


def _mana(box, player, amount, color=None):
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind

    box.game.player(player).mana_pool.add(ManaKind(color or Color.NONE), amount)


def _creature(box, name, controller):
    before = set(box.game.objects)
    box.put(name, "battlefield", controller)
    return next(
        box.game.objects[oid]
        for oid in box.game.objects
        if oid not in before
        and box.game.objects[oid].zone is Zone.BATTLEFIELD
        and box.game.objects[oid].card.name == name
    )


def _cast_action(box, player, name):
    game = box.game
    return next(
        (
            action
            for action in legal_actions(game, player)
            if action.kind.name.startswith("CAST")
            and game.objects[action.source].card.name == name
        ),
        None,
    )


def _cast(box, player, name, targets, **changes):
    action = _cast_action(box, player, name)
    assert action is not None, f"{name} is not castable"
    return _perform(box.game, player, replace(action, targets=targets, **changes))


def _alive(obj) -> bool:
    return obj.zone is Zone.BATTLEFIELD and not obj.superseded_by


def _spell(db, name):
    face = parse_card(db.lookup(name)).faces[0]
    spells = [a for a in face.abilities if a.kind.name == "SPELL"]
    assert spells and not any(a.unparsed for a in spells), face.failures
    return spells[0]


def _bite(box, name="Rabid Bite"):
    _need(box, name, "Hill Giant", "Grizzly Bears")
    giant = _creature(box, "Hill Giant", 0)  # 3/3
    bears = _creature(box, "Grizzly Bears", 1)  # 2/2
    box.put(name, "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.GREEN if name == "Rabid Bite" else Color.RED)
    return giant, bears


# ---------------------------------------------------------------------------
# Bite
# ---------------------------------------------------------------------------


def test_bite_has_two_targets_and_the_first_deals_the_damage(box):
    _need(box, "Rabid Bite")
    ability = _spell(box.db, "Rabid Bite")
    designate, damage = targeted_nodes(ability.effects)
    assert designate.kind is EffectKind.DESIGNATE
    assert damage.kind is EffectKind.DAMAGE
    assert damage.damage_source is not None and damage.damage_source.remembered


def test_rabid_bite_deals_the_first_targets_power_to_the_second(box):
    giant, bears = _bite(box)
    assert _cast(box, 0, "Rabid Bite", ((giant.id,), (bears.id,)))
    box.resolve_top()
    assert not _alive(bears)
    assert _alive(giant) and giant.damage == 0


def test_bite_the_damage_is_dealt_by_the_creature(box):
    """CR 120.3f: the creature's lifelink applies - it is the source."""
    _need(box, "Rabid Bite", "Grizzly Bears", "Vampire Nighthawk")
    nighthawk = _creature(box, "Vampire Nighthawk", 0)  # 2/3 lifelink deathtouch
    bears = _creature(box, "Grizzly Bears", 1)
    box.put("Rabid Bite", "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.GREEN)
    life = box.game.player(0).life
    assert _cast(box, 0, "Rabid Bite", ((nighthawk.id,), (bears.id,)))
    box.resolve_top()
    assert not _alive(bears)
    assert box.game.player(0).life == life + 2


def test_bite_with_its_creature_gone_deals_no_damage(box):
    """CR 608.2b: the power of an illegal target can't be determined, and
    the damage that needs it isn't dealt; the other target keeps the spell
    resolving, and no other object stands in as the dealer."""
    giant, bears = _bite(box)
    assert _cast(box, 0, "Rabid Bite", ((giant.id,), (bears.id,)))
    actions.bounce(box.game, giant)
    box.resolve_top()
    assert _alive(bears) and bears.damage == 0
    assert not box.game.stack


def test_bite_at_a_creature_that_is_gone_deals_no_damage(box):
    """CR 608.2b: an illegal target isn't affected - and the damage goes
    nowhere else."""
    giant, bears = _bite(box)
    hound = _creature(box, "Grizzly Bears", 1)
    life = box.game.player(1).life
    assert _cast(box, 0, "Rabid Bite", ((giant.id,), (bears.id,)))
    actions.bounce(box.game, bears)
    box.resolve_top()
    assert _alive(giant) and giant.damage == 0
    assert _alive(hound) and hound.damage == 0
    assert box.game.player(1).life == life


def test_another_target_creature_must_differ_from_the_biter(box):
    """Fall of the Hammer: "another target creature" after the first target
    is other than that target (CR 115.3, 601.2c)."""
    _need(box, "Fall of the Hammer", "Hill Giant", "Grizzly Bears")
    giant = _creature(box, "Hill Giant", 0)
    box.put("Fall of the Hammer", "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.RED)
    assert _cast_action(box, 0, "Fall of the Hammer") is None
    bears = _creature(box, "Grizzly Bears", 0)
    assert not _cast(box, 0, "Fall of the Hammer", ((giant.id,), (giant.id,)))
    # Your own creature is "another target creature" too.
    assert _cast(box, 0, "Fall of the Hammer", ((giant.id,), (bears.id,)))
    box.resolve_top()
    assert not _alive(bears)
    assert giant.damage == 0


@pytest.mark.parametrize(
    "name",
    [
        # "each of two other target creatures", "each of X target creatures
        # and/or planeswalkers": several targets in one slot, a number the
        # engine does not hold the choice to.
        "Betrayal at the Vault",
        "Spinning Wheel Kick",
        # "each other creature and each opponent": an untargeted "other"
        # after the biter means other than the biter.
        "Chandra's Ignition",
        # "that player" is the first target's controller.
        "Mutiny",
    ],
)
def test_bites_the_engine_cannot_place_stay_unread(box, name):
    _need(box, name)
    face = parse_card(box.db.lookup(name)).faces[0]
    assert any(a.unparsed for a in face.abilities)


# ---------------------------------------------------------------------------
# Fight
# ---------------------------------------------------------------------------


def _prey(box):
    _need(box, "Prey Upon", "Hill Giant", "Grizzly Bears")
    giant = _creature(box, "Hill Giant", 0)
    bears = _creature(box, "Grizzly Bears", 1)
    box.put("Prey Upon", "hand", 0)
    _mana(box, 0, 1, Color.GREEN)
    return giant, bears


def test_prey_upon_both_creatures_deal_damage(box):
    giant, bears = _prey(box)
    assert _cast(box, 0, "Prey Upon", ((giant.id,), (bears.id,)))
    box.resolve_top()
    assert not _alive(bears)
    assert _alive(giant) and giant.damage == 2


@pytest.mark.parametrize("gone", ["fighter", "opponent"])
def test_a_fight_with_an_illegal_target_deals_no_damage(box, gone):
    """CR 701.14b: neither creature fights."""
    giant, bears = _prey(box)
    assert _cast(box, 0, "Prey Upon", ((giant.id,), (bears.id,)))
    actions.bounce(box.game, giant if gone == "fighter" else bears)
    box.resolve_top()
    survivor = bears if gone == "fighter" else giant
    assert _alive(survivor) and survivor.damage == 0


def test_primal_might_pumps_then_it_fights(box):
    _need(box, "Primal Might", "Grizzly Bears", "Hill Giant")
    bears = _creature(box, "Grizzly Bears", 0)
    giant = _creature(box, "Hill Giant", 1)
    box.put("Primal Might", "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.GREEN)
    assert _cast(box, 0, "Primal Might", ((bears.id,), (giant.id,)), x_value=1)
    box.resolve_top()
    # 3/3 Bears and the 3/3 Giant trade.
    assert not _alive(giant)
    assert not _alive(bears)


def test_primal_might_with_no_creature_to_fight_still_pumps(box):
    _need(box, "Primal Might", "Grizzly Bears")
    bears = _creature(box, "Grizzly Bears", 0)
    box.put("Primal Might", "hand", 0)
    _mana(box, 0, 2)
    _mana(box, 0, 1, Color.GREEN)
    assert _cast(box, 0, "Primal Might", ((bears.id,), ()), x_value=2)
    box.resolve_top()
    chars = box.game.characteristics(bears)
    assert (chars.power, chars.toughness) == (4, 4)
    assert bears.damage == 0


@pytest.mark.parametrize(
    "name",
    [
        # "... target creature you control if it's legendary. Then it
        # fights ...": the fighter is remembered only if the condition held,
        # and the fight happens either way.
        "Ancient Animus",
        "Savage Swipe",
        # "Up to one target creature you control fights ...": with none
        # chosen there is no fighter to name.
        "Earth Rumble",
    ],
)
def test_fights_the_engine_cannot_place_stay_unread(box, name):
    _need(box, name)
    face = parse_card(box.db.lookup(name)).faces[0]
    assert any(a.unparsed for a in face.abilities)


def test_this_creature_fights_names_the_source(box):
    _need(box, "Kogla, the Titan Ape")
    face = parse_card(box.db.lookup("Kogla, the Titan Ape")).faces[0]
    fights = [
        node
        for ability in face.abilities
        for top in ability.effects
        for node in top.walk()
        if node.kind is EffectKind.FIGHT
    ]
    assert len(fights) == 1
    assert fights[0].damage_source is not None and fights[0].damage_source.source_only
