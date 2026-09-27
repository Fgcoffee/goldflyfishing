"""Edicts that target a player: "target player sacrifices a creature".

The word "target" belongs to the player (CR 115.1). The player is chosen as
the spell is cast (CR 601.2c), sacrifices a permanent they choose from what
they control (CR 701.21a, 608.2d), and a spell whose only target has become
illegal does nothing at all (CR 608.2b).

The parser read the player of an edict as untargeted, and the engine then
resolved "target player" to nobody: Diabolic Edict cast at the opponent left
their only creature alive, and still counted as understood. "Target
opponent" meant every opponent. "Exile up to one other target creature"
(Solitude) was read as untargeted too, one word too deep for the lookahead.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mtgfish.parser.compile import parse_card
from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import CastError
from mtgfish.rules.kernel.enums import Color, Zone
from mtgfish.rules.kernel.ids import PlayerId, player_target
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox


class Chooser(PassiveOpponent):
    """Sacrifices the named creature when asked, and records being asked."""

    def __init__(self, pick: str = "") -> None:
        self.pick = pick
        self.asked = 0

    def choose_objects(self, game, player, effect, candidates, count):
        self.asked += 1
        named = [obj for obj in candidates if obj.card.name == self.pick]
        return named[:count] or candidates[:count]


class AtTheOpponent(PassiveOpponent):
    """Aims every target at player 1 where that is on offer."""

    def choose_targets(self, game, player, source, candidates):
        return tuple(
            (player_target(1),) if player_target(1) in group else tuple(group[:1])
            for group in candidates
        )


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


def _on_battlefield(game, name, controller=None):
    return [
        obj
        for obj in game.objects.values()
        if obj.card is not None
        and obj.card.name == name
        and obj.zone is Zone.BATTLEFIELD
        and not obj.superseded_by
        and (controller is None or obj.controller == controller)
    ]


def _cast_action(box, player, name):
    game = box.game
    return next(
        action
        for action in legal_actions(game, player)
        if action.kind.name.startswith("CAST")
        and game.objects[action.source].card.name == name
    )


def _cast(box, player, name, targets):
    action = _cast_action(box, player, name)
    assert _perform(box.game, player, replace(action, targets=targets))


def _add_third_player(box):
    from mtgfish.rules.cr100_game_concepts.player import Player

    game = box.game
    game.players.append(Player(id=PlayerId(2), name="Third"))
    game.player(PlayerId(2)).life = 40
    game.turn_order.append(PlayerId(2))
    game.agents[PlayerId(2)] = PassiveOpponent()


def _sacrifice_node(db, name):
    for face in parse_card(db.lookup(name)).faces:
        for ability in face.abilities:
            if ability.unparsed:
                continue
            for effect in ability.effects:
                for node in effect.walk():
                    if node.kind.name == "SACRIFICE":
                        return node
    pytest.skip(f"{name} has no understood sacrifice")


# ---------------------------------------------------------------------------
# The IR: the player is the target, the creature is not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Diabolic Edict", "Cruel Edict", "Geth's Verdict"])
def test_the_player_of_a_targeted_edict_is_a_target(box, name):
    _need(box, name)
    node = _sacrifice_node(box.db, name)
    assert node.is_targeted and node.targets_its_player and node.targets_a_player


@pytest.mark.parametrize("name", ["Innocent Blood", "Fleshbag Marauder"])
def test_an_each_player_edict_targets_nobody(box, name):
    _need(box, name)
    node = _sacrifice_node(box.db, name)
    assert not node.is_targeted


# ---------------------------------------------------------------------------
# On a board
# ---------------------------------------------------------------------------


def test_diabolic_edict_kills_the_target_players_only_creature(box):
    _need(box, "Diabolic Edict", "Grizzly Bears")
    game = box.game
    box.put("Grizzly Bears", "battlefield", 1)
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Diabolic Edict", "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.BLACK)

    _cast(box, 0, "Diabolic Edict", ((player_target(1),),))
    box.resolve_top()

    assert _on_battlefield(game, "Grizzly Bears", 1) == []
    assert len(_on_battlefield(game, "Grizzly Bears", 0)) == 1


def test_the_target_player_chooses_what_to_sacrifice(box):
    _need(box, "Diabolic Edict", "Grizzly Bears", "Hill Giant")
    game = box.game
    caster, victim = Chooser(), Chooser(pick="Hill Giant")
    game.agents[0], game.agents[1] = caster, victim
    box.put("Grizzly Bears", "battlefield", 1)
    box.put("Hill Giant", "battlefield", 1)
    box.put("Diabolic Edict", "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.BLACK)

    _cast(box, 0, "Diabolic Edict", ((player_target(1),),))
    box.resolve_top()

    # Left to a default the cheaper Bears would go; the victim chose the Giant.
    assert _on_battlefield(game, "Hill Giant") == []
    assert len(_on_battlefield(game, "Grizzly Bears", 1)) == 1
    assert victim.asked == 1 and caster.asked == 0


def test_the_caster_may_target_themself(box):
    _need(box, "Diabolic Edict", "Grizzly Bears")
    game = box.game
    box.put("Grizzly Bears", "battlefield", 1)
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Diabolic Edict", "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.BLACK)

    _cast(box, 0, "Diabolic Edict", ((player_target(0),),))
    box.resolve_top()

    assert _on_battlefield(game, "Grizzly Bears", 0) == []
    assert len(_on_battlefield(game, "Grizzly Bears", 1)) == 1


def test_a_creature_is_not_a_legal_target_for_an_edict(box):
    _need(box, "Diabolic Edict", "Grizzly Bears")
    game = box.game
    box.put("Grizzly Bears", "battlefield", 1)
    bear = _on_battlefield(game, "Grizzly Bears", 1)[0]
    box.put("Diabolic Edict", "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.BLACK)
    action = _cast_action(box, 0, "Diabolic Edict")

    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell

    with pytest.raises(CastError):
        cast_spell(game, 0, replace(action, targets=((bear.id,),)))


def test_target_opponent_is_one_opponent_not_each(box):
    _need(box, "Cruel Edict", "Grizzly Bears")
    game = box.game
    _add_third_player(box)
    for player in (0, 1, 2):
        box.put("Grizzly Bears", "battlefield", player)
    box.put("Cruel Edict", "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.BLACK)

    _cast(box, 0, "Cruel Edict", ((player_target(2),),))
    box.resolve_top()

    assert [len(_on_battlefield(game, "Grizzly Bears", p)) for p in (0, 1, 2)] == [1, 1, 0]


def test_target_opponent_cannot_be_you(box):
    _need(box, "Cruel Edict")
    box.put("Cruel Edict", "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.BLACK)
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import _candidates_for

    spell = box.game.objects[_cast_action(box, 0, "Cruel Edict").source]
    node = _sacrifice_node(box.db, "Cruel Edict")
    assert _candidates_for(box.game, spell, node, 0) == [player_target(1)]


def test_geths_verdict_fizzles_when_its_target_player_is_gone(box):
    """CR 608.2b: the only target is illegal, so nothing happens - no
    sacrifice and no life loss for anybody."""
    _need(box, "Geth's Verdict", "Grizzly Bears")
    game = box.game
    _add_third_player(box)
    box.put("Grizzly Bears", "battlefield", 2)
    box.put("Grizzly Bears", "battlefield", 1)
    box.put("Geth's Verdict", "hand", 0)
    _mana(box, 0, 2, Color.BLACK)
    life = [p.life for p in game.players]

    _cast(box, 0, "Geth's Verdict", ((player_target(2),),))
    game.player(PlayerId(2)).has_lost = True
    box.resolve_top()

    assert [p.life for p in game.players] == life
    assert len(_on_battlefield(game, "Grizzly Bears", 1)) == 1


def test_geths_verdict_takes_a_creature_and_a_life_from_the_same_player(box):
    _need(box, "Geth's Verdict", "Grizzly Bears")
    game = box.game
    _add_third_player(box)
    box.put("Grizzly Bears", "battlefield", 1)
    box.put("Grizzly Bears", "battlefield", 2)
    box.put("Geth's Verdict", "hand", 0)
    _mana(box, 0, 2, Color.BLACK)
    life = [p.life for p in game.players]

    _cast(box, 0, "Geth's Verdict", ((player_target(2),),))
    box.resolve_top()

    assert [p.life for p in game.players] == [life[0], life[1], life[2] - 1]
    assert _on_battlefield(game, "Grizzly Bears", 2) == []
    assert len(_on_battlefield(game, "Grizzly Bears", 1)) == 1


def test_each_player_edict_is_unaffected(box):
    _need(box, "Innocent Blood", "Grizzly Bears")
    game = box.game
    box.put("Grizzly Bears", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 1)
    box.put("Innocent Blood", "hand", 0)
    _mana(box, 0, 1, Color.BLACK)

    _cast(box, 0, "Innocent Blood", ())
    box.resolve_top()

    assert _on_battlefield(game, "Grizzly Bears") == []


def test_archon_of_cruelty_takes_everything_from_the_one_opponent_it_targets(box):
    """"Target opponent sacrifices ..., discards a card, and loses 3 life":
    the elided subjects are the targeted opponent (CR 608.2c) - it was
    refused while the sacrifice did not target, having no player to copy."""
    _need(box, "Archon of Cruelty", "Grizzly Bears", "Forest")
    game = box.game
    box.put("Grizzly Bears", "battlefield", 1)
    box.put("Forest", "hand", 1)
    box.put("Forest", "library", 0)
    box.put("Archon of Cruelty", "hand", 0)
    _mana(box, 0, 6)
    _mana(box, 0, 2, Color.BLACK)
    life = [p.life for p in game.players]

    _cast(box, 0, "Archon of Cruelty", ())
    box.resolve_top()  # the Archon enters, and its trigger targets player 1
    trigger = game.objects[game.stack[-1]]
    assert trigger.targets == ((player_target(1),),)
    box.resolve_top()

    assert _on_battlefield(game, "Grizzly Bears") == []
    assert game.player(1).hand == []
    assert [p.life for p in game.players] == [life[0] + 3, life[1] - 3]
    assert len(game.player(0).hand) == 1


# ---------------------------------------------------------------------------
# "Exile target player's graveyard": the other half of the same construct
# ---------------------------------------------------------------------------


def test_bojuka_bog_exiles_the_whole_graveyard_of_its_target(box):
    _need(box, "Bojuka Bog", "Grizzly Bears")
    game = box.game
    game.agents[0] = AtTheOpponent()
    box.put("Grizzly Bears", "graveyard", 1, count=3)
    box.put("Grizzly Bears", "graveyard", 0, count=2)
    box.put("Bojuka Bog", "hand", 0)
    play = next(
        action
        for action in legal_actions(game, 0)
        if action.kind.name == "PLAY_LAND"
        and game.objects[action.source].card.name == "Bojuka Bog"
    )
    assert _perform(game, 0, play)
    box.settle()
    trigger = game.objects[game.stack[-1]]
    assert trigger.targets == ((player_target(1),),)
    box.resolve_top()

    assert game.player(1).graveyard == []
    assert len(game.player(0).graveyard) == 2


# ---------------------------------------------------------------------------
# Solitude: "exile up to one other target creature"
# ---------------------------------------------------------------------------


def _cast_solitude(box):
    box.put("Solitude", "hand", 0)
    _mana(box, 0, 3)
    _mana(box, 0, 2, Color.WHITE)
    _cast(box, 0, "Solitude", ())
    box.resolve_top()  # Solitude enters; its trigger goes on the stack.


def test_solitude_exiles_and_its_controller_gains_life(box):
    """"That creature's controller gains life equal to its power": the
    exiled creature's power as it last existed (CR 608.2h) - Grizzly Bears'
    2, not Solitude's own 3."""
    _need(box, "Solitude", "Grizzly Bears")
    game = box.game
    box.put("Grizzly Bears", "battlefield", 1)
    life = [p.life for p in game.players]

    _cast_solitude(box)
    trigger = game.objects[game.stack[-1]]
    bears = _on_battlefield(game, "Grizzly Bears", 1)[0]
    assert trigger.targets == ((bears.id,),)
    box.resolve_top()

    assert _on_battlefield(game, "Grizzly Bears") == []
    assert [p.life for p in game.players] == [life[0], life[1] + 2]


