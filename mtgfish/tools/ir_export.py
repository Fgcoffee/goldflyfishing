"""Write the parser's IR as versioned JSON, and prove the JSON is exact.

    python -m mtgfish.tools.ir_export schema            # schema/ir.schema.json, engine/src/ir/{types,defaults}.ts
    python -m mtgfish.tools.ir_export samples           # schema/samples/: the bench decks' cards
    python -m mtgfish.tools.ir_export usage             # schema/usage.json: which kinds the pool uses
    python -m mtgfish.tools.ir_export export            # cache/ir/v1/<oracle_id>.json + index.json
    python -m mtgfish.tools.ir_export export --out DIR
    python -m mtgfish.tools.ir_export verify            # every face: IR -> JSON -> IR is equal
    python -m mtgfish.tools.ir_export verify --schema   # ... and validates against the schema

``export`` writes one file per card (all its faces), named by oracle id, so
a browser fetches only the cards in the decks it simulates. ``index.json``
maps every card and face name to its oracle id and card hash: a client
that holds a file whose hash still matches need not fetch it again.

``verify`` parses every face live, encodes it, serialises the JSON to text
and back, decodes it, and compares with the original - both ``==`` and the canonical
encoding, which is type-exact, so a value that compares equal but is of a
different type (an int where an enum was, a list where a tuple was) is
caught too. (``repr`` is not used: a frozenset's repr depends on hash order.) It exits non-zero on any mismatch.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent

_WORKER_DB = None
_WORKER_SCHEMA = None


def _init(db_path: str, with_schema: bool) -> None:
    global _WORKER_DB, _WORKER_SCHEMA
    os.environ["MTGFISH_PARSE_STORE"] = "off"
    from ..data.db import CardDatabase

    _WORKER_DB = CardDatabase(Path(db_path))
    _WORKER_DB.registry()
    if with_schema:
        import jsonschema

        from ..ir.schema import json_schema

        _WORKER_SCHEMA = jsonschema.Draft202012Validator(json_schema())


def _card_document(card) -> dict:
    from ..ir import IR_VERSION, encode_face
    from ..parser.compile import parse_face
    from ..parser.store import card_hash

    digest = card_hash(card)
    faces = []
    for index in range(max(1, len(card.faces))):
        parsed = parse_face(card, index)
        faces.append(encode_face(card, index, parsed.abilities, parsed.failures, digest))
    return {
        "ir_version": IR_VERSION,
        "oracle_id": card.oracle_id,
        "name": card.name,
        "card_hash": digest,
        "faces": faces,
    }


def _export_chunk(args) -> list[tuple[str, str, list[str], str]]:
    oracle_ids, out_dir = args
    rows = []
    for oracle_id in oracle_ids:
        card = _WORKER_DB.by_oracle_id(oracle_id)
        if card is None:
            continue
        document = _card_document(card)
        text = json.dumps(document, separators=(",", ":"), ensure_ascii=False)
        (Path(out_dir) / f"{oracle_id}.json").write_text(text, encoding="utf-8", newline="\n")
        names = [face["face_name"] for face in document["faces"]]
        rows.append((oracle_id, card.name, names, document["card_hash"]))
    return rows


def _verify_chunk(oracle_ids: list[str]) -> tuple[int, list[str]]:
    from ..ir import decode_face, encode_face
    from ..ir.codec import exact
    from ..parser.compile import parse_face
    from ..parser.store import card_hash

    checked = 0
    problems: list[str] = []
    for oracle_id in oracle_ids:
        card = _WORKER_DB.by_oracle_id(oracle_id)
        if card is None:
            continue
        digest = card_hash(card)
        for index in range(max(1, len(card.faces))):
            try:
                parsed = parse_face(card, index)
            except Exception as error:  # noqa: BLE001 - a parser crash, reported
                problems.append(f"{card.name} face {index}: parser raised {error!r}")
                continue
            abilities, failures = tuple(parsed.abilities), list(parsed.failures)
            try:
                document = encode_face(card, index, abilities, failures, digest)
                text = json.dumps(document, separators=(",", ":"), ensure_ascii=False)
                back = json.loads(text)
                if _WORKER_SCHEMA is not None:
                    errors = list(_WORKER_SCHEMA.iter_errors(back))
                    if errors:
                        first = errors[0]
                        problems.append(
                            f"{card.name} face {index}: schema: {first.message[:200]}"
                            f" at {'/'.join(str(p) for p in first.absolute_path)}"
                        )
                got_abilities, got_failures = decode_face(back)
            except Exception as error:  # noqa: BLE001 - a codec failure, reported
                problems.append(f"{card.name} face {index}: codec raised {error!r}")
                continue
            checked += 1
            if not exact(got_abilities, abilities):
                problems.append(f"{card.name} face {index}: abilities differ after the round trip")
            if not exact(got_failures, failures):
                problems.append(f"{card.name} face {index}: failures differ after the round trip")
    return checked, problems


def _db():
    from ..data.db import CardDatabase

    db = CardDatabase()
    db.registry()
    return db


def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def cmd_schema(args) -> int:
    from ..ir.schema import json_schema, typescript, typescript_defaults

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ir.schema.json").write_text(
        json.dumps(json_schema(), indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    written = [out / "ir.schema.json"]
    engine = Path(args.engine)
    if engine.is_dir():
        # The engine's TS types: the same shapes as the schema, generated
        # from the same classes. engine/src/ir/types.ts is the one TS copy.
        (engine / "types.ts").write_text(typescript(), encoding="utf-8", newline="\n")
        (engine / "defaults.ts").write_text(typescript_defaults(), encoding="utf-8", newline="\n")
        written += [engine / "types.ts", engine / "defaults.ts"]
    print("wrote " + ", ".join(str(p) for p in written))
    return 0


#: The enums whose members say what the engine has to implement.
_USAGE_ENUMS = ("EffectKind", "ConditionKind", "CostKind", "EventKind", "ValueKind",
                "PlayerScope", "AbilityKind", "Duration", "Act")


def _usage_of(value, counts, seen) -> None:
    """Count every enum member and IR class in one face's JSON, once per face."""
    if isinstance(value, list):
        for item in value:
            _usage_of(item, counts, seen)
    elif isinstance(value, dict):
        name = value.get("$")
        if name is not None:
            seen.add(("class", name))
        if name == "Ability" and value.get("keyword"):
            # Expanded already, but the engine still asks for keywords by
            # name (flying when blocking, deathtouch when dealing damage).
            seen.add(("keyword", value["keyword"]))
        for key, item in value.items():
            if key != "$":
                _usage_of(item, counts, seen)
    elif isinstance(value, str):
        head, dot, member = value.partition(".")
        if dot and head in _USAGE_ENUMS:
            seen.add((head, member))


