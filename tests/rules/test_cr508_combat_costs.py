"""Costs to attack and to block (CR 508.1c-d, 508.1h-j, 509.1c-f).

"Creatures can't attack you unless their controller pays {2} for each
creature they control that's attacking you" parsed cleanly for years and the
engine never charged it: the tax sat inside an ``UNLESS_PAYS`` that nothing
in combat looked at, so Propaganda, Ghostly Prison and Baird were blank
cards that looked read. These run the real oracle text on a real board.
"""

from __future__ import annotations

from harness import FixedAgent, make_board

from mtgfish.parser.compile import OracleAbilities, parse_card
from mtgfish.rules.cr500_turn_structure.cr506_combat import (
    AttackPermanent,
    declare_attackers,
    declare_blockers,
)
from mtgfish.rules.kernel.ids import PlayerId


def board_for(card_db, *, attackers=None, blockers=None):
    board = make_board(card_db, OracleAbilities())
    board.game.agents[PlayerId(0)] = FixedAgent(attackers=attackers or {})
    board.game.agents[PlayerId(1)] = FixedAgent(blockers=blockers or {})
    board.game.active_player = PlayerId(0)
    return board


def lands(board, name, count, controller=0):
    return [board.play(name, controller=controller) for _ in range(count)]


def attack(board, *creatures, at=1):
    board.game.agents[PlayerId(0)].attackers = {obj.id: at for obj in creatures}
    declare_attackers(board.game)
    return board.game.combat


def tapped(objs):
    return sum(1 for obj in objs if obj.tapped)


# ---------------------------------------------------------------------------
# CR 508.1h-j: the attacking player pays
# ---------------------------------------------------------------------------


def test_propaganda_charges_two_for_each_attacker(card_db):
    board = board_for(card_db)
    board.play("Propaganda", controller=1)
    forests = lands(board, "Forest", 5)
    bears = [board.play("Grizzly Bears"), board.play("Grizzly Bears")]

    combat = attack(board, *bears)

    assert set(combat.attacking) == {b.id for b in bears}
    assert tapped(forests) == 4


def test_an_attacker_the_player_cannot_pay_for_stays_home(card_db):
    """Partial payment is not allowed (CR 508.1j): with three lands, two
    attackers at {2} each is unaffordable, so one of them does not attack."""
    board = board_for(card_db)
    board.play("Ghostly Prison", controller=1)
    forests = lands(board, "Forest", 3)
    bears = [board.play("Grizzly Bears"), board.play("Grizzly Bears")]

    combat = attack(board, *bears)

    assert len(combat.attacking) == 1
    assert tapped(forests) == 2
    stayed = next(b for b in bears if b.id not in combat.attacking)
    assert not stayed.tapped
    assert not stayed.attacked_this_turn


def test_no_mana_no_attack(card_db):
    board = board_for(card_db)
    board.play("Windborn Muse", controller=1)
    bears = board.play("Grizzly Bears")

    combat = attack(board, bears)

    assert not combat.attacking
    assert not bears.tapped


def test_an_attacker_cannot_tap_for_mana_to_pay_its_own_tax(card_db):
    """CR 508.1f taps the attackers before the cost is paid, so a mana
    creature that attacks is no longer a source."""
    board = board_for(card_db)
    board.play("Propaganda", controller=1)
    lands(board, "Forest", 1)
    elves = board.play("Llanowar Elves")

    combat = attack(board, elves)

    assert not combat.attacking


def test_the_tax_is_on_attacking_its_controller_only(card_db):
    board = make_board(card_db, OracleAbilities(), players=3)
    board.game.agents[PlayerId(0)] = FixedAgent()
    board.game.active_player = PlayerId(0)
    board.play("Propaganda", controller=1)
    forests = lands(board, "Forest", 2)
    bears = board.play("Grizzly Bears")

    combat = attack(board, bears, at=2)

    assert combat.attacking == {bears.id: PlayerId(2)}
    assert tapped(forests) == 0


