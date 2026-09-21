"""Abilities that work from a graveyard are activated from there, and do what
they say.

CR 113.6: an ability functions only in the zones it says it does. Unearth
(CR 702.84a) and Scavenge (CR 702.97a) are activated from a graveyard, and so
is every printed "{cost}: Return this card from your graveyard to your hand".
Legality searched permanents, hand and the command zone, so none of them were
ever offered - and the two keywords were each missing half their text, which is
why the graveyard was deliberately left out until now.

The rest of the file is the other half of that bargain: an ability offered from
the graveyard has to do what the card says, including the parts that are only
in the reminder text - the exile that pays for scavenge, and unearth's end step
exile and "if it would leave the battlefield, exile it instead".
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules import actions
from mtgfish.rules.abilities import Ability, AbilityKind, DelayedTrigger, TriggerCondition
from mtgfish.rules.cr117_priority import ActionKind, _perform
from mtgfish.rules.cr500_turn import take_turn
from mtgfish.rules.effects import Effect, EffectKind
from mtgfish.rules.enums import Phase, Step, Zone
from mtgfish.rules.events import EventKind
from mtgfish.rules.ids import PlayerId
from mtgfish.rules.legality import legal_actions
from mtgfish.rules.query import ConditionKind, PlayerFilter, PlayerScope, Value
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

YOU = PlayerFilter(PlayerScope.YOU)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
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


def _graveyard_ability(game, obj):
    """The index of the ability that works from a graveyard."""
    abilities = game.characteristics(obj).abilities
    return next(
        i
        for i, ability in enumerate(abilities)
        if ability.kind is AbilityKind.ACTIVATED and Zone.GRAVEYARD in ability.functions_in
    )


def _offered(game, player, obj, index=None):
    return [
        action
        for action in legal_actions(game, PlayerId(player))
        if action.kind in (ActionKind.ACTIVATE_ABILITY, ActionKind.ACTIVATE_MANA_ABILITY)
        and action.source == obj.id
        and (index is None or action.ability_index == index)
    ]


def _names(game, object_ids):
    return sorted(game.printed_characteristics(game.objects[o]).name for o in object_ids)


def _permanent(game, name, player=0):
    return next(
        (obj for obj in game.permanents(PlayerId(player))
         if game.characteristics(obj).name == name),
        None,
    )


def _to_end_step(box):
    """Walk the turn forward to the end step, as the turn loop would."""
    for _ in range(12):
        if box.game.step is Step.END_STEP:
            return
        box.advance()
    raise AssertionError("never reached the end step")


# ---------------------------------------------------------------------------
# Unearth (CR 702.84a)
# ---------------------------------------------------------------------------


def test_unearth_is_offered_from_the_graveyard_and_nowhere_else(box):
    _need(box, "Dregscape Zombie")
    game = box.game
    in_graveyard = _place(box, "Dregscape Zombie", "graveyard")
    in_hand = _place(box, "Dregscape Zombie", "hand")
    in_play = _place(box, "Dregscape Zombie", "battlefield")
    box.give_mana(2)

    unearth = _index(game, in_graveyard, "Unearth")
    assert _offered(game, 0, in_graveyard, unearth)
    assert not _offered(game, 0, in_hand, unearth)
    assert not _offered(game, 0, in_play, unearth)

    # CR 702.84a: "Activate only as a sorcery", so not in the upkeep either.
    game.phase, game.step = Phase.BEGINNING, Step.UPKEEP
    assert not _offered(game, 0, in_graveyard, unearth)


def test_only_the_owner_may_unearth_a_card_in_their_graveyard(box):
    """CR 602.2 with 108.4a: a card in a graveyard has no controller."""
    _need(box, "Dregscape Zombie")
    game = box.game
    card = _place(box, "Dregscape Zombie", "graveyard", player=0)
    box.give_mana(2, player=1)

    assert not _offered(game, 1, card)


def test_unearth_returns_it_with_haste_and_exiles_it_at_the_next_end_step(box):
    """The whole of CR 702.84a, not just the first sentence."""
    _need(box, "Dregscape Zombie")
    game = box.game
    player = game.player(PlayerId(0))
    card = _place(box, "Dregscape Zombie", "graveyard")
    box.give_mana(2)

    (unearth,) = _offered(game, 0, card, _index(game, card, "Unearth"))
    assert _perform(game, PlayerId(0), unearth)
    box.resolve_top()

    zombie = _permanent(game, "Dregscape Zombie")
    assert zombie is not None, "it comes back onto the battlefield"
    assert not player.graveyard, "and is no longer in the graveyard"
    assert game.characteristics(zombie).has_keyword("Haste")

    _to_end_step(box)
    box.settle()
    while game.stack:
        box.resolve_top()

    assert _permanent(game, "Dregscape Zombie") is None
    assert "Dregscape Zombie" in _names(game, game.exile)
    assert not player.graveyard, "exiled, not put back into the graveyard"


def test_an_unearthed_creature_that_would_die_is_exiled_instead(box):
    """"If it would leave the battlefield, exile it instead of putting it
    anywhere else" - otherwise it could be recurred again and again."""
    _need(box, "Dregscape Zombie")
    game = box.game
    player = game.player(PlayerId(0))
    card = _place(box, "Dregscape Zombie", "graveyard")
    box.give_mana(2)

    (unearth,) = _offered(game, 0, card, _index(game, card, "Unearth"))
    assert _perform(game, PlayerId(0), unearth)
    box.resolve_top()

    zombie = _permanent(game, "Dregscape Zombie")
    actions.destroy(game, zombie)

    assert _names(game, player.graveyard) == []
    assert "Dregscape Zombie" in _names(game, game.exile)


