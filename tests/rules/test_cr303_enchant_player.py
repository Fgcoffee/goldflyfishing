"""Auras that enchant players - the Curses (CR 303.4a-c, 702.5a) - and
protection knocking off an Aura or Equipment of the stated quality
(CR 702.16c, 702.16d).

The enchant ability is scripted with a player quality, the shape the parser
hands over for "Enchant player" / "Enchant opponent"."""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaKind
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr600_spells_and_abilities.abilities import (
    Ability,
    AbilityKind,
    TriggerCondition,
)
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import (
    _candidates_for,
    cast_spell,
    targeting_effects,
)
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import (
    KeywordInstance,
    build,
)
from mtgfish.rules.kernel.enums import CardType, Color, Duration, Phase, Step, Zone
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.ids import PlayerId, player_target
from mtgfish.rules.kernel.query import (
    YOU,
    ObjectFilter,
    PlayerFilter,
    PlayerScope,
    Value,
)

P0, P1 = PlayerId(0), PlayerId(1)
CURSE = "Curse of the Pierced Heart"  # {1}{R}
ENCHANTED = PlayerFilter(PlayerScope.ENCHANTED_PLAYER)

UPKEEP_PING = Ability.triggered(
    TriggerCondition(
        event_kinds=frozenset({EventKind.UPKEEP}),
        players=ENCHANTED,
        text="at the beginning of enchanted player's upkeep",
    ),
    Effect(EffectKind.DAMAGE, players=ENCHANTED, amount=Value.of(1)),
    text="this Aura deals 1 damage to that player",
)


def enchant(scope: PlayerScope) -> Ability:
    (ability,) = build(KeywordInstance("Enchant", players=PlayerFilter(scope), text="Enchant"))
    return ability


def protection(quality: ObjectFilter) -> Ability:
    return Ability(AbilityKind.STATIC, keyword="Protection", quality=quality, text="Protection")


@pytest.fixture
def board(card_db):
    for name in (CURSE, "Pacifism", "Bonesplitter", "Grizzly Bears", "Divination"):
        if card_db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")
    board = make_board(card_db, ScriptedAbilities())
    game = board.game
    game.active_player = P0
    game.phase = Phase.PRECOMBAT_MAIN
    game.step = Step.MAIN
    return board


def curse_board(board, scope=PlayerScope.EACH_PLAYER):
    board.scripts.add(CURSE, enchant(scope), UPKEEP_PING)
    board.refresh()
    return board.hand(CURSE)


def cast_curse_at(board, card, player):
    game = board.game
    game.player(P0).mana_pool.add(ManaKind(Color.RED), 2)
    cast_spell(
        game, P0, Action(ActionKind.CAST_SPELL, source=card.id, targets=((player_target(player),),))
    )
    board.resolve_stack()
    board.sba()
    return next(o for o in game.objects.values()
                if o.is_live and o.card is not None and o.card.name == CURSE)


def test_a_curse_targets_a_player_and_enchants_them(board):
    curse = cast_curse_at(board, curse_board(board), P1)
    assert curse.zone is Zone.BATTLEFIELD, "a Curse on a player is legal (CR 303.4b)"
    assert curse.attached_player == P1


def test_enchanted_player_is_the_one_it_is_attached_to(board):
    game = board.game
    cast_curse_at(board, curse_board(board), P1)
    game.pending_triggers.clear()
    game.emit(Event(EventKind.UPKEEP, player=P0))
    assert not game.pending_triggers, "not the enchanted player's upkeep"
    game.emit(Event(EventKind.UPKEEP, player=P1))
    assert len(game.pending_triggers) == 1
    before = game.player(P1).life
    board.settle()
    board.resolve_stack()
    assert game.player(P1).life == before - 1


def test_enchant_opponent_offers_only_opponents(board):
    card = curse_board(board, PlayerScope.EACH_OPPONENT)
    (effect,) = targeting_effects(board.game, card)
    offered = _candidates_for(board.game, card, effect, P0)
    assert player_target(P1) in offered
    assert player_target(P0) not in offered


def test_protection_from_the_aura_puts_it_into_the_graveyard(board):
    """CR 702.16c: a player with protection from red can't be enchanted by a
    red Curse; one already on them goes to the graveyard."""
    game = board.game
    curse = cast_curse_at(board, curse_board(board), P1)
    board.scripts.add("Divination", Ability.spell(Effect(
        EffectKind.GRANT_ABILITY,
        players=YOU,
        granted_abilities=(protection(ObjectFilter(colors_any=Color.RED, zones=frozenset())),),
        duration=int(Duration.END_OF_TURN),
    )))
    board.refresh()
    game.active_player = P1
    game.player(P1).mana_pool.add(ManaKind(Color.BLUE), 3)
    cast_spell(game, P1, Action(ActionKind.CAST_SPELL, source=board.hand("Divination", 1).id))
    board.resolve_stack()
    board.sba()
    assert curse.zone is not Zone.BATTLEFIELD or not curse.is_live


