# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The import report: every place the GenX -> CE mapping loses or assumes something.

A conversion between two models is never exact. Rather than hiding the gaps,
the importer records each one as a *note* with a stable ``code`` (listed in the
README), a human-readable message and the GenX resources or lines it touched.
The report is returned by :func:`ce_genx.import_case` and written next to the
bundle as ``x_genx_import_report.toml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._toml import dumps

__all__ = ["Note", "ImportReport"]

#: severities, in increasing order of importance
LEVELS = ("info", "lossy", "dropped")


@dataclass
class Note:
    """One mapping gap.

    ``level`` is one of:

    * ``info``    – an assumption or default that does not change results;
    * ``lossy``   – the data was mapped, but approximately;
    * ``dropped`` – the data has no CE bundle equivalent and was not carried over.
    """

    code: str
    level: str
    message: str
    items: list[str] = field(default_factory=list)


@dataclass
class ImportReport:
    """Notes plus a few summary facts about the import."""

    summary: dict = field(default_factory=dict)
    notes: list[Note] = field(default_factory=list)

    def add(self, code: str, level: str, message: str, items=None) -> None:
        """Record a note; repeated codes are merged and their items unioned."""
        if level not in LEVELS:
            raise ValueError(f"unknown note level {level!r}")
        items = [str(i) for i in (items or [])]
        for note in self.notes:
            if note.code == code:
                note.items.extend(i for i in items if i not in note.items)
                return
        self.notes.append(Note(code, level, message, items))

    def codes(self) -> set[str]:
        return {n.code for n in self.notes}

    def get(self, code: str) -> Note | None:
        return next((n for n in self.notes if n.code == code), None)

    def to_toml(self) -> str:
        doc: dict = {"summary": {k: v for k, v in self.summary.items()}}
        notes = []
        for n in self.notes:
            entry = {"code": n.code, "level": n.level, "message": n.message}
            if n.items:
                entry["items"] = list(n.items)
            notes.append(entry)
        if notes:
            doc["note"] = notes
        return dumps(doc)

    def format(self) -> str:
        """A short plain-text rendering for the command line."""
        lines = []
        for level in reversed(LEVELS):
            for n in (n for n in self.notes if n.level == level):
                suffix = f" [{', '.join(n.items[:6])}{', ...' if len(n.items) > 6 else ''}]" if n.items else ""
                lines.append(f"{n.level:>7}  {n.code}: {n.message}{suffix}")
        return "\n".join(lines)
