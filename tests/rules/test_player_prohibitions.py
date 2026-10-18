"""Prohibitions on players (CR 101.2), on a board.

"Your opponents can't cast spells", "target player can't play lands",
"players can't gain life": who is forbidden is the meaning of these cards.
Asked of the card being cast or played, the restriction used to compare it
with a filter for spells *on the stack* or lands *on the battlefield* - where
the card is going, not where it is - so none of them stopped anything, and the
round-trip printed them as "all object in stack can't cast_spell", a lock on
everyone including the caster.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.kernel.enums import Phase, Step, Zone
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


def _casts(box, player):
    return [a["name"] for a in box.legal(player) if a["kind"] == "CAST_SPELL"]


def _plays(box, player):
    return [a["name"] for a in box.legal(player) if a["kind"] == "PLAY_LAND"]


def _cast(box, name, player=0, target_player=None):
    index = next(a["index"] for a in box.legal(player) if a["name"] == name)
    targets = None
    if target_player is not None:
        groups = box.targets_for(index, player)
        targets = [
            [c["id"] for c in g["candidates"] if c["is_player"] and c["controller"] == target_player]
            for g in groups
        ]
    result = box.perform(index, player, targets)
    assert not result.get("error"), result.get("error")
    box.resolve_top()


def _bolts_in_hand(box):
    for player in (0, 1):
        box.put("Lightning Bolt", "hand", player)
        box.give_mana(5, player)


def test_silence_stops_the_opponents_and_not_its_caster(box):
    box.put("Silence", "hand", 0)
    _bolts_in_hand(box)
    _cast(box, "Silence")

    assert "Lightning Bolt" in _casts(box, 0)
    assert "Lightning Bolt" not in _casts(box, 1)


def test_silence_ends_at_end_of_turn(box):
    box.put("Silence", "hand", 0)
    _bolts_in_hand(box)
    _cast(box, "Silence")
    assert "Lightning Bolt" not in _casts(box, 1)

    box.next_turn()
    box.give_mana(5, 1)
    assert "Lightning Bolt" in _casts(box, 1)


def test_target_player_is_the_player_chosen(box):
    """Orim's Chant: "target player" is chosen as the spell is cast
    (CR 601.2c) and only that player is stopped."""
    box.put("Orim's Chant", "hand", 0)
    _bolts_in_hand(box)
    _cast(box, "Orim's Chant", target_player=1)

    assert "Lightning Bolt" in _casts(box, 0)
    assert "Lightning Bolt" not in _casts(box, 1)


def test_a_spell_filter_narrows_what_is_forbidden(box):
    """Cease-Fire: "target player can't cast creature spells" leaves their
    instants alone."""
    box.put("Cease-Fire", "hand", 0)
    _bolts_in_hand(box)
    box.put("Grizzly Bears", "hand", 1)
    _cast(box, "Cease-Fire", target_player=1)
    box.next_turn()  # player 1's turn: a creature would be castable
    box.give_mana(5, 1)
    assert "Lightning Bolt" in _casts(box, 1)


def test_a_static_prohibition_on_opponents(box):
    """Kutzil: "Your opponents can't cast spells during your turn."."""
    box.put("Kutzil, Malamet Exemplar", "battlefield", 0)
    _bolts_in_hand(box)
    assert "Lightning Bolt" in _casts(box, 0)
    assert "Lightning Bolt" not in _casts(box, 1)


def test_abilities_of_the_named_types_only(box):
    """Grand Abolisher: the spells are any spells; the abilities are those of
    artifacts, creatures and enchantments - mana abilities included - and not
    of lands."""
    box.put("Grand Abolisher", "battlefield", 0)
    box.put("Mind Stone", "battlefield", 1)
    box.put("Ghost Quarter", "battlefield", 1)
    box.give_mana(5, 1)
    activations = [
        a["name"] for a in box.legal(1) if a["kind"].startswith("ACTIVATE")
    ]
    assert "Mind Stone" not in activations
    assert "Ghost Quarter" in activations
    mine = [a["name"] for a in box.legal(0) if a["kind"].startswith("ACTIVATE")]
    box.put("Mind Stone", "battlefield", 0)
    box.give_mana(5, 0)
    mine = [a["name"] for a in box.legal(0) if a["kind"].startswith("ACTIVATE")]
    assert "Mind Stone" in mine


def test_split_second_stops_casting_from_hand(box):
    """CR 702.61a: while the spell is on the stack nobody casts spells - asked
    of the card in hand, not just of the player."""
    box.put("Sudden Shock", "hand", 0)
    _bolts_in_hand(box)
    box.give_mana(5, 0)
    index = next(a["index"] for a in box.legal(0) if a["name"] == "Sudden Shock")
    groups = box.targets_for(index, 0)
    targets = [[next(c["id"] for c in g["candidates"] if c["is_player"] and c["controller"] == 1)] for g in groups]
    assert not box.perform(index, 0, targets).get("error")
    assert "Lightning Bolt" not in _casts(box, 0)
    assert "Lightning Bolt" not in _casts(box, 1)


def test_players_cant_play_lands(box):
    box.put("Forest", "hand", 0)
    assert "Forest" in _plays(box, 0)
    box.put("Territorial Dispute", "battlefield", 1)
    assert "Forest" not in _plays(box, 0)


def test_target_player_cant_play_lands(box):
    """Turf Wound on yourself, so the land drop is in reach this turn."""
    box.put("Turf Wound", "hand", 0)
    box.put("Forest", "hand", 0)
    box.give_mana(5, 0)
    _cast(box, "Turf Wound", target_player=0)
    assert "Forest" not in _plays(box, 0)


def test_defending_player_of_a_static_ability(box):
    """Wardscale Dragon: the defending player, from the combat (CR 506.2)."""
    from mtgfish.rules.cr500_turn_structure.cr506_combat import Combat

    box.put("Wardscale Dragon", "battlefield", 0)
    _bolts_in_hand(box)
    assert "Lightning Bolt" in _casts(box, 1)
    dragon = next(
        o for o in box.game.objects.values()
        if o.card is not None and o.card.name == "Wardscale Dragon" and o.zone is Zone.BATTLEFIELD
    )
    box.game.phase = Phase.COMBAT
    box.game.step = Step.DECLARE_BLOCKERS
    box.game.combat = Combat(attacking={dragon.id: 1})
    box.game.invalidate_characteristics()
    assert "Lightning Bolt" not in _casts(box, 1)
    assert "Lightning Bolt" in _casts(box, 0)


def test_opponents_cant_gain_life(box):
    from mtgfish.rules.cr100_game_concepts.actions import gain_life

    box.put("Erebos, God of the Dead", "battlefield", 0)
    assert gain_life(box.game, PlayerId(1), 3) == 0
    assert gain_life(box.game, PlayerId(0), 3) == 3


def test_players_cant_draw(box):
    box.put("Omen Machine", "battlefield", 0)
    box.put("Island", "library", 1, count=3)
    before = len(box.game.player(PlayerId(1)).hand)
    box.game.draw(PlayerId(1), 2)
    assert len(box.game.player(PlayerId(1)).hand) == before


def test_a_life_total_that_cant_change(box):
    """Platinum Emperion: no gain, no loss, and life can't be paid
    (CR 119.7, 119.8)."""
    from mtgfish.rules.cr100_game_concepts.actions import gain_life, lose_life

    box.put("Platinum Emperion", "battlefield", 0)
    life = box.game.player(PlayerId(0)).life
    gain_life(box.game, PlayerId(0), 3)
    lose_life(box.game, PlayerId(0), 3)
    assert box.game.player(PlayerId(0)).life == life
    lose_life(box.game, PlayerId(1), 3)
    assert box.game.player(PlayerId(1)).life == life - 3


def test_silence_round_trip_names_the_players(card_db):
    from mtgfish.parser import parse_card
    from mtgfish.parser.explain import explain_ability

    ability = parse_card(card_db.lookup("Silence")).faces[0].abilities[0]
    said = explain_ability(ability)
    assert "each opponent can't cast" in said.lower()
    assert "until end of turn" in said


def _card(box, name, zone, player):
    return next(
        o for o in box.game.objects.values()
        if o.card is not None and o.card.name == name and o.zone is zone
        and o.owner == player
    )


def test_silence_stops_a_cast_an_effect_makes(box):
    """CR 601.3: the prohibition is asked of every cast, not only of the
    casts offered at priority - a cascade or "you may cast it" is a cast."""
    from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
        CastError,
        cast_spell,
    )

    box.put("Silence", "hand", 0)
    _bolts_in_hand(box)
    _cast(box, "Silence")
    bolt = _card(box, "Lightning Bolt", Zone.HAND, PlayerId(1))
    bolt.cast_without_paying = True
    with pytest.raises(CastError):
        cast_spell(box.game, PlayerId(1), Action(ActionKind.CAST_SPELL, source=bolt.id))
    assert bolt.zone is Zone.HAND


def test_orims_chant_ends_at_end_of_turn(box):
    box.put("Orim's Chant", "hand", 0)
    _bolts_in_hand(box)
    _cast(box, "Orim's Chant", target_player=1)
    assert "Lightning Bolt" not in _casts(box, 1)
    box.next_turn()
    box.give_mana(5, 1)
    assert "Lightning Bolt" in _casts(box, 1)


def test_grand_abolisher_only_during_its_controllers_turn(box):
    box.put("Grand Abolisher", "battlefield", 0)
    _bolts_in_hand(box)
    assert "Lightning Bolt" in _casts(box, 0)
    assert "Lightning Bolt" not in _casts(box, 1)
    box.next_turn()  # player 1's turn
    box.give_mana(5, 1)
    assert "Lightning Bolt" in _casts(box, 1)


def test_drannith_magistrate_stops_casting_from_anywhere_but_the_hand(box):
    """"Your opponents can't cast spells from anywhere other than their
    hands": a flashback card in an opponent's graveyard stays there, their
    hand is open, and the Magistrate's controller is not touched."""
    box.put("Drannith Magistrate", "battlefield", 0)
    for player in (0, 1):
        box.put("Think Twice", "graveyard", player)
    _bolts_in_hand(box)
    box.next_turn()  # player 1's turn, so sorcery-speed timing is no factor
    for player in (0, 1):
        box.give_mana(5, player)

    assert "Lightning Bolt" in _casts(box, 1)
    assert "Think Twice" not in _casts(box, 1)
    assert "Think Twice" in _casts(box, 0)