def test_protection_knocks_off_an_aura_of_that_quality(board):
    """CR 702.16c on a permanent: a creature that gains protection from white
    sheds its white Aura."""
    board.scripts.add("Pacifism", *build(KeywordInstance(
        "Enchant", filter=ObjectFilter(types_all=CardType.CREATURE), text="Enchant creature")))
    bears = board.play("Grizzly Bears")
    pacifism = board.play("Pacifism")
    board.refresh()
    actions.attach(board.game, pacifism, bears)
    board.sba()
    assert pacifism.zone is Zone.BATTLEFIELD
    board.scripts.add("Grizzly Bears", protection(ObjectFilter(colors_any=Color.WHITE)))
    board.refresh()
    board.sba()
    assert not pacifism.is_live or pacifism.zone is not Zone.BATTLEFIELD


def test_protection_unattaches_equipment_of_that_quality(board):
    """CR 702.16d: protection from artifacts - the Equipment falls off and
    stays on the battlefield."""
    bears = board.play("Grizzly Bears")
    blade = board.play("Bonesplitter")
    actions.attach(board.game, blade, bears)
    board.sba()
    assert blade.attached_to == bears.id
    board.scripts.add("Grizzly Bears", protection(ObjectFilter(types_all=CardType.ARTIFACT)))
    board.refresh()
    board.sba()
    assert blade.zone is Zone.BATTLEFIELD
    assert blade.attached_to != bears.id


# ---------------------------------------------------------------------------
# CR 303.4f, 303.4g: an Aura put onto the battlefield without being cast
# ---------------------------------------------------------------------------


def pacifism_in_graveyard(board):
    board.scripts.add("Pacifism", *build(KeywordInstance(
        "Enchant", filter=ObjectFilter(types_all=CardType.CREATURE), text="Enchant creature")))
    board.refresh()
    return board.graveyard("Pacifism")


def test_a_returned_aura_enters_attached_to_a_legal_object(board):
    bears = board.play("Grizzly Bears", controller=1)
    aura = board.game.move_object(pacifism_in_graveyard(board), Zone.BATTLEFIELD)
    assert aura.zone is Zone.BATTLEFIELD
    assert aura.attached_to == bears.id
    board.sba()
    assert aura.is_live and aura.zone is Zone.BATTLEFIELD


def test_with_nothing_to_enchant_it_stays_where_it_was(board):
    """CR 303.4g: no creature, so Pacifism stays in the graveyard."""
    card = pacifism_in_graveyard(board)
    assert board.game.move_object(card, Zone.BATTLEFIELD) is card
    assert card.zone is Zone.GRAVEYARD and card.is_live


def test_protection_rules_out_a_host_but_hexproof_does_not(board):
    """It is not targeted, so hexproof doesn't matter; protection does
    (CR 303.4f, 702.16c)."""
    from harness import keyword

    board.scripts.add("Grizzly Bears", protection(ObjectFilter(colors_any=Color.WHITE)))
    board.scripts.add("Hill Giant", keyword("Hexproof"))
    board.play("Grizzly Bears", controller=1)
    giant = board.play("Hill Giant", controller=1)
    aura = board.game.move_object(pacifism_in_graveyard(board), Zone.BATTLEFIELD)
    assert aura.attached_to == giant.id


def test_a_returned_curse_enchants_an_opponent(board):
    board.scripts.add(CURSE, enchant(PlayerScope.EACH_OPPONENT), UPKEEP_PING)
    board.refresh()
    curse = board.game.move_object(board.graveyard(CURSE), Zone.BATTLEFIELD)
    assert curse.attached_player == P1


def test_an_aura_attached_to_nothing_goes_to_the_graveyard(board):
    """F60, CR 704.5m: a Curse whose enchant ability was not read resolves
    attached to nothing, and the state-based actions put it in the
    graveyard."""
    board.scripts.add(CURSE, UPKEEP_PING)  # no enchant ability read
    board.refresh()
    game = board.game
    game.player(P0).mana_pool.add(ManaKind(Color.RED), 2)
    cast_spell(game, P0, Action(ActionKind.CAST_SPELL, source=board.hand(CURSE).id))
    board.resolve_stack()
    board.sba()
    (curse,) = [o for o in game.objects.values()
                if o.is_live and o.card is not None and o.card.name == CURSE]
    assert curse.zone is Zone.GRAVEYARD
