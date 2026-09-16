"""A clone that enters as a copy has to actually copy something.

"You may have this creature enter as a copy of a creature you control" parsed
perfectly - OPTIONAL wrapping COPY_PERMANENT, targets read, nothing marked
unparsed - and did nothing. The entry-replacement handler knew two shapes,
tapping and counters, and this was a third, so thirteen clones in the played
pool entered as their printed selves: 0/0 creatures that die to state-based
actions before anyone can respond.

The same shape as the Equipment bug in ``test_attachment_grants``: a correct
parse, a correct opcode, and nothing joining them - which the coverage report
scores as a success.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.ui.sandbox import Sandbox


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


def _on_battlefield(box, printed_name):
    """The object whose *card* is this one, whatever it now looks like.

    Looked up by card rather than by characteristics on purpose: after the
    copy its name is the thing it copied, which is the whole point.
    """
    for obj in box.game.objects.values():
        card = getattr(obj, "card", None)
        if card is None or obj.zone.name != "BATTLEFIELD":
            continue
        if printed_name in getattr(card, "name", ""):
            return obj
    raise AssertionError(f"{printed_name} is not on the battlefield")


@pytest.mark.parametrize(
    "clone",
    ["Glasspool Mimic", "Clever Impersonator", "Phyrexian Metamorph"],
)
def test_a_clone_enters_as_a_copy_of_the_best_creature(box, clone):
    if box.db.lookup(clone) is None or box.db.lookup("Serra Angel") is None:
        pytest.skip(f"{clone} is not in this card pool")

    box.put("Serra Angel", "battlefield", 0)
    box.put(clone, "battlefield", 0)
    box.game.invalidate_characteristics()

    copy = _on_battlefield(box, clone.split(" //")[0])
    chars = box.game.characteristics(copy)
    assert chars.name == "Serra Angel", "the clone entered as its printed self"
    assert (chars.power, chars.toughness) == (4, 4)


def test_a_clone_with_nothing_to_copy_enters_as_itself(box):
    """CR 706.2: no legal choice means the permanent enters as printed.

    Worth pinning because the failure mode of "copy something" code is
    usually a crash on an empty board, and a clone cast into an empty board
    is an ordinary thing to do.
    """
    if box.db.lookup("Clever Impersonator") is None:
        pytest.skip("Clever Impersonator is not in this card pool")

    box.put("Clever Impersonator", "battlefield", 0)
    box.game.invalidate_characteristics()

    copy = _on_battlefield(box, "Clever Impersonator")
    assert box.game.characteristics(copy).name == "Clever Impersonator"


def test_the_choice_is_deterministic(box):
    """Two identical boards must clone the same creature.

    Every replay in the run depends on it: the seed decides the shuffle, and
    anything that picks a target by iteration order would make the same seed
    produce two different games.
    """
    if box.db.lookup("Clever Impersonator") is None:
        pytest.skip("Clever Impersonator is not in this card pool")

    names = []
    for _ in range(2):
        table = Sandbox(db=box.db, verdicts=box.verdicts)
        table.put("Grizzly Bears", "battlefield", 0)
        table.put("Serra Angel", "battlefield", 0)
        table.put("Clever Impersonator", "battlefield", 0)
        table.game.invalidate_characteristics()
        copy = _on_battlefield(table, "Clever Impersonator")
        names.append(table.game.characteristics(copy).name)

    assert names[0] == names[1]
    assert names[0] == "Serra Angel", "the bigger creature is the obvious copy"
