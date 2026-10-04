# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Opening run directories: discovery, formats, validation, legacy names, TOML stamps."""

from __future__ import annotations

import shutil

import pandas as pd
import pytest

from ce_results import Run, SchemaError
from ce_results.synthetic import write_synthetic_run


def test_opens_and_lists_tables(base_run):
    for name in ("investment", "lmp", "flows", "generation", "storage_state", "qa_checks"):
        assert base_run.has(name), name
    assert base_run.name == "synthetic_base"
    assert base_run.missing_tables == []
    assert base_run.table_info("lmp")["family"] == "price"
    assert base_run.time_axis.source == "manifest"


def test_not_a_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        Run(tmp_path / "absent")
    with pytest.raises(FileNotFoundError):
        Run(tmp_path, bundle=tmp_path / "absent")


def test_unknown_table_name_raises(base_run):
    with pytest.raises(KeyError, match="no table"):
        base_run.table("nope")


def test_solve_stamps(base_run, runs_dir):
    s = base_run.stamps
    assert s.objective == pytest.approx(1.234e9)
    assert s.gap == pytest.approx(4e-4)
    assert s.lower_bound == pytest.approx(1.234e9 * (1 - 4e-4))
    assert s.iterations == 6 and s.converged is True and s.stop_cause == "gap"
    assert s.certified(1e-3)
    assert not s.certified(1e-4)
    un = Run(write_synthetic_run(runs_dir / "unconverged", variant="unconverged")[0])
    assert un.stamps.stop_cause == "wall_cap"
    assert not un.stamps.certified(0.05)  # gap inside tolerance, but the run did not converge


def test_provenance(base_run):
    p = base_run.provenance
    assert p.format_version == "1.0" and p.mode == "cem"
    assert p.engine_version == "0.1.0"
    assert p.currency == "GBP" and p.dollar_year == 2025
    assert p.investment_years == (2030, 2035) and p.weather_years == (2012,)
    assert p.inputs["bundle_digest"].startswith("sha256:")
    assert p.register_version == "synthetic-register-0001"
    assert p.results_schema_version == 1


def test_qa_report(base_run):
    qa = base_run.qa
    assert qa.passed and qa.n_pass == 3 and qa.n_na == 1 and qa.n_required_na == 0
    assert len(qa.checks) == 4
    assert set(qa.checks["status"]) == {"PASS", "NA"}
    assert qa.tolerances["balance_mw"] == 1e-6


def test_no_qa_report(tmp_path):
    r = Run(write_synthetic_run(tmp_path / "noqa", with_qa=False)[0])
    assert r.qa is None


# ---------------------------------------------------------------- legacy layout

def test_legacy_columns_are_renamed(legacy_run):
    p = legacy_run.lmp()
    assert "lmp_per_mwh" in p.columns and "lmp_gbp_per_mwh" not in p.columns
    assert "bus_label" in p.columns
    assert "line_label" in legacy_run.flows().columns
    assert "varcost_per_mwh" in legacy_run.dispatch().columns
    av = legacy_run.availability()
    assert {"site_name", "cf"} <= set(av.columns)


def test_legacy_storage_energy_component(legacy_run):
    e = legacy_run.storage_energy()
    assert set(e["component"]) == {"storage_energy"}
    assert e.set_index("year")["built_mwh"][2035] == 160.0
    assert "storage_energy" not in set(legacy_run.builds()["component"])


def test_legacy_stamps_and_provenance(legacy_run):
    s, p = legacy_run.stamps, legacy_run.provenance
    assert s.objective == pytest.approx(1.234e9) and s.stop_cause == "gap"  # mapped from stop_reason
    assert s.integrality_gap == 0.0 and s.invariants_passed is True
    assert p.engine_version == "0.0.0-synthetic"
    assert p.investment_years == (2030, 2035)
    assert p.system_counts["buses"] == 3
    assert p.system_meta["data_vintage"] == "synthetic"
    assert p.register_version == "synthetic-register-0001"
    assert legacy_run.time_axis.source == "legacy tables"


def test_legacy_summary_keys_are_canonical(legacy_run):
    assert legacy_run.summary["cost"]["total"] == pytest.approx(1.234e9)
    assert "total_gbp" not in legacy_run.summary["cost"]
    assert "total_gbp" in legacy_run.document("summary")["cost"]  # the raw document is untouched
    assert legacy_run.summary["cost_decomposition"]["fuel"] == pytest.approx(0.35 * 1.234e9)


def test_stamps_fall_back_to_manifest_extra(tmp_path, legacy_path):
    d = tmp_path / "r"
    shutil.copytree(legacy_path, d)
    (d / "summary.toml").unlink()
    s = Run(d).stamps
    assert s.objective == pytest.approx(1.234e9)
    assert s.gap == pytest.approx(4e-4)
    assert s.mip_master is None  # only summary.toml carries it


# ---------------------------------------------------------------- validation and formats

