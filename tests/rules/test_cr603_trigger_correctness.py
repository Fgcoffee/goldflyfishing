"""Triggers that were read but fired at the wrong times (CR 603.2).

Each test reads a trigger condition from oracle text with the real parser,
puts it on a permanent, makes the event happen - and makes a near miss
happen, to show it does *not* fire then.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.parser.tokens import Stream
from mtgfish.parser.triggers import parse_trigger
from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.events import Event, EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ObjectFilter, Value


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def watch(board, text: str, holder: str = "Grizzly Bears", controller: int = 0):
    """Put a permanent carrying a trigger read from ``text`` on the board."""
    trigger = parse_trigger(Stream.of(text))
    assert trigger is not None, text
    ability = Ability.triggered(trigger, Effect(EffectKind.NOTHING, text=text), text=text)
    board.scripts.add(holder, ability)
    obj = board.play(holder, controller)
    board.game.pending_triggers.clear()
    return obj


def fired(board, text: str) -> int:
    count = sum(1 for p in board.game.pending_triggers if p.ability.text == text)
    board.game.pending_triggers.clear()
    return count


# ---------------------------------------------------------------------------
# CR 603.2b: a step named without an owner begins on every turn
# ---------------------------------------------------------------------------


def test_the_end_step_is_every_players_end_step(board):
    """Ball Lightning's "at the beginning of the end step" was read as your
    end step only, so a Ball Lightning given to an opponent - or one cast
    with flash on their turn - never went away."""
    text = "At the beginning of the end step, sacrifice this creature."
    watch(board, text)
    board.game.emit(Event(EventKind.END_STEP, player=PlayerId(1)))
    assert fired(board, text) == 1
    board.game.emit(Event(EventKind.END_STEP, player=PlayerId(0)))
    assert fired(board, text) == 1


def test_your_end_step_is_still_only_yours(board):
    text = "At the beginning of your end step, draw a card."
    watch(board, text)
    board.game.emit(Event(EventKind.END_STEP, player=PlayerId(1)))
    assert fired(board, text) == 0
    board.game.emit(Event(EventKind.END_STEP, player=PlayerId(0)))
    assert fired(board, text) == 1


def test_combat_on_your_turn_keeps_its_owner():
    trigger = parse_trigger(
        Stream.of("At the beginning of combat on your turn, draw a card.")
    )
    assert trigger.players is not None


@pytest.mark.parametrize(
    "text",
    [
        "At the beginning of the next end step, draw a card.",
        "At the beginning of your next upkeep, draw a card.",
    ],
)
def test_a_next_step_is_not_a_recurring_trigger(text):
    """CR 603.7: "the next" makes a delayed trigger, which fires once. Read
    as a recurring one it would fire every turn."""
    assert parse_trigger(Stream.of(text)) is None


# ---------------------------------------------------------------------------
# CR 122.1, 603.2c: which counters, and how many occurrences
# ---------------------------------------------------------------------------


def test_a_counter_trigger_keeps_its_kind(board):
    text = "Whenever one or more +1/+1 counters are put on this creature, draw a card."
    me = watch(board, text)
    actions.add_counters(board.game, me, "-1/-1", 1)
    assert fired(board, text) == 0
    actions.add_counters(board.game, me, "+1/+1", 3)
    assert fired(board, text) == 1


def test_a_counter_fires_once_for_each_counter(board):
    """Fathom Mage: "whenever a +1/+1 counter is put on this creature" -
    three counters at once are three occurrences."""
    text = "Whenever a +1/+1 counter is put on this creature, draw a card."
    me = watch(board, text)
    actions.add_counters(board.game, me, "+1/+1", 3)
    assert fired(board, text) == 3
    actions.add_counters(board.game, me, "charge", 2)
    assert fired(board, text) == 0


def test_one_or_more_counters_of_any_kind(board):
    text = "Whenever one or more counters are put on this creature, draw a card."
    me = watch(board, text)
    actions.add_counters(board.game, me, "charge", 2)
    assert fired(board, text) == 1


@pytest.mark.parametrize(
    "text",
    [
        "When the twelfth hour counter is put on this artifact, draw a card.",
        "Whenever two +1/+1 counters are put on this creature, draw a card.",
    ],
)
def test_other_counter_counts_are_not_read(text):
    """CR 122.7: "the Nth counter" is a threshold, and "two counters" is
    neither "a counter" nor "one or more" - left unread, not widened."""
    assert parse_trigger(Stream.of(text)) is None


# ---------------------------------------------------------------------------
# CR 701.37b: becoming monstrous is not getting a counter
# ---------------------------------------------------------------------------


def test_becoming_monstrous_fires_once_and_counters_do_not(board):
    text = "When this creature becomes monstrous, draw a card."
    me = watch(board, text)
    actions.add_counters(board.game, me, "+1/+1", 1)
    assert fired(board, text) == 0

    monstrosity = Effect(
        EffectKind.MONSTROSITY,
        targets=ObjectFilter(source_only=True),
        amount=Value.of(2),
    )
    resolution = Resolution(game=board.game, source=me.id, controller=PlayerId(0))
    execute(resolution, (monstrosity,))
    assert fired(board, text) == 1
    # Already monstrous: monstrosity does nothing, and nothing triggers.
    execute(Resolution(game=board.game, source=me.id, controller=PlayerId(0)), (monstrosity,))
    assert fired(board, text) == 0


# ---------------------------------------------------------------------------
# CR 603.2c: "one or more" triggers once for what happened at the same time
# ---------------------------------------------------------------------------


def _lethal(board, *objs):
    for obj in objs:
        obj.damage = 100
    board.game.invalidate_characteristics()
    board.sba()


def test_one_or_more_deaths_at_once_trigger_once(board):
    """Morbid Opportunist, Chainsaw: a board wipe is one occurrence."""
    text = "Whenever one or more other creatures die, draw a card."
    watch(board, text, holder="Llanowar Elves")
    a = board.play("Hill Giant", 1)
    b = board.play("Hill Giant", 1)
    _lethal(board, a, b)
    (pending,) = [p for p in board.game.pending_triggers if p.ability.text == text]
    # "That many" counts the batch.
    assert pending.event.amount == 2
    board.game.pending_triggers.clear()

    c = board.play("Hill Giant", 1)
    _lethal(board, c)
    assert fired(board, text) == 1


def test_a_creature_dying_still_triggers_for_each(board):
    text = "Whenever another creature dies, draw a card."
    watch(board, text, holder="Llanowar Elves")
    a = board.play("Hill Giant", 1)
    b = board.play("Hill Giant", 1)
    _lethal(board, a, b)
    assert fired(board, text) == 2


def test_separate_instructions_are_separate_batches(board):
    """CR 608.2c: "destroy it, then destroy that one" happens twice."""
    text = "Whenever one or more other creatures die, draw a card."
    watch(board, text, holder="Llanowar Elves")
    a = board.play("Hill Giant", 1)
    b = board.play("Hill Giant", 1)
    kill = (
        Effect(EffectKind.DESTROY, targets=ObjectFilter(specific=(a.id,))),
        Effect(EffectKind.DESTROY, targets=ObjectFilter(specific=(b.id,))),
    )
    execute(Resolution(game=board.game, source=a.id, controller=PlayerId(0)), kill)
    assert fired(board, text) == 2


def test_combat_damage_to_two_players_is_two_occurrences(card_db):
    """Professional Face-Breaker: "to a player" is per player hit."""
    board = make_board(card_db, ScriptedAbilities(), players=3)
    text = "Whenever one or more creatures you control deal combat damage to a player, draw a card."
    watch(board, text)
    x = board.play("Hill Giant", 0)
    y = board.play("Hill Giant", 0)
    game = board.game
    game.event_batch += 1
    for attacker, victim in ((x, 1), (y, 1)):
        game.emit(Event(EventKind.COMBAT_DAMAGE_DEALT, player=PlayerId(victim), source=attacker.id, amount=3))
    assert fired(board, text) == 1
    game.event_batch += 1
    for attacker, victim in ((x, 1), (y, 2)):
        game.emit(Event(EventKind.COMBAT_DAMAGE_DEALT, player=PlayerId(victim), source=attacker.id, amount=3))
    assert fired(board, text) == 2


def test_one_or_more_discards_count_the_cards(board):
    text = "Whenever you discard one or more cards, draw a card."
    watch(board, text)
    first = board.hand("Hill Giant", 0)
    second = board.hand("Forest", 0)
    board.game.event_batch += 1
    actions.discard(board.game, first)
    actions.discard(board.game, second)
    (pending,) = board.game.pending_triggers
    assert pending.event.amount == 2


def test_two_or_more_is_not_read_as_one():
    """Argent Dais: a threshold on the batch, which nothing here records."""
    assert parse_trigger(Stream.of("Whenever two or more creatures attack, draw a card.")) is None


# ---------------------------------------------------------------------------
# "This ability triggers only once each turn"
# ---------------------------------------------------------------------------


def test_once_each_turn_is_enforced(board):
    from dataclasses import replace

    text = "Whenever another creature dies, draw a card."
    trigger = replace(parse_trigger(Stream.of(text)), once_each_turn=True)
    ability = Ability.triggered(trigger, Effect(EffectKind.NOTHING, text=text), text=text)
    board.scripts.add("Llanowar Elves", ability)
    board.play("Llanowar Elves", 0)
    a = board.play("Hill Giant", 1)
    b = board.play("Hill Giant", 1)
    _lethal(board, a)
    assert fired(board, text) == 1
    _lethal(board, b)
    assert fired(board, text) == 0
    board.game.triggered_once_this_turn.clear()  # what a new turn does
    c = board.play("Hill Giant", 1)
    _lethal(board, c)
    assert fired(board, text) == 1


# ---------------------------------------------------------------------------
# Filters on a player's action
# ---------------------------------------------------------------------------


def test_a_discard_trigger_keeps_its_card_filter(board):
    """Waste Not's three abilities each fired on every discard."""
    text = "Whenever an opponent discards a creature card, draw a card."
    watch(board, text)
    actions.discard(board.game, board.hand("Forest", 1))
    assert fired(board, text) == 0
    actions.discard(board.game, board.hand("Hill Giant", 1))
    assert fired(board, text) == 1
    actions.discard(board.game, board.hand("Hill Giant", 0))
    assert fired(board, text) == 0


