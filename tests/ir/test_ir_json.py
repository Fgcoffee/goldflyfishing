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

    committed = json.loads((SCHEMA_DIR / "ir.schema.json").read_text(encoding="utf-8"))
    assert committed == json_schema()
    assert (ENGINE_IR_DIR / "types.ts").read_text(encoding="utf-8") == typescript()
    assert (ENGINE_IR_DIR / "defaults.ts").read_text(encoding="utf-8") == typescript_defaults()


def test_card_documents_validate_against_the_schema(card_db):
    jsonschema = pytest.importorskip("jsonschema")
    from mtgfish.ir.schema import json_schema
    from mtgfish.tools.ir_export import _card_document

    validator = jsonschema.Draft202012Validator(json_schema())
    for name in CARDS:
        document = json.loads(json.dumps(_card_document(card_db.lookup(name))))
        errors = list(validator.iter_errors(document))
        assert not errors, (name, errors[0].message[:300])


def test_printed_card_round_trips(card_db):
    """The printed card - layout, faces with mana cost, type line, P/T,
    loyalty, defense, colors; mana value, color identity - comes back equal."""
    from mtgfish.ir import decode_card, encode_card

    for name in (*CARDS, "Fire // Ice", "Jace, Vryn's Prodigy", "Invasion of Ikoria", "Tarmogoyf"):
        card = card_db.lookup(name)
        assert card is not None, name
        back = decode_card(json.loads(json.dumps(encode_card(card))))
        assert exact(back, card), name
    goyf = json.loads(json.dumps(encode_card(card_db.lookup("Tarmogoyf"))))
    face = goyf["faces"][0]
    assert face["power"] == "*" and face["toughness"] == "1+*"


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


def test_store_serves_exported_files_and_falls_back_to_live(card_db, tmp_path):
    from mtgfish.ir.store import IRStore
    from mtgfish.tools.ir_export import _card_document

    bolt = card_db.lookup("Lightning Bolt")
    ring = card_db.lookup("Sol Ring")
    # An exported file for Bolt; a stale one (wrong card_hash) for Sol Ring.
    (tmp_path / f"{bolt.oracle_id}.json").write_text(json.dumps(_card_document(bolt)), encoding="utf-8", newline="\n")
    stale = _card_document(ring)
    stale["card_hash"] = "0" * 32
    (tmp_path / f"{ring.oracle_id}.json").write_text(json.dumps(stale), encoding="utf-8", newline="\n")

    store = IRStore.open(card_db, tmp_path)
    documents, missing = store.deck(["Lightning Bolt", "Sol Ring", "Lightning Bolt", "No Such Card"])
    assert [d["name"] for d in documents] == ["Lightning Bolt", "Sol Ring"]
    assert missing == ["No Such Card"]
    assert store.live_parses == 1  # Sol Ring's file was stale; Bolt's was used
    assert documents[1]["card_hash"] == card_hash(ring)
    for document in documents:
        for face in document["faces"]:
            decode_face(face)


def test_every_file_access_names_utf8():
    """On Windows the default encoding is cp1252, and a card name such as
    "Ōkami" made the exporter raise UnicodeEncodeError. Every read and write
    in the IR tooling names its encoding."""
    import ast

    root = Path(__file__).resolve().parents[2]
    sources = [root / "mtgfish" / "tools" / "ir_export.py", *(root / "mtgfish" / "ir").glob("*.py")]
    missing = []
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name not in ("write_text", "read_text", "open"):
                continue
            if name == "open" and isinstance(node.func, ast.Attribute):
                continue  # IRStore.open, not a file
            if not any(k.arg == "encoding" for k in node.keywords):
                missing.append(f"{path.name}:{node.lineno} {name}")
    assert not missing, missing


def test_an_export_from_other_code_is_stale(card_db, tmp_path):
    """index.json records the code that wrote it. After a parser change and no
    re-export, the old files must not be served as current."""
    from mtgfish.ir.store import IRStore, code_fingerprint
    from mtgfish.tools.ir_export import _card_document

    bolt = card_db.lookup("Lightning Bolt")
    (tmp_path / f"{bolt.oracle_id}.json").write_text(
        json.dumps(_card_document(bolt)), encoding="utf-8", newline="\n"
    )

    def index(code):
        (tmp_path / "index.json").write_text(
            json.dumps({"ir_version": IR_VERSION, "code": code}), encoding="utf-8", newline="\n"
        )

    index(code_fingerprint())
    store = IRStore.open(card_db, tmp_path)
    store.deck(["Lightning Bolt"])
    assert store.current and store.live_parses == 0

    index("written by some other parser")
    store = IRStore.open(card_db, tmp_path)
    documents, _ = store.deck(["Lightning Bolt"])
    assert not store.current and store.live_parses == 1
    assert documents[0]["name"] == "Lightning Bolt"
