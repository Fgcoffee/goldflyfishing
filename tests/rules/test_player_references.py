"""Who "that player", "its controller" and "defending player" are.

Each of these names a player by reference to something else the ability
mentioned, and every one of them used to resolve to nobody - "that player"
and "they" were read as an untargeted "target player", "its controller" as
the controller of no object in particular. So Beast Within made no token,
Swords to Plowshares gained nobody life, Rhystic Study's tax asked nobody
(and you always drew), Mana Leak asked nobody (and always countered), and
Smothering Tithe had *you* decide whether to pay {2}.

The parser now binds each reference to a scope the resolution can answer
(``mtgfish.parser.referents``), and these tests prove each one on a board.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.compile import parse_card
from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr117_priority import _perform, settle
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.ids import player_target
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox


class Agreeable(PassiveOpponent):
    """Takes every option and pays every tax, and remembers being asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def choose_optional(self, game, player, effect):
        self.asked.append("optional")
        return True

    def choose_pay(self, game, player, cost, effect):
        self.asked.append("pay")
        return True


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


def _find(game, name, zone=Zone.BATTLEFIELD, controller=None):
    return next(
        obj
        for obj in game.objects.values()
        if obj.card is not None
        and obj.card.name == name
        and obj.zone is zone
        and not obj.superseded_by
        and (controller is None or obj.controller == controller)
    )


def _effects(db, name, text_part=""):
    for face in parse_card(db.lookup(name)).faces:
        for ability in face.abilities:
            if text_part in ability.text and not ability.unparsed:
                return ability.effects
    pytest.skip(f"{name} has no understood ability containing {text_part!r}")


def _cast(box, player, name, targets):
    game = box.game
    action = next(
        action
        for action in legal_actions(game, player)
        if action.kind.name.startswith("CAST")
        and game.objects[action.source].card.name == name
    )
    from dataclasses import replace

    assert _perform(game, player, replace(action, targets=targets))


def _tokens(game, player):
    return [
        obj for obj in game.objects.values()
        if obj.zone is Zone.BATTLEFIELD and obj.controller == player
        and not obj.superseded_by and obj.kind.name == "TOKEN"
    ]


# ---------------------------------------------------------------------------
# "Its controller" of an object the spell acted on (CR 608.2h)
# ---------------------------------------------------------------------------


def test_beast_within_gives_the_token_to_the_destroyed_permanents_controller(box):
    _need(box, "Beast Within", "Grizzly Bears")
    game = box.game
    box.put("Grizzly Bears", "battlefield", 1)
    bear = _find(game, "Grizzly Bears")
    box.put("Beast Within", "hand", 0)
    box.give_mana(3, 0)

    _cast(box, 0, "Beast Within", ((bear.id,),))
    box.resolve_top()

    assert bear.superseded_by, "the bear was not destroyed"
    assert len(_tokens(game, 1)) == 1, "its controller got no Beast"
    assert _tokens(game, 0) == []


def test_its_controller_is_read_as_the_object_last_existed(box):
    """Nature's Claim: the artifact is in the graveyard when its controller
    gains the life - asked as it was on the battlefield, not as the card in
    the graveyard (which its owner controls, CR 400.7)."""
    _need(box, "Nature's Claim", "Sol Ring")
    game = box.game
    box.put("Sol Ring", "battlefield", 1)
    ring = _find(game, "Sol Ring")
    ring.controller = ring.base_controller = 0  # stolen: controlled by 0, owned by 1
    before = [p.life for p in game.players]
    box.put("Nature's Claim", "hand", 1)
    box.give_mana(1, 1)

    _cast(box, 1, "Nature's Claim", ((ring.id,),))
    box.resolve_top()

    assert ring.superseded_by, "not destroyed"
    assert [p.life for p in game.players] == [before[0] + 4, before[1]]


def test_another_players_may_is_theirs_to_decide_and_carry_out(box):
    """Path to Exile: the exiled creature's controller may search *their*
    library, and the land enters under *their* control (CR 608.2d)."""
    _need(box, "Path to Exile", "Grizzly Bears", "Forest")
    game = box.game
    opponent = Agreeable()
    game.agents[1] = opponent
    box.put("Grizzly Bears", "battlefield", 1)
    box.put("Forest", "library", 1)
    bear = _find(game, "Grizzly Bears")
    box.put("Path to Exile", "hand", 0)
    box.give_mana(1, 0)

    _cast(box, 0, "Path to Exile", ((bear.id,),))
    box.resolve_top()

    assert opponent.asked == ["optional"], "the controller of the exiled creature was not asked"
    forest = _find(game, "Forest")
    assert forest.controller == 1 and forest.owner == 1
    assert game.player(1).library == []


