"""The IR JSON described two ways, both generated from the IR classes.

* ``json_schema()`` - a JSON Schema (draft 2020-12) for one face document,
  written to schema/ir.schema.json;
* ``typescript()`` - the same shapes as TypeScript types, written to
  engine/src/ir/types.ts (with ``typescript_defaults()`` in defaults.ts),
  for the engine to import rather than retype.

Both come from the dataclass definitions and their type hints, so they
cannot drift from the code that writes the JSON. Where a hint is looser or
narrower than what the pool actually holds (a bare ``tuple``; a Duration
field that also holds a plain integer), ``_OVERRIDES`` says what is really
there; ``mtgfish.tools.ir_export verify --schema`` checks every face against
the schema, so an override that is wrong fails loudly.
"""

from __future__ import annotations

import dataclasses
import enum
import types
import typing
from typing import Any

from .codec import IR_VERSION, classes, enums, field_default

_ANY = "IRValue"

#: Fields whose hint does not say what the pool holds: (class, field) -> the
#: type, in the same little language the hints are read into.
_OVERRIDES: dict[tuple[str, str], Any] = {
    # Untyped tuples in the source.
    ("Cost", "choices"): ("tuple", "Cost"),
    ("Effect", "granted_abilities"): ("tuple", "Ability"),
    ("Effect", "restrictions"): ("tuple", "Restriction"),
    ("Effect", "mode_costs"): ("tuple", _ANY),
    ("Effect", "outcomes"): ("tuple", _ANY),
    ("Effect", "mode_weights"): ("tuple", _ANY),
    # One (condition, at_least, at_most) triple, or empty.
    ("Effect", "modes_instead"): ("tuple", ("union", "Condition", "bool", "int")),
    ("ObjectFilter", "any_of"): ("tuple", "ObjectFilter"),
    # Hinted narrower than they are.
    ("Effect", "duration"): ("union", ("enum", "Duration"), "int"),
    ("ObjectFilter", "count"): ("union", "Value", "int", "null"),
    ("ObjectFilter", "from_top"): ("union", "int", "bool"),
    ("Ability", "quality"): ("union", "ObjectFilter", "PlayerFilter", "Value", "null"),
    ("Condition", "event_kinds"): ("tuple", ("union", ("enum", "EventKind"), "int")),
    ("TriggerCondition", "phases"): ("set", ("union", ("enum", "Phase"), "int")),
    ("TriggerCondition", "steps"): ("set", ("union", ("enum", "Step"), "int")),
}


# ---------------------------------------------------------------------------
# Reading the hints into a small type language
# ---------------------------------------------------------------------------


def _read_hint(hint: Any) -> Any:
    """A type hint as one of: "int", "bool", "str", "null", _ANY, a class
    name, ("enum", name), ("tuple", t), ("set", t), ("union", t, ...)."""
    if hint is type(None) or hint is None:
        return "null"
    if hint is bool:
        return "bool"
    if hint is int:
        return "int"
    if hint is str:
        return "str"
    if hint in (object, Any, tuple, frozenset):
        return ("tuple", _ANY) if hint is tuple else ("set", _ANY) if hint is frozenset else _ANY
    supertype = getattr(hint, "__supertype__", None)  # NewType
    if supertype is not None:
        return _read_hint(supertype)
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin in (typing.Union, types.UnionType):
        return ("union", *(_read_hint(a) for a in args))
    if origin is tuple:
        items = [a for a in args if a is not Ellipsis]
        if not items:
            return ("tuple", _ANY)
        if len(items) == 1:
            return ("tuple", _read_hint(items[0]))
        return ("tuple", ("union", *(_read_hint(a) for a in items)))
    if origin in (frozenset, set):
        return ("set", _read_hint(args[0]) if args else _ANY)
    if origin is list:
        return ("list", _read_hint(args[0]) if args else _ANY)
    if isinstance(hint, type):
        if issubclass(hint, enum.Enum):
            return ("enum", hint.__name__)
        if dataclasses.is_dataclass(hint):
            return hint.__name__
    raise TypeError(f"no schema rule for hint {hint!r}")


def fields_of(name: str) -> list[tuple[str, Any, Any]]:
    """``(field, type, default)`` for each field of an IR class."""
    cls = classes()[name]
    hints = typing.get_type_hints(cls)
    out = []
    for field in dataclasses.fields(cls):
        kind = _OVERRIDES.get((name, field.name))
        if kind is None:
            kind = _read_hint(hints.get(field.name, Any))
        out.append((field.name, kind, field_default(field)))
    return out


def _all_enums() -> dict[str, type]:
    """The registered enums plus any a hint names (Phase, Step)."""
    found = dict(enums())
    from ..rules.kernel import enums as kernel_enums

    for extra in ("Phase", "Step"):
        found.setdefault(extra, getattr(kernel_enums, extra))
    return found


# ---------------------------------------------------------------------------
# JSON Schema
# ---------------------------------------------------------------------------


