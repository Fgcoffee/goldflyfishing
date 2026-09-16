"""Casting, timing, targeting, and costs (CR 115, 117, 307, 601, 608).

The CR 601.2 sequence is strict, and several of its steps are only observable
through their consequences: a spell that cannot be cast without a legal target
never reaches the stack, a spell whose targets all die is countered on
resolution, and a cast that cannot be paid for leaves the game state untouched.
"""

from __future__ import annotations

import pytest

from mtgfish.rules.abilities import Ability
from mtgfish.rules.casting import CastError, cast_spell, play_land
from mtgfish.rules.effects import Effect, EffectKind
from mtgfish.rules.enums import CardType, Phase, Step, Zone
from mtgfish.rules.ids import PlayerId
from mtgfish.rules.legality import has_sorcery_speed, legal_actions
from mtgfish.rules.priority import Action, ActionKind
from mtgfish.rules.query import ObjectFilter, Value
from mtgfish.rules.stack import resolve_top

from harness import ScriptedAbilities, keyword, make_board

CREATURES = ObjectFilter(types_all=CardType.CREATURE)


def shock() -> Ability:
    """"Shock deals 2 damage to any target." - a targeted spell ability."""
    return Ability.spell(
        Effect(
            EffectKind.DAMAGE,
            targets=CREATURES,
            amount=Value.of(2),
            is_targeted=True,
            text="deals 2 damage to target creature",
        ),
        text="Shock deals 2 damage to any target.",
    )


def wrath() -> Ability:
    """"Destroy all creatures." - untargeted, so it matches fresh at resolution."""
    return Ability.spell(
        Effect(EffectKind.DESTROY, targets=CREATURES, text="destroy all creatures"),
        text="Destroy all creatures.",
    )


@pytest.fixture
def scripts() -> ScriptedAbilities:
    return ScriptedAbilities(
        {
            "Shock": (shock(),),
            "Wrath of God": (wrath(),),
            "Ambush Viper": (keyword("Flash"), keyword("Deathtouch")),
        }
    )


@pytest.fixture
def board(card_db, scripts):
    board = make_board(card_db, scripts)
    board.game.active_player = PlayerId(0)
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    return board


def give_mana(board, player: int, amount: int, color: str = "R"):
    """Put mana straight into a pool, bypassing lands."""
    from mtgfish.rules.enums import LETTER_TO_COLOR
    from mtgfish.rules.mana import ManaKind

    board.game.player(PlayerId(player)).mana_pool.add(
        ManaKind(LETTER_TO_COLOR[color]), amount
    )


# ---------------------------------------------------------------------------
# Timing (CR 307.1, 117.1a)
# ---------------------------------------------------------------------------


def test_sorcery_speed_requires_your_main_phase_with_an_empty_stack(board):
    game = board.game
    assert has_sorcery_speed(game, PlayerId(0))
    assert not has_sorcery_speed(game, PlayerId(1))

    game.phase = Phase.COMBAT
    assert not has_sorcery_speed(game, PlayerId(0))


def test_sorcery_cannot_be_cast_on_an_opponents_turn(board):
    board.hand("Wrath of God", controller=0)
    give_mana(board, 0, 5)
    board.game.active_player = PlayerId(1)

    casts = [a for a in legal_actions(board.game, PlayerId(0)) if a.kind is ActionKind.CAST_SPELL]
    assert not casts


def test_instant_can_be_cast_any_time(board):
    board.hand("Shock", controller=0)
    give_mana(board, 0, 3)
    board.game.active_player = PlayerId(1)
    board.play("Grizzly Bears", controller=1)

    casts = [a for a in legal_actions(board.game, PlayerId(0)) if a.kind is ActionKind.CAST_SPELL]
    assert casts


def test_flash_makes_a_creature_instant_speed(board):
    board.hand("Ambush Viper", controller=0)
    give_mana(board, 0, 5, "G")
    board.game.active_player = PlayerId(1)

    casts = [a for a in legal_actions(board.game, PlayerId(0)) if a.kind is ActionKind.CAST_SPELL]
    assert any(
        board.game.printed_characteristics(board.game.objects[a.source]).name == "Ambush Viper"
        for a in casts
    )


# ---------------------------------------------------------------------------
# Land drops (CR 305.2)
# ---------------------------------------------------------------------------


def test_one_land_per_turn(board):
    game = board.game
    first = board.hand("Forest", controller=0)
    second = board.hand("Forest", controller=0)

    play_land(game, PlayerId(0), Action(ActionKind.PLAY_LAND, source=first.id))
    assert game.player(PlayerId(0)).lands_played == 1

    with pytest.raises(CastError):
        play_land(game, PlayerId(0), Action(ActionKind.PLAY_LAND, source=second.id))


