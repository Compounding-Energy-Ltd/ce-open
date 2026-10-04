# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Write a small synthetic run directory (and its input bundle).

The data describe an invented three-bus system and are generated from simple
formulas -- they are *not* a solved dispatch. They are internally consistent
wherever the reader relies on consistency (price components sum to the price,
renewable dispatch never exceeds availability, storage state of charge follows
charge and discharge) so tests and examples have known answers.

Two layouts are available:

* ``layout="1.0"`` (default): the CE data format 1.0 -- canonical column
  names, the time axis in ``manifest.toml``, and a separate input bundle
  holding buses, lines, existing generators, availability and weather weights.
* ``layout="legacy"``: the layout older engine releases wrote -- currency-
  suffixed money columns, calendar and network tables echoed into the run
  directory, no bundle.

Example::

    from ce_results.synthetic import write_synthetic_run
    from ce_results import Run

    run_dir, bundle_dir = write_synthetic_run("demo")
    run = Run(run_dir, bundle=bundle_dir)
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import math
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from . import _toml

__all__ = ["write_synthetic_run", "VARIANTS", "BUSES", "LINES"]

BUSES = (("b1", "North"), ("b2", "Central"), ("b3", "South"))
LINES = (  # line, from, to, rating_mw, display label
    ("l1", "b1", "b2", 100.0, "North-Central"),
    ("l2", "b2", "b3", 60.0, "Central-South"),
    ("l3", "b1", "b3", 80.0, "North-South"),
)
YEARS = (2030, 2035)
DISCOUNT = (1.0, 0.78)
FIRST_WEATHER_YEAR = 2012
MLF = {"b1": 0.0, "b2": 0.01, "b3": 0.02}  # loss component = energy price * factor

# Named variants: knobs that move the headline numbers so comparisons have deltas.
VARIANTS = {
    "base": dict(wind_mult=1.0, price_shift=0.0, objective=1.234e9, gap=4e-4, converged=True,
                 stop="gap", iterations=6),
    "alt": dict(wind_mult=1.5, price_shift=-4.0, objective=1.234e9 * 0.97, gap=6e-4, converged=True,
                stop="gap", iterations=7),
    "unconverged": dict(wind_mult=1.0, price_shift=0.0, objective=1.25e9, gap=0.02, converged=False,
                        stop="wall_cap", iterations=4),
}
_LEGACY_STOP = {"gap": "gap", "iteration_cap": "iterations", "wall_cap": "wall"}

# cumulative new build by year index (MW; storage energy in MWh)
_BUILDS = (  # component, name, technology, bus, (y0, y1)
    ("thermal", "ccgt_new_b2", "ccgt", "b2", (50.0, 80.0)),
    ("renewable", "wind_b3", "wind_onshore", "b3", (100.0, 150.0)),
    ("renewable", "solar_b2", "solar_pv", "b2", (60.0, 120.0)),
    ("storage", "bess_b3", "li_ion", "b3", (20.0, 40.0)),
    ("storage_energy", "bess_b3", "li_ion", "b3", (80.0, 160.0)),
    ("ac_line", "l3", "ac_line", "b1", (0.0, 25.0)),
    ("retirement", "coal_b1", "coal", "b1", (0.0, -30.0)),
)
# build options: candidate_id, technology, kind, bus, max_mw, annualised capex per MW
_CANDIDATES = (("ccgt_new_b2", "ccgt", "thermal", "b2", 200.0, 60000.0),
               ("wind_b3", "wind_onshore", "renewable", "b3", 300.0, 90000.0),
               ("solar_b2", "solar_pv", "renewable", "b2", 300.0, 45000.0),
               ("bess_b3", "li_ion", "storage", "b3", 100.0, 20000.0))
# existing fleet: name -> (technology, kind, bus, MW)
_EXISTING = {"ccgt_b1": ("ccgt", "thermal", "b1", 300.0), "coal_b1": ("coal", "thermal", "b1", 100.0),
             "wind_old_b3": ("wind_onshore", "renewable", "b3", 40.0)}