def test_attacking_something_in_particular_is_not_read():
    """Mila: "an opponent attacks one or more planeswalkers you control" is
    not "an opponent attacks" (CR 508.3e)."""
    text = "Whenever an opponent attacks one or more planeswalkers you control, draw a card."
    assert parse_trigger(Stream.of(text)) is None
    assert parse_trigger(Stream.of("Whenever you attack, draw a card.")) is not None


# ---------------------------------------------------------------------------
# Whose graveyard, and from where (CR 400.3, 603.6c)
# ---------------------------------------------------------------------------


def _in_library(board, name: str, player: int):
    from mtgfish.rules.kernel.enums import Zone

    obj = board.game.create_object(board.db.lookup(name), PlayerId(player), Zone.LIBRARY)
    board.game.invalidate_characteristics()
    return obj


def test_into_your_graveyard_from_the_battlefield_is_your_creatures(board):
    """Nether Traitor fired on every opponent's creature dying."""
    text = "Whenever another creature is put into your graveyard from the battlefield, draw a card."
    watch(board, text)
    _lethal(board, board.play("Hill Giant", 1))
    assert fired(board, text) == 0
    _lethal(board, board.play("Hill Giant", 0))
    assert fired(board, text) == 1


def test_from_your_library_is_a_mill_not_a_discard(board):
    text = "Whenever a creature card is put into your graveyard from your library, draw a card."
    watch(board, text)
    actions.discard(board.game, board.hand("Hill Giant", 0))
    assert fired(board, text) == 0
    from mtgfish.rules.kernel.enums import Zone

    board.game.move_object(_in_library(board, "Hill Giant", 1), Zone.GRAVEYARD)
    assert fired(board, text) == 0
    board.game.move_object(_in_library(board, "Hill Giant", 0), Zone.GRAVEYARD)
    assert fired(board, text) == 1


