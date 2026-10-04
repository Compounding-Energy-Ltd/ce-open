# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""CE bundle (CE data format 1.0) -> PyPSA network.

Entry points: :func:`export_bundle` (writes a PyPSA CSV folder, or netCDF when
``pypsa`` is installed) and :func:`to_network` (returns a ``pypsa.Network``).

PyPSA describes one deterministic sequence of snapshots, so one scenario and
one investment year (and, for weather-year bundles, one weather year) are
exported at a time. Blocks are laid end to end as snapshots. Each snapshot's
``stores`` weighting is its ``dt_h`` and its ``objective``/``generators``
weighting is ``dt_h * weight_h / sum(dt_h)`` of its block, so the snapshots
add up to the bundle's year. When the bundle came from PyPSA and still agrees
with its ``x_pypsa_snapshots`` table, the original labels and weightings are
restored instead, and annual costs are scaled back to the original horizon.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .bundle import Bundle
from .carriers import carrier_for
from .importer import EXPANSION_SUFFIX, annual_scale
from .pypsa_csv import PypsaTables, write_csv_folder
from .report import ConversionReport

__all__ = ["export_bundle", "to_network", "bundle_to_tables", "crf"]


def _pypsa_version() -> str | None:
    try:
        import pypsa

        return str(pypsa.__version__)
    except ImportError:
        return None


def crf(rate: float, lifetime: float) -> float:
    """Capital recovery factor r(1+r)^n / ((1+r)^n - 1), with its r = 0 and n = inf limits."""
    if rate == 0:
        return 0.0 if math.isinf(lifetime) else 1.0 / lifetime
    if math.isinf(lifetime):
        return rate
    g = (1.0 + rate) ** lifetime
    return rate * g / (g - 1.0)


def _num(v, default=0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) else f


def _flag(v) -> bool:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