# ---------------------------------------------------------------------------
# "Unless its controller pays" - the object the guarded instruction acts on
# ---------------------------------------------------------------------------


def _mana(box, player, amount, color=None):
    """Exactly ``amount`` mana - ``Sandbox.give_mana`` adds that much of
    *every* colour, which would leave a player able to pay a tax the test
    means them to be unable to pay."""
    from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
    from mtgfish.rules.kernel.enums import Color

    box.game.player(player).mana_pool.add(ManaKind(color or Color.NONE), amount)


def _opponent_casts_bears(box):
    """The opponent casts Grizzly Bears in their own main phase - a creature
    spell is cast only by the active player, in a main phase, with an empty
    stack (CR 117.1a) - keeps priority (CR 117.3c) and passes it to you
    (CR 117.3d), and you respond. Their pool is empty afterwards."""
    from mtgfish.rules.kernel.enums import Color

    game = box.game
    game.active_player = game.priority_player = 1
    box.put("Grizzly Bears", "hand", 1)
    _mana(box, 1, 1)
    _mana(box, 1, 1, Color.GREEN)
    _cast(box, 1, "Grizzly Bears", ())
    game.priority_player = 0
    assert game.player(1).mana_pool.total == 0
    return _find(game, "Grizzly Bears", Zone.STACK)


def test_mana_leak_asks_the_spells_controller(box):
    _need(box, "Mana Leak", "Grizzly Bears")
    game = box.game
    opponent = Agreeable()
    game.agents[1] = opponent
    spell = _opponent_casts_bears(box)
    box.put("Mana Leak", "hand", 0)
    box.give_mana(2, 0)
    _mana(box, 1, 3)

    _cast(box, 0, "Mana Leak", ((spell.id,),))
    box.resolve_top()

    assert opponent.asked == ["pay"]
    assert game.player(1).mana_pool.total == 0, "the spell's controller did not pay"
    assert spell.zone is Zone.STACK and not spell.superseded_by, "paid, yet countered"


def test_mana_leak_counters_when_its_controller_cannot_pay(box):
    _need(box, "Mana Leak", "Grizzly Bears")
    game = box.game
    spell = _opponent_casts_bears(box)
    box.put("Mana Leak", "hand", 0)
    box.give_mana(2, 0)

    _cast(box, 0, "Mana Leak", ((spell.id,),))
    box.resolve_top()

    assert spell.superseded_by, "not countered"


# ---------------------------------------------------------------------------
# "That player" in a trigger - the player the event is about (CR 603.2)
# ---------------------------------------------------------------------------


def test_sheoldred_drains_the_opponent_who_drew(box):
    _need(box, "Sheoldred, the Apocalypse", "Forest")
    game = box.game
    box.put("Sheoldred, the Apocalypse", "battlefield", 0)
    box.put("Forest", "library", 1)

    before = [p.life for p in game.players]
    game.draw(1)
    settle(game)
    box.resolve_top()

    assert [p.life for p in game.players] == [before[0], before[1] - 2]


def test_smothering_tithe_asks_the_player_who_drew(box):
    _need(box, "Smothering Tithe", "Forest")
    game = box.game
    opponent = Agreeable()
    game.agents[1] = opponent
    box.put("Smothering Tithe", "battlefield", 0)
    box.put("Forest", "library", 1)
    _mana(box, 1, 2)
    _mana(box, 0, 2)

    game.draw(1)
    settle(game)
    box.resolve_top()

    assert opponent.asked == ["optional"]
    assert game.player(1).mana_pool.total == 0, "the drawer did not pay"
    assert game.player(0).mana_pool.total == 2, "the Tithe's controller paid"
    assert _tokens(game, 0) == [], "paid, yet a Treasure was made"


