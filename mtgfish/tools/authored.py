"""Write and inspect authored readings (``mtgfish.ir.authored``).

    python -m mtgfish.tools.authored show "Mother of Runes"
    python -m mtgfish.tools.authored write "Mother of Runes" \\
        --as 0.0 "{T}: Target creature you control gains protection from the color of your choice until end of turn." \\
        --note "why this card is authored"
    python -m mtgfish.tools.authored check     # every authored file current and valid

``show`` lists each ability by ``face.index`` with the parser's reading.
``write`` keeps every ability the parser reads, and for each ``--as FACE.INDEX
PARAPHRASE`` parses the paraphrase in the card's context (same name, types
and faces) and stores the result: it must read completely, as one ability.
An empty paraphrase folds that line into another line's paraphrase (the
parser split one ability in two).
The paraphrase is kept beside the IR for the reviewer. An ability that is
neither read nor paraphrased stops the write.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from ..ir import authored as store


def _db():
    from ..data.db import CardDatabase

    db = CardDatabase()
    db.registry()
    return db


def _faces(card):
    from ..parser.compile import parse_face

    return [parse_face(card, index) for index in range(max(1, len(card.faces)))]


def _read(ability) -> bool:
    from ..ir.codec import _unread

    return not _unread(ability)


def _paraphrase(card, face_index: int, text: str):
    """The single ability ``text`` reads as on this face of this card."""
    from ..parser.compile import parse_face

    face = card.faces[face_index]
    faces = list(card.faces)
    faces[face_index] = dataclasses.replace(face, oracle_text=text)
    variant = dataclasses.replace(card, faces=tuple(faces))
    parsed = parse_face(variant, face_index)
    if parsed.failures or len(parsed.abilities) != 1 or not _read(parsed.abilities[0]):
        reasons = "; ".join(f.reason for f in parsed.failures) or f"{len(parsed.abilities)} abilities"
        raise SystemExit(f"paraphrase does not read as one ability: {text!r} ({reasons})")
    return parsed.abilities[0]


def cmd_show(args) -> int:
    from ..parser.explain import explain_ability

    card = _db().lookup(args.name)
    if card is None:
        raise SystemExit(f"no card named {args.name!r}")
    for f, face in enumerate(_faces(card)):
        for i, ability in enumerate(face.abilities):
            state = "read" if _read(ability) else "UNREAD"
            print(f"{f}.{i} [{state}] {ability.text}")
            if state == "read":
                print(f"      {explain_ability(ability)}")
        for failure in face.failures:
            if failure.text not in {a.text for a in face.abilities}:
                print(f"{f}.- [UNREAD, no ability] {failure.text}")
    return 0


def cmd_write(args) -> int:
    from ..ir.codec import encode
    from ..parser.store import card_hash

    card = _db().lookup(args.name)
    if card is None:
        raise SystemExit(f"no card named {args.name!r}")
    wanted = {key: text for key, text in args.paraphrase}
    faces_out = []
    missing = []
    for f, face in enumerate(_faces(card)):
        orphans = [x.text for x in face.failures if x.text not in {a.text for a in face.abilities}]
        if orphans:
            missing.extend(f"{f}.- {text}" for text in orphans)
        rows = []
        for i, ability in enumerate(face.abilities):
            key = f"{f}.{i}"
            if key in wanted and wanted[key] == "":
                # Folded into another line's paraphrase (Cabal Ritual's
                # threshold line belongs to its first): no ability of its own.
                wanted.pop(key)
                rows.append({"text": ability.text, "as": "", "ir": None})
            elif key in wanted:
                read = dataclasses.replace(_paraphrase(card, f, wanted.pop(key)), text=ability.text)
                rows.append({"text": ability.text, "as": args_text(args, key), "ir": encode(read, "Ability")})
            elif _read(ability):
                rows.append({"text": ability.text, "as": None, "ir": encode(ability, "Ability")})
            else:
                missing.append(f"{key} {ability.text}")
        faces_out.append({"abilities": rows})
    if wanted:
        raise SystemExit(f"no such abilities: {', '.join(wanted)}")
    if missing:
        raise SystemExit("unread and not paraphrased:\n  " + "\n  ".join(missing))
    data = {
        "oracle_id": card.oracle_id,
        "name": card.name,
        "card_hash": card_hash(card),
        "note": args.note,
        "faces": faces_out,
    }
    store.DIRECTORY.mkdir(exist_ok=True)
    path = store.DIRECTORY / f"{card.oracle_id}.json"
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {path}")
    return 0


def args_text(args, key: str) -> str:
    return dict(args.paraphrase)[key]


def problems() -> list[str]:
    """Every authored file that is stale, invalid or unknown."""
    from ..parser.store import card_hash

    db = _db()
    out = []
    for oracle_id in store.oracle_ids():
        card = db.by_oracle_id(oracle_id)
        if card is None:
            out.append(f"{oracle_id}: no such card")
            continue
        data = store.entry(oracle_id)
        if data.get("card_hash") != card_hash(card):
            out.append(f"{card.name}: card text changed since it was authored; re-author or remove it")
            continue
        try:
            faces = store.abilities(card, card_hash(card))
        except Exception as exc:  # noqa: BLE001
            out.append(f"{card.name}: IR does not decode ({type(exc).__name__}: {exc})")
            continue
        if len(faces) != max(1, len(card.faces)):
            out.append(f"{card.name}: {len(faces)} faces authored, the card has {len(card.faces)}")
        for face in faces:
            for ability in face:
                if not _read(ability):
                    out.append(f"{card.name}: an authored ability holds an unread node: {ability.text}")
    return out


def cmd_check(args) -> int:
    found = problems()
    for line in found:
        print(line)
    print(f"{len(store.oracle_ids())} authored cards, {len(found)} problems")
    return 1 if found else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show")
    show.add_argument("name")
    show.set_defaults(fn=cmd_show)
    write = sub.add_parser("write")
    write.add_argument("name")
    write.add_argument("--as", dest="paraphrase", nargs=2, action="append", default=[], metavar=("FACE.INDEX", "TEXT"))
    write.add_argument("--note", required=True)
    write.set_defaults(fn=cmd_write)
    check = sub.add_parser("check")
    check.set_defaults(fn=cmd_check)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
