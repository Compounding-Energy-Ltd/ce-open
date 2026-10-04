# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Bundle validation: the toy bundle is valid in every format, and every planted defect is reported by
its named code — and only by that code."""

from __future__ import annotations

import pyarrow as pa
import pytest

from conftest import EXAMPLES, edit_text, errors, mutate, refresh_prov, table_file, warnings
from ce_spec import validate_bundle
from ce_spec.bundle import load_bundle
from ce_spec.issues import Collector
from ce_spec.tables import read_raw, write_table


def test_toy_bundle_is_valid_in_every_format(any_format):
    b, _ = any_format
    rep = validate_bundle(b)
    assert rep.ok and rep.issues == [], rep.summary()


def test_checked_in_example_is_valid():
    rep = validate_bundle(EXAMPLES / "toy3")
    assert rep.ok and rep.issues == [], rep.summary()


def test_loaded_tables_are_canonical(bundle):
    lb = load_bundle(bundle, Collector())
    assert lb.tables["lines"].schema.field("id").type == pa.int32()
    assert lb.tables["storage"].column("self_discharge_frac_per_h").to_pylist() == [0.0]   # default filled
    assert lb.axis.n_scen == 2 and lb.axis.blocks == {1: 24, 2: 24}


def only(rep, code: str) -> None:
    assert errors(rep) == {code}, rep.summary()


# ---------------------------------------------------------------------------------------------- documents

def test_missing_descriptor(bundle):
    (bundle / "bundle.toml").unlink()
    only(validate_bundle(bundle), "missing_document")


def test_not_a_directory(tmp_path):
    only(validate_bundle(tmp_path / "nope"), "missing_document")


def test_descriptor_does_not_parse(bundle):
    edit_text(bundle / "bundle.toml", 'name = "toy3"', 'name = toy3')
    only(validate_bundle(bundle), "toml_parse_error")


@pytest.mark.parametrize("old,new,code", [
    ('name = "ce-bundle"', 'name = "something-else"', "bad_format_name"),
    ('version = "1.0"', 'version = "1"', "bad_version"),
    ('version = "1.0"', 'version = "2.0"', "unsupported_major_version"),
    ('version = "1.0"', 'version = "1.7"', "newer_minor_version"),
])
def test_format_gate_stops_validation(bundle, old, new, code):
    edit_text(bundle / "bundle.toml", old, new)
    (bundle / "rubbish.txt").write_text("would be unknown_file if validation went on")
    only(validate_bundle(bundle), code)


def test_unknown_key_refused_extension_ignored(bundle):
    p = bundle / "bundle.toml"
    p.write_text(p.read_text() + "\n[x_vendor]\nfoo = 1\n")
    assert validate_bundle(bundle).issues == []
    p.write_text(p.read_text() + "\n[mystery]\nfoo = 1\n")
    only(validate_bundle(bundle), "unknown_key")


def test_missing_money_section(bundle):
    edit_text(bundle / "bundle.toml", '[money]\ncurrency = "USD"\ndollar_year = 2024\n', "[money]\n")
    only(validate_bundle(bundle), "missing_key")


def test_bad_currency(bundle):
    edit_text(bundle / "bundle.toml", 'currency = "USD"', 'currency = "usd"')
    only(validate_bundle(bundle), "bad_currency")


def test_years_must_increase(bundle):
    edit_text(bundle / "bundle.toml", "investment_years = [2030, 2035]", "investment_years = [2035, 2030]")
    only(validate_bundle(bundle), "bad_axis")


def test_probabilities_must_sum_to_one(bundle):
    edit_text(bundle / "bundle.toml", "probability = [0.7, 0.3]", "probability = [0.7, 0.2]")
    only(validate_bundle(bundle), "probabilities_not_normalised")


def test_partial_year_is_only_a_warning(bundle):
    edit_text(bundle / "bundle.toml", "weight_h = 4380.0", "weight_h = 4000.0")
    rep = validate_bundle(bundle)
    assert rep.ok and warnings(rep) == {"block_weights_not_full_year"}


def test_chronological_block_needs_start(bundle):
    edit_text(bundle / "bundle.toml", 'kind = "representative"\nnperiods = 24\ndt_h = 1.0\nweight_h = 4380.0',
              'kind = "chronological"\nnperiods = 24\ndt_h = 1.0\nweight_h = 24.0')
    rep = validate_bundle(bundle)
    assert errors(rep) == {"missing_value"} and warnings(rep) == {"block_weights_not_full_year"}


def test_dt_list_length(bundle):
    edit_text(bundle / "bundle.toml", "dt_h = 1.0", "dt_h = [1.0, 1.0]")
    only(validate_bundle(bundle), "bad_axis")


# ---------------------------------------------------------------------------------------------- files

def test_missing_required_table(bundle):
    table_file(bundle, "demand").unlink()
    only(validate_bundle(bundle), "missing_table")


def test_unknown_table_refused_extension_ignored(bundle):
    write_table(pa.table({"a": [1]}), bundle / "x_private.parquet")
    assert validate_bundle(bundle).issues == []
    write_table(pa.table({"a": [1]}), bundle / "weather_stations.parquet")
    only(validate_bundle(bundle), "unknown_table")


def test_unknown_file_and_directory(bundle):
    (bundle / "notes.txt").write_text("hello")
    (bundle / "subdir").mkdir()
    (bundle / "x_cache").mkdir()
    (bundle / "README.md").write_text("documentation is allowed")
    rep = validate_bundle(bundle)
    assert errors(rep) == {"unknown_file"} and len(rep.errors) == 2


def test_ambiguous_table(bundle):
    write_table(read_raw(table_file(bundle, "buses")), bundle / "buses.csv")
    only(validate_bundle(bundle), "ambiguous_table")


def test_json_is_refused(bundle):
    (bundle / "candidates.json").write_text("[]")
    only(validate_bundle(bundle), "json_forbidden")


def test_unreadable_table(bundle):
    table_file(bundle, "peak_demand").write_bytes(b"PAR1 this is not parquet")
    only(validate_bundle(bundle), "unreadable_table")


# ---------------------------------------------------------------------------------------------- columns

def test_missing_required_column(bundle):
    mutate(bundle, "lines", lambda rows: [{k: v for k, v in r.items() if k != "rating_mw"} for r in rows])
    only(validate_bundle(bundle), "missing_column")


def test_unknown_column_refused_extension_ignored(bundle):
    mutate(bundle, "buses", lambda rows: [r | {"x_colour": "red"} for r in rows], types={"x_colour": pa.string()})
    assert validate_bundle(bundle).issues == []
    mutate(bundle, "buses", lambda rows: [r | {"colour": "red"} for r in rows], types={"colour": pa.string()})
    only(validate_bundle(bundle), "unknown_column")


def test_legacy_spelling_accepted_both_spellings_refused(bundle):
    def rename(rows):
        return [{("varcost_gbp_per_mwh" if k == "varcost_per_mwh" else k): v for k, v in r.items()} for r in rows]
    mutate(bundle, "generators", rename, types={"varcost_gbp_per_mwh": pa.float64()})
    rep = validate_bundle(bundle)
    assert rep.ok and warnings(rep) == {"deprecated_spelling"}
    lb = load_bundle(bundle, Collector())
    assert lb.tables["generators"].column("varcost_per_mwh").to_pylist() == [45.0, 120.0, 0.0]
    mutate(bundle, "generators", lambda rows: [r | {"varcost_per_mwh": 1.0} for r in rows],
           types={"varcost_gbp_per_mwh": pa.float64()})
    only(validate_bundle(bundle), "duplicate_spelling")


def test_engine_style_candidate_spellings(bundle):
    def rename(rows):
        m = {"candidate_id": "site_id", "bus": "bus_id", "interconnection_cost_per_kw": "interconnection_gbp_per_kw"}
        return [{m.get(k, k): v for k, v in r.items()} for r in rows]
    mutate(bundle, "candidates", rename, types={"site_id": pa.string(), "bus_id": pa.int64()})
    rep = validate_bundle(bundle)
    assert rep.ok, rep.summary()
    assert warnings(rep) == {"deprecated_spelling"} and len(rep.warnings) == 2


def test_ambiguous_legacy_cost_spelling_is_refused_not_renamed(bundle):
    """capex_gbp_per_mw has held overnight AND annualised costs in CE's history: never guess (SPEC.md 3.5)."""
    def rename(rows):
        return [{("capex_gbp_per_mw" if k == "capex_per_mw_yr" else k): v for k, v in r.items()} for r in rows]
    mutate(bundle, "candidates", rename, types={"capex_gbp_per_mw": pa.float64()})
    rep = validate_bundle(bundle)
    assert not rep.ok
    assert "ambiguous_legacy_cost" in errors(rep)
    hit = [i for i in rep.issues if i.code == "ambiguous_legacy_cost"]
    assert hit[0].column == "capex_gbp_per_mw" and "capex_overnight_per_mw" in hit[0].message
    assert "unknown_column" not in errors(rep)  # reported once, under its own code


