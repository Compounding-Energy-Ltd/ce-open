# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Synthetic fixtures: a three-bus toy bundle and a matching toy results directory.

EVERY NUMBER HERE IS INVENTED. The bundle is a deterministic arithmetic construction (sinusoids and
constants) that exercises every table of the format; the "results" are NOT the output of any solve — they
are written to be internally consistent (identities hold, signs follow the conventions) so that tests and
examples have something valid to read.
"""

from __future__ import annotations

import datetime as _dt
import math
from pathlib import Path
from typing import Any

import pyarrow as pa

from .prov import format_prov, sha256_file
from .schema import SPEC_VERSION, table_schema
from .tables import read_raw, write_table

YEARS = [2030, 2035]
WEATHER = [2018, 2019]
SCENARIOS = ["central", "high_demand"]
PROBS = [0.7, 0.3]
BLOCKS = [("winter_day", 4380.0), ("summer_day", 4380.0)]
NPER = 24
BUILT_UTC = "2026-10-01T00:00:00Z"
GENERATION = "toy3-g1"

BUSES = [(1, "north", "zone_a", 10.0, 20.0, 400.0),
         (2, "south", "zone_b", 9.6, 20.1, 400.0),
         (3, "coast", "zone_b", 9.8, 20.6, 220.0)]
BASE_LOAD = {1: 250.0, 2: 420.0, 3: 180.0}


def _ext(fmt: str) -> str:
    if fmt not in ("parquet", "csv", "arrow"):
        raise ValueError(f"format must be parquet, csv or arrow, got {fmt!r}")
    return "." + fmt


def _write(root: Path, name: str, scope: str, data: dict[str, list], fmt: str) -> Path:
    """Write a table with its canonical Arrow types (columns in the given order)."""
    ts = table_schema(scope, name)
    t = pa.table({k: pa.array(v, type=ts.column(k).arrow_type) for k, v in data.items()})
    p = root / f"{name}{_ext(fmt)}"
    write_table(t, p)
    return p


def _axis():
    for y in YEARS:
        for k in range(1, len(SCENARIOS) + 1):
            for w in WEATHER:
                for b in range(1, len(BLOCKS) + 1):
                    for p in range(1, NPER + 1):
                        yield y, k, w, b, p


def demand_mw(bus: int, y: int, k: int, w: int, b: int, p: int) -> float:
    hour = p - 1
    daily = 1.0 + 0.25 * math.sin(2 * math.pi * (hour - 9) / 24)
    season = 1.15 if b == 1 else 0.95
    growth = 1.0 + 0.02 * (y - YEARS[0])
    scen = 1.0 if k == 1 else 1.1
    wx = 1.0 + (0.03 if w == WEATHER[1] else 0.0) * (1 if b == 1 else -1)
    return round(BASE_LOAD[bus] * daily * season * growth * scen * wx, 3)


def cf(site: str, y: int, k: int, w: int, b: int, p: int) -> float:
    hour = p - 1
    if site == "wind_north":
        v = 0.38 + 0.22 * math.sin(2 * math.pi * (hour + 3 * (w - WEATHER[0])) / 24) + (0.1 if b == 1 else -0.05)
    else:   # solar-shaped sites
        v = max(0.0, math.sin(math.pi * (hour - 6) / 12)) * (0.55 if b == 1 else 0.85)
        if site == "hybrid_coast_c1":
            v *= 0.95
        v *= 0.97 if w == WEATHER[1] else 1.0
    return round(min(1.0, max(0.0, v)), 4)


AVAIL_SITES = ["wind_north", "solar_south_c1", "hybrid_coast_c1"]


BUNDLE_TOML = """\
# Synthetic three-bus test bundle. Every number in this directory is invented.
[format]
name = "ce-bundle"
version = "{version}"

[bundle]
name = "toy3"
description = "Synthetic three-bus test system exercising every table of the CE data format"
created_utc = {built}
license = "Apache-2.0"
source = "ce_spec.toy (deterministic synthetic construction)"

[money]
currency = "USD"
dollar_year = 2024
default_annualisation_rate = 0.07   # used for rows carrying overnight costs only
default_lifetime_years = 25.0

[time]
timezone = "UTC"
hour_convention = "hour_ending"
investment_years = [2030, 2035]
weather_years = [2018, 2019]
leap_day = "keep"

[[time.blocks]]
id = 1
name = "winter_day"
kind = "representative"
nperiods = 24
dt_h = 1.0
weight_h = 4380.0

