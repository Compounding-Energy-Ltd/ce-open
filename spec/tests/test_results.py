# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Results validation: the toy run is valid; declared identities, sign conventions, the QA derivation and
the table index are enforced; unknowns are warnings unless strict."""

from __future__ import annotations

import pyarrow as pa
import pytest

from conftest import EXAMPLES, edit_text, errors, mutate, table_file, warnings
from ce_spec import validate_results
from ce_spec.results import qa_overall


def r_mutate(root, name, fn, **kw):
    mutate(root, name, fn, scope="results", **kw)
    # keep the index consistent unless the test is about the index
    idx = root / "results_manifest.toml"
    if idx.exists():
        idx.unlink()


def only(rep, code):
    assert errors(rep) == {code}, rep.summary()


def test_toy_results_valid_in_every_format(any_format):
    b, r = any_format
    for strict in (False, True):
        rep = validate_results(r, bundle=b, strict=strict)
        assert rep.ok and rep.issues == [], rep.summary()


def test_checked_in_example_is_valid():
    rep = validate_results(EXAMPLES / "toy3_run", bundle=EXAMPLES / "toy3", strict=True)
    assert rep.ok and rep.issues == [], rep.summary()


@pytest.mark.parametrize("doc", ["manifest.toml", "summary.toml"])
def test_required_documents(run, doc):
    (run / doc).unlink()
    only(validate_results(run), "missing_document")


def test_required_investment_table(run):
    table_file(run, "investment").unlink()
    (run / "results_manifest.toml").unlink()
    only(validate_results(run), "missing_table")


def test_wrong_format_name(run):
    edit_text(run / "manifest.toml", 'name = "ce-results"', 'name = "ce-bundle"')
    only(validate_results(run), "bad_format_name")


def test_newer_minor_is_a_warning_unless_strict(run):
    edit_text(run / "manifest.toml", 'version = "1.0"', 'version = "1.4"')
    rep = validate_results(run)
    assert rep.ok and warnings(rep) == {"newer_minor_version"}
    only(validate_results(run, strict=True), "newer_minor_version")


def test_unknowns_warn_by_default_and_refuse_when_strict(run):
    p = run / "summary.toml"
    p.write_text(p.read_text() + "\n[cost_decomposition]\nens = 1.0\n")
    pa_t = pa.table({"x": [1]})
    import pyarrow.parquet as pq
    pq.write_table(pa_t, run / "price_duration_curve.parquet")
    (run / "summary.json").write_text("{}")
    lenient = validate_results(run)
    assert lenient.ok and warnings(lenient) == {"unknown_key", "unknown_table", "json_forbidden"}
    strict = validate_results(run, strict=True)
    assert errors(strict) == {"unknown_key", "unknown_table", "json_forbidden"}


def test_unknown_enum_is_an_error_even_when_lenient(run):
    r_mutate(run, "generation", lambda rows: [r | {"source_type": "import"} if i == 0 else r
                                              for i, r in enumerate(rows)])
    only(validate_results(run), "bad_enum")


def test_cost_identity(run):
    edit_text(run / "summary.toml", "total = 475000000.0", "total = 476000000.0")
    only(validate_results(run), "cost_identity_mismatch")


def test_stop_cause_consistency_is_a_warning(run):
    edit_text(run / "summary.toml", 'stop_cause = "gap"', 'stop_cause = "wall_cap"')
    rep = validate_results(run)
    assert rep.ok and warnings(rep) == {"stop_cause_inconsistent"}


def test_engine_style_summary_keys_are_legacy_spellings(run):
    edit_text(run / "summary.toml", "total = 475000000.0", "total_gbp = 475000000.0")
    edit_text(run / "summary.toml", "lower_bound = ", "lower_bound_gbp = ")
    rep = validate_results(run)
    assert rep.ok and warnings(rep) == {"deprecated_spelling"}


def test_lmp_decomposition(run):
    r_mutate(run, "lmp", lambda rows: [r | {"lmp_congestion": r["lmp_congestion"] + 1.0} if i == 5 else r
                                       for i, r in enumerate(rows)])
    rep = validate_results(run)
    only(rep, "lmp_decomposition_mismatch")
    assert rep.errors[0].rows == (5,)


def test_partial_decomposition(run):
    r_mutate(run, "lmp", lambda rows: [{k: v for k, v in r.items() if k != "lmp_loss"} for r in rows])
    only(validate_results(run), "partial_decomposition")


def test_legacy_lmp_spelling(run):
    r_mutate(run, "lmp", lambda rows: [{("lmp_gbp_per_mwh" if k == "lmp_per_mwh" else k): v for k, v in r.items()}
                                       for r in rows], types={"lmp_gbp_per_mwh": pa.float64()})
    rep = validate_results(run)
    assert rep.ok and warnings(rep) == {"deprecated_spelling"}


def test_curtailment_identity(run):
    r_mutate(run, "curtailment", lambda rows: [r | {"curtailed_mw": r["curtailed_mw"] + 2.0} if i == 3 else r
                                               for i, r in enumerate(rows)])
    only(validate_results(run), "curtailment_identity_mismatch")


def test_negative_dispatch(run):
    r_mutate(run, "generation", lambda rows: [r | {"dispatch_mw": -5.0} if i == 0 else r for i, r in enumerate(rows)])
    only(validate_results(run), "out_of_range")


def test_solver_noise_within_tolerance(run):
    r_mutate(run, "generation", lambda rows: [r | {"dispatch_mw": -1e-9} if i == 0 else r for i, r in enumerate(rows)])
    assert validate_results(run).ok


def test_retirement_sign(run):
    r_mutate(run, "investment", lambda rows: [r | {"built_mw": 50.0} if r["component"] == "retirement" else r
                                              for r in rows])
    only(validate_results(run), "sign_convention")


def test_negative_build(run):
    r_mutate(run, "investment", lambda rows: [r | {"built_mw": -1.0} if r["component"] == "storage" else r
                                              for r in rows])
    only(validate_results(run), "sign_convention")


def test_storage_energy_rows_need_mwh(run):
    r_mutate(run, "investment", lambda rows: rows + [{"year": 2035, "component": "storage_energy",
                                                      "name": "battery_coast_c1", "technology": "battery",
                                                      "bus": "coast", "built_mw": None, "built_mwh": None}])
    only(validate_results(run), "missing_value")


def test_year_outside_run(run):
    r_mutate(run, "investment", lambda rows: [r | {"year": 2040} if i == 0 else r for i, r in enumerate(rows)])
    only(validate_results(run), "year_not_in_horizon")


def test_period_beyond_block(run):
    r_mutate(run, "flows", lambda rows: [r | {"period": 25} if i == 0 else r for i, r in enumerate(rows)])
    only(validate_results(run), "axis_out_of_range")


def test_weather_axis_declaration(run):
    edit_text(run / "manifest.toml", 'weather_axis = "label"', 'weather_axis = "ordinal"')
    rep = validate_results(run)
    only(rep, "weather_not_declared")
    assert "ordinal" in rep.errors[0].message


def test_qa_vacuous_pass(run):
    r_mutate(run, "qa_checks", lambda rows: [r | {"status": "PASS"} if r["status"] == "NA" else r for r in rows])
    rep = validate_results(run)
    assert errors(rep) == {"qa_vacuous_pass", "qa_status_inconsistent"}


def test_qa_status_derivation(run):
    edit_text(run / "qa_report.toml", 'status = "PASS"', 'status = "WARN"')
    only(validate_results(run), "qa_status_inconsistent")


def test_qa_rule():
    rows = [{"status": "PASS", "required": True}, {"status": "NA", "required": True}]
    assert qa_overall(rows)["status"] == "INCOMPLETE"
    assert qa_overall(rows + [{"status": "WARN", "required": False}])["status"] == "WARN"
    assert qa_overall(rows + [{"status": "FAIL", "required": False}])["status"] == "FAIL"
    assert qa_overall([{"status": "NA", "required": False}])["status"] == "PASS"


def test_index_row_count(run):
    edit_text(run / "results_manifest.toml", "nrows = 8\n", "nrows = 9\n")
    only(validate_results(run), "index_mismatch")


def test_index_must_list_every_table(run):
    edit_text(run / "results_manifest.toml", 'name = "flows"', 'name = "flows_renamed"')
    rep = validate_results(run)
    only(rep, "index_mismatch")
    assert len(rep.errors) == 2   # listed-but-absent and present-but-unlisted


def test_currency_mismatch_against_bundle(run_and_bundle):
    run, b = run_and_bundle
    edit_text(run / "manifest.toml", 'currency = "USD"', 'currency = "EUR"')
    only(validate_results(run, bundle=b), "currency_mismatch")


def test_unknown_bus_against_bundle(run_and_bundle):
    run, b = run_and_bundle
    r_mutate(run, "lmp", lambda rows: [r | {"bus": "atlantis"} if i == 0 else r for i, r in enumerate(rows)])
    only(validate_results(run, bundle=b), "dangling_reference")


def test_flow_over_rating_is_a_warning(run_and_bundle):
    run, b = run_and_bundle
    r_mutate(run, "flows", lambda rows: [r | {"flow_mw": -400.0} if r["line"] == "north_coast" and i < 50 else r
                                         for i, r in enumerate(rows)])
    rep = validate_results(run, bundle=b)
    assert rep.ok and warnings(rep) == {"flow_exceeds_rating"}


def test_prov_in_results(run):
    p = table_file(run, "investment")
    from ce_spec.prov import format_prov, sha256_file
    (run / (p.name + ".prov")).write_text(format_prov({"rows": 8, "sha256": sha256_file(p)}))
    assert validate_results(run, strict=True).issues == []
    (run / (p.name + ".prov")).write_text(format_prov({"rows": 3}))
    only(validate_results(run), "prov_rows_mismatch")


def test_example_reports_its_hybrid_per_part():
    """SPEC.md 9.4: a built hybrid is a renewable row and a storage row under its candidate_id."""
    from ce_spec.tables import read_raw

    inv = read_raw(EXAMPLES / "toy3_run" / "investment.csv").to_pylist()
    rows = {r["component"]: r for r in inv if r["name"] == "hybrid_coast_c1"}
    assert set(rows) == {"renewable", "storage"}
    assert {r["technology"] for r in rows.values()} == {"solar_pv_battery"}
    sto = read_raw(EXAMPLES / "toy3_run" / "storage_state.csv").column("storage").to_pylist()
    gen = read_raw(EXAMPLES / "toy3_run" / "generation.csv").to_pylist()
    assert "hybrid_coast_c1" in sto
    assert {g["source_type"] for g in gen if g["component_name"] == "hybrid_coast_c1"} == {
        "renewable", "storage_charge", "storage_discharge"}
