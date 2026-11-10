"""The IR to JSON and back, exactly.

One rule per kind of value, so a reader in another language needs nothing
but this list:

* an IR object (a frozen dataclass: Ability, Effect, ObjectFilter, ...) is a
  JSON object whose ``"$"`` key names its class. Only fields that differ
  from the class's default are written; a missing field takes the default
  given in the schema;
* an enum member is a string ``"Enum.MEMBER"`` - ``"Zone.GRAVEYARD"``. A
  flag enum (CardType, Color, Supertype) joins its set bits with ``|`` -
  ``"CardType.ARTIFACT|CREATURE"`` - and is ``"CardType.NONE"`` when empty;
* a tuple is a JSON array;
* a frozenset is ``{"$set": [...]}``, its items sorted so the output is
  stable;
* a list is ``{"$list": [...]}`` (only the face's failures use one);
* a string that looks like an enum member (``"Zone.X"``) is written
  ``{"$str": "..."}`` so it cannot be read as one;
* ``None``, booleans, integers and other strings are themselves. A boolean stays a
  boolean and an integer an integer, even where a field holds either (an
  ObjectFilter's ``from_top`` is both ``True`` and ``3`` in the pool), and an
  enum stays distinct from a bare integer in the same field.

``decode(encode(x)) == x`` for every face in the pool, with every type
preserved (``exact``); ``mtgfish.tools.ir_export verify`` checks that.

The version is bumped whenever a reader of the old JSON would misread the
new: a field renamed or removed, a default changed, an enum member renamed.
New fields with defaults and new enum members do not bump it.
"""

from __future__ import annotations

import dataclasses
import enum
import importlib
import json
from typing import Any

#: Bumped on any change a reader of the previous version would misread.
IR_VERSION = 1

#: Every class that can appear in the IR, by the name the JSON uses.
_CLASS_PATHS = {
    "Ability": "mtgfish.rules.cr600_spells_and_abilities.abilities",
    "TriggerCondition": "mtgfish.rules.cr600_spells_and_abilities.abilities",
    "Effect": "mtgfish.rules.cr600_spells_and_abilities.effects",
    "TokenSpec": "mtgfish.rules.cr600_spells_and_abilities.effects",
    "ObjectFilter": "mtgfish.rules.kernel.query",
    "PlayerFilter": "mtgfish.rules.kernel.query",
    "Value": "mtgfish.rules.kernel.query",
    "Condition": "mtgfish.rules.kernel.query",
    "NumericConstraint": "mtgfish.rules.kernel.query",
    "Cost": "mtgfish.rules.cr100_game_concepts.cr118_costs",
    "CostComponent": "mtgfish.rules.cr100_game_concepts.cr118_costs",
    "AdditionalCost": "mtgfish.rules.cr100_game_concepts.cr118_costs",
    "AlternativeCost": "mtgfish.rules.cr100_game_concepts.cr118_costs",
    "ManaCost": "mtgfish.rules.cr100_game_concepts.cr106_mana",
    "ManaSymbol": "mtgfish.rules.cr100_game_concepts.cr106_mana",
    "SpendOnlyOn": "mtgfish.rules.cr100_game_concepts.cr106_mana",
    "Restriction": "mtgfish.rules.cr500_turn_structure.restrictions",
    "ParseFailure": "mtgfish.parser.errors",
}

#: Every enum that can appear in the IR.
_ENUM_PATHS = {
    "AbilityKind": "mtgfish.rules.cr600_spells_and_abilities.abilities",
    "EffectKind": "mtgfish.rules.cr600_spells_and_abilities.effects",
    "CardType": "mtgfish.rules.kernel.enums",
    "Supertype": "mtgfish.rules.kernel.enums",
    "Color": "mtgfish.rules.kernel.enums",
    "Zone": "mtgfish.rules.kernel.enums",
    "Timing": "mtgfish.rules.kernel.enums",
    "Duration": "mtgfish.rules.kernel.enums",
    "ControllerRelation": "mtgfish.rules.kernel.query",
    "Comparison": "mtgfish.rules.kernel.query",
    "ValueKind": "mtgfish.rules.kernel.query",
    "PlayerScope": "mtgfish.rules.kernel.query",
    "ConditionKind": "mtgfish.rules.kernel.query",
    "CostKind": "mtgfish.rules.cr100_game_concepts.cr118_costs",
    "ManaSymbolKind": "mtgfish.rules.cr100_game_concepts.cr106_mana",
    "EventKind": "mtgfish.rules.kernel.events",
    "Act": "mtgfish.rules.cr500_turn_structure.restrictions",
}

_classes: dict[str, type] | None = None
_enums: dict[str, type] | None = None


def classes() -> dict[str, type]:
    global _classes
    if _classes is None:
        _classes = {
            name: getattr(importlib.import_module(module), name)
            for name, module in _CLASS_PATHS.items()
        }
    return _classes


def enums() -> dict[str, type]:
    global _enums
    if _enums is None:
        _enums = {
            name: getattr(importlib.import_module(module), name)
            for name, module in _ENUM_PATHS.items()
        }
    return _enums


class IRError(ValueError):
    """A value the codec has no rule for, or JSON that is not IR."""