def test_attacking_you_does_not_cover_your_planeswalkers(card_db):
    """Propaganda: "attack you". Baird: "attack you or planeswalkers you
    control". The difference is the whole card."""
    board = board_for(card_db)
    board.play("Propaganda", controller=1)
    walker = board.play("Jace Beleren", controller=1)
    walker.add_counters("loyalty", 3)
    forests = lands(board, "Forest", 2)
    bears = board.play("Grizzly Bears")

    board.game.agents[PlayerId(0)].attackers = {bears.id: AttackPermanent(walker.id)}
    declare_attackers(board.game)

    assert bears.id in board.game.combat.attacking
    assert tapped(forests) == 0


def test_baird_taxes_attacks_on_planeswalkers_too(card_db):
    board = board_for(card_db)
    board.play("Baird, Steward of Argive", controller=1)
    walker = board.play("Jace Beleren", controller=1)
    walker.add_counters("loyalty", 3)
    forests = lands(board, "Forest", 2)
    bears = board.play("Grizzly Bears")
    bears2 = board.play("Grizzly Bears")

    board.game.agents[PlayerId(0)].attackers = {
        bears.id: AttackPermanent(walker.id),
        bears2.id: 1,
    }
    declare_attackers(board.game)

    assert set(board.game.combat.attacking) == {bears.id, bears2.id}
    assert tapped(forests) == 2


def test_sphere_of_safety_charges_x_per_attacker(card_db):
    """{X} for each of those creatures, X the number of enchantments its
    controller has: two enchantments and two attackers is {4}."""
    board = board_for(card_db)
    board.play("Sphere of Safety", controller=1)
    board.play("Propaganda", controller=0)  # the attacker's enchantment: not counted
    board.play("Pacifism", controller=1)
    forests = lands(board, "Forest", 5)
    bears = [board.play("Grizzly Bears"), board.play("Grizzly Bears")]

    combat = attack(board, *bears)

    assert set(combat.attacking) == {b.id for b in bears}
    assert tapped(forests) == 4


def test_sphere_of_safety_leaves_an_attacker_it_cannot_pay_for_home(card_db):
    board = board_for(card_db)
    board.play("Sphere of Safety", controller=1)
    board.play("Pacifism", controller=1)
    forests = lands(board, "Forest", 3)
    bears = [board.play("Grizzly Bears"), board.play("Grizzly Bears")]

    combat = attack(board, *bears)

    assert len(combat.attacking) == 1
    assert tapped(forests) == 2


def test_a_goaded_creature_is_not_forced_to_pay(card_db):
    """CR 508.1d: a player is not required to pay a cost to attack, even to
    obey a requirement."""
    board = board_for(card_db)
    board.play("Propaganda", controller=1)
    forests = lands(board, "Forest", 2)
    bears = board.play("Grizzly Bears")
    bears.goaded_by.add(PlayerId(1))
    board.refresh()

    combat = attack(board)

    assert not combat.attacking
    assert tapped(forests) == 0


def test_a_creatures_own_tax_scales_with_its_counters(card_db):
    board = board_for(card_db)
    myr = board.play("Myr Prototype")
    myr.add_counters("+1/+1", 3)
    forests = lands(board, "Forest", 3)

    combat = attack(board, myr)

    assert myr.id in combat.attacking
    assert tapped(forests) == 3


def test_a_tax_gated_on_an_untapped_creature_lapses_when_it_taps(card_db):
    board = board_for(card_db)
    angel = board.play("Archangel of Tithes", controller=1)
    forests = lands(board, "Forest", 1)
    bears = board.play("Grizzly Bears")

    angel.tapped = True
    board.refresh()
    combat = attack(board, bears)

    assert bears.id in combat.attacking
    assert tapped(forests) == 0


