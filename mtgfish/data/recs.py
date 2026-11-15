"""Card recommendations for a deck: from EDHREC, or from Scryfall's tags.

Two sources, answering different questions:

**EDHREC** says what other people play with this commander. It is popularity,
not quality, it lags new sets, and it knows nothing about this particular
list - but it is the widest net there is, and "a lot of decks like yours run
this" is a good reason to try a card.

**Similar cards** reads the deck itself. Scryfall's tagger labels every card
with what it does ("mana rock", "gives pp counters", "sweeper-one-sided"), and
a deck's tags, weighted by how unusual each one is, are a fingerprint of what
it is trying to do. Cards outside the deck whose tags overlap that fingerprint
most are recommended. A goal filter ("ramp", "removal") narrows that to cards
that do a particular job.

Legality: only cards legal in Commander, within the commander's colour
identity, and not already in the deck. The one exception is cards not yet
released - they are not legal *yet*, and seeing what is coming is half the
point of a recency filter. Those come from a live Scryfall search, because the
local card snapshot predates them or carries them without a release date.

Release dates also come from Scryfall live: the local snapshot does not store
them, and it is weeks old by design, which is exactly the window a "released
in the last N months" filter is about.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import re
import threading
import time
from dataclasses import dataclass, field

import requests

from .scryfall import API, HEADERS

EDHREC = "https://json.edhrec.com/pages/commanders/{slug}.json"

#: Tags that say nothing about what a card does: wordplay, trivia, set
#: bookkeeping. Kept out of the fingerprint, where their sheer frequency would
#: otherwise make every card look alike.
NOISE_TAGS = frozenset(
    {
        "alliteration", "meme", "unique type line", "namesake spell",
        "virtual vanilla", "french vanilla", "virtual french vanilla",
        "vanilla", "rhystic", "noncreature typal", "intervening if clause",
        "activated ability", "triggered ability", "drawback",
        "single target instant/sorcery", "multiple targets",
        "cheaper than mv", "more expensive than mv", "repeatable crime",
    }
)
_NOISE_PREFIXES = ("cycle-", "art-", "flavor-", "reprint-", "namesake")

#: What a goal filter means in tags. A card qualifies if it has any of the
#: exact tags or any tag starting with one of the prefixes.
GOALS: dict[str, dict] = {
    "ramp": {
        "label": "Ramp",
        "tags": {"ramp", "land ramp", "multi land ramp", "mana rock",
                 "utility mana rock", "mana rock with set's mechanic",
                 "mana dork", "adds multiple mana", "ritual",
                 "tutor-land-to-battlefield"},
        "prefixes": (),
    },
    "draw": {
        "label": "Card draw / looting",
        "tags": {"draw engine", "repeatable pure draw", "pure draw", "burst draw",
                 "repeatable impulsive draw", "long term impulsive draw",
                 "impulsive draw", "loot", "repeatable loot", "card advantage",
                 "cantrip", "wheel"},
        "prefixes": ("loot",),
    },
    "removal": {
        "label": "Removal",
        "tags": {"spot removal", "repeatable removal", "multi removal"},
        "prefixes": ("removal-",),
    },
    "wipes": {
        "label": "Board wipes",
        "tags": {"sweeper", "sweeper-one-sided", "mass removal"},
        "prefixes": ("sweeper",),
    },
    "tutors": {
        "label": "Tutors",
        "tags": set(),
        "prefixes": ("tutor-",),
    },
    "counters": {
        "label": "Counterspells",
        "tags": set(),
        "prefixes": ("counterspell",),
    },
    "protection": {
        "label": "Protection",
        "tags": {"gives protection", "gives hexproof", "gives indestructible",
                 "gives shroud", "gives player protection", "phasing"},
        "prefixes": (),
    },
}


def goal_matches(goal: str | None, tags) -> bool:
    if not goal:
        return True
    spec = GOALS.get(goal)
    if spec is None:
        return True
    prefixes = spec["prefixes"]
    return any(
        tag in spec["tags"] or (prefixes and tag.startswith(prefixes)) for tag in tags
    )


def _noise(tag: str) -> bool:
    return tag in NOISE_TAGS or tag.startswith(_NOISE_PREFIXES)


# ---------------------------------------------------------------------------
# The local tag index
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Card:
    oracle_id: str
    name: str
    identity: frozenset
    commander_legal: bool
    format_legal: bool
    edhrec_rank: int | None
    is_land: bool
    tags: frozenset


@dataclass(slots=True)
class TagIndex:
    """Every card's tags and the facts needed to filter it, held in memory."""

    cards: dict[str, _Card] = field(default_factory=dict)  # by oracle id
    by_name: dict[str, str] = field(default_factory=dict)  # casefolded -> id
    idf: dict[str, float] = field(default_factory=dict)