def test_ambiguous_legacy_cost_spelling_is_refused_on_hvdc(bundle):
    def rename(rows):
        return [{("capex_gbp_per_mw" if k == "capex_per_mw_yr" else k): v for k, v in r.items()} for r in rows]
    mutate(bundle, "hvdc", rename, types={"capex_gbp_per_mw": pa.float64()})
    assert "ambiguous_legacy_cost" in errors(validate_bundle(bundle))


def test_schemas_never_alias_an_ambiguous_cost_spelling():
    from ce_spec.schema import table_schemas

    for scope in ("bundle", "results"):
        for ts in table_schemas(scope).values():
            assert not set(ts.aliases) & {"capex_gbp_per_mw", "capex_gbp_per_mwh"}, ts.name


def test_bad_type_in_csv_reports_column_and_row(csv_bundle):
    p = table_file(csv_bundle, "buses")
    lines = p.read_text().splitlines()
    lines[2] = lines[2].replace("2,", "two,", 1)
    p.write_text("\n".join(lines) + "\n")
    rep = validate_bundle(csv_bundle)
    assert "bad_type" in errors(rep)
    issue = next(i for i in rep.errors if i.code == "bad_type")
    assert (issue.table, issue.column, issue.rows) == ("buses", "id", (1,))


def test_repeated_csv_header(csv_bundle):
    p = table_file(csv_bundle, "peak_demand")
    lines = p.read_text().splitlines()
    lines = [ln + "," + ln.split(",")[-1] for ln in lines]   # repeat the last column, header included
    p.write_text("\n".join(lines) + "\n")
    only(validate_bundle(csv_bundle), "duplicate_spelling")


