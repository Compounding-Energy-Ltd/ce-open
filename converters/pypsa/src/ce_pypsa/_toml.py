# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Minimal TOML writer for the small documents this package emits.

Python's standard library reads TOML (``tomllib``) but cannot write it. The
documents written here (``bundle.toml`` and the conversion report) are
small, so a full writer is not needed. Supported values:

* scalars (``str``, ``int``, ``float``, ``bool``, ``datetime``) and lists of
  scalars, written as plain keys (an aware datetime is written in UTC with
  a ``Z`` suffix);
* ``dict`` values, written as ``[table]`` sections (nested to any depth);
* lists of dicts, written as ``[[array-of-tables]]`` sections.

Within each table, plain keys are emitted first, then sub-tables, then arrays
of tables, so the output is valid TOML whatever the insertion order.
"""

from __future__ import annotations

import datetime as _dt
import math
import tomllib
from pathlib import Path
from typing import Any

__all__ = ["dumps", "dump", "load"]

_ESCAPES = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def _string(value: str) -> str:
    out = []
    for ch in value:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _key(key: str) -> str:
    if key and all(c.isalnum() or c in "_-" for c in key):
        return key
    return _string(key)


def _scalar(value: Any) -> str:
    # bool first: bool is a subclass of int
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        text = repr(value)
        return text if any(c in text for c in ".eE") else text + ".0"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            return value.isoformat()  # local date-time
        utc = value.astimezone(_dt.timezone.utc)
        return utc.replace(tzinfo=None).isoformat() + "Z"
    if isinstance(value, _dt.date):
        return value.isoformat()
    # numpy scalars and similar: fall back on their Python equivalents
    if hasattr(value, "item"):
        return _scalar(value.item())
    raise TypeError(f"cannot write {type(value).__name__} as a TOML value")


def _value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_scalar(v) for v in value) + "]"
    return _scalar(value)


def _is_table(value: Any) -> bool:
    return isinstance(value, dict)


def _is_table_array(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) > 0 and all(isinstance(v, dict) for v in value)


def _body(doc: dict) -> list[str]:
    lines = []
    for key, value in doc.items():
        if value is None or _is_table(value) or _is_table_array(value):
            continue
        lines.append(f"{_key(key)} = {_value(value)}")
    return lines


def _emit(doc: dict, prefix: str, lines: list[str]) -> None:
    """Sub-tables and arrays of tables of ``doc``, recursively, under the dotted ``prefix``."""
    for key, value in doc.items():
        if _is_table(value):
            path = f"{prefix}.{_key(key)}" if prefix else _key(key)
            lines.extend(["", f"[{path}]"])
            lines.extend(_body(value))
            _emit(value, path, lines)
    for key, value in doc.items():
        if _is_table_array(value):
            path = f"{prefix}.{_key(key)}" if prefix else _key(key)
            for item in value:
                lines.extend(["", f"[[{path}]]"])
                lines.extend(_body(item))
                _emit(item, path, lines)


def dumps(doc: dict, header: str | None = None) -> str:
    """Serialise ``doc`` to a TOML string, optionally preceded by ``# header`` comment lines."""
    lines: list[str] = []
    if header:
        lines.extend("# " + h if h else "#" for h in header.splitlines())
        lines.append("")
    lines.extend(_body(doc))
    _emit(doc, "", lines)
    tidy = [ln for i, ln in enumerate(lines) if ln or (i > 0 and lines[i - 1])]  # no double blank lines
    return "\n".join(tidy).lstrip("\n") + "\n"


def dump(doc: dict, path: str | Path, header: str | None = None) -> None:
    """Write ``doc`` to ``path`` as TOML."""
    Path(path).write_text(dumps(doc, header), encoding="utf-8")


def load(path: str | Path) -> dict:
    """Read a TOML file."""
    with open(path, "rb") as fh:
        return tomllib.load(fh)
