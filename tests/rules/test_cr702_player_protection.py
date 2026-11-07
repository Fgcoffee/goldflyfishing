"""A player with protection from everything can't be targeted and isn't
dealt damage (CR 702.16b, 702.16e, 702.16j): Teferi's Protection."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform
from mtgfish.rules.kernel.enums import Color
from mtgfish.rules.kernel.ids import PlayerId, player_target
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

P0, P1 = PlayerId(0), PlayerId(1)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    for name in ("Teferi's Protection", "Lightning Bolt"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def resolve_all(box):
    box.settle()
    for _ in range(10):
        if not box.game.stack:
            return
        box.resolve_top()


def bolt_targets(box, player):
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
        _candidates_for,
        targeting_effects,
    )

    bolt = next(o for o in box.game.objects.values()
                if o.is_live and o.card is not None and o.card.name == "Lightning Bolt")
    (effect,) = targeting_effects(box.game, bolt)
    return _candidates_for(box.game, bolt, effect, player)


def protect_p0(box):
    box.put("Teferi's Protection", "hand", 0)
    box.game.player(P0).mana_pool.add(ManaKind(Color.WHITE), 3)
    action = next(a for a in legal_actions(box.game, P0)
                  if a.kind.name.startswith("CAST")
                  and box.game.objects[a.source].card.name == "Teferi's Protection")
    assert _perform(box.game, P0, replace(action))
    resolve_all(box)


def test_a_protected_player_is_not_a_legal_target(box):
    box.put("Lightning Bolt", "hand", 1)
    assert player_target(P0) in bolt_targets(box, P1)
    protect_p0(box)
    assert player_target(P0) not in bolt_targets(box, P1)
    assert player_target(P1) in bolt_targets(box, P1)


def test_damage_to_a_protected_player_is_prevented(card_db):
    """CR 702.16e, with protection alone - Teferi's Protection also stops the
    life total changing, which would hide whether protection did anything."""
    from harness import ScriptedAbilities, make_board

    from mtgfish.rules.cr100_game_concepts.actions import deal_damage
    from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
    from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
    from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
    from mtgfish.rules.kernel.enums import Duration, Phase, Step
    from mtgfish.rules.kernel.query import YOU, ObjectFilter

    protection = Ability(
        AbilityKind.STATIC,
        keyword="Protection",
        quality=ObjectFilter(zones=frozenset()),
        text="Protection from everything",
    )
    board = make_board(card_db, ScriptedAbilities({
        "Divination": (Ability.spell(Effect(
            EffectKind.GRANT_ABILITY,
            players=YOU,
            granted_abilities=(protection,),
            duration=int(Duration.YOUR_NEXT_TURN),
            text="you gain protection from everything",
        )),),
    }))
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    game.player(P0).mana_pool.add(ManaKind(Color.BLUE), 3)
    card = board.hand("Divination")
    cast_spell(game, P0, Action(ActionKind.CAST_SPELL, source=card.id))
    board.resolve_stack()
    source = board.play("Grizzly Bears", controller=1)
    before = game.player(P0).life
    deal_damage(game, P0, 3, source=source.id, source_controller=P1)
    assert game.player(P0).life == before
    deal_damage(game, P1, 3, source=source.id, source_controller=P1)
    assert game.player(P1).life == before - 3
