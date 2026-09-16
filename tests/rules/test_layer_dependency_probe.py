"""Dependency ordering (CR 613.8) must not change when it gets faster.

``order_effects`` used to call ``_depends_on`` for every ordered pair, and each
call walked the whole board again for the second effect. A deck full of "creatures
you control have ..." effects made one game take over ten minutes. The fix
computes each effect's footprint once; this checks, on a busy board of real
cards, that every pairwise answer is still exactly what the original definition
gives.
"""

from __future__ import annotations

from mtgfish.rules import layers
from mtgfish.ui.sandbox import Sandbox

STATICS = [
    "Concordant Crossroads", "Archetype of Courage", "Akroma's Memorial", "Levitation",
    "Fervor", "Mass Hysteria", "Archetype of Endurance", "Asceticism", "Leyline of Sanctity",
    "Intangible Virtue", "Eldrazi Monument", "Glorious Anthem", "Always Watching",
    "Urborg, Tomb of Yawgmoth", "Blood Moon",
]
BODIES = ["Grizzly Bears", "Llanowar Elves", "Serra Angel", "Bayou"]


def _reference_depends_on(game, a, b, state, by_id) -> bool:
    """The original, pair-at-a-time definition, kept here as the oracle."""
    if a is b or a.is_cda != b.is_cda:
        return False
    if layers._would_remove_source_ability(game, a, b, state):
        return True
    for object_id in sorted(by_id):
        obj = by_id[object_id]
        base = state[object_id]
        if not layers._affects(game, b, obj, base):
            continue
        after_b = layers.apply_effect(game, obj, base, b)
        applies_before = layers._affects(game, a, obj, base)
        if applies_before != layers._affects(game, a, obj, after_b):
            return True
        if applies_before and layers._operation(game, obj, base, a) != layers._operation(
            game, obj, after_b, a
        ):
            return True
    return False


def test_every_pairwise_dependency_matches_the_original_definition(card_db):
    box = Sandbox(db=card_db)
    for player in (0, 1):
        for name in STATICS:
            box.put(name, "battlefield", player)
        for name in BODIES:
            box.put(name, "battlefield", player)
    game = box.game

    checked = 0
    real_order = layers.order_effects

    def checking(game_, effects, state, by_id):
        ordered = sorted(effects, key=lambda ce: (0 if ce.is_cda else 1, ce.timestamp, ce.source))
        probe = layers._DependencyProbe(game_, ordered, state, by_id)
        nonlocal checked
        for i, a in enumerate(ordered):
            for j, b in enumerate(ordered):
                if i == j:
                    continue
                expected = _reference_depends_on(game_, a, b, state, by_id)
                assert probe.depends_on(i, j) == expected, (a, b)
                checked += 1
        return real_order(game_, effects, state, by_id)

    layers.order_effects = checking
    try:
        game.invalidate_characteristics()
        game.board()
    finally:
        layers.order_effects = real_order

    assert checked > 500  # a board busy enough to mean something


def test_a_lone_effect_pair_answers_the_same_through_depends_on(card_db):
    """``_depends_on`` is still callable on its own, and agrees with the probe."""
    box = Sandbox(db=card_db)
    box.put("Archetype of Courage", "battlefield", 0)
    box.put("Asceticism", "battlefield", 1)
    box.put("Grizzly Bears", "battlefield", 0)
    game = box.game
    game.invalidate_characteristics()
    state = game.board()
    by_id = {oid: game.objects[oid] for oid in state}
    effects = layers._live_effects(game, dict(state), by_id)
    assert len(effects) >= 2
    for a in effects:
        for b in effects:
            assert layers._depends_on(game, a, b, state, by_id) == _reference_depends_on(
                game, a, b, state, by_id
            )
