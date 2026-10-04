# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Synthetic test systems (every number is invented).

* :func:`write_clean_network` writes a PyPSA CSV folder that uses only
  features with an exact CE representation, so PyPSA -> CE -> PyPSA must
  reproduce it.
* :func:`clean_bundle` builds a CE bundle that uses only features with an
  exact PyPSA representation, so CE -> PyPSA -> CE must reproduce it.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from ce_pypsa import Bundle

HOURS = 24


def shapes(n: int = HOURS) -> dict[str, np.ndarray]:
    h = np.arange(n)
    return {
        "solar": np.round(np.clip(np.sin((h % 24 - 6) / 12 * np.pi), 0, None) * 0.75, 4),
        "wind": np.round(0.5 + 0.3 * np.cos((h - 4) / 24 * 2 * np.pi), 4),
        "load": np.round(0.85 + 0.15 * np.sin((h % 24 - 8) / 24 * 2 * np.pi), 4),
    }


def write_clean_network(folder: Path, *, committable: bool = True, hours: int = HOURS,
                        weight: float | None = None) -> Path:
    """A three-bus network whose every attribute survives PyPSA -> CE -> PyPSA."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    weight = 8760.0 / hours if weight is None else weight
    snaps = [str(t) for t in pd.date_range("2030-03-01", periods=hours, freq="h")]
    s = shapes(hours)
    pd.DataFrame([{"name": "Clean", "_multi_invest": 0}]).to_csv(folder / "network.csv", index=False)
    pd.DataFrame({"snapshot": snaps, "objective": weight, "stores": 1.0, "generators": weight}).to_csv(
        folder / "snapshots.csv", index_label="")

    def static(name, rows):
        pd.DataFrame(rows).set_index("name").to_csv(folder / f"{name}.csv")

    def series(name, cols):
        pd.DataFrame(cols, index=pd.Index(snaps, name="snapshot")).to_csv(folder / f"{name}.csv")

    static("buses", [
        {"name": "A", "v_nom": 380.0, "x": 10.0, "y": 50.0, "carrier": "AC", "location": "west"},
        {"name": "B", "v_nom": 380.0, "x": 11.0, "y": 50.5, "carrier": "AC", "location": "east"},
        {"name": "C", "v_nom": 220.0, "x": 10.5, "y": 49.5, "carrier": "AC", "location": "east"},
    ])
    static("carriers", [{"name": n, "co2_emissions": e} for n, e in
                        (("CCGT", 0.35), ("OCGT", 0.5), ("nuclear", 0.0), ("onwind", 0.0), ("solar", 0.0),
                         ("battery", 0.0), ("PHS", 0.0))])
    static("lines", [
        {"name": "A-B", "bus0": "A", "bus1": "B", "x": 30.0, "s_nom": 900.0, "s_nom_extendable": False},
        {"name": "B-C", "bus0": "B", "bus1": "C", "x": 12.0, "s_nom": 500.0, "s_nom_extendable": True,
         "s_nom_min": 500.0, "s_nom_max": 1500.0, "capital_cost": 25.0, "length": 80.0},
        {"name": "A-C", "bus0": "A", "bus1": "C", "x": 18.0, "s_nom": 0.0, "s_nom_extendable": True,
         "s_nom_min": 0.0, "s_nom_max": 700.0, "capital_cost": 40.0},
    ])
    static("links", [
        {"name": "A-B DC", "bus0": "A", "bus1": "B", "p_nom": 300.0, "p_min_pu": -1.0, "efficiency": 0.97,
         "carrier": "DC", "p_nom_extendable": True, "p_nom_min": 300.0, "p_nom_max": 1000.0, "capital_cost": 90.0},
    ])
    static("generators", [
        {"name": "A CCGT", "bus": "A", "carrier": "CCGT", "p_nom": 900.0, "marginal_cost": 65.0,
         "ramp_limit_up": 0.5, "ramp_limit_down": 0.5},
        {"name": "B nuclear", "bus": "B", "carrier": "nuclear", "p_nom": 600.0, "marginal_cost": 11.0,
         "committable": committable, "p_min_pu": 0.4 if committable else 0.0,
         "min_up_time": 4 if committable else 0, "min_down_time": 3 if committable else 0,
         "start_up_cost": 5000.0 if committable else 0.0, "stand_by_cost": 100.0 if committable else 0.0},
        {"name": "C OCGT", "bus": "C", "carrier": "OCGT", "p_nom": 100.0, "p_nom_extendable": True,
         "p_nom_min": 100.0, "p_nom_max": 900.0, "marginal_cost": 120.0, "capital_cost": 42000.0},
        {"name": "A wind", "bus": "A", "carrier": "onwind", "p_nom": 1200.0, "marginal_cost": 0.5},
        {"name": "C solar", "bus": "C", "carrier": "solar", "p_nom": 0.0, "p_nom_extendable": True,
         "p_nom_min": 0.0, "p_nom_max": 3000.0, "marginal_cost": 0.0, "capital_cost": 50000.0},
    ])
    series("generators-p_max_pu", {"A wind": s["wind"], "C solar": s["solar"]})
    static("storage_units", [
        {"name": "B battery", "bus": "B", "carrier": "battery", "p_nom": 100.0, "p_nom_extendable": True,
         "p_nom_min": 100.0, "p_nom_max": 600.0, "max_hours": 4.0, "efficiency_store": 0.92,
         "efficiency_dispatch": 0.93, "standing_loss": 0.001, "cyclic_state_of_charge": True,
         "capital_cost": 60000.0, "marginal_cost": 0.2},
        {"name": "A PHS", "bus": "A", "carrier": "PHS", "p_nom": 250.0, "max_hours": 8.0, "efficiency_store": 0.88,
         "efficiency_dispatch": 0.9, "cyclic_state_of_charge": True},
    ])
    static("loads", [{"name": b, "bus": b} for b in ("A", "B", "C")])
    series("loads-p_set", {"A": np.round(900 * s["load"], 3), "B": np.round(1300 * s["load"], 3),
                           "C": np.round(500 * s["load"], 3)})
    static("global_constraints", [{"name": "CO2Limit", "type": "primary_energy", "carrier_attribute": "co2_emissions",
                                   "sense": "<=", "constant": 2.5e6}])
    return folder


def clean_bundle() -> Bundle:
    """A two-block CE bundle whose every value survives CE -> PyPSA -> CE (with ``block_hours=24``)."""
    s = shapes()
    blocks = [
        {"id": 1, "name": "block_1", "nperiods": HOURS, "dt_h": 1.0, "weight_h": 4380.0, "kind": "representative",
         "start": dt.datetime(2035, 1, 10, tzinfo=dt.timezone.utc)},
        {"id": 2, "name": "block_2", "nperiods": HOURS, "dt_h": 1.0, "weight_h": 4380.0, "kind": "representative",
         "start": dt.datetime(2035, 7, 10, tzinfo=dt.timezone.utc)},
    ]
    descriptor = {
        "format": {"name": "ce-bundle", "version": "1.0"},
        "bundle": {"name": "clean_ce", "description": "Synthetic.", "source": "invented"},
        "money": {"currency": "GBP", "dollar_year": 2025},
        "time": {"timezone": "UTC", "hour_convention": "hour_ending", "investment_years": [2035], "blocks": blocks},
        "scenarios": {"names": ["base"], "probability": [1.0]},
        "system": {"voll_per_mwh": 6000.0, "planning_reserve_margin": 0.0, "discount_factors": [1.0]},
        "x_policy": [{"name": "cap", "kind": "emission_cap_t", "value": 1.5e6, "pollutant": "co2"}],
    }
    buses = pd.DataFrame({"id": [1, 2, 3], "name": ["n1", "n2", "n3"], "zone": ["z1", "z2", "z2"],
                          "lat": [55.0, 54.0, 53.5], "lon": [-3.0, -2.0, -1.0], "voltage_kv": [400.0, 400.0, 275.0]})
    lines = pd.DataFrame({
        "id": [1, 2, 3], "name": ["l12", "l23", "l13"], "from_bus": [1, 2, 1], "to_bus": [2, 3, 3],
        "susceptance_pu": [25.0, 40.0, 12.5], "rating_mw": [1000.0, 600.0, 800.0], "existing": [True, True, False],
        "candidate": [False, True, True], "delta_rating_mw": [0.0, 400.0, 0.0], "capex_per_yr": [0.0, 2.0e6, 3.2e6],
        "loss_frac": [0.0, 0.0, 0.0], "contingency": [False, False, False]})
    hvdc = pd.DataFrame({"id": [1], "name": ["dc13"], "from_bus": [1], "to_bus": [3], "existing_mw": [500.0],
                         "candidate_max_mw": [500.0], "converter_efficiency": [0.97], "capex_per_mw_yr": [80000.0]})
    generators = pd.DataFrame({
        "id": [1, 2, 3], "name": ["ccgt1", "nuke1", "wind1"], "bus": [1, 2, 3],
        "technology": ["gas_cc", "nuclear", "wind_onshore"], "kind": ["thermal", "thermal", "renewable"],
        "existing_mw": [800.0, 500.0, 900.0], "varcost_per_mwh": [70.0, 10.0, 0.0],
        "fom_per_mw_yr": [15000.0, 90000.0, 25000.0], "co2_t_per_mwh": [0.37, 0.0, 0.0]})
    storage = pd.DataFrame({
        "id": [1], "name": ["bess1"], "bus": [2], "technology": ["battery"], "existing_mw": [200.0],
        "duration_h": [2.0], "eta_charge": [0.94], "eta_discharge": [0.94], "self_discharge_frac_per_h": [0.0005],
        "soc_init_frac": [0.5], "varcost_per_mwh": [0.5]})
    candidates = pd.DataFrame({
        "candidate_id": ["ocgt_new", "solar_new", "bess_new"], "technology": ["gas_ct", "solar_pv", "battery"],
        "kind": ["thermal", "renewable", "storage"], "bus": [3, 2, 1], "max_mw": [1000.0, 2500.0, 800.0],
        "capex_per_mw_yr": [45000.0, 38000.0, 52000.0], "fom_per_mw_yr": [0.0, 0.0, 0.0],
        "varcost_per_mwh": [140.0, 0.0, 0.3], "co2_t_per_mwh": [0.55, 0.0, 0.0], "duration_h": [np.nan, np.nan, 4.0]})
    t = np.arange(HOURS)

    def long(idcol, valcol, keys, make):
        rows = []
        for k in keys:
            for b in (1, 2):
                v = make(k, b)
                rows.append(pd.DataFrame({idcol: k, "scenario": 1, "block": b, "period": t + 1, valcol: v}))
        return pd.concat(rows, ignore_index=True)

    demand = long("bus", "mw", [1, 2, 3], lambda k, b: np.round((300.0 * k + 200 * (b - 1)) * s["load"], 3))
    availability = long("site_name", "cf", ["wind1", "solar_new"],
                        lambda k, b: s["wind"] if k == "wind1" else np.round(s["solar"] * (0.6 if b == 1 else 1.0), 4))
    return Bundle(descriptor=descriptor, tables={
        "buses": buses, "lines": lines, "hvdc": hvdc, "generators": generators, "storage": storage,
        "candidates": candidates, "demand": demand, "availability": availability})