def test_an_opponents_graveyard_from_anywhere(board):
    text = "Whenever a creature card is put into an opponent's graveyard from anywhere, draw a card."
    watch(board, text)
    actions.discard(board.game, board.hand("Hill Giant", 0))
    assert fired(board, text) == 0
    actions.discard(board.game, board.hand("Hill Giant", 1))
    assert fired(board, text) == 1


def test_leaving_your_graveyard_is_your_cards(board):
    """Tormod fired whenever an opponent's card left their graveyard."""
    text = "Whenever one or more cards leave your graveyard, draw a card."
    watch(board, text)
    actions.exile(board.game, board.graveyard("Hill Giant", 1))
    assert fired(board, text) == 0
    actions.exile(board.game, board.graveyard("Hill Giant", 0))
    assert fired(board, text) == 1


@pytest.mark.parametrize(
    "text",
    [
        "Whenever a creature card is put into your graveyard from the stack, draw a card.",
        "Whenever a creature card is put into an opponent's graveyard from your library, draw a card.",
        "Whenever a card leaves their graveyard, draw a card.",
    ],
)
def test_unreadable_origins_and_owners_are_not_read(text):
    assert parse_trigger(Stream.of(text)) is None


# ---------------------------------------------------------------------------
# What acted: "of a spell", "by a source you control", "by a creature"
# ---------------------------------------------------------------------------