def test_land_drops_are_not_offered_once_used(board):
    game = board.game
    board.hand("Forest", controller=0)
    game.player(PlayerId(0)).lands_played = 1
    assert not [a for a in legal_actions(game, PlayerId(0)) if a.kind is ActionKind.PLAY_LAND]


def test_extra_land_drop_allows_a_second(board):
    game = board.game
    first = board.hand("Forest", controller=0)
    second = board.hand("Forest", controller=0)
    game.player(PlayerId(0)).max_lands = 2

    play_land(game, PlayerId(0), Action(ActionKind.PLAY_LAND, source=first.id))
    play_land(game, PlayerId(0), Action(ActionKind.PLAY_LAND, source=second.id))
    assert game.player(PlayerId(0)).lands_played == 2


def test_a_land_taps_for_mana_the_turn_it_arrives(board):
    """Summoning sickness (CR 302.6) applies to creatures, never to lands."""
    game = board.game
    forest = board.hand("Forest", controller=0)
    play_land(game, PlayerId(0), Action(ActionKind.PLAY_LAND, source=forest.id))

    permanent = next(o for o in game.permanents(PlayerId(0)))
    mana_abilities = [
        (i, a) for i, a in enumerate(game.characteristics(permanent).abilities)
        if a.is_mana_ability
    ]
    assert mana_abilities

    from mtgfish.rules.casting import activate_ability

    index, _ = mana_abilities[0]
    activate_ability(
        game, PlayerId(0), Action(ActionKind.ACTIVATE_MANA_ABILITY, source=permanent.id,
                                  ability_index=index)
    )
    assert game.player(PlayerId(0)).mana_pool.total == 1


# ---------------------------------------------------------------------------
# Targeting (CR 601.2c, 115)
# ---------------------------------------------------------------------------


def test_a_spell_needing_a_target_is_not_castable_without_one(board):
    """CR 601.2c: it never reaches the stack."""
    board.hand("Shock", controller=0)
    give_mana(board, 0, 3)
    casts = [a for a in legal_actions(board.game, PlayerId(0)) if a.kind is ActionKind.CAST_SPELL]
    assert not casts


def test_the_same_spell_is_castable_once_a_target_exists(board):
    board.hand("Shock", controller=0)
    board.play("Grizzly Bears", controller=1)
    give_mana(board, 0, 3)
    casts = [a for a in legal_actions(board.game, PlayerId(0)) if a.kind is ActionKind.CAST_SPELL]
    assert casts


def test_casting_at_an_illegal_target_is_rejected_and_rewound(board):
    """CR 601.2h: an incomplete cast leaves the game exactly as it was."""
    game = board.game
    card = board.hand("Shock", controller=0)
    land = board.play("Forest", controller=1)  # Not a creature.
    give_mana(board, 0, 3)

    hand_before = list(game.player(PlayerId(0)).hand)
    with pytest.raises(CastError):
        cast_spell(
            game,
            PlayerId(0),
            Action(ActionKind.CAST_SPELL, source=card.id, targets=((land.id,),)),
        )

    assert not game.stack
    assert len(game.player(PlayerId(0)).hand) == len(hand_before)


def test_hexproof_cannot_be_targeted_by_an_opponent(board):
    """CR 702.11b."""
    game = board.game
    card = board.hand("Shock", controller=0)
    board.scripts.add("Grizzly Bears", keyword("Hexproof"))
    bears = board.play("Grizzly Bears", controller=1)
    board.refresh()
    give_mana(board, 0, 3)

    with pytest.raises(CastError):
        cast_spell(
            game,
            PlayerId(0),
            Action(ActionKind.CAST_SPELL, source=card.id, targets=((bears.id,),)),
        )


def test_hexproof_does_not_stop_its_own_controller(board):
    game = board.game
    card = board.hand("Shock", controller=0)
    board.scripts.add("Grizzly Bears", keyword("Hexproof"))
    bears = board.play("Grizzly Bears", controller=0)
    board.refresh()
    give_mana(board, 0, 3)

    spell = cast_spell(
        game,
        PlayerId(0),
        Action(ActionKind.CAST_SPELL, source=card.id, targets=((bears.id,),)),
    )
    assert spell.zone is Zone.STACK