def _usage_chunk(oracle_ids: list[str]) -> tuple[dict, dict]:
    from collections import Counter

    from ..ir import encode
    from ..parser.compile import parse_face

    all_faces: Counter = Counter()
    read_faces: Counter = Counter()
    for oracle_id in oracle_ids:
        card = _WORKER_DB.by_oracle_id(oracle_id)
        if card is None:
            continue
        for index in range(max(1, len(card.faces))):
            parsed = parse_face(card, index)
            seen: set = set()
            _usage_of([encode(a) for a in parsed.abilities if not a.unparsed], None, seen)
            for key in seen:
                all_faces[key] += 1
                if not parsed.failures:
                    read_faces[key] += 1
    return dict(all_faces), dict(read_faces)


def cmd_usage(args) -> int:
    """How many faces use each effect, condition, cost, event, ... kind -
    the order in which the engine port pays off. Counts only abilities the
    parser read; a face counts once per kind."""
    from collections import Counter
    from concurrent.futures import ProcessPoolExecutor

    db = _db()
    pool = [card.oracle_id for card in db.iter_cards(format_legal_only=True)]
    all_faces: Counter = Counter()
    read_faces: Counter = Counter()
    with ProcessPoolExecutor(
        max_workers=args.jobs or os.cpu_count() or 1,
        initializer=_init,
        initargs=(str(db.path), False),
    ) as executor:
        for got_all, got_read in executor.map(_usage_chunk, _chunks(pool, 200)):
            all_faces.update(got_all)
            read_faces.update(got_read)
    samples = Path(args.samples)
    in_bench: Counter = Counter()
    for path in samples.glob("*.json"):
        if path.name == "decks.json":
            continue
        for face in json.loads(path.read_text(encoding="utf-8"))["faces"]:
            seen: set = set()
            _usage_of([a for a in face["abilities"] if not a.get("unparsed")], None, seen)
            in_bench.update(seen)
    groups: dict[str, list] = {}
    for (group, member), count in all_faces.items():
        groups.setdefault(group, []).append({
            "member": member,
            "faces": count,
            "fully_read_faces": read_faces.get((group, member), 0),
            "bench_faces": in_bench.get((group, member), 0),
        })
    for rows in groups.values():
        rows.sort(key=lambda r: (-r["bench_faces"], -r["faces"], r["member"]))
    out = Path(args.out)
    out.write_text(json.dumps({
        "about": "Faces (legal in a format) whose read abilities use each IR class or enum"
                 " member, all faces and fully read faces; bench_faces counts the bench"
                 " decks' samples. Generated by `ir_export usage`.",
        "groups": dict(sorted(groups.items())),
    }, indent=1) + "\n",
        encoding="utf-8", newline="\n"
    )
    print(f"wrote {out}: {sum(len(r) for r in groups.values())} kinds over {len(pool)} cards")
    return 0