def test_solitude_fizzles_when_its_target_is_gone(box):
    _need(box, "Solitude", "Hill Giant")
    game = box.game
    box.put("Hill Giant", "battlefield", 1)
    life = [p.life for p in game.players]

    _cast_solitude(box)
    giant = _on_battlefield(game, "Hill Giant", 1)[0]
    game.move_object(giant, Zone.GRAVEYARD, to_player=giant.owner)
    box.resolve_top()

    assert [p.life for p in game.players] == life


def test_tribute_to_hunger_you_gain_the_sacrificed_toughness(box):
    _need(box, "Tribute to Hunger", "Hill Giant")
    game = box.game
    box.put("Hill Giant", "battlefield", 1)
    box.put("Tribute to Hunger", "hand", 0)
    _mana(box, 0, 2)
    _mana(box, 0, 1, Color.BLACK)
    life = [p.life for p in game.players]

    _cast(box, 0, "Tribute to Hunger", ((player_target(1),),))
    box.resolve_top()

    assert _on_battlefield(game, "Hill Giant") == []
    assert [p.life for p in game.players] == [life[0] + 3, life[1]]


def test_swords_to_plowshares_gains_the_exiled_creatures_power(box):
    """"Its controller gains life equal to its power" is the exiled
    creature's power (CR 608.2h). Read off the source - an instant - it was
    always 0."""
    _need(box, "Swords to Plowshares", "Hill Giant")
    game = box.game
    box.put("Hill Giant", "battlefield", 1)
    giant = _on_battlefield(game, "Hill Giant", 1)[0]
    box.put("Swords to Plowshares", "hand", 0)
    _mana(box, 0, 1, Color.WHITE)
    life = [p.life for p in game.players]

    _cast(box, 0, "Swords to Plowshares", ((giant.id,),))
    box.resolve_top()

    assert _on_battlefield(game, "Hill Giant") == []
    assert [p.life for p in game.players] == [life[0], life[1] + 3]