def _on_stack(board, name: str, player: int, *, ability: bool = False):
    from mtgfish.rules.kernel.enums import Zone
    from mtgfish.rules.kernel.gameobject import ObjectKind

    kind = ObjectKind.ABILITY if ability else ObjectKind.CARD
    obj = board.game.create_object(board.db.lookup(name), PlayerId(player), Zone.STACK, kind=kind)
    board.game.invalidate_characteristics()
    return obj


def _targeted(board, target, by):
    board.game.emit(
        Event(EventKind.TARGETED, object_id=target.id, player=target.controller, source=by.id)
    )


def test_the_target_of_a_spell_is_not_the_target_of_an_ability(board):
    text = "Whenever this creature becomes the target of a spell, draw a card."
    me = watch(board, text)
    _targeted(board, me, _on_stack(board, "Lightning Bolt", 1, ability=True))
    assert fired(board, text) == 0
    _targeted(board, me, _on_stack(board, "Lightning Bolt", 1))
    assert fired(board, text) == 1


def test_a_spell_or_ability_an_opponent_controls(board):
    text = "Whenever a creature you control becomes the target of a spell or ability an opponent controls, draw a card."
    watch(board, text)
    bear = board.play("Hill Giant", 0)
    _targeted(board, bear, _on_stack(board, "Lightning Bolt", 0))
    assert fired(board, text) == 0
    _targeted(board, bear, _on_stack(board, "Lightning Bolt", 1, ability=True))
    assert fired(board, text) == 1


def test_targeting_your_own_creature_is_an_event(board):
    """CR 115.1: becoming a target does not depend on whose spell it is -
    the engine announced only opponents' targets, so "becomes the target of
    a spell you control" could never fire."""
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import _announce_targets

    text = "Whenever this creature becomes the target of a spell you control, draw a card."
    me = watch(board, text)
    spell = _on_stack(board, "Giant Growth", 0)
    spell.targets = ((me.id,),)
    _announce_targets(board.game, spell, PlayerId(0))
    assert fired(board, text) == 1


