# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Refusal codes: the catalog, and recovery of codes from failure text.

A scenario the service cannot apply comes back in one of three shapes:

1. **A 422 on submit** with ``{"error": <code or sentence>, "rows": [...]}``
   — rows carry ``table, row, column, id, code, message``.
2. **A failed queue row** whose ``detail`` is a compact one-line summary of
   the same rows::

       add_candidates[3] bus_id `site-7` out_of_scope: bus 912 is outside ...; add_loads[1] ...

   clipped by the service at about 2 KB, with ``(+N more)`` when rows were
   dropped.
3. **A run-level stop** whose ``detail`` is a sentence, often carrying the
   code as ``refused (<code>): ...`` or ``<code>: ...``.

:func:`parse_detail` turns any of these into :class:`Refusal` objects, each
annotated from the catalog (``data/refusal_codes.toml``) with a category,
a one-line summary and the usual fix. Unknown codes are kept verbatim.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any, Mapping

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# One row of the compact summary: table[row] column `id` code: message
_ROW_HEAD_RE = re.compile(
    r"(?:^|; )"
    r"(?P<table>[a-z][a-z0-9_.]*)\[(?P<row>\d+)\] "
    r"(?P<column>[^ ]*) "
    r"(?:`(?P<id>[^`]*)` )?"
    r"(?P<code>[a-z][a-z0-9_]*): "
)
_MORE_RE = re.compile(r"(?:^|; )\(\+(?P<n>\d+) more\)\s*$")
_REFUSED_RE = re.compile(r"refused \((?P<code>[a-z][a-z0-9_]*)\)")
_PREFIX_RE = re.compile(r"^(?P<code>[a-z][a-z0-9_]*): ")


@dataclass(frozen=True)
class CodeInfo:
    """What the catalog knows about a code."""

    code: str
    category: str
    summary: str
    fix: str
    known: bool = True  # False when only a family prefix (or nothing) matched

    @property
    def is_warning(self) -> bool:
        return self.category == "warning"


@dataclass(frozen=True)
class Refusal:
    """One refusal (or warning), with catalog context attached."""

    code: str
    message: str = ""
    table: str = ""
    row: int | None = None
    column: str = ""
    id: str = ""
    info: CodeInfo = field(default=None, compare=False)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.info is None:
            object.__setattr__(self, "info", describe(self.code))

    @classmethod
    def of(cls, code: str, message: str = "", **where: Any) -> "Refusal":
        return cls(code=code, message=message, **where)

    @property
    def category(self) -> str:
        return self.info.category

    @property
    def fix(self) -> str:
        return self.info.fix

    @property
    def is_warning(self) -> bool:
        return self.info.is_warning

    @property
    def location(self) -> str:
        """``table[row] column `id``` — empty parts left out."""
        bits = []
        if self.table:
            bits.append(f"{self.table}[{self.row}]" if self.row is not None else self.table)
        if self.column:
            bits.append(self.column)
        if self.id:
            bits.append(f"`{self.id}`")
        return " ".join(bits)

    def __str__(self) -> str:
        head = f"{self.location} {self.code}".strip()
        text = self.message or self.info.summary
        return f"{head}: {text}" if text else head

    def as_dict(self) -> dict[str, Any]:
        """Plain mapping (for TOML/CSV output)."""
        return {
            "code": self.code,
            "category": self.category,
            "table": self.table,
            "row": self.row,
            "column": self.column,
            "id": self.id,
            "message": self.message,
            "summary": self.info.summary,
            "fix": self.fix,
            "warning": self.is_warning,
        }


@dataclass(frozen=True)
class ParsedDetail:
    """The result of :func:`parse_detail`."""

    refusals: list[Refusal]
    omitted: int = 0  # rows the service summarised as "(+N more)"
    text: str = ""

    @property
    def codes(self) -> list[str]:
        return [r.code for r in self.refusals]

    def __bool__(self) -> bool:
        return bool(self.refusals)


