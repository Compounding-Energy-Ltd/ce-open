# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Typed accessors: builds, dispatch, prices, flows, weights, time, curtailment, ledger, xarray."""

from __future__ import annotations

import shutil

import numpy as np
import pandas as pd
import pytest

from ce_results import Run
from ce_results.synthetic import write_synthetic_run


def test_builds_cumulative_and_incremental(base_run):
    cum = base_run.builds()
    assert "storage_energy" not in set(cum["component"])
    final = cum[cum["year"] == 2035].set_index("name")["built_mw"]
    assert final["wind_b3"] == 150.0 and final["coal_b1"] == -30.0

    inc = base_run.builds(incremental=True)
    total = inc.groupby("name")["built_mw"].sum()
    for name, mw in final.items():
        assert total[name] == pytest.approx(mw)
    wind = inc[inc["name"] == "wind_b3"].set_index("year")["built_mw"]
    assert wind[2030] == 100.0 and wind[2035] == 50.0
    assert "l3" not in set(inc[inc["year"] == 2030]["name"])  # nothing built in 2030


def test_builds_grouped_and_filtered(base_run):
    by = base_run.builds(by=["bus", "technology", "year"])
    assert list(by.columns) == ["bus", "technology", "year", "built_mw"]
    row = by[(by["bus"] == "b3") & (by["technology"] == "li_ion") & (by["year"] == 2035)]
    assert row["built_mw"].item() == 40.0
    assert set(base_run.builds(component="retirement")["name"]) == {"coal_b1"}


def test_storage_energy(base_run):
    e = base_run.storage_energy()
    assert e.set_index("year")["built_mwh"][2035] == 160.0
    inc = base_run.storage_energy(incremental=True)
    assert inc.set_index("year")["built_mwh"].tolist() == [80.0, 80.0]


def test_weights_sum_to_a_year(base_run):
    w = base_run.weights()
    assert w.groupby("year")["annual_h"].sum().tolist() == pytest.approx([8760.0, 8760.0])
    # block 2 has unequal periods (dt 1, 2, 3, 2 h in an 8 h span): weight follows period length
    b2 = w[(w["year"] == 2030) & (w["block"] == 2)].sort_values("period")
    assert b2["annual_h"].tolist() == pytest.approx([547.5, 1095.0, 1642.5, 1095.0])
    assert set(w["discount"]) == {1.0, 0.78}  # from the bundle's discount factors


def test_legacy_weights_match_1_0(base_run, legacy_run):
    a = base_run.weights().drop(columns="weather")
    b = legacy_run.weights().drop(columns="weather")
    pd.testing.assert_frame_equal(a, b)


def test_chronological_times(chrono_paths):
    r = Run(*chrono_paths[:1], bundle=chrono_paths[1])
    assert r.time_mode == "chronological"
    assert r.weights().groupby("year")["annual_h"].sum().tolist() == pytest.approx([24.0, 24.0])
    p = r.lmp(with_time=True)
    assert p["period_start"].notna().all()
    assert str(p["period_start"].dt.tz) == "UTC"
    row = p[(p["year"] == 2035) & (p["period"] == 7)].iloc[0]
    assert row["period_start"] == pd.Timestamp("2012-01-01 06:00", tz="UTC")
    assert row["period_end"] == pd.Timestamp("2012-01-01 07:00", tz="UTC")  # hour-ending label


def test_legacy_chronological_times(tmp_path):
    r = Run(write_synthetic_run(tmp_path / "lc", layout="legacy", chronological=True)[0])
    p = r.lmp(with_time=True)
    assert pd.api.types.is_datetime64_any_dtype(p["period_start"])
    row = p[(p["year"] == 2035) & (p["period"] == 7)].iloc[0]
    assert row["period_start"] == pd.Timestamp("2035-01-01 06:00")


def test_representative_run_has_no_fabricated_timestamps(base_run, legacy_run):
    assert base_run.time_mode == "representative"
    assert base_run.lmp(with_time=True)["period_start"].isna().all()
    assert legacy_run.lmp(with_time=True)["period_end"].isna().all()