class _Ctx:
    def __init__(self, bundle: Bundle, report: ConversionReport, year: int | None, scenario: int,
                 weather: int | None):
        self.b = bundle
        self.r = report
        years = bundle.years or [2030]
        self.year = int(year) if year is not None else years[0]
        if self.year not in years:
            raise ValueError(f"year {self.year} is not one of the bundle's investment years {years}")
        if len(years) > 1:
            report.add("dropped", "time", "investment_years",
                       f"a PyPSA export covers one investment year; exported {self.year}",
                       [y for y in years if y != self.year])
        names = bundle.scenario_names
        if not 1 <= scenario <= len(names):
            raise ValueError(f"scenario {scenario} out of range 1..{len(names)}")
        self.scenario = scenario
        if len(names) > 1:
            report.add("dropped", "scenarios", "names", f"a PyPSA export is deterministic; exported "
                       f"scenario {scenario} ({names[scenario - 1]!r})", [n for i, n in enumerate(names, 1)
                                                                         if i != scenario])
        self.weather = weather
        self.blocks = bundle.blocks
        self.dt = [bundle.block_dt(b) for b in range(1, len(self.blocks) + 1)]
        self.T = int(sum(len(d) for d in self.dt))
        self.offset = np.cumsum([0] + [len(d) for d in self.dt])
        self._snapshots()
        self.dt_mean = float(np.concatenate(self.dt).mean())

    def _snapshots(self) -> None:
        sto = np.concatenate(self.dt)
        obj = np.concatenate([dt * float(blk["weight_h"]) / dt.sum() for blk, dt in zip(self.blocks, self.dt)])
        gen, labels, restored = obj, None, False
        side = self.b.tables.get("x_pypsa_snapshots")
        if side is not None and len(side) == self.T:
            side = side.sort_values(["block", "period"])
            year_h, s = annual_scale(float(side["weight_objective"].sum()))
            consistent = np.allclose(side["weight_stores"].to_numpy(float), sto) and all(
                math.isclose(s * float(side.loc[side["block"] == b, "weight_objective"].sum()), float(blk["weight_h"]),
                             rel_tol=1e-9) for b, blk in enumerate(self.blocks, start=1))
            if consistent and side["label"].is_unique:
                labels = side["label"].astype(str).tolist()
                obj = side["weight_objective"].to_numpy(float)
                gen = side["weight_generators"].to_numpy(float)
                restored = True
        if labels is None:
            labels = self._labels_from_starts()
        self.snap = pd.DataFrame({"objective": obj, "generators": gen, "stores": sto},
                                 index=pd.Index(labels, name="snapshot"))
        self.H = float(obj.sum())
        self.Y, self.s = annual_scale(self.H) if restored else (self.H, 1.0)
        if not restored and not any(math.isclose(self.H, h) for h in (8760.0, 8784.0)):
            self.r.add("approximated", "time", "blocks", f"block weights add up to {self.H:g} h, not a full year; "
                       "annual costs are exported unscaled")

    def _labels_from_starts(self) -> list[str]:
        labels = []
        for b, (blk, dt) in enumerate(zip(self.blocks, self.dt), start=1):
            start = blk.get("start")
            if start is None:
                labels.extend(f"b{b}p{p}" for p in range(1, len(dt) + 1))
                continue
            t0 = pd.Timestamp(start).tz_convert("UTC").tz_localize(None) if pd.Timestamp(start).tzinfo else \
                pd.Timestamp(start)
            offs = np.concatenate([[0.0], np.cumsum(dt)[:-1]])
            labels.extend(str(t0 + pd.Timedelta(hours=float(h))) for h in offs)
        if len(set(labels)) != len(labels):
            labels = [f"b{b}p{p}" for b, dt in enumerate(self.dt, start=1) for p in range(1, len(dt) + 1)]
        return labels

    def series(self, table: str, idcol: str, valcol: str, ids) -> dict[Any, np.ndarray]:
        """Per-snapshot arrays from a long-format table for the chosen scenario / year / weather year."""
        df = self.b.tables.get(table)
        if df is None or not len(df):
            return {}
        df = df[df["scenario"] == self.scenario]
        if "year" in df.columns and df["year"].notna().any():
            df = df[df["year"] == self.year]
        if "weather" in df.columns and df["weather"].notna().any():
            labels = sorted(df["weather"].dropna().unique())
            w = self.weather if self.weather is not None else labels[0]
            if len(labels) > 1:
                self.r.add("dropped", table, "weather", f"one weather year exported ({w})",
                           [x for x in labels if x != w])
            df = df[df["weather"] == w]
        out: dict[Any, np.ndarray] = {}
        wanted = set(ids)
        for cid, g in df.groupby(idcol):
            if cid not in wanted:
                continue
            arr = np.full(self.T, np.nan)
            pos = self.offset[g["block"].to_numpy(int) - 1] + g["period"].to_numpy(int) - 1
            arr[pos] = g[valcol].to_numpy(float)
            if np.isnan(arr).any():
                raise ValueError(f"{table}: {idcol} {cid!r} does not cover every (block, period)")
            out[cid] = arr
        return out


def _unique(names, report: ConversionReport, component: str) -> list[str]:
    seen: dict[str, int] = {}
    out, renamed = [], []
    for n in map(str, names):
        if n in seen:
            seen[n] += 1
            out.append(f"{n}_{seen[n]}")
            renamed.append(n)
        else:
            seen[n] = 0
            out.append(n)
    if renamed:
        report.add("approximated", component, "name", "duplicate names made unique with a numeric suffix", renamed)
    return out


def _wide(snap: pd.DataFrame, data: dict[str, np.ndarray]) -> pd.DataFrame:
    return pd.DataFrame(data, index=snap.index) if data else pd.DataFrame(index=snap.index)


def _set_names(df: pd.DataFrame, col: str, value) -> list[str]:
    if col not in df.columns or not len(df):
        return []
    v = df[col]
    mask = v.map(_flag) if isinstance(value, bool) else pd.to_numeric(v, errors="coerce").fillna(0) != value
    key = "candidate_id" if "candidate_id" in df.columns else "name"
    return list(df.loc[mask.astype(bool), key])