def test_cowed_by_wisdom_counts_the_aura_controllers_hand(card_db):
    from mtgfish.rules.cr100_game_concepts import actions

    board = board_for(card_db)
    bears = board.play("Grizzly Bears")
    aura = board.play("Cowed by Wisdom", controller=1)
    actions.attach(board.game, aura, bears)
    for _ in range(2):
        board.hand("Forest", controller=1)
    hand = len(board.game.player(PlayerId(1)).hand)
    # "Your hand" is the aura controller's, not every hand at the table.
    assert hand != len(board.game.player(PlayerId(0)).hand)
    forests = lands(board, "Forest", hand)
    board.refresh()

    combat = attack(board, bears)

    assert bears.id in combat.attacking
    assert tapped(forests) == hand


# ---------------------------------------------------------------------------
# CR 509.1d-f: the defending player pays to block
# ---------------------------------------------------------------------------


def test_archangel_taxes_blockers_while_it_attacks(card_db):
    board = board_for(card_db)
    angel = board.play("Archangel of Tithes")
    spider = board.play("Giant Spider", controller=1)
    board.game.agents[PlayerId(1)].blockers = {spider.id: [angel.id]}
    attack(board, angel)

    declare_blockers(board.game)
    assert not board.game.combat.blocking


def test_archangels_block_tax_is_paid_when_it_can_be(card_db):
    board = board_for(card_db)
    angel = board.play("Archangel of Tithes")
    spider = board.play("Giant Spider", controller=1)
    forest = board.play("Forest", controller=1)
    board.game.agents[PlayerId(1)].blockers = {spider.id: [angel.id]}
    attack(board, angel)

    declare_blockers(board.game)
    assert board.game.combat.blocking == {spider.id: [angel.id]}
    assert forest.tapped


def test_norns_annex_can_be_paid_with_life(card_db):
    """{W/P} is payable with 2 life (CR 107.4f) - with no white mana at all."""
    board = board_for(card_db)
    board.play("Norn's Annex", controller=1)
    bears = board.play("Grizzly Bears")
    life = board.game.player(PlayerId(0)).life

    combat = attack(board, bears)

    assert bears.id in combat.attacking
    assert board.game.player(PlayerId(0)).life == life - 2


def test_a_blocker_whose_tax_is_unpaid_does_not_block(card_db):
    board = board_for(card_db)
    myr = board.play("Myr Prototype", controller=1)
    myr.add_counters("+1/+1", 2)
    lands(board, "Forest", 1, controller=1)
    bears = board.play("Grizzly Bears")
    board.game.agents[PlayerId(1)].blockers = {myr.id: [bears.id]}
    attack(board, bears)

    declare_blockers(board.game)

    assert not board.game.combat.blocking


def test_a_blocker_whose_tax_is_paid_blocks(card_db):
    board = board_for(card_db)
    behemoth = board.play("Qal Sisma Behemoth", controller=1)
    forests = lands(board, "Forest", 2, controller=1)
    bears = board.play("Grizzly Bears")
    board.game.agents[PlayerId(1)].blockers = {behemoth.id: [bears.id]}
    attack(board, bears)

    declare_blockers(board.game)

    assert board.game.combat.blocking == {behemoth.id: [bears.id]}
    assert tapped(forests) == 2


# ---------------------------------------------------------------------------
# What is not charged is not read
# ---------------------------------------------------------------------------


def _read(card_db, name, fragment):
    parsed = parse_card(card_db.lookup(name))
    ability = next(
        a for face in parsed.faces for a in face.abilities if fragment in a.text
    )
    return not ability.unparsed


def test_a_tax_scaled_by_creatures_it_does_not_cover_is_refused(card_db):
    """Elephant Grass taxes nonblack attackers "for each creature they control
    that's attacking you" - black ones included. Neither reading is charged."""
    assert not _read(card_db, "Elephant Grass", "Nonblack")


def test_the_taxes_the_engine_charges_are_read(card_db):
    for name in (
        "Propaganda",
        "Ghostly Prison",
        "Baird, Steward of Argive",
        "Brainwash",
        "Sphere of Safety",
    ):
        assert _read(card_db, name, "unless"), name
