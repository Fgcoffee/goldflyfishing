"""Abilities that work from hand are activated from hand, and only there.

CR 113.6: an ability functions only in the zones it says it does, and Cycling
(CR 702.29a) is an activated ability of a card in a player's hand. Legality
looked only at permanents and never asked where an ability functions, so both
halves were wrong at once. Replaying bench/decks/control.txt four-handed, game
165 offered a played Irrigated Farmland's Cycling 605 times - 37 of those
reached cost payment on the battlefield - and never once offered to cycle a
card in anyone's hand.

The same gap left every printed "Discard this card:" ability as an ability of
a permanent, and "discard this card" discarded whichever card the agent chose.
"""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.actions import discard
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind, _perform
from mtgfish.rules.cr100_game_concepts.cr118_costs import Cost, CostKind
from mtgfish.rules.cr500_turn_structure.cr506_combat import (
    AttackPermanent,
    deal_combat_damage,
    declare_attackers,
    declare_blockers,
    end_combat,
)
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import CastError, activate_ability
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr700_additional_rules import keywords
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.cr700_additional_rules.keywords import Status
from mtgfish.rules.kernel.enums import Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.rules.kernel.query import YOU, Value
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

DRAW_A_CARD = (Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1)),)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    # Discards the last card in hand when asked, so a card placed after the one
    # being cycled is the card a "discard a card" payment would take instead.
    table.game.agents[0] = PassiveOpponent()
    return table


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def _need(box, *names):
    for name in names:
        if box.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")


def _place(box, name, zone, player=0):
    """Place a card and return the object, not the sandbox's state summary."""
    box.put(name, zone, player)
    return next(
        obj
        for obj in reversed(list(box.game.objects.values()))
        if obj.card is not None and obj.card.name == name and obj.zone is Zone[zone.upper()]
    )


def _index(game, obj, keyword):
    abilities = game.characteristics(obj).abilities
    return next(i for i, ability in enumerate(abilities) if ability.keyword == keyword)


def _discards_itself(game, obj):
    abilities = game.characteristics(obj).abilities
    return next(
        i
        for i, ability in enumerate(abilities)
        if any(c.kind is CostKind.DISCARD for c in ability.cost.components)
    )


def _offered(game, player, obj, index=None):
    return [
        action
        for action in legal_actions(game, PlayerId(player))
        if action.kind in (ActionKind.ACTIVATE_ABILITY, ActionKind.ACTIVATE_MANA_ABILITY)
        and action.source == obj.id
        and (index is None or action.ability_index == index)
    ]


def test_a_played_cycling_land_does_not_offer_cycling(box):
    _need(box, "Irrigated Farmland", "Island")
    game = box.game
    land = _place(box, "Irrigated Farmland", "battlefield")
    land.tapped = False
    kept = _place(box, "Island", "hand")
    box.give_mana(2)
    pool = game.player(PlayerId(0)).mana_pool
    before = pool.total
    cycling = _index(game, land, "Cycling")

    assert not _offered(game, 0, land, cycling)
    assert _offered(game, 0, land), "its mana abilities still work in play"

    with pytest.raises(CastError):
        activate_ability(
            game,
            PlayerId(0),
            Action(ActionKind.ACTIVATE_ABILITY, source=land.id, ability_index=cycling),
        )
    assert land.zone is Zone.BATTLEFIELD
    assert kept.id in game.player(PlayerId(0)).hand
    assert pool.total == before


def test_cycling_is_offered_from_hand_and_discards_the_cycling_card(box):
    _need(box, "Irrigated Farmland", "Island")
    game = box.game
    player = game.player(PlayerId(0))
    for _ in range(3):
        box.put("Island", "library", 0)
    land = _place(box, "Irrigated Farmland", "hand")
    last_in_hand = _place(box, "Island", "hand")
    box.give_mana(1)

    (cycle,) = _offered(game, 0, land, _index(game, land, "Cycling"))
    assert _perform(game, PlayerId(0), cycle)
    box.resolve_top()

    assert last_in_hand.id in player.hand
    assert len(player.hand) == 2, "the Island stays, and cycling drew a card"
    assert [game.objects[o].card.name for o in player.graveyard] == ["Irrigated Farmland"]