def test_dealt_damage_by_a_source_you_control(board):
    text = "Whenever this creature is dealt damage by a source you control, draw a card."
    me = watch(board, text)
    theirs = board.play("Hill Giant", 1)
    mine = board.play("Hill Giant", 0)
    actions.deal_damage(board.game, me, 1, source=theirs.id, source_controller=PlayerId(1))
    assert fired(board, text) == 0
    actions.deal_damage(board.game, me, 1, source=mine.id, source_controller=PlayerId(0))
    assert fired(board, text) == 1


@pytest.mark.parametrize(
    "text",
    [
        "Whenever this creature becomes blocked by one or more black creatures, draw a card.",
        "Whenever this creature becomes blocked by a creature, draw a card.",
        "Whenever a creature you control becomes blocked by a creature, draw a card.",
        "Whenever a creature you control becomes the target of an ability that targets only it, draw a card.",
        "Whenever this creature is dealt damage by one or more creatures, draw a card.",
        "Whenever this creature becomes tapped by a spell, draw a card.",
    ],
)
def test_unreadable_actors_are_not_read(text):
    stream = Stream.of(text)
    trigger = parse_trigger(stream)
    assert trigger is None or not stream.at(",")


# ---------------------------------------------------------------------------
# CR 106.12a: "is tapped for mana" - the real cards, through real activation
# ---------------------------------------------------------------------------


@pytest.fixture
def box(card_db, tmp_path):
    from mtgfish.parser.verdicts import VerdictStore
    from mtgfish.ui.sandbox import PassiveOpponent, Sandbox

    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    table.game.agents[1] = PassiveOpponent()
    return table


def _only(game, name):
    from mtgfish.rules.kernel.enums import Zone

    return next(
        o for o in game.objects.values()
        if o.card is not None and o.card.name == name
        and o.zone is Zone.BATTLEFIELD and not o.superseded_by
    )


def _tap_for_mana(game, land):
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import activate_ability
    from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind

    index = next(
        i for i, a in enumerate(game.characteristics(land).abilities) if a.is_mana_ability
    )
    activate_ability(
        game,
        land.controller,
        Action(ActionKind.ACTIVATE_ABILITY, source=land.id, ability_index=index),
    )


def test_an_enchanted_land_tapped_for_mana_adds_more(box):
    """Wild Growth never fired: the mana event named no permanent."""
    from mtgfish.rules.kernel.enums import Color

    if box.db.lookup("Wild Growth") is None:
        pytest.skip("Wild Growth is not in this card pool")
    game = box.game
    box.put("Forest", "battlefield", 0)
    box.put("Forest", "battlefield", 0)
    first, second = [
        o for o in game.objects.values()
        if o.card is not None and o.card.name == "Forest" and o.zone.name == "BATTLEFIELD"
    ]
    box.put("Wild Growth", "battlefield", 0)
    actions.attach(game, _only(game, "Wild Growth"), first)
    game.invalidate_characteristics()
    pool = game.player(PlayerId(0)).mana_pool

    _tap_for_mana(game, second)
    assert pool.amount_of(Color.GREEN) == 1, "an unenchanted land set it off"
    _tap_for_mana(game, first)
    # One extra {G}, and the extra mana does not set it off again.
    assert pool.amount_of(Color.GREEN) == 3


