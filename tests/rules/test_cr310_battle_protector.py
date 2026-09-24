"""A battle's protector, and the Siege (CR 310.9, 310.11, 310.12a, 310.12b).

Every battle has a player designated as its protector (CR 310.9), and for a
long time the engine had no such designation - so combat fell back to the
battle's *controller* as the defending player.

For a Siege that is exactly backwards. You cast the Siege, CR 310.12a makes
you choose an opponent as its protector, and then *you* are the one who
attacks it: that is the whole design of the card type. The engine instead
forbade you from attacking the Siege you control and invited you to attack
the one your opponent controls, which is nobody's to attack.

The designation, the choice as it enters, the CR 704.5x state-based action
that repairs it, and the defending-player lookup are all in place now. What
is still missing is CR 310.12b, the Siege's own intrinsic ability - exile
itself when the last defense counter comes off, then let its controller cast
the transformed back face. That needs three things the engine does not have:
an intrinsic-ability hook keyed on card type, a way for an effect to name a
face to cast, and a self-reference that survives the exile (CR 400.7 makes
the exiled card a new object). It is left as a strict xfail, which turns into
a loud failure the moment somebody builds it.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr500_turn_structure.cr506_combat import (
    AttackPermanent,
    _attack_is_permitted,
)
from mtgfish.rules.kernel.enums import Zone

SIEGE = "Invasion of Gobakhan"  # Battle - Siege, printed defense 3
CREATURE = "Grizzly Bears"


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


@pytest.fixture
def four(card_db):
    return make_board(card_db, ScriptedAbilities(), players=4)


def enters(board, name, controller):
    card = board.hand(name, controller=controller)
    return board.game.move_object(card, Zone.BATTLEFIELD, to_player=controller)


# ---------------------------------------------------------------------------
# CR 310.9 / 310.12a: the designation itself
# ---------------------------------------------------------------------------


def test_a_battle_has_a_protector(board):
    """CR 310.9: each battle has a player designated as its protector. Not a
    derived fact and not the controller - a designation of its own, like the
    monarch or a Class's level."""
    siege = enters(board, SIEGE, controller=0)

    assert getattr(siege, "protector", None) is not None


def test_a_sieges_protector_is_an_opponent_of_its_controller(board):
    """CR 310.12a: its controller chooses from among their opponents, and only
    an opponent of the controller can be one."""
    siege = enters(board, SIEGE, controller=0)

    assert getattr(siege, "protector", None) in board.game.opponents(siege.controller)


def test_a_siege_still_enters_with_its_defense_counters(board):
    """Not in dispute: CR 310.4b works, and is applied in the same place the
    protector choice would have to be made."""
    siege = enters(board, SIEGE, controller=0)

    assert siege.counter_count("defense") == 3


# ---------------------------------------------------------------------------
# CR 310.11: who may attack a battle
# ---------------------------------------------------------------------------


def test_a_player_may_attack_the_siege_they_control(board):
    """CR 506.2 with CR 310.12a: the defending player is the protector, who is
    an opponent of the Siege's controller. So the controller's own creatures
    are exactly the ones that may attack it - which is how a Siege is ever
    beaten at all."""
    siege = enters(board, SIEGE, controller=0)
    bear = board.play(CREATURE, controller=0)

    assert _attack_is_permitted(board.game, bear, AttackPermanent(siege.id))


def test_a_player_may_not_attack_a_siege_they_protect(board):
    """The other side of the same rule: the protector is the defender, and a
    player does not attack what they are defending."""
    siege = enters(board, SIEGE, controller=1)
    bear = board.play(CREATURE, controller=0)

    assert not _attack_is_permitted(board.game, bear, AttackPermanent(siege.id))


def test_a_third_player_may_attack_a_siege_they_neither_control_nor_protect(four):
    """CR 310.9b: a battle can be attacked by any player for whom its
    protector is a defending player. With three or more players that is
    everyone except the protector - so this is the case that shows the rule
    is about the protector rather than simply inverting the controller."""
    siege = enters(four, SIEGE, controller=0)
    protector = siege.protector
    bystander = next(p for p in (1, 2, 3) if p != protector)
    bear = four.play(CREATURE, controller=bystander)

    assert _attack_is_permitted(four.game, bear, AttackPermanent(siege.id))


def test_the_protector_may_not_attack_the_battle_they_protect(four):
    """CR 310.9b, first sentence: a battle's protector can never attack it."""
    siege = enters(four, SIEGE, controller=0)
    bear = four.play(CREATURE, controller=siege.protector)

    assert not _attack_is_permitted(four.game, bear, AttackPermanent(siege.id))


def test_a_planeswalker_is_still_defended_by_its_controller(board):
    """The control that keeps the fix narrow: CR 306.6 is unchanged, and only
    battles read a protector."""
    walker = board.play("Chandra, Torch of Defiance", controller=1)
    bear = board.play(CREATURE, controller=0)

    assert _attack_is_permitted(board.game, bear, AttackPermanent(walker.id))

    own = board.play(CREATURE, controller=1)
    assert not _attack_is_permitted(board.game, own, AttackPermanent(walker.id))


def test_a_battle_with_no_possible_protector_is_put_into_the_graveyard(board):
    """CR 310.11: if no player can be chosen as protector, the battle goes to
    its owner's graveyard as a state-based action. In a two-player game a
    departed opponent leaves a Siege with nobody to protect it."""
    enters(board, SIEGE, controller=0)
    board.game.player(1).has_lost = True
    board.sba()

    assert SIEGE not in board.alive(0)


def test_a_battle_with_defense_left_is_otherwise_untouched(board):
    """Not in dispute, and the control for the test above: with both players
    present, nothing removes a healthy battle."""
    enters(board, SIEGE, controller=0)
    board.sba()

    assert SIEGE in board.alive(0)


# ---------------------------------------------------------------------------
# CR 310.12b: the Siege's intrinsic ability
# ---------------------------------------------------------------------------


def test_a_beaten_siege_is_exiled_rather_than_buried(board):
    """CR 310.12b: when the last defense counter is removed, exile it - then
    its controller may cast it transformed for free. CR 310.7 only reaches a
    Siege that is still on the battlefield, so with the intrinsic ability in
    place the graveyard is never where it lands."""
    siege = enters(board, SIEGE, controller=0)
    source = board.play(CREATURE, controller=1)
    actions.deal_damage(board.game, siege, 3, source=source.id)
    board.settle()
    board.resolve_stack()

    assert SIEGE not in board.in_graveyard(0)


def test_a_siege_at_zero_defense_is_spared_until_its_trigger_resolves(board):
    """CR 704.5v, and the reason CR 310.12b works at all.

    A triggered ability reaches the stack only when a player would next
    receive priority, and state-based actions run before that. So the
    state-based action that buries a battle at zero defense has to spare a
    Siege whose own ability has triggered and is still waiting - otherwise it
    is in the graveyard before the ability that would have exiled it ever
    gets there, and the back face is never seen.
    """
    siege = enters(board, SIEGE, controller=0)
    source = board.play(CREATURE, controller=1)
    actions.deal_damage(board.game, siege, 3, source=source.id)
    board.sba()

    assert SIEGE in board.alive(0), "the Siege should be spared, not buried"


def test_a_non_siege_battle_at_zero_defense_is_buried(board):
    """The control: CR 704.5w has no such exception, and a battle with no
    battle type has no intrinsic ability to wait for."""
    battle = enters(board, "Occupation of Kulrath", controller=0)
    while battle.counter_count("defense"):
        battle.remove_counters("defense", battle.counter_count("defense"))
    board.sba()

    assert "Occupation of Kulrath" not in board.alive(0)