def test_bad_type_in_parquet(bundle):
    mutate(bundle, "lines", lambda rows: [r | {"rating_mw": str(r["rating_mw"])} for r in rows],
           types={"rating_mw": pa.string()})
    only(validate_bundle(bundle), "bad_type")


def test_null_in_required_column(bundle):
    mutate(bundle, "generators", lambda rows: [r | {"existing_mw": None} if r["id"] == 2 else r for r in rows])
    only(validate_bundle(bundle), "null_value")


def test_nan_counts_as_missing(bundle):
    mutate(bundle, "lines", lambda rows: [r | {"susceptance_pu": float("nan")} if r["id"] == 1 else r for r in rows])
    only(validate_bundle(bundle), "null_value")


def test_nan_text_in_a_csv_counts_as_null(csv_bundle):
    """SPEC.md 2.2: a NaN where a value is required is reported like an empty cell."""
    p = table_file(csv_bundle, "lines")
    lines = p.read_text().splitlines()
    head = lines[0].split(",")
    cells = lines[1].split(",")
    cells[head.index("susceptance_pu")] = "NaN"
    lines[1] = ",".join(cells)
    p.write_text("\n".join(lines) + "\n")
    refresh_prov(csv_bundle)
    only(validate_bundle(csv_bundle), "null_value")