[[time.blocks]]
id = 2
name = "summer_day"
kind = "representative"
nperiods = 24
dt_h = 1.0
weight_h = 4380.0

[scenarios]
names = ["central", "high_demand"]
probability = [0.7, 0.3]

[system]
voll_per_mwh = 9000.0
planning_reserve_margin = 0.15
discount_factors = [1.0, 0.713]
co2_price_per_t = 50.0
"""


def write_toy_bundle(root: str | Path, fmt: str = "parquet") -> Path:
    """Write the synthetic three-bus bundle into ``root`` (created if needed). Returns ``root``."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "bundle.toml").write_text(BUNDLE_TOML.format(version=SPEC_VERSION, built=BUILT_UTC), encoding="utf-8")

    _write(root, "buses", "bundle", {
        "id": [b[0] for b in BUSES], "name": [b[1] for b in BUSES], "zone": [b[2] for b in BUSES],
        "lat": [b[3] for b in BUSES], "lon": [b[4] for b in BUSES], "voltage_kv": [b[5] for b in BUSES],
        "hosting_mw": [None, 800.0, 600.0], "hosting_expandable": [False, False, True],
        "hosting_expand_cost_per_mw_yr": [None, None, 12000.0]}, fmt)
    _write(root, "lines", "bundle", {
        "id": [1, 2, 3, 4], "name": ["north_south", "south_coast", "north_coast", "north_coast_2"],
        "from_bus": [1, 2, 1, 1], "to_bus": [2, 3, 3, 3], "susceptance_pu": [12.0, 8.0, 6.0, 10.0],
        "rating_mw": [300.0, 200.0, 150.0, 400.0], "existing": [True, True, True, False],
        "candidate": [False, True, False, True], "delta_rating_mw": [0.0, 200.0, 0.0, 0.0],
        "capex_per_yr": [0.0, 1.5e6, 0.0, 4.0e6], "loss_frac": [0.01, 0.015, 0.02, 0.01],
        "contingency": [True, False, False, False], "length_km": [80.0, 60.0, 120.0, 115.0],
        "lead_time_years": [None, 2, None, 4]}, fmt)
    _write(root, "hvdc", "bundle", {
        "id": [1], "name": ["coast_link"], "from_bus": [3], "to_bus": [1], "existing_mw": [0.0],
        "candidate_max_mw": [500.0], "converter_efficiency": [0.97], "capex_per_mw_yr": [90000.0],
        "lead_time_years": [5]}, fmt)
    _write(root, "generators", "bundle", {
        "id": [1, 2, 3], "name": ["ccgt_north", "peaker_south", "wind_north"], "bus": [1, 2, 1],
        "technology": ["gas_cc", "gas_ct", "wind_onshore"], "kind": ["thermal", "thermal", "renewable"],
        "existing_mw": [600.0, 150.0, 300.0], "varcost_per_mwh": [45.0, 120.0, 0.0],
        "fom_per_mw_yr": [14000.0, 9000.0, 30000.0], "pmin_frac": [0.4, 0.2, None],
        "ramp_frac_per_h": [0.5, 1.0, None], "co2_t_per_mwh": [0.37, 0.55, 0.0],
        "firm_capacity_frac": [0.95, 0.95, 0.12], "retirable": [False, True, False],
        "retirement_year": [None, None, None]}, fmt)
    _write(root, "storage", "bundle", {
        "id": [1], "name": ["battery_south"], "bus": [2], "technology": ["battery"], "existing_mw": [100.0],
        "duration_h": [4.0], "eta_charge": [0.93], "eta_discharge": [0.93], "soc_init_frac": [0.5],
        "firm_capacity_frac": [0.5]}, fmt)
    cand = _write(root, "candidates", "bundle", {
        "candidate_id": ["solar_south_c1", "hybrid_coast_c1", "battery_coast_c1", "ocgt_south_c1"],
        "technology": ["solar_pv", "solar_pv_battery", "battery", "gas_ct"],
        "kind": ["renewable", "hybrid", "storage", "thermal"], "bus": [2, 3, 3, 2],
        "max_mw": [500.0, 300.0, 200.0, 400.0],
        "capex_per_mw_yr": [60000.0, None, 50000.0, 70000.0],
        "capex_overnight_per_mw": [None, 1.2e6, None, None],
        "fom_per_mw_yr": [15000.0, 25000.0, 10000.0, 8000.0],
        "varcost_per_mwh": [0.0, 0.0, 2.0, 95.0],
        "lead_time_years": [1, 2, 1, 3],
        "duration_h": [None, 4.0, 4.0, None],
        "generation_mw": [None, 300.0, None, None], "storage_mw": [None, 150.0, None, None],
        "co2_t_per_mwh": [0.0, 0.0, 0.0, 0.55], "firm_capacity_frac": [0.3, 0.5, 0.5, 0.95],
        "integer_build": [False, False, False, True], "unit_size_mw": [None, None, None, 100.0],
        "cf_mean": [0.24, 0.23, None, None],
        "lat": [9.55, 9.82, 9.81, 9.62], "lon": [20.15, 20.55, 20.58, 20.12]}, fmt)

    d: dict[str, list] = {c: [] for c in ("bus", "scenario", "block", "period", "mw", "year", "weather")}
    for y, k, w, b, p in _axis():
        for bus in (1, 2, 3):
            for c, v in zip(d, (bus, k, b, p, demand_mw(bus, y, k, w, b, p), y, w)):
                d[c].append(v)
    _write(root, "demand", "bundle", d, fmt)
    a: dict[str, list] = {c: [] for c in ("site_name", "scenario", "block", "period", "cf", "year", "weather")}
    for y, k, w, b, p in _axis():
        for s in AVAIL_SITES:
            for c, v in zip(a, (s, k, b, p, cf(s, y, k, w, b, p), y, w)):
                a[c].append(v)
    avail = _write(root, "availability", "bundle", a, fmt)
    _write(root, "weather_weights", "bundle", {
        "year": [2030, 2030, 2035, 2035], "weather": [2018, 2019, 2018, 2019], "weight": [0.6, 0.4, 0.6, 0.4]}, fmt)
    _write(root, "peak_demand", "bundle", {
        "bus": [1, 2, 3, 1, 2, 3], "year": [2030, 2030, 2030, 2035, 2035, 2035],
        "mw": [365.0, 610.0, 262.0, 405.0, 675.0, 290.0]}, fmt)
    _write(root, "interfaces", "bundle", {
        "interface_id": [1, 1, 1], "name": ["north_export"] * 3, "line_id": [1, 3, 4], "orientation": [1, 1, 1],
        "max_fwd_mw": [500.0] * 3, "max_rev_mw": [400.0] * 3, "source": ["synthetic"] * 3}, fmt)
    _write(root, "interface_limits_monthly", "bundle", {
        "interface_id": [1, 1, 1], "month": [6, 7, 8], "max_fwd_mw": [450.0] * 3, "max_rev_mw": [400.0] * 3}, fmt)

    (root / (cand.name + ".prov")).write_text(
        "# provenance of the candidate table (synthetic)\n" + format_prov({
            "source": "ce_spec.toy synthetic construction", "license": "Apache-2.0", "built_utc": BUILT_UTC,
            "tool": "ce_spec.toy", "tool_version": SPEC_VERSION, "generation": GENERATION,
            "rows": 4, "sha256": sha256_file(cand)}), encoding="utf-8")
    (root / (avail.name + ".prov")).write_text(format_prov({
        "source": "ce_spec.toy synthetic construction", "license": "Apache-2.0", "built_utc": BUILT_UTC,
        "tool": "ce_spec.toy",
        "derived_from": cand.name, "source_generation": GENERATION, "rows": len(a["cf"]),
        "sha256": sha256_file(avail)}), encoding="utf-8")
    return root