def field_default(field: dataclasses.Field) -> Any:
    if field.default is not dataclasses.MISSING:
        return field.default
    if field.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        return field.default_factory()  # type: ignore[misc]
    return dataclasses.MISSING


def _same(value: Any, default: Any) -> bool:
    """Whether ``value`` may be left out: equal to the default *and* of the
    same type, so ``0`` is never confused with ``Duration.INSTANT`` or
    ``False``."""
    return type(value) is type(default) and value == default


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def encode_enum(member: enum.Enum) -> str:
    cls = type(member)
    name = cls.__name__
    if name not in _ENUM_PATHS:
        raise IRError(f"enum {cls.__module__}.{name} is not in the IR")
    if isinstance(member, enum.Flag):
        value = int(member)
        if value == 0:
            zero = cls(0)
            if zero.name is None:
                raise IRError(f"{name} has no name for the empty set")
            return f"{name}.{zero.name}"
        bits = []
        covered = 0
        for single in cls:
            bit = int(single)
            if bit and bit & (bit - 1) == 0 and value & bit:
                bits.append(single.name)
                covered |= bit
        if covered != value:
            raise IRError(f"{name} value {value} has bits with no member")
        return f"{name}." + "|".join(bits)
    if member.name is None:
        raise IRError(f"{name} value {int(member)} has no member")
    return f"{name}.{member.name}"


def decode_enum(text: str) -> enum.Enum:
    name, _, members = text.partition(".")
    cls = enums().get(name)
    if cls is None:
        raise IRError(f"unknown enum {name!r}")
    if issubclass(cls, enum.Flag):
        value = cls(0)
        for member in members.split("|"):
            value |= cls[member]
        return value
    return cls[members]


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def encode(value: Any) -> Any:
    """Any IR value as JSON-ready Python data."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        head, dot, _ = value.partition(".")
        return {"$str": value} if dot and head in _ENUM_PATHS else value
    if isinstance(value, enum.Enum):
        return encode_enum(value)
    if isinstance(value, int):
        return value
    if isinstance(value, tuple):
        return [encode(item) for item in value]
    if isinstance(value, frozenset):
        items = [encode(item) for item in value]
        return {"$set": sorted(items, key=lambda x: json.dumps(x, sort_keys=True))}
    if isinstance(value, list):
        return {"$list": [encode(item) for item in value]}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        name = type(value).__name__
        if classes().get(name) is not type(value):
            raise IRError(f"class {type(value).__module__}.{name} is not in the IR")
        out: dict[str, Any] = {"$": name}
        for field in dataclasses.fields(value):
            item = getattr(value, field.name)
            if _same(item, field_default(field)):
                continue
            out[field.name] = encode(item)
        return out
    raise IRError(f"no JSON rule for {type(value).__name__}: {value!r}")


def decode(data: Any) -> Any:
    """JSON data back into IR values."""
    if data is None or isinstance(data, (bool, int)):
        return data
    if isinstance(data, str):
        head, dot, _ = data.partition(".")
        if dot and head in _ENUM_PATHS:
            return decode_enum(data)
        return data
    if isinstance(data, list):
        return tuple(decode(item) for item in data)
    if isinstance(data, dict):
        if "$str" in data:
            return data["$str"]
        if "$set" in data:
            return frozenset(decode(item) for item in data["$set"])
        if "$list" in data:
            return [decode(item) for item in data["$list"]]
        name = data.get("$")
        cls = classes().get(name)
        if cls is None:
            raise IRError(f"unknown IR class {name!r}")
        known = {f.name: f for f in dataclasses.fields(cls)}
        kwargs = {}
        for key, item in data.items():
            if key == "$":
                continue
            if key not in known:
                raise IRError(f"{name} has no field {key!r}")
            kwargs[key] = decode(item)
        return cls(**kwargs)
    raise IRError(f"not IR JSON: {data!r}")


def exact(a: Any, b: Any) -> bool:
    """Equal, and of the same types all the way down: ``==`` plus the
    canonical encoding, which tells an enum from an int, a bool from an int
    and a list from a tuple, and sorts sets so their order cannot matter."""
    return a == b and encode(a) == encode(b)


# ---------------------------------------------------------------------------
# Faces
# ---------------------------------------------------------------------------


def encode_face(card, face_index: int, abilities, failures, card_hash: str) -> dict:
    """One card face's parse as a JSON document."""
    face = card.faces[face_index] if card.faces else None
    return {
        "ir_version": IR_VERSION,
        "oracle_id": card.oracle_id,
        "name": card.name,
        "face": face_index,
        "face_name": face.name if face is not None else card.name,
        "card_hash": card_hash,
        "fully_read": not failures,
        "abilities": [encode(ability) for ability in abilities],
        "failures": [encode(failure) for failure in failures],
    }


def decode_face(document: dict) -> tuple[tuple, list]:
    """``(abilities, failures)`` from a face document."""
    if document.get("ir_version") != IR_VERSION:
        raise IRError(
            f"IR version {document.get('ir_version')!r}; this reader is {IR_VERSION}"
        )
    abilities = tuple(decode(item) for item in document["abilities"])
    failures = [decode(item) for item in document["failures"]]
    return abilities, failures