def test_shroud_stops_everyone(board):
    """CR 702.18b - including the controller."""
    game = board.game
    card = board.hand("Shock", controller=0)
    board.scripts.add("Grizzly Bears", keyword("Shroud"))
    bears = board.play("Grizzly Bears", controller=0)
    board.refresh()
    give_mana(board, 0, 3)

    with pytest.raises(CastError):
        cast_spell(
            game,
            PlayerId(0),
            Action(ActionKind.CAST_SPELL, source=card.id, targets=((bears.id,),)),
        )


# ---------------------------------------------------------------------------
# Resolution and fizzling (CR 608.2b)
# ---------------------------------------------------------------------------


def test_a_spell_resolves_onto_its_target(board):
    game = board.game
    card = board.hand("Shock", controller=0)
    bears = board.play("Grizzly Bears", controller=1)
    give_mana(board, 0, 3)

    cast_spell(
        game, PlayerId(0),
        Action(ActionKind.CAST_SPELL, source=card.id, targets=((bears.id,),)),
    )
    resolve_top(game)
    board.sba()

    assert bears.id not in game.battlefield


def test_a_spell_whose_only_target_is_gone_is_countered(board):
    """CR 608.2b: countered on resolution, and it does nothing at all."""
    game = board.game
    card = board.hand("Shock", controller=0)
    bears = board.play("Grizzly Bears", controller=1)
    give_mana(board, 0, 3)

    cast_spell(
        game, PlayerId(0),
        Action(ActionKind.CAST_SPELL, source=card.id, targets=((bears.id,),)),
    )
    # The target leaves before the spell resolves.
    game.move_object(bears, Zone.HAND, to_player=PlayerId(1))

    game.log.enabled = True
    before = len(game.log)
    resolve_top(game)
    assert "FIZZLED" in " ".join(e.text for e in game.log.entries[before:])


def test_an_untargeted_spell_hits_whatever_is_there_at_resolution(board):
    """A Wrath cast before a creature arrives still kills it."""
    game = board.game
    card = board.hand("Wrath of God", controller=0)  # {2}{W}{W}
    give_mana(board, 0, 6, "W")

    cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    late = board.play("Grizzly Bears", controller=1)

    resolve_top(game)
    board.sba()
    assert late.id not in game.battlefield


def test_a_permanent_spell_becomes_a_permanent(board):
    """CR 608.3: it resolves by entering the battlefield, not by doing something."""
    game = board.game
    card = board.hand("Grizzly Bears", controller=0)  # {1}{G}
    give_mana(board, 0, 4, "G")

    cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    assert game.stack
    resolve_top(game)

    assert not game.stack
    assert any(
        game.characteristics(o).name == "Grizzly Bears" for o in game.permanents(PlayerId(0))
    )


def test_an_instant_goes_to_the_graveyard_after_resolving(board):
    """CR 608.2m."""
    game = board.game
    card = board.hand("Shock", controller=0)
    bears = board.play("Grizzly Bears", controller=1)
    give_mana(board, 0, 3)

    cast_spell(
        game, PlayerId(0),
        Action(ActionKind.CAST_SPELL, source=card.id, targets=((bears.id,),)),
    )
    resolve_top(game)
    assert "Shock" in board.in_graveyard(0)


# ---------------------------------------------------------------------------
# Paying (CR 601.2f-h)
# ---------------------------------------------------------------------------


def test_casting_spends_mana(board):
    game = board.game
    card = board.hand("Grizzly Bears", controller=0)  # {1}{G}
    give_mana(board, 0, 4, "G")
    cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    assert game.player(PlayerId(0)).mana_pool.total == 2


def test_casting_taps_lands_when_the_pool_is_empty(board):
    """CR 601.2g: mana abilities are activated as part of paying."""
    game = board.game
    card = board.hand("Grizzly Bears", controller=0)
    forests = [board.play("Forest", controller=0) for _ in range(2)]

    cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))
    assert all(f.tapped for f in forests)


def test_an_unpayable_cast_is_rewound(board):
    game = board.game
    card = board.hand("Grizzly Bears", controller=0)
    hand_before = len(game.player(PlayerId(0)).hand)

    with pytest.raises(CastError):
        cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=card.id))

    assert not game.stack
    assert len(game.player(PlayerId(0)).hand) == hand_before


def test_mana_pools_empty_between_steps(board):
    """CR 500.4."""
    from mtgfish.rules.turn import _empty_mana_pools

    give_mana(board, 0, 5)
    assert board.game.player(PlayerId(0)).mana_pool.total == 5
    _empty_mana_pools(board.game)
    assert board.game.player(PlayerId(0)).mana_pool.total == 0
