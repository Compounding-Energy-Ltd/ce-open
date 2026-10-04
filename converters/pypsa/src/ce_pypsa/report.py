# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Conversion report: an explicit record of every lossy step.

Each converter call returns (and writes) a report so nothing is approximated
or dropped silently. Notes have a ``kind``:

``dropped``
    The source carried information the target cannot represent; it was
    discarded.
``approximated``
    The information was mapped onto a nearby target concept (for example a
    time-varying cost replaced by its weighted mean).
``defaulted``
    The target needs a value the source does not carry; a documented
    default was used.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

__all__ = ["Note", "ConversionReport", "LossyConversionWarning"]

MAX_ITEMS = 20


class LossyConversionWarning(UserWarning):
    """Emitted (once per note) when ``ConversionReport(warn=True)``."""


@dataclass
class Note:
    kind: str
    component: str
    attribute: str
    message: str
    items: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        shown = [str(i) for i in self.items[:MAX_ITEMS]]
        d = {"kind": self.kind, "component": self.component, "attribute": self.attribute,
             "message": self.message, "count": len(self.items)}
        if shown:
            d["items"] = shown
        if len(self.items) > MAX_ITEMS:
            d["items_truncated"] = True
        return d


@dataclass
class ConversionReport:
    direction: str
    source: str = ""
    notes: list[Note] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    warn: bool = False

    def add(self, kind: str, component: str, attribute: str, message: str, items=()) -> None:
        if kind not in ("dropped", "approximated", "defaulted"):
            raise ValueError(f"unknown note kind {kind!r}")
        items = [str(i) for i in items]
        for note in self.notes:  # merge repeated notes about the same thing
            if (note.kind, note.component, note.attribute, note.message) == (kind, component, attribute, message):
                note.items.extend(items)
                return
        self.notes.append(Note(kind, component, attribute, message, items))
        if self.warn:
            warnings.warn(f"{component}.{attribute}: {message}", LossyConversionWarning, stacklevel=3)

    def find(self, component: str | None = None, attribute: str | None = None, kind: str | None = None) -> list[Note]:
        return [n for n in self.notes
                if (component is None or n.component == component)
                and (attribute is None or n.attribute == attribute)
                and (kind is None or n.kind == kind)]

    def as_dict(self) -> dict:
        counts = {k: sum(1 for n in self.notes if n.kind == k) for k in ("dropped", "approximated", "defaulted")}
        return {
            "direction": self.direction,
            "source": self.source,
            "summary": {**counts, **self.stats},
            "note": [n.as_dict() for n in self.notes],
        }

    def __str__(self) -> str:
        lines = [f"{self.direction}: {len(self.notes)} note(s)"]
        for n in self.notes:
            extra = f" [{len(n.items)}]" if n.items else ""
            lines.append(f"  {n.kind:12s} {n.component}.{n.attribute}: {n.message}{extra}")
        return "\n".join(lines)