def test_only_its_owner_may_cycle_a_card_in_hand(box):
    """CR 602.2 and 108.4a: a card in hand has no controller; its owner activates."""
    _need(box, "Irrigated Farmland")
    game = box.game
    land = _place(box, "Irrigated Farmland", "hand", player=0)
    box.give_mana(1, player=1)

    with pytest.raises(CastError):
        activate_ability(
            game,
            PlayerId(1),
            Action(
                ActionKind.ACTIVATE_ABILITY,
                source=land.id,
                ability_index=_index(game, land, "Cycling"),
            ),
        )
    assert land.id in game.player(PlayerId(0)).hand


def test_street_wraith_pays_its_life_to_cycle(box):
    """"Cycling - Pay 2 life": a keyword's cost was read only as mana symbols,
    so the life was dropped and cycling cost nothing but the card."""
    _need(box, "Street Wraith", "Island")
    game = box.game
    player = game.player(PlayerId(0))
    box.put("Island", "library", 0)
    wraith = _place(box, "Street Wraith", "hand")
    life = player.life

    (cycle,) = _offered(game, 0, wraith, _index(game, wraith, "Cycling"))
    assert _perform(game, PlayerId(0), cycle)
    assert player.life == life - 2
    assert wraith.id not in player.hand


def test_a_channel_land_channels_from_hand_and_not_from_play(box):
    """"{3}{U}, Discard this card: ..." can only be paid from hand (CR 113.6m)."""
    _need(box, "Otawara, Soaring City", "Grizzly Bears")
    game = box.game
    box.put("Grizzly Bears", "battlefield", 1)
    in_hand = _place(box, "Otawara, Soaring City", "hand")
    in_play = _place(box, "Otawara, Soaring City", "battlefield")
    box.give_mana(4)
    channel = _discards_itself(game, in_hand)

    assert game.characteristics(in_hand).abilities[channel].functions_in == {Zone.HAND}
    assert _offered(game, 0, in_hand, channel)
    assert not _offered(game, 0, in_play, channel)


def test_a_spirit_guide_makes_mana_from_hand_only(box):
    """"Exile this card from your hand: Add {R}." - a mana ability of a card in hand."""
    _need(box, "Simian Spirit Guide")
    game = box.game
    player = game.player(PlayerId(0))
    in_play = _place(box, "Simian Spirit Guide", "battlefield")
    guide = _place(box, "Simian Spirit Guide", "hand")

    assert not _offered(game, 0, in_play)
    (add_mana,) = _offered(game, 0, guide)
    assert add_mana.kind is ActionKind.ACTIVATE_MANA_ABILITY
    assert _perform(game, PlayerId(0), add_mana)
    assert player.mana_pool.total == 1
    assert guide.id not in player.hand
    assert any(game.objects[o].card.name == "Simian Spirit Guide" for o in game.exile)


def test_forecast_is_offered_from_hand_only_during_your_upkeep(board):
    """CR 702.57a: "Activate only during your upkeep"."""
    game = board.game
    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Forecast", effects=DRAW_A_CARD)))
    bears = board.hand("Grizzly Bears", controller=0)
    game.active_player = PlayerId(0)

    game.phase, game.step = Phase.PRECOMBAT_MAIN, Step.MAIN
    assert not _offered(game, 0, bears)

    game.phase, game.step = Phase.BEGINNING, Step.UPKEEP
    assert _offered(game, 0, bears)

    game.active_player = PlayerId(1)
    assert not _offered(game, 0, bears), "not during an opponent's upkeep"


def _names(game, object_ids):
    return [game.objects[object_id].card.name for object_id in object_ids]


def _live(game, name, zone):
    """The current object for a named card, however many zone changes it made."""
    return next(
        obj
        for obj in game.objects.values()
        if obj.is_live and obj.card is not None and obj.card.name == name and obj.zone is zone
    )