def _enum_schema(name: str) -> dict:
    cls = _all_enums()[name]
    members = [m.name for m in cls]
    if issubclass(cls, enum.Flag):
        singles = [m.name for m in cls if int(m) and int(m) & (int(m) - 1) == 0]
        zero = cls(0).name
        alternatives = "|".join(singles)
        pattern = f"^{name}\\.(({alternatives})(\\|({alternatives}))*|{zero})$"
        return {"type": "string", "pattern": pattern, "description": f"flag enum {name}"}
    return {"type": "string", "enum": [f"{name}.{m}" for m in members]}


def _json_type(kind: Any) -> dict:
    if kind == "int":
        return {"type": "integer"}
    if kind == "bool":
        return {"type": "boolean"}
    if kind == "str":
        return {"$ref": "#/$defs/IRString"}
    if kind == "null":
        return {"type": "null"}
    if kind == _ANY:
        return {"$ref": f"#/$defs/{_ANY}"}
    if isinstance(kind, str):
        return {"$ref": f"#/$defs/{kind}"}
    tag = kind[0]
    if tag == "enum":
        return {"$ref": f"#/$defs/enum.{kind[1]}"}
    if tag == "tuple":
        return {"type": "array", "items": _json_type(kind[1])}
    if tag == "set":
        return {
            "type": "object",
            "properties": {"$set": {"type": "array", "items": _json_type(kind[1])}},
            "required": ["$set"],
            "additionalProperties": False,
        }
    if tag == "list":
        return {
            "type": "object",
            "properties": {"$list": {"type": "array", "items": _json_type(kind[1])}},
            "required": ["$list"],
            "additionalProperties": False,
        }
    if tag == "union":
        return {"anyOf": [_json_type(k) for k in kind[1:]]}
    raise TypeError(kind)


def _enums_used() -> set[str]:
    used: set[str] = set(enums())

    def visit(kind):
        if isinstance(kind, tuple):
            if kind[0] == "enum":
                used.add(kind[1])
            for part in kind[1:]:
                visit(part)

    for name in classes():
        for _field, kind, _default in fields_of(name):
            visit(kind)
    return used


def json_schema() -> dict:
    defs: dict[str, Any] = {}
    for name in classes():
        properties: dict[str, Any] = {"$": {"const": name}}
        for field, kind, default in fields_of(name):
            schema = _json_type(kind)
            if default is not dataclasses.MISSING:
                from .codec import encode

                schema = {**schema, "default": encode(default)}
            properties[field] = schema
        required = ["$"] + [f for f, _k, d in fields_of(name) if d is dataclasses.MISSING]
        defs[name] = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
    for name in sorted(_enums_used()):
        defs[f"enum.{name}"] = _enum_schema(name)
    enum_names = "|".join(sorted(_enums_used()))
    defs["IRString"] = {
        "anyOf": [
            {"type": "string", "not": {"pattern": f"^({enum_names})\\."}},
            {
                "type": "object",
                "properties": {"$str": {"type": "string"}},
                "required": ["$str"],
                "additionalProperties": False,
            },
        ],
        "description": "a string; one that looks like an enum member is wrapped in $str",
    }
    defs[_ANY] = {
        "anyOf": [
            {"type": ["null", "boolean", "integer"]},
            {"$ref": "#/$defs/IRString"},
            *({"$ref": f"#/$defs/enum.{n}"} for n in sorted(_enums_used())),
            {"type": "array", "items": {"$ref": f"#/$defs/{_ANY}"}},
            *({"$ref": f"#/$defs/{n}"} for n in classes()),
        ],
        "description": "any IR value",
    }
    defs["FaceDocument"] = {
        "type": "object",
        "description": "One card face: its abilities and the parse failures.",
        "properties": {
            "ir_version": {"const": IR_VERSION},
            "oracle_id": {"type": "string"},
            "name": {"type": "string"},
            "face": {"type": "integer", "minimum": 0},
            "face_name": {"type": "string"},
            "card_hash": {"type": "string"},
            "fully_read": {"type": "boolean"},
            "abilities": {"type": "array", "items": {"$ref": "#/$defs/Ability"}},
            "failures": {"type": "array", "items": {"$ref": "#/$defs/ParseFailure"}},
        },
        "required": [
            "ir_version", "oracle_id", "name", "face", "face_name", "card_hash",
            "fully_read", "abilities", "failures",
        ],
        "additionalProperties": False,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://goldflyfishing/ir/v{IR_VERSION}/card.schema.json",
        "title": f"Card IR document, IR version {IR_VERSION}",
        "description": (
            "One card: the printed card (CardDef) and each face's abilities and parse"
            " failures (FaceDocument). One file per card, <oracle_id>.json. See"
            " docs/ir-json.md."
        ),
        "type": "object",
        "properties": {
            "ir_version": {"const": IR_VERSION},
            "oracle_id": {"type": "string"},
            "name": {"type": "string"},
            "card_hash": {"type": "string"},
            "card": {"$ref": "#/$defs/CardDef"},
            "faces": {"type": "array", "items": {"$ref": "#/$defs/FaceDocument"}},
        },
        "required": ["ir_version", "oracle_id", "name", "card_hash", "card", "faces"],
        "additionalProperties": False,
        "$defs": defs,
    }


# ---------------------------------------------------------------------------
# TypeScript
# ---------------------------------------------------------------------------


