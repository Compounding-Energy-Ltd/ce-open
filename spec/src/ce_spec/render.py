# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Render TABLES.md — the column reference — from the schema files and the issue-code catalogue.

TABLES.md is generated, never edited by hand; a test fails when the committed file is out of date.
Regenerate with ``python -m ce_spec render-docs > TABLES.md``.
"""

from __future__ import annotations

from .issues import CODES
from .schema import SPEC_VERSION, Column, DocSchema, KeySpec, TableSchema, doc_schemas, table_schemas


def _cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def _range(c: Column | KeySpec) -> str:
    if c.enum is not None:
        return "one of " + ", ".join(f"`{v}`" for v in c.enum)
    if c.min is None and c.max is None:
        return ""
    lo = "−∞" if c.min is None else f"{c.min:g}"
    hi = "∞" if c.max is None else f"{c.max:g}"
    return (("(" if c.min_exclusive or c.min is None else "[") + f"{lo}, {hi}"
            + (")" if c.max_exclusive or c.max is None else "]"))


def _table_md(ts: TableSchema) -> list[str]:
    out = [f"### `{ts.name}`", ""]
    req = "required" if ts.required else "optional"
    out.append(f"*{req} · one row per {ts.grain} · since {ts.since}*")
    out.append("")
    out.append(ts.description)
    out.append("")
    if ts.primary_key:
        out.append(f"Primary key: ({', '.join(f'`{c}`' for c in ts.primary_key)})"
                   + (" — key columns absent from a file are left out of the key." if ts.series else "."))
    for u in ts.unique:
        out.append(f"Unique: ({', '.join(f'`{c}`' for c in u)}).")
    for fk in ts.foreign_keys:
        out.append(f"Reference: `{fk.column}` → `{fk.table}.{fk.ref_column}`.")
    out.append("")
    out.append("| column | type | req. | unit | range / values | default | description |")
    out.append("|---|---|---|---|---|---|---|")
    for c in ts.columns:
        default = "" if c.default is None else f"`{str(c.default).lower() if isinstance(c.default, bool) else c.default}`"
        out.append(f"| `{c.name}` | {c.type} | {'yes' if c.required else ''} | {_cell(c.unit)} | {_cell(_range(c))} "
                   f"| {default} | {_cell(c.description)} |")
    if ts.aliases:
        out.append("")
        out.append("Legacy spellings (accepted with a `deprecated_spelling` warning): "
                   + ", ".join(f"`{a}` → `{b}`" for a, b in ts.aliases.items()) + ".")
    if ts.refused:
        out.append("")
        out.append("Refused historical spellings (`ambiguous_legacy_cost`, an error): "
                   + "; ".join(f"`{a}` — {_cell(r)}" for a, r in ts.refused.items()) + ".")
    out.append("")
    return out


def _doc_md(ds: DocSchema) -> list[str]:
    out = [f"### `{ds.name}`", "", f"*{'required' if ds.required else 'optional'} · since {ds.since}*", "",
           ds.description, "", "| key | type | req. | range / values | default | description |",
           "|---|---|---|---|---|---|"]
    for k in ds.keys:
        default = "" if k.default is None else f"`{k.default}`"
        out.append(f"| `{k.path}` | {' or '.join(k.types)} | {'yes' if k.required else ''} | {_cell(_range(k))} "
                   f"| {default} | {_cell(k.description)} |")
    if ds.aliases:
        out.append("")
        out.append("Legacy spellings: " + ", ".join(f"`{a}` → `{b}`" for a, b in ds.aliases.items()) + ".")
    out.append("")
    return out


def render_tables_md() -> str:
    out = [f"# CE data format {SPEC_VERSION} — column and key reference", "",
           "<!-- GENERATED from schemas/ by `python -m ce_spec render-docs`. Do not edit by hand. -->", "",
           "The normative rules (versioning, units, signs, time axis, money, provenance) are in SPEC.md; this",
           "file lists every table, column and TOML key the format defines. `currency` in a unit means the",
           "bundle's declared currency in its declared dollar year.", ""]
    for scope, title in (("bundle", "Bundle (inputs)"), ("results", "Results (outputs)")):
        out += [f"## {title}", ""]
        for ds in sorted(doc_schemas(scope).values(), key=lambda d: (not d.required, d.name)):
            out += _doc_md(ds)
        tables = table_schemas(scope)
        for ts in sorted(tables.values(), key=lambda t: (not t.required, t.name)):
            out += _table_md(ts)
    out += ["## Issue codes", "", "| code | default severity | meaning |", "|---|---|---|"]
    for code, (sev, text) in CODES.items():
        out.append(f"| `{code}` | {sev} | {_cell(text)} |")
    out.append("")
    return "\n".join(out)