def _declare_attackers(game, attacks):
    """Player 0 attacks: ``attacks`` is (attacker, player or AttackPermanent) pairs."""
    game.agents[PlayerId(0)] = FixedAgent(attackers={obj.id: at for obj, at in attacks})
    game.phase, game.step = Phase.COMBAT, Step.DECLARE_ATTACKERS
    declare_attackers(game)


def _declare_blockers(game, blocks=()):
    """Player 1 blocks: ``blocks`` is (blocker, attacker) pairs."""
    game.agents[PlayerId(1)] = FixedAgent(
        blockers={blocker.id: [attacker.id] for blocker, attacker in blocks}
    )
    game.step = Step.DECLARE_BLOCKERS
    declare_blockers(game)


# -- ninjutsu (CR 702.49) ------------------------------------------------------


def test_ninjutsu_waits_until_blockers_leave_an_attacker_unblocked(box):
    """The cost returns an unblocked attacker, and CR 509.1h makes an attacker
    neither blocked nor unblocked until blockers are declared - so there is
    nothing to pay with outside combat, while attackers are being declared,
    or once combat is over."""
    _need(box, "Ninja of the Deep Hours", "Grizzly Bears", "Runeclaw Bear", "Hill Giant")
    game = box.game
    ninja = _place(box, "Ninja of the Deep Hours", "hand")
    bears = _place(box, "Grizzly Bears", "battlefield")
    runeclaw = _place(box, "Runeclaw Bear", "battlefield")
    giant = _place(box, "Hill Giant", "battlefield", player=1)
    box.give_mana(2)
    ninjutsu = _index(game, ninja, "Ninjutsu")

    assert not _offered(game, 0, ninja, ninjutsu), "nothing is attacking"

    _declare_attackers(game, [(bears, 1), (runeclaw, 1)])
    assert not _offered(game, 0, ninja, ninjutsu), "attacking, but not yet unblocked"

    game.step = Step.DECLARE_BLOCKERS
    assert not _offered(game, 0, ninja, ninjutsu), "the step alone does not unblock anything"

    _declare_blockers(game, [(giant, runeclaw)])
    assert _offered(game, 0, ninja, ninjutsu)

    game.step = Step.END_OF_COMBAT
    assert _offered(game, 0, ninja, ninjutsu), "still unblocked until combat ends"

    end_combat(game)
    assert not _offered(game, 0, ninja, ninjutsu)


def test_a_blocked_attacker_cannot_pay_for_ninjutsu(box):
    _need(box, "Ninja of the Deep Hours", "Runeclaw Bear", "Hill Giant")
    game = box.game
    ninja = _place(box, "Ninja of the Deep Hours", "hand")
    runeclaw = _place(box, "Runeclaw Bear", "battlefield")
    giant = _place(box, "Hill Giant", "battlefield", player=1)
    box.give_mana(2)

    _declare_attackers(game, [(runeclaw, 1)])
    _declare_blockers(game, [(giant, runeclaw)])

    assert not _offered(game, 0, ninja, _index(game, ninja, "Ninjutsu"))


def test_ninjutsu_returns_the_unblocked_attacker_and_the_ninja_attacks_in_its_place(box):
    """CR 702.49a and 508.4: the unblocked attacker goes back to hand as the
    cost, and the Ninja enters tapped and attacking without ever having been
    declared - unblocked, so its damage lands on the player."""
    _need(box, "Ninja of the Deep Hours", "Grizzly Bears", "Runeclaw Bear", "Hill Giant")
    game = box.game
    you, opponent = game.player(PlayerId(0)), game.player(PlayerId(1))
    ninja = _place(box, "Ninja of the Deep Hours", "hand")
    bears = _place(box, "Grizzly Bears", "battlefield")
    runeclaw = _place(box, "Runeclaw Bear", "battlefield")
    giant = _place(box, "Hill Giant", "battlefield", player=1)
    box.give_mana(2)
    pool = you.mana_pool.total
    _declare_attackers(game, [(bears, 1), (runeclaw, 1)])
    _declare_blockers(game, [(giant, runeclaw)])

    (ninjutsu,) = _offered(game, 0, ninja, _index(game, ninja, "Ninjutsu"))
    assert _perform(game, PlayerId(0), ninjutsu)
    assert you.mana_pool.total == pool - 2
    assert "Grizzly Bears" in _names(game, you.hand), "the unblocked attacker paid"
    assert game.combat.is_attacking(runeclaw.id), "the blocked one could not"
    assert ninja.id in you.hand, "the Ninja waits in hand until the ability resolves"

    box.resolve_top()

    entered = _live(game, "Ninja of the Deep Hours", Zone.BATTLEFIELD)
    assert entered.controller == PlayerId(0)
    assert entered.tapped
    assert game.combat.attacking.get(entered.id) == PlayerId(1)
    assert not game.combat.is_blocked(entered.id)
    assert not entered.attacked_this_turn, "it attacks, but it never attacked (CR 508.4)"

    life = opponent.life
    game.step = Step.COMBAT_DAMAGE
    deal_combat_damage(game)
    assert opponent.life == life - 2


