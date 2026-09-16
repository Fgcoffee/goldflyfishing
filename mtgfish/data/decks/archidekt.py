"""Archidekt deck import.

Archidekt exposes a public read API at ``/api/decks/{id}/`` that returns the
whole deck in one call, including each card's Scryfall oracle id and its
category assignments. The commander is the card in the "Commander" category.

Moxfield deliberately has no counterpart: their API rejects unapproved clients
with a 403, so Moxfield decks come in through the paste box using their own
Export output.
"""

from __future__ import annotations

import re

import requests

from ..db import CardDatabase
from .model import Deck, DeckEntry, DeckIssue, Severity, validate

_DECK_ID_RE = re.compile(r"archidekt\.com/(?:api/)?decks/(\d+)")
_BARE_ID_RE = re.compile(r"^\d+$")

API_TEMPLATE = "https://archidekt.com/api/decks/{deck_id}/"

HEADERS = {
    "User-Agent": "mtgfish/0.1 (Commander simulator; local research tool)",
    "Accept": "application/json",
}

#: Category names that mean "not actually in the deck". Archidekt also flags
#: this per-category with ``includedInDeck``, which we honour first; these are
#: the fallback for decks that predate that flag.
_EXCLUDED_CATEGORIES = {"maybeboard", "sideboard", "considering", "tokens", "token"}

COMMANDER_CATEGORY = "commander"


class ArchidektError(RuntimeError):
    pass


def is_archidekt_url(text: str) -> bool:
    return bool(_DECK_ID_RE.search(text))


def extract_deck_id(text: str) -> str | None:
    """Pull a deck id out of any Archidekt URL form, or a bare numeric id."""
    match = _DECK_ID_RE.search(text)
    if match:
        return match.group(1)
    stripped = text.strip()
    return stripped if _BARE_ID_RE.match(stripped) else None


def fetch_archidekt(
    url_or_id: str,
    db: CardDatabase,
    *,
    session: requests.Session | None = None,
    token: str | None = None,
) -> Deck:
    """Fetch and parse an Archidekt deck.

    ``token`` is an access token from ``archidekt_login``; with it a private
    deck belonging to that account can be read.
    """
    deck_id = extract_deck_id(url_or_id)
    if deck_id is None:
        raise ArchidektError(f"not an Archidekt deck URL or id: {url_or_id!r}")

    http = session or requests.Session()
    response = _get(http, API_TEMPLATE.format(deck_id=deck_id), token, timeout=45)
    if response.status_code == 404:
        raise ArchidektError(f"Archidekt deck {deck_id} not found (it may be private).")
    if response.status_code != 200:
        raise ArchidektError(f"Archidekt returned HTTP {response.status_code} for deck {deck_id}")

    return parse_archidekt_payload(response.json(), db, source=f"archidekt:{deck_id}")


LOGIN_URL = "https://archidekt.com/api/rest-auth/login/"
DECK_LIST_URL = "https://archidekt.com/api/decks/v3/"

#: Archidekt's ``deckFormat`` numbers, for labelling a deck list.
DECK_FORMATS = {
    1: "Standard", 2: "Modern", 3: "Commander", 4: "Legacy", 5: "Vintage",
    6: "Pauper", 7: "Custom", 8: "Frontier", 9: "Future Standard",
    10: "Penny Dreadful", 11: "1v1 Commander", 12: "Duel Commander", 13: "Brawl",
    14: "Oathbreaker", 15: "Pioneer", 16: "Historic", 17: "Pauper Commander",
    18: "Alchemy", 19: "Explorer", 20: "Historic Brawl", 21: "Gladiator",
    22: "Premodern", 23: "PreDH", 24: "Timeless", 25: "Canadian Highlander",
}


def _get(http: requests.Session, url: str, token: str | None, **kwargs) -> requests.Response:
    """GET with the sign-in token, if there is one.

    Archidekt's API is unpublished, so which header scheme it wants is
    observed rather than documented: its site sends ``JWT <token>``. ``Bearer``
    is tried if that is refused, so a change on their side degrades to one
    extra request rather than to "your private decks vanished".
    """
    if not token:
        return http.get(url, headers=HEADERS, **kwargs)
    response = None
    for scheme in ("JWT", "Bearer"):
        response = http.get(url, headers={**HEADERS, "Authorization": f"{scheme} {token}"}, **kwargs)
        if response.status_code not in (401, 403):
            return response
    return response


