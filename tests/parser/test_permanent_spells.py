""""Permanent spell", "permanent card", "nonpermanent spell" (CR 110.4a).

Off the battlefield "permanent" is a constraint - one of the permanent types -
not a bare noun. Dropped, Codie's "You can't cast permanent spells" forbade
every spell.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.kernel.enums import PERMANENT_TYPES
from mtgfish.ui.sandbox import Sandbox


@pytest.mark.parametrize(
    "text, field",
    [("permanent spells.", "types_any"), ("nonpermanent spell.", "types_none"),
     ("permanent card", "types_any")],
)
def test_permanent_constrains_the_type(text, field):
    from mtgfish.parser.nouns import parse_object_filter
    from mtgfish.parser.tokens import Stream, tokenize

    spec = parse_object_filter(Stream(tokenize(text)))
    assert getattr(spec, field) == PERMANENT_TYPES


def test_codie_forbids_permanent_spells_only(card_db, tmp_path):
    box = Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))
    box.put("Codie, Vociferous Codex", "battlefield", 0)
    box.put("Grizzly Bears", "hand", 0)
    box.put("Lightning Bolt", "hand", 0)
    box.give_mana(5, 0)
    casts = [a["name"] for a in box.legal(0) if a["kind"] == "CAST_SPELL"]
    assert "Lightning Bolt" in casts
    assert "Grizzly Bears" not in casts