_INDEX: dict[str, TagIndex] = {}
_INDEX_LOCK = threading.Lock()


def tag_index(db) -> TagIndex:
    """Built once per card snapshot: a full read of the cards and tags tables."""
    key = db.content_hash
    with _INDEX_LOCK:
        if key in _INDEX:
            return _INDEX[key]
        index = TagIndex()
        tags: dict[str, set] = {}
        for oracle_id, tag in db._conn.execute("SELECT oracle_id, tag FROM oracle_tags"):
            tags.setdefault(oracle_id, set()).add(tag)
        for row in db._conn.execute(
            "SELECT oracle_id, name, commander_legal, format_legal, edhrec_rank, data"
            " FROM cards WHERE playable = 1"
        ):
            raw = json.loads(row["data"])
            type_line = (raw.get("type_line") or "").split("//")[0]
            card = _Card(
                oracle_id=row["oracle_id"],
                name=row["name"],
                identity=frozenset(raw.get("color_identity") or ()),
                commander_legal=bool(row["commander_legal"]),
                format_legal=bool(row["format_legal"]),
                edhrec_rank=row["edhrec_rank"],
                is_land="Land" in type_line,
                tags=frozenset(t for t in tags.get(row["oracle_id"], ()) if not _noise(t)),
            )
            index.cards[card.oracle_id] = card
            index.by_name.setdefault(card.name.casefold(), card.oracle_id)
        total = max(1, len(index.cards))
        frequency: dict[str, int] = {}
        for card in index.cards.values():
            for tag in card.tags:
                frequency[tag] = frequency.get(tag, 0) + 1
        index.idf = {tag: math.log(total / count) for tag, count in frequency.items()}
        _INDEX.clear()
        _INDEX[key] = index
        return index


def deck_profile(index: TagIndex, names) -> dict[str, float]:
    """The deck's fingerprint: tag weight = how many of its cards carry it x rarity.

    Lands are left out - every deck runs them and their tags ("tapped land",
    "fetchland") describe the mana base, not the plan.
    """
    counts: dict[str, int] = {}
    for name in names:
        oracle_id = index.by_name.get(name.casefold())
        card = index.cards.get(oracle_id) if oracle_id else None
        if card is None or card.is_land:
            continue
        for tag in card.tags:
            counts[tag] = counts.get(tag, 0) + 1
    # A tag on a single card is that card's quirk, not the deck's theme -
    # unless the deck is tiny.
    floor = 2 if sum(counts.values()) > 40 else 1
    return {
        tag: count * index.idf.get(tag, 0.0)
        for tag, count in counts.items()
        if count >= floor
    }


def similarity(index: TagIndex, profile: dict[str, float], card: _Card) -> tuple[float, list[str]]:
    """How much of the deck's fingerprint a card shares, and which tags did it."""
    shared = [(profile[tag] * index.idf.get(tag, 0.0), tag) for tag in card.tags if tag in profile]
    if not shared:
        return 0.0, []
    shared.sort(reverse=True)
    # Divided by the root of the card's own tag count, so a card tagged with
    # everything does not win by sheer volume.
    score = sum(weight for weight, _ in shared) / math.sqrt(len(card.tags))
    return score, [tag for _, tag in shared[:4]]


# ---------------------------------------------------------------------------
# Live sources
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_LOCK = threading.Lock()
#: How long a live answer is reused. EDHREC's pages and Scryfall's new cards
#: change daily at most; an hour keeps the screen fast while filters change.
CACHE_SECONDS = 3600


def _cached(key: str, fetch):
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = fetch()
    with _CACHE_LOCK:
        _CACHE[key] = (now, value)
    return value


class SourceError(RuntimeError):
    """A live source could not be reached or did not answer usefully."""


