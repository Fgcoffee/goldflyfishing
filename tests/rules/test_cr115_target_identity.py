"""Which object each word "target" names (CR 115.3, 601.2c, 608.2b).

* One word "target" governing two verbs - "target creature gets +3/+0 and
  gains first strike" - is one target. It was read as two instructions that
  each held a target of their own, so the spell asked for two choices and
  could pump one creature and give first strike to another.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mtgfish.parser.compile import parse_card
from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import targeted_nodes
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
    """Put a creature onto the battlefield and return its object."""
    before = set(box.game.objects)
    box.put(name, "battlefield", controller)
    return next(
        box.game.objects[oid]
        for oid in box.game.objects
        if oid not in before
        and box.game.objects[oid].zone is Zone.BATTLEFIELD
        and box.game.objects[oid].card.name == name
    )


def _cast(box, player, name, targets):
    game = box.game
    action = next(
        action
        for action in legal_actions(game, player)
        if action.kind.name.startswith("CAST")
        and game.objects[action.source].card.name == name
    )
    assert _perform(game, player, replace(action, targets=targets))


def _castable(box, player, name) -> bool:
    game = box.game
    return any(
        action.kind.name.startswith("CAST")
        and game.objects[action.source].card.name == name
        for action in legal_actions(game, player)
    )


def _spell(db, name):
    face = parse_card(db.lookup(name)).faces[0]
    spells = [a for a in face.abilities if a.kind.name == "SPELL"]
    assert spells and not any(a.unparsed for a in spells), face.failures
    return spells[0]


def _alive(game, obj) -> bool:
    return obj.zone is Zone.BATTLEFIELD and not obj.superseded_by


# ---------------------------------------------------------------------------
# One "target", two verbs
# ---------------------------------------------------------------------------


def test_one_target_governing_two_verbs_is_one_target(box):
    _need(box, "Sure Strike")
    ability = _spell(box.db, "Sure Strike")
    nodes = [n for e in ability.effects for n in e.walk() if n.is_targeted]
    assert len(nodes) == 2
    assert len(targeted_nodes(ability.effects)) == 1
    assert nodes[1].same_target


def test_both_verbs_act_on_the_one_creature_chosen(box):
    _need(box, "Sure Strike", "Grizzly Bears", "Hill Giant")
    game = box.game
    bears = _creature(box, "Grizzly Bears", 0)
    giant = _creature(box, "Hill Giant", 0)
    box.put("Sure Strike", "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.RED)

    # A second slot would be ignored: the spell asks for one target.
    _cast(box, 0, "Sure Strike", ((bears.id,), (giant.id,)))
    spell = game.objects[game.stack[-1]]
    assert spell.targets[:1] == ((bears.id,),)
    box.resolve_top()

    bears_chars = game.characteristics(bears)
    assert (bears_chars.power, bears_chars.toughness) == (5, 2)
    assert bears_chars.has_keyword("First strike")
    giant_chars = game.characteristics(giant)
    assert (giant_chars.power, giant_chars.toughness) == (3, 3)
    assert not giant_chars.has_keyword("First strike")


def test_two_separate_actions_are_two_targets(box):
    """"Earthbend 3, then earthbend 3" says "target land" twice (CR 701.66a):
    each earthbend chooses its own land, however alike the two filters are."""
    _need(box, "Cracked Earth Technique")
    ability = _spell(box.db, "Cracked Earth Technique")
    assert len(targeted_nodes(ability.effects)) == 2


# ---------------------------------------------------------------------------
# "Another target" after an earlier target
# ---------------------------------------------------------------------------


def test_another_target_is_marked_distinct_not_other_than_the_source(box):
    _need(box, "Consume Strength")
    ability = _spell(box.db, "Consume Strength")
    first, second = targeted_nodes(ability.effects)
    assert not first.distinct_from_earlier_targets
    assert second.distinct_from_earlier_targets
    assert not second.targets.other_than_source


def test_consume_strength_pumps_one_and_shrinks_another(box):
    _need(box, "Consume Strength", "Grizzly Bears", "Hill Giant")
    game = box.game
    bears = _creature(box, "Grizzly Bears", 0)
    giant = _creature(box, "Hill Giant", 1)
    box.put("Consume Strength", "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.GREEN)
    _mana(box, 0, 1, Color.BLACK)

    _cast(box, 0, "Consume Strength", ((bears.id,), (giant.id,)))
    box.resolve_top()

    assert (game.characteristics(bears).power, game.characteristics(bears).toughness) == (4, 4)
    assert (game.characteristics(giant).power, game.characteristics(giant).toughness) == (1, 1)


def test_the_same_creature_cannot_be_both_targets(box):
    """CR 601.2c: "another" is a targeting criterion the choice must meet."""
    _need(box, "Consume Strength", "Grizzly Bears", "Hill Giant")
    bears = _creature(box, "Grizzly Bears", 0)
    _creature(box, "Hill Giant", 1)
    box.put("Consume Strength", "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.GREEN)
    _mana(box, 0, 1, Color.BLACK)
    game = box.game
    action = next(
        action
        for action in legal_actions(game, 0)
        if action.kind.name.startswith("CAST")
        and game.objects[action.source].card.name == "Consume Strength"
    )
    assert not _perform(game, 0, replace(action, targets=((bears.id,), (bears.id,))))
    assert not game.stack


def test_one_creature_is_not_enough_for_two_distinct_targets(box):
    _need(box, "Consume Strength", "Grizzly Bears")
    _creature(box, "Grizzly Bears", 0)
    box.put("Consume Strength", "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.GREEN)
    _mana(box, 0, 1, Color.BLACK)
    assert not _castable(box, 0, "Consume Strength")
    _creature(box, "Grizzly Bears", 1)
    assert _castable(box, 0, "Consume Strength")


def test_a_chooser_that_picks_one_creature_twice_is_corrected(box):
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
        choose_targets,
        targeting_effects,
    )

    _need(box, "Consume Strength", "Grizzly Bears", "Hill Giant")
    game = box.game
    bears = _creature(box, "Grizzly Bears", 0)
    giant = _creature(box, "Hill Giant", 1)
    box.put("Consume Strength", "hand", 0)
    spell = next(
        obj for obj in game.objects.values()
        if obj.zone is Zone.HAND and obj.card.name == "Consume Strength"
    )

    class Stubborn(PassiveOpponent):
        def choose_targets(self, game, player, source, candidates):
            return tuple((bears.id,) for _ in candidates)

    game.agents[0] = Stubborn()
    chosen = choose_targets(game, spell, targeting_effects(game, spell), 0)
    assert chosen == ((bears.id,), (giant.id,))


def test_deadshot_taps_one_creature_and_it_deals_its_power_to_another(box):
    _need(box, "Deadshot", "Hill Giant", "Grizzly Bears")
    game = box.game
    giant = _creature(box, "Hill Giant", 0)
    bears = _creature(box, "Grizzly Bears", 1)
    box.put("Deadshot", "hand", 0)
    _mana(box, 0, 3)
    _mana(box, 0, 1, Color.RED)

    _cast(box, 0, "Deadshot", ((giant.id,), (bears.id,)))
    box.resolve_top()

    assert giant.tapped
    assert not _alive(game, bears)  # 3 damage from the Giant
    assert _alive(game, giant)


def test_deadshot_deals_nothing_when_its_first_target_is_gone(box):
    """CR 608.2b: the power of an illegal target can't be determined, and
    the part of the effect that needs it doesn't happen - no damage, and no
    other object stands in for "it"."""
    from mtgfish.rules.cr100_game_concepts import actions

    _need(box, "Deadshot", "Hill Giant", "Grizzly Bears")
    game = box.game
    giant = _creature(box, "Hill Giant", 0)
    bears = _creature(box, "Grizzly Bears", 1)
    box.put("Deadshot", "hand", 0)
    _mana(box, 0, 3)
    _mana(box, 0, 1, Color.RED)
    _cast(box, 0, "Deadshot", ((giant.id,), (bears.id,)))
    actions.bounce(game, giant)
    box.resolve_top()

    assert _alive(game, bears)
    assert bears.damage == 0


@pytest.mark.parametrize("name", ["Jilt", "Arm the Cathars"])
def test_another_target_the_engine_cannot_place_stays_unread(box, name):
    """Jilt's second target exists only if the spell was kicked (CR 601.2c);
    Arm the Cathars' "those creatures" means all three targets, and the
    resolution remembers only the last."""
    _need(box, name)
    face = parse_card(box.db.lookup(name)).faces[0]
    assert any(a.unparsed for a in face.abilities)
