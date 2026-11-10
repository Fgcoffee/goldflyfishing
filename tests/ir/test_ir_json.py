"""The parser's IR as JSON: exact both ways, and the schema files current.

The whole pool is checked by ``python -m mtgfish.tools.ir_export verify``
(about 90 seconds); this checks the encoding rules and a spread of cards
that exercise every corner of them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mtgfish.ir import IR_VERSION, decode, decode_face, encode, encode_face
from mtgfish.ir.codec import IRError, exact
from mtgfish.parser.compile import parse_face
from mtgfish.parser.store import card_hash
from mtgfish.rules.kernel.enums import CardType, Duration, Zone
from mtgfish.rules.kernel.query import ObjectFilter

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"
ENGINE_IR_DIR = Path(__file__).resolve().parents[2] / "engine" / "src" / "ir"

#: Cards that between them reach any_of, token copies, frozensets of zones,
#: flag enums, granted abilities, modal costs, restrictions, unread abilities,
#: keyword expansions, mana symbols and double faces.
CARDS = (
    "Lightning Bolt", "Bounce Off", "Replication Technique", "Spring-Loaded Sawblades",
    "Delver of Secrets", "Inscription of Insight", "Akroma's Will", "Torment of Scarabs",
    "Curse of the Bloody Tome", "Llanowar Elves", "Esika's Chariot", "Sol Ring",
    "Vivid Marsh", "Faerie Impostor", "Alpha Deathclaw", "Blackblade Reforged",
    "Ward of Bones", "Progenitus", "Cyclonic Rift", "Smashing Success",
)


def _round_trip(card_db, name):
    card = card_db.lookup(name)
    assert card is not None, name
    for index in range(max(1, len(card.faces))):
        parsed = parse_face(card, index)
        abilities, failures = tuple(parsed.abilities), list(parsed.failures)
        document = encode_face(card, index, abilities, failures, card_hash(card))
        back = json.loads(json.dumps(document))
        got_abilities, got_failures = decode_face(back)
        assert exact(got_abilities, abilities), (name, index)
        assert exact(got_failures, failures), (name, index)
        assert back["fully_read"] == (not failures)


@pytest.mark.parametrize("name", CARDS)
def test_round_trip_is_exact(card_db, name):
    _round_trip(card_db, name)


def test_enum_and_flag_encoding():
    assert encode(Zone.GRAVEYARD) == "Zone.GRAVEYARD"
    assert encode(CardType.ARTIFACT | CardType.CREATURE) == "CardType.ARTIFACT|CREATURE"
    assert encode(CardType.NONE) == "CardType.NONE"
    assert decode("CardType.ARTIFACT|CREATURE") == CardType.ARTIFACT | CardType.CREATURE
    assert decode(encode(Duration.END_OF_TURN)) is Duration.END_OF_TURN


def test_types_are_kept_apart():
    # 0, False and an enum whose value is 0 compare equal; the JSON keeps them apart.
    assert encode(0) == 0 and encode(False) is False
    assert decode(encode((1, True, "x"))) == (1, True, "x")
    assert type(decode(encode((True,)))[0]) is bool
    assert not exact([1], (1,))
    assert encode(frozenset({Zone.HAND, Zone.EXILE})) == {"$set": ["Zone.EXILE", "Zone.HAND"]}


def test_defaults_are_left_out_and_restored():
    spec = ObjectFilter(types_all=CardType.CREATURE)
    data = encode(spec)
    assert data == {"$": "ObjectFilter", "types_all": "CardType.CREATURE"}
    assert decode(data) == spec
    # A default zone set written explicitly differs from one left out only if
    # its value differs.
    assert "zones" in encode(ObjectFilter(zones=frozenset()))


def test_a_string_that_looks_like_an_enum_survives():
    assert decode(encode("Zone.GRAVEYARD as text")) == "Zone.GRAVEYARD as text"
    assert decode(encode("Act. Then something")) == "Act. Then something"


def test_unknown_values_are_refused():
    with pytest.raises(IRError):
        encode(1.5)
    with pytest.raises(IRError):
        decode({"$": "NotAClass"})
    with pytest.raises(IRError):
        decode({"$": "ObjectFilter", "no_such_field": 1})
    with pytest.raises(IRError):
        decode_face({"ir_version": IR_VERSION + 1, "abilities": [], "failures": []})


def test_committed_schema_files_are_current():
    """schema/ir.schema.json and engine/src/ir/{types,defaults}.ts are generated; regenerate
    with `python -m mtgfish.tools.ir_export schema`."""
    from mtgfish.ir.schema import json_schema, typescript, typescript_defaults

    committed = json.loads((SCHEMA_DIR / "ir.schema.json").read_text())
    assert committed == json_schema()
    assert (ENGINE_IR_DIR / "types.ts").read_text() == typescript()
    assert (ENGINE_IR_DIR / "defaults.ts").read_text() == typescript_defaults()


def test_faces_validate_against_the_schema(card_db):
    jsonschema = pytest.importorskip("jsonschema")
    from mtgfish.ir.schema import json_schema

    validator = jsonschema.Draft202012Validator(json_schema())
    for name in CARDS:
        card = card_db.lookup(name)
        for index in range(max(1, len(card.faces))):
            parsed = parse_face(card, index)
            document = json.loads(json.dumps(
                encode_face(card, index, parsed.abilities, parsed.failures, card_hash(card))
            ))
            errors = list(validator.iter_errors(document))
            assert not errors, (name, errors[0].message[:300])


def test_usage_counts_each_kind_once_per_face():
    from mtgfish.tools.ir_export import _usage_of

    seen: set = set()
    _usage_of([
        {"$": "Ability", "keyword": "Flying", "effects": [
            {"$": "Effect", "kind": "EffectKind.DRAW"},
            {"$": "Effect", "kind": "EffectKind.DRAW", "text": "Zone.HAND"},
        ]},
    ], None, seen)
    assert ("EffectKind", "DRAW") in seen
    assert ("keyword", "Flying") in seen
    assert ("class", "Effect") in seen
    # Only the enums the usage report is about; a Zone is not one.
    assert not any(group == "Zone" for group, _ in seen)