def test_bad_enum(bundle):
    mutate(bundle, "generators", lambda rows: [r | {"kind": "nuclear"} if r["id"] == 1 else r for r in rows])
    only(validate_bundle(bundle), "bad_enum")


def test_out_of_range_cf(bundle):
    mutate(bundle, "availability", lambda rows: [r | {"cf": 1.2} if i == 7 else r for i, r in enumerate(rows)])
    rep = validate_bundle(bundle)
    only(rep, "out_of_range")
    assert rep.errors[0].rows == (7,)


def test_cf_tolerance(bundle):
    mutate(bundle, "availability", lambda rows: [r | {"cf": 1.0 + 5e-7} if i == 7 else r for i, r in enumerate(rows)])
    assert validate_bundle(bundle).ok


def test_exclusive_minimum(bundle):
    mutate(bundle, "storage", lambda rows: [r | {"duration_h": 0.0} for r in rows])
    only(validate_bundle(bundle), "out_of_range")


# ---------------------------------------------------------------------------------------------- keys

def test_duplicate_primary_key(bundle):
    mutate(bundle, "buses", lambda rows: [r | {"id": 1} if r["id"] == 3 else r for r in rows])
    rep = validate_bundle(bundle)
    assert "duplicate_key" in errors(rep)   # bus 3 references now dangle too
    assert errors(rep) <= {"duplicate_key", "dangling_reference"}


def test_duplicate_series_key(bundle):
    mutate(bundle, "demand", lambda rows: rows + [rows[0]])
    assert errors(validate_bundle(bundle)) == {"duplicate_key", "incomplete_series"}


def test_dangling_reference(bundle):
    mutate(bundle, "lines", lambda rows: [r | {"to_bus": 99} if r["id"] == 2 else r for r in rows])
    rep = validate_bundle(bundle)
    only(rep, "dangling_reference")
    assert rep.errors[0].column == "to_bus" and "99" in rep.errors[0].message


def test_unknown_availability_site(bundle):
    mutate(bundle, "availability", lambda rows: [r | {"site_name": "ghost"} if r["site_name"] == "wind_north" else r
                                                 for r in rows])
    assert errors(validate_bundle(bundle)) == {"dangling_reference", "missing_availability"}


def test_name_collision(bundle):
    mutate(bundle, "storage", lambda rows: [r | {"name": "ccgt_north"} for r in rows])
    only(validate_bundle(bundle), "name_collision")


def test_line_and_hvdc_names_share_one_name_space(bundle):
    """flows.line and network investment rows name either a line or a corridor: the names must differ."""
    line_name = read_raw(table_file(bundle, "lines")).column("name").to_pylist()[0]
    mutate(bundle, "hvdc", lambda rows: [r | {"name": line_name} for r in rows[:1]] + rows[1:])
    rep = validate_bundle(bundle)
    only(rep, "name_collision")
    assert "lines / hvdc" in next(i.message for i in rep.issues if i.code == "name_collision")


# ---------------------------------------------------------------------------------------------- semantics

def test_self_loop(bundle):
    mutate(bundle, "lines", lambda rows: [r | {"to_bus": r["from_bus"]} if r["id"] == 1 else r for r in rows])
    only(validate_bundle(bundle), "self_loop")


def test_inert_line_is_a_warning(bundle):
    mutate(bundle, "lines", lambda rows: [r | {"existing": False, "candidate": False} if r["id"] == 3 else r
                                          for r in rows])
    rep = validate_bundle(bundle)
    assert rep.ok and warnings(rep) == {"inert_line"}