def _attack_allowed(box, name, owner, defender):
    from mtgfish.rules.cr500_turn_structure.cr506_combat import _attack_is_permitted

    attacker = _card(box, name, Zone.BATTLEFIELD, PlayerId(owner))
    return _attack_is_permitted(box.game, attacker, defender)


def test_creatures_cant_attack_you_protects_only_you(box):
    """Blazing Archon: "Creatures can't attack you" (CR 508.1c). Read as
    "can't attack" it also kept its controller's own creatures home."""
    box.put("Blazing Archon", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 1)

    assert not _attack_allowed(box, "Grizzly Bears", 1, 0)
    assert _attack_allowed(box, "Grizzly Bears", 0, 1)


def test_or_planeswalkers_you_control(box):
    """Sandwurm Convergence: fliers can't attack you or your planeswalkers;
    a creature without flying can attack either."""
    from mtgfish.rules.cr500_turn_structure.cr506_combat import AttackPermanent

    box.put("Sandwurm Convergence", "battlefield", 0)
    box.put("Jace Beleren", "battlefield", 0)
    box.put("Serra Angel", "battlefield", 1)
    box.put("Grizzly Bears", "battlefield", 1)
    jace = _card(box, "Jace Beleren", Zone.BATTLEFIELD, PlayerId(0))

    assert not _attack_allowed(box, "Serra Angel", 1, 0)
    assert not _attack_allowed(box, "Serra Angel", 1, AttackPermanent(jace.id))
    assert _attack_allowed(box, "Grizzly Bears", 1, 0)
    assert _attack_allowed(box, "Grizzly Bears", 1, AttackPermanent(jace.id))


def test_from_your_hand_belongs_to_the_land_play_too(box):
    """Experimental Frenzy: "You can't play lands or cast spells from your
    hand" - the hand is where neither may come from; a land on top of the
    library is what the card is for."""
    box.put("Experimental Frenzy", "battlefield", 0)
    box.put("Forest", "hand", 0)
    assert "Forest" not in _plays(box, 0)
    from mtgfish.parser import parse_card
    from mtgfish.rules.cr500_turn_structure.restrictions import Act
    from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind

    ability = next(
        a for a in parse_card(box.db.lookup("Experimental Frenzy")).faces[0].abilities
        if any(e.kind is EffectKind.RESTRICTION for e in a.effects)
    )
    plays = [
        r for e in ability.effects for r in e.restrictions if r.act is Act.PLAY_LAND
    ]
    assert plays and all(r.subject.zones == frozenset({Zone.HAND}) for r in plays)
