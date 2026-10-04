# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The ``.prov`` provenance sidecar: grammar, parser and checks (SPEC.md section 7).

A sidecar ``<file>.prov`` sits next to the file it describes (``candidates.parquet.prov`` describes
``candidates.parquet``). It is line-oriented UTF-8 text:

* a KEY LINE is ``key=value``: the key is one or more characters other than whitespace, ``=`` and ``#``;
  blanks around the FIRST ``=`` are ignored; the value is the rest of the line with surrounding blanks
  trimmed and ONE surrounding pair of double quotes removed (no escape processing);
* blank lines, and lines whose first non-blank character is ``#``, are comments;
* any other line is ignored by readers (the validator warns ``prov_unparsed_line``);
* the FIRST occurrence of a key wins (later ones are ignored; the validator warns ``prov_duplicate_key``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_KEY_LINE = re.compile(r"^\s*([^=#\s]+)\s*=\s*(.*?)\s*$")

#: Keys with a defined meaning. Any other key is allowed and carried as free text.
STANDARD_KEYS = {
    "source": "Where the data comes from (free text or URL).",
    "license": "SPDX identifier of the data's licence.",
    "built_utc": "Instant the described file was written, ISO 8601 UTC (e.g. 2026-10-01T12:00:00Z).",
    "tool": "Name of the program that wrote the file.",
    "tool_version": "Its version.",
    "sha256": "Lower-case hex SHA-256 of the described file's bytes.",
    "rows": "Number of data rows of the described table.",
    "generation": "Identifier of the build the file belongs to; derived files repeat it as source_generation.",
    "source_generation": "The `generation` of the source table a derived table was cut from.",
    "derived_from": "File name (in the same directory) of the source table a derived table was cut from.",
    "notes": "Free text.",
}


@dataclass
class Prov:
    """A parsed sidecar: the key/value map plus what the grammar skipped."""

    values: dict[str, str] = field(default_factory=dict)
    unparsed: list[int] = field(default_factory=list)      # 1-based line numbers
    duplicates: list[tuple[int, str]] = field(default_factory=list)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.values.get(key, default)


def parse_prov_text(text: str) -> Prov:
    out = Prov()
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _KEY_LINE.match(line)
        if m is None:
            out.unparsed.append(lineno)
            continue
        key, value = m.group(1), m.group(2)
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        if key in out.values:
            out.duplicates.append((lineno, key))
            continue
        out.values[key] = value
    return out


def read_prov(path: str | Path) -> Prov:
    """Parse a sidecar; an absent file reads as an empty :class:`Prov`."""
    p = Path(path)
    if not p.is_file():
        return Prov()
    return parse_prov_text(p.read_text(encoding="utf-8"))


def format_prov(values: dict[str, object]) -> str:
    """Render a sidecar (one ``key=value`` line per entry, insertion order). Values containing leading or
    trailing blanks are quoted so they survive the round trip."""
    lines = []
    for k, v in values.items():
        if not k or any(ch.isspace() or ch in "=#" for ch in k):
            raise ValueError(f"invalid .prov key {k!r}")
        s = str(v)
        if "\n" in s:
            raise ValueError(f".prov value for {k!r} spans lines")
        if s != s.strip() or (len(s) >= 2 and s.startswith('"') and s.endswith('"')):
            s = f'"{s}"'
        lines.append(f"{k}={s}")
    return "\n".join(lines) + "\n"


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def valid_utc_instant(s: str) -> bool:
    if not s.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(s[:-1] + "+00:00")
    except ValueError:
        return False
    return True
