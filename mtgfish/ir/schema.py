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

from .codec import ANY, IR_VERSION, classes, enums, field_default, fields_of
from .bracket import TAGS as BRACKET_TAGS

_ANY = ANY


def _all_enums() -> dict[str, type]:
    return dict(enums())


# ---------------------------------------------------------------------------
# JSON Schema
# ---------------------------------------------------------------------------


def _enum_schema(name: str) -> dict:
    cls = _all_enums()[name]
    if issubclass(cls, enum.Flag):
        bits = ", ".join(f"{m.name}={int(m)}" for m in cls if m.name)
        return {
            "type": "integer",
            "minimum": 0,
            "description": f"flag enum {name}: the OR of its bits ({bits}; {cls(0).name}=0)",
        }
    return {
        "type": "integer",
        "enum": [int(m) for m in cls],
        "description": f"enum {name}: " + ", ".join(f"{m.name}={int(m)}" for m in cls),
    }


def _json_type(kind: Any) -> dict:
    if kind == "int":
        return {"type": "integer"}
    if kind == "bool":
        return {"type": "boolean"}
    if kind == "str":
        return {"type": "string"}
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
        parts = [_json_type(k) for k in kind[1:]]
        if any(isinstance(k, tuple) and k[0] == "enum" for k in kind[1:]) and "int" in kind[1:]:
            # A bare integer in an enum-typed field is {"$int": n}.
            parts = [p for p in parts if p != {"type": "integer"}] + [{"$ref": "#/$defs/IRInt"}]
        return {"anyOf": parts}
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

                schema = {**schema, "default": encode(default, kind)}
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
    defs["IRInt"] = {
        "type": "object",
        "properties": {"$int": {"type": "integer"}},
        "required": ["$int"],
        "additionalProperties": False,
        "description": "a plain integer in a field whose type is an enum",
    }
    defs[_ANY] = {
        "anyOf": [
            {"type": ["null", "boolean", "integer", "string"]},
            {"type": "array", "items": {"$ref": f"#/$defs/{_ANY}"}},
            *({"$ref": f"#/$defs/{n}"} for n in classes()),
        ],
        "description": "any IR value (never an enum: an enum is only where a field declares one)",
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
            "bracket": {
                "description": (
                    "What the card counts as for a Commander bracket estimate"
                    " (mtgfish.ir.bracket), in this order. \"unread\": some of the"
                    " card couldn't be read, so its other tags are a floor."
                ),
                "type": "array",
                "items": {"enum": list(BRACKET_TAGS)},
                "uniqueItems": True,
            },
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
        return "string"
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
        parts = [_ts_type(k) for k in kind[1:]]
        if any(isinstance(k, tuple) and k[0] == "enum" for k in kind[1:]) and "int" in kind[1:]:
            parts = [p for p in parts if p != "number"] + ["IRInt"]
        return " | ".join(parts)
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
            field: encode(default, kind)
            for field, kind, default in fields_of(name)
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
        "/** A plain integer in a field whose type is an enum (complete() unwraps it). */",
        "export type IRInt = { $int: number };",
        "",
    ]
    for name in sorted(_enums_used()):
        cls = _all_enums()[name]
        if issubclass(cls, enum.Flag):
            bits = {cls(0).name: 0, **{m.name: int(m) for m in cls if m.name}}
            lines.append(f"/** Flag enum {name}: the OR of the bits in {name}Bits. */")
            lines.append(f"export type Enum{name} = number;")
            lines.append(
                f"export const {name}Bits: Record<string, number> = {{ "
                + ", ".join(f"{k}: {v}" for k, v in bits.items())
                + " };"
            )
        else:
            lines.append(f"/** Enum {name}: one of the numbers in {name}Values. */")
            lines.append(f"export type Enum{name} = number;")
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

                note = f"  // default {json.dumps(encode(default, kind))}"
            lines.append(f"  {field}{optional}: {_ts_type(kind)};{note}")
        lines.append("}")
        lines.append("")
    lines.append(
        f"export type {_ANY} = null | boolean | number | string | Array<{_ANY}> | "
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
        "  /** Bracket-estimate tags (mtgfish.ir.bracket); \"unread\" makes the rest a floor. */",
        f"  bracket?: Array<{' | '.join(json.dumps(t) for t in BRACKET_TAGS)}>;",
        "}",
        "",
    ]
    return "\n".join(lines)