def test_that_creatures_controller_in_a_dies_trigger(box):
    """Fecundity-style: "that creature's controller" is the dead creature's
    controller as it last existed on the battlefield."""
    _need(box, "Massacre Wurm", "Grizzly Bears")
    game = box.game
    box.put("Massacre Wurm", "battlefield", 0)
    settle(game)
    while game.stack:
        # The Wurm's own enters trigger, resolved before the bear arrives:
        # its -2/-2 would otherwise kill the bear first. It does not reach a
        # creature that arrives later (CR 611.2c).
        box.resolve_top()
    box.put("Grizzly Bears", "battlefield", 1)
    bear = _find(game, "Grizzly Bears")
    settle(game)
    assert not bear.superseded_by and not game.stack
    life = game.player(1).life

    from mtgfish.rules.cr100_game_concepts import actions

    actions.destroy(game, bear)
    settle(game)
    box.resolve_top()

    assert game.player(1).life == life - 2


# ---------------------------------------------------------------------------
# "That player" after a targeted player (CR 608.2c)
# ---------------------------------------------------------------------------


def test_that_player_is_the_player_targeted_earlier(box):
    _need(box, "Compulsive Research", "Grizzly Bears")
    game = box.game
    for _ in range(4):
        box.put("Grizzly Bears", "library", 1)
    effects = _effects(box.db, "Compulsive Research", "Target player")

    execute(
        Resolution(game=game, source=0, controller=0, targets=((player_target(1),),)),
        effects,
    )

    # Drew three non-lands, so discarded two of them.
    assert len(game.player(1).hand) == 1
    assert len(game.player(1).graveyard) == 2
    assert game.player(0).hand == []


def test_they_in_the_unless_is_the_same_targeted_player(box):
    """"... unless they discard a land card": with lands drawn, the targeted
    player discards just one land - and you, with nothing, discard nothing."""
    _need(box, "Compulsive Research", "Island")
    game = box.game
    for _ in range(4):
        box.put("Island", "library", 1)
    effects = _effects(box.db, "Compulsive Research", "Target player")

    execute(
        Resolution(game=game, source=0, controller=0, targets=((player_target(1),),)),
        effects,
    )

    assert len(game.player(1).hand) == 2
    assert len(game.player(1).graveyard) == 1
    assert game.player(0).hand == [] and game.player(0).graveyard == []


# ---------------------------------------------------------------------------
# Defending player (CR 506.2, 802.2a)
# ---------------------------------------------------------------------------


def test_defending_player_is_the_player_the_creature_attacks(box):
    _need(box, "Silent Skimmer")
    from mtgfish.rules.cr500_turn_structure.cr506_combat import Combat

    game = box.game
    box.put("Silent Skimmer", "battlefield", 0)
    skimmer = _find(game, "Silent Skimmer")
    game.combat = Combat(attacking={skimmer.id: 1})
    effects = _effects(box.db, "Silent Skimmer", "defending player")

    before = [p.life for p in game.players]
    execute(Resolution(game=game, source=skimmer.id, controller=0), effects)

    assert [p.life for p in game.players] == [before[0], before[1] - 2]


def test_defending_player_outside_combat_is_nobody(box):
    _need(box, "Silent Skimmer")
    game = box.game
    box.put("Silent Skimmer", "battlefield", 0)
    skimmer = _find(game, "Silent Skimmer")
    game.combat = None
    effects = _effects(box.db, "Silent Skimmer", "defending player")
    before = [p.life for p in game.players]

    execute(Resolution(game=game, source=skimmer.id, controller=0), effects)

    assert [p.life for p in game.players] == before


def test_a_prohibition_on_the_defending_player_names_them(box):
    """Xantid Swarm: the standing "can't cast spells" is pinned to the player
    it meant when it resolved, not left as a reference nothing can answer."""
    _need(box, "Xantid Swarm")
    from mtgfish.rules.cr500_turn_structure.cr506_combat import Combat
    from mtgfish.rules.kernel.query import PlayerScope

    game = box.game
    box.put("Xantid Swarm", "battlefield", 0)
    swarm = _find(game, "Xantid Swarm")
    game.combat = Combat(attacking={swarm.id: 1})
    effects = _effects(box.db, "Xantid Swarm", "defending player")

    execute(Resolution(game=game, source=swarm.id, controller=0), effects)

    standing = [
        r for r in getattr(game, "standing_restrictions", ())
        if r.players is not None and r.players.scope is PlayerScope.SPECIFIC
    ]
    assert standing and standing[-1].players.specific == 1


# ---------------------------------------------------------------------------
# Mana for another player (CR 106.4)
# ---------------------------------------------------------------------------