def test_missing_capex(bundle):
    mutate(bundle, "candidates", lambda rows: [r | {"capex_per_mw_yr": None} if r["candidate_id"] == "ocgt_south_c1"
                                               else r for r in rows])
    only(validate_bundle(bundle), "missing_capex")


def test_overnight_cost_needs_annualisation(bundle):
    edit_text(bundle / "bundle.toml", "default_annualisation_rate = 0.07", "")
    only(validate_bundle(bundle), "overnight_cost_without_annualisation")
    mutate(bundle, "candidates", lambda rows: [r | {"annualisation_rate": 0.06, "lifetime_years": 30.0}
                                               if r["candidate_id"] == "hybrid_coast_c1" else r for r in rows])
    assert validate_bundle(bundle).ok


def test_interconnection_cost_needs_annualisation(bundle):
    edit_text(bundle / "bundle.toml", "default_annualisation_rate = 0.07", "")
    mutate(bundle, "candidates", lambda rows: [r | {"interconnection_cost_per_kw": 100.0}
                                               if r["candidate_id"] == "solar_south_c1" else r for r in rows])
    rep = validate_bundle(bundle)
    only(rep, "overnight_cost_without_annualisation")
    assert rep.errors[0].count == 2


@pytest.mark.parametrize("cid,col", [("hybrid_coast_c1", "storage_mw"), ("hybrid_coast_c1", "generation_mw"),
                                     ("ocgt_south_c1", "unit_size_mw"), ("battery_coast_c1", "duration_h")])
def test_conditionally_required_values(bundle, cid, col):
    mutate(bundle, "candidates", lambda rows: [r | {col: None} if r["candidate_id"] == cid else r for r in rows])
    only(validate_bundle(bundle), "missing_value")


def test_expandable_bus_needs_cost(bundle):
    mutate(bundle, "buses", lambda rows: [r | {"hosting_expand_cost_per_mw_yr": None} for r in rows])
    only(validate_bundle(bundle), "missing_value")


def test_inconsistent_interface(bundle):
    mutate(bundle, "interfaces", lambda rows: [r | {"max_fwd_mw": 999.0} if r["line_id"] == 3 else r for r in rows])
    only(validate_bundle(bundle), "inconsistent_interface")


def test_interface_with_unknown_line(bundle):
    mutate(bundle, "interfaces", lambda rows: [r | {"line_id": 42} if r["line_id"] == 3 else r for r in rows])
    only(validate_bundle(bundle), "dangling_reference")


def test_monthly_limit_month_range(bundle):
    mutate(bundle, "interface_limits_monthly", lambda rows: [r | {"month": 13} if r["month"] == 8 else r
                                                             for r in rows])
    only(validate_bundle(bundle), "out_of_range")


# ---------------------------------------------------------------------------------------------- series

def test_weather_without_year(bundle):
    def drop_year(rows):
        seen, out = set(), []
        for r in rows:
            r = {k: v for k, v in r.items() if k != "year"}
            key = (r["bus"], r["scenario"], r["block"], r["period"], r["weather"])
            if key not in seen:
                seen.add(key)
                out.append(r)
        return out
    mutate(bundle, "demand", drop_year)
    only(validate_bundle(bundle), "weather_without_year")


def test_year_not_in_horizon(bundle):
    mutate(bundle, "peak_demand", lambda rows: [r | {"year": 2040} if r["year"] == 2035 else r for r in rows])
    only(validate_bundle(bundle), "year_not_in_horizon")


def test_weather_not_declared(bundle):
    mutate(bundle, "weather_weights", lambda rows: [r | {"weather": 2020} if r["weather"] == 2019 else r
                                                    for r in rows])
    only(validate_bundle(bundle), "weather_not_declared")


def test_weather_column_needs_declared_weather_years(bundle):
    edit_text(bundle / "bundle.toml", "weather_years = [2018, 2019]\n", "")
    assert errors(validate_bundle(bundle)) == {"weather_not_declared"}


