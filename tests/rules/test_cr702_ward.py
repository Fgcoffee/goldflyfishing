"""Ward (CR 702.21), played out on a board.

"Ward [cost]" means "Whenever this permanent becomes the target of a spell or
ability an opponent controls, counter that spell or ability unless that player
pays [cost]." Every Ward creature in the format used to ward nothing: the
"unless that player pays" half had no player and no object to act on, so the
builder marked it unreadable. These tests hold the three references to the
rule - which player pays, what is countered, and whose spells trigger it - and
the non-mana costs a player can be charged outside an activation.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaCost, ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
    CastError,
    activate_ability,
    cast_spell,
)
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.kernel.enums import LETTER_TO_COLOR, CardType, Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ObjectFilter, Value

YOU = PlayerId(0)
THEM = PlayerId(1)


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.game.active_player = THEM
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    return board


def _ward(*components: CostComponent) -> tuple[Ability, ...]:
    return build(KeywordInstance("Ward", cost=Cost(components), text="Ward"))


def _mana(text: str) -> CostComponent:
    return CostComponent(CostKind.MANA, mana=ManaCost.parse(text))


def _bolt(board, target, *, extra: int = 0):
    """Lightning Bolt, cast by the opponent at ``target`` with R floating and
    ``extra`` more mana left in their pool to pay a ward cost with."""
    board.scripts.add(
        "Lightning Bolt",
        Ability.spell(
            Effect(
                EffectKind.DAMAGE,
                targets=ObjectFilter(types_any=CardType.CREATURE),
                amount=Value.of(3),
                is_targeted=True,
                text="3 damage to target creature",
            ),
            text="Lightning Bolt deals 3 damage to target creature.",
        ),
    )
    bolt = board.hand("Lightning Bolt", controller=THEM)
    pool = board.game.player(THEM).mana_pool
    pool.add(ManaKind(LETTER_TO_COLOR["R"]), 1 + extra)
    cast_spell(
        board.game, THEM, Action(ActionKind.CAST_SPELL, source=bolt.id, targets=((target.id,),))
    )
    return bolt


def _empty_hand(board, player):
    hand = board.game.player(player).hand
    for object_id in list(hand):
        board.game.move_object(board.game.objects[object_id], Zone.LIBRARY, to_player=player)


def _warded(board, *components: CostComponent, name: str = "Grizzly Bears"):
    board.scripts.add(name, *_ward(*components))
    return board.play(name, controller=YOU)


def test_an_unpaid_ward_counters_the_spell(board):
    bears = _warded(board, _mana("{2}"))
    _bolt(board, bears)
    assert [t.ability.keyword for t in board.game.pending_triggers] == ["Ward"]
    board.settle()
    board.resolve_stack()
    assert "Grizzly Bears" in board.alive(YOU), "the Bolt was countered"
    assert "Lightning Bolt" in board.in_graveyard(THEM)


def test_a_paid_ward_lets_the_spell_resolve(board):
    bears = _warded(board, _mana("{2}"))
    _bolt(board, bears, extra=2)
    board.settle()
    board.resolve_stack()
    assert "Grizzly Bears" not in board.alive(YOU), "the opponent paid {2}"
    assert board.game.player(THEM).mana_pool.total == 0


def test_the_payer_is_the_spells_controller_not_wards(board):
    """"That player" is whoever controls the targeting spell. Ward's own
    controller having mana is irrelevant - it is never asked."""
    bears = _warded(board, _mana("{2}"))
    board.game.player(YOU).mana_pool.add(ManaKind(LETTER_TO_COLOR["G"]), 2)
    _bolt(board, bears)
    board.settle()
    board.resolve_stack()
    assert "Grizzly Bears" in board.alive(YOU)
    assert board.game.player(YOU).mana_pool.total == 2


def test_ward_ignores_its_controllers_own_spells(board):
    """"An opponent controls": your own spell targeting your warded creature
    is not taxed."""
    bears = _warded(board, _mana("{2}"))
    board.scripts.add(
        "Giant Growth",
        Ability.spell(
            Effect(
                EffectKind.MODIFY_PT,
                targets=ObjectFilter(types_any=CardType.CREATURE),
                amount=Value.of(3),
                amount2=Value.of(3),
                is_targeted=True,
                text="target creature gets +3/+3",
            ),
            text="Target creature gets +3/+3 until end of turn.",
        ),
    )
    growth = board.hand("Giant Growth", controller=YOU)
    board.game.active_player = YOU
    board.game.player(YOU).mana_pool.add(ManaKind(LETTER_TO_COLOR["G"]), 1)
    cast_spell(
        board.game, YOU, Action(ActionKind.CAST_SPELL, source=growth.id, targets=((bears.id,),))
    )
    assert not board.game.pending_triggers


def test_ward_counters_an_ability_too(board):
    """"A spell or ability": an activated ability that targets triggers ward,
    and it is the ability that is countered."""
    bears = _warded(board, _mana("{2}"))
    board.scripts.add(
        "Prodigal Sorcerer",
        Ability(
            AbilityKind.ACTIVATED,
            cost=Cost((CostComponent(CostKind.TAP_SELF),)),
            effects=(
                Effect(
                    EffectKind.DAMAGE,
                    targets=ObjectFilter(types_any=CardType.CREATURE),
                    amount=Value.of(3),
                    is_targeted=True,
                    text="3 damage to target creature",
                ),
            ),
            text="{T}: This creature deals 3 damage to target creature.",
        ),
    )
    sorcerer = board.play("Prodigal Sorcerer", controller=THEM)
    sorcerer.summoning_sick = False
    activate_ability(
        board.game,
        THEM,
        Action(
            ActionKind.ACTIVATE_ABILITY,
            source=sorcerer.id,
            ability_index=0,
            targets=((bears.id,),),
        ),
    )
    board.settle()
    assert len(board.game.stack) == 2, "the ability, and ward above it"
    board.resolve_stack()
    assert "Grizzly Bears" in board.alive(YOU), "the ability was countered"


def test_ward_pay_life(board):
    bears = _warded(
        board, CostComponent(CostKind.PAY_LIFE, amount=Value.of(2), text="pay life")
    )
    before = board.game.player(THEM).life
    _bolt(board, bears)
    board.settle()
    board.resolve_stack()
    assert board.game.player(THEM).life == before - 2
    assert "Grizzly Bears" not in board.alive(YOU)


def test_ward_discard_a_card_is_a_real_discard(board):
    bears = _warded(
        board,
        CostComponent(CostKind.DISCARD, amount=Value.of(1), text="discard"),
    )
    _empty_hand(board, THEM)
    board.hand("Forest", controller=THEM)
    _bolt(board, bears)
    board.settle()
    board.resolve_stack()
    assert "Forest" in board.in_graveyard(THEM)
    assert "Grizzly Bears" not in board.alive(YOU)


def test_ward_discard_with_nothing_to_discard_counters(board):
    bears = _warded(
        board,
        CostComponent(CostKind.DISCARD, amount=Value.of(1), text="discard"),
    )
    _empty_hand(board, THEM)
    _bolt(board, bears)
    board.settle()
    board.resolve_stack()
    assert "Grizzly Bears" in board.alive(YOU)


def test_ward_sacrifice_counts_and_is_paid_from_the_payers_own_permanents(board):
    """"Sacrifice two permanents" is two, and they are the payer's: an
    opponent's permanents on the battlefield cannot pay (CR 701.17a)."""
    two = ObjectFilter(count=Value.of(2))
    bears = _warded(
        board,
        CostComponent(CostKind.SACRIFICE, filter=two, amount=Value.of(2), text="sacrifice"),
    )
    board.play("Forest", controller=YOU)
    board.play("Forest", controller=YOU)
    board.play("Forest", controller=THEM)
    _bolt(board, bears)
    board.settle()
    board.resolve_stack()
    assert "Grizzly Bears" in board.alive(YOU), "one permanent of their own is not two"
    assert board.alive(THEM) == ["Forest"]

    board.play("Mountain", controller=THEM)
    _bolt(board, board.game.objects[bears.id])
    board.settle()
    board.resolve_stack()
    assert "Grizzly Bears" not in board.alive(YOU)
    assert board.alive(THEM) == []
    assert board.alive(YOU) == ["Forest", "Forest"]


