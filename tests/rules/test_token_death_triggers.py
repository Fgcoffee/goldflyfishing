"""A token that dies has died.

Every token in the engine left the battlefield through a special branch of
``move_object`` that moved the token's zone to the graveyard *before* announcing
that it had died. The death trigger then asked the obvious question - "was that
a creature you controlled, on the battlefield?" - about an object already
sitting in the graveyard, got no, and did nothing.

So Zulaport Cutthroat, Blood Artist, Pitiless Plunderer and every other
aristocrat triggered off cards and never off tokens, which in Commander is most
of what they are for. A card's death worked because the card path keeps the
pre-move object as last-known information (CR 603.6e); the token path had none.

Found by the infinite-loop tests, which needed thirty tokens to die under a
Zulaport and watched the opponent's life total not move.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules import actions
from mtgfish.rules.cr117_priority import run_priority
from mtgfish.rules.enums import Zone
from mtgfish.rules.gameobject import ObjectKind
from mtgfish.ui.sandbox import PassiveOpponent, Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    table = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    table.game.agents[0] = PassiveOpponent()
    return table


def _need(box, *names):
    for name in names:
        if box.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")


def _token(box, name, controller=0):
    """A token made the way ``tokens.create_tokens`` makes one."""
    game = box.game
    token = game.create_object(
        box.db.lookup(name), controller, Zone.BATTLEFIELD, kind=ObjectKind.TOKEN
    )
    token.summoning_sick = False
    game.invalidate_characteristics()
    return token


def test_an_aristocrat_triggers_when_a_token_dies(box):
    _need(box, "Zulaport Cutthroat", "Grizzly Bears")
    box.put("Zulaport Cutthroat", "battlefield", 0)
    token = _token(box, "Grizzly Bears")

    actions.destroy(box.game, token)
    run_priority(box.game)

    assert [p.life for p in box.game.players] == [41, 39]


def test_a_card_and_a_token_trigger_it_the_same_way(box):
    """The comparison that shows it was the token path, not the trigger."""
    _need(box, "Zulaport Cutthroat", "Grizzly Bears")
    game = box.game
    box.put("Zulaport Cutthroat", "battlefield", 0)
    box.put("Grizzly Bears", "battlefield", 0)
    card = next(
        o
        for o in game.objects.values()
        if o.card.name == "Grizzly Bears" and o.zone is Zone.BATTLEFIELD
    )
    token = _token(box, "Grizzly Bears")

    actions.destroy(game, card)
    actions.destroy(game, token)
    run_priority(game)

    assert [p.life for p in game.players] == [42, 38]


def test_the_token_still_ceases_to_exist(box):
    """CR 704.5d: fixing the trigger must not leave a Grizzly Bears in the graveyard."""
    _need(box, "Grizzly Bears")
    game = box.game
    token = _token(box, "Grizzly Bears")

    actions.destroy(game, token)
    run_priority(game)

    assert not game.player(0).graveyard
    assert not any(
        o.kind is ObjectKind.TOKEN and o.zone is not Zone.BATTLEFIELD and o.is_live
        for o in game.objects.values()
    )


def test_a_token_that_only_watches_its_own_side_does_not_see_the_other(box):
    """"Another creature *you control*" still means you: last-known controller."""
    _need(box, "Zulaport Cutthroat", "Grizzly Bears")
    box.put("Zulaport Cutthroat", "battlefield", 0)
    token = _token(box, "Grizzly Bears", controller=1)

    actions.destroy(box.game, token)
    run_priority(box.game)

    assert [p.life for p in box.game.players] == [40, 40]


def test_a_token_copy_of_an_aristocrat_triggers_on_its_own_death(box):
    """"Whenever this creature ... dies" on a token - Kiki-Jiki's copies, Myr
    Retriever tokens, anything a populate or a clone effect made."""
    _need(box, "Zulaport Cutthroat")
    token = _token(box, "Zulaport Cutthroat")

    actions.destroy(box.game, token)
    run_priority(box.game)

    assert [p.life for p in box.game.players] == [41, 39]
