"""Local card database: a SQLite snapshot of the Scryfall card pool.

Everything downstream reads from here rather than the network, which is what
makes a simulation run reproducible. The snapshot's content hash is recorded
with every run so a replay can tell whether it is still looking at the same
card pool.

Scryfall's card objects carry a lot we will never use - prices, image URLs,
purchase links, artist credits. Those are pruned on the way in. It matters more
than tidiness: the pruned database is a shipped asset inside the executable.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from difflib import get_close_matches
from pathlib import Path
from typing import Iterator

from ..paths import card_db_path, ensure
from ..rules.cr205_typeline import SubtypeRegistry, TypeLine, install_registry
from ..rules.enums import CARD_TYPE_NAMES, CardType
from .cards import CardDef
from .scryfall import SUBTYPE_CATALOGS, ScryfallClient, file_hash, iter_jsonl

#: Bumped when the stored shape changes, forcing a rebuild rather than letting
#: a stale database produce subtly wrong cards.
SCHEMA_VERSION = 2

#: Layouts that are not real, castable cards. They stay in the database - token
#: definitions are needed for "create a 1/1 Soldier" - but they must never win a
#: name lookup. Scryfall's art-series entry for Delver of Secrets carries the
#: face name "Delver of Secrets" just as the real card does, and resolving to
#: the art card would put a blank object in someone's deck.
NON_PLAYABLE_LAYOUTS = frozenset(
    {
        "art_series",
        "token",
        "double_faced_token",
        "emblem",
        "planar",
        "scheme",
        "vanguard",
    }
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    oracle_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    commander_legal INTEGER NOT NULL,
    playable        INTEGER NOT NULL,
    edhrec_rank     INTEGER,
    data            TEXT NOT NULL
);

-- One row per name a card can be found by: its full name, and each face name
-- separately so "Fire" resolves to "Fire // Ice".
CREATE TABLE IF NOT EXISTS card_names (
    name_key  TEXT NOT NULL,
    oracle_id TEXT NOT NULL,
    is_full   INTEGER NOT NULL,
    PRIMARY KEY (name_key, oracle_id)
);
CREATE INDEX IF NOT EXISTS ix_card_names_key ON card_names(name_key);

CREATE TABLE IF NOT EXISTS rulings (
    oracle_id    TEXT NOT NULL,
    published_at TEXT,
    comment      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rulings_oracle ON rulings(oracle_id);

CREATE TABLE IF NOT EXISTS subtypes (
    card_type TEXT NOT NULL,
    subtype   TEXT NOT NULL,
    PRIMARY KEY (card_type, subtype)
);

-- Scryfall's tagger data, used only to cross-check the parser. It never drives
-- card behaviour: if the tagger says "removal" and our IR has no removal
-- effect, that is a review flag, not a licence to invent one.
CREATE TABLE IF NOT EXISTS oracle_tags (
    oracle_id TEXT NOT NULL,
    tag       TEXT NOT NULL,
    PRIMARY KEY (oracle_id, tag)
);

-- Authoritative vocabulary lists (keyword abilities, keyword actions, ability
-- words) used to audit engine coverage.
CREATE TABLE IF NOT EXISTS vocabulary (
    catalog TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (catalog, value)
);
"""

#: Card-level fields worth keeping. Everything else is presentation or commerce.
_KEEP_CARD = frozenset(
    {
        "oracle_id",
        "id",
        "name",
        "layout",
        "mana_cost",
        "cmc",
        "type_line",
        "oracle_text",
        "power",
        "toughness",
        "loyalty",
        "defense",
        "colors",
        "color_indicator",
        "color_identity",
        "keywords",
        "produced_mana",
        "card_faces",
        "all_parts",
        "edhrec_rank",
        "scryfall_uri",
    }
)

_KEEP_FACE = frozenset(
    {
        "name",
        "mana_cost",
        "type_line",
        "oracle_text",
        "power",
        "toughness",
        "loyalty",
        "defense",
        "colors",
        "color_indicator",
        "produced_mana",
    }
)

#: Ligatures that Unicode normalisation leaves alone but players type out in
#: full ("Aether Vial" for "AEther Vial").
_LIGATURES = str.maketrans({"Æ": "AE", "æ": "ae", "Œ": "OE", "œ": "oe", "ß": "ss"})

