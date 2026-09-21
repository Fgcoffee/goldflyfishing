"""Everything the Deck tab shows about a deck, as plain data.

Headless for the same reason the sandbox is: the logic worth testing - which
cards are fully read, what the curve is - should not need a window to test.

Three parse states matter, and they are the ones ``Bridge.validate_deck``
already reports:

    ok        every ability the card prints is understood (or it has none)
    partial   some abilities are read, some are not           -> yellow
    blank     nothing about the card is understood            -> red

plus ``off`` for a card a person switched off in the sandbox, and
``missing`` for a line that names no card at all.
"""

from __future__ import annotations

from ..data.cards import CardDef
from ..data.db import CardDatabase
from ..parser import parse_card
from ..rules.cr106_mana import ManaSymbolKind
from ..rules.enums import COLOR_LETTERS, COLOR_ORDER, CardType, Color, color_letters

#: Scryfall's image CDN. Addressed by printing id, so showing a whole deck
#: costs no API calls at all - which matters, because the API asks for no more
#: than ten requests a second and a deck is a hundred cards.
IMAGE_CDN = "https://cards.scryfall.io/{size}/{side}/{a}/{b}/{id}.jpg"
#: Used only when the database predates storing printing ids.
IMAGE_BY_NAME = "https://api.scryfall.com/cards/named?exact={name}&format=image&version={size}"

#: Order the type groups are shown in. A card goes in the first that fits,
#: which is the convention every deck site uses: an artifact creature is a
#: creature.
TYPE_GROUPS: tuple[tuple[str, CardType], ...] = (
    ("Creature", CardType.CREATURE),
    ("Planeswalker", CardType.PLANESWALKER),
    ("Battle", CardType.BATTLE),
    ("Instant", CardType.INSTANT),
    ("Sorcery", CardType.SORCERY),
    ("Artifact", CardType.ARTIFACT),
    ("Enchantment", CardType.ENCHANTMENT),
    ("Land", CardType.LAND),
)

#: Double-faced layouts whose back face has its own image.
_TWO_IMAGE_LAYOUTS = {"transform", "modal_dfc", "double_faced_token", "reversible_card"}


def image_urls(card: CardDef, raw: dict | None) -> dict:
    """Front (and back, for double-faced cards) image URLs at two sizes."""
    from urllib.parse import quote

    print_id = (raw or {}).get("id") or ""
    layout = (raw or {}).get("layout") or ""
    if not print_id:
        name = quote(card.name)
        return {
            "normal": IMAGE_BY_NAME.format(name=name, size="normal"),
            "large": IMAGE_BY_NAME.format(name=name, size="large"),
            "art": IMAGE_BY_NAME.format(name=name, size="art_crop"),
            "back": None,
        }

    def url(size: str, side: str = "front") -> str:
        return IMAGE_CDN.format(size=size, side=side, a=print_id[0], b=print_id[1], id=print_id)

    return {
        "normal": url("normal"),
        "large": url("large"),
        "art": url("art_crop"),
        "back": url("normal", "back") if layout in _TWO_IMAGE_LAYOUTS else None,
    }


def parse_status(card: CardDef, verdicts=None) -> dict:
    """How much of this card the engine will actually do."""
    if verdicts is not None and verdicts.is_suppressed(card.name):
        return {"status": "off", "abilities": 0, "understood": 0, "unread": []}

    parsed = parse_card(card)
    total = sum(face.total for face in parsed.faces)
    read = sum(face.understood for face in parsed.faces)
    unread = [
        ability.text or ability.kind.name
        for face in parsed.faces
        for ability in face.abilities
        if ability.unparsed
        or any(node.is_unparsed for effect in ability.effects for node in effect.walk())
    ]
    if total == 0:
        status = "ok"  # A vanilla creature or a basic land is not a gap.
    elif read == 0:
        status = "blank"
    elif read < total:
        status = "partial"
    else:
        status = "ok"
    return {"status": status, "abilities": total, "understood": read, "unread": unread}


def type_group(card: CardDef) -> str:
    line = card.front.type_line
    for label, card_type in TYPE_GROUPS:
        if line.has_type(card_type):
            return label
    return "Other"


def _pips(card: CardDef) -> dict[str, int]:
    """Coloured symbols in the card's castable faces' costs.

    A hybrid symbol counts for each colour that can pay it, since either
    colour's sources can cast the card - which is the question pips answer.
    """
    out: dict[str, int] = {}
    for face in card.castable_faces:
        for symbol in face.mana_cost:
            if symbol.kind is ManaSymbolKind.COLORLESS:
                out["C"] = out.get("C", 0) + 1
                continue
            for color in COLOR_ORDER:
                if symbol.color_contribution & color:
                    letter = COLOR_LETTERS[color]
                    out[letter] = out.get(letter, 0) + 1
    return out


