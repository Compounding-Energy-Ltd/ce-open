# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Write the synthetic toy PyPSA network used by the examples and tests.

All data are invented: a four-bus electrical system plus one hydrogen bus,
24 hourly snapshots each weighted to stand for 365 hours (so the day
represents a year). The profiles are smooth deterministic curves, not
measurements. PyPSA is not needed: the CSV folder is written directly.

    python make_toy_network.py [output_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HOURS = 24
DAYS_PER_YEAR = 365.0


def profiles() -> dict[str, np.ndarray]:
    h = np.arange(HOURS)
    solar = np.clip(np.sin((h - 6) / 12 * np.pi), 0, None) * 0.8
    onwind = 0.45 + 0.25 * np.cos((h - 3) / 24 * 2 * np.pi)
    offwind = 0.60 + 0.20 * np.cos((h - 9) / 24 * 2 * np.pi)
    shape = 0.8 + 0.2 * np.sin((h - 9) / 24 * 2 * np.pi) + 0.1 * np.exp(-((h - 18) ** 2) / 6)
    inflow = 120 + 30 * np.sin(h / 24 * 2 * np.pi)
    return {"solar": solar.round(4), "onwind": onwind.round(4), "offwind": offwind.round(4),
            "shape": shape.round(4), "inflow": inflow.round(3)}


def write(folder: str | Path) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    p = profiles()
    snaps = [str(t) for t in pd.date_range("2030-01-15", periods=HOURS, freq="h")]

    pd.DataFrame([{"name": "Toy network", "_multi_invest": 0}]).to_csv(folder / "network.csv", index=False)
    pd.DataFrame({"snapshot": snaps, "objective": DAYS_PER_YEAR, "stores": 1.0,
                  "generators": DAYS_PER_YEAR}).to_csv(folder / "snapshots.csv", index_label="")

    def static(name: str, rows: list[dict]) -> None:
        pd.DataFrame(rows).set_index("name").to_csv(folder / f"{name}.csv")

    def series(name: str, cols: dict[str, np.ndarray]) -> None:
        pd.DataFrame(cols, index=pd.Index(snaps, name="snapshot")).to_csv(folder / f"{name}.csv")

    static("buses", [
        {"name": "North", "v_nom": 400.0, "x": -3.0, "y": 56.5, "carrier": "AC", "country": "NR"},
        {"name": "South", "v_nom": 400.0, "x": -1.5, "y": 52.0, "carrier": "AC", "country": "SR"},
        {"name": "East", "v_nom": 400.0, "x": 0.5, "y": 53.0, "carrier": "AC", "country": "SR"},
        {"name": "Offshore", "v_nom": 220.0, "x": 2.0, "y": 54.0, "carrier": "AC", "country": "SR"},
        {"name": "East H2", "v_nom": 1.0, "x": 0.5, "y": 53.0, "carrier": "H2", "country": "SR"},
    ])
    static("carriers", [
        {"name": "gas", "co2_emissions": 0.2}, {"name": "OCGT", "co2_emissions": 0.2},
        {"name": "nuclear", "co2_emissions": 0.0}, {"name": "onwind", "co2_emissions": 0.0},
        {"name": "offwind", "co2_emissions": 0.0}, {"name": "solar", "co2_emissions": 0.0},
        {"name": "battery", "co2_emissions": 0.0}, {"name": "PHS", "co2_emissions": 0.0},
        {"name": "hydro", "co2_emissions": 0.0}, {"name": "H2", "co2_emissions": 0.0},
    ])
    static("lines", [
        {"name": "North-South", "bus0": "North", "bus1": "South", "x": 40.0, "r": 4.0, "s_nom": 1500.0,
         "s_nom_extendable": False, "capital_cost": 0.0},
        {"name": "South-East", "bus0": "South", "bus1": "East", "x": 20.0, "r": 2.0, "s_nom": 800.0,
         "s_nom_extendable": True, "s_nom_min": 0.0, "s_nom_max": 2000.0, "capital_cost": 30.0},
        {"name": "North-East", "bus0": "North", "bus1": "East", "x": 60.0, "r": 6.0, "s_nom": 1000.0,
         "s_max_pu": 0.7, "s_nom_extendable": False, "capital_cost": 0.0},
    ])
    static("transformers", [
        {"name": "Offshore-East", "bus0": "Offshore", "bus1": "East", "x": 0.1, "s_nom": 1200.0},
    ])
    static("links", [
        {"name": "North-South HVDC", "bus0": "North", "bus1": "South", "p_nom": 600.0, "p_min_pu": -1.0,
         "efficiency": 0.97, "carrier": "DC", "p_nom_extendable": True, "p_nom_min": 600.0,
         "p_nom_max": 1600.0, "capital_cost": 150.0},
        {"name": "East electrolyser", "bus0": "East", "bus1": "East H2", "p_nom": 200.0, "efficiency": 0.7,
         "carrier": "H2"},
    ])
    static("generators", [
        {"name": "South CCGT", "bus": "South", "carrier": "gas", "p_nom": 2000.0, "marginal_cost": 70.0,
         "efficiency": 0.55, "ramp_limit_up": 0.6, "ramp_limit_down": 0.6},
        {"name": "South nuclear", "bus": "South", "carrier": "nuclear", "p_nom": 800.0, "marginal_cost": 12.0,
         "p_min_pu": 0.5},
        {"name": "East OCGT", "bus": "East", "carrier": "OCGT", "p_nom": 0.0, "p_nom_extendable": True,
         "p_nom_max": 1500.0, "marginal_cost": 130.0, "capital_cost": 45000.0, "efficiency": 0.38},
        {"name": "North onwind", "bus": "North", "carrier": "onwind", "p_nom": 2500.0, "marginal_cost": 0.5},
        {"name": "Offshore wind", "bus": "Offshore", "carrier": "offwind", "p_nom": 1000.0,
         "p_nom_extendable": True, "p_nom_min": 0.0, "p_nom_max": 3000.0, "capital_cost": 160000.0,
         "marginal_cost": 1.0},
        {"name": "East solar", "bus": "East", "carrier": "solar", "p_nom": 1200.0, "p_nom_extendable": True,
         "capital_cost": 40000.0, "marginal_cost": 0.0},
    ])
    series("generators-p_max_pu", {"North onwind": p["onwind"], "Offshore wind": p["offwind"],
                                   "East solar": p["solar"]})
    static("storage_units", [
        {"name": "East battery", "bus": "East", "carrier": "battery", "p_nom": 300.0, "max_hours": 4.0,
         "efficiency_store": 0.95, "efficiency_dispatch": 0.95, "cyclic_state_of_charge": True,
         "p_nom_extendable": True, "p_nom_min": 300.0, "p_nom_max": 1500.0, "capital_cost": 55000.0},
        {"name": "North PHS", "bus": "North", "carrier": "PHS", "p_nom": 400.0, "max_hours": 8.0,
         "efficiency_store": 0.87, "efficiency_dispatch": 0.87, "cyclic_state_of_charge": True},
        {"name": "North hydro", "bus": "North", "carrier": "hydro", "p_nom": 300.0, "max_hours": 100.0,
         "p_min_pu": 0.0, "efficiency_dispatch": 0.9, "cyclic_state_of_charge": True},
    ])
    series("storage_units-inflow", {"North hydro": p["inflow"]})
    static("stores", [{"name": "East H2 tank", "bus": "East H2", "carrier": "H2", "e_nom": 5000.0}])
    static("loads", [
        {"name": "North load", "bus": "North"}, {"name": "South load", "bus": "South"},
        {"name": "East homes", "bus": "East"}, {"name": "East industry", "bus": "East"},
        {"name": "East H2 demand", "bus": "East H2", "p_set": 50.0},
    ])
    series("loads-p_set", {"North load": (1400 * p["shape"]).round(2), "South load": (2600 * p["shape"]).round(2),
                           "East homes": (900 * p["shape"]).round(2), "East industry": np.full(HOURS, 500.0)})
    static("global_constraints", [
        {"name": "CO2Limit", "type": "primary_energy", "carrier_attribute": "co2_emissions", "sense": "<=",
         "constant": 4.0e6},
    ])
    return folder


if __name__ == "__main__":
    out = write(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("toy_network"))
    print(f"wrote {out}")