def test_the_ninja_attacks_whatever_the_returned_creature_was_attacking(box):
    """CR 702.49c: the same planeswalker, not the first opponent at the table."""
    _need(box, "Ninja of the Deep Hours", "Memnite", "Jace Beleren")
    game = box.game
    ninja = _place(box, "Ninja of the Deep Hours", "hand")
    memnite = _place(box, "Memnite", "battlefield")
    jace = _place(box, "Jace Beleren", "battlefield", player=1)
    jace.add_counters("loyalty", 3)
    box.give_mana(2)
    _declare_attackers(game, [(memnite, AttackPermanent(jace.id))])
    _declare_blockers(game)

    (ninjutsu,) = _offered(game, 0, ninja, _index(game, ninja, "Ninjutsu"))
    assert _perform(game, PlayerId(0), ninjutsu)
    box.resolve_top()

    entered = _live(game, "Ninja of the Deep Hours", Zone.BATTLEFIELD)
    assert game.combat.attacking_permanent.get(entered.id) == jace.id
    assert game.combat.attacking.get(entered.id) == PlayerId(1)


def test_a_ninja_that_leaves_hand_in_response_is_not_put_onto_the_battlefield(box):
    """The ability puts *this card* onto the battlefield from hand. Discarded
    in response it is a new object in a graveyard (CR 400.7) and stays there,
    and the creature returned for the cost stays returned."""
    _need(box, "Ninja of the Deep Hours", "Grizzly Bears")
    game = box.game
    you = game.player(PlayerId(0))
    ninja = _place(box, "Ninja of the Deep Hours", "hand")
    bears = _place(box, "Grizzly Bears", "battlefield")
    box.give_mana(2)
    _declare_attackers(game, [(bears, 1)])
    _declare_blockers(game)

    (ninjutsu,) = _offered(game, 0, ninja, _index(game, ninja, "Ninjutsu"))
    assert _perform(game, PlayerId(0), ninjutsu)
    discard(game, ninja)
    box.resolve_top()

    assert _names(game, you.graveyard) == ["Ninja of the Deep Hours"]
    assert _names(game, you.hand) == ["Grizzly Bears"]
    assert not list(game.permanents(PlayerId(0)))


def test_commander_ninjutsu_works_from_the_command_zone_and_ninjutsu_does_not(box):
    """CR 702.49d: commander ninjutsu also functions in the command zone.
    Plain ninjutsu functions only in hand (CR 702.49a)."""
    _need(box, "Yuriko, the Tiger's Shadow", "Ninja of the Deep Hours", "Grizzly Bears")
    game = box.game
    yuriko = _place(box, "Yuriko, the Tiger's Shadow", "command")
    yuriko.is_commander = True
    stranded = _place(box, "Ninja of the Deep Hours", "command")
    bears = _place(box, "Grizzly Bears", "battlefield")
    box.give_mana(2)
    _declare_attackers(game, [(bears, 1)])
    _declare_blockers(game)

    assert not _offered(game, 0, stranded)
    (ninjutsu,) = _offered(game, 0, yuriko, _index(game, yuriko, "Commander ninjutsu"))
    assert _perform(game, PlayerId(0), ninjutsu)
    box.resolve_top()

    entered = _live(game, "Yuriko, the Tiger's Shadow", Zone.BATTLEFIELD)
    assert entered.tapped
    assert game.combat.attacking.get(entered.id) == PlayerId(1)
    assert yuriko.id not in game.command
    assert _names(game, game.player(PlayerId(0)).hand) == ["Grizzly Bears"]