@pytest.mark.parametrize("col,value", [("block", 3), ("period", 25), ("scenario", 3)])
def test_axis_out_of_range(bundle, col, value):
    def plant(rows):
        rows[0] = rows[0] | {col: value}
        return rows
    mutate(bundle, "demand", plant)
    assert errors(validate_bundle(bundle)) == {"axis_out_of_range", "incomplete_series"}


def test_missing_period_is_incomplete(bundle):
    mutate(bundle, "demand", lambda rows: rows[:-1])
    rep = validate_bundle(bundle)
    only(rep, "incomplete_series")
    assert "periods 1..nperiods" in rep.errors[0].message


def test_missing_scenario_cell_is_incomplete(bundle):
    mutate(bundle, "availability", lambda rows: [r for r in rows if not (r["site_name"] == "wind_north"
                                                 and r["scenario"] == 2 and r["block"] == 2)])
    only(validate_bundle(bundle), "incomplete_series")


def test_id_missing_from_one_weather_group(bundle):
    mutate(bundle, "demand", lambda rows: [r for r in rows if not (r["bus"] == 3 and r["weather"] == 2019
                                           and r["year"] == 2035)])
    only(validate_bundle(bundle), "incomplete_series")


def test_investment_year_absent_from_series(bundle):
    mutate(bundle, "demand", lambda rows: [r for r in rows if r["year"] == 2030])
    rep = validate_bundle(bundle)
    only(rep, "incomplete_series")   # also: demand and availability now disagree on (year, weather)
    assert len(rep.errors) == 2


def test_missing_availability(bundle):
    mutate(bundle, "availability", lambda rows: [r for r in rows if r["site_name"] != "solar_south_c1"])
    rep = validate_bundle(bundle)
    only(rep, "missing_availability")
    assert "solar_south_c1" in rep.errors[0].message


def test_unweighted_year(bundle):
    mutate(bundle, "weather_weights", lambda rows: [r | {"weight": 0.0} if r["year"] == 2030 else r for r in rows])
    only(validate_bundle(bundle), "out_of_range")


# ---------------------------------------------------------------------------------------------- provenance

def test_tampered_file_breaks_hash(bundle):
    p = table_file(bundle, "candidates")
    raw = read_raw(p)
    rows = raw.to_pylist()
    rows[0]["max_mw"] = 501.0
    write_table(pa.Table.from_pylist(rows, schema=raw.schema), p)   # no prov refresh: the hash goes stale
    only(validate_bundle(bundle), "prov_hash_mismatch")


def test_rows_mismatch(bundle):
    sc = bundle / "candidates.parquet.prov"
    edit_text(sc, "rows=4", "rows=5")
    only(validate_bundle(bundle), "prov_rows_mismatch")


def test_prov_grammar_warnings(bundle):
    sc = bundle / "candidates.parquet.prov"
    sc.write_text(sc.read_text() + "legacy: style line\nsource=second value ignored\n")
    rep = validate_bundle(bundle)
    assert rep.ok and warnings(rep) == {"prov_unparsed_line", "prov_duplicate_key"}


def test_orphan_and_bad_timestamp(bundle):
    (bundle / "gone.parquet.prov").write_text("source=x\n")
    edit_text(bundle / "availability.parquet.prov", "built_utc=2026-10-01T00:00:00Z", "built_utc=last tuesday")
    rep = validate_bundle(bundle)
    assert rep.ok and warnings(rep) == {"prov_orphan", "prov_bad_timestamp"}


def test_stale_derived_table(bundle):
    edit_text(bundle / "candidates.parquet.prov", "generation=toy3-g1", "generation=toy3-g2")
    rep = validate_bundle(bundle)
    only(rep, "stale_derived_table")
    assert "toy3-g2" in rep.errors[0].message


def test_refresh_prov_keeps_fixture_valid(bundle):
    refresh_prov(bundle)
    assert validate_bundle(bundle).issues == []