def card_row(card: CardDef, raw: dict | None, quantity: int, verdicts=None) -> dict:
    front = card.front
    return {
        "name": card.name,
        "quantity": quantity,
        "mana_cost": " // ".join(str(f.mana_cost) for f in card.castable_faces if f.has_mana_cost)
        or str(front.mana_cost),
        "mana_value": card.mana_value,
        "type_line": " // ".join(str(f.type_line) for f in card.faces),
        "type_group": type_group(card),
        "oracle_text": "\n//\n".join(f.oracle_text for f in card.faces if f.oracle_text),
        "power": front.power,
        "toughness": front.toughness,
        "color_identity": color_letters(card.color_identity),
        "is_land": front.is_land,
        "can_be_commander": card.can_be_commander,
        "produced_mana": list(front.produced_mana),
        "pips": _pips(card),
        "images": image_urls(card, raw),
        "scryfall_uri": card.scryfall_uri,
        **parse_status(card, verdicts),
    }


def _raw(db: CardDatabase, oracle_id: str) -> dict | None:
    """The stored Scryfall object, for the printing id the image URL needs."""
    return db.raw_card(oracle_id)


def deck_view(deck, db: CardDatabase, verdicts=None) -> dict:
    """The whole Deck tab for one parsed deck."""
    commanders: dict[str, dict] = {}
    for card in deck.commanders:
        if card.name in commanders:
            commanders[card.name]["quantity"] += 1
        else:
            commanders[card.name] = card_row(card, _raw(db, card.oracle_id), 1, verdicts)

    # Merge repeated lines ("1 Forest" twice) so each card is one tile.
    merged: dict[str, list] = {}
    for entry in deck.entries:
        if entry.card.name in merged:
            merged[entry.card.name][1] += entry.quantity
        else:
            merged[entry.card.name] = [entry.card, entry.quantity]
    cards = [
        card_row(card, _raw(db, card.oracle_id), quantity, verdicts)
        for card, quantity in merged.values()
    ]

    identity = Color.NONE
    for card in deck.commanders:
        identity |= card.color_identity
    if not deck.commanders:
        for card, _ in merged.values():
            identity |= card.color_identity

    everything = list(commanders.values()) + cards
    return {
        "name": deck.name,
        "source": deck.source,
        "commanders": list(commanders.values()),
        "cards": cards,
        "unresolved": list(deck.unresolved),
        "issues": [
            {"severity": issue.severity.name.lower(), "message": issue.message}
            for issue in deck.issues
        ],
        "color_identity": color_letters(identity),
        "stats": summary(everything),
    }


def summary(rows: list[dict]) -> dict:
    """Curve, pips, type counts and parse quality over a deck's rows.

    Commanders are included: they are cast, so they are on the curve.
    """
    curve: dict[str, int] = {}
    curve_by_type: dict[str, dict[str, int]] = {}
    pips: dict[str, int] = {}
    sources: dict[str, int] = {}
    types: dict[str, int] = {}
    status: dict[str, int] = {}
    total = lands = abilities = understood = 0
    value_sum = spells = 0

    for row in rows:
        qty = row["quantity"]
        total += qty
        types[row["type_group"]] = types.get(row["type_group"], 0) + qty
        status[row["status"]] = status.get(row["status"], 0) + qty
        abilities += row["abilities"] * qty
        understood += row["understood"] * qty
        if row["is_land"]:
            lands += qty
            for letter in set(row["produced_mana"]):
                sources[letter] = sources.get(letter, 0) + qty
            continue
        bucket = str(min(row["mana_value"], 7))  # "7" means seven or more
        curve[bucket] = curve.get(bucket, 0) + qty
        by_type = curve_by_type.setdefault(bucket, {})
        by_type[row["type_group"]] = by_type.get(row["type_group"], 0) + qty
        value_sum += row["mana_value"] * qty
        spells += qty
        for letter, count in row["pips"].items():
            pips[letter] = pips.get(letter, 0) + count * qty

    return {
        "total": total,
        "lands": lands,
        "spells": spells,
        "average_mana_value": round(value_sum / spells, 2) if spells else 0,
        "curve": curve,
        "curve_by_type": curve_by_type,
        "pips": pips,
        "sources": sources,
        "types": types,
        "status": status,
        "abilities": abilities,
        "abilities_understood": understood,
    }
