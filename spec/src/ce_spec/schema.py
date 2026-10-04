# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Load the machine-readable table and document schemas (``schemas/**/*.schema.toml`` / ``*.doc.toml``).

The schema files are the single source of truth for column names, types, units and constraints; the
Markdown column reference (TABLES.md) and the Arrow schemas returned by :meth:`TableSchema.arrow_schema`
are both derived from them.
"""

from __future__ import annotations

import functools
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa

#: The specification version this package implements.
SPEC_VERSION = "1.0"
SPEC_MAJOR, SPEC_MINOR = (int(x) for x in SPEC_VERSION.split("."))

ARROW_TYPES: dict[str, pa.DataType] = {
    "int8": pa.int8(), "int16": pa.int16(), "int32": pa.int32(), "int64": pa.int64(),
    "float32": pa.float32(), "float64": pa.float64(),
    "string": pa.string(), "bool": pa.bool_(),
}

FAMILIES = {"int8": "int", "int16": "int", "int32": "int", "int64": "int",
            "float32": "float", "float64": "float", "string": "string", "bool": "bool"}

SCOPES = ("bundle", "results")


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    required: bool = False
    nullable: bool | None = None
    unit: str = ""
    min: float | None = None
    max: float | None = None
    min_exclusive: bool = False
    max_exclusive: bool = False
    enum: tuple | None = None
    default: Any = None
    atol: float = 0.0
    description: str = ""
    since: str = SPEC_VERSION

    @property
    def family(self) -> str:
        return FAMILIES[self.type]

    @property
    def arrow_type(self) -> pa.DataType:
        return ARROW_TYPES[self.type]

    @property
    def allows_null(self) -> bool:
        """Required columns reject nulls unless the schema says otherwise; optional columns accept them."""
        return (not self.required) if self.nullable is None else self.nullable


@dataclass(frozen=True)
class ForeignKey:
    column: str
    table: str
    ref_column: str


@dataclass(frozen=True)
class TableSchema:
    name: str
    scope: str
    required: bool
    since: str
    grain: str
    description: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...] = ()
    unique: tuple[tuple[str, ...], ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()
    aliases: dict[str, str] = field(default_factory=dict)
    #: historical spellings that are refused rather than renamed (``ambiguous_legacy_cost``): name -> what to do
    refused: dict[str, str] = field(default_factory=dict)
    series: bool = False
    series_id: str = ""
    series_value: str = ""
    family: str = ""

    def column(self, name: str) -> Column | None:
        for c in self.columns:
            if c.name == name:
                return c
        return None

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def required_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.required]

    def arrow_schema(self, columns: list[str] | None = None) -> pa.Schema:
        """The canonical Arrow schema (all columns, or the named subset), with unit/description metadata."""
        cols = self.columns if columns is None else [self.column(n) for n in columns]
        fields = []
        for c in cols:
            if c is None:
                raise KeyError("unknown column in arrow_schema request")
            meta = {b"unit": c.unit.encode(), b"description": c.description.encode(),
                    b"required": b"true" if c.required else b"false"}
            fields.append(pa.field(c.name, c.arrow_type, nullable=c.allows_null, metadata=meta))
        return pa.schema(fields, metadata={b"ce_table": self.name.encode(), b"ce_format_version": SPEC_VERSION.encode()})


@dataclass(frozen=True)
class KeySpec:
    path: str
    types: tuple[str, ...]
    required: bool = False
    enum: tuple | None = None
    min: float | None = None
    max: float | None = None
    min_exclusive: bool = False
    max_exclusive: bool = False
    min_items: int | None = None
    default: Any = None
    open: bool = False
    description: str = ""


@dataclass(frozen=True)
class DocSchema:
    name: str
    scope: str
    required: bool
    since: str
    description: str
    keys: tuple[KeySpec, ...]
    aliases: dict[str, str] = field(default_factory=dict)


def schema_dir() -> Path:
    """Directory holding the schema files: packaged copy if installed, else the source checkout's."""
    packaged = Path(__file__).with_name("schemas")
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "schemas"