def _ts_type(kind: Any) -> str:
    if kind == "int":
        return "number"
    if kind == "bool":
        return "boolean"
    if kind == "str":
        return "IRString"
    if kind == "null":
        return "null"
    if kind == _ANY:
        return _ANY
    if isinstance(kind, str):
        return kind
    tag = kind[0]
    if tag == "enum":
        return f"Enum{kind[1]}"
    if tag == "tuple":
        return f"Array<{_ts_type(kind[1])}>"
    if tag == "set":
        return f"{{ $set: Array<{_ts_type(kind[1])}> }}"
    if tag == "list":
        return f"{{ $list: Array<{_ts_type(kind[1])}> }}"
    if tag == "union":
        return " | ".join(_ts_type(k) for k in kind[1:])
    raise TypeError(kind)


def typescript_defaults() -> str:
    """engine/src/ir/defaults.ts: each class's field defaults, as JSON."""
    import json

    from .codec import encode

    lines = [
        f"// Generated by `python -m mtgfish.tools.ir_export schema`. IR version {IR_VERSION}.",
        "// Do not edit by hand. A field missing from an IR object takes the default here.",
        "",
        "export const DEFAULTS: Record<string, Record<string, unknown>> = {",
    ]
    for name in classes():
        fields = {
            field: encode(default)
            for field, _kind, default in fields_of(name)
            if default is not dataclasses.MISSING
        }
        lines.append(f"  {name}: {json.dumps(fields)},")
    lines += [
        "};",
        "",
        "/** Fields with no default: they are always present. */",
        "export const REQUIRED: Record<string, readonly string[]> = {",
    ]
    for name in classes():
        required = [f for f, _k, d in fields_of(name) if d is dataclasses.MISSING]
        lines.append(f"  {name}: {json.dumps(required)},")
    lines += ["};", ""]
    return "\n".join(lines)


def typescript() -> str:
    from .codec import encode

    lines = [
        f"// Generated by `python -m mtgfish.tools.ir_export schema`. IR version {IR_VERSION}.",
        "// Do not edit by hand: regenerate from the Python IR classes.",
        "// Encoding rules: docs/ir-json.md. A missing field takes the default noted beside it.",
        "",
        f"export const IR_VERSION = {IR_VERSION};",
        "",
        "/** A string; one that looks like an enum member is wrapped in $str. */",
        "export type IRString = string | { $str: string };",
        "",
    ]
    for name in sorted(_enums_used()):
        cls = _all_enums()[name]
        if issubclass(cls, enum.Flag):
            singles = [m.name for m in cls if int(m) and int(m) & (int(m) - 1) == 0]
            lines.append(
                f"/** Flag enum: \"{name}.\" then members joined by \"|\" "
                f"(e.g. \"{name}.{'|'.join(singles[:2])}\"), or \"{name}.{cls(0).name}\". "
                f"Members: {', '.join(singles)}. */"
            )
            lines.append(f"export type Enum{name} = `{name}.${{string}}`;")
            bits = {cls(0).name: 0, **{m.name: int(m) for m in cls if m.name}}
            lines.append(
                f"export const {name}Bits: Record<string, number> = {{ "
                + ", ".join(f"{k}: {v}" for k, v in bits.items())
                + " };"
            )
        else:
            members = " | ".join(f'"{name}.{m.name}"' for m in cls)
            lines.append(f"export type Enum{name} = {members};")
            lines.append(
                f"export const {name}Values: Record<string, number> = {{ "
                + ", ".join(f"{m.name}: {int(m)}" for m in cls)
                + " };"
            )
        lines.append("")
    for name in classes():
        lines.append(f"export interface {name} {{")
        lines.append(f'  $: "{name}";')
        for field, kind, default in fields_of(name):
            optional = "?" if default is not dataclasses.MISSING else ""
            note = ""
            if default is not dataclasses.MISSING:
                import json

                note = f"  // default {json.dumps(encode(default))}"
            lines.append(f"  {field}{optional}: {_ts_type(kind)};{note}")
        lines.append("}")
        lines.append("")
    lines.append(
        f"export type {_ANY} = null | boolean | number | IRString | Array<{_ANY}> | "
        + " | ".join(f"Enum{n}" for n in sorted(_enums_used()))
        + " | "
        + " | ".join(classes())
        + ";"
    )
    lines.append("")
    lines += [
        "export interface FaceDocument {",
        f"  ir_version: {IR_VERSION};",
        "  oracle_id: string;",
        "  name: string;",
        "  face: number;",
        "  face_name: string;",
        "  card_hash: string;",
        "  fully_read: boolean;",
        "  abilities: Array<Ability>;",
        "  failures: Array<ParseFailure>;",
        "}",
        "",
        "export interface CardDocument {",
        f"  ir_version: {IR_VERSION};",
        "  oracle_id: string;",
        "  name: string;",
        "  card_hash: string;",
        "  /** The printed card: faces with mana cost, type line, P/T, colors. */",
        "  card: CardDef;",
        "  faces: Array<FaceDocument>;",
        "}",
        "",
    ]
    return "\n".join(lines)