def bundle_to_tables(bundle: Bundle | str | Path, *, year: int | None = None, scenario: int = 1,
                     weather: int | None = None, unit_commitment: bool | None = None,
                     warn: bool = False) -> tuple[PypsaTables, ConversionReport]:
    """Translate a CE bundle into :class:`PypsaTables` plus the conversion report.

    ``unit_commitment``: ``None`` (default) makes thermal units committable
    where the bundle flags them ``x_committable``; ``True`` makes every thermal
    unit committable; ``False`` exports a pure LP (no commitment data).
    """
    if not isinstance(bundle, Bundle):
        bundle = Bundle.read(bundle)
    rep = ConversionReport(direction="ce -> pypsa", source=str(bundle.descriptor.get("bundle", {}).get("name", "")),
                           warn=warn)
    c = _Ctx(bundle, rep, year, scenario, weather)
    snap, s = c.snap, c.s
    money = bundle.descriptor.get("money", {})
    system = bundle.descriptor.get("system", {})
    co2_price = _num(system.get("co2_price_per_t"), 0.0)
    static: dict[str, pd.DataFrame] = {}
    series: dict[str, dict[str, pd.DataFrame]] = {}

    # ---------------------------------------------------------------- buses
    buses = bundle.table("buses")
    bus_names = _unique(buses["name"], rep, "buses")
    bname = dict(zip(buses["id"], bus_names))
    def column(name, default):
        return list(buses[name]) if name in buses.columns else [default] * len(buses)

    vnom = {i: _num(v, 1.0) or 1.0 for i, v in zip(buses["id"], column("voltage_kv", 1.0))}
    static["buses"] = pd.DataFrame({
        "v_nom": [vnom[i] for i in buses["id"]],
        "x": [_num(v) for v in column("lon", 0.0)],
        "y": [_num(v) for v in column("lat", 0.0)],
        "carrier": "AC",
        "location": [z if isinstance(z, str) and z != "default" else "" for z in buses["zone"]],
    }, index=pd.Index(bus_names, name="name"))

    # ---------------------------------------------------------------- lines
    lines = bundle.table("lines")
    if len(lines):
        for col, why in (("contingency", "N-1 contingency membership is not represented"),
                         ("loss_frac", "line losses are not represented in PyPSA's lossless DC power flow"),
                         ("lead_time_years", "construction lead times are not represented")):
            names = _set_names(lines, col, True if col == "contingency" else 0)
            if names:
                rep.add("dropped", "lines", col, why, names)
        ex_flag, cand_flag = lines["existing"].map(_flag), lines["candidate"].map(_flag)
        never = list(lines.loc[~ex_flag & ~cand_flag, "name"])
        if never:
            rep.add("dropped", "lines", "existing", "neither existing nor a candidate: never in service", never)
        rows, names = [], []
        for (_, l), ex, cand in zip(lines.iterrows(), ex_flag, cand_flag):
            if not ex and not cand:
                continue
            rating, delta = float(l["rating_mw"]), _num(l.get("delta_rating_mw"))
            capex = _num(l.get("capex_per_yr"))
            row = {"bus0": bname[l["from_bus"]], "bus1": bname[l["to_bus"]], "carrier": "AC",
                   "x": vnom[l["from_bus"]] ** 2 / (float(l["susceptance_pu"]) * 100.0)}
            length = _num(l.get("length_km"), float("nan"))
            if math.isfinite(length):
                row["length"] = length
            if ex and cand and delta > 0:
                row.update(s_nom=rating, s_nom_extendable=True, s_nom_min=rating, s_nom_max=rating + delta,
                           capital_cost=capex / delta / s)
            elif cand and not ex:
                row.update(s_nom=0.0, s_nom_extendable=True, s_nom_min=0.0, s_nom_max=rating,
                           capital_cost=capex / rating / s)
            else:
                row.update(s_nom=rating, s_nom_extendable=False)
            rows.append(row)
            names.append(l["name"])
        cands = list(lines.loc[cand_flag, "name"])
        if cands:
            rep.add("approximated", "lines", "candidate",
                    "a CE line candidate (one build at capex_per_yr) is exported as continuous expansion at "
                    "capex_per_yr / MW added", cands)
        static["lines"] = pd.DataFrame(rows, index=pd.Index(_unique(names, rep, "lines"), name="name"))

    # ---------------------------------------------------------------- hvdc -> links
    hvdc = bundle.table("hvdc")
    if len(hvdc):
        rows = []
        for _, h in hvdc.iterrows():
            ex, cm = _num(h.get("existing_mw")), _num(h.get("candidate_max_mw"))
            rows.append({"bus0": bname[h["from_bus"]], "bus1": bname[h["to_bus"]], "carrier": "DC",
                         "p_nom": ex, "p_min_pu": -1.0, "efficiency": _num(h.get("converter_efficiency"), 0.985),
                         "p_nom_extendable": cm > 0, "p_nom_min": ex, "p_nom_max": ex + cm,
                         "capital_cost": _num(h.get("capex_per_mw_yr")) / s})
        lossy = list(hvdc.loc[pd.to_numeric(hvdc["converter_efficiency"]) < 1, "name"])
        if lossy:
            rep.add("approximated", "hvdc", "converter_efficiency",
                    "PyPSA applies link efficiency to forward flow only (reverse flow is not charged the loss)", lossy)
        names = _set_names(hvdc, "lead_time_years", 0)
        if names:
            rep.add("dropped", "hvdc", "lead_time_years", "construction lead times are not represented", names)
        static["links"] = pd.DataFrame(rows, index=pd.Index(_unique(hvdc["name"], rep, "links"), name="name"))

    # ---------------------------------------------------------------- generators (+ candidates)
    gens = bundle.table("generators")
    cands = bundle.table("candidates")
    gen_rows: dict[str, dict] = {}
    gen_meta: dict[str, dict] = {}   # name -> technology, kind, co2, flags
    for _, g in gens.iterrows():
        name = str(g["name"])
        row = {"bus": bname[g["bus"]], "carrier": carrier_for(g["technology"]), "p_nom": float(g["existing_mw"]),
               "p_nom_extendable": False, "marginal_cost": _num(g.get("varcost_per_mwh")),
               "capital_cost": _num(g.get("fom_per_mw_yr")) / s}
        gen_rows[name] = row
        gen_meta[name] = {"technology": g["technology"], "kind": g["kind"], "co2": _num(g.get("co2_t_per_mwh")),
                          "unit": {k: g.get(k) for k in ("pmin_frac", "ramp_frac_per_h", "min_up_h", "min_down_h",
                                                         "startup_cost", "noload_cost_per_h")},
                          "committable": _flag(g.get("x_committable")), "must_run": _flag(g.get("x_must_run")),
                          "site": name}
    for col, why in (("retirable", "endogenous retirement is not represented"),
                     ("retirement_year", "announced retirements are not represented")):
        names = _set_names(gens, col, True if col == "retirable" else 0)
        if names:
            rep.add("dropped", "generators", col, why, names)

    sto_rows: dict[str, dict] = {}
    storage = bundle.table("storage")
    for _, st in storage.iterrows():
        name = str(st["name"])
        p, hours = float(st["existing_mw"]), float(st["duration_h"])
        sto_rows[name] = {"bus": bname[st["bus"]], "carrier": carrier_for(st.get("technology", "battery")),
                          "p_nom": p, "p_nom_extendable": False, "max_hours": hours,
                          "efficiency_store": _num(st.get("eta_charge"), 0.95),
                          "efficiency_dispatch": _num(st.get("eta_discharge"), 0.95),
                          "standing_loss": _num(st.get("self_discharge_frac_per_h")),
                          "marginal_cost": _num(st.get("varcost_per_mwh")), "p_min_pu": -1.0,
                          "cyclic_state_of_charge": True,
                          "state_of_charge_initial": _num(st.get("soc_init_frac"), 0.5) * p * hours,
                          "capital_cost": _num(st.get("x_fom_per_mw_yr")) / s}
    dod = _set_names(storage, "dod_floor_frac", 0)
    if dod:
        rep.add("dropped", "storage", "dod_floor_frac", "a depth-of-discharge floor is not represented", dod)
    if len(storage):
        rep.add("approximated", "storage", "soc_init_frac",
                "exported as cyclic_state_of_charge = True (PyPSA leaves the cyclic level free)")

    default_rate = money.get("default_annualisation_rate")
    default_life = money.get("default_lifetime_years")
    merged, hybrids, integer, decoupled, energy_cost = [], [], [], [], []
    for _, cd in cands.iterrows():
        cid, kind = str(cd["candidate_id"]), str(cd["kind"])
        if kind == "hybrid":
            hybrids.append(cid)
            continue
        if _flag(cd.get("integer_build")):
            integer.append(cid)
        rate = _num(cd.get("annualisation_rate"), float("nan"))
        life = _num(cd.get("lifetime_years"), float("nan"))
        rate = rate if not math.isnan(rate) else _num(default_rate, float("nan"))
        life = life if not math.isnan(life) else _num(default_life, float("nan"))
        factor = crf(rate, life) if not (math.isnan(rate) or math.isnan(life)) else float("nan")
        annual = _num(cd.get("capex_per_mw_yr"), float("nan"))
        if math.isnan(annual):
            overnight = _num(cd.get("capex_overnight_per_mw"), float("nan"))
            if math.isnan(overnight) or math.isnan(factor):
                raise ValueError(f"candidate {cid}: no capex_per_mw_yr and no annualisable overnight cost")
            annual = overnight * factor
        ic = _num(cd.get("interconnection_cost_per_kw"))
        if ic > 0:
            if math.isnan(factor):
                raise ValueError(f"candidate {cid}: interconnection cost needs annualisation inputs")
            annual += ic * 1000.0 * factor
        annual += _num(cd.get("fom_per_mw_yr"))
        max_mw = float(cd["max_mw"])
        if kind == "storage":
            hours = _num(cd.get("duration_h"), float("nan"))
            if not math.isnan(_num(cd.get("max_mwh"), float("nan"))):
                decoupled.append(cid)
                if math.isnan(hours):
                    hours = _num(cd.get("max_mwh")) / max_mw
            if _num(cd.get("capex_per_mwh_yr")) > 0:
                energy_cost.append(cid)
                annual += _num(cd.get("capex_per_mwh_yr")) * hours
            stem = cid[: -len(EXPANSION_SUFFIX)] if cid.endswith(EXPANSION_SUFFIX) else None
            if stem in sto_rows and sto_rows[stem]["bus"] == bname[cd["bus"]]:
                row = sto_rows[stem]
                row.update(p_nom_extendable=True, p_nom_min=row["p_nom"], p_nom_max=row["p_nom"] + max_mw,
                           capital_cost=annual / s)
                merged.append(cid)
                continue
            sto_rows[cid] = {"bus": bname[cd["bus"]], "carrier": carrier_for(cd["technology"]), "p_nom": 0.0,
                             "p_nom_extendable": True, "p_nom_min": 0.0, "p_nom_max": max_mw, "max_hours": hours,
                             "efficiency_store": _num(cd.get("x_eta_charge"), 0.95),
                             "efficiency_dispatch": _num(cd.get("x_eta_discharge"), 0.95),
                             "standing_loss": _num(cd.get("x_self_discharge_frac_per_h")),
                             "marginal_cost": _num(cd.get("varcost_per_mwh")), "p_min_pu": -1.0,
                             "cyclic_state_of_charge": True,
                             "state_of_charge_initial": _num(cd.get("x_soc_init_frac"), 0.5) * max_mw * hours,
                             "capital_cost": annual / s}
            continue
        stem = cid[: -len(EXPANSION_SUFFIX)] if cid.endswith(EXPANSION_SUFFIX) else None
        if stem in gen_rows and gen_rows[stem]["bus"] == bname[cd["bus"]] and \
                gen_meta[stem]["technology"] == cd["technology"]:
            row = gen_rows[stem]
            row.update(p_nom_extendable=True, p_nom_min=row["p_nom"], p_nom_max=row["p_nom"] + max_mw,
                       capital_cost=annual / s)
            merged.append(cid)
            continue
        gen_rows[cid] = {"bus": bname[cd["bus"]], "carrier": carrier_for(cd["technology"]), "p_nom": 0.0,
                         "p_nom_extendable": True, "p_nom_min": 0.0, "p_nom_max": max_mw,
                         "marginal_cost": _num(cd.get("varcost_per_mwh")), "capital_cost": annual / s}
        gen_meta[cid] = {"technology": cd["technology"], "kind": kind, "co2": _num(cd.get("co2_t_per_mwh")),
                         "unit": {k: cd.get(f"x_{k}") for k in ("pmin_frac", "ramp_frac_per_h", "min_up_h",
                                                                "min_down_h", "startup_cost", "noload_cost_per_h")},
                         "committable": _flag(cd.get("x_committable")), "must_run": _flag(cd.get("x_must_run")),
                         "site": cid}
    if merged:
        rep.add("approximated", "candidates", "candidate_id",
                f"a '<name>{EXPANSION_SUFFIX}' candidate is merged with the existing unit <name> into one extendable "
                "PyPSA component (capital cost applies to its whole optimised capacity, a constant on the existing "
                "part)", merged)
    if hybrids:
        rep.add("dropped", "candidates", "kind", "hybrid candidates have no PyPSA equivalent here", hybrids)
    if integer:
        rep.add("approximated", "candidates", "integer_build", "exported as continuous expansion", integer)
    if decoupled:
        rep.add("approximated", "candidates", "max_mwh",
                "PyPSA StorageUnits have a fixed energy/power ratio; independently sized energy is not exported",
                decoupled)
    if energy_cost:
        rep.add("approximated", "candidates", "capex_per_mwh_yr",
                "energy capex folded into the per-MW capital cost at duration_h", energy_cost)
    names = _set_names(cands, "lead_time_years", 0)
    if names:
        rep.add("dropped", "candidates", "lead_time_years", "construction lead times are not represented", names)

    # thermal unit data, CO2 and carriers
    carriers_co2: dict[str, float] = {}
    thermal = [n for n, m in gen_meta.items() if m["kind"] == "thermal"]
    for n in thermal:
        tech = gen_rows[n]["carrier"]
        carriers_co2[tech] = max(carriers_co2.get(tech, 0.0), gen_meta[n]["co2"])
    spread, zero_in_emitting, uc_lost = [], [], []
    for n in thermal:
        row, meta = gen_rows[n], gen_meta[n]
        top, co2 = carriers_co2[row["carrier"]], meta["co2"]
        if top > 0 and co2 > 0:
            row["efficiency"] = top / co2
            if not math.isclose(row["efficiency"], 1.0):
                spread.append(n)
        elif top > 0:
            zero_in_emitting.append(n)
            row["carrier"] = f"{row['carrier']} zero-co2"
            carriers_co2[row["carrier"]] = 0.0
        if co2_price > 0:
            row["marginal_cost"] += co2_price * co2
        u = meta["unit"]
        pmin = _num(u.get("pmin_frac"))
        ramp = _num(u.get("ramp_frac_per_h"), float("nan"))
        if math.isfinite(ramp) and ramp * c.dt_mean < 1.0:
            row["ramp_limit_up"] = row["ramp_limit_down"] = ramp * c.dt_mean
        commit = unit_commitment if unit_commitment is not None else meta["committable"]
        row["committable"] = bool(commit)
        if commit:
            row.update(committable=True, p_min_pu=pmin,
                       min_up_time=int(round(_num(u.get("min_up_h")) / c.dt_mean)),
                       min_down_time=int(round(_num(u.get("min_down_h")) / c.dt_mean)),
                       start_up_cost=_num(u.get("startup_cost")), stand_by_cost=_num(u.get("noload_cost_per_h")))
        elif meta["must_run"] and unit_commitment is None:
            row["p_min_pu"] = pmin
        elif pmin > 0 or _num(u.get("startup_cost")) > 0 or _num(u.get("noload_cost_per_h")) > 0:
            uc_lost.append(n)
    if spread:
        rep.add("approximated", "generators", "co2_t_per_mwh",
                "CO2 intensity differs within a carrier: carrier co2_emissions is the maximum and generator "
                "efficiency = maximum / own intensity (marginal_cost is exported directly)", spread)
    if zero_in_emitting:
        rep.add("approximated", "generators", "co2_t_per_mwh",
                "zero-emission unit of an emitting carrier exported under a separate '<carrier> zero-co2' carrier",
                zero_in_emitting)
    if uc_lost:
        rep.add("dropped", "generators", "pmin_frac", "unit-commitment data not exported for units that are not "
                "committable (pass unit_commitment=True to export every thermal unit as committable)", uc_lost)
    if co2_price > 0:
        rep.add("approximated", "system", "co2_price_per_t", "carbon price added to thermal marginal_cost")
    if gen_rows:
        names = _unique(list(gen_rows), rep, "generators")
        static["generators"] = pd.DataFrame(list(gen_rows.values()), index=pd.Index(names, name="name"))
        sites = {gen_meta[n]["site"]: out for n, out in zip(gen_rows, names) if gen_meta[n]["kind"] == "renewable"}
        av = c.series("availability", "site_name", "cf", list(sites))
        pmax, missing = {}, []
        for site, out in sites.items():
            if site in av:
                pmax[out] = av[site]
            else:
                stem = site[: -len(EXPANSION_SUFFIX)] if site.endswith(EXPANSION_SUFFIX) else None
                if stem is not None and stem in av:
                    pmax[out] = av[stem]
                else:
                    pmax[out] = np.zeros(c.T)
                    missing.append(site)
        if missing:
            rep.add("defaulted", "availability", "cf",
                    "renewable resource without an availability series exported with p_max_pu = 0", missing)
        if pmax:
            series.setdefault("generators", {})["p_max_pu"] = _wide(snap, pmax)
    for n, row in gen_rows.items():
        carriers_co2.setdefault(row["carrier"], 0.0)
    if sto_rows:
        names = _unique(list(sto_rows), rep, "storage_units")
        static["storage_units"] = pd.DataFrame(list(sto_rows.values()), index=pd.Index(names, name="name"))
        for row in sto_rows.values():
            carriers_co2.setdefault(row["carrier"], 0.0)
    if any(_num(v) > 0 for v in [system.get("planning_reserve_margin")]):
        rep.add("dropped", "system", "planning_reserve_margin",
                "PyPSA has no planning reserve margin; it and firm_capacity_frac are not exported")

    # ---------------------------------------------------------------- loads
    dem = c.series("demand", "bus", "mw", list(buses["id"]))
    if dem:
        load_names = [bname[b] for b in buses["id"] if b in dem]
        static["loads"] = pd.DataFrame({"bus": load_names}, index=pd.Index(load_names, name="name"))
        series["loads"] = {"p_set": _wide(snap, {bname[b]: dem[b] for b in buses["id"] if b in dem})}

    # ---------------------------------------------------------------- carriers + global constraints
    if carriers_co2:
        static["carriers"] = pd.DataFrame({"co2_emissions": list(carriers_co2.values())},
                                          index=pd.Index(list(carriers_co2), name="name"))
    gcs = {}
    w_gen = float(snap["generators"].sum())
    all_zones = set(buses["zone"].dropna().astype(str)) if "zone" in buses.columns else set()
    for pol in bundle.descriptor.get("x_policy", []):
        name = str(pol.get("name", f"policy{len(gcs) + 1}"))
        zones = set(map(str, pol.get("zones") or ()))
        partial_zones = bool(zones) and zones != all_zones   # a zones list naming every zone is system-wide
        scoped = partial_zones or pol.get("technologies") or (pol.get("years") and c.year not in pol["years"])
        if pol.get("kind") == "emission_cap_t" and str(pol.get("pollutant", "co2")) == "co2" and not scoped:
            gcs[name] = {"type": "primary_energy", "carrier_attribute": "co2_emissions", "sense": "<=",
                         "constant": float(pol["value"]) * w_gen / c.Y}
        else:
            rep.add("dropped", "x_policy", str(pol.get("kind", "")),
                    "only system-wide CO2 caps translate to PyPSA global constraints", [name])
    if gcs:
        static["global_constraints"] = pd.DataFrame.from_dict(gcs, orient="index").rename_axis("name")
    # nothing is lost silently: every other extension section of bundle.toml is reported as not applied
    for key in sorted(k for k in bundle.descriptor if k.startswith("x_") and k not in APPLIED_DESCRIPTOR_EXTENSIONS):
        val = bundle.descriptor[key]
        items = sorted(val) if isinstance(val, dict) else [str(len(val))] if isinstance(val, list) else []
        rep.add("dropped", key, "", f"bundle.toml extension section [{key}] is not applied by the PyPSA exporter",
                items)
    rep.add("dropped", "system", "voll_per_mwh",
            "PyPSA has no built-in value of lost load; add load-shedding generators if needed")
    if money:
        rep.add("dropped", "money", "currency", f"PyPSA records no currency: costs are in {money.get('currency')} "
                f"of {money.get('dollar_year')} (state it again when re-importing)")

    tables = PypsaTables(name=str(bundle.descriptor.get("bundle", {}).get("name", "ce_bundle")), snapshots=snap,
                         static=static, series=series)
    rep.stats.update({f"{k}_rows": int(len(v)) for k, v in static.items()})
    rep.stats["snapshots"] = c.T
    return tables, rep