def test_mana_added_without_tapping_is_not_tapping_for_mana(board):
    """A spell or trigger that adds mana taps nothing (CR 106.12), and one
    activation that adds mana twice was tapped once."""
    text = "Whenever a land is tapped for mana, draw a card."
    watch(board, text)
    land = board.play("Forest", 0)
    add = Effect(EffectKind.ADD_MANA, mana_produced=("G",), text="Add {G}.")
    from mtgfish.rules.cr600_spells_and_abilities.resolve import resolve_mana_ability

    execute(Resolution(game=board.game, source=land.id, controller=PlayerId(0)), (add,))
    assert fired(board, text) == 0
    # A mana ability without {T} in its cost ("Sacrifice this: Add {G}").
    resolve_mana_ability(
        Resolution(game=board.game, source=land.id, controller=PlayerId(0)),
        (add,),
        tapped=False,
    )
    assert fired(board, text) == 0
    resolve_mana_ability(
        Resolution(game=board.game, source=land.id, controller=PlayerId(0)),
        (add, add),
        tapped=True,
    )
    assert fired(board, text) == 1
    # CR 106.12a: it must also produce mana; a tapping that produced none
    # (no Swamps for "Add {B} for each Swamp you control") triggers nothing.
    resolve_mana_ability(
        Resolution(game=board.game, source=land.id, controller=PlayerId(0)),
        (Effect(EffectKind.NOTHING, text="nothing"),),
        tapped=True,
    )
    assert fired(board, text) == 0


@pytest.mark.parametrize(
    "text",
    [
        "Whenever you tap a permanent for {C}, add an additional {C}.",
        "Whenever a basic land is tapped for mana of the chosen color, draw a card.",
    ],
)
def test_tapped_for_a_kind_of_mana_is_not_read(text):
    """CR 106.12a: only that mana counts, which the event does not record."""
    stream = Stream.of(text)
    trigger = parse_trigger(stream)
    assert trigger is None or not stream.at(",")


def test_a_land_tapped_to_pay_for_a_spell_is_tapped_for_mana(box):
    """The engine's own payment path (CR 601.2g) activates the same mana
    abilities, and Wild Growth's land was tapped for mana there too."""
    from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
    from mtgfish.rules.kernel.enums import Color

    if box.db.lookup("Wild Growth") is None:
        pytest.skip("Wild Growth is not in this card pool")
    game = box.game
    box.put("Forest", "battlefield", 0)
    box.put("Wild Growth", "battlefield", 0)
    actions.attach(game, _only(game, "Wild Growth"), _only(game, "Forest"))
    box.put("Llanowar Elves", "hand", 0)
    game.invalidate_characteristics()
    elves = next(
        o for o in game.objects.values()
        if o.card is not None and o.card.name == "Llanowar Elves" and o.zone.name == "HAND"
    )
    cast_spell(game, PlayerId(0), Action(ActionKind.CAST_SPELL, source=elves.id))
    assert game.stack, "the spell could not be paid for"
    # {G} paid, and Wild Growth's {G} left over.
    assert game.player(PlayerId(0)).mana_pool.amount_of(Color.GREEN) == 1


def test_you_tap_a_land_for_mana_is_your_land_only(box):
    """Zendikar Resurgent: "whenever *you* tap a land for mana" - an
    opponent tapping theirs adds nothing to either pool."""
    from mtgfish.rules.kernel.enums import Color

    if box.db.lookup("Zendikar Resurgent") is None:
        pytest.skip("Zendikar Resurgent is not in this card pool")
    game = box.game
    box.put("Zendikar Resurgent", "battlefield", 0)
    box.put("Forest", "battlefield", 1)
    _tap_for_mana(game, _only(game, "Forest"))
    assert game.player(PlayerId(1)).mana_pool.amount_of(Color.GREEN) == 1
    assert game.player(PlayerId(0)).mana_pool.total == 0

    box.put("Forest", "battlefield", 0)
    mine = next(
        o for o in game.objects.values()
        if o.card is not None and o.card.name == "Forest"
        and o.zone.name == "BATTLEFIELD" and o.controller == PlayerId(0)
    )
    _tap_for_mana(game, mine)
    # One mana of a type that land produced: green again.
    assert game.player(PlayerId(0)).mana_pool.amount_of(Color.GREEN) == 2

