"""What a card counts as for a Commander bracket estimate, read from its IR.

    from mtgfish.ir.bracket import classify
    tags = classify(abilities)        # {"tutor", "mass_land_denial", ...}

The roadmap's *estimated bracket* counts Game Changers, tutors, extra turns,
mass land denial and two-card combos in a decklist. This module answers the
per-card half from what the parser read, never from oracle text or Scryfall's
tags (those are only a cross-check, in ``ir_export brackets``):

* ``tutor``: searches a library for a card that need not be a land. Searching
  for lands only (Cultivate, fetch lands) is ramp, not a tutor.
* ``extra_turn``: an EXTRA_TURN effect.
* ``mass_land_denial``: destroys, exiles, bounces or makes players sacrifice
  lands without targeting them: every land, or several of each player's.
  Static denial (Winter Orb, Blood Moon) is not recognised yet.
* ``game_changer``: Scryfall's ``game_changer`` flag, the official list. Not
  read from the IR; :func:`game_changers` loads it from the card dump.

A card the parser could not fully read is classified from what it did read,
and ``unread`` is added: its tags are a floor, not an answer. Two-card
combos need a combo list, and there is none in the repository yet.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Iterator

from ..rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from ..rules.kernel.enums import CardType, Zone
from ..rules.kernel.query import (
    ControllerRelation,
    ObjectFilter,
    PlayerScope,
    Value,
    ValueKind,
)

TAGS = ("game_changer", "tutor", "extra_turn", "mass_land_denial", "unread")

_REMOVAL = {
    EffectKind.DESTROY,
    EffectKind.EXILE,
    EffectKind.SACRIFICE,
    EffectKind.RETURN_TO_HAND,
}


def effects_in(value) -> Iterator[Effect]:
    """Every Effect inside an IR value, nested ones included (modes,
    conditionals, granted and delayed abilities)."""
    if isinstance(value, Effect):
        yield value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            yield from effects_in(getattr(value, field.name))
    elif isinstance(value, (tuple, list, frozenset, set)):
        for item in value:
            yield from effects_in(item)


_land_types: frozenset[str] | None = None


def land_types() -> frozenset[str]:
    """CR 205.3i: the land subtypes, from the catalog the card data ships with."""
    global _land_types
    if _land_types is None:
        import json
        from pathlib import Path

        catalog = Path(__file__).resolve().parents[2] / "cache" / "scryfall" / "catalog-land-types.json"
        _land_types = frozenset(json.loads(catalog.read_text(encoding="utf-8")))
    return _land_types


def _land_only(filt: ObjectFilter | None) -> bool:
    """The filter can only match lands: by card type, or by land subtypes
    alone ("a Forest or Island card")."""
    if filt is None:
        return False
    if filt.types_all & CardType.LAND:
        return True
    if filt.types_any:
        return not (filt.types_any & ~CardType.LAND)
    subtypes = filt.subtypes_any or filt.subtypes_all
    return bool(subtypes) and all(name in land_types() for name in subtypes)


#: The ObjectFilter fields a "mass" land filter may set. Anything else
#: (enchanted, attacking, a specific object, a name) makes it about a few
#: lands, not all of them.
_MASS_FIELDS = frozenset(
    {
        "types_any",
        "types_all",
        "types_none",
        "supertypes_all",
        "supertypes_none",
        "subtypes_any",
        "subtypes_all",
        "subtypes_none",
        "controller",
        "other_than_source",
        "zones",
        "count",
        "up_to",
        "tapped",
    }
)


def _hits_lands(filt: ObjectFilter | None) -> bool:
    """The filter matches lands on the battlefield, not only the
    controller's own, and nothing but their kind narrows it.

    A land subtype is allowed: "destroy all Islands" (Boil) changes what four
    or more lands per player can do, which is the official definition of mass
    land denial.
    """
    if filt is None or filt.types_none & CardType.LAND:
        return False
    if filt.zones != frozenset({Zone.BATTLEFIELD}) or filt.controller == ControllerRelation.YOU:
        return False
    default = ObjectFilter()
    for field in dataclasses.fields(ObjectFilter):
        if field.name in _MASS_FIELDS or not field.compare:
            continue
        if getattr(filt, field.name) != getattr(default, field.name):
            return False
    if filt.types_all and filt.types_all != CardType.LAND:
        return False
    if filt.types_any and not (filt.types_any & CardType.LAND):
        return False
    if filt.types_any or filt.types_all:
        return True
    # No card type: every permanent ("exile all permanents"), or lands by
    # subtype alone.
    subtypes = filt.subtypes_any or filt.subtypes_all
    return not subtypes or all(name in land_types() for name in subtypes)


def is_tutor(effect: Effect) -> bool:
    """Searches a library (not "outside the game") for a card that needn't
    be a land."""
    if effect.kind != EffectKind.SEARCH_LIBRARY or _land_only(effect.targets):
        return False
    source = effect.from_zone
    return source is None or source == Zone.LIBRARY


def is_mass_land_denial(effect: Effect) -> bool:
    if effect.kind not in _REMOVAL or effect.is_targeted:
        return False
    filt = effect.targets
    if not _hits_lands(filt):
        return False
    if effect.kind == EffectKind.SACRIFICE:
        # "Each player sacrifices N lands": several, and not only yours.
        if effect.players is None or effect.players.scope == PlayerScope.YOU:
            return False
        if not _land_only(filt):
            return False
        # A number of lands: two or more, or one that varies (X, "half").
        count = filt.count
        if count is None:
            return True
        if not isinstance(count, Value) or count.kind != ValueKind.CONSTANT:
            return True
        return count.constant >= 2
    return filt.count is None


def classify(abilities: Iterable, *, fully_read: bool = True) -> set[str]:
    """The bracket tags of one face's abilities (all but ``game_changer``)."""
    tags: set[str] = set()
    for effect in effects_in(tuple(abilities)):
        if is_tutor(effect):
            tags.add("tutor")
        elif effect.kind == EffectKind.EXTRA_TURN:
            tags.add("extra_turn")
        elif is_mass_land_denial(effect):
            tags.add("mass_land_denial")
    if not fully_read:
        tags.add("unread")
    return tags