def test_equal_weather_weights_by_default(tmp_path):
    rd, bd = write_synthetic_run(tmp_path / "w2", weather_draws=2)
    r = Run(rd, bundle=bd)
    w = r.weights()
    assert w.groupby("year")["annual_h"].sum().tolist() == pytest.approx([8760.0, 8760.0])
    assert sorted(w["weather"].unique()) == [2012, 2013]
    assert any("weather" in n for n in r.notes)


@pytest.mark.parametrize("layout", ["1.0", "legacy"])
def test_weather_weights_are_normalised(tmp_path, layout):
    rd, bd = write_synthetic_run(tmp_path / layout, layout=layout, weather_draws=2, weather_weights=[7, 3])
    r = Run(rd, bundle=bd)
    by = r.weights().query("year == 2030").groupby("weather")["annual_h"].sum()
    assert by.iloc[0] == pytest.approx(0.7 * 8760) and by.iloc[1] == pytest.approx(0.3 * 8760)
    assert not any("weather" in n for n in r.notes)


def test_weather_axis_ordinal(tmp_path):
    """A run spelling weather as ordinals still finds label-keyed weights."""
    rd, bd = write_synthetic_run(tmp_path / "ord", weather_draws=2, weather_weights=[1, 3])
    for name in ("lmp", "flows", "generation", "storage_state"):
        df = pd.read_parquet(rd / f"{name}.parquet")
        df["weather"] = df["weather"] - 2011
        df.to_parquet(rd / f"{name}.parquet", index=False)
    text = (rd / "manifest.toml").read_text().replace('weather_axis = "label"', 'weather_axis = "ordinal"')
    (rd / "manifest.toml").write_text(text)
    r = Run(rd, bundle=bd)
    by = r.weights().query("year == 2030").groupby("weather")["annual_h"].sum()
    assert by[1] == pytest.approx(0.25 * 8760) and by[2] == pytest.approx(0.75 * 8760)


def test_lmp_components_sum(base_run):
    assert base_run.lmp_component_residual() < 1e-9
    p = base_run.lmp()
    assert (p.loc[p["bus"] == "b1", "lmp_congestion"] == 0).all()


def test_network_from_bundle_and_legacy(base_run, legacy_run):
    a = base_run.network().set_index("line")
    b = legacy_run.network().set_index("line")
    pd.testing.assert_frame_equal(a[["from_bus", "to_bus", "rating_mw"]], b[["from_bus", "to_bus", "rating_mw"]],
                                  check_dtype=False)


def test_flows_with_rating(base_run):
    f = base_run.flows()
    assert {"from_bus", "to_bus", "rating_mw", "loading"} <= set(f.columns)
    assert f[f["line"] == "l2"]["loading"].max() == pytest.approx(1.0)
    assert (f[f["line"] == "l3"]["flow_mw"] < 0).any()  # signed flows are kept


def test_dispatch_filters(base_run):
    d = base_run.dispatch("renewable")
    assert set(d["source_type"]) == {"renewable"}
    both = base_run.dispatch(["storage_charge", "storage_discharge"])
    assert set(both["source_type"]) == {"storage_charge", "storage_discharge"}
    assert (both["dispatch_mw"] > 0).all()
    w = base_run.dispatch("thermal", with_weights=True)
    assert {"annual_h", "dt_h"} <= set(w.columns)


def test_storage_state_consistent(base_run):
    s = base_run.storage()
    per = base_run.time_axis.periods[["block", "period", "dt_h"]]
    s = s.merge(per, on=["block", "period"]).sort_values(["year", "weather", "block", "period"])
    for _, g in s.groupby(["year", "weather", "block"]):
        step = ((g["charge_mw"] - g["discharge_mw"]) * g["dt_h"]).to_numpy()
        assert np.allclose(np.diff(g["soc_mwh"].to_numpy()), step[1:])


def test_curtailment_derived_matches_written_table(tmp_path):
    rd, bd = write_synthetic_run(tmp_path / "ct", with_curtailment=True)
    written = Run(rd, bundle=bd).curtailment()
    (rd / "curtailment.parquet").unlink()
    derived = Run(rd, bundle=bd).curtailment()
    m = written.merge(derived, on=["year", "scenario", "weather", "block", "period", "site_name"],
                      suffixes=("_w", "_d"))
    assert len(m) == len(written) == len(derived)
    for c in ("available_mw", "dispatch_mw", "curtailed_mw"):
        assert np.allclose(m[f"{c}_w"], m[f"{c}_d"])
    assert not derived["capacity_short"].any()  # existing capacity came from the bundle


