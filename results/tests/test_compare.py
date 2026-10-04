# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Comparing two runs."""

from __future__ import annotations

import pandas as pd
import pytest

from ce_results import Run, compare_runs
from ce_results.synthetic import write_synthetic_run


def _run(paths):
    return Run(paths[0], bundle=paths[1])


@pytest.fixture()
def cmp(base_paths, alt_paths):
    return compare_runs(_run(base_paths), _run(alt_paths))


def test_build_deltas(cmp):
    b = cmp.builds.set_index(["component", "technology"])
    assert b.loc[("renewable", "wind_onshore"), "delta"] == pytest.approx(75.0)  # 150 -> 225
    assert b.loc[("renewable", "solar_pv"), "delta"] == 0.0
    assert b.loc[("retirement", "coal"), "built_mw_a"] == -30.0


def test_cost_and_stamp_deltas(cmp):
    c = cmp.costs.set_index(["section", "item"])
    assert c.loc[("cost", "total"), "delta"] == pytest.approx(-0.03 * 1.234e9)
    assert c.loc[("cost", "total"), "rel_delta"] == pytest.approx(-0.03)
    s = cmp.stamps.set_index("metric")
    assert s.loc["iterations", "delta"] == 1
    assert pd.isna(s.loc["stop_cause", "delta"])  # no numeric delta for text stamps
    h = cmp.headline()
    assert h["total_cost_rel_delta"] == pytest.approx(-0.03) and h["max_gap"] == pytest.approx(6e-4)


def test_price_deltas(cmp):
    p = cmp.prices.set_index(["year", "bus"])
    # the alt variant shifts the energy price by -4; the loss component scales with it
    assert p.loc[(2030, "b1"), "delta"] == pytest.approx(-4.0)
    assert p.loc[(2030, "b3"), "delta"] == pytest.approx(-4.0 * 1.02)


def test_generation_deltas(cmp):
    g = cmp.generation.set_index(["year", "source_type", "technology"])
    assert g.loc[(2035, "renewable", "wind_onshore"), "delta"] > 0
    assert g.loc[(2035, "thermal", "coal"), "delta"] == pytest.approx(0.0)


def test_comparable_runs_have_no_warnings(cmp):
    assert cmp.comparable, cmp.warnings
    assert cmp.skipped == []


def test_identical_runs_are_inside_the_gap(base_paths):
    c = compare_runs(_run(base_paths), _run(base_paths))
    assert c.costs["delta"].abs().max() == 0.0
    assert any("inside the optimality gap" in w for w in c.warnings)


def test_unconverged_run_is_flagged(base_paths, tmp_path):
    un = _run(write_synthetic_run(tmp_path / "un", variant="unconverged"))
    c = compare_runs(_run(base_paths), un)
    assert any("not certified" in w and "wall_cap" in w for w in c.warnings)
    assert not c.comparable


def test_register_mismatch_is_flagged(base_paths, tmp_path):
    other = _run(write_synthetic_run(tmp_path / "reg", variant="alt", register_version="synthetic-register-0002"))
    c = compare_runs(_run(base_paths), other)
    assert any("register" in w for w in c.warnings)


def test_time_mode_mismatch_is_flagged(base_paths, chrono_paths):
    c = compare_runs(_run(base_paths), _run(chrono_paths))
    assert any("time modes differ" in w for w in c.warnings)


def test_all_years_nodal(base_paths, alt_paths):
    c = compare_runs(_run(base_paths), _run(alt_paths), builds_by=["component", "bus"], final_year_only=False)
    assert list(c.builds.columns[:3]) == ["year", "component", "bus"]
    w = c.builds.set_index(["year", "component", "bus"])
    assert w.loc[(2030, "renewable", "b3"), "delta"] == pytest.approx(50.0)


def test_missing_table_is_skipped(base_paths, tmp_path):
    rd, bd = write_synthetic_run(tmp_path / "nogen", variant="alt")
    (rd / "generation.parquet").unlink()
    c = compare_runs(_run(base_paths), Run(rd, bundle=bd))
    assert c.generation is None and any("generation" in s for s in c.skipped)


def test_legacy_and_1_0_runs_compare_cleanly(base_paths, legacy_path):
    """The same synthetic study in both layouts: every cost, build and price delta is zero."""
    c = compare_runs(_run(base_paths), Run(legacy_path))
    cost = c.costs[c.costs["section"] == "cost"]
    assert cost["delta"].abs().max() == 0.0
    assert c.builds["delta"].abs().max() == 0.0
    assert c.prices["delta"].abs().max() < 1e-9