def test_missing_required_column_raises(tmp_path, base_paths):
    d = tmp_path / "broken"
    shutil.copytree(base_paths[0], d)
    lmp = pd.read_parquet(d / "lmp.parquet").drop(columns=["lmp_per_mwh"])
    lmp.to_parquet(d / "lmp.parquet", index=False)
    with pytest.raises(SchemaError, match="lmp_per_mwh"):
        Run(d).lmp()
    assert "lmp_energy" in Run(d, validate=False).table("lmp").columns  # forensic read


def test_doubled_spelling_is_refused_like_the_spec(tmp_path, base_paths):
    """duplicate_spelling: a table carrying both lmp_gbp_per_mwh and lmp_per_mwh is refused, not resolved."""
    d = tmp_path / "doubled"
    shutil.copytree(base_paths[0], d)
    lmp = pd.read_parquet(d / "lmp.parquet")
    lmp["lmp_gbp_per_mwh"] = lmp["lmp_per_mwh"] + 1.0
    lmp.to_parquet(d / "lmp.parquet", index=False)
    with pytest.raises(SchemaError, match="duplicate_spelling"):
        Run(d).lmp()
    forensic = Run(d, validate=False).table("lmp")
    assert (forensic["lmp_per_mwh"] == lmp["lmp_per_mwh"]).all()  # without validation the canonical one is kept


def test_bad_value_raises(tmp_path, base_paths):
    d = tmp_path / "badval"
    shutil.copytree(base_paths[0], d)
    inv = pd.read_parquet(d / "investment.parquet")
    inv["built_mw"] = inv["built_mw"].astype(str)
    inv.loc[0, "built_mw"] = "lots"
    inv.to_parquet(d / "investment.parquet", index=False)
    with pytest.raises(SchemaError, match="built_mw"):
        Run(d).builds()


def test_csv_only_run_reads_like_parquet(tmp_path, base_run):
    csv = Run(write_synthetic_run(tmp_path / "csv", formats=("csv",))[0])
    pd.testing.assert_frame_equal(base_run.lmp(), csv.lmp(), check_dtype=False)
    b = csv.lmp()
    assert b["year"].dtype == "int64" and b["lmp_per_mwh"].dtype == "float64"
    assert b["floor_slack_at_bus"].dtype == bool


def test_gzipped_csv(tmp_path, base_paths):
    d = tmp_path / "gz"
    shutil.copytree(base_paths[0], d)
    pd.read_parquet(d / "flows.parquet").to_csv(d / "flows.csv.gz", index=False)
    (d / "flows.parquet").unlink()
    assert len(Run(d).flows(with_rating=False)) == 2 * 8 * 3  # years x periods x lines


def test_legacy_csv_timeindex_parses_timestamps(tmp_path):
    r = Run(write_synthetic_run(tmp_path / "csvchrono", layout="legacy", formats=("csv",),
                                chronological=True)[0])
    ti = r.table("timeindex")
    assert pd.api.types.is_datetime64_any_dtype(ti["timestamp"])
    assert ti["timestamp"].iloc[0] == pd.Timestamp("2030-01-01 00:00")
    assert r.time_mode == "chronological"


def test_arrow_run(tmp_path, base_run):
    r = Run(write_synthetic_run(tmp_path / "arrow", formats=("arrow",))[0])
    assert len(r.flows(with_rating=False)) == len(base_run.flows())


def test_parquet_preferred_over_csv_twin(tmp_path, base_paths):
    d = tmp_path / "twin"
    shutil.copytree(base_paths[0], d)
    inv = pd.read_parquet(d / "investment.parquet")
    inv.assign(built_mw=-1.0).to_csv(d / "investment.csv", index=False)  # a stale twin
    assert (Run(d).builds()["built_mw"] != -1.0).all()


def test_discovery_without_results_manifest(tmp_path):
    r = Run(write_synthetic_run(tmp_path / "noman", with_manifest=False)[0])
    assert r.has("lmp") and r.results_manifest == {}
    assert r.provenance.run_name == "synthetic_base"


def test_missing_tables_reported(tmp_path, base_paths):
    d = tmp_path / "partial"
    shutil.copytree(base_paths[0], d)
    (d / "storage_state.parquet").unlink()
    r = Run(d)
    assert r.missing_tables == ["storage_state"]
    with pytest.raises(KeyError):
        r.storage()


def test_unknown_table_passes_through(tmp_path, base_paths):
    d = tmp_path / "extra"
    shutil.copytree(base_paths[0], d)
    pd.DataFrame({"x": [1, 2]}).to_parquet(d / "custom_metric.parquet", index=False)
    assert Run(d).table("custom_metric")["x"].tolist() == [1, 2]


def test_column_selection_and_copies(base_run):
    df = base_run.table("lmp", columns=["year", "bus", "lmp_per_mwh"])
    assert list(df.columns) == ["year", "bus", "lmp_per_mwh"]
    a = base_run.table("lmp")
    a["lmp_per_mwh"] = 0.0
    assert (base_run.table("lmp")["lmp_per_mwh"] != 0).any()


def test_bundle_tables_need_a_bundle(base_paths):
    r = Run(base_paths[0])
    with pytest.raises(KeyError, match="no bundle"):
        r.bundle_table("lines")
    with pytest.raises(KeyError, match="line endpoints"):
        r.network()
    assert "rating_mw" not in r.flows().columns  # flows still load without the network