def _column(d: dict) -> Column:
    if d["type"] not in ARROW_TYPES:
        raise ValueError(f"schema column {d.get('name')!r}: unknown type {d['type']!r}")
    enum = d.get("enum")
    return Column(name=d["name"], type=d["type"], required=d.get("required", False),
                  nullable=d.get("nullable"), unit=d.get("unit", ""), min=d.get("min"), max=d.get("max"),
                  min_exclusive=d.get("min_exclusive", False), max_exclusive=d.get("max_exclusive", False),
                  enum=tuple(enum) if enum is not None else None, default=d.get("default"),
                  atol=float(d.get("atol", 0.0)), description=" ".join(d.get("description", "").split()),
                  since=d.get("since", SPEC_VERSION))


def _table(doc: dict) -> TableSchema:
    t = doc["table"]
    fks = []
    for fk in doc.get("foreign_key", []):
        ref_table, ref_col = fk["references"].split(".")
        fks.append(ForeignKey(fk["column"], ref_table, ref_col))
    return TableSchema(
        name=t["name"], scope=t["scope"], required=t.get("required", False), since=t.get("since", SPEC_VERSION),
        grain=t.get("grain", ""), description=" ".join(t.get("description", "").split()),
        columns=tuple(_column(c) for c in doc.get("column", [])),
        primary_key=tuple(t.get("primary_key", ())), unique=tuple(tuple(u) for u in t.get("unique", ())),
        foreign_keys=tuple(fks), aliases={a["legacy"]: a["canonical"] for a in doc.get("alias", [])},
        refused={r["legacy"]: " ".join(r["reason"].split()) for r in doc.get("refused_spelling", [])},
        series=t.get("series", False), series_id=t.get("series_id", ""), series_value=t.get("series_value", ""),
        family=t.get("family", ""))


def _doc(doc: dict) -> DocSchema:
    d = doc["document"]
    keys = []
    for k in doc.get("key", []):
        types = k["type"] if isinstance(k["type"], list) else [k["type"]]
        enum = k.get("enum")
        keys.append(KeySpec(path=k["path"], types=tuple(types), required=k.get("required", False),
                            enum=tuple(enum) if enum is not None else None, min=k.get("min"), max=k.get("max"),
                            min_exclusive=k.get("min_exclusive", False), max_exclusive=k.get("max_exclusive", False),
                            min_items=k.get("min_items"), default=k.get("default"), open=k.get("open", False),
                            description=" ".join(k.get("description", "").split())))
    return DocSchema(name=d["name"], scope=d["scope"], required=d.get("required", False),
                     since=d.get("since", SPEC_VERSION), description=" ".join(d.get("description", "").split()),
                     keys=tuple(keys), aliases={a["legacy"]: a["canonical"] for a in doc.get("alias", [])})


@functools.lru_cache(maxsize=None)
def _load() -> tuple[dict[str, dict[str, TableSchema]], dict[str, dict[str, DocSchema]]]:
    tables: dict[str, dict[str, TableSchema]] = {s: {} for s in SCOPES}
    docs: dict[str, dict[str, DocSchema]] = {s: {} for s in SCOPES}
    root = schema_dir()
    for scope in SCOPES:
        for p in sorted((root / scope).glob("*.schema.toml")):
            ts = _table(tomllib.loads(p.read_text(encoding="utf-8")))
            if ts.scope != scope or p.name != f"{ts.name}.schema.toml":
                raise ValueError(f"schema file {p} declares table {ts.name!r} in scope {ts.scope!r}")
            tables[scope][ts.name] = ts
        for p in sorted((root / scope).glob("*.doc.toml")):
            ds = _doc(tomllib.loads(p.read_text(encoding="utf-8")))
            if ds.scope != scope:
                raise ValueError(f"document schema {p} declares scope {ds.scope!r}")
            docs[scope][ds.name] = ds
    return tables, docs


def table_schemas(scope: str) -> dict[str, TableSchema]:
    """All table schemas of a scope (``"bundle"`` or ``"results"``), by table name."""
    return _load()[0][scope]


def table_schema(scope: str, name: str) -> TableSchema:
    return table_schemas(scope)[name]


def doc_schemas(scope: str) -> dict[str, DocSchema]:
    """All TOML document schemas of a scope, by file name."""
    return _load()[1][scope]


def doc_schema(scope: str, name: str) -> DocSchema:
    return doc_schemas(scope)[name]


def arrow_schema(scope: str, name: str) -> pa.Schema:
    """Shorthand for ``table_schema(scope, name).arrow_schema()``."""
    return table_schema(scope, name).arrow_schema()
