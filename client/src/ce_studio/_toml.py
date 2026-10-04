# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""A minimal TOML writer for the CLI's machine-readable output and the TOML
documents :meth:`StudioClient.download_run` writes.

The standard library reads TOML (:mod:`tomllib`) but cannot write it. This
covers what the CLI emits: tables, arrays of tables, and scalars / arrays of
scalars. TOML has no null, so ``None`` values are left out (absent = not
reported, which is also how the service itself treats a missing measure).
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from typing import Any, Mapping

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(k: str) -> str:
    return k if _BARE_KEY.match(k) else _string(k)


def _string(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


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
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_scalar(x) for x in v if x is not None) + "]"
    if isinstance(v, Mapping):
        items = ", ".join(f"{_key(str(k))} = {_scalar(x)}" for k, x in v.items() if x is not None)
        return "{" + items + "}" if items else "{}"
    return _string(str(v))


def _is_table_array(v: Any) -> bool:
    return isinstance(v, (list, tuple)) and bool(v) and all(isinstance(x, Mapping) for x in v)


def dumps(doc: Mapping[str, Any]) -> str:
    """Serialise a mapping to TOML text."""
    lines: list[str] = []
    _emit(doc, [], lines)
    return "\n".join(lines).strip() + "\n"


def _emit(doc: Mapping[str, Any], path: list[str], lines: list[str]) -> None:
    scalars = [(k, v) for k, v in doc.items()
               if v is not None and not isinstance(v, Mapping) and not _is_table_array(v)]
    tables = [(k, v) for k, v in doc.items() if isinstance(v, Mapping)]
    arrays = [(k, v) for k, v in doc.items() if _is_table_array(v)]
    for k, v in scalars:
        lines.append(f"{_key(str(k))} = {_scalar(v)}")
    for k, v in tables:
        sub = path + [str(k)]
        lines.append("")
        lines.append("[" + ".".join(_key(p) for p in sub) + "]")
        _emit(v, sub, lines)
    for k, v in arrays:
        sub = path + [str(k)]
        for item in v:
            lines.append("")
            lines.append("[[" + ".".join(_key(p) for p in sub) + "]]")
            _emit(item, sub, lines)


def dump(doc: Mapping[str, Any], path: Any) -> None:
    """Write ``doc`` as a TOML file (UTF-8)."""
    from pathlib import Path

    Path(path).write_text(dumps(doc), encoding="utf-8")