def archidekt_login(
    username_or_email: str, password: str, *, session: requests.Session | None = None
) -> dict:
    """Sign in to Archidekt and return ``{"token", "username", "user_id"}``.

    Archidekt has no OAuth and no published API; this is the endpoint its own
    site signs in with (a django-rest-auth login taking ``username`` or
    ``email`` plus ``password``). The password is sent to Archidekt and not
    kept anywhere - only the token that comes back is.
    """
    field = "email" if "@" in username_or_email else "username"
    http = session or requests.Session()
    response = http.post(
        LOGIN_URL,
        json={field: username_or_email.strip(), "password": password},
        headers=HEADERS,
        timeout=30,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code != 200:
        reasons = payload.get("non_field_errors") or payload.get("detail") or []
        if isinstance(reasons, str):
            reasons = [reasons]
        raise ArchidektError(
            " ".join(reasons) or f"Archidekt refused the sign-in (HTTP {response.status_code})"
        )

    token = payload.get("access_token") or payload.get("token") or payload.get("key")
    user = payload.get("user") or {}
    if not token:
        raise ArchidektError("Archidekt accepted the sign-in but returned no token")
    return {
        "token": token,
        "username": user.get("username") or (username_or_email if field == "username" else ""),
        "user_id": user.get("id") or user.get("pk"),
    }


def list_archidekt_decks(
    username: str,
    *,
    token: str | None = None,
    page: int = 1,
    session: requests.Session | None = None,
) -> dict:
    """One page of a user's decks, newest first.

    Without a token this is whatever the user has made public; with their own
    token Archidekt includes their private and unlisted decks too.
    """
    http = session or requests.Session()
    response = _get(
        http,
        DECK_LIST_URL,
        token,
        params={"ownerUsername": username, "orderBy": "-updatedAt", "page": page, "pageSize": 50},
        timeout=30,
    )
    if response.status_code != 200:
        raise ArchidektError(f"Archidekt returned HTTP {response.status_code} listing decks")
    payload = response.json()
    decks = []
    for raw in payload.get("results") or ():
        colors = raw.get("colors") or {}
        decks.append(
            {
                "id": raw.get("id"),
                "name": raw.get("name") or "(untitled)",
                "size": raw.get("size"),
                "format": DECK_FORMATS.get(raw.get("deckFormat"), ""),
                "updated": raw.get("updatedAt"),
                "private": bool(raw.get("private")),
                "unlisted": bool(raw.get("unlisted")),
                "colors": "".join(c for c in "WUBRG" if colors.get(c)),
                "url": f"https://archidekt.com/decks/{raw.get('id')}",
            }
        )
    return {"count": payload.get("count", len(decks)), "next": bool(payload.get("next")), "decks": decks}


def parse_archidekt_payload(
    payload: dict, db: CardDatabase, *, source: str = "archidekt"
) -> Deck:
    """Convert an Archidekt API response into a validated Deck."""
    excluded = _excluded_category_names(payload)

    commanders: list = []
    entries: list[DeckEntry] = []
    unresolved: list[str] = []
    issues: list[DeckIssue] = []

    for raw in payload.get("cards") or ():
        quantity = int(raw.get("quantity") or 0)
        if quantity <= 0:
            continue

        categories = [c.lower() for c in (raw.get("categories") or [])]
        if any(c in excluded for c in categories):
            continue

        card_data = raw.get("card") or {}
        oracle = card_data.get("oracleCard") or {}
        name = oracle.get("name") or card_data.get("displayName") or ""

        # The oracle uid is Scryfall's oracle_id, which is what our snapshot is
        # keyed on. Falling back to the name covers cards Archidekt knows about
        # that our snapshot predates.
        card = db.by_oracle_id(oracle.get("uid") or "") or (db.lookup(name) if name else None)
        if card is None:
            unresolved.append(name or "(unnamed card)")
            continue

        if COMMANDER_CATEGORY in categories:
            commanders.extend([card] * quantity)
        else:
            entries.append(DeckEntry(card, quantity))

    if not commanders:
        issues.append(
            DeckIssue(
                Severity.ERROR,
                "no-commander",
                "No card is in Archidekt's Commander category for this deck.",
            )
        )

    # Sorted, because Archidekt does not return a deck's cards in a stable
    # order. The order becomes the library before the shuffle, so the same deck
    # and seed fetched twice played different games - a run from a link could
    # not be reproduced, and a "slow game 37" vanished on the next fetch.
    commanders.sort(key=lambda card: card.name)
    entries.sort(key=lambda entry: entry.card.name)

    deck = Deck(
        name=payload.get("name") or "Archidekt deck",
        commanders=tuple(commanders),
        entries=tuple(entries),
        source=source,
        unresolved=tuple(unresolved),
    )
    return deck.with_issues(issues + validate(deck))


def _excluded_category_names(payload: dict) -> set[str]:
    """Categories whose cards are not part of the 100.

    Archidekt lets a deck define arbitrary categories and mark each as included
    or not, so a deck with a "Cuts" or "Upgrades" pile does not import 140
    cards.
    """
    excluded = set(_EXCLUDED_CATEGORIES)
    for category in payload.get("categories") or ():
        if isinstance(category, dict) and category.get("includedInDeck") is False:
            name = category.get("name")
            if name:
                excluded.add(name.lower())
    return excluded