def test_ward_with_a_cost_no_player_can_be_charged_is_not_built(board):
    """A ward cost ``can_pay_cost`` cannot charge would counter everything, or
    nothing: it is left unread instead."""
    from mtgfish.parser.compile import understood

    (ability,) = _ward(CostComponent(CostKind.MILL, amount=Value.of(2)))
    assert not understood(ability)
    (ability,) = build(KeywordInstance("Ward", text="Ward"))
    assert not understood(ability)


def test_the_counter_acts_on_the_triggering_spell_only(board):
    """Another spell on the stack is not "that spell"."""
    bears = _warded(board, _mana("{2}"))
    board.scripts.add(
        "Divination",
        Ability.spell(Effect(EffectKind.DRAW, amount=Value.of(2), text="draw two")),
    )
    divination = board.hand("Divination", controller=THEM)
    drawn = len(board.game.player(THEM).hand) - 1
    board.game.player(THEM).mana_pool.add(ManaKind(LETTER_TO_COLOR["U"]), 3)
    cast_spell(board.game, THEM, Action(ActionKind.CAST_SPELL, source=divination.id))
    _bolt(board, bears)
    board.settle()
    board.resolve_stack()
    assert "Divination" in board.in_graveyard(THEM)
    assert len(board.game.player(THEM).hand) == drawn + 2, "Divination still resolved"
    assert "Grizzly Bears" in board.alive(YOU)


def test_a_rewound_cast_leaves_no_ward_trigger(board):
    """CR 601.2h: a cast that cannot be paid never happened, so nothing
    became a target."""
    bears = _warded(board, _mana("{2}"))
    board.scripts.add(
        "Lightning Bolt",
        Ability.spell(
            Effect(
                EffectKind.DAMAGE,
                targets=ObjectFilter(types_any=CardType.CREATURE),
                amount=Value.of(3),
                is_targeted=True,
            ),
        ),
    )
    bolt = board.hand("Lightning Bolt", controller=THEM)
    with pytest.raises(CastError):
        cast_spell(
            board.game,
            THEM,
            Action(ActionKind.CAST_SPELL, source=bolt.id, targets=((bears.id,),)),
        )
    assert not board.game.pending_triggers
    assert bolt.zone is Zone.HAND or board.game.objects.get(bolt.id) is None