def edhrec_slug(name: str) -> str:
    """EDHREC's URL form of a card name: "Atraxa, Praetors' Voice" -> atraxa-praetors-voice."""
    front = name.split(" // ")[0]
    text = front.casefold().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def fetch_edhrec(commanders: list[str]) -> list[dict]:
    """The cards EDHREC lists for these commanders, most synergistic first.

    Partner pairs are tried in both orders, since EDHREC files a pair under
    one of them.
    """
    if not commanders:
        raise SourceError("the deck has no commander, so EDHREC has no page for it")
    slugs = [edhrec_slug(name) for name in commanders]
    candidates = ["-".join(slugs)]
    if len(slugs) == 2:
        candidates.append("-".join(reversed(slugs)))
        candidates.append("-".join(sorted(slugs)))

    def fetch():
        last = ""
        for slug in dict.fromkeys(candidates):
            try:
                reply = requests.get(EDHREC.format(slug=slug), headers=HEADERS, timeout=20)
            except requests.RequestException as exc:
                raise SourceError(f"could not reach EDHREC: {exc}") from None
            if reply.status_code == 404:
                last = slug
                continue
            if reply.status_code != 200:
                raise SourceError(f"EDHREC answered {reply.status_code}")
            try:
                data = reply.json()
                lists = data["container"]["json_dict"]["cardlists"]
            except (ValueError, KeyError, TypeError):
                raise SourceError("EDHREC's page was not in the expected shape") from None
            out: dict[str, dict] = {}
            for section in lists:
                for view in section.get("cardviews", []):
                    name = view.get("name")
                    if not name or name in out:
                        continue
                    decks = view.get("num_decks") or 0
                    potential = view.get("potential_decks") or 0
                    out[name] = {
                        "name": name,
                        "section": section.get("header", ""),
                        "synergy": view.get("synergy") or 0.0,
                        "num_decks": decks,
                        "inclusion": decks / potential if potential else 0.0,
                    }
            return list(out.values())
        raise SourceError(f"EDHREC has no page for {' + '.join(commanders)} ({last})")

    return _cached("edhrec:" + "|".join(candidates), fetch)


def _scryfall_search(query: str, *, pages: int = 6) -> list[dict]:
    """Every card a Scryfall search returns, up to ``pages`` pages of 175."""

    def fetch():
        url = f"{API}/cards/search"
        params = {"q": query, "order": "edhrec", "unique": "cards"}
        out: list[dict] = []
        for _ in range(pages):
            try:
                reply = requests.get(url, params=params, headers=HEADERS, timeout=20)
            except requests.RequestException as exc:
                raise SourceError(f"could not reach Scryfall: {exc}") from None
            if reply.status_code == 404:
                break  # "no cards found" is a 404 with a body, not an error
            if reply.status_code != 200:
                raise SourceError(f"Scryfall answered {reply.status_code}")
            body = reply.json()
            out += body.get("data", [])
            if not body.get("has_more"):
                break
            url, params = body["next_page"], None
            time.sleep(0.1)  # Scryfall asks for 50-100ms between requests
        return out

    return _cached("scryfall:" + query, fetch)


def _identity_query(identity) -> str:
    letters = "".join(c for c in "WUBRG" if c in identity)
    return f"id<={letters or 'C'}"


def months_ago(months: int, today: _dt.date | None = None) -> _dt.date:
    today = today or _dt.date.today()
    month = today.month - months
    year = today.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(today.day, 28)
    return _dt.date(year, month, day)


def recent_cards(identity, since: _dt.date | None, today: _dt.date | None = None) -> list[dict]:
    """Cards first printed since ``since`` (or not yet released), legal or upcoming.

    ``since=None`` asks only for the upcoming ones.
    """
    today = today or _dt.date.today()
    window = f"date>={since.isoformat()}" if since else f"date>{today.isoformat()}"
    query = (
        f"not:reprint {window} {_identity_query(identity)} -t:basic "
        f"(legal:commander or date>{today.isoformat()}) game:paper"
    )
    try:
        return _scryfall_search(query)
    except SourceError:
        # Unreleased cards are often not marked game:paper yet.
        return _scryfall_search(query.replace(" game:paper", ""))


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Rec:
    name: str
    score: float = 0.0
    reason: str = ""
    tags: list[str] = field(default_factory=list)
    edhrec_rank: int | None = None
    synergy: float | None = None
    inclusion: float | None = None
    on_edhrec: bool = False
    released_at: str = ""
    upcoming: bool = False
    #: The Scryfall object, for cards the local snapshot does not have.
    live: dict | None = None


