"""Scryfall bulk data acquisition.

Scryfall publishes the whole card pool as compressed JSONL and asks clients to
identify themselves and to stay under roughly ten requests a second. We make
very few requests - one index call, one bulk file, a handful of catalogs - and
cache everything keyed on Scryfall's own ``updated_at`` so a re-run downloads
nothing until the pool actually changes.

The catalog endpoints matter more than they look. ``creature-types`` is what
lets the type-line parser know that "Time Lord" is one subtype rather than two,
and ``keyword-abilities``/``keyword-actions`` give us an authoritative list to
check the engine's keyword registry against - so a new set's new keyword shows
up as a named gap instead of silently doing nothing.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import requests

from ..paths import ensure, scryfall_cache

API = "https://api.scryfall.com"

#: Scryfall asks for a descriptive User-Agent and an explicit Accept header.
#: Requests without them are rejected with a 400.
HEADERS = {
    "User-Agent": "mtgfish/0.1 (Commander simulator; local research tool)",
    "Accept": "application/json",
}

#: Scryfall requests 50-100ms between calls. We make single-digit numbers of
#: requests, but throttling is cheap and being a good citizen costs nothing.
_MIN_INTERVAL = 0.1

#: Subtype catalogs, mapped to the card type they belong to (CR 205.3).
SUBTYPE_CATALOGS = {
    "artifact-types": "artifact",
    "battle-types": "battle",
    "creature-types": "creature",
    "enchantment-types": "enchantment",
    "land-types": "land",
    "planeswalker-types": "planeswalker",
    "spell-types": "spell",
}

#: Vocabulary catalogs used to audit engine coverage rather than to drive it.
VOCAB_CATALOGS = ("keyword-abilities", "keyword-actions", "ability-words")


class ScryfallError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BulkEntry:
    kind: str
    updated_at: str
    uri: str
    compressed_size: int

    @property
    def filename(self) -> str:
        # Scryfall's updated_at is an ISO timestamp; flatten it for a filename.
        stamp = self.updated_at.replace(":", "").replace("-", "").replace("+", "_")
        return f"{self.kind}-{stamp}.jsonl.gz"


class ScryfallClient:
    """Fetches and caches Scryfall bulk data and catalogs."""

    def __init__(self, cache_dir: Path | None = None, *, offline: bool = False) -> None:
        self.cache_dir = ensure(cache_dir or scryfall_cache())
        self.offline = offline
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        self._last_request = 0.0

    # -- HTTP ---------------------------------------------------------------

    def _get(self, url: str, *, stream: bool = False) -> requests.Response:
        if self.offline:
            raise ScryfallError(f"offline mode: refusing to fetch {url}")
        wait = _MIN_INTERVAL - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        response = self._session.get(url, stream=stream, timeout=120)
        self._last_request = time.monotonic()
        if response.status_code != 200:
            raise ScryfallError(f"{url} returned HTTP {response.status_code}")
        return response

    # -- bulk data ----------------------------------------------------------

    def bulk_index(self) -> dict[str, BulkEntry]:
        payload = self._get(f"{API}/bulk-data").json()
        return {
            item["type"]: BulkEntry(
                kind=item["type"],
                updated_at=item["updated_at"],
                uri=item["jsonl_download_uri"],
                compressed_size=item.get("compressed_size", 0),
            )
            for item in payload["data"]
        }

    def fetch_bulk(self, kind: str, *, index: dict[str, BulkEntry] | None = None) -> Path:
        """Download bulk data of ``kind``, or reuse the cached copy.

        Returns the path to the gzipped JSONL. Older versions of the same kind
        are pruned so the cache does not grow without bound.
        """
        index = index if index is not None else self.bulk_index()
        try:
            entry = index[kind]
        except KeyError:
            raise ScryfallError(
                f"unknown bulk kind {kind!r}; available: {sorted(index)}"
            ) from None

        target = self.cache_dir / entry.filename
        if target.exists() and target.stat().st_size > 0:
            return target

        partial = target.with_suffix(".part")
        response = self._get(entry.uri, stream=True)
        with open(partial, "wb") as fh:
            for chunk in response.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
        partial.replace(target)

        for stale in self.cache_dir.glob(f"{kind}-*.jsonl.gz"):
            if stale != target:
                stale.unlink(missing_ok=True)
        return target

    def cached_bulk(self, kind: str) -> Path | None:
        """Most recent cached file for ``kind``, for offline use."""
        candidates = sorted(self.cache_dir.glob(f"{kind}-*.jsonl.gz"))
        return candidates[-1] if candidates else None

    # -- catalogs -----------------------------------------------------------

    def catalog(self, name: str) -> list[str]:
        """Fetch a catalog, caching it on disk.

        A missing catalog returns an empty list rather than raising: Scryfall
        occasionally adds them, and an absent ``battle-types`` should not stop
        an ingest.
        """
        cached = self.cache_dir / f"catalog-{name}.json"
        if cached.exists():
            return json.loads(cached.read_text(encoding="utf8"))
        try:
            payload = self._get(f"{API}/catalog/{name}").json()
        except ScryfallError:
            return []
        values = payload.get("data", [])
        cached.write_text(json.dumps(values), encoding="utf8")
        return values

    def all_catalogs(self) -> dict[str, list[str]]:
        names = list(SUBTYPE_CATALOGS) + list(VOCAB_CATALOGS) + ["supertypes", "card-types"]
        return {name: self.catalog(name) for name in names}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def iter_jsonl(path: Path) -> Iterator[dict]:
    """Stream objects from a gzipped JSONL bulk file.

    Streaming rather than ``json.load`` matters: the all-cards dump is over a
    gigabyte decompressed, and holding it in memory alongside the card database
    build is needless.
    """
    with gzip.open(path, "rt", encoding="utf8") as fh:
        for line in fh:
            line = line.strip().rstrip(",")
            if not line or line in ("[", "]"):
                continue
            yield json.loads(line)


def file_hash(path: Path) -> str:
    """SHA-256 of a bulk file, recorded with every simulation run.

    A replay reconstructs a game by re-simulating its seed. If the card pool
    changed underneath it, the reconstruction would silently differ, so runs
    carry the hash of the data they were produced from.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
