"""A full-pool parse census: every card, every ability, in parallel.

    python -m mtgfish.tools.census                       # summary + work queue
    python -m mtgfish.tools.census --out census.jsonl.gz # keep the per-card record
    python -m mtgfish.tools.census --baseline old.jsonl.gz --out new.jsonl.gz
    python -m mtgfish.tools.census --grep "villainous choice"

``parse_report`` answers "how much of the pool is read". This answers the
three questions that working on the parser actually needs answered, fast
enough to ask after every change:

* **What is still unread, grouped by construct.** Failures are clustered by the
  words sitting where the parse gave up, so the biggest cluster is the next
  grammar rule worth writing.
* **What did my change break.** Against a baseline census, every card that was
  read and now is not, and every card that was read and now reads
  *differently*, is listed with the before-and-after round-trip. A grammar
  change that gains two hundred cards and silently changes the meaning of
  thirty others has to be looked at before it is kept, and nothing else in the
  repository shows those thirty.
* **Which cards are in scope.** The default pool is every card legal in at
  least one format. Cards legal nowhere - joke cards, playtest cards - are
  counted separately and never held against the parser.

The record for a read ability is a hash of the whole ``Ability`` value, not of
its explanation: the round-trip is for a person to read, and two different
parses can explain identically.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

POOLS = ("format", "commander", "all")

# ---------------------------------------------------------------------------
# Worker side
# ---------------------------------------------------------------------------

_DB = None


def _worker_db():
    global _DB
    if _DB is None:
        from ..data.db import CardDatabase

        _DB = CardDatabase()
        _DB.registry()
    return _DB


def _census_one(card) -> dict:
    from ..parser import parse_card
    from ..parser.compile import understood
    from ..parser.explain import explain_ability

    parsed = parse_card(card)
    abilities = []
    for face_index, face in enumerate(parsed.faces):
        by_text: dict[str, list] = collections.defaultdict(list)
        for failure in face.failures:
            by_text[failure.text].append(failure)
        for ability in face.abilities:
            ok = understood(ability)
            row = {
                "face": face_index,
                "text": ability.text,
                "ok": ok,
                "kind": ability.kind.name,
            }
            try:
                row["ir"] = explain_ability(ability)
            except Exception as exc:  # noqa: BLE001 - a census must finish
                row["ir"] = f"(explain raised {type(exc).__name__}: {exc})"
            if ok:
                row["hash"] = hashlib.sha1(repr(ability).encode()).hexdigest()[:16]
                # Every opcode and the snippet it was read from: the raw
                # material for auditing a clause across the whole pool
                # ("which sentences became SCRY?").
                row["nodes"] = [
                    [node.kind.name, node.text]
                    for effect in ability.effects
                    for node in effect.walk()
                ]
            else:
                failures = by_text.get(ability.text) or []
                if failures:
                    failure = failures[0]
                    row["reason"] = failure.reason
                    row["rule"] = failure.rule
                    row["remaining"] = " ".join(failure.remaining[:12])
                else:
                    # Consumed completely but inert: an UNPARSED node or an
                    # unreadable condition somewhere in the tree.
                    row["reason"] = "inert (UNPARSED node or unreadable condition)"
                    row["rule"] = "inert"
                    row["remaining"] = ""
            abilities.append(row)
        # A failure whose text matches no ability (a line that produced no
        # ability at all) still has to be counted.
        texts = {a.text for a in face.abilities}
        for failure in face.failures:
            if failure.text not in texts:
                abilities.append(
                    {
                        "face": face_index,
                        "text": failure.text,
                        "ok": False,
                        "kind": "NONE",
                        "reason": failure.reason,
                        "rule": failure.rule,
                        "remaining": " ".join(failure.remaining[:12]),
                    }
                )
    has_text = bool(abilities)
    return {
        "name": card.name,
        "oracle_id": card.oracle_id,
        "status": "notext" if not has_text else ("ok" if parsed.fully_parsed else "failed"),
        "abilities": abilities,
    }


def _census_chunk(oracle_ids: list[str]) -> list[dict]:
    db = _worker_db()
    out = []
    for oracle_id in oracle_ids:
        card = db.by_oracle_id(oracle_id)
        if card is None:
            out.append(
                {
                    "name": oracle_id,
                    "oracle_id": oracle_id,
                    "status": "unrepresentable",
                    "reason": db.unrepresentable.get(oracle_id, ""),
                    "abilities": [],
                }
            )
            continue
        try:
            out.append(_census_one(card))
        except Exception as exc:  # noqa: BLE001
            out.append(
                {
                    "name": card.name,
                    "oracle_id": oracle_id,
                    "status": "crashed",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "abilities": [],
                }
            )
    return out


# ---------------------------------------------------------------------------
# Driver side
# ---------------------------------------------------------------------------


def _oracle_texts(data: dict) -> str:
    texts = [data.get("oracle_text") or ""]
    texts.extend(face.get("oracle_text") or "" for face in data.get("card_faces") or ())
    return "\n".join(texts)


def _pool_ids(pool: str, match: str | None = None) -> list[tuple[str, int | None]]:
    from ..data.db import CardDatabase

    db = CardDatabase()
    sql = "SELECT oracle_id, edhrec_rank, data FROM cards WHERE playable = 1"
    if pool == "format":
        sql += " AND format_legal = 1"
    elif pool == "commander":
        sql += " AND commander_legal = 1"
    sql += " ORDER BY oracle_id"
    pattern = re.compile(match, re.IGNORECASE) if match else None
    rows = []
    for r in db._conn.execute(sql):
        if pattern is not None and not pattern.search(_oracle_texts(json.loads(r["data"]))):
            continue
        rows.append((r["oracle_id"], r["edhrec_rank"]))
    db.close()
    return rows


def run_census(
    pool: str = "format", jobs: int | None = None, limit: int = 0, match: str | None = None
) -> list[dict]:
    """Parse the pool and return one record per card.

    ``match`` narrows it to cards whose oracle text matches a regex, which is
    how to iterate on one construct in seconds rather than a minute; always
    finish with a full run, because a rule written for one construct changes
    how others read.
    """
    rows = _pool_ids(pool, match)
    if limit:
        rows = rows[:limit]
    ranks = dict(rows)
    ids = [oracle_id for oracle_id, _ in rows]
    jobs = jobs or os.cpu_count() or 1
    size = max(50, len(ids) // (jobs * 8) or 1)
    chunks = [ids[i : i + size] for i in range(0, len(ids), size)]

    records: list[dict] = []
    if jobs == 1:
        for chunk in chunks:
            records.extend(_census_chunk(chunk))
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool_exec:
            for result in pool_exec.map(_census_chunk, chunks):
                records.extend(result)
    for record in records:
        record["rank"] = ranks.get(record["oracle_id"])
    records.sort(key=lambda r: r["name"])
    return records


def save(records: list[dict], path: Path) -> None:
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "wt", encoding="utf8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def load(path: Path) -> list[dict]:
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def summary(records: list[dict]) -> str:
    status = collections.Counter(r["status"] for r in records)
    with_text = status["ok"] + status["failed"]
    abilities = [a for r in records for a in r["abilities"]]
    read = sum(1 for a in abilities if a["ok"])
    pct = 100.0 * status["ok"] / with_text if with_text else 100.0
    apct = 100.0 * read / len(abilities) if abilities else 100.0
    lines = [
        f"Cards:      {len(records)}  ({status['notext']} with no text,"
        f" {status['unrepresentable']} unrepresentable, {status['crashed']} crashed)",
        f"Fully read: {status['ok']} of {with_text}  ({pct:.2f}%)",
        f"Abilities:  {read} of {len(abilities)}  ({apct:.2f}%)",
    ]
    ranked = [r for r in records if r.get("rank") is not None and r["status"] in ("ok", "failed")]
    ranked.sort(key=lambda r: r["rank"])
    for top in (100, 1000, 5000):
        head = ranked[:top]
        if head:
            ok = sum(1 for r in head if r["status"] == "ok")
            lines.append(f"Top {top:>5} by play rate: {ok}/{len(head)} fully read")
    return "\n".join(lines)


_WORD = re.compile(r"[A-Za-z']+|\{[^}]+\}|[+-]?\d+/[+-]?\d+|\d+|[^\sA-Za-z\d]")


def _signature(remaining: str, words: int) -> str:
    tokens = _WORD.findall(remaining)[:words]
    return " ".join(tokens)


def clusters(records: list[dict], words: int = 3, top: int = 60, examples: int = 3) -> str:
    """Failures grouped by where they stopped, biggest first."""
    groups: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    for record in records:
        if record["status"] != "failed":
            continue
        for ability in record["abilities"]:
            if ability["ok"]:
                continue
            if ability.get("rule") == "inert":
                key = "(inert: consumed but UNPARSED inside)"
            else:
                key = f"[{ability.get('rule', '')}] {_signature(ability.get('remaining', ''), words)}"
            groups[key].append((record["name"], ability["text"]))
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    lines = [f"{len(groups)} clusters; top {min(top, len(ordered))}:"]
    for key, members in ordered[:top]:
        lines.append(f"{len(members):6}  {key}")
        for name, text in members[:examples]:
            snippet = text if len(text) < 140 else text[:137] + "..."
            lines.append(f"          - {name}: {snippet}")
    return "\n".join(lines)


def blockers(records: list[dict], top: int = 40) -> str:
    """Cards one ability away from fully read, by play rate - the cheapest wins."""
    near = []
    for record in records:
        if record["status"] != "failed":
            continue
        bad = [a for a in record["abilities"] if not a["ok"]]
        if len(bad) == 1:
            near.append((record.get("rank") or 10**9, record["name"], bad[0]))
    near.sort(key=lambda item: item[0])
    lines = [f"{len(near)} cards are exactly one ability short. Most played:"]
    for rank, name, ability in near[:top]:
        where = ability.get("remaining", "")[:60]
        lines.append(f"  #{rank if rank < 10**9 else '-':>6} {name}: {ability['text'][:90]}  <<{where}>>")
    return "\n".join(lines)


def opcode_audit(records: list[dict], opcode: str, examples: int = 4) -> str:
    """Which sentences a given opcode was read from, commonest first.

    The quickest way to find a clause that approximates: every snippet that
    became SCRY should say "scry", and one that says "look at" is a card read
    as something it does not do.
    """
    groups: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    for record in records:
        for ability in record["abilities"]:
            for kind, text in ability.get("nodes", ()):
                if kind == opcode:
                    groups[text.lower()].append((record["name"], ability["text"]))
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    lines = [f"{opcode}: {sum(len(v) for v in groups.values())} uses, {len(groups)} distinct snippets"]
    for snippet, members in ordered:
        lines.append(f"{len(members):6}  {snippet!r}")
        for name, text in members[:examples]:
            lines.append(f"          - {name}: {text[:130]}")
    return "\n".join(lines)


def diff(before: list[dict], after: list[dict], limit: int = 200) -> tuple[str, int]:
    """What changed between two censuses. Returns the report and how many
    cards got *worse or different*, which is what a reviewer has to look at."""
    old = {r["oracle_id"]: r for r in before}
    lost, gained, changed = [], [], []
    for record in after:
        prior = old.get(record["oracle_id"])
        if prior is None:
            continue
        if prior["status"] == "ok" and record["status"] != "ok":
            lost.append((prior, record))
        elif prior["status"] != "ok" and record["status"] == "ok":
            gained.append(record)
        elif prior["status"] == "ok" and record["status"] == "ok":
            if [a.get("hash") for a in prior["abilities"]] != [
                a.get("hash") for a in record["abilities"]
            ]:
                changed.append((prior, record))
    lines = [
        f"gained {len(gained)} cards, lost {len(lost)}, changed meaning {len(changed)}",
    ]
    if lost:
        lines.append("\nLOST (was fully read, now is not):")
        for prior, record in lost[:limit]:
            lines.append(f"  {record['name']}")
            for ability in record["abilities"]:
                if not ability["ok"]:
                    lines.append(
                        f"      {ability['text'][:100]}  <<{ability.get('remaining', '')[:50]}>>"
                    )
    if changed:
        lines.append("\nCHANGED (read before and after, but to a different IR):")
        for prior, record in changed[:limit]:
            lines.append(f"  {record['name']}")
            for a, b in zip(prior["abilities"], record["abilities"]):
                if a.get("hash") != b.get("hash"):
                    lines.append(f"      text:   {b['text'][:110]}")
                    lines.append(f"      before: {a.get('ir', '')[:160]}")
                    lines.append(f"      after:  {b.get('ir', '')[:160]}")
    return "\n".join(lines), len(lost) + len(changed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pool", choices=POOLS, default="format")
    parser.add_argument("--jobs", type=int, default=0, help="worker processes (default: all cores)")
    parser.add_argument("--limit", type=int, default=0, help="only the first N cards")
    parser.add_argument(
        "--match", help="only cards whose oracle text matches this regex (fast iteration)"
    )
    parser.add_argument("--out", type=Path, help="write the per-card census (.jsonl or .jsonl.gz)")
    parser.add_argument("--load", type=Path, help="report on a saved census instead of parsing")
    parser.add_argument("--baseline", type=Path, help="a saved census to diff against")
    parser.add_argument("--clusters", type=int, default=40, help="how many failure clusters to show")
    parser.add_argument("--words", type=int, default=3, help="words per cluster signature")
    parser.add_argument("--grep", help="show every failing ability whose text matches this regex")
    parser.add_argument("--near", type=int, default=0, help="show N one-ability-short cards")
    parser.add_argument(
        "--opcode",
        help="audit: every read ability that emits this EffectKind, with the snippet"
        " it was read from, grouped by snippet",
    )
    args = parser.parse_args(argv)

    if args.load:
        records = load(args.load)
    else:
        from ..bootstrap import seed_card_database

        seed_card_database()
        started = time.monotonic()
        records = run_census(
            args.pool, jobs=args.jobs or None, limit=args.limit, match=args.match
        )
        print(f"(parsed in {time.monotonic() - started:.1f}s)", file=sys.stderr)
        if args.out:
            save(records, args.out)

    print(summary(records))
    if args.baseline:
        report, _ = diff(load(args.baseline), records)
        print()
        print(report)
    if args.grep:
        pattern = re.compile(args.grep, re.IGNORECASE)
        print()
        for record in records:
            for ability in record["abilities"]:
                if not ability["ok"] and pattern.search(ability["text"]):
                    print(f"  {record['name']}: {ability['text']}")
                    print(f"      [{ability.get('rule')}] <<{ability.get('remaining', '')}>>")
    if args.clusters:
        print()
        print(clusters(records, words=args.words, top=args.clusters))
    if args.near:
        print()
        print(blockers(records, top=args.near))
    if args.opcode:
        print()
        print(opcode_audit(records, args.opcode.upper()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