def cmd_samples(args) -> int:
    """schema/samples/: the IR of every card in the bench decks, for the
    engine's tests to load without the card database."""
    from ..data.decks.loader import load_deck

    db = _db()
    os.environ["MTGFISH_PARSE_STORE"] = "off"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    decks: dict[str, dict] = {}
    written: set[str] = set()
    for path in sorted(Path(args.decks).glob("*.txt")):
        deck = load_deck(str(path), db)
        cards = list(deck.commanders) + [entry.card for entry in deck.entries]
        decks[path.stem] = {
            "commanders": [card.oracle_id for card in deck.commanders],
            "cards": [
                {"oracle_id": entry.card.oracle_id, "name": entry.card.name, "count": entry.quantity}
                for entry in deck.entries
            ],
            "unresolved": list(deck.unresolved),
        }
        for card in cards:
            if card.oracle_id in written:
                continue
            document = _card_document(card)
            (out / f"{card.oracle_id}.json").write_text(
                json.dumps(document, indent=1, ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            written.add(card.oracle_id)
    (out / "decks.json").write_text(
        json.dumps(decks, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {len(written)} cards of {len(decks)} decks to {out}")
    return 0


def cmd_export(args) -> int:
    from concurrent.futures import ProcessPoolExecutor

    from ..ir import IR_VERSION
    from ..paths import data_root

    from ..ir.store import code_fingerprint

    db = _db()
    # Taken before parsing, so code edited during the export leaves it stale.
    code = code_fingerprint()
    out = Path(args.out) if args.out else data_root() / "ir" / f"v{IR_VERSION}"
    out.mkdir(parents=True, exist_ok=True)
    oracle_ids = [card.oracle_id for card in db.iter_cards()]
    started = time.time()
    cards: dict[str, dict] = {}
    names: dict[str, str] = {}
    with ProcessPoolExecutor(
        max_workers=args.jobs or os.cpu_count() or 1,
        initializer=_init,
        initargs=(str(db.path), False),
    ) as pool:
        work = [(chunk, str(out)) for chunk in _chunks(oracle_ids, 200)]
        for rows in pool.map(_export_chunk, work):
            for oracle_id, name, face_names, digest in rows:
                cards[oracle_id] = {"name": name, "card_hash": digest}
                for alias in {name, *face_names}:
                    names.setdefault(alias.lower(), oracle_id)
    index = {
        "ir_version": IR_VERSION,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "snapshot": str(getattr(db, "content_hash", "")),
        # The code that wrote it: an export from other code is stale.
        "code": code,
        "cards": cards,
        "names": names,
    }
    (out / "index.json").write_text(
        json.dumps(index, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )
    total = sum(p.stat().st_size for p in out.glob("*.json"))
    print(
        f"wrote {len(cards)} cards to {out} in {time.time() - started:.0f}s"
        f" ({total / 1e6:.1f} MB, {total / max(1, len(cards)) / 1e3:.1f} kB per card)"
    )
    return 0


def cmd_verify(args) -> int:
    from concurrent.futures import ProcessPoolExecutor

    db = _db()
    oracle_ids = [card.oracle_id for card in db.iter_cards()]
    if args.sample:
        import random

        oracle_ids = random.SystemRandom().sample(oracle_ids, min(args.sample, len(oracle_ids)))
    started = time.time()
    checked = 0
    problems: list[str] = []
    with ProcessPoolExecutor(
        max_workers=args.jobs or os.cpu_count() or 1,
        initializer=_init,
        initargs=(str(db.path), args.schema),
    ) as pool:
        for count, found in pool.map(_verify_chunk, _chunks(oracle_ids, 200)):
            checked += count
            problems += found
    for line in problems[:50]:
        print(line)
    if len(problems) > 50:
        print(f"... and {len(problems) - 50} more")
    print(
        f"{checked} faces of {len(oracle_ids)} cards round-tripped"
        f"{' and validated' if args.schema else ''} in {time.time() - started:.0f}s:"
        f" {len(problems)} problems"
    )
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    schema = sub.add_parser("schema")
    schema.add_argument("--out", default=str(_REPO / "schema"))
    schema.add_argument("--engine", default=str(_REPO / "engine" / "src" / "ir"))
    schema.set_defaults(func=cmd_schema)
    usage = sub.add_parser("usage")
    usage.add_argument("--samples", default=str(_REPO / "schema" / "samples"))
    usage.add_argument("--out", default=str(_REPO / "schema" / "usage.json"))
    usage.add_argument("--jobs", type=int, default=None)
    usage.set_defaults(func=cmd_usage)
    samples = sub.add_parser("samples")
    samples.add_argument("--decks", default=str(_REPO / "bench" / "decks"))
    samples.add_argument("--out", default=str(_REPO / "schema" / "samples"))
    samples.set_defaults(func=cmd_samples)
    export = sub.add_parser("export")
    export.add_argument("--out", default=None)
    export.add_argument("--jobs", type=int, default=None)
    export.set_defaults(func=cmd_export)
    verify = sub.add_parser("verify")
    verify.add_argument("--schema", action="store_true", help="also validate against the schema")
    verify.add_argument("--sample", type=int, default=0, help="only this many random cards")
    verify.add_argument("--jobs", type=int, default=None)
    verify.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
