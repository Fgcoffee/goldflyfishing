"""Linked abilities (CR 607).

"Exile target creature" and "return the exiled card" are two abilities of one
object, and the second refers only to what the first did (CR 607.1, 607.2a):
not cards some other object exiled, not cards another, unlinked ability of the
same object exiled, and not a card that has since left exile. The link is to
objects, so CR 400.7 ends it when either side changes zones - with the two
exceptions the rules write in: a leaves-the-battlefield ability looks back at
the permanent that did the exiling (CR 603.10a), and a permanent refers to the
cards exiled to pay for the spell it was (CR 607.2q).

The abilities here are scripted onto real cards only because the harness needs
something to put on the board; nothing depends on which card it is.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts import actions
from mtgfish.rules.cr600_spells_and_abilities.abilities import (
    Ability,
    AbilityKind,
    TriggerCondition,
)
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind, TokenSpec
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.enums import CardType, Zone
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.gameobject import GameObject, ObjectKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ObjectFilter

HOST = "Glorious Anthem"


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    # The host's printed anthem would only get in the way.
    board.scripts.add(HOST)
    return board


def linked(link_id: int = 0, zone: Zone = Zone.EXILE) -> ObjectFilter:
    return ObjectFilter(linked_to_source=True, link_id=link_id, zones=frozenset({zone}))


def return_linked(link_id: int = 0) -> Effect:
    return Effect(
        EffectKind.PUT_ONTO_BATTLEFIELD,
        targets=linked(link_id),
        under_owners_control=True,
        text="return the exiled card to the battlefield under its owner's control",
    )


def resolve_ability(board, source, effects, *, link_id: int = 0):
    """Resolve ``effects`` as an ability of ``source`` carrying ``link_id``."""
    stack_object = GameObject(
        id=board.game.ids.object_id(),
        kind=ObjectKind.ABILITY,
        owner=PlayerId(0),
        controller=PlayerId(0),
        zone=Zone.STACK,
        ability=Ability(AbilityKind.ACTIVATED, effects=tuple(effects), link_id=link_id),
        source=source.id,
    )
    execute(
        Resolution(
            game=board.game,
            source=source.id,
            controller=PlayerId(0),
            stack_object=stack_object,
        ),
        tuple(effects),
    )
    board.refresh()


def exile_it(obj) -> Effect:
    return Effect(EffectKind.EXILE, targets=ObjectFilter(specific=(obj.id,)))


def current(board, obj):
    while obj.superseded_by:
        obj = board.game.objects[obj.superseded_by]
    return obj


# ---------------------------------------------------------------------------
# CR 607.2a - "the exiled cards" are this object's
# ---------------------------------------------------------------------------


def test_it_returns_what_this_object_exiled(board):
    host = board.play(HOST)
    bears = board.play("Grizzly Bears", controller=1)
    resolve_ability(board, host, [exile_it(bears)])
    resolve_ability(board, host, [return_linked()])
    assert current(board, bears).zone is Zone.BATTLEFIELD


def test_it_does_not_return_what_another_object_exiled(board):
    """Two copies of one card: each refers to its own exiled card only."""
    mine = board.play(HOST)
    other = board.play(HOST)
    first = board.play("Grizzly Bears", controller=1)
    second = board.play("Grizzly Bears", controller=1)
    resolve_ability(board, mine, [exile_it(first)])
    resolve_ability(board, other, [exile_it(second)])

    resolve_ability(board, mine, [return_linked()])
    assert current(board, first).zone is Zone.BATTLEFIELD
    assert current(board, second).zone is Zone.EXILE


def test_a_named_link_ignores_the_objects_other_abilities(board):
    """CR 607.2a: only the partner ability's exile. The object's other,
    unlinked exile (the example under CR 607.5) is not "the exiled cards"."""
    host = board.play(HOST)
    partnered = board.play("Grizzly Bears", controller=1)
    unlinked = board.play("Grizzly Bears", controller=1)
    resolve_ability(board, host, [exile_it(partnered)], link_id=7)
    resolve_ability(board, host, [exile_it(unlinked)], link_id=0)

    resolve_ability(board, host, [return_linked(7)], link_id=7)
    assert current(board, partnered).zone is Zone.BATTLEFIELD
    assert current(board, unlinked).zone is Zone.EXILE


def test_a_card_that_left_exile_is_no_longer_linked(board):
    """CR 400.7: exiled again, by something else, it is a new object."""
    host = board.play(HOST)
    bears = board.play("Grizzly Bears", controller=1)
    resolve_ability(board, host, [exile_it(bears)])
    in_graveyard = board.game.move_object(current(board, bears), Zone.GRAVEYARD)
    board.game.move_object(in_graveyard, Zone.EXILE)
    board.refresh()

    resolve_ability(board, host, [return_linked()])
    assert current(board, bears).zone is Zone.EXILE


def test_a_blinked_host_is_linked_to_nothing(board):
    """CR 400.7: the host that comes back is a new object, and its abilities
    never exiled anything."""
    host = board.play(HOST)
    bears = board.play("Grizzly Bears", controller=1)
    resolve_ability(board, host, [exile_it(bears)])
    away = actions.exile(board.game, host)
    back = board.game.move_object(away, Zone.BATTLEFIELD)
    board.refresh()

    resolve_ability(board, back, [return_linked()])
    assert current(board, bears).zone is Zone.EXILE


def test_the_filter_says_what_it_holds():
    assert "linked ability" in linked().describe()


# ---------------------------------------------------------------------------
# CR 603.10a with CR 607.2a - "when this leaves the battlefield, return it"
# ---------------------------------------------------------------------------

WHEN_THIS_LEAVES = TriggerCondition(
    event_kinds=frozenset({EventKind.LEAVES_BATTLEFIELD}),
    subject=ObjectFilter(source_only=True),
    uses_last_known_information=True,
    functions_in=frozenset(Zone),
    text="when this leaves the battlefield",
)


def test_the_leaves_the_battlefield_half_finds_the_exiled_card(board):
    board.scripts.add(HOST, Ability.triggered(WHEN_THIS_LEAVES, return_linked()))
    host = board.play(HOST)
    other = board.play(HOST)
    mine = board.play("Grizzly Bears", controller=1)
    theirs = board.play("Grizzly Bears", controller=1)
    resolve_ability(board, host, [exile_it(mine)])
    resolve_ability(board, other, [exile_it(theirs)])

    actions.destroy(board.game, host, source=0)
    board.settle()
    board.resolve_stack()
    assert current(board, mine).zone is Zone.BATTLEFIELD
    assert current(board, theirs).zone is Zone.EXILE, "a control: not its card"


# ---------------------------------------------------------------------------
# CR 607.2c - "created with" / "put onto the battlefield with"
# ---------------------------------------------------------------------------


def test_tokens_created_with_this_are_its_own(board):
    host = board.play(HOST)
    other = board.play(HOST)
    token = Effect(
        EffectKind.CREATE_TOKEN,
        token=TokenSpec(types=CardType.CREATURE, subtypes=("Spirit",)),
    )
    resolve_ability(board, host, [token])
    resolve_ability(board, other, [token])
    from mtgfish.rules.kernel.matching import matches

    spirits = [o for o in board.game.permanents() if o.kind is ObjectKind.TOKEN]
    assert len(spirits) == 2
    ours = [o for o in spirits if matches(board.game, o, linked(zone=Zone.BATTLEFIELD), source=host.id)]
    assert len(ours) == 1
    assert actions.linked_objects(board.game, host.id) == ours


# ---------------------------------------------------------------------------
# CR 607.2q - cards exiled to pay for the spell the permanent was
# ---------------------------------------------------------------------------


def test_cards_exiled_paying_for_the_spell_stay_linked_to_the_permanent(board):
    from mtgfish.rules.cr600_spells_and_abilities.cr608_stack import resolve_top

    fodder = board.graveyard("Grizzly Bears", controller=0)
    spell = board.game.create_object(board.db.lookup(HOST), PlayerId(0), Zone.STACK)
    exiled = actions.exile(board.game, fodder, source=spell.id)
    board.refresh()
    resolve_top(board.game)

    permanent = current(board, spell)
    assert permanent.zone is Zone.BATTLEFIELD
    assert actions.exiled_with(board.game, permanent.id) == [exiled]