def game_changers(path=None) -> frozenset[str]:
    """Oracle ids Scryfall flags ``game_changer``, from the card dump the
    card database is built from."""
    import gzip
    import json

    from ..paths import data_root

    if path is None:
        dumps = sorted((data_root() / "scryfall").glob("oracle_cards-*.jsonl.gz"))
        if not dumps:
            from pathlib import Path

            dumps = sorted(
                (Path(__file__).resolve().parents[2] / "cache" / "scryfall").glob(
                    "oracle_cards-*.jsonl.gz"
                )
            )
        if not dumps:
            return frozenset()
        path = dumps[-1]
    ids = set()
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            card = json.loads(line)
            if card.get("game_changer") and card.get("oracle_id"):
                ids.add(card["oracle_id"])
    return frozenset(ids)


_game_changers: frozenset[str] | None = None


def is_game_changer(oracle_id: str) -> bool:
    global _game_changers
    if _game_changers is None:
        _game_changers = game_changers()
    return oracle_id in _game_changers


def game_changers_digest() -> str:
    """A hash of the Game Changers list, part of the IR's code fingerprint:
    the list changes without any card's text changing."""
    import hashlib

    global _game_changers
    if _game_changers is None:
        _game_changers = game_changers()
    return hashlib.sha256("\n".join(sorted(_game_changers)).encode()).hexdigest()


def card_tags(oracle_id: str, faces) -> list[str]:
    """A card's bracket tags, in ``TAGS`` order: the union over its faces.
    ``faces`` holds each face's (abilities, fully_read)."""
    tags: set[str] = set()
    for abilities, fully_read in faces:
        tags |= classify(abilities, fully_read=fully_read)
    if is_game_changer(oracle_id):
        tags.add("game_changer")
    return [tag for tag in TAGS if tag in tags]


# ---------------------------------------------------------------------------
# A deck's estimate
# ---------------------------------------------------------------------------

#: Thresholds of the estimate, from the official bracket descriptions
#: (Wizards of the Coast, Commander Brackets): Core (2) has no Game Changers,
#: no mass land denial, "few" tutors and extra turns "in low quantity";
#: Upgraded (3) allows up to three Game Changers; Optimized (4) anything.
#: "Few" and "low quantity" are not numbers in the source; these are.
MAX_GAME_CHANGERS_IN_3 = 3
MAX_TUTORS_IN_2 = 3
MAX_EXTRA_TURNS_IN_2 = 2


def estimate(documents) -> dict:
    """An estimated bracket for a deck: its card documents (one per distinct
    card; a ``count`` key, when present, is the number of copies).

    It is never 1 or 5: Exhibition and cEDH are about intent and tuning,
    which a decklist doesn't show. ``reasons`` says what drove the answer;
    it is a floor when some cards could not be read, and two-card combos
    are not checked at all.
    """
    cards: dict[str, list[str]] = {tag: [] for tag in TAGS}
    counts: dict[str, int] = {tag: 0 for tag in TAGS}
    for document in documents:
        copies = int(document.get("count", 1))
        for tag in document.get("bracket", ()):
            cards[tag].append(document["name"])
            counts[tag] += copies

    bracket = 2
    reasons: list[str] = []

    def at_least(level: int, why: str) -> None:
        nonlocal bracket
        bracket = max(bracket, level)
        reasons.append(why)

    gc = counts["game_changer"]
    if gc > MAX_GAME_CHANGERS_IN_3:
        at_least(4, f"{gc} Game Changers (more than {MAX_GAME_CHANGERS_IN_3})")
    elif gc:
        at_least(3, f"{gc} Game Changer{'s' if gc > 1 else ''}")
    if counts["mass_land_denial"]:
        at_least(4, f"{counts['mass_land_denial']} mass land denial")
    if counts["tutor"] > MAX_TUTORS_IN_2:
        at_least(3, f"{counts['tutor']} tutors (more than {MAX_TUTORS_IN_2})")
    if counts["extra_turn"] > MAX_EXTRA_TURNS_IN_2:
        at_least(3, f"{counts['extra_turn']} extra-turn cards (more than {MAX_EXTRA_TURNS_IN_2})")
    if not reasons:
        reasons.append("no Game Changers, mass land denial, or many tutors or extra turns")
    caveats = ["two-card combos are not checked"]
    if counts["unread"]:
        caveats.append(
            f"{counts['unread']} cards not fully read: their counts are a floor"
        )
    return {
        "bracket": bracket,
        "reasons": reasons,
        "caveats": caveats,
        "counts": counts,
        "cards": cards,
    }