_PUNCTUATION = str.maketrans(
    {"’": "'", "‘": "'", "`": "'", "“": '"', "”": '"', "—": "-", "–": "-", " ": " "}
)


def normalize_name(name: str) -> str:
    """Fold a card name to a stable lookup key.

    Handles the three things that actually break pasted decklists: curly
    apostrophes from word processors, accented letters typed without accents,
    and inconsistent whitespace.
    """
    text = name.translate(_PUNCTUATION).translate(_LIGATURES)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().split())


class CardDatabase:
    """Read access to the card snapshot."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or card_db_path()
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._cache: dict[str, CardDef] = {}
        #: Cards this build of the engine cannot represent, oracle id -> why.
        #: They are illegal rather than fatal: see ``by_oracle_id``.
        self.unrepresentable: dict[str, str] = {}
        self._names: dict[str, list[tuple[str, bool]]] | None = None
        self._registry: SubtypeRegistry | None = None
        #: Read once, here, on the thread that owns the connection. It is a
        #: property of the file and never changes, and it was the one piece of
        #: the database that other threads had a reason to ask for - which is
        #: how the swap lab came to query sqlite from a worker thread and fail
        #: before it had played a game.
        self._content_hash = self.meta("oracle_hash", "") or ""

    # -- metadata -----------------------------------------------------------

    def meta(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    @property
    def content_hash(self) -> str:
        """Hash of the source bulk file this snapshot was built from.

        Served from the value read at construction, so this is safe to touch
        from any thread - which matters because it is the one thing about the
        database that the UI passes across a thread boundary.
        """
        return self._content_hash

    @property
    def card_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]

    # -- subtype registry ---------------------------------------------------

    def registry(self) -> SubtypeRegistry:
        """The subtype registry for this snapshot.

        Loaded once and installed as the process default, so type-line parsing
        everywhere agrees on what a subtype is.
        """
        if self._registry is None:
            registry = SubtypeRegistry()
            rows = self._conn.execute("SELECT card_type, subtype FROM subtypes")
            grouped: dict[CardType, list[str]] = {}
            for row in rows:
                card_type = CARD_TYPE_NAMES.get(row["card_type"])
                if card_type is not None:
                    grouped.setdefault(card_type, []).append(row["subtype"])
            for card_type, names in grouped.items():
                registry.register(card_type, names)
            self._registry = registry
            install_registry(registry)
        return self._registry

    def vocabulary(self, catalog: str) -> list[str]:
        """An authoritative catalog, e.g. ``keyword-abilities``."""
        rows = self._conn.execute(
            "SELECT value FROM vocabulary WHERE catalog = ? ORDER BY value", (catalog,)
        )
        return [row["value"] for row in rows]

    # -- lookup -------------------------------------------------------------

    def _name_index(self) -> dict[str, list[tuple[int, int, int, str]]]:
        """Name key -> candidate cards, best first.

        Each entry sorts as (not playable, not full-name match, not
        Commander-legal, oracle_id): a real card always beats an art-series or
        token entry sharing the same face name, and a whole-card name beats a
        half-card name.
        """
        if self._names is None:
            index: dict[str, list[tuple[int, int, int, str]]] = {}
            rows = self._conn.execute(
                "SELECT n.name_key, n.oracle_id, n.is_full, c.playable, c.commander_legal"
                " FROM card_names n JOIN cards c ON c.oracle_id = n.oracle_id"
            )
            for row in rows:
                index.setdefault(row["name_key"], []).append(
                    (
                        0 if row["playable"] else 1,
                        0 if row["is_full"] else 1,
                        0 if row["commander_legal"] else 1,
                        row["oracle_id"],
                    )
                )
            for entries in index.values():
                entries.sort()
            self._names = index
        return self._names

    def by_oracle_id(self, oracle_id: str) -> CardDef | None:
        """The card, or None if this build of the engine cannot represent it.

        A card carrying something the engine has no model for - a mana symbol
        from a set newer than the parser, say - is treated as *illegal*: it is
        not in the pool, so no deck can contain it and no sweep can trip over
        it. One such card used to raise out of the middle of an iteration and
        abort the whole pass, which made every full-pool report impossible to
        run rather than one card short.

        Illegal, not silent. Every exclusion is recorded in
        ``unrepresentable`` with the reason, so a report can say what the
        engine is not modelling and a new symbol still surfaces.
        """
        cached = self._cache.get(oracle_id)
        if cached is not None:
            return cached
        if oracle_id in self.unrepresentable:
            return None
        row = self._conn.execute(
            "SELECT data FROM cards WHERE oracle_id = ?", (oracle_id,)
        ).fetchone()
        if row is None:
            return None
        try:
            card = CardDef.from_scryfall(json.loads(row["data"]), self.registry())
        except ValueError as exc:
            # UnknownManaSymbol and its kin. Narrow on purpose: a bug in the
            # engine should still crash, and only a card the engine cannot
            # express is quietly excluded.
            self.unrepresentable[oracle_id] = str(exc)
            return None
        self._cache[oracle_id] = card
        return card

    def raw_card(self, oracle_id: str) -> dict | None:
        """The stored (pruned) Scryfall object - for the printing id, mostly.

        ``CardDef`` deliberately drops presentation fields; the Deck tab needs
        one of them to build an image URL without calling the Scryfall API.
        """
        row = self._conn.execute(
            "SELECT data FROM cards WHERE oracle_id = ?", (oracle_id,)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def lookup(self, name: str) -> CardDef | None:
        """Find a card by any of its names.

        "Fire" resolves to "Fire // Ice", but an actual card named "Fire" would
        take precedence. See ``_name_index`` for the full priority order.
        """
        entries = self._name_index().get(normalize_name(name))
        if not entries:
            return None
        return self.by_oracle_id(entries[0][3])

    def suggest(self, name: str, limit: int = 3) -> list[str]:
        """Close matches for a name that did not resolve.

        Used to tell the user "did you mean" instead of silently guessing - a
        wrong guess would quietly change their deck.
        """
        keys = get_close_matches(
            normalize_name(name), self._name_index().keys(), n=limit, cutoff=0.75
        )
        out: list[str] = []
        for key in keys:
            card = self.by_oracle_id(self._name_index()[key][0][3])
            if card is not None and card.name not in out:
                out.append(card.name)
        return out

    def search_names(self, text: str, limit: int = 20) -> list[str]:
        """Playable card names containing ``text``, most-played first.

        For type-ahead. ``suggest`` answers "did you mean" for a name that did
        not resolve, and its fuzzy match is both too slow for every keystroke
        and wrong for a prefix: "lightning b" is not close to "Lightning Bolt"
        by edit distance, but it is plainly what the person is typing.
        """
        needle = " ".join(text.split())
        if len(needle) < 2:
            return []
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self._conn.execute(
            "SELECT name FROM cards WHERE playable = 1 AND name LIKE ? ESCAPE '\\'"
            " ORDER BY (name LIKE ? ESCAPE '\\') DESC, edhrec_rank IS NULL, edhrec_rank, name"
            " LIMIT ?",
            (f"%{escaped}%", f"{escaped}%", int(limit)),
        )
        return [row["name"] for row in rows]

    def iter_cards(self, *, commander_legal_only: bool = False) -> Iterator[CardDef]:
        sql = "SELECT oracle_id FROM cards"
        if commander_legal_only:
            sql += " WHERE commander_legal = 1"
        sql += " ORDER BY oracle_id"
        for row in self._conn.execute(sql):
            card = self.by_oracle_id(row["oracle_id"])
            if card is not None:
                yield card

    def iter_by_play_rate(
        self, *, limit: int | None = None, commander_legal_only: bool = True
    ) -> Iterator[CardDef]:
        """Cards in descending order of how often they are actually played.

        Scryfall carries EDHREC's rank on every card, so this needs no
        scraping and no second data source. It matters because coverage over
        the whole pool answers the wrong question: the pool is mostly cards
        nobody plays, and a percentage that treats a staple and a draft
        common as equally important cannot tell you whether a real deck will
        simulate correctly.

        Cards with no rank are excluded rather than sorted last - an unranked
        card is one EDHREC has never seen played.
        """
        sql = "SELECT oracle_id FROM cards WHERE edhrec_rank IS NOT NULL"
        if commander_legal_only:
            sql += " AND commander_legal = 1"
        sql += " ORDER BY edhrec_rank"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        for row in self._conn.execute(sql):
            card = self.by_oracle_id(row["oracle_id"])
            if card is not None:
                yield card

    def play_rank(self, oracle_id: str) -> int | None:
        row = self._conn.execute(
            "SELECT edhrec_rank FROM cards WHERE oracle_id = ?", (oracle_id,)
        ).fetchone()
        return row["edhrec_rank"] if row else None

    def rulings(self, oracle_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT comment FROM rulings WHERE oracle_id = ? ORDER BY rowid", (oracle_id,)
        )
        return [row["comment"] for row in rows]

    def oracle_tags(self, oracle_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT tag FROM oracle_tags WHERE oracle_id = ? ORDER BY tag", (oracle_id,)
        )
        return [row["tag"] for row in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> CardDatabase:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def _type_lines(card: dict) -> Iterator[str]:
    """Every type line on a card, front and back."""
    top = card.get("type_line")
    if top and "//" not in top:
        yield top
    for face in card.get("card_faces") or ():
        line = face.get("type_line")
        if line:
            yield line


def _prune(card: dict) -> dict:
    out = {k: v for k, v in card.items() if k in _KEEP_CARD}
    faces = out.get("card_faces")
    if faces:
        out["card_faces"] = [{k: v for k, v in f.items() if k in _KEEP_FACE} for f in faces]
    # Keep just the one legality we care about. Dropping the whole block would
    # leave CardDef unable to tell whether it is Commander-legal.
    out["legalities"] = {"commander": (card.get("legalities") or {}).get("commander")}
    return out


def _bulk(client: ScryfallClient, kind: str, index: dict, offline: bool) -> Path | None:
    """The dump for ``kind``: downloaded, or the newest already in the cache."""
    if offline:
        return client.cached_bulk(kind)
    return client.fetch_bulk(kind, index=index)


def build_database(
    path: Path | None = None,
    client: ScryfallClient | None = None,
    *,
    include_rulings: bool = True,
    include_tags: bool = True,
    progress=None,
) -> Path:
    """Download Scryfall bulk data and build the local card snapshot.

    Rebuilds from scratch every time. Incremental updates are not worth the
    correctness risk for a file that takes under a minute to regenerate.
    """
    path = path or card_db_path()
    ensure(path.parent)
    client = client or ScryfallClient()

    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    # Offline builds the snapshot from whatever dumps are already in the cache.
    # The dumps are in the repository and the database is not - it is 95 MB of
    # derived data - so a fresh clone with no network can still get a card pool.
    offline = bool(getattr(client, "offline", False))
    if offline:
        report("Offline: building from the cached Scryfall dumps")
        index: dict = {}
    else:
        report("Fetching Scryfall bulk index")
        index = client.bulk_index()

    report("Reading oracle cards" if offline else "Downloading oracle cards")
    oracle_path = _bulk(client, "oracle_cards", index, offline)
    if oracle_path is None:
        raise FileNotFoundError(
            f"no cached oracle_cards dump in {client.cache_dir}. Run without "
            "--offline once to download it."
        )
    oracle_hash = file_hash(oracle_path)

    report("Fetching catalogs")
    catalogs = client.all_catalogs()

    tmp = path.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")

        # Subtype catalogs first: the type-line parser needs them to split
        # multi-word subtypes correctly while inserting the cards themselves.
        registry = SubtypeRegistry()
        for catalog_name, type_word in SUBTYPE_CATALOGS.items():
            values = catalogs.get(catalog_name, [])
            conn.executemany(
                "INSERT OR IGNORE INTO subtypes(card_type, subtype) VALUES (?, ?)",
                [(type_word, v) for v in values],
            )
            card_type = CARD_TYPE_NAMES.get(type_word)
            if card_type is not None:
                registry.register(card_type, values)
        install_registry(registry)

        for catalog_name, values in catalogs.items():
            conn.executemany(
                "INSERT OR IGNORE INTO vocabulary(catalog, value) VALUES (?, ?)",
                [(catalog_name, v) for v in values],
            )

        report("Indexing cards")
        card_rows: list[tuple] = []
        name_rows: list[tuple] = []
        # An unknown type word on a Commander-legal card is a genuine engine
        # gap, not a curiosity. Collected here and surfaced by the build tool.
        unknown_types: dict[str, list[str]] = {}
        seen = 0
        for raw in iter_jsonl(oracle_path):
            oracle_id = raw.get("oracle_id") or raw.get("id")
            if not oracle_id:
                continue
            legalities = raw.get("legalities", {})
            commander_legal = 1 if legalities.get("commander") == "legal" else 0
            if commander_legal:
                for type_line in _type_lines(raw):
                    _, unknown = TypeLine.scan(type_line, registry)
                    for word in unknown:
                        unknown_types.setdefault(word, []).append(raw.get("name", ""))
            card_rows.append(
                (
                    oracle_id,
                    raw.get("name", ""),
                    commander_legal,
                    0 if raw.get("layout") in NON_PLAYABLE_LAYOUTS else 1,
                    raw.get("edhrec_rank"),
                    json.dumps(_prune(raw), separators=(",", ":")),
                )
            )

            full_name = raw.get("name", "")
            name_rows.append((normalize_name(full_name), oracle_id, 1))
            for face in raw.get("card_faces") or ():
                face_name = face.get("name")
                if face_name and face_name != full_name:
                    name_rows.append((normalize_name(face_name), oracle_id, 0))
            # "Fire // Ice" should also be findable as "Fire" and "Ice" even for
            # layouts Scryfall does not give card_faces for.
            if "//" in full_name:
                for part in full_name.split("//"):
                    part = part.strip()
                    if part:
                        name_rows.append((normalize_name(part), oracle_id, 0))

            seen += 1
            if seen % 5000 == 0:
                report(f"Indexing cards ({seen})")

        conn.executemany(
            "INSERT OR REPLACE INTO cards"
            "(oracle_id, name, commander_legal, playable, edhrec_rank, data)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            card_rows,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO card_names(name_key, oracle_id, is_full) VALUES (?, ?, ?)",
            name_rows,
        )

        rulings_path = _bulk(client, "rulings", index, offline) if include_rulings else None
        if include_rulings and rulings_path is None:
            report("No cached rulings dump; skipping")
        if rulings_path is not None:
            report("Reading rulings" if offline else "Downloading rulings")
            conn.executemany(
                "INSERT INTO rulings(oracle_id, published_at, comment) VALUES (?, ?, ?)",
                (
                    (r.get("oracle_id", ""), r.get("published_at"), r.get("comment", ""))
                    for r in iter_jsonl(rulings_path)
                    if r.get("oracle_id")
                ),
            )

        if include_tags:
            report("Reading oracle tags" if offline else "Downloading oracle tags")
            try:
                tags_path = _bulk(client, "oracle_tags", index, offline)
                if tags_path is None:
                    raise FileNotFoundError("no cached oracle_tags dump")
                conn.executemany(
                    "INSERT OR IGNORE INTO oracle_tags(oracle_id, tag) VALUES (?, ?)",
                    _iter_tag_rows(tags_path),
                )
            except Exception as exc:  # noqa: BLE001 - tags are a nicety, not a dependency
                report(f"Skipping oracle tags: {exc}")

        conn.executemany(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            [
                ("schema_version", str(SCHEMA_VERSION)),
                ("oracle_hash", oracle_hash),
                ("oracle_file", oracle_path.name),
                ("card_count", str(len(card_rows))),
                (
                    "unknown_type_words",
                    json.dumps({k: v[:5] for k, v in sorted(unknown_types.items())}),
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    path.unlink(missing_ok=True)
    tmp.replace(path)
    report(f"Card database ready: {path}")
    return path


def _iter_tag_rows(path: Path) -> Iterator[tuple[str, str]]:
    """Flatten the tagger dump into (oracle_id, tag) pairs.

    Each entry is one tag carrying a ``taggings`` list of the cards that have
    it, so the dump is inverted relative to how we want to query it.
    """
    for entry in iter_jsonl(path):
        label = entry.get("label")
        if not label:
            continue
        for tagging in entry.get("taggings") or ():
            oracle_id = tagging.get("oracle_id") if isinstance(tagging, dict) else tagging
            if oracle_id:
                yield (oracle_id, label)