def _blocks(chronological: bool):
    """(block, weight_h, [dt_h], [hour of day of each period], kind)."""
    if chronological:
        return [(1, 24.0, [1.0] * 24, list(range(24)), "chronological")]
    return [(1, 4380.0, [1.0, 1.0, 1.0, 1.0], [0, 6, 12, 18], "representative"),
            (2, 4380.0, [1.0, 2.0, 3.0, 2.0], [3, 9, 15, 21], "representative")]


def _cf_wind(hour: int, w: int) -> float:
    return round(0.40 + 0.30 * math.cos(2 * math.pi * hour / 24) - 0.05 * (w - 1), 6)


def _cf_solar(hour: int) -> float:
    return round(max(0.0, math.sin(math.pi * (hour - 6) / 12)) if 6 <= hour <= 18 else 0.0, 6)


def _write_table(df: pd.DataFrame, d: Path, name: str, formats: Sequence[str]) -> list[str]:
    written = []
    for fmt in formats:
        if fmt == "parquet":
            df.to_parquet(d / f"{name}.parquet", index=False)
        elif fmt == "csv":
            df.to_csv(d / f"{name}.csv", index=False)
        elif fmt == "arrow":
            import pyarrow as pa
            import pyarrow.feather as feather

            feather.write_feather(pa.Table.from_pandas(df, preserve_index=False), d / f"{name}.arrow")
        else:
            raise ValueError(f"unknown format {fmt!r}")
        written.append(fmt)
    return written