#: bundle.toml extension sections the exporter reads (``x_pypsa`` is the importer's own provenance record)
APPLIED_DESCRIPTOR_EXTENSIONS = frozenset({"x_policy", "x_pypsa"})


def export_bundle(bundle: Bundle | str | Path, out: str | Path | None = None, *, year: int | None = None,
                  scenario: int = 1, weather: int | None = None, unit_commitment: bool | None = None,
                  warn: bool = False) -> tuple[PypsaTables, ConversionReport]:
    """Export a CE bundle to PyPSA.

    ``out`` ending in ``.nc`` writes netCDF (needs ``pypsa``); any other path is
    written as a PyPSA CSV folder, with the conversion report alongside as
    ``x_conversion_report.toml`` (PyPSA ignores it).
    """
    tables, rep = bundle_to_tables(bundle, year=year, scenario=scenario, weather=weather,
                                   unit_commitment=unit_commitment, warn=warn)
    if out is not None:
        out = Path(out)
        if out.suffix == ".nc":
            to_network(tables).export_to_netcdf(str(out))
        else:
            write_csv_folder(tables, out, pypsa_version=_pypsa_version())
            from . import _toml

            _toml.dump(rep.as_dict(), out / "x_conversion_report.toml",
                       header="What the converter approximated or dropped. Informational; not read by PyPSA.")
    return tables, rep


def to_network(bundle_or_tables: Any, **kwargs):
    """Build a ``pypsa.Network`` (needs ``pypsa``) from a bundle, a bundle path or :class:`PypsaTables`."""
    import tempfile

    import pypsa

    tables = bundle_or_tables if isinstance(bundle_or_tables, PypsaTables) else bundle_to_tables(
        bundle_or_tables, **kwargs)[0]
    with tempfile.TemporaryDirectory() as tmp:
        write_csv_folder(tables, tmp, pypsa_version=_pypsa_version())
        return pypsa.Network(tmp)
