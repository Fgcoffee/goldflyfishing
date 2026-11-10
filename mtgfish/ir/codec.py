"""The IR to JSON and back, exactly (IR version 2).

One rule per kind of value, so a reader in another language needs nothing
but this list:

* an IR object (a frozen dataclass: Ability, Effect, ObjectFilter, ...) is a
  JSON object whose ``"$"`` key names its class. Only fields that differ
  from the class's default are written; a missing field takes the default
  given in the schema;
* an enum member is its **number**, the same number the Python engine uses
  (``Zone.GRAVEYARD`` is ``3``; ``CardType.ARTIFACT | CREATURE`` is ``5``).
  Which enum a number belongs to is the field's declared type, given in the
  schema; the names are in the schema and in the generated TS tables;
* a plain integer in a field whose type is an enum - a few
  ``Effect.duration`` values are bare ints - is ``{"$int": n}``, so that it
  stays a plain integer;
* a tuple is a JSON array;
* a frozenset is ``{"$set": [...]}``, its items sorted so the output is
  stable;
* a list is ``{"$list": [...]}`` (only the face's failures use one);
* ``None``, booleans, integers and strings are themselves. A boolean stays a
  boolean, even where a field holds a boolean or an integer (an
  ObjectFilter's ``from_top`` is both ``True`` and ``3`` in the pool).

``decode(encode(x)) == x`` for every face in the pool, with every type
preserved (``exact``); ``mtgfish.tools.ir_export verify`` checks that.

The version is bumped whenever a reader of the old JSON would misread the
new: a field renamed or removed, a default changed, an enum member
renumbered. New fields with defaults and new enum members do not bump it.

Version 1 wrote enums as ``"Zone.GRAVEYARD"`` strings; version 2 writes the
numbers, because the engine compares and masks them in its hottest paths.
"""

from __future__ import annotations

import dataclasses
import enum
import importlib
import json
from typing import Any

#: Bumped on any change a reader of the previous version would misread.
IR_VERSION = 2

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
    "Phase": "mtgfish.rules.kernel.enums",
    "Step": "mtgfish.rules.kernel.enums",
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
# Field types
# ---------------------------------------------------------------------------

#: An untyped position: any IR value. An enum is refused there, because its
#: number would not say which enum it is.
ANY = "IRValue"

#: Fields whose hint does not say what the pool holds: (class, field) -> the
#: type, in the same little language the hints are read into.
OVERRIDES: dict[tuple[str, str], Any] = {
    # Untyped tuples in the source.
    ("Cost", "choices"): ("tuple", "Cost"),
    ("Effect", "granted_abilities"): ("tuple", "Ability"),
    ("Effect", "restrictions"): ("tuple", "Restriction"),
    ("Effect", "mode_costs"): ("tuple", ANY),
    ("Effect", "outcomes"): ("tuple", ANY),
    ("Effect", "mode_weights"): ("tuple", ANY),
    # One (condition, at_least, at_most) triple, or empty.
    ("Effect", "modes_instead"): ("tuple", ("union", "Condition", "bool", "int")),
    ("ObjectFilter", "any_of"): ("tuple", "ObjectFilter"),
    ("TokenSpec", "abilities"): ("tuple", "Ability"),
    # Hinted as ``object`` (a forward reference) in the source.
    ("Ability", "alternative_cost"): ("union", "AlternativeCost", "null"),
    ("Ability", "additional_cost"): ("union", "AdditionalCost", "null"),
    ("Effect", "pay_cost"): ("union", "Cost", "null"),
    ("Effect", "mana_restriction"): ("union", "SpendOnlyOn", "null"),
    ("Effect", "trigger"): ("union", "TriggerCondition", "null"),
    ("AlternativeCost", "from_zone"): ("union", ("enum", "Zone"), "null"),
    ("AlternativeCost", "condition"): ("union", "Condition", "null"),
    ("SpendOnlyOn", "filter"): ("union", "ObjectFilter", "null"),
    ("TriggerCondition", "alternatives"): ("tuple", "TriggerCondition"),
    # Hinted narrower than they are.
    ("Effect", "duration"): ("union", ("enum", "Duration"), "int"),
    ("ObjectFilter", "count"): ("union", "Value", "int", "null"),
    ("ObjectFilter", "from_top"): ("union", "int", "bool"),
    ("Ability", "quality"): ("union", "ObjectFilter", "PlayerFilter", "Value", "null"),
    ("Condition", "event_kinds"): ("tuple", ("union", ("enum", "EventKind"), "int")),
    ("TriggerCondition", "phases"): ("set", ("union", ("enum", "Phase"), "int")),
    ("TriggerCondition", "steps"): ("set", ("union", ("enum", "Step"), "int")),
}


def read_hint(hint: Any) -> Any:
    """A type hint as one of: "int", "bool", "str", "null", ANY, a class
    name, ("enum", name), ("tuple", t), ("set", t), ("list", t),
    ("union", t, ...)."""
    import types
    import typing

    if hint is type(None) or hint is None:
        return "null"
    if hint is bool:
        return "bool"
    if hint is int:
        return "int"
    if hint is str:
        return "str"
    if hint in (object, Any):
        return ANY
    if hint is tuple:
        return ("tuple", ANY)
    if hint is frozenset:
        return ("set", ANY)
    supertype = getattr(hint, "__supertype__", None)  # NewType
    if supertype is not None:
        return read_hint(supertype)
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin in (typing.Union, types.UnionType):
        return ("union", *(read_hint(a) for a in args))
    if origin is tuple:
        items = [a for a in args if a is not Ellipsis]
        if not items:
            return ("tuple", ANY)
        if len(items) == 1:
            return ("tuple", read_hint(items[0]))
        return ("tuple", ("union", *(read_hint(a) for a in items)))
    if origin in (frozenset, set):
        return ("set", read_hint(args[0]) if args else ANY)
    if origin is list:
        return ("list", read_hint(args[0]) if args else ANY)
    if isinstance(hint, type):
        if issubclass(hint, enum.Enum):
            return ("enum", hint.__name__)
        if dataclasses.is_dataclass(hint):
            return hint.__name__
    raise TypeError(f"no IR rule for hint {hint!r}")


