# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""A small TOML writer for the documents this package emits.

The standard library reads TOML (``tomllib``) but does not write it, and the
documents written here (a bundle descriptor and an import report) are simple
enough not to need a dependency. Supported values:

* ``str``, ``int``, ``float`` (including ``inf``/``nan``), ``bool``;
* ``datetime.datetime`` (written as an offset date-time; naive values are
  taken to be UTC);
* lists of the above;
* ``dict`` (a table) and lists of dicts (an array of tables), nested to any
  depth.

Within each table, plain keys are written first and sub-tables after them,
so the output is valid TOML whatever the insertion order of ``doc``.
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any

__all__ = ["dumps"]

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
    key = str(key)
    if key and all(c.isascii() and (c.isalnum() or c in "_-") for c in key):
        return key
    return _string(key)


def _scalar(value: Any) -> str:
    if isinstance(value, bool):  # before int: bool is an int subclass
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        text = repr(value)
        return text if any(c in text for c in ".e") else text + ".0"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        value = value.astimezone(_dt.timezone.utc).replace(microsecond=0)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if hasattr(value, "item"):  # numpy scalars
        return _scalar(value.item())
    raise TypeError(f"unsupported TOML value {value!r} ({type(value).__name__})")


def _value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_scalar(v) for v in value) + "]"
    return _scalar(value)


def _is_table_array(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0 and all(isinstance(v, dict) for v in value)


def _emit(table: dict, path: list[str], lines: list[str]) -> None:
    for k, v in table.items():
        if v is None or isinstance(v, dict) or _is_table_array(v):
            continue
        lines.append(f"{_key(k)} = {_value(v)}")
    for k, v in table.items():
        sub = path + [_key(k)]
        if isinstance(v, dict):
            lines.append("")
            lines.append(f"[{'.'.join(sub)}]")
            _emit(v, sub, lines)
        elif _is_table_array(v):
            for entry in v:
                lines.append("")
                lines.append(f"[[{'.'.join(sub)}]]")
                _emit(entry, sub, lines)


def dumps(doc: dict) -> str:
    """Serialise ``doc`` to a TOML string. ``None`` values are omitted."""
    lines: list[str] = []
    _emit(doc, [], lines)
    while lines and lines[0] == "":
        lines.pop(0)
    return "\n".join(lines) + "\n"