def test_devour_flesh_the_target_player_gains_the_sacrificed_toughness(box):
    """"Target player sacrifices a creature of their choice, then gains life
    equal to that creature's toughness": the player who sacrificed gains."""
    _need(box, "Devour Flesh", "Hill Giant")
    game = box.game
    box.put("Hill Giant", "battlefield", 1)
    box.put("Devour Flesh", "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.BLACK)
    life = [p.life for p in game.players]

    _cast(box, 0, "Devour Flesh", ((player_target(1),),))
    box.resolve_top()

    assert _on_battlefield(game, "Hill Giant") == []
    assert [p.life for p in game.players] == [life[0], life[1] + 3]


# ---------------------------------------------------------------------------
# "Becomes a Frog with base power and toughness 1/1": the base is the target's
# ---------------------------------------------------------------------------


def test_turn_to_frog_sets_the_base_of_its_target_only(box):
    """The base power and toughness half used to be untargeted, so every
    creature on the battlefield became 1/1 (CR 115.1, 613.4b)."""
    _need(box, "Turn to Frog", "Hill Giant")
    game = box.game
    box.put("Hill Giant", "battlefield", 1)
    box.put("Hill Giant", "battlefield", 0)
    target = _on_battlefield(game, "Hill Giant", 1)[0]
    mine = _on_battlefield(game, "Hill Giant", 0)[0]
    box.put("Turn to Frog", "hand", 0)
    _mana(box, 0, 1)
    _mana(box, 0, 1, Color.BLUE)

    _cast(box, 0, "Turn to Frog", ((target.id,), (target.id,), (target.id,)))
    box.resolve_top()

    frog = game.characteristics(target)
    giant = game.characteristics(mine)
    assert (frog.power, frog.toughness) == (1, 1)
    assert (giant.power, giant.toughness) == (3, 3)


# ---------------------------------------------------------------------------
# What stays unread
# ---------------------------------------------------------------------------


def _readable(db, name, fragment):
    for face in parse_card(db.lookup(name)).faces:
        for ability in face.abilities:
            if fragment in ability.text:
                return not ability.unparsed
    raise AssertionError(f"{name} has no ability with {fragment!r}")


@pytest.mark.parametrize(
    "name, fragment",
    [
        # "Another target creature" after a target means other than that
        # target (CR 115.3); the filter can only say "other than the source".
        ("Consume Strength", "Another target creature"),
        ("Deadshot", "another target creature"),
        # Two objects, one of each - not "an artifact or a land".
        ("Structural Collapse", "an artifact and a land"),
        ("Perilous Predicament", "an artifact creature and"),
        # "A permanent of their choice for each soot counter": a sacrifice
        # has no amount to multiply, and one permanent was sacrificed.
        ("Smokestack", "for each soot counter"),
    ],
)
def test_what_the_engine_cannot_say_stays_unread(box, name, fragment):
    _need(box, name)
    assert not _readable(box.db, name, fragment)


@pytest.mark.parametrize(
    "name, fragment",
    [
        # One "target" shared by two verbs is one target, not an earlier one.
        ("Ashroot Animist", "another target creature"),
        ("Lizard, Connors's Curse", "up to one other target creature"),
        ("Solitude", "exile up to one other target creature"),
    ],
)
def test_one_target_shared_by_two_verbs_is_read(box, name, fragment):
    _need(box, name)
    assert _readable(box.db, name, fragment)
