"""A payment asked for during a resolution can fail, and failing is not a crash.

Smothering Tithe asks the player who drew to pay {2}. ``can_pay_cost`` is an
upper bound - it counts an Azorius Signet's {W}{U} without asking where the
Signet's own {1} comes from - so with every land tapped the answer was "yes",
tapping the Signet then failed partway, and the CastError went up through the
stack. During a cast that error is caught and the cast rewound (CR 601.2h);
during a resolution nothing caught it, and one game ended the whole run.

Found by the compute-cost benchmark: game 165 of a four-player control mirror.

The fix rewinds the attempt - the sources it tapped untap, the mana it made
is gone - and the player simply did not pay. And "unless that player pays"
no longer treats a payment that failed as a payment that happened.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.compile import parse_card
from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import pay_cost
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.query import PlayerFilter, PlayerScope, Value
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


def _on_battlefield(game, name, controller):
    return next(
        obj
        for obj in game.objects.values()
        if getattr(obj.card, "name", "") == name
        and obj.zone is Zone.BATTLEFIELD
        and obj.controller == controller
    )


def _tithe_cost(db):
    """The {2} Smothering Tithe asks for, as the parser built it."""
    for face in parse_card(db.lookup("Smothering Tithe")).faces:
        for ability in face.abilities:
            for effect in ability.effects:
                for node in effect.walk():
                    if node.pay_cost is not None:
                        return node.pay_cost
    pytest.skip("Smothering Tithe has no parsed payment")


def test_a_payment_that_cannot_be_completed_is_not_made(box):
    _need(box, "Azorius Signet", "Smothering Tithe")
    game = box.game
    box.put("Azorius Signet", "battlefield", 1)
    signet = _on_battlefield(game, "Azorius Signet", 1)

    assert pay_cost(game, 1, _tithe_cost(box.db)) is False

    # Nothing of the attempt survives it.
    assert not signet.tapped
    assert game.player(1).mana_pool.total == 0


def test_a_payment_that_can_be_completed_still_is(box):
    """The rewind must only happen on failure: two lands pay {2}."""
    _need(box, "Plains", "Island", "Smothering Tithe")
    game = box.game
    box.put("Plains", "battlefield", 1)
    box.put("Island", "battlefield", 1)

    assert pay_cost(game, 1, _tithe_cost(box.db)) is True
    assert all(
        obj.tapped
        for obj in game.objects.values()
        if obj.zone is Zone.BATTLEFIELD and obj.controller == 1
    )


def test_unless_pays_happens_when_the_payment_fails(box):
    """"Draw a card unless that player pays {2}": they could not, so you draw."""
    _need(box, "Azorius Signet", "Smothering Tithe", "Island")
    game = box.game
    box.put("Azorius Signet", "battlefield", 1)
    for _ in range(3):
        box.put("Island", "library", 0)

    effect = Effect(
        kind=EffectKind.UNLESS_PAYS,
        players=PlayerFilter(PlayerScope.SPECIFIC, specific=1),
        pay_cost=_tithe_cost(box.db),
        children=(Effect(kind=EffectKind.DRAW, amount=Value.of(1)),),
    )
    execute(Resolution(game=game, source=0, controller=0), (effect,))

    assert len(game.player(0).hand) == 1, "a failed payment waived the effect"
    assert not _on_battlefield(game, "Azorius Signet", 1).tapped


def test_smothering_tithe_with_only_a_signet_does_not_end_the_game(box):
    """The benchmark crash, end to end, with the bots that hit it."""
    _need(box, "Azorius Signet", "Smothering Tithe", "Island")
    from mtgfish.ai.simple import SimpleAgent
    from mtgfish.rules.cr100_game_concepts.cr117_priority import run_priority

    game = box.game
    game.agents[0] = SimpleAgent()
    game.agents[1] = SimpleAgent()
    box.put("Smothering Tithe", "battlefield", 0)
    box.put("Azorius Signet", "battlefield", 1)
    box.put("Island", "library", 1)

    game.draw(1)
    run_priority(game)

    assert not game.game_over
    assert not _on_battlefield(game, "Azorius Signet", 1).tapped