def test_unearth_returns_nothing_if_the_card_left_the_graveyard_in_response(box):
    """CR 400.7: the card in exile is a new object, and not the one the
    ability was announced about. The husk left behind is not it either."""
    _need(box, "Dregscape Zombie")
    game = box.game
    card = _place(box, "Dregscape Zombie", "graveyard")
    box.give_mana(2)

    (unearth,) = _offered(game, 0, card, _index(game, card, "Unearth"))
    assert _perform(game, PlayerId(0), unearth)
    actions.exile(game, card)
    box.resolve_top()

    assert _permanent(game, "Dregscape Zombie") is None
    assert _names(game, game.exile) == ["Dregscape Zombie"], "one card, not two"


# ---------------------------------------------------------------------------
# Scavenge (CR 702.97a)
# ---------------------------------------------------------------------------


def test_scavenge_exiles_the_card_that_scavenged_and_no_other(box):
    """"Exile this card from your graveyard" is the cost, and it names this
    card: paying it with whatever is on top keeps the scavenger to be used
    again and throws away something else."""
    _need(box, "Deadbridge Goliath", "Grizzly Bears", "Island")
    game = box.game
    player = game.player(PlayerId(0))
    goliath = _place(box, "Deadbridge Goliath", "graveyard")
    _place(box, "Island", "graveyard")
    bears = _place(box, "Grizzly Bears", "battlefield")
    box.give_mana(6)

    (scavenge,) = _offered(game, 0, goliath, _index(game, goliath, "Scavenge"))
    assert _perform(game, PlayerId(0), replace(scavenge, targets=((bears.id,),)))
    box.resolve_top()

    assert _names(game, player.graveyard) == ["Island"], "the Island is untouched"
    assert "Deadbridge Goliath" in _names(game, game.exile)
    assert bears.counter_count("+1/+1") == 5, "counters equal to the Goliath's power"


def test_scavenge_is_offered_from_the_graveyard_and_nowhere_else(box):
    _need(box, "Deadbridge Goliath", "Grizzly Bears")
    game = box.game
    in_graveyard = _place(box, "Deadbridge Goliath", "graveyard")
    in_play = _place(box, "Deadbridge Goliath", "battlefield")
    _place(box, "Grizzly Bears", "battlefield")
    box.give_mana(6)

    scavenge = _index(game, in_graveyard, "Scavenge")
    assert _offered(game, 0, in_graveyard, scavenge)
    assert not _offered(game, 0, in_play, scavenge)


# ---------------------------------------------------------------------------
# Printed graveyard abilities (CR 113.6m)
# ---------------------------------------------------------------------------


def test_eternal_dragon_returns_itself_only_during_your_upkeep(box):
    """"{3}{W}{W}: Return this card from your graveyard to your hand. Activate
    only during your upkeep." The zone comes from the effect, and the
    restriction is part of the ability (CR 602.5b)."""
    _need(box, "Eternal Dragon")
    game = box.game
    player = game.player(PlayerId(0))
    dragon = _place(box, "Eternal Dragon", "graveyard")
    in_play = _place(box, "Eternal Dragon", "battlefield")
    box.give_mana(5)

    index = _graveyard_ability(game, dragon)
    ability = game.characteristics(dragon).abilities[index]
    assert ability.functions_in == {Zone.GRAVEYARD}
    assert ability.activation_condition.kind is ConditionKind.IS_STEP

    assert not _offered(game, 0, dragon, index), "not during your main phase"
    assert not _offered(game, 0, in_play, index), "and never from the battlefield"

    game.phase, game.step = Phase.BEGINNING, Step.UPKEEP
    game.active_player = PlayerId(1)
    assert not _offered(game, 0, dragon, index), "not during an opponent's upkeep"

    game.active_player = PlayerId(0)
    (activate,) = _offered(game, 0, dragon, index)
    assert _perform(game, PlayerId(0), activate)
    box.resolve_top()

    assert _names(game, player.hand) == ["Eternal Dragon"]
    assert not player.graveyard