def test_curtailment_total_matches_summary(base_run):
    from ce_results import curtailment_summary

    total = curtailment_summary(base_run)["curtailed_mwh"].sum()
    assert total == pytest.approx(base_run.summary["energy"]["curtailed_mwh"])


def test_curtailment_legacy_needs_existing_capacity(legacy_run):
    c = legacy_run.curtailment()
    old = c[c["site_name"] == "wind_old_b3"]
    assert old["capacity_short"].all()  # dispatching with no known capacity
    fixed = legacy_run.curtailment(existing_mw={"wind_old_b3": 40.0})
    assert not fixed["capacity_short"].any()


def test_curtailment_with_weather_specific_availability(tmp_path):
    rd, bd = write_synthetic_run(tmp_path / "cw", weather_draws=2, with_curtailment=True)
    written = Run(rd, bundle=bd).curtailment()
    (rd / "curtailment.parquet").unlink()
    derived = Run(rd, bundle=bd).curtailment()
    assert np.allclose(written.sort_values(list(written.columns[:6]))["curtailed_mw"],
                       derived.sort_values(list(derived.columns[:6]))["curtailed_mw"])


def test_cost_ledger(base_run, legacy_run):
    led = base_run.cost_ledger().set_index(["section", "item"])["value"]
    assert led[("cost", "total")] == pytest.approx(1.234e9)
    assert led[("cost", "investment_capex")] + led[("cost", "operational_opex")] == \
        pytest.approx(led[("cost", "total")])
    leg = legacy_run.cost_ledger().set_index(["section", "item"])["value"]
    assert leg[("cost", "total")] == pytest.approx(1.234e9)
    assert leg[("qa_cost_ledger", "opex_by_class.fuel")] == pytest.approx(0.35 * 1.234e9)
    assert leg[("cost_decomposition", "fuel")] == pytest.approx(0.35 * 1.234e9)


def test_convergence(base_run):
    c = base_run.convergence()
    assert c["iteration"].tolist() == list(range(1, 7))
    assert (c["lb"] <= c["ub"]).all()
    assert c["gap"].iloc[-1] == pytest.approx(base_run.stamps.gap)


def test_demand_sources(base_run, legacy_run):
    a = base_run.demand().sort_values(["block", "period", "bus"]).reset_index(drop=True)
    b = legacy_run.demand().sort_values(["block", "period", "bus"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(a[["bus", "scenario", "block", "period", "demand_mw"]],
                                  b[["bus", "scenario", "block", "period", "demand_mw"]], check_dtype=False)


def test_availability_sources(base_run, legacy_run):
    assert base_run.availability()["cf"].between(0, 1).all()
    assert set(legacy_run.availability()["site_name"]) == set(base_run.availability()["site_name"])
    assert set(legacy_run.demand()["bus"]) == {"b1", "b2", "b3"}


def test_to_xarray(base_run):
    pytest.importorskip("xarray")
    da = base_run.to_xarray("lmp", "lmp_per_mwh")
    assert da.dims == ("year", "scenario", "weather", "block", "period", "bus")
    expect = base_run.lmp().query("year == 2030 and block == 1 and period == 1 and bus == 'b1'")["lmp_per_mwh"]
    assert da.sel(year=2030, scenario=1, weather=2012, block=1, period=1, bus="b1").item() == \
        pytest.approx(expect.item())
    with pytest.raises(ValueError, match="uniquely"):
        base_run.to_xarray(base_run.lmp(), "lmp_per_mwh", dims=["year", "bus"])


def test_no_time_axis_falls_back_to_one_hour(tmp_path, base_paths):
    d = tmp_path / "notime"
    shutil.copytree(base_paths[0], d)
    text = (d / "manifest.toml").read_text()
    (d / "manifest.toml").write_text(text.split("[time]")[0])  # drop the time axis and everything after
    r = Run(d)
    assert r.time_axis is None
    assert (r.weights()["annual_h"] == 1.0).all()
    assert any("no time axis" in n for n in r.notes)
