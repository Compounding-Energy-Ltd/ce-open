# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Derived metrics checked against hand-worked numbers on the tiny run.

Tiny run (see conftest.write_tiny_run): annual_h = [2, 6];
LMP A = [10, 20], B = [30, 20] (B: congestion [15, 0], loss [5, 0]);
line L A->B rated 100, flow [100, -50]; unit G at A [50, 100]; wind W at B [40, 10],
W availability [0.5, 0.2] with 60 MW built. Line endpoints and availability come from the bundle.
"""

from __future__ import annotations

import pytest

from ce_results import (
    capture_prices,
    congestion_rent,
    curtailment_summary,
    generation_by_technology,
    mean_lmp_by_bus,
)


def test_weights(tiny_run):
    assert tiny_run.weights()["annual_h"].tolist() == pytest.approx([2.0, 6.0])


def test_mean_lmp_by_bus(tiny_run):
    m = mean_lmp_by_bus(tiny_run).set_index("bus")
    assert m.loc["A", "mean_lmp"] == pytest.approx((10 * 2 + 20 * 6) / 8)  # 17.5
    assert m.loc["B", "mean_lmp"] == pytest.approx((30 * 2 + 20 * 6) / 8)  # 22.5
    assert m.loc["B", "max_lmp"] == 30.0 and m.loc["A", "hours"] == 8.0


def test_capture_prices(tiny_run):
    c = capture_prices(tiny_run).set_index("component_name")
    assert c.loc["G", "energy_mwh"] == pytest.approx(50 * 2 + 100 * 6)          # 700
    assert c.loc["G", "revenue"] == pytest.approx(50 * 2 * 10 + 100 * 6 * 20)   # 13000
    assert c.loc["G", "capture_price"] == pytest.approx(13000 / 700)
    assert c.loc["G", "capture_ratio"] == pytest.approx((13000 / 700) / 17.5)
    assert c.loc["W", "capture_price"] == pytest.approx((40 * 2 * 30 + 10 * 6 * 20) / 140)


def test_capture_prices_filter(tiny_run):
    assert set(capture_prices(tiny_run, ["renewable"])["component_name"]) == {"W"}


def test_congestion_rent(tiny_run):
    r = congestion_rent(tiny_run).iloc[0]
    assert r["line"] == "L"
    assert r["merchandising_surplus"] == pytest.approx(100 * 2 * (30 - 10) + (-50) * 6 * 0)  # 4000
    assert r["congestion_rent"] == pytest.approx(100 * 2 * 15)                              # 3000
    assert r["loss_surplus"] == pytest.approx(100 * 2 * 5)                                   # 1000
    assert r["congestion_rent"] + r["loss_surplus"] == pytest.approx(r["merchandising_surplus"])
    assert r["energy_mwh"] == pytest.approx(100 * 2 + 50 * 6)
    assert r["mean_loading"] == pytest.approx((1.0 * 2 + 0.5 * 6) / 8)
    assert r["hours_at_limit"] == pytest.approx(2.0)


def test_congestion_rent_without_components(tiny_paths):
    from ce_results import Run

    run_dir, bundle = tiny_paths
    p = Run(run_dir).table("lmp").drop(columns=["lmp_energy", "lmp_congestion", "lmp_loss"])
    p.to_parquet(run_dir / "lmp.parquet", index=False)
    r = congestion_rent(Run(run_dir, bundle=bundle)).iloc[0]
    assert r["congestion_rent"] == pytest.approx(4000.0)
    assert r["loss_surplus"] != r["loss_surplus"]  # NaN


def test_congestion_rent_synthetic_split(base_run):
    r = congestion_rent(base_run)
    assert (r["congestion_rent"] + r["loss_surplus"] - r["merchandising_surplus"]).abs().max() < 1e-6
    l2 = r[(r["line"] == "l2") & (r["year"] == 2030)].iloc[0]
    assert l2["hours_at_limit"] == pytest.approx(2190.0)  # the evening periods, 1/4 of the year


def test_generation_by_technology(tiny_run):
    g = generation_by_technology(tiny_run).set_index("technology")["energy_mwh"]
    assert g["ccgt"] == pytest.approx(700.0) and g["wind"] == pytest.approx(140.0)


def test_curtailment_new_build_only_flags_shortfall(tiny_run):
    c = tiny_run.curtailment()
    # 60 MW built: available [30, 12]; dispatch [40, 10] -> period 1 exceeds availability
    assert c["available_mw"].tolist() == pytest.approx([30.0, 12.0])
    assert c["capacity_short"].tolist() == [True, False]
    assert c["curtailed_mw"].tolist() == pytest.approx([0.0, 2.0])
    assert any("existing capacity" in n for n in tiny_run.notes)


def test_curtailment_with_existing_capacity(tiny_run):
    s = curtailment_summary(tiny_run, existing_mw={"W": 40.0}).iloc[0]
    # capacity 100 MW: available [50, 20], curtailed [10, 10]
    assert s["available_mwh"] == pytest.approx(50 * 2 + 20 * 6)
    assert s["dispatch_mwh"] == pytest.approx(40 * 2 + 10 * 6)
    assert s["curtailed_mwh"] == pytest.approx(10 * 2 + 10 * 6)
    assert s["curtailment_rate"] == pytest.approx(80 / 220)
    assert s["capacity_short_h"] == 0.0
    assert s["bus"] == "B" and s["technology"] == "wind"