# ---------------------------------------------------------------------------------------------------- TOML out

def _toml_str(s: str) -> str:
    out = []
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _toml_val(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
        return repr(v)
    if isinstance(v, str):
        return _toml_str(v)
    if isinstance(v, _dt.datetime):
        return v.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, list):
        return "[" + ", ".join(_toml_val(x) for x in v) + "]"
    raise TypeError(f"cannot write {type(v).__name__} as a TOML value")


def dump_toml(doc: dict, header: str = "") -> str:
    """A small TOML writer for the documents this package produces (scalars, arrays of scalars, tables and
    arrays of tables)."""
    lines: list[str] = [f"# {h}" if h else "#" for h in header.splitlines()] if header else []

    def emit(d: dict, prefix: str) -> None:
        scalars = {k: v for k, v in d.items() if not isinstance(v, dict)
                   and not (isinstance(v, list) and v and all(isinstance(x, dict) for x in v))}
        for k, v in scalars.items():
            lines.append(f"{_key(k)} = {_toml_val(v)}")
        for k, v in d.items():
            if isinstance(v, dict):
                name = f"{prefix}.{_key(k)}" if prefix else _key(k)
                lines.append("")
                lines.append(f"[{name}]")
                emit(v, name)
            elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                name = f"{prefix}.{_key(k)}" if prefix else _key(k)
                for item in v:
                    lines.append("")
                    lines.append(f"[[{name}]]")
                    emit(item, name)

    emit(doc, "")
    return "\n".join(lines).lstrip("\n") + "\n"