# --------------------------------------------------------------------- catalog


@lru_cache(maxsize=1)
def _catalog() -> tuple[dict[str, CodeInfo], tuple[tuple[str, CodeInfo], ...]]:
    raw = resources.files("ce_studio").joinpath("data/refusal_codes.toml").read_bytes()
    doc = tomllib.loads(raw.decode("utf-8"))
    codes = {
        code: CodeInfo(code, v["category"], v["summary"], v["fix"])
        for code, v in doc.get("codes", {}).items()
    }
    families = tuple(
        (f["prefix"], CodeInfo(f["prefix"] + "*", f["category"], f["summary"], f["fix"], known=False))
        for f in doc.get("family", [])
    )
    return codes, families


def catalog() -> dict[str, CodeInfo]:
    """Every individually documented code (a copy)."""
    return dict(_catalog()[0])


def looks_like_code(s: str) -> bool:
    """True for identifier-shaped strings (``snake_case``) — the shape of every code."""
    return bool(_CODE_RE.match(s or ""))


def is_known_code(code: str) -> bool:
    """True when the catalog lists the code individually."""
    return code in _catalog()[0]


def describe(code: str) -> CodeInfo:
    """Catalog entry for ``code``; a family entry or a generic one when not listed."""
    codes, families = _catalog()
    if code in codes:
        return codes[code]
    for prefix, info in families:
        if code.startswith(prefix):
            return CodeInfo(code, info.category, info.summary, info.fix, known=False)
    return CodeInfo(
        code,
        "unknown",
        "A refusal this client does not have in its catalog.",
        "Read the service's message; upgrading the client may add a description.",
        known=False,
    )


# --------------------------------------------------------------------- parsing


def from_row(row: Mapping[str, Any], default_code: str | None = None) -> Refusal:
    """A :class:`Refusal` from a row record (``table, row, column, id, code, message``)."""
    code = str(row.get("code") or default_code or "unclassified")
    r = row.get("row")
    try:
        r = int(r) if r is not None and r != "" else None
    except (TypeError, ValueError):
        r = None
    return Refusal(
        code=code,
        message=str(row.get("message") or row.get("error") or ""),
        table=str(row.get("table") or ""),
        row=r,
        column=str(row.get("column") or ""),
        id=str(row.get("id") or ""),
    )


def parse_detail(detail: str | None) -> ParsedDetail:
    """Recover refusals from a failed scenario's ``detail`` text.

    Never raises; text it cannot classify comes back as a single
    ``unclassified`` refusal carrying the whole text as its message.
    """
    text = (detail or "").strip()
    if not text:
        return ParsedDetail([], 0, "")

    omitted = 0
    m_more = _MORE_RE.search(text)
    body = text
    if m_more:
        omitted = int(m_more.group("n"))
        body = text[: m_more.start()]

    heads = list(_ROW_HEAD_RE.finditer(body))
    if heads and heads[0].start() == 0:
        out = []
        for i, h in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
            out.append(
                Refusal(
                    code=h.group("code"),
                    message=body[h.end():end].strip(),
                    table=h.group("table"),
                    row=int(h.group("row")),
                    column=h.group("column"),
                    id=h.group("id") or "",
                )
            )
        return ParsedDetail(out, omitted, text)

    m = _REFUSED_RE.search(text)
    if m:
        return ParsedDetail([Refusal(code=m.group("code"), message=text)], omitted, text)

    m = _PREFIX_RE.match(text)
    if m and (is_known_code(m.group("code")) or "_" in m.group("code")):
        return ParsedDetail(
            [Refusal(code=m.group("code"), message=text[m.end():].strip())], omitted, text
        )

    # last resort: the first catalogued code mentioned as a whole word
    for word in re.findall(r"[a-z][a-z0-9_]+", text):
        if "_" in word and is_known_code(word):
            return ParsedDetail([Refusal(code=word, message=text)], omitted, text)

    return ParsedDetail([Refusal(code="unclassified", message=text)], omitted, text)
