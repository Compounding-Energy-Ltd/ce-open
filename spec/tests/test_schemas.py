# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The schema files are self-consistent, render to the committed TABLES.md, and every issue code is live."""

from __future__ import annotations

import re

import pyarrow as pa
import pytest

from conftest import SPEC_ROOT
from ce_spec.issues import CODES
from ce_spec.render import render_tables_md
from ce_spec.schema import SCOPES, doc_schemas, table_schemas


def test_every_scope_has_tables_and_documents():
    assert set(table_schemas("bundle")) == {
        "buses", "lines", "hvdc", "generators", "storage", "candidates", "demand", "availability",
        "weather_weights", "peak_demand", "interfaces", "interface_limits_monthly"}
    assert set(table_schemas("results")) == {
        "investment", "generation", "lmp", "flows", "curtailment", "storage_state", "transmission_losses",
        "qa_checks"}
    assert set(doc_schemas("bundle")) == {"bundle.toml"}
    assert set(doc_schemas("results")) == {"manifest.toml", "summary.toml", "qa_report.toml", "results_manifest.toml"}


@pytest.mark.parametrize("scope", SCOPES)
def test_table_schema_consistency(scope):
    tables = table_schemas(scope)
    name_re = re.compile(r"^[a-z][a-z0-9_]*$")
    for ts in tables.values():
        names = ts.column_names
        assert len(names) == len(set(names)), ts.name
        assert all(name_re.match(n) for n in names), ts.name
        assert ts.description and all(c.description for c in ts.columns), ts.name
        for k in ts.primary_key:
            assert k in names, (ts.name, k)
        for u in ts.unique:
            assert set(u) <= set(names), ts.name
        for fk in ts.foreign_keys:
            assert fk.column in names
            assert fk.table in tables and tables[fk.table].column(fk.ref_column) is not None, (ts.name, fk)
        for legacy, canonical in ts.aliases.items():
            assert canonical in names and legacy not in names, (ts.name, legacy)
        for c in ts.columns:
            if c.default is not None:
                assert not c.required, (ts.name, c.name)
            if c.enum is not None:
                pa.array(list(c.enum), type=c.arrow_type)   # enum values fit the column type
        if ts.series and scope == "bundle":
            assert ts.series_id in names and ts.series_value in names
        assert not any(n.endswith("_gbp") or "_gbp_" in n for n in names), "currency in a column name"


def test_arrow_schema_carries_units():
    from ce_spec import arrow_schema
    s = arrow_schema("bundle", "lines")
    assert s.field("rating_mw").type == pa.float64()
    assert s.field("rating_mw").metadata[b"unit"] == b"MW"
    assert s.field("id").nullable is False and s.field("loss_frac").nullable is True
    assert s.metadata[b"ce_table"] == b"lines"


@pytest.mark.parametrize("scope", SCOPES)
def test_document_key_paths_have_parents(scope):
    for ds in doc_schemas(scope).values():
        paths = {k.path for k in ds.keys}
        for k in ds.keys:
            parent = k.path.rsplit(".", 1)[0] if "." in k.path else None
            if parent is not None:
                assert parent.removesuffix("[]") in paths, (ds.name, k.path)
        for legacy, canonical in ds.aliases.items():
            assert canonical in paths and legacy not in paths


def test_tables_md_is_up_to_date():
    committed = (SPEC_ROOT / "TABLES.md").read_text(encoding="utf-8")
    assert committed == render_tables_md(), "TABLES.md is stale: python -m ce_spec render-docs > TABLES.md"


def test_every_code_is_used_and_every_used_code_is_registered():
    src = "\n".join(p.read_text(encoding="utf-8") for p in (SPEC_ROOT / "src" / "ce_spec").glob("*.py"))
    used = set(re.findall(r'ctx\.add\(\s*"([a-z_]+)"', src)) | set(re.findall(r'^\s+"([a-z_]+)": WARNING', src, re.M))
    assert used <= set(CODES), used - set(CODES)
    assert set(CODES) <= used, f"catalogued but never raised: {set(CODES) - used}"


def test_spec_md_sections_referenced_by_schemas_exist():
    spec = (SPEC_ROOT / "SPEC.md").read_text(encoding="utf-8")
    headings = set(re.findall(r"^#{2,3} (\d+(?:\.\d+)?)\.? ", spec, re.M))
    refs = set()
    for p in list((SPEC_ROOT / "schemas").rglob("*.toml")) + list((SPEC_ROOT / "src").rglob("*.py")):
        text = p.read_text(encoding="utf-8")
        refs |= set(re.findall(r"SPEC\.md §(\d+(?:\.\d+)?)", text))
        refs |= set(re.findall(r"SPEC\.md section (\d+(?:\.\d+)?)", text))
    assert refs and refs <= headings, refs - headings


def test_no_json_in_the_component():
    hits = [p for p in SPEC_ROOT.rglob("*") if p.suffix.lower() in (".json", ".geojson") and ".venv" not in p.parts]
    assert not hits, hits
