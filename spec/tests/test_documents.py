# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""TOML document validation: types, enums, ranges, wildcards, unions, aliases, extensions."""

from __future__ import annotations

import datetime as dt
import tomllib

from conftest import EXAMPLES
from ce_spec.common import parse_axis
from ce_spec.documents import validate_document
from ce_spec.issues import Collector
from ce_spec.schema import doc_schema


def _check(doc: dict, scope: str, name: str) -> Collector:
    ctx = Collector()
    validate_document(doc, doc_schema(scope, name), ctx)
    return ctx


def _codes(ctx: Collector) -> set[str]:
    return {i.code for i in ctx.issues}


def _summary(**cost) -> dict:
    return {"cost": {"total": 3.0, "investment_capex": 1.0, "operational_opex": 2.0, **cost}}


def test_minimal_summary_is_valid():
    assert _codes(_check(_summary(), "results", "summary.toml")) == set()


def test_wildcard_map_accepts_any_key_of_the_declared_type():
    doc = _summary() | {"builds_mw_by_technology": {"solar_pv": 10.0, "made_up_tech": 3}}
    assert _codes(_check(doc, "results", "summary.toml")) == set()
    doc["builds_mw_by_technology"]["bad"] = "ten"
    assert _codes(_check(doc, "results", "summary.toml")) == {"bad_type"}


def test_bool_is_not_a_number():
    assert _codes(_check(_summary(total=True), "results", "summary.toml")) == {"bad_type"}


def test_enum_and_range():
    doc = _summary() | {"solve": {"stop_cause": "timeout", "relative_gap": -0.1}}
    assert _codes(_check(doc, "results", "summary.toml")) == {"bad_enum", "out_of_range"}


def test_unknown_key_and_extension():
    doc = _summary() | {"mystery": {"a": 1}, "x_vendor": {"anything": [1, "two"]}}
    ctx = _check(doc, "results", "summary.toml")
    assert _codes(ctx) == {"unknown_key"}
    assert ctx.issues[0].column == "mystery"


def test_missing_required_key():
    ctx = _check({"cost": {"total": 1.0, "investment_capex": 1.0}}, "results", "summary.toml")
    assert _codes(ctx) == {"missing_key"}


def test_legacy_key_spelling_is_renamed():
    doc = {"cost": {"total_gbp": 3.0, "investment_capex": 1.0, "operational_opex": 2.0}}
    ctx = _check(doc, "results", "summary.toml")
    assert _codes(ctx) == {"deprecated_spelling"} and doc["cost"]["total"] == 3.0
    doc = {"cost": {"total_gbp": 3.0, "total": 3.0, "investment_capex": 1.0, "operational_opex": 2.0}}
    assert _codes(_check(doc, "results", "summary.toml")) == {"duplicate_spelling"}


def test_union_type_and_array_of_tables():
    doc = tomllib.loads((EXAMPLES / "toy3" / "bundle.toml").read_text())
    doc["time"]["blocks"][0]["dt_h"] = [1.0] * 24
    assert _codes(_check(doc, "bundle", "bundle.toml")) == set()
    doc["time"]["blocks"][1]["dt_h"] = "hourly"
    doc["time"]["blocks"][1]["surprise"] = 1
    ctx = _check(doc, "bundle", "bundle.toml")
    assert _codes(ctx) == {"bad_type", "unknown_key"}
    assert {i.column for i in ctx.issues} == {"time.blocks[1].dt_h", "time.blocks[1].surprise"}


def test_datetime_needs_an_offset():
    doc = tomllib.loads((EXAMPLES / "toy3" / "bundle.toml").read_text())
    doc["bundle"]["created_utc"] = dt.datetime(2026, 1, 1)   # local, no offset
    assert _codes(_check(doc, "bundle", "bundle.toml")) == {"bad_type"}


def test_open_table_children_are_not_checked():
    doc = {"schema_version": 1, "run": "r", "tables": [
        {"name": "t", "formats": ["csv"], "nrows": 0, "columns": [], "provenance": {"anything": "goes", "n": 1}}]}
    assert _codes(_check(doc, "results", "results_manifest.toml")) == set()


def test_chronological_snippet_is_a_valid_descriptor():
    doc = tomllib.loads((EXAMPLES / "snippets" / "bundle_chronological_8760.toml").read_text())
    ctx = Collector()
    validate_document(doc, doc_schema("bundle", "bundle.toml"), ctx)
    ax = parse_axis(doc, ctx, "bundle.toml", blocks_required=True)
    assert ctx.issues == []
    assert ax.blocks == {1: 8784} and ax.weather_years == [2012, 2016, 2020]


def test_leap_day_rule():
    doc = tomllib.loads((EXAMPLES / "snippets" / "bundle_chronological_8760.toml").read_text())
    doc["time"]["leap_day"] = "drop"
    ctx = Collector()
    parse_axis(doc, ctx, "bundle.toml", blocks_required=True)
    assert _codes(ctx) == {"leap_day_mismatch"}
