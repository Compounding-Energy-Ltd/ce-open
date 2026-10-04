# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""A small TOML writer (the standard library reads TOML but does not write it).

Supports what run documents need: nested tables, arrays of tables, strings,
booleans, integers, floats (including nan / inf), dates and arrays of scalars.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from typing import Any, Mapping

__all__ = ["dumps", "dump"]

_BARE = re.compile(r"^[A-Za-z0-9_-]+$")
_ESC = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def _key(k: str) -> str:
    k = str(k)
    return k if _BARE.match(k) else _string(k)


def _string(s: str) -> str:
    out = []
    for ch in s:
        if ch in _ESC:
            out.append(_ESC[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
        return repr(v)
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if hasattr(v, "item"):  # numpy scalar
        return _scalar(v.item())
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_scalar(x) for x in v) + "]"
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def _is_table_array(v: Any) -> bool:
    return isinstance(v, (list, tuple)) and len(v) > 0 and all(isinstance(x, Mapping) for x in v)


def _emit(table: Mapping[str, Any], path: list[str], lines: list[str]) -> None:
    scalars = {k: v for k, v in table.items() if not isinstance(v, Mapping) and not _is_table_array(v)}
    subs = {k: v for k, v in table.items() if isinstance(v, Mapping)}
    arrays = {k: v for k, v in table.items() if _is_table_array(v)}
    for k in sorted(scalars):
        lines.append(f"{_key(k)} = {_scalar(scalars[k])}")
    for k in sorted(subs):
        p = path + [_key(k)]
        lines.append("")
        lines.append("[" + ".".join(p) + "]")
        _emit(subs[k], p, lines)
    for k in sorted(arrays):
        p = path + [_key(k)]
        for item in arrays[k]:
            lines.append("")
            lines.append("[[" + ".".join(p) + "]]")
            _emit(item, p, lines)


def dumps(doc: Mapping[str, Any]) -> str:
    """Serialise ``doc`` as TOML text (keys sorted within each table)."""
    lines: list[str] = []
    _emit(doc, [], lines)
    return "\n".join(lines).lstrip("\n") + "\n"


def dump(doc: Mapping[str, Any], path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dumps(doc))