def test_a_graveyard_ability_returns_tapped_when_it_says_so(box):
    """"Return this card from your graveyard to the battlefield tapped."
    Offering it and letting it arrive ready to attack is not the card."""
    _need(box, "Reassembling Skeleton")
    game = box.game
    skeleton = _place(box, "Reassembling Skeleton", "graveyard")
    box.give_mana(2)

    (activate,) = _offered(game, 0, skeleton, _graveyard_ability(game, skeleton))
    assert _perform(game, PlayerId(0), activate)
    box.resolve_top()

    returned = _permanent(game, "Reassembling Skeleton")
    assert returned is not None
    assert returned.tapped


def test_an_unmodelled_activation_restriction_is_not_offered_from_a_graveyard(box):
    """"Activate only if you control a legendary creature" and its kin are not
    modelled. Offering the ability anyway would let it be used whenever, which
    is a different card - so it stays unactivatable until the restriction can
    be checked."""
    _need(box, "Haunt of the Dead Marshes")
    game = box.game
    card = _place(box, "Haunt of the Dead Marshes", "graveyard")
    box.give_mana(5)

    assert not _offered(game, 0, card)


# ---------------------------------------------------------------------------
# What the graveyard abilities need from the turn structure
# ---------------------------------------------------------------------------


def test_upkeep_and_end_step_triggers_fire_during_a_real_turn(board):
    """CR 503.1a and 513.1a. The steps announced themselves only as
    STEP_BEGAN, so no upkeep or end step trigger - printed or delayed - ever
    fired in a game, and unearth's exile would have waited for ever."""
    game = board.game
    board.scripts.add(
        "Grizzly Bears",
        Ability.triggered(
            TriggerCondition(
                event_kinds=frozenset({EventKind.UPKEEP}),
                players=YOU,
                text="at the beginning of your upkeep",
            ),
            Effect(EffectKind.NOTHING, text="upkeep probe"),
            text="upkeep probe",
        ),
    )
    board.play("Grizzly Bears", controller=0)
    game.delayed_triggers.append(
        DelayedTrigger(
            trigger=TriggerCondition(
                event_kinds=frozenset({EventKind.END_STEP}),
                text="at the beginning of the next end step",
            ),
            effects=(Effect(EffectKind.NOTHING, text="end step probe"),),
            controller=PlayerId(0),
            source=0,
        )
    )

    resolved: list[str] = []

    def watch(_game, event):
        if event.kind is EventKind.ABILITY_RESOLVED:
            obj = _game.objects.get(event.object_id)
            if obj is not None and obj.ability is not None:
                resolved.append(obj.ability.text)

    game.observer = watch
    for player in game.players:
        game.agents[player.id] = FixedAgent()
    take_turn(game)

    assert "upkeep probe" in resolved
    assert "at the beginning of the next end step" in resolved


def test_a_delayed_trigger_fires_once_in_a_turn_not_at_every_end_step(board):
    """CR 603.7b. Every delayed trigger was created as repeating - the
    duration that was read to decide it is zero when unset and zero for
    "permanent" alike - so "at the beginning of the next end step, draw a
    card" would have drawn one every end step for the rest of the game."""
    game = board.game
    game.delayed_triggers.append(
        DelayedTrigger(
            trigger=TriggerCondition(
                event_kinds=frozenset({EventKind.END_STEP}),
                text="at the beginning of the next end step",
            ),
            effects=(Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1)),),
            controller=PlayerId(0),
            source=0,
        )
    )

    take_turn(game)
    assert not game.delayed_triggers, "it fired, and it is gone"


def test_a_finality_counter_exiles_instead_of_the_graveyard(board):
    """CR 122.1. Several graveyard abilities return the card "with a finality
    counter on it", which is what stops them recurring for ever."""
    game = board.game
    bears = board.play("Grizzly Bears", controller=0)
    bears.add_counters("finality", 1)

    actions.destroy(game, bears)

    assert board.in_graveyard(0) == []
    assert _names(game, game.exile) == ["Grizzly Bears"]