def _key(k: str) -> str:
    return k if k and all(c.isalnum() or c in "_-" for c in k) else _toml_str(k)


# ---------------------------------------------------------------------------------------------------- results

BUILDS = [  # (year, component, name, technology, bus, built_mw, built_mwh)
    (2030, "renewable", "solar_south_c1", "solar_pv", "south", 200.0, None),
    (2030, "storage", "battery_coast_c1", "battery", "coast", 50.0, None),
    (2035, "renewable", "solar_south_c1", "solar_pv", "south", 350.0, None),
    (2035, "storage", "battery_coast_c1", "battery", "coast", 100.0, None),
    (2035, "ac_line", "north_coast_2", "ac_line", "north", 400.0, None),
    (2035, "retirement", "peaker_south", "gas_ct", "south", -50.0, None),
    # a hybrid reports one row per part under its candidate_id (SPEC.md 9.4): the renewable part and the storage
    # part's power; its energy is storage_mw x duration_h (no storage_energy row: not sized independently)
    (2035, "renewable", "hybrid_coast_c1", "solar_pv_battery", "coast", 240.0, None),
    (2035, "storage", "hybrid_coast_c1", "solar_pv_battery", "coast", 120.0, None),
]
CAPACITY = {"wind_north": {2030: 300.0, 2035: 300.0}, "solar_south_c1": {2030: 200.0, 2035: 350.0},
            "hybrid_coast_c1": {2030: 0.0, 2035: 240.0}}
#: the hybrid's storage part: power (MW) by year, duration, one-way efficiency, daily charge/discharge
HYBRID_STORAGE = {"name": "hybrid_coast_c1", "mw": {2030: 0.0, 2035: 120.0}, "duration_h": 4.0, "eta": 0.95,
                  "charge_mw": 60.0}
LINE_CAP = {"north_south": 300.0, "south_coast": 200.0, "north_coast": 150.0}


