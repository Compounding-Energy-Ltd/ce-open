# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Checks that span components, so independently installable packages cannot drift apart unnoticed.

* The four small TOML writers (client, results, GenX and PyPSA converters) — kept separate so that no
  package needs another at run time — write the same documents that ``tomllib`` reads back identically.
* The column types the PyPSA converter declares for the bundle tables it writes agree with the CE format
  schemas in ``spec/``.

Every component is imported from this checkout; nothing here is skipped.
"""

from __future__ import annotations

import datetime as dt
import importlib
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for src in ("spec/src", "client/src", "results/src", "converters/genx/src", "converters/pypsa/src"):
    if str(ROOT / src) not in sys.path:
        sys.path.insert(0, str(ROOT / src))

WRITERS = {
    "ce_studio": "ce_studio._toml",
    "ce_results": "ce_results._toml",
    "ce_genx": "ce_genx._toml",
    "ce_pypsa": "ce_pypsa._toml",
}

DOCUMENTS = [
    {"format": {"name": "ce-bundle", "version": "1.0"}, "money": {"currency": "USD", "dollar_year": 2024}},
    {"text": 'quote " backslash \\ tab \t newline \n unicode é ✓', "empty": "", "key with space": 1},
    {"ints": [1, -2, 0], "floats": [0.1, 1e-7, 2.5e9, -0.0], "bools": [True, False], "big": 2**40},
    {"when": dt.datetime(2026, 10, 1, 12, 30, tzinfo=dt.timezone.utc), "nested": {"deeper": {"x": 1.5}}},
    {"x_policy": [{"name": "cap", "kind": "emission_cap_t", "value": 1.5e6, "zones": ["a", "b"]},
                  {"name": "cap2", "kind": "emission_cap_t", "value": 2.0}],
     "time": {"blocks": [{"id": 1, "nperiods": 24, "dt_h": 1.0}, {"id": 2, "nperiods": 24, "dt_h": 1.0}]}},
]


@pytest.mark.parametrize("package", sorted(WRITERS))
@pytest.mark.parametrize("doc", DOCUMENTS, ids=lambda d: next(iter(d)))
def test_toml_writers_round_trip_the_same_documents(package, doc):
    writer = importlib.import_module(WRITERS[package])
    assert tomllib.loads(writer.dumps(doc)) == doc


def test_pypsa_converter_columns_match_the_spec_schemas():
    from ce_pypsa.bundle import SCHEMA
    from ce_spec.schema import table_schema

    checked = 0
    for table, columns in SCHEMA.items():
        if table.startswith("x_"):
            continue
        spec = table_schema("bundle", table)
        assert spec is not None, table
        for col in columns:
            if col.name.startswith("x_"):
                continue
            sc = spec.column(col.name)
            assert sc is not None, f"{table}.{col.name} is not a column of the spec"
            assert sc.type == col.type, f"{table}.{col.name}: converter {col.type}, spec {sc.type}"
            assert sc.required == col.required, f"{table}.{col.name}: required differs"
            checked += 1
    assert checked > 50