# -- transmute (CR 702.53) -----------------------------------------------------


def test_transmute_is_offered_from_hand_only_at_sorcery_speed(box):
    """CR 702.53a: "Activate only as a sorcery" - your main phase, empty stack."""
    _need(box, "Muddle the Mixture")
    game = box.game
    muddle = _place(box, "Muddle the Mixture", "hand")
    box.give_mana(3)
    transmute = _index(game, muddle, "Transmute")

    assert _offered(game, 0, muddle, transmute)

    game.phase, game.step = Phase.COMBAT, Step.DECLARE_BLOCKERS
    assert not _offered(game, 0, muddle, transmute), "not during combat"

    game.phase, game.step = Phase.PRECOMBAT_MAIN, Step.MAIN
    game.active_player = PlayerId(1)
    assert not _offered(game, 0, muddle, transmute), "not on an opponent's turn"


def test_transmute_finds_only_a_card_with_the_discarded_cards_mana_value(box):
    """CR 702.53a: the same mana value as the discarded card. By the time the
    search happens the card is in the graveyard, so the value is last-known
    information about the card that was discarded."""
    _need(box, "Muddle the Mixture", "Sol Ring", "Serra Angel", "Island", "Grizzly Bears")
    game = box.game
    you = game.player(PlayerId(0))
    # The match is last, so a search that ignored mana value would take Sol Ring.
    for name in ("Sol Ring", "Serra Angel", "Island", "Grizzly Bears"):
        box.put(name, "library", 0)
    muddle = _place(box, "Muddle the Mixture", "hand")
    box.give_mana(3)

    (transmute,) = _offered(game, 0, muddle, _index(game, muddle, "Transmute"))
    assert _perform(game, PlayerId(0), transmute)
    assert _names(game, you.graveyard) == ["Muddle the Mixture"], "discarded to pay"
    box.resolve_top()

    assert _names(game, you.hand) == ["Grizzly Bears"]
    assert sorted(_names(game, you.library)) == ["Island", "Serra Angel", "Sol Ring"]


def test_transmute_takes_nothing_when_no_card_has_that_mana_value(box):
    _need(box, "Muddle the Mixture", "Sol Ring", "Serra Angel")
    game = box.game
    you = game.player(PlayerId(0))
    for name in ("Sol Ring", "Serra Angel"):
        box.put(name, "library", 0)
    muddle = _place(box, "Muddle the Mixture", "hand")
    box.give_mana(3)

    (transmute,) = _offered(game, 0, muddle, _index(game, muddle, "Transmute"))
    assert _perform(game, PlayerId(0), transmute)
    box.resolve_top()

    assert not you.hand
    assert sorted(_names(game, you.library)) == ["Serra Angel", "Sol Ring"]


@pytest.mark.parametrize("name", ["Ninjutsu", "Commander ninjutsu", "Transmute"])
def test_ninjutsu_and_transmute_read_as_implemented(name):
    """Keyword status is derived from what the builders produce, so with
    nothing left unmodelled these are no longer partial."""
    spec = keywords.lookup(name)
    assert spec is not None and spec.status is Status.IMPLEMENTED


@pytest.mark.parametrize("name", ["Cycling", "Plainscycling", "Channel", "Reinforce", "Transmute"])
def test_a_discard_this_card_keyword_discards_its_own_card(name):
    """The reminder text's "Discard this card" is part of the cost, and it
    means this card: without it the card never leaves hand."""
    ability = build(KeywordInstance(name, cost=Cost.mana("{1}"), amount=1, effects=DRAW_A_CARD))[0]

    assert ability.functions_in == {Zone.HAND}
    discards = [c for c in ability.cost.components if c.kind is CostKind.DISCARD]
    assert len(discards) == 1
    assert discards[0].filter is not None and discards[0].filter.source_only