def write_toy_results(root: str | Path, fmt: str = "parquet") -> Path:
    """Write a synthetic results directory matching the toy bundle. NOT solver output: see module docstring."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    ext = _ext(fmt)
    written: list[tuple[str, Path, str, str, str, list[str]]] = []

    def put(name: str, data: dict[str, list], family: str, grain: str, desc: str, measures: list[str]) -> None:
        p = _write(root, name, "results", data, fmt)
        written.append((name, p, family, grain, desc, measures))

    put("investment", {
        "year": [r[0] for r in BUILDS], "component": [r[1] for r in BUILDS], "name": [r[2] for r in BUILDS],
        "technology": [r[3] for r in BUILDS], "bus": [r[4] for r in BUILDS], "built_mw": [r[5] for r in BUILDS],
        "built_mwh": [r[6] for r in BUILDS]}, "investment", "component-year",
        "Cumulative capacity built (retirements negative) by component and year", ["built_mw"])

    gen: dict[str, list] = {c: [] for c in ("year", "scenario", "weather", "block", "period", "source_type",
                                            "component_name", "technology", "bus", "dispatch_mw", "varcost_per_mwh")}
    cur: dict[str, list] = {c: [] for c in ("year", "scenario", "weather", "block", "period", "site_name",
                                            "technology", "bus", "available_mw", "dispatch_mw", "curtailed_mw")}
    lmp: dict[str, list] = {c: [] for c in ("year", "scenario", "weather", "block", "bus", "period", "lmp_per_mwh",
                                            "lmp_energy", "lmp_congestion", "lmp_loss")}
    flows: dict[str, list] = {c: [] for c in ("year", "scenario", "weather", "block", "line", "period", "flow_mw")}
    sto: dict[str, list] = {c: [] for c in ("year", "scenario", "weather", "block", "period", "storage",
                                            "technology", "bus", "soc_mwh", "charge_mw", "discharge_mw")}
    meta = {"wind_north": ("wind_onshore", "north"), "solar_south_c1": ("solar_pv", "south"),
            "hybrid_coast_c1": ("solar_pv_battery", "coast")}
    hs = HYBRID_STORAGE
    for y, k, w, b, p in _axis():
        ax = (y, k, w, b, p)
        total_load = sum(demand_mw(bus, *ax) for bus in (1, 2, 3))
        ren = 0.0
        for site, (tech, bus) in meta.items():
            if CAPACITY[site][y] == 0.0:
                continue   # not in service this year: no rows (an absent row reads as zero)
            avail = round(CAPACITY[site][y] * cf(site, *ax), 6)
            used = round(min(avail, 0.9 * avail + 5.0), 6) if avail > 0 else 0.0
            ren += used
            for c, v in zip(cur, (y, k, w, b, p, site, tech, bus, avail, used, avail - used)):
                cur[c].append(v)
            for c, v in zip(gen, (y, k, w, b, p, "renewable", site, tech, bus, used, 0.0)):
                gen[c].append(v)
        hour = p - 1
        charging = 6 <= hour < 12
        sp = 50.0 if (charging or 17 <= hour < 21) else 0.0
        if sp:
            st = "storage_charge" if charging else "storage_discharge"
            for c, v in zip(gen, (y, k, w, b, p, st, "battery_south", "battery", "south", sp, None if charging else 0.0)):
                gen[c].append(v)
        # the hybrid's battery: charges 08-12 from its own solar, discharges 17-21; the daily cycle closes exactly
        h_net = 0.0
        if hs["mw"][y] > 0:
            h_dis = hs["charge_mw"] * hs["eta"] ** 2
            h_soc = (0.5 * hs["mw"][y] * hs["duration_h"] + hs["charge_mw"] * hs["eta"] * min(max(hour - 7, 0), 4)
                     - (h_dis / hs["eta"]) * min(max(hour - 16, 0), 4))
            h_chg = hs["charge_mw"] if 8 <= hour < 12 else 0.0
            h_out = h_dis if 17 <= hour < 21 else 0.0
            if h_chg or h_out:
                st = "storage_charge" if h_chg else "storage_discharge"
                for c, v in zip(gen, (y, k, w, b, p, st, hs["name"], "solar_pv_battery", "coast", round(h_chg or h_out, 6),
                                      None if h_chg else 0.0)):
                    gen[c].append(v)
            for c, v in zip(sto, (y, k, w, b, p, hs["name"], "solar_pv_battery", "coast", round(h_soc, 6), h_chg,
                                  round(h_out, 6))):
                sto[c].append(v)
            h_net = h_chg - h_out
        thermal = max(0.0, total_load - ren + (sp if charging else -sp) + h_net)
        for c, v in zip(gen, (y, k, w, b, p, "thermal", "ccgt_north", "gas_cc", "north", round(thermal, 6), 45.0)):
            gen[c].append(v)
        soc = 100.0 + 50.0 * 0.93 * min(max(hour - 5, 0), 6) - (50.0 / 0.93) * min(max(hour - 16, 0), 4)
        for c, v in zip(sto, (y, k, w, b, p, "battery_south", "battery", "south", round(soc, 6),
                              sp if charging else 0.0, sp if (sp and not charging) else 0.0)):
            sto[c].append(v)
        energy = 45.0 + 10.0 * math.sin(2 * math.pi * (hour - 12) / 24)
        for bus, name in ((1, "north"), (2, "south"), (3, "coast")):
            loss = -energy * 0.004 * (bus - 1)
            cong = 0.0 if bus == 1 else 3.0 * bus * (1 if 17 <= hour < 21 else 0)
            for c, v in zip(lmp, (y, k, w, b, name, p, energy + cong + loss, energy, cong, loss)):
                lmp[c].append(v)
        for line, capmw in LINE_CAP.items():
            f = round(0.6 * capmw * math.sin(2 * math.pi * (hour + len(line)) / 24), 6)
            for c, v in zip(flows, (y, k, w, b, line, p, f)):
                flows[c].append(v)
    put("generation", gen, "dispatch", "unit-period", "Per-period dispatch by unit", ["dispatch_mw"])
    put("lmp", lmp, "price", "bus-period", "Nodal price with energy/congestion/loss decomposition",
        ["lmp_per_mwh", "lmp_energy", "lmp_congestion", "lmp_loss"])
    put("flows", flows, "flow", "line-period", "Per-period signed line flow", ["flow_mw"])
    put("curtailment", cur, "dispatch", "site-period", "Available but unused output", ["curtailed_mw"])
    put("storage_state", sto, "dispatch", "storage-period", "State of charge with charge/discharge",
        ["soc_mwh", "charge_mw", "discharge_mw"])
    checks = [  # check, family, status, value, tolerance, unit, count, population, required
        ("balance_resum", "balance", "PASS", 2.0e-7, 1.0e-3, "MW", 0, 1152, True),
        ("flow_limits", "flows", "PASS", 0.0, 1.0e-4, "MW", 0, 1152, True),
        ("cost_reconciliation", "cost", "PASS", 1.0e-9, 1.0e-6, "relative", 0, 1, True),
        ("adequacy_reserve_margin", "adequacy", "PASS", 0.18, 0.15, "fraction", 0, 2, True),
        ("seasonal_soc_chain", "storage", "NA", None, 1.0e-2, "MWh", 0, 0, False),
    ]
    put("qa_checks", {
        "check": [c[0] for c in checks], "family": [c[1] for c in checks], "status": [c[2] for c in checks],
        "value": [c[3] for c in checks], "tolerance": [c[4] for c in checks], "unit": [c[5] for c in checks],
        "worst_where": [None] * len(checks), "count": [c[6] for c in checks], "population": [c[7] for c in checks],
        "year": [0] * len(checks), "scope": [None] * len(checks), "source": ["synthetic"] * len(checks),
        "note": [None, None, None, None, "no seasonal storage in this run: nothing to check"],
        "required": [c[8] for c in checks]}, "audit", "check", "Run quality checks", ["value", "count", "population"])

    blocks = [{"id": i, "name": n, "kind": "representative", "nperiods": NPER, "dt_h": 1.0, "weight_h": wt}
              for i, (n, wt) in enumerate(BLOCKS, start=1)]
    created = _dt.datetime(2026, 10, 1, tzinfo=_dt.timezone.utc)
    (root / "manifest.toml").write_text(dump_toml({
        "format": {"name": "ce-results", "version": SPEC_VERSION},
        "run": {"name": "toy3_synthetic", "created_utc": created, "engine": "ce_spec.toy (synthetic, not a solve)",
                "engine_version": SPEC_VERSION, "mode": "cem"},
        "money": {"currency": "USD", "dollar_year": 2024},
        "time": {"timezone": "UTC", "hour_convention": "hour_ending", "investment_years": YEARS,
                 "weather_years": WEATHER, "weather_axis": "label", "blocks": blocks},
        "scenarios": {"names": SCENARIOS, "probability": PROBS},
        "inputs": {"bundle_name": "toy3", "bundle_format_version": SPEC_VERSION},
    }, header="Synthetic results for the toy3 bundle. NOT the output of a solve: every number is invented."),
        encoding="utf-8")
    (root / "summary.toml").write_text(dump_toml({
        "cost": {"total": 475000000.0, "investment_capex": 125000000.0, "operational_opex": 350000000.0},
        "solve": {"lower_bound": 473100000.0, "relative_gap": 0.004, "iterations": 12, "converged": True,
                  "stop_cause": "gap", "wall_s": 3.2, "mip_master": True},
        "builds_mw_by_technology": {"solar_pv": 350.0, "battery": 100.0, "solar_pv_battery": 240.0},
        "energy": {"demand_mwh": 8.1e6, "unserved_mwh": 0.0, "curtailed_mwh": 52000.0},
        "emissions": {"co2_t": 1.9e6},
    }, header="Synthetic summary (invented numbers)."), encoding="utf-8")
    (root / "qa_report.toml").write_text(dump_toml({
        "qa": {"status": "PASS", "n_pass": 4, "n_warn": 0, "n_fail": 0, "n_na": 1, "n_required_na": 0,
               "failing": [], "table": f"qa_checks{ext}"},
        "tolerances": {"balance_atol_mw": 1.0e-3, "flow_mw": 1.0e-4, "cost_rel": 1.0e-6},
    }, header="Synthetic QA report (invented numbers)."), encoding="utf-8")
    entries = []
    for name, p, family, grain, desc, measures in written:
        raw = read_raw(p)
        entries.append({"name": name, "formats": [fmt], "nrows": raw.num_rows, "columns": list(raw.column_names),
                        "family": family, "grain": grain, "description": desc, "measures": measures})
    (root / "results_manifest.toml").write_text(dump_toml({
        "schema_version": 1, "run": "toy3_synthetic",
        "formats_note": f"{fmt} = the only format written for this synthetic run",
        "toml": ["manifest.toml", "summary.toml", "qa_report.toml"], "tables": entries,
    }), encoding="utf-8")
    return root
