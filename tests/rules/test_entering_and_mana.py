"""Two bugs the round-trip explainer found, pinned so they cannot return.

Both had the same shape and it is the shape worth remembering: the card parsed
completely, the cross-check saw nothing to complain about, and the engine did
something quietly wrong. Neither would ever have shown up as a failure - only
as a simulation that was a bit too fast.
"""

from __future__ import annotations

import pytest

from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    from mtgfish.parser.verdicts import VerdictStore

    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


# ---------------------------------------------------------------------------
# "Add {G}{U}" is two mana, not four
# ---------------------------------------------------------------------------


def test_a_dual_land_makes_one_of_each_colour(box):
    """The IR used to hold a count and a set of colours, and the executor
    looped the count over every colour - so a two-mana dual made two green
    *and* two blue. Every dual land in the format produced double, which
    inflates ramp everywhere and shows up as nothing but good luck."""
    box.put("Simic Growth Chamber", "battlefield", 0)
    box.game.objects[next(iter(box.game.objects))].tapped = False

    action = next(a for a in box.legal() if "Add" in a["description"])
    box.perform(action["index"])

    pool = box.game.player(0).mana_pool
    assert pool.total == 2, f"expected two mana, got {pool}"


def test_a_colourless_source_makes_colourless_mana(box):
    box.put("Sol Ring", "battlefield", 0)
    action = next(a for a in box.legal() if "Add" in a["description"])
    box.perform(action["index"])
    assert box.game.player(0).mana_pool.total == 2


def test_a_single_colour_source_is_unaffected(box):
    box.put("Llanowar Elves", "battlefield", 0)
    action = next(a for a in box.legal() if "Add" in a["description"])
    box.perform(action["index"])
    assert box.game.player(0).mana_pool.total == 1


# ---------------------------------------------------------------------------
# "Enters tapped" (CR 614.1c)
# ---------------------------------------------------------------------------


def test_a_tapland_played_from_hand_enters_tapped(box):
    """The engine had the ENTERS_TAPPED replacement and the parser produced
    the effect; nothing connected them, because a permanent that is entering
    has not yet contributed anything to the replacement registry. Every
    tapland in the format was a fast land."""
    box.put("Temple of Mystery", "hand", 0)
    action = next(a for a in box.legal() if "Temple" in a["description"])
    box.perform(action["index"])

    land = box.state()["players"][0]["battlefield"][0]
    assert land["tapped"] is True


def test_an_ordinary_land_still_enters_untapped(box):
    box.put("Forest", "hand", 0)
    action = next(a for a in box.legal() if "Forest" in a["description"])
    box.perform(action["index"])
    assert box.state()["players"][0]["battlefield"][0]["tapped"] is False


def test_placing_a_tapland_agrees_with_playing_one(box):
    """An instrument that disagrees with the engine it exists to test is
    worse than no instrument, so ``put`` runs the same entry effects."""
    box.put("Temple of Mystery", "battlefield", 0)
    assert box.state()["players"][0]["battlefield"][0]["tapped"] is True


def test_a_creature_that_enters_with_counters_gets_them(box, card_db):
    """The other half of CR 614.1c, on the same code path as enters-tapped."""
    from mtgfish.parser import parse_card
    from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind

    name = None
    for index, card in enumerate(card_db.iter_cards(commander_legal_only=True)):
        if index > 6000:
            break
        text = (card.faces[0].oracle_text or "").lower()
        if "enters with two +1/+1 counters" not in text:
            continue
        parsed = parse_card(card)
        if any(
            node.kind is EffectKind.ADD_COUNTERS and node.text.startswith("enters ")
            for face in parsed.faces
            for ability in face.abilities
            for effect in ability.effects
            for node in effect.walk()
        ):
            name = card.name
            break

    if name is None:
        pytest.skip("no enters-with-counters card parsed in the sample")

    box.put(name, "battlefield", 0)
    entered = box.state()["players"][0]["battlefield"][0]
    assert entered["counters"].get("+1/+1") == 2, entered