_fields_cache: dict[str, list] = {}


def fields_of(name: str) -> list[tuple[str, Any, Any]]:
    """``(field, type, default)`` for each field of an IR class."""
    cached = _fields_cache.get(name)
    if cached is not None:
        return cached
    import typing

    cls = classes()[name]
    hints = typing.get_type_hints(cls)
    out = []
    for field in dataclasses.fields(cls):
        kind = OVERRIDES.get((name, field.name))
        if kind is None:
            kind = read_hint(hints.get(field.name, Any))
        out.append((field.name, kind, field_default(field)))
    _fields_cache[name] = out
    return out


def _enum_of(kind: Any) -> str | None:
    """The enum a field type names, directly or in a union."""
    if isinstance(kind, tuple):
        if kind[0] == "enum":
            return kind[1]
        if kind[0] == "union":
            for part in kind[1:]:
                found = _enum_of(part)
                if found is not None:
                    return found
    return None


def _item_kind(kind: Any, tag: str) -> Any:
    """The item type of a tuple, set or list type, looking inside a union."""
    if isinstance(kind, tuple):
        if kind[0] == tag:
            return kind[1]
        if kind[0] == "union":
            for part in kind[1:]:
                if isinstance(part, tuple) and part[0] == tag:
                    return part[1]
    return ANY


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def encode(value: Any, kind: Any = ANY) -> Any:
    """Any IR value as JSON-ready Python data. ``kind`` is the declared type
    of the position it sits in, which says which enum a number is."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, enum.Enum):
        name = type(value).__name__
        if name not in _ENUM_PATHS:
            raise IRError(f"enum {type(value).__module__}.{name} is not in the IR")
        if _enum_of(kind) != name:
            raise IRError(f"{name}.{value.name} where the IR declares {kind!r}")
        return int(value)
    if isinstance(value, int):
        return {"$int": value} if _enum_of(kind) is not None else value
    if isinstance(value, tuple):
        item = _item_kind(kind, "tuple")
        return [encode(x, item) for x in value]
    if isinstance(value, frozenset):
        item = _item_kind(kind, "set")
        items = [encode(x, item) for x in value]
        return {"$set": sorted(items, key=lambda x: json.dumps(x, sort_keys=True))}
    if isinstance(value, list):
        item = _item_kind(kind, "list")
        return {"$list": [encode(x, item) for x in value]}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        name = type(value).__name__
        if classes().get(name) is not type(value):
            raise IRError(f"class {type(value).__module__}.{name} is not in the IR")
        out: dict[str, Any] = {"$": name}
        for field_name, field_kind, default in fields_of(name):
            item = getattr(value, field_name)
            if _same(item, default):
                continue
            out[field_name] = encode(item, field_kind)
        return out
    raise IRError(f"no JSON rule for {type(value).__name__}: {value!r}")


def decode(data: Any, kind: Any = ANY) -> Any:
    """JSON data back into IR values, ``kind`` being the position's type."""
    if data is None or isinstance(data, (bool, str)):
        return data
    if isinstance(data, int):
        name = _enum_of(kind)
        if name is None:
            return data
        cls = enums().get(name)
        if cls is None:
            raise IRError(f"unknown enum {name!r}")
        try:
            return cls(data)
        except ValueError as error:
            raise IRError(f"{name} has no member {data}") from error
    if isinstance(data, list):
        item = _item_kind(kind, "tuple")
        return tuple(decode(x, item) for x in data)
    if isinstance(data, dict):
        if "$int" in data:
            return int(data["$int"])
        if "$set" in data:
            item = _item_kind(kind, "set")
            return frozenset(decode(x, item) for x in data["$set"])
        if "$list" in data:
            item = _item_kind(kind, "list")
            return [decode(x, item) for x in data["$list"]]
        name = data.get("$")
        cls = classes().get(name)
        if cls is None:
            raise IRError(f"unknown IR class {name!r}")
        known = {field: field_kind for field, field_kind, _ in fields_of(name)}
        kwargs = {}
        for key, item in data.items():
            if key == "$":
                continue
            if key not in known:
                raise IRError(f"{name} has no field {key!r}")
            kwargs[key] = decode(item, known[key])
        return cls(**kwargs)
    raise IRError(f"not IR JSON: {data!r}")


def exact(a: Any, b: Any, kind: Any = ANY) -> bool:
    """Equal, and of the same types all the way down: ``==`` plus the
    canonical encoding, which tells an enum from an int, a bool from an int
    and a list from a tuple, and sorts sets so their order cannot matter.
    ``kind`` is the declared type of the position, as for ``encode``."""
    return a == b and encode(a, kind) == encode(b, kind)


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
        "abilities": [encode(ability, "Ability") for ability in abilities],
        "failures": [encode(failure, "ParseFailure") for failure in failures],
    }


def decode_face(document: dict) -> tuple[tuple, list]:
    """``(abilities, failures)`` from a face document."""
    if document.get("ir_version") != IR_VERSION:
        raise IRError(
            f"IR version {document.get('ir_version')!r}; this reader is {IR_VERSION}"
        )
    abilities = tuple(decode(item, "Ability") for item in document["abilities"])
    failures = [decode(item, "ParseFailure") for item in document["failures"]]
    return abilities, failures