def test_its_controller_adds_the_mana_from_a_triggered_mana_ability(box):
    _need(box, "Vernal Bloom", "Forest")
    game = box.game
    box.put("Forest", "battlefield", 1)
    forest = _find(game, "Forest")
    effects = _effects(box.db, "Vernal Bloom", "its controller")

    execute(
        Resolution(
            game=game,
            source=0,
            controller=0,
            trigger_event=Event(EventKind.MANA_ADDED, object_id=forest.id, player=1),
        ),
        effects,
    )

    assert game.player(1).mana_pool.total == 1
    assert game.player(0).mana_pool.total == 0


# ---------------------------------------------------------------------------
# A possessive in a trigger about the source itself names some other object
# ---------------------------------------------------------------------------


def test_the_controller_of_the_spell_that_targeted_it(box):
    """Forsaken Wastes: "that spell's controller" is the player whose spell
    targeted the Wastes (CR 603.2e) - not the Wastes' own controller, which
    is what "the object the trigger is about" would have been."""
    _need(box, "Forsaken Wastes", "Disenchant")
    from mtgfish.rules.kernel.enums import Color

    game = box.game
    box.put("Forsaken Wastes", "battlefield", 0)
    wastes = _find(game, "Forsaken Wastes")
    box.put("Disenchant", "hand", 1)
    _mana(box, 1, 1)
    _mana(box, 1, 1, Color.WHITE)
    game.priority_player = 1
    before = [p.life for p in game.players]

    _cast(box, 1, "Disenchant", ((wastes.id,),))
    settle(game)
    assert len(game.stack) == 2, "the Wastes did not trigger"
    box.resolve_top()

    assert [p.life for p in game.players] == [before[0], before[1] - 5]


@pytest.mark.parametrize(
    "name, text_part",
    [
        # The enchanted permanent, not the Aura that left.
        ("Reality Acid", "enchanted permanent's controller"),
        # Each attacking creature's controller, as attackers are declared
        # (CR 508.1h) - not the Spirit's controller.
        ("Forbidding Spirit", "unless their controller pays"),
        # The targeted player searches, but the land it "puts onto the
        # battlefield" would enter under the caster's control (CR 110.2a).
        ("Restorative Technique", "searches their library"),
        # "Target opponent sacrifices ..., discards a card, and loses 3
        # life": the sacrifice is not read as targeting, so the elided
        # subject has no single player to copy - once it was *you*.
        ("Archon of Cruelty", "discards a card"),
    ],
)
def test_a_possessive_about_another_object_is_refused(box, name, text_part):
    _need(box, name)
    abilities = [
        ability
        for face in parse_card(box.db.lookup(name)).faces
        for ability in face.abilities
        if text_part in ability.text
    ]
    assert abilities and all(ability.unparsed for ability in abilities)


# ---------------------------------------------------------------------------
# A second verb with its subject left out (CR 608.2c)
# ---------------------------------------------------------------------------


def test_an_elided_subject_is_the_first_verbs(box):
    """Gibbering Descent: "that player loses 1 life and discards a card" -
    the player whose upkeep it is discards, not the Descent's controller."""
    _need(box, "Gibbering Descent", "Island")
    game = box.game
    box.put("Island", "hand", 0)
    box.put("Island", "hand", 1)
    effects = _effects(box.db, "Gibbering Descent", "that player")
    before = [p.life for p in game.players]

    execute(
        Resolution(
            game=game,
            source=0,
            controller=0,
            trigger_event=Event(EventKind.STEP_BEGAN, player=1),
        ),
        effects,
    )

    assert [p.life for p in game.players] == [before[0], before[1] - 1]
    assert len(game.player(1).hand) == 0 and len(game.player(1).graveyard) == 1
    assert len(game.player(0).hand) == 1, "the Descent's controller discarded"


def test_an_elided_subject_after_a_target_is_the_player_chosen(box):
    """"Target player loses 2 life and discards a card": the discard is the
    targeted player's - not a second target, and not you."""
    from mtgfish.parser.clauses import parse_effects
    from mtgfish.parser.referents import bind_ability
    from mtgfish.parser.tokens import Stream
    from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
    from mtgfish.rules.kernel.query import PlayerScope

    stream = Stream.of("Target player loses 2 life and discards a card.")
    effects = parse_effects(stream)
    assert effects is not None and stream.done
    bound = bind_ability(Ability.spell(*effects, text="")).effects
    assert [e.players.scope for e in bound] == [
        PlayerScope.TARGET_PLAYER,
        PlayerScope.CHOSEN_PLAYER,
    ]
    assert not bound[1].is_targeted