def recommend(
    db,
    *,
    deck_names: list[str],
    commanders: list[str],
    identity,
    mode: str = "edhrec",
    goal: str | None = None,
    months: int | None = None,
    include_lands: bool = False,
    sort: str = "relevance",
    limit: int = 60,
    today: _dt.date | None = None,
) -> tuple[list[Rec], list[str]]:
    """Recommendations, best first, and notes on anything that went wrong.

    A failed live source is a note, not an exception: similar-cards mode
    works offline, and EDHREC being down should say so rather than empty the
    screen without explanation.
    """
    index = tag_index(db)
    notes: list[str] = []
    today = today or _dt.date.today()
    in_deck = {name.casefold() for name in deck_names} | {n.casefold() for n in commanders}
    identity = frozenset(identity)

    edhrec: dict[str, dict] = {}
    if commanders:
        try:
            edhrec = {row["name"]: row for row in fetch_edhrec(commanders)}
        except SourceError as exc:
            notes.append(str(exc))
    elif mode == "edhrec":
        notes.append("the deck has no commander, so there is no EDHREC page to read")

    # Recent and upcoming cards, by name, with their release dates.
    live: dict[str, dict] = {}
    try:
        for raw in recent_cards(identity, months_ago(months, today) if months else None, today):
            live[raw["name"]] = raw
    except SourceError as exc:
        notes.append(f"{exc} - recency filter and upcoming cards unavailable")
        if months:
            return [], notes

    def local(name: str) -> _Card | None:
        oracle_id = index.by_name.get(name.casefold())
        return index.cards.get(oracle_id) if oracle_id else None

    def upcoming(raw: dict | None) -> bool:
        return bool(raw) and (raw.get("released_at") or "") > today.isoformat()

    def allowed(name: str) -> bool:
        if name.casefold() in in_deck:
            return False
        card = local(name)
        raw = live.get(name)
        if months and raw is None:
            return False
        if card is not None:
            if not card.identity <= identity:
                return False
            if card.is_land and not include_lands:
                return False
            if not card.commander_legal and not upcoming(raw):
                return False
            if goal and not goal_matches(goal, card.tags):
                return False
            return True
        if raw is None:
            return False
        # Known only to Scryfall: newer than the snapshot. Its legality came
        # from the search itself; it has no local tags for a goal filter.
        if "Land" in (raw.get("type_line") or "") and not include_lands:
            return False
        return not goal
    recs: list[Rec] = []
    profile = deck_profile(index, deck_names)

    if mode == "edhrec":
        for name, row in edhrec.items():
            if not allowed(name):
                continue
            card = local(name)
            raw = live.get(name)
            recs.append(
                Rec(
                    name=name,
                    score=row["synergy"],
                    reason=f"{row['inclusion'] * 100:.0f}% of {row['section'].lower() or 'decks'}"
                    f" with this commander, synergy {row['synergy'] * 100:+.0f}%",
                    edhrec_rank=card.edhrec_rank if card else None,
                    synergy=row["synergy"],
                    inclusion=row["inclusion"],
                    on_edhrec=True,
                    released_at=(raw or {}).get("released_at", ""),
                    upcoming=upcoming(raw),
                    live=raw if card is None else None,
                    tags=sorted(card.tags & profile.keys())[:4] if card else [],
                )
            )
    else:
        if not profile:
            notes.append("the deck's cards carry no tags to match against")
        pool = (
            [local(name) or name for name in live]  # recency: only the new cards
            if months
            else [card for card in index.cards.values()]
        )
        if not months:
            # Upcoming cards join the full pool too: not legal yet, but worth seeing.
            pool += [local(name) or name for name, raw in live.items() if upcoming(raw)]
        seen: set[str] = set()
        for entry in pool:
            name = entry if isinstance(entry, str) else entry.name
            if name in seen or not allowed(name):
                continue
            seen.add(name)
            raw = live.get(name)
            if isinstance(entry, str):
                recs.append(
                    Rec(
                        name=name,
                        reason="new - not in the local card snapshot yet, so no tags to compare",
                        released_at=(raw or {}).get("released_at", ""),
                        upcoming=upcoming(raw),
                        on_edhrec=name in edhrec,
                        live=raw,
                        edhrec_rank=(raw or {}).get("edhrec_rank"),
                    )
                )
                continue
            score, tags = similarity(index, profile, entry)
            if score <= 0:
                continue
            recs.append(
                Rec(
                    name=name,
                    score=score,
                    reason="shares " + ", ".join(tags),
                    tags=tags,
                    edhrec_rank=entry.edhrec_rank,
                    on_edhrec=name in edhrec,
                    synergy=edhrec.get(name, {}).get("synergy"),
                    inclusion=edhrec.get(name, {}).get("inclusion"),
                    released_at=(raw or {}).get("released_at", ""),
                    upcoming=upcoming(raw),
                )
            )

    if sort == "edhrec":
        recs.sort(key=lambda r: (r.edhrec_rank is None, r.edhrec_rank or 0, -r.score))
    elif sort == "newest":
        recs.sort(key=lambda r: r.released_at or "", reverse=True)
    else:
        recs.sort(key=lambda r: -r.score)
    return recs[:limit], notes
