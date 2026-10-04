# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Validation of TOML documents (bundle.toml, manifest.toml, summary.toml, ...) against ``*.doc.toml`` schemas.

Key paths are dotted; ``name[]`` addresses the elements of an array of tables and ``*`` matches any single
key of a free-form map. Keys and sections whose name starts with ``x_`` are extensions: never checked,
never refused.
"""

from __future__ import annotations

import datetime as _dt
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .issues import Collector
from .schema import DocSchema, KeySpec


@dataclass
class _Node:
    spec: KeySpec | None = None
    children: dict[str, "_Node"] = field(default_factory=dict)
    items: "_Node | None" = None


def _tree(schema: DocSchema) -> _Node:
    root = _Node()
    for ks in schema.keys:
        node = root
        for seg in ks.path.split("."):
            if seg.endswith("[]"):
                node = node.children.setdefault(seg[:-2], _Node())
                if node.items is None:
                    node.items = _Node()
                node = node.items
            else:
                node = node.children.setdefault(seg, _Node())
        node.spec = ks
    return root


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_float(v: Any) -> bool:
    return (isinstance(v, (int, float))) and not isinstance(v, bool)


def _type_ok(v: Any, t: str) -> bool:
    if t == "string":
        return isinstance(v, str)
    if t == "int":
        return _is_int(v)
    if t == "float":
        return _is_float(v)
    if t == "bool":
        return isinstance(v, bool)
    if t == "datetime":
        return isinstance(v, _dt.datetime) and v.tzinfo is not None
    if t == "table":
        return isinstance(v, dict)
    if t == "array_of_tables":
        return isinstance(v, list) and all(isinstance(x, dict) for x in v)
    if t.startswith("array[") and t.endswith("]"):
        inner = t[6:-1]
        return isinstance(v, list) and all(_type_ok(x, inner) for x in v)
    raise ValueError(f"unknown document key type {t!r}")


def _apply_aliases(doc: dict, schema: DocSchema, ctx: Collector, file: str) -> None:
    for legacy, canonical in schema.aliases.items():
        *lparent, lkey = legacy.split(".")
        *cparent, ckey = canonical.split(".")
        if lparent != cparent:
            raise ValueError(f"alias {legacy!r} -> {canonical!r} must keep the parent table")
        node: Any = doc
        for seg in lparent:
            node = node.get(seg) if isinstance(node, dict) else None
        if not isinstance(node, dict) or lkey not in node:
            continue
        if ckey in node:
            ctx.add("duplicate_spelling", f"both {legacy!r} (legacy) and {canonical!r} are present",
                    file=file, column=canonical)
        else:
            ctx.add("deprecated_spelling", f"legacy key {legacy!r} read as {canonical!r}", file=file, column=legacy)
            node[ckey] = node[lkey]
        del node[lkey]


def _check_value(v: Any, node: _Node, path: str, ctx: Collector, file: str) -> None:
    ks = node.spec
    if ks is None:   # an intermediate path the schema only declares through children
        if isinstance(v, dict):
            _check_table(v, node, path, ctx, file)
        return
    if not any(_type_ok(v, t) for t in ks.types):
        hint = " (a date-time needs an offset, e.g. Z)" if "datetime" in ks.types and isinstance(v, _dt.datetime) else ""
        ctx.add("bad_type", f"`{path}` must be {' or '.join(ks.types)}, got {type(v).__name__}{hint}",
                file=file, column=path)
        return
    if ks.enum is not None and not isinstance(v, (list, dict)) and v not in ks.enum:
        ctx.add("bad_enum", f"`{path}` = {v!r} is not one of {list(ks.enum)}", file=file, column=path)
    if _is_float(v):
        lo_bad = ks.min is not None and (v <= ks.min if ks.min_exclusive else v < ks.min)
        hi_bad = ks.max is not None and (v >= ks.max if ks.max_exclusive else v > ks.max)
        if lo_bad or hi_bad:
            ctx.add("out_of_range", f"`{path}` = {v!r} is outside the permitted range "
                    f"[{ks.min if ks.min is not None else '-inf'}, {ks.max if ks.max is not None else 'inf'}]",
                    file=file, column=path)
    if isinstance(v, list) and ks.min_items is not None and len(v) < ks.min_items:
        ctx.add("missing_value", f"`{path}` needs at least {ks.min_items} item(s)", file=file, column=path)
    if isinstance(v, dict) and not ks.open:
        _check_table(v, node, path, ctx, file)
    if isinstance(v, list) and node.items is not None and not ks.open:
        for i, item in enumerate(v):
            if isinstance(item, dict):
                _check_table(item, node.items, f"{path}[{i}]", ctx, file)


def _check_table(d: dict, node: _Node, path: str, ctx: Collector, file: str) -> None:
    for key, value in d.items():
        if key.startswith("x_"):
            continue
        child = node.children.get(key) or node.children.get("*")
        full = f"{path}.{key}" if path else key
        if child is None:
            ctx.add("unknown_key", f"`{full}` is not defined by the specification", file=file, column=full)
            continue
        _check_value(value, child, full, ctx, file)
    for key, child in node.children.items():
        if key != "*" and child.spec is not None and child.spec.required and key not in d:
            full = f"{path}.{key}" if path else key
            ctx.add("missing_key", f"required key `{full}` is absent", file=file, column=full)


def validate_document(doc: dict, schema: DocSchema, ctx: Collector, file: str | None = None) -> dict:
    """Check a parsed TOML document against its schema. Legacy key spellings are renamed in place.
    Returns the (renamed) document."""
    file = file or schema.name
    _apply_aliases(doc, schema, ctx, file)
    _check_table(doc, _tree(schema), "", ctx, file)
    return doc


def load_document(path: Path, schema: DocSchema, ctx: Collector) -> dict | None:
    """Parse and validate a TOML document file; None when it is absent or does not parse."""
    if not path.is_file():
        if schema.required:
            ctx.add("missing_document", f"{schema.name} is required", file=schema.name)
        return None
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        ctx.add("toml_parse_error", str(exc), file=path.name)
        return None
    return doc


def get_path(doc: dict, path: str, default: Any = None) -> Any:
    node: Any = doc
    for seg in path.split("."):
        if not isinstance(node, dict) or seg not in node:
            return default
        node = node[seg]
    return node