def _digest(d: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(d.iterdir()):
        if p.is_file():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return "sha256:" + h.hexdigest()


def write_synthetic_run(path, *, name: str | None = None, variant: str = "base", layout: str = "1.0",
                        bundle_path=None, chronological: bool = False, weather_draws: int = 1,
                        weather_weights: Sequence[float] | None = None,
                        formats: Sequence[str] = ("parquet",), with_qa: bool = True,
                        with_manifest: bool = True, with_curtailment: bool = False,
                        register_version: str = "synthetic-register-0001"):
    """Write a synthetic run directory at ``path``.

    Returns ``(run_dir, bundle_dir)`` for ``layout="1.0"`` (the bundle goes to
    ``bundle_path``, default ``<path>_bundle``) and ``(run_dir, None)`` for
    ``layout="legacy"``.

    Parameters
    ----------
    variant:
        ``"base"``, ``"alt"`` (more wind, lower prices, lower cost) or
        ``"unconverged"`` (stopped on the wall-clock budget at a 2% gap).
    chronological:
        One 24-hour block of real time instead of two representative blocks
        weighted to a full year.
    weather_draws, weather_weights:
        Number of weather years per investment year; with ``weather_weights`` a
        ``weather_weights`` table is written (otherwise draws weigh equally).
    formats:
        Table formats to write: any of ``"parquet"``, ``"csv"``, ``"arrow"``.
    with_qa, with_manifest, with_curtailment:
        Write the QA report, the ``results_manifest.toml`` inventory, and the
        optional ``curtailment`` table.
    """
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; choose from {sorted(VARIANTS)}")
    if layout not in ("1.0", "legacy"):
        raise ValueError("layout must be '1.0' or 'legacy'")
    legacy = layout == "legacy"
    if weather_weights is not None and len(weather_weights) != weather_draws:
        raise ValueError("weather_weights needs one weight per weather draw")
    v = VARIANTS[variant]
    d = Path(path)
    d.mkdir(parents=True, exist_ok=True)
    name = name or f"synthetic_{variant}"
    blocks = _blocks(chronological)
    wdraws = list(range(1, weather_draws + 1))
    wlabel = (lambda w: w) if legacy else (lambda w: FIRST_WEATHER_YEAR + w - 1)
    label = dict(BUSES)
    L = "bus_name" if legacy else "bus_label"

    built = {}
    inv_rows = []
    for comp, nm, tech, bus, vals in _BUILDS:
        for yi, yr in enumerate(YEARS):
            x = vals[yi] * (v["wind_mult"] if nm == "wind_b3" else 1.0)
            built[(comp, nm, yr)] = x
            if abs(x) <= 1e-6:
                continue
            if comp == "storage_energy":
                inv_rows.append((yr, "storage_energy_mwh" if legacy else comp, len(inv_rows) + 1, nm, tech, bus,
                                 label[bus], x if legacy else np.nan, np.nan if legacy else x))
            else:
                inv_rows.append((yr, comp, len(inv_rows) + 1, nm, tech, bus, label[bus], x, np.nan))
    inv = pd.DataFrame(inv_rows, columns=["year", "component", "id", "name", "technology", "bus", L,
                                          "built_mw", "built_mwh"])
    if legacy:
        inv = inv.drop(columns="built_mwh")

    wnorm = ([float(x) / sum(weather_weights) for x in weather_weights] if weather_weights is not None
             else [1.0 / weather_draws] * weather_draws)
    lmp, flows, gen, sto, avail, dem, curt = [], [], [], [], [], [], []
    energy = {"demand_mwh": 0.0, "curtailed_mwh": 0.0}  # horizon totals, undiscounted
    for yi, yr in enumerate(YEARS):
        cap = {nm: built.get(("renewable", nm, yr), 0.0) for nm in ("wind_b3", "solar_b2")}
        cap["wind_old_b3"] = _EXISTING["wind_old_b3"][3]
        bess_p = built[("storage", "bess_b3", yr)]
        bess_e = built[("storage_energy", "bess_b3", yr)]
        ccgt_new = built[("thermal", "ccgt_new_b2", yr)]
        coal_cap = _EXISTING["coal_b1"][3] + built[("retirement", "coal_b1", yr)]
        for w in wdraws:
            wl = wlabel(w)
            for b, weight, dts, hours, _kind in blocks:
                soc = 0.5 * bess_e
                for t, (dt, hour) in enumerate(zip(dts, hours), start=1):
                    key = (yr, 1, wl, b)
                    annual_h = wnorm[w - 1] * weight / sum(dts) * dt
                    evening, midday = 17 <= hour <= 21, 11 <= hour <= 15
                    e = (40.0 + 25.0 * math.sin(2 * math.pi * (hour - 8) / 24) + 5.0 * yi
                         + v["price_shift"] + 3.0 * (w - 1))
                    cong = {"b1": 0.0, "b2": 4.0 if midday else 0.0, "b3": 12.0 if evening else 0.0}
                    for bus, _ in BUSES:
                        loss = e * MLF[bus]
                        lmp.append((*key, bus, label[bus], t, e + cong[bus] + loss, e, cong[bus], loss, False))
                    f = {"l1": 60.0 + 30.0 * math.sin(2 * math.pi * hour / 24),
                         "l2": 60.0 if evening else 30.0,
                         "l3": 30.0 * math.cos(2 * math.pi * hour / 24)}
                    for ln, _fb, _tb, _r, llab in LINES:
                        flows.append((*key, ln, llab, t, round(f[ln], 6)))

                    cw, cs = _cf_wind(hour, w), _cf_solar(hour)
                    cf = {"wind_b3": cw, "wind_old_b3": cw, "solar_b2": cs}
                    cut = {"wind_b3": 0.85 if cw > 0.55 else 1.0, "wind_old_b3": 0.85 if cw > 0.55 else 1.0,
                           "solar_b2": 0.9 if cs > 0.8 else 1.0}
                    res = {s: cf[s] * cap[s] * cut[s] for s in cf}
                    dis = min(15.0 * bess_p / 20.0, soc / dt) if evening else 0.0
                    chg = min(15.0 * bess_p / 20.0, (bess_e - soc) / dt) if midday else 0.0
                    soc = soc + (chg - dis) * dt
                    sto.append((*key, t, "bess_b3", "li_ion", "b3", soc, chg, dis))
                    units = [
                        ("thermal", 1, "ccgt_b1", "ccgt", "b1", 120.0 + 40.0 * math.sin(2 * math.pi * hour / 24), 35.0),
                        ("thermal", 2, "ccgt_new_b2", "ccgt", "b2", (0.4 if evening else 0.2) * ccgt_new, 33.0),
                        ("thermal", 3, "coal_b1", "coal", "b1", 0.5 * coal_cap, 28.0),
                        ("renewable", 4, "wind_b3", "wind_onshore", "b3", res["wind_b3"], 0.0),
                        ("renewable", 5, "solar_b2", "solar_pv", "b2", res["solar_b2"], 0.0),
                        ("renewable", 7, "wind_old_b3", "wind_onshore", "b3", res["wind_old_b3"], 0.0),
                        ("storage_discharge", 6, "bess_b3", "li_ion", "b3", dis, 1.0),
                        ("storage_charge", 6, "bess_b3", "li_ion", "b3", chg, np.nan),
                    ]
                    for st, cid, cn, tech, bus, mw, vc in units:
                        if mw > 1e-9:  # zero-dispatch rows are omitted, as the format allows
                            gen.append((*key, t, st, cid, cn, tech, bus, label[bus], round(mw, 9), 0.0, vc))
                    for s in ("wind_b3", "solar_b2", "wind_old_b3"):
                        tech = "solar_pv" if s == "solar_b2" else "wind_onshore"
                        bus = "b2" if s == "solar_b2" else "b3"
                        av = cf[s] * cap[s]
                        curt.append((*key, t, s, tech, bus, av, res[s], av - res[s]))
                        energy["curtailed_mwh"] += (av - res[s]) * annual_h
                        if legacy:
                            if yi == 0 and w == 1:
                                avail.append((1, b, s, t, cf[s]))
                        elif weather_draws == 1:
                            if yi == 0:
                                avail.append((s, 1, b, t, cf[s]))
                        else:
                            avail.append((s, 1, b, t, cf[s], yr, wl))
                    for bus, base in (("b1", 150.0), ("b2", 120.0), ("b3", 90.0)):
                        mw = base * (1.2 if evening else 1.0)
                        energy["demand_mwh"] += mw * annual_h
                        if yi == 0 and w == 1:
                            dem.append((1, b, bus, t, mw))

    T = ["year", "scenario", "weather", "block"]
    vc_col = "varcost_gbp_per_mwh" if legacy else "varcost_per_mwh"
    tables = {
        "investment": inv,
        "lmp": pd.DataFrame(lmp, columns=T + ["bus", L, "period", "lmp_gbp_per_mwh" if legacy else "lmp_per_mwh",
                                              "lmp_energy", "lmp_congestion", "lmp_loss", "floor_slack_at_bus"]),
        "flows": pd.DataFrame(flows, columns=T + ["line", "line_name" if legacy else "line_label", "period",
                                                  "flow_mw"]),
        "generation": pd.DataFrame(gen, columns=T + ["period", "source_type", "component_id", "component_name",
                                                     "technology", "bus", L, "dispatch_mw", "floor_slack_mw",
                                                     vc_col]),
        "storage_state": pd.DataFrame(sto, columns=T + ["period", "storage", "technology", "bus", "soc_mwh",
                                                        "charge_mw", "discharge_mw"]),
    }
    if with_curtailment:
        tables["curtailment"] = pd.DataFrame(curt, columns=T + ["period", "site_name", "technology", "bus",
                                                                "available_mw", "dispatch_mw", "curtailed_mw"])

    obj, gap, nit = v["objective"], v["gap"], v["iterations"]
    conv = []
    for it in range(1, nit + 1):
        frac = (nit - it) / max(nit - 1, 1)
        g = gap + (0.25 - gap) * frac ** 2
        ub = obj * (1.0 + 0.05 * frac)
        conv.append((it, "full", ub * (1.0 - g), ub, g, 3 * it, 0, 0, 0, 40.0 * it))
    tables["convergence"] = pd.DataFrame(conv, columns=["iteration", "phase", "lb", "ub", "gap", "ops_cuts",
                                                        "ctg_cuts", "elcc_cuts", "active_contingencies", "wall_s"])

    checks = [
        dict(check="balance_resum", family="balance", status="PASS", value=0.0, tolerance=1e-6, unit="MW",
             worst_where="", count=0, population=len(tables["lmp"]), year=0, scope="run", source="synthetic",
             note="", required=True),
        dict(check="flow_limits", family="network", status="PASS", value=0.0, tolerance=1e-6, unit="MW",
             worst_where="", count=0, population=len(tables["flows"]), year=0, scope="run", source="synthetic",
             note="", required=True),
        dict(check="cost_reconciliation", family="cost", status="PASS", value=1e-9, tolerance=1e-6,
             unit="rel", worst_where="", count=0, population=1, year=0, scope="run", source="synthetic",
             note="", required=True),
        dict(check="adequacy_row", family="adequacy", status="NA", value=0.0, tolerance=0.0, unit="",
             worst_where="", count=0, population=0, year=0, scope="run", source="synthetic",
             note="no adequacy run", required=False),
    ]
    if with_qa:
        tables["qa_checks"] = pd.DataFrame(checks)

    if legacy:  # the calendar, network and input echoes older releases wrote into the run directory
        _legacy_calendar(tables, blocks, chronological, avail, dem, weather_draws, weather_weights)

    families = {"investment": ("investment", "component-year", ["built_mw"]),
                "lmp": ("price", "bus-period", ["lmp_per_mwh"]),
                "flows": ("flow", "line-period", ["flow_mw"]),
                "generation": ("dispatch", "unit-period", ["dispatch_mw"]),
                "storage_state": ("dispatch", "storage-period", ["soc_mwh", "charge_mw", "discharge_mw"]),
                "curtailment": ("dispatch", "site-period", ["curtailed_mw"]),
                "convergence": ("audit", "iteration", ["lb", "ub", "gap"]),
                "qa_checks": ("audit", "check", ["value"])}
    if not legacy:  # not a 1.0 table: written as an `x_` extension, which the index does not list
        _write_table(tables.pop("convergence"), d, "x_convergence", formats)
    entries = []
    for tname, df in tables.items():
        fmts = _write_table(df, d, tname, formats)
        fam, grain, measures = families.get(tname, ("calendar", "", []))
        entries.append({"name": tname, "formats": fmts, "nrows": len(df), "columns": list(df.columns),
                        "family": fam, "grain": grain, "description": f"synthetic {tname}",
                        "measures": measures})

    register = {"version": register_version}
    capex = 0.42 * obj
    final = YEARS[-1]
    bdir = None
    if legacy:
        _legacy_documents(d, name, v, obj, gap, nit, capex, built, final, register, blocks, with_qa, checks)
    else:
        bdir = Path(bundle_path) if bundle_path is not None else d.with_name(d.name + "_bundle")
        _write_bundle(bdir, blocks, chronological, wdraws, wlabel, weather_weights, avail, dem, weather_draws)
        _spec_documents(d, name, v, obj, gap, nit, capex, built, final, register, blocks, chronological,
                        wdraws, wlabel, with_qa, energy, bdir)

    if with_manifest:
        toml_docs = [f for f in ("manifest.toml", "summary.toml", "validation.toml", "qa_report.toml")
                     if (d / f).is_file()]
        rman = {"schema_version": 1, "run": name, "formats_note": "parquet = canonical",
                "toml": toml_docs, "viz": [], "tables": entries}
        rman["register" if legacy else "x_register"] = register
        _toml.dump(rman, d / "results_manifest.toml")
    return d, bdir


# ---------------------------------------------------------------------- 1.0 layout

def _time_section(blocks, chronological, wdraws, wlabel, results: bool = True):
    tb = []
    for b, wt, dts, _h, kind in blocks:
        blk = {"id": b, "nperiods": len(dts), "dt_h": dts if len(set(dts)) > 1 else dts[0], "weight_h": wt,
               "kind": kind, "name": f"block_{b}"}
        if kind == "chronological":
            blk["start"] = _dt.datetime(FIRST_WEATHER_YEAR, 1, 1, tzinfo=_dt.timezone.utc)
        tb.append(blk)
    out = {"timezone": "UTC", "hour_convention": "hour_ending", "investment_years": list(YEARS),
           "weather_years": [wlabel(w) for w in wdraws], "blocks": tb}
    if results:
        out["weather_axis"] = "label"  # how result series spell the weather column
    return out


def _write_bundle(bdir: Path, blocks, chronological, wdraws, wlabel, weather_weights, avail, dem, weather_draws):
    bdir.mkdir(parents=True, exist_ok=True)
    ids = {b: i for i, (b, _) in enumerate(BUSES, start=1)}
    pd.DataFrame({"id": list(ids.values()), "name": list(ids), "label": [lab for _, lab in BUSES],
                  "zone": ["Z1", "Z1", "Z2"]}).to_parquet(bdir / "buses.parquet", index=False)
    pd.DataFrame({"id": [1, 2, 3], "name": [x[0] for x in LINES], "from_bus": [ids[x[1]] for x in LINES],
                  "to_bus": [ids[x[2]] for x in LINES], "susceptance_pu": [10.0, 8.0, 6.0],
                  "rating_mw": [x[3] for x in LINES], "existing": [True] * 3,
                  "candidate": [False, False, True], "delta_rating_mw": [0.0, 0.0, 25.0],
                  "label": [x[4] for x in LINES]}).to_parquet(bdir / "lines.parquet", index=False)
    pd.DataFrame([(i, n, ids[bus], tech, kind, mw) for i, (n, (tech, kind, bus, mw)) in
                  enumerate(_EXISTING.items(), start=1)],
                 columns=["id", "name", "bus", "technology", "kind", "existing_mw"]) \
        .to_parquet(bdir / "generators.parquet", index=False)
    pd.DataFrame([(cid, tech, kind, ids[bus], mx, capex) for cid, tech, kind, bus, mx, capex in _CANDIDATES],
                 columns=["candidate_id", "technology", "kind", "bus", "max_mw", "capex_per_mw_yr"]) \
        .assign(max_mwh=lambda df: np.where(df["kind"] == "storage", 400.0, np.nan),
                capex_per_mwh_yr=lambda df: np.where(df["kind"] == "storage", 9000.0, np.nan)) \
        .to_parquet(bdir / "candidates.parquet", index=False)
    pd.DataFrame([(ids[bus], sc, b, t, mw) for sc, b, bus, t, mw in dem],
                 columns=["bus", "scenario", "block", "period", "mw"]).to_parquet(bdir / "demand.parquet", index=False)
    cols = ["site_name", "scenario", "block", "period", "cf"] + (["year", "weather"] if weather_draws > 1 else [])
    pd.DataFrame(avail, columns=cols).to_parquet(bdir / "availability.parquet", index=False)
    if weather_weights is not None:
        pd.DataFrame([(yr, wlabel(w), float(weather_weights[w - 1])) for yr in YEARS for w in wdraws],
                     columns=["year", "weather", "weight"]).to_parquet(bdir / "weather_weights.parquet",
                                                                      index=False)
    _toml.dump({
        "format": {"name": "ce-bundle", "version": "1.0"},
        "bundle": {"name": "synthetic-3bus", "license": "CC0-1.0",
                   "source": "Synthetic data generated by ce_results.synthetic"},
        "money": {"currency": "GBP", "dollar_year": 2025},
        "time": _time_section(blocks, chronological, wdraws, wlabel, results=False),
        "scenarios": {"names": ["base"], "probability": [1.0]},
        "system": {"voll_per_mwh": 6000.0, "discount_factors": list(DISCOUNT)},
    }, bdir / "bundle.toml")


def _spec_documents(d, name, v, obj, gap, nit, capex, built, final, register, blocks, chronological,
                    wdraws, wlabel, with_qa, energy, bdir):
    _toml.dump({
        "format": {"name": "ce-results", "version": "1.0"},
        "run": {"name": name, "created_utc": _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
                "engine": "ce-results-synthetic", "engine_version": "0.1.0", "mode": "cem", "seed": 7},
        "money": {"currency": "GBP", "dollar_year": 2025},
        "time": _time_section(blocks, chronological, wdraws, wlabel),
        "scenarios": {"names": ["base"], "probability": [1.0]},
        "inputs": {"bundle_name": "synthetic-3bus", "bundle_format_version": "1.0", "bundle_digest": _digest(bdir)},
        "x_register": register,
    }, d / "manifest.toml")
    builds = {}
    for comp, nm, tech, _bus, _vals in _BUILDS:
        if comp in ("thermal", "renewable", "storage"):
            builds[tech] = builds.get(tech, 0.0) + built[(comp, nm, final)]
    _toml.dump({
        "cost": {"total": obj, "investment_capex": capex, "operational_opex": obj - capex,
                 "total_ex_policy_penalty": obj},
        "solve": {"lower_bound": obj * (1 - gap), "relative_gap": gap, "iterations": nit,
                  "converged": v["converged"], "stop_cause": v["stop"], "wall_s": 40.0 * nit, "mip_master": False},
        "builds_mw_by_technology": builds,
        "energy": {"demand_mwh": energy["demand_mwh"], "unserved_mwh": 0.0,
                   "curtailed_mwh": energy["curtailed_mwh"]},
        "emissions": {"co2_t": 9.5e5},
    }, d / "summary.toml")
    if with_qa:
        _toml.dump({
            "qa": {"status": "PASS", "n_pass": 3, "n_warn": 0, "n_fail": 0, "n_na": 1, "n_required_na": 0,
                   "failing": [], "table": "qa_checks.parquet"},
            "tolerances": {"balance_mw": 1e-6, "flow_mw": 1e-6, "cost_rel": 1e-6},
            "x_cost_ledger": {"objective": obj, "capex_headline": capex, "opex": obj - capex, "residual": 0.0},
        }, d / "qa_report.toml")


# ---------------------------------------------------------------------- legacy layout

def _legacy_calendar(tables, blocks, chronological, avail, dem, weather_draws, weather_weights):
    ids = dict(BUSES)
    tables["network"] = pd.DataFrame([(ln, llab, fb, ids[fb], tb, ids[tb], r) for ln, fb, tb, r, llab in LINES],
                                     columns=["line", "line_name", "from_bus", "from_bus_name", "to_bus",
                                              "to_bus_name", "rating_mw"])
    tables["blocks"] = pd.DataFrame([(b, wt, len(dts), sum(dts)) for b, wt, dts, _h, _k in blocks],
                                    columns=["block", "weight_h", "nperiods", "sum_dt_h"])
    tables["block_periods"] = pd.DataFrame([(b, t, dt, float(np.cumsum(dts)[t - 1]))
                                            for b, _w, dts, _h, _k in blocks for t, dt in enumerate(dts, start=1)],
                                           columns=["block", "period", "dt_h", "cum_h_in_block"])
    tables["scenarios"] = pd.DataFrame({"scenario": [1], "probability": [1.0]})
    tables["years"] = pd.DataFrame({"year_index": [1, 2], "year": list(YEARS), "discount": list(DISCOUNT)})
    tables["demand_profile"] = pd.DataFrame(dem, columns=["scenario", "block", "bus", "period", "demand_mw"])
    tables["availability_profile"] = pd.DataFrame(avail, columns=["scenario", "block", "renewable", "period",
                                                                  "availability_cf"])
    if weather_weights is not None:
        tables["weather_weights"] = pd.DataFrame(
            [(yr, w, float(weather_weights[w - 1])) for yr in YEARS for w in range(1, weather_draws + 1)],
            columns=["year", "weather", "weight"])
    ti = []
    for yr in YEARS:
        for b, _wt, dts, _h, _k in blocks:
            left = 0.0
            for t, dt in enumerate(dts, start=1):
                if chronological:
                    ts = _dt.datetime(yr, 1, 1) + _dt.timedelta(hours=left)
                    ti.append((yr, b, t, left, "chronological", True, ts, ts.month, ts.hour))
                else:
                    ti.append((yr, b, t, left, "representative", False, None, None, None))
                left += dt
    tdf = pd.DataFrame(ti, columns=["year", "block", "period", "hour_in_block_h", "time_mode",
                                    "calendar_complete", "timestamp", "month", "hour"])
    tdf["timestamp"] = pd.to_datetime(tdf["timestamp"])
    tdf["month"] = tdf["month"].astype("Int64")
    tdf["hour"] = tdf["hour"].astype("Int64")
    tables["timeindex"] = tdf


def _legacy_documents(d, name, v, obj, gap, nit, capex, built, final, register, blocks, with_qa, checks):
    stop = _LEGACY_STOP[v["stop"]]
    summary = {
        "cost": {"total_gbp": obj, "investment_capex_gbp": capex, "operational_opex_gbp": obj - capex,
                 "total_ex_policy_penalty_gbp": obj},
        "solve": {"lower_bound_gbp": obj * (1 - gap), "relative_gap": gap, "mip_master": False,
                  "integrality_gap": 0.0, "iterations": nit, "converged": v["converged"], "stop_reason": stop,
                  "wall_s": 40.0 * nit, "invariants_passed": True},
        "cost_decomposition": {"fuel_gbp": 0.35 * obj, "vom_gbp": 0.05 * obj, "carbon_gbp": 0.10 * obj,
                               "startup_gbp": 0.08 * obj, "shed_gbp": 0.0, "policy_shortfall_cost_gbp": 0.0},
        "builds_mw_by_technology": {
            "thermal": {"ccgt": built[("thermal", "ccgt_new_b2", final)]},
            "renewable": {"wind_onshore": built[("renewable", "wind_b3", final)],
                          "solar_pv": built[("renewable", "solar_b2", final)]},
            "storage_power": {"li_ion": built[("storage", "bess_b3", final)]},
            "retired_thermal": {"coal": -built[("retirement", "coal_b1", final)]}},
        "storage_energy_mwh_by_technology": {"li_ion": built[("storage_energy", "bess_b3", final)]},
        "cost_basis": {"cost_book": "synthetic-book-1", "commission_year": 2030},
        "hydro_energy": True,
        "labels": [],
    }
    if with_qa:
        summary["qa"] = {"status": "PASS", "mode": "warn", "n_pass": 3, "n_warn": 0, "n_fail": 0, "n_na": 1,
                         "failing": [], "report": "qa_report.toml", "table": "qa_checks.csv"}
    _toml.dump(summary, d / "summary.toml")
    _toml.dump({
        "run": {"name": name, "timestamp": "2026-01-01T00:00:00", "engine_version": "0.0.0-synthetic",
                "seed": 7},
        "config": {"name": name, "gap_tol": 1e-3, "max_iterations": 50},
        "system": {"name": "synthetic-3bus", "buses": len(BUSES), "lines": len(LINES), "hvdc": 0, "thermal": 3,
                   "renewables": 3, "storage": 1, "flex": 0, "scenarios": 1, "blocks": len(blocks),
                   "years": list(YEARS), "meta": {"data_vintage": "synthetic", "topology_revision": "toy-1"}},
        "extra": {"objective_gbp": obj, "lower_bound_gbp": obj * (1 - gap), "gap": gap, "iterations": nit,
                  "converged": v["converged"], "stop_reason": stop, "wall_s": 40.0 * nit},
        "register": register,
        "hydro_energy": True,
        "labels": [],
    }, d / "manifest.toml")
    _toml.dump({"passed": True, "max_balance_residual_mw": 0.0}, d / "validation.toml")
    if with_qa:
        _toml.dump({
            "qa": dict(summary["qa"], deferred=[]),
            "checks": checks,
            "cost_ledger": {"objective_gbp": obj, "investment_cost_gbp": capex, "capex_headline_gbp": capex,
                            "opex_secured_gbp": obj - capex, "residual_gbp": 0.0, "residual_rel": 0.0,
                            "opex_by_class": {"fuel": 0.35 * obj, "vom": 0.05 * obj}},
            "register": register,
        }, d / "qa_report.toml")
