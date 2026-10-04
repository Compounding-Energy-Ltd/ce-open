# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""PyPSA network -> CE bundle (CE data format 1.0).

Entry point: :func:`import_network`. Every approximation or omission is
recorded in the bundle's conversion report; README.md lists the mappings.

Time and money conventions:

* ``H`` is the modelled horizon in hours, the sum of the snapshot
  ``objective`` weightings. PyPSA charges ``capital_cost`` once for the whole
  horizon; a CE bundle is per year, with block weights that add up to a full
  year ``Y`` (8760 h, or 8784 h when the network already spans a leap year).
  The importer scales the block weights AND every fixed (per-MW) cost by
  ``s = Y / H``. The CE objective is then exactly ``s`` times the PyPSA
  objective, so optimal decisions are unchanged. With ``H = 8760`` (the usual
  case) ``s = 1`` and nothing is scaled.
* A snapshot's ``stores`` weighting is its duration (``dt_h``). A block has
  one ``weight_h`` spread pro rata to duration, so per-snapshot objective
  weights are kept exactly when ``objective / stores`` is constant within the
  block (true for uniform weightings and for most clustered networks).
"""

from __future__ import annotations

import datetime as _dt
import math
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .bundle import FORMAT_NAME, FORMAT_VERSION, Bundle, with_defaults
from .carriers import ELECTRIC_BUS_CARRIERS, RENEWABLE_TECHNOLOGIES, TECHNOLOGY, technology_for
from .pypsa_csv import PypsaTables, UnsupportedNetworkError, read_csv_folder
from .report import ConversionReport

__all__ = ["import_network", "load_source", "EXPANSION_SUFFIX", "annual_scale"]

#: candidate_id suffix for the expansion part of a PyPSA asset that also has existing capacity
EXPANSION_SUFFIX = "__expansion"
BASE_MVA = 100.0  # the specification's per-unit base for susceptance_pu


def converter_version() -> str:
    try:
        return _pkg_version("ce-pypsa")
    except PackageNotFoundError:
        from . import __version__

        return __version__


def annual_scale(horizon_h: float) -> tuple[float, float]:
    """(Y, s): the full-year length the bundle's block weights add up to, and ``s = Y / horizon_h``."""
    year_h = 8784.0 if math.isclose(horizon_h, 8784.0, rel_tol=1e-9) else 8760.0
    return year_h, year_h / horizon_h


# --------------------------------------------------------------------------- source loading
def _pypsa_tables(network) -> tuple[PypsaTables, dict[str, pd.Series]]:
    """PypsaTables from a live ``pypsa.Network``, plus effective per-unit reactances (1 MVA base)."""
    import tempfile

    x_eff: dict[str, pd.Series] = {}
    network.calculate_dependent_values()
    for list_name in ("lines", "transformers"):
        df = getattr(network, list_name)
        if len(df) and "x_pu_eff" in df.columns:
            x_eff[list_name] = df["x_pu_eff"].astype(float).rename(index=str)
    with tempfile.TemporaryDirectory() as tmp:
        network.export_to_csv_folder(tmp)
        tables = read_csv_folder(tmp)
    return tables, x_eff


def _needs_line_types(tables: PypsaTables) -> bool:
    for list_name in ("lines", "transformers"):
        df = tables.get(list_name)
        if "type" in df.columns and df["type"].fillna("").astype(str).str.len().gt(0).any():
            return True
    return False


def load_source(source: Any) -> tuple[PypsaTables, dict[str, pd.Series]]:
    """Accept a CSV folder, a ``.nc``/``.h5`` file, a ``pypsa.Network`` or :class:`PypsaTables`."""
    if isinstance(source, PypsaTables):
        return source, {}
    if hasattr(source, "export_to_csv_folder"):  # a pypsa.Network
        return _pypsa_tables(source)
    path = Path(source)
    if path.is_file() and path.suffix in (".nc", ".h5", ".hdf5"):
        try:
            import pypsa
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError("reading netCDF/HDF5 networks needs pypsa: pip install 'ce-pypsa[pypsa]'") from exc
        return _pypsa_tables(pypsa.Network(str(path)))
    tables = read_csv_folder(path)
    if _needs_line_types(tables):  # standard line/transformer types: PyPSA resolves their reactance
        try:
            import pypsa
        except ImportError as exc:
            raise UnsupportedNetworkError(
                "lines or transformers use a standard 'type'; resolving their reactance needs pypsa "
                "(pip install 'ce-pypsa[pypsa]')") from exc
        _, x_eff = _pypsa_tables(pypsa.Network(str(path)))
        return tables, x_eff
    return tables, {}


# --------------------------------------------------------------------------- column helpers
def _col(df: pd.DataFrame, name: str, default) -> pd.Series:
    """Column ``name`` with blanks replaced by ``default`` (typed like the default)."""
    if name not in df.columns:
        return pd.Series([default] * len(df), index=df.index, dtype=object if isinstance(default, str) else None)
    s = df[name]
    if isinstance(default, bool):
        return s.map(lambda v: default if pd.isna(v) or v == "" else str(v).strip().lower() in ("true", "1", "1.0")
                     ).astype(bool)
    if isinstance(default, str):
        return s.where(s.notna(), default).astype(str)
    return pd.to_numeric(s, errors="coerce").fillna(default).astype(float)


def _active(df: pd.DataFrame) -> pd.Series:
    return _col(df, "active", True)


def _nondefault(df: pd.DataFrame, attr: str, default) -> list[str]:
    """Names of rows whose ``attr`` differs from ``default`` (NaN default: any value set)."""
    if attr not in df.columns or not len(df):
        return []
    s = df[attr]
    if isinstance(default, str):
        mask = s.notna() & (s.astype(str) != default) & (s.astype(str) != "")
    else:
        v = pd.to_numeric(s, errors="coerce")
        if isinstance(default, float) and math.isnan(default):
            mask = v.notna()
        elif isinstance(default, float) and math.isinf(default):
            mask = v.notna() & ~np.isinf(v)
        else:
            mask = v.notna() & (v != default)
    return list(df.index[mask])


def _annuity(rate: float, lifetime: float) -> float:
    """PyPSA's annuity factor r / (1 - (1 + r)^-n) with its zero-rate and infinite-life limits."""
    if lifetime is None or math.isnan(lifetime) or lifetime <= 0:
        lifetime = math.inf
    if rate == 0:
        return 0.0 if math.isinf(lifetime) else 1.0 / lifetime
    if math.isinf(lifetime):
        return max(rate, 0.0)
    return rate / (1.0 - (1.0 + rate) ** (-lifetime))


class _Names:
    """Allocates unique names within one CE name space."""

    def __init__(self, report: ConversionReport, component: str):
        self.used: set[str] = set()
        self.r = report
        self.component = component

    def take(self, name: str) -> str:
        out, k = name, 1
        while out in self.used:
            k += 1
            out = f"{name}_{k}"
        if out != name:
            self.r.add("approximated", self.component, "name",
                       "name already used in the CE name space; a numeric suffix was added", [name])
        self.used.add(out)
        return out


VINTAGE_ATTRS = (("build_year", 0.0, "single-period import; vintages are not represented"),
                 ("lifetime", float("inf"), "single-period import; vintages are not represented"))


class _Ctx:
    """Shared state for one import."""

    def __init__(self, tables: PypsaTables, x_eff: dict, report: ConversionReport, opts: dict):
        self.t = tables
        self.x_eff = x_eff
        self.r = report
        self.o = opts
        snaps = tables.snapshots
        self.labels = list(snaps.index)
        self.w_obj = snaps["objective"].to_numpy(float)
        self.w_gen = snaps["generators"].to_numpy(float)
        self.w_sto = snaps["stores"].to_numpy(float)
        self.T = len(self.labels)
        self.H = float(self.w_obj.sum())
        if self.H <= 0:
            raise UnsupportedNetworkError("snapshot objective weightings sum to zero")
        if (self.w_sto <= 0).any():
            raise UnsupportedNetworkError("snapshot 'stores' weightings (durations) must be positive")
        self.Y, self.s = annual_scale(self.H)
        self.nyears = self.H / 8760.0   # PyPSA's n.nyears, used for overnight-cost annuities
        self.dt_mean = float(self.w_sto.mean())
        self.block_of, self.period_of = self._blocks()
        self.names = _Names(report, "generators/storage/candidates")   # one name space in CE
        self.branch_names = _Names(report, "lines/hvdc")   # flows.line names either, so one name space too

    def _blocks(self):
        bh = self.o["block_hours"]
        if bh is None:
            return np.ones(self.T, int), np.arange(1, self.T + 1)
        if bh <= 0 or self.T % bh:
            raise ValueError(f"block_hours={bh} must divide the {self.T} snapshots exactly")
        idx = np.arange(self.T)
        return idx // bh + 1, idx % bh + 1

    def series(self, list_name: str, attr: str) -> pd.DataFrame | None:
        return self.t.get_series(list_name, attr)

    def long(self, idcol: str, valcol: str, values: dict) -> pd.DataFrame:
        """Long-format series rows: ``values`` maps an id to a per-snapshot array."""
        frames = [pd.DataFrame({idcol: key, "scenario": 1, "block": self.block_of, "period": self.period_of,
                                valcol: np.asarray(v, float)}) for key, v in values.items()]
        if not frames:
            return pd.DataFrame(columns=[idcol, "scenario", "block", "period", valcol])
        return pd.concat(frames, ignore_index=True)

    def weighted_mean(self, values: np.ndarray) -> float:
        return float(np.dot(values, self.w_obj) / self.H)

    def fixed_costs(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """(capex, fom) per MW per CE year, following PyPSA's capital/overnight/FOM precedence."""
        cap = _col(df, "capital_cost", 0.0)
        if "overnight_cost" in df.columns:
            on = pd.to_numeric(df["overnight_cost"], errors="coerce")
            rate = _col(df, "discount_rate", float("nan"))
            life = _col(df, "lifetime", float("inf"))
            for name in df.index[on.notna()]:
                if math.isnan(rate[name]):
                    raise UnsupportedNetworkError(f"{name}: overnight_cost is set but discount_rate is missing")
                cap[name] = on[name] * _annuity(rate[name], life[name]) * self.nyears
        return cap * self.s, _col(df, "fom_cost", 0.0) * self.s

    def static_or_series(self, list_name: str, attr: str, df: pd.DataFrame, default: float) -> dict[str, np.ndarray]:
        """Per-snapshot values of ``attr`` for each row of ``df`` (a series overrides the static value)."""
        static = _col(df, attr, default)
        ts = self.series(list_name, attr)
        out = {}
        for name in df.index:
            if ts is not None and name in ts.columns:
                out[name] = ts[name].to_numpy(float)
            else:
                out[name] = np.full(self.T, float(static[name]))
        return out

    def capacity_split(self, df: pd.DataFrame, comp: str, nom: str) -> tuple[pd.Series, pd.Series]:
        """(existing, expandable) in nominal units; PyPSA ignores ``p_nom`` on extendable components."""
        ext = _col(df, f"{nom}_extendable", False)
        fixed = _col(df, nom, 0.0)
        lo = _col(df, f"{nom}_min", 0.0)
        hi = _col(df, f"{nom}_max", float("inf"))
        cap = self.o["inf_capacity_mw"]
        unbounded = list(df.index[ext & np.isinf(hi)])
        if unbounded:
            self.r.add("defaulted", comp, f"{nom}_max",
                       f"unbounded expansion capped at {cap:g} MW (CE needs a finite build envelope)", unbounded)
        hi = hi.where(~np.isinf(hi), np.maximum(cap, lo))
        existing = fixed.where(~ext, lo)
        expand = (hi - lo).clip(lower=0.0).where(ext, 0.0)
        sunk = list(df.index[ext & (lo > 0) & (_col(df, "capital_cost", 0.0) > 0)])
        if sunk:
            self.r.add("approximated", comp, f"{nom}_min",
                       f"extendable with {nom}_min > 0: the minimum becomes existing capacity (its capital cost is a "
                       "constant, carried as fixed O&M) and the rest a candidate; decisions are unchanged", sunk)
        return existing, expand

    def drop_if_set(self, comp: str, df: pd.DataFrame, attrs) -> None:
        for attr, default, why in attrs:
            names = _nondefault(df, attr, default)
            if names:
                self.r.add("dropped", comp, attr, why, names)

    def drop_series(self, comp: str, list_name: str, df: pd.DataFrame, attrs, kind="dropped", why=None) -> None:
        for attr in attrs:
            ts = self.series(list_name, attr)
            if ts is not None and len(set(ts.columns) & set(df.index)):
                self.r.add(kind, comp, f"{attr}(t)", why or "time-varying value is not represented",
                           sorted(set(ts.columns) & set(df.index)))


# --------------------------------------------------------------------------- time axis
def _parse_times(labels: list[str]) -> list[_dt.datetime] | None:
    try:
        ts = pd.to_datetime(pd.Series(labels), errors="raise", format="mixed")
    except (ValueError, TypeError):
        return None
    if ts.isna().any():
        return None
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    return [t.to_pydatetime().astimezone(_dt.timezone.utc) for t in ts]


def _time_axis(c: _Ctx) -> list[dict]:
    times = _parse_times(c.labels)
    blocks = []
    ratio = c.w_obj / c.w_sto
    for b in range(1, int(c.block_of.max()) + 1):
        m = np.flatnonzero(c.block_of == b)
        dt = c.w_sto[m]
        weight = float(c.w_obj[m].sum() * c.s)
        blk: dict = {"id": b, "name": "snapshots" if c.o["block_hours"] is None else f"block_{b}",
                     "nperiods": int(len(m))}
        blk["dt_h"] = float(dt[0]) if np.allclose(dt, dt[0]) else [float(x) for x in dt]
        blk["weight_h"] = weight
        contiguous = times is not None and all(
            math.isclose((times[m[i + 1]] - times[m[i]]).total_seconds() / 3600.0, dt[i], rel_tol=1e-9)
            for i in range(len(m) - 1))
        chronological = contiguous and math.isclose(weight, float(dt.sum()), rel_tol=1e-9)
        blk["kind"] = "chronological" if chronological else "representative"
        if times is not None:
            blk["start"] = times[m[0]]
        blocks.append(blk)
        if not np.allclose(ratio[m], ratio[m][0], rtol=1e-9):
            c.r.add("approximated", "snapshots", "objective",
                    "objective/stores weighting ratio varies within a block; CE spreads the block weight pro "
                    "rata to duration, so per-snapshot cost weights are not kept", [f"block {b}"])
    if times is not None:
        c.r.add("defaulted", "snapshots", "timezone", "PyPSA snapshots carry no time zone; read as UTC")
    if not math.isclose(c.s, 1.0, rel_tol=1e-12):
        c.r.add("approximated", "snapshots", "objective",
                f"the {c.H:g} h horizon is scaled by {c.s:.6g} to a {c.Y:g} h year (block weights and fixed costs "
                "alike); decisions are unchanged and the CE objective is the PyPSA objective times that factor")
    if not np.allclose(c.w_gen, c.w_obj, rtol=1e-9):
        c.r.add("approximated", "snapshots", "generators",
                "'generators' weightings differ from 'objective' weightings; CE has one block weight, so "
                "emissions are weighted like costs")
    if c.o["block_hours"] is not None:
        c.r.add("approximated", "snapshots", "chronology",
                f"snapshots chunked into blocks of {c.o['block_hours']} periods; storage continuity across block "
                "boundaries follows the CE model's settings, not PyPSA's single sequence")
    return blocks


# --------------------------------------------------------------------------- network
def _buses(c: _Ctx):
    df = c.t.get("buses")
    carrier = _col(df, "carrier", "AC").str.strip().str.lower()
    keep = carrier.isin(ELECTRIC_BUS_CARRIERS)
    if (~keep).any():
        c.r.add("dropped", "buses", "carrier", "non-electric buses (and everything attached to them) are not imported",
                list(df.index[~keep]))
    df = df[keep]
    ids = {name: i + 1 for i, name in enumerate(df.index)}
    zone = pd.Series("default", index=df.index, dtype=object)
    for attr in ("location", "country"):  # country wins when both are present
        if attr in df.columns:
            v = df[attr].fillna("").astype(str)
            zone = zone.where(v == "", v)
    lon, lat = _col(df, "x", 0.0), _col(df, "y", 0.0)
    geo = (lat.abs() <= 90) & (lon.abs() <= 180)
    if (~geo).any():
        c.r.add("dropped", "buses", "x/y", "coordinates outside WGS 84 degree ranges (projected?) are not kept",
                list(df.index[~geo]))
    vnom = _col(df, "v_nom", 1.0)
    out = pd.DataFrame({
        "id": [ids[n] for n in df.index], "name": list(df.index), "zone": zone.to_numpy(),
        "lat": lat.where(geo).to_numpy(), "lon": lon.where(geo).to_numpy(),
        "voltage_kv": vnom.where(vnom > 0).to_numpy(),
    })
    dc = set(df.index[(carrier[keep] == "dc").to_numpy()])
    return out, ids, dc


def _branches(c: _Ctx, bus_ids: dict[str, int], dc_buses: set[str]):
    """Lines and transformers -> CE lines; lines between DC buses -> corridors (returned for hvdc)."""
    rows, dc_rows = [], []
    names = c.branch_names
    vnom = _col(c.t.get("buses"), "v_nom", 1.0)
    for list_name in ("lines", "transformers"):
        df = c.t.get(list_name)
        if not len(df):
            continue
        df = df[_active(df)]
        ok = df["bus0"].astype(str).isin(bus_ids.keys()) & df["bus1"].astype(str).isin(bus_ids.keys())
        if (~ok).any():
            c.r.add("dropped", list_name, "bus0/bus1", "connects a bus that was not imported", list(df.index[~ok]))
        df = df[ok]
        existing, expand = c.capacity_split(df, list_name, "s_nom")
        smax = _col(df, "s_max_pu", 1.0)
        smax_ts = c.series(list_name, "s_max_pu")
        capex, fom = c.fixed_costs(df)
        x = _col(df, "x", 0.0)
        length = _col(df, "length", float("nan"))
        for attr in ("r", "g", "b"):
            dropped = _nondefault(df, attr, 0.0)
            if dropped:
                c.r.add("dropped", list_name, attr, "the DC power-flow model uses reactance only", dropped)
        c.drop_if_set(list_name, df, VINTAGE_ATTRS)
        if list_name == "transformers":
            c.drop_if_set(list_name, df, (("phase_shift", 0.0, "phase shift is not represented"),))
            if "transformers" not in c.x_eff:
                c.drop_if_set(list_name, df, (("tap_ratio", 1.0, "tap ratio is not represented"),))
            c.r.add("approximated", list_name, "component", "transformers are imported as CE lines", list(df.index))
        if smax_ts is not None and len(set(smax_ts.columns) & set(df.index)):
            c.r.add("approximated", list_name, "s_max_pu(t)",
                    "time-varying rating replaced by its minimum (CE ratings are static)",
                    sorted(set(smax_ts.columns) & set(df.index)))
        for name in df.index:
            b0, b1 = str(df.at[name, "bus0"]), str(df.at[name, "bus1"])
            sm = float(smax_ts[name].min()) if smax_ts is not None and name in smax_ts.columns else float(smax[name])
            ex, exp_ = float(existing[name]), float(expand[name])
            per_mw = float(capex[name] + fom[name])
            if b0 in dc_buses and b1 in dc_buses and list_name == "lines":
                dc_rows.append((name, b0, b1, ex * sm, exp_ * sm, per_mw / sm if sm > 0 else 0.0))
                continue
            if list_name in c.x_eff and name in c.x_eff[list_name].index:
                xpu = float(c.x_eff[list_name][name])
            elif list_name == "lines":
                xpu = float(x[name]) / float(vnom.get(b0, 1.0)) ** 2
            else:
                s_nom = float(_col(df, "s_nom", 0.0)[name])
                if s_nom <= 0:
                    raise UnsupportedNetworkError(f"transformer {name}: s_nom must be > 0 to convert per-unit x")
                xpu = float(x[name]) / s_nom
            if not xpu > 0:
                raise UnsupportedNetworkError(f"{list_name[:-1]} {name}: reactance must be positive")
            if ex * sm <= 0 and exp_ * sm <= 0:
                c.r.add("dropped", list_name, "s_nom", "zero rating and no expansion: never carries flow", [name])
                continue
            row = {"name": names.take(name), "from_bus": bus_ids[b0], "to_bus": bus_ids[b1],
                   "susceptance_pu": 1.0 / xpu / BASE_MVA, "loss_frac": 0.0, "contingency": False}
            if math.isfinite(length[name]) and length[name] > 0:
                row["length_km"] = float(length[name])
            if exp_ <= 0:
                row.update(existing=True, candidate=False, rating_mw=ex * sm, delta_rating_mw=0.0, capex_per_yr=0.0)
            elif ex > 0:   # reinforcement of an existing branch
                row.update(existing=True, candidate=True, rating_mw=ex * sm, delta_rating_mw=exp_ * sm,
                           capex_per_yr=per_mw * exp_)
            else:          # greenfield candidate
                row.update(existing=False, candidate=True, rating_mw=exp_ * sm, delta_rating_mw=0.0,
                           capex_per_yr=per_mw * exp_)
            rows.append(row)
        cands = [n for n in df.index if float(expand[n]) > 0]
        if cands:
            c.r.add("approximated", list_name, "s_nom_extendable",
                    "PyPSA expands a branch continuously at capital_cost per MW; a CE line candidate is one build "
                    "of the whole increment at capex_per_yr (a model may relax it to a fraction)", cands)
        greenfield = [n for n in df.index if float(existing[n]) == 0 and float(expand[n]) > 0]
        if greenfield:
            c.r.add("approximated", list_name, "s_nom_min",
                    "greenfield branch: PyPSA keeps an unbuilt branch in the meshed network (zero capacity, angles "
                    "still coupled); CE treats it as absent until built", greenfield)
    if dc_rows:
        c.r.add("approximated", "lines", "carrier", "lines between DC buses are imported as hvdc corridors",
                [r[0] for r in dc_rows])
    lines = pd.DataFrame(rows)
    if len(lines):
        lines.insert(0, "id", np.arange(1, len(lines) + 1))
    return lines, dc_rows


def _hvdc(c: _Ctx, bus_ids: dict[str, int], dc_rows: list) -> pd.DataFrame:
    rows = []
    names = c.branch_names
    df = c.t.get("links")
    if len(df):
        df = df[_active(df)]
        ports = [col for col in df.columns if col.startswith("bus") and col[3:].isdigit() and int(col[3:]) >= 2]
        multi = pd.Series(False, index=df.index)
        for col in ports:
            multi |= df[col].fillna("").astype(str).str.len() > 0
        if multi.any():
            c.r.add("dropped", "links", "bus2+", "multi-port links have no CE equivalent", list(df.index[multi]))
        df = df[~multi]
        electric = df["bus0"].astype(str).isin(bus_ids.keys()) & df["bus1"].astype(str).isin(bus_ids.keys())
        if (~electric).any():
            c.r.add("dropped", "links", "bus0/bus1",
                    "link touches a non-electric (or missing) bus, e.g. a sector-coupling conversion",
                    list(df.index[~electric]))
        df = df[electric]
        existing, expand = c.capacity_split(df, "links", "p_nom")
        pmax, pmin = _col(df, "p_max_pu", 1.0), _col(df, "p_min_pu", 0.0)
        eff = _col(df, "efficiency", 1.0)
        capex, fom = c.fixed_costs(df)
        asym = list(df.index[~np.isclose(pmin, -pmax)])
        if asym:
            c.r.add("approximated", "links", "p_min_pu",
                    "CE corridors are symmetric and bidirectional; a link with p_min_pu != -p_max_pu (e.g. one-way) "
                    "is imported with its forward rating in both directions", asym)
        c.drop_if_set("links", df, [(a, 0.0, "CE corridors carry no operating cost")
                                    for a in ("marginal_cost", "marginal_cost_quadratic", "stand_by_cost",
                                              "start_up_cost")])
        c.drop_if_set("links", df, [("ramp_limit_up", float("nan"), "corridor ramp limits are not represented"),
                                    ("ramp_limit_down", float("nan"), "corridor ramp limits are not represented"),
                                    ("delay", 0.0, "link delays are not represented"), *VINTAGE_ATTRS])
        c.drop_series("links", "links", df, ("efficiency", "p_max_pu", "p_min_pu", "marginal_cost"))
        lossy = list(df.index[eff < 1.0])
        if lossy:
            c.r.add("approximated", "links", "efficiency",
                    "PyPSA applies link efficiency to forward flow only; CE applies converter_efficiency to "
                    "transfers in both directions", lossy)
        bad_eff = list(df.index[(eff <= 0) | (eff > 1)])
        if bad_eff:
            c.r.add("approximated", "links", "efficiency", "efficiency outside (0, 1] clipped", bad_eff)
        for name in df.index:
            sc = float(pmax[name])
            rows.append({"name": names.take(name), "from_bus": bus_ids[str(df.at[name, "bus0"])],
                         "to_bus": bus_ids[str(df.at[name, "bus1"])], "existing_mw": float(existing[name]) * sc,
                         "candidate_max_mw": float(expand[name]) * sc,
                         "converter_efficiency": min(max(float(eff[name]), 1e-6), 1.0),
                         "capex_per_mw_yr": float(capex[name] + fom[name]) / sc if sc > 0 else 0.0})
    for name, b0, b1, ex, exp_, per_mw in dc_rows:
        rows.append({"name": names.take(name), "from_bus": bus_ids[b0], "to_bus": bus_ids[b1], "existing_mw": ex,
                     "candidate_max_mw": exp_, "converter_efficiency": 1.0, "capex_per_mw_yr": per_mw})
    out = pd.DataFrame(rows)
    if len(out):
        out.insert(0, "id", np.arange(1, len(out) + 1))
    return out


# --------------------------------------------------------------------------- generation and storage
class _Fleet:
    """Accumulates generators, storage, candidates and availability series."""

    def __init__(self):
        self.gens: list[dict] = []
        self.storage: list[dict] = []
        self.cands: list[dict] = []
        self.avail: dict[str, np.ndarray] = {}

    def add_resource(self, c: _Ctx, *, name: str, bus: int, technology: str, kind: str, existing: float,
                     expand: float, capex: float, fom: float, gen_cols: dict, cand_cols: dict,
                     cf: np.ndarray | None, extendable: bool) -> None:
        """An existing-fleet row (unless the asset is purely a build option) and/or a candidate row."""
        has_existing = existing > 0 or not extendable
        if has_existing:
            row_name = c.names.take(name)
            self.gens.append({"id": len(self.gens) + 1, "name": row_name, "bus": bus, "technology": technology,
                              "kind": kind, "existing_mw": existing, "fom_per_mw_yr": max(capex + fom, 0.0),
                              **gen_cols})
            if cf is not None:
                self.avail[row_name] = cf
        if expand > 0:
            cid = c.names.take(name + EXPANSION_SUFFIX if has_existing else name)
            self.cands.append({"candidate_id": cid, "technology": technology, "kind": kind, "bus": bus,
                               "max_mw": expand, "capex_per_mw_yr": max(capex, 0.0), "fom_per_mw_yr": max(fom, 0.0),
                               **cand_cols})
            if cf is not None:
                self.avail[cid] = cf


def _generators(c: _Ctx, bus_ids: dict[str, int], fleet: _Fleet) -> None:
    df = c.t.get("generators")
    if not len(df):
        return
    df = df[_active(df)]
    on_bus = df["bus"].astype(str).isin(bus_ids.keys())
    if (~on_bus).any():
        c.r.add("dropped", "generators", "bus", "generator sits on a bus that was not imported",
                list(df.index[~on_bus]))
    df = df[on_bus]
    sign = _col(df, "sign", 1.0)
    if (sign < 0).any():
        c.r.add("dropped", "generators", "sign", "negative-sign generators are not imported", list(df.index[sign < 0]))
    df = df[sign >= 0]
    if not len(df):
        return

    carriers = c.t.get("carriers")
    co2_by_carrier = _col(carriers, "co2_emissions", 0.0) if len(carriers) else pd.Series(dtype=float)
    existing, expand = c.capacity_split(df, "generators", "p_nom")
    capex, fom = c.fixed_costs(df)
    pmax = c.static_or_series("generators", "p_max_pu", df, 1.0)
    mc = c.static_or_series("generators", "marginal_cost", df, 0.0)
    eff = c.static_or_series("generators", "efficiency", df, 1.0)
    ext = _col(df, "p_nom_extendable", False)
    pmin = _col(df, "p_min_pu", 0.0)
    committable = _col(df, "committable", False)
    carrier = _col(df, "carrier", "")
    ramp_up, ramp_dn = _col(df, "ramp_limit_up", float("nan")), _col(df, "ramp_limit_down", float("nan"))
    start_up, stand_by = _col(df, "start_up_cost", 0.0), _col(df, "stand_by_cost", 0.0)
    min_up, min_dn = _col(df, "min_up_time", 0.0), _col(df, "min_down_time", 0.0)

    c.drop_series("generators", "generators", df, ("marginal_cost", "efficiency"), kind="approximated",
                  why="time-varying value replaced by its objective-weighted mean")
    c.drop_series("generators", "generators", df, ("p_min_pu", "p_set", "ramp_limit_up", "ramp_limit_down",
                                                   "stand_by_cost"))
    c.drop_if_set("generators", df, (
        ("marginal_cost_quadratic", 0.0, "quadratic costs are not represented"),
        ("shut_down_cost", 0.0, "shut-down costs are not represented"),
        ("e_sum_min", float("-inf"), "energy-sum limits are not represented"),
        ("e_sum_max", float("inf"), "energy-sum limits are not represented"),
        ("ramp_limit_start_up", 1.0, "start-up ramp limits are not represented"),
        ("ramp_limit_shut_down", 1.0, "shut-down ramp limits are not represented"),
        *VINTAGE_ATTRS,
    ))
    tmap = c.o["technology_map"]
    relabelled, folded, derated, must_run, uc, asym = [], [], [], [], [], []
    for name in df.index:
        tech = technology_for(carrier[name], tmap)
        kind = "renewable" if tech in RENEWABLE_TECHNOLOGIES else "thermal"
        if tech != str(carrier[name]):
            relabelled.append(name)
        bus = bus_ids[str(df.at[name, "bus"])]
        varcost = c.weighted_mean(mc[name])
        if kind == "renewable":
            cf = pmax[name]
            if (cf < 0).any() or (cf > 1).any():
                c.r.add("approximated", "generators", "p_max_pu", "renewable availability clipped to [0, 1]", [name])
            if float(pmin[name]) > 0:
                c.r.add("dropped", "generators", "p_min_pu", "a renewable site's minimum output is not represented",
                        [name])
            if bool(committable[name]):
                c.r.add("dropped", "generators", "committable", "renewable sites are not unit-committed", [name])
            fleet.add_resource(c, name=name, bus=bus, technology=tech, kind=kind, existing=float(existing[name]),
                               expand=float(expand[name]), capex=float(capex[name]), fom=float(fom[name]),
                               gen_cols={"varcost_per_mwh": varcost, "co2_t_per_mwh": 0.0},
                               cand_cols={"varcost_per_mwh": varcost, "co2_t_per_mwh": 0.0},
                               cf=np.clip(cf, 0.0, 1.0), extendable=bool(ext[name]))
            continue

        # thermal: a p_max_pu derate is folded into the capacity (CE thermal units have no availability series)
        a = c.weighted_mean(pmax[name]) if np.any(pmax[name] != pmax[name][0]) else float(pmax[name][0])
        if not math.isclose(a, 1.0):
            derated.append(name)
        a = a if a > 0 else 1.0
        e = c.weighted_mean(eff[name])
        if not math.isclose(e, 1.0):
            folded.append(name)
        co2_carrier = float(co2_by_carrier.get(carrier[name], 0.0)) if len(co2_by_carrier) else 0.0
        co2 = co2_carrier / e if e > 0 else 0.0
        ramps = [v for v in (ramp_up[name], ramp_dn[name]) if not math.isnan(v)]
        if len(ramps) == 2 and not math.isclose(ramps[0], ramps[1]):
            asym.append(name)
        is_uc = bool(committable[name])
        pm = min(max(float(pmin[name]), 0.0) / a, 1.0)
        cols = {"pmin_frac": pm}
        if ramps:
            cols["ramp_frac_per_h"] = min(ramps) / c.dt_mean / a
        if is_uc:
            uc.append(name)
            cols.update(min_up_h=int(round(float(min_up[name]) * c.dt_mean)),
                        min_down_h=int(round(float(min_dn[name]) * c.dt_mean)),
                        startup_cost=float(start_up[name]), noload_cost_per_h=float(stand_by[name]))
        elif pm > 0:
            must_run.append(name)
        flags = {"x_committable": is_uc, "x_must_run": bool(pm > 0 and not is_uc)}
        base = {"varcost_per_mwh": varcost, "co2_t_per_mwh": co2}
        fleet.add_resource(c, name=name, bus=bus, technology=tech, kind="thermal",
                           existing=float(existing[name]) * a, expand=float(expand[name]) * a,
                           capex=float(capex[name]) / a, fom=float(fom[name]) / a,
                           gen_cols={**base, **cols, **flags},
                           cand_cols={**base, **{f"x_{k}": v for k, v in cols.items()}, **flags},
                           cf=None, extendable=bool(ext[name]))
    if relabelled:
        c.r.add("approximated", "generators", "carrier", "carrier translated to a CE technology code "
                "(exported back under the code's PyPSA carrier)", relabelled)
    if derated:
        c.r.add("approximated", "generators", "p_max_pu",
                "thermal availability (p_max_pu) folded into capacity; per-MW costs and pmin/ramp fractions "
                "rescaled to match", derated)
    if folded:
        c.r.add("approximated", "generators", "efficiency",
                "efficiency folded into co2_t_per_mwh (marginal_cost already prices fuel); not kept separately",
                folded)
    if asym:
        c.r.add("approximated", "generators", "ramp_limit_up/down",
                "asymmetric ramp limits replaced by the tighter of the two", asym)
    if uc:
        c.r.add("approximated", "generators", "committable",
                "unit-commitment data kept (pmin_frac, min up/down, start-up, no-load); a CE model applies it "
                "when it runs unit commitment (flagged x_committable)", uc)
    if must_run:
        c.r.add("approximated", "generators", "p_min_pu",
                "non-committable generator with p_min_pu > 0: kept as pmin_frac and flagged x_must_run (CE "
                "applies pmin_frac to committed units)", must_run)


def _storage_units(c: _Ctx, bus_ids: dict[str, int], fleet: _Fleet) -> None:
    df = c.t.get("storage_units")
    if not len(df):
        return
    df = df[_active(df)]
    on_bus = df["bus"].astype(str).isin(bus_ids.keys())
    if (~on_bus).any():
        c.r.add("dropped", "storage_units", "bus", "storage sits on a bus that was not imported",
                list(df.index[~on_bus]))
    df = df[on_bus]
    if not len(df):
        return
    existing, expand = c.capacity_split(df, "storage_units", "p_nom")
    capex, fom = c.fixed_costs(df)
    ext = _col(df, "p_nom_extendable", False)
    max_hours = _col(df, "max_hours", 1.0)
    pmin, pmax = _col(df, "p_min_pu", -1.0), _col(df, "p_max_pu", 1.0)
    eff_store, eff_disp = _col(df, "efficiency_store", 1.0), _col(df, "efficiency_dispatch", 1.0)
    standing = _col(df, "standing_loss", 0.0)
    mc = c.static_or_series("storage_units", "marginal_cost", df, 0.0)
    cyclic = _col(df, "cyclic_state_of_charge", False)
    soc0 = _col(df, "state_of_charge_initial", 0.0)
    carrier = _col(df, "carrier", "")
    inflow = c.static_or_series("storage_units", "inflow", df, 0.0)

    c.drop_if_set("storage_units", df, (
        ("marginal_cost_storage", 0.0, "a cost on stored energy is not represented"),
        ("spill_cost", 0.0, "spillage costs are not represented"),
        ("marginal_cost_quadratic", 0.0, "quadratic costs are not represented"),
        *VINTAGE_ATTRS,
    ))
    c.drop_series("storage_units", "storage_units", df, ("p_max_pu", "p_min_pu", "state_of_charge_set",
                                                         "efficiency_store", "efficiency_dispatch", "standing_loss",
                                                         "spill_cost"))
    c.drop_series("storage_units", "storage_units", df, ("marginal_cost",), kind="approximated",
                  why="time-varying value replaced by its objective-weighted mean")
    tmap = c.o["technology_map"]
    reservoirs, idle, partial, noncyclic, cyc, lost_inflow = [], [], [], [], [], []
    for name in df.index:
        bus = bus_ids[str(df.at[name, "bus"])]
        can_charge = float(pmin[name]) < 0 and float(eff_store[name]) > 0
        sc = float(pmax[name]) if float(pmax[name]) > 0 else 1.0
        has_inflow = bool(np.any(inflow[name] > 0))
        varcost = c.weighted_mean(mc[name])
        if not can_charge:
            if not has_inflow:
                idle.append(name)
                continue
            # a reservoir that cannot pump: the specification models it as availability-bounded generation
            reservoirs.append(name)
            nominal = float(existing[name] + expand[name])
            cf = (np.clip(float(eff_disp[name]) * inflow[name] / nominal, 0.0, 1.0) if nominal > 0
                  else np.zeros(c.T))
            fleet.add_resource(c, name=name, bus=bus, technology="hydro_reservoir", kind="renewable",
                               existing=float(existing[name]) * sc, expand=float(expand[name]) * sc,
                               capex=float(capex[name]) / sc, fom=float(fom[name]) / sc,
                               gen_cols={"varcost_per_mwh": varcost, "co2_t_per_mwh": 0.0},
                               cand_cols={"varcost_per_mwh": varcost, "co2_t_per_mwh": 0.0},
                               cf=cf, extendable=bool(ext[name]))
            continue
        if has_inflow:
            lost_inflow.append(name)
        if not math.isclose(-float(pmin[name]), float(pmax[name])):
            partial.append(name)
        (cyc if bool(cyclic[name]) else noncyclic).append(name)
        energy = float(existing[name] + expand[name]) * float(max_hours[name])
        frac = float(soc0[name]) / energy if energy > 0 and not bool(cyclic[name]) else 0.5
        tech = technology_for(carrier[name], tmap) if str(carrier[name]).strip() else "battery"
        sto = {"eta_charge": min(float(eff_store[name]), 1.0), "eta_discharge": min(float(eff_disp[name]), 1.0),
               "self_discharge_frac_per_h": min(float(standing[name]), 0.999999),
               "soc_init_frac": min(max(frac, 0.0), 1.0)}
        has_existing = float(existing[name]) > 0 or not bool(ext[name])
        if has_existing:
            row_name = c.names.take(name)
            fixed = max(float(capex[name] + fom[name]) / sc, 0.0)
            row = {"id": len(fleet.storage) + 1, "name": row_name, "bus": bus, "technology": tech,
                   "existing_mw": float(existing[name]) * sc, "duration_h": float(max_hours[name]) / sc, **sto,
                   "varcost_per_mwh": varcost}
            if fixed > 0:
                row["x_fom_per_mw_yr"] = fixed
            fleet.storage.append(row)
        if float(expand[name]) > 0:
            cid = c.names.take(name + EXPANSION_SUFFIX if has_existing else name)
            fleet.cands.append({"candidate_id": cid, "technology": tech, "kind": "storage", "bus": bus,
                                "max_mw": float(expand[name]) * sc,
                                "capex_per_mw_yr": max(float(capex[name]) / sc, 0.0),
                                "fom_per_mw_yr": max(float(fom[name]) / sc, 0.0),
                                "duration_h": float(max_hours[name]) / sc, "varcost_per_mwh": varcost,
                                **{f"x_{k}": v for k, v in sto.items()}})
    if reservoirs:
        c.r.add("approximated", "storage_units", "inflow",
                "a store that cannot charge from the grid becomes an availability-bounded generator "
                "(technology hydro_reservoir, cf = efficiency_dispatch x inflow / p_nom); the reservoir's "
                "ability to shift energy in time is lost", reservoirs)
    if idle:
        c.r.add("dropped", "storage_units", "p_min_pu", "cannot charge and has no inflow: not imported", idle)
    if lost_inflow:
        c.r.add("dropped", "storage_units", "inflow", "inflow into chargeable storage is not represented",
                lost_inflow)
    if partial:
        c.r.add("approximated", "storage_units", "p_min_pu",
                "charging rating differs from discharging rating; CE uses one power rating (the discharge one)",
                partial)
    if noncyclic:
        c.r.add("approximated", "storage_units", "cyclic_state_of_charge",
                "PyPSA fixes the initial state of charge and leaves the end free; CE starts and ends each block "
                "at soc_init_frac", noncyclic)
    if cyc:
        c.r.add("approximated", "storage_units", "cyclic_state_of_charge",
                "PyPSA's cyclic option leaves the level free; CE starts and ends each block at soc_init_frac "
                "(0.5 is written)", cyc)
    sto_cands = [r["candidate_id"] for r in fleet.cands if r["kind"] == "storage"]
    if sto_cands:
        c.r.add("approximated", "candidates", "x_eta_charge",
                "storage candidates have no efficiency columns in the specification; efficiencies, self-discharge "
                "and initial state of charge are carried as x_ extension columns", sto_cands)


def _demand(c: _Ctx, bus_ids: dict[str, int], buses: pd.DataFrame) -> pd.DataFrame:
    df = c.t.get("loads")
    if not len(df):
        c.r.add("defaulted", "loads", "p_set", "the network has no loads; the demand table is empty")
        return pd.DataFrame(columns=["bus", "scenario", "block", "period", "mw"])
    df = df[_active(df)]
    on_bus = df["bus"].astype(str).isin(bus_ids.keys())
    if (~on_bus).any():
        c.r.add("dropped", "loads", "bus", "load sits on a bus that was not imported", list(df.index[~on_bus]))
    df = df[on_bus]
    p = c.static_or_series("loads", "p_set", df, 0.0)
    sign = _col(df, "sign", -1.0)
    per_bus: dict[str, np.ndarray] = {}
    count: dict[str, int] = {}
    for name in df.index:
        bus = str(df.at[name, "bus"])
        per_bus[bus] = per_bus.get(bus, 0.0) + (-float(sign[name])) * p[name]
        count[bus] = count.get(bus, 0) + 1
    multi = [b for b, k in count.items() if k > 1]
    if multi:
        c.r.add("approximated", "loads", "name", "several loads on one bus are summed into one bus demand", multi)
    if len(df):
        c.r.add("approximated", "loads", "name",
                "CE demand is keyed by bus; load names are not kept (export names each load after its bus)")
    neg = [b for b, v in per_bus.items() if np.any(v < 0)]
    if neg:
        c.r.add("approximated", "loads", "p_set", "negative net demand clipped to 0 (CE demand is non-negative)", neg)
    c.drop_if_set("loads", df, (("q_set", 0.0, "reactive power is not represented"),))
    order = [b for b in buses["name"] if b in per_bus]
    return c.long("bus", "mw", {bus_ids[b]: np.clip(per_bus[b], 0.0, None) for b in order})


def _policies(c: _Ctx) -> list[dict]:
    df = c.t.get("global_constraints")
    out = []
    if not len(df):
        return out
    w_gen = float(c.w_gen.sum())
    for name in df.index:
        def get(k):
            return str(df.at[name, k]) if k in df.columns and pd.notna(df.at[name, k]) else ""

        typ, attr, sense = get("type"), get("carrier_attribute"), get("sense")
        if typ == "primary_energy" and attr == "co2_emissions" and sense in ("<=", "=="):
            value = float(df.at[name, "constant"]) * c.Y / w_gen
            out.append({"name": name, "kind": "emission_cap_t", "value": value, "pollutant": "co2"})
            c.r.add("approximated", "global_constraints", "constant",
                    "CO2 limit imported as an x_policy emission cap in t/yr (generator emissions; PyPSA's "
                    "primary-energy form also counts changes in stored fuel)", [name])
            if sense == "==":
                c.r.add("approximated", "global_constraints", "sense", "'==' imported as a cap ('<=')", [name])
        else:
            c.r.add("dropped", "global_constraints", "type", f"global constraint type {typ!r} is not translated",
                    [name])
    return out


def _unsupported(c: _Ctx) -> None:
    for list_name, why in (("stores", "PyPSA Stores (energy-only, attached through links) are not translated"),
                           ("shunt_impedances", "shunt impedances are not represented in DC power flow")):
        df = c.t.get(list_name)
        if len(df):
            c.r.add("dropped", list_name, "component", why, list(df.index))
    if c.t.unread:
        c.r.add("dropped", "network", "files", "component files this converter does not translate", c.t.unread)


def _guess_year(labels: list[str]) -> int:
    try:
        return int(pd.Timestamp(labels[0]).year)
    except (ValueError, TypeError):
        return 2030


# --------------------------------------------------------------------------- public entry point
def import_network(
    source: Any,
    bundle_dir: str | Path | None = None,
    *,
    year: int | None = None,
    currency: str = "EUR",
    money_year: int | None = None,
    voll: float = 6000.0,
    prm: float = 0.0,
    block_hours: int | None = None,
    inf_capacity_mw: float = 1.0e5,
    technology_map: dict[str, str] | None = None,
    name: str | None = None,
    fmt: str = "parquet",
    overwrite: bool = False,
    warn: bool = False,
) -> Bundle:
    """Convert a PyPSA network into a CE bundle.

    Parameters
    ----------
    source
        A PyPSA CSV folder, a netCDF/HDF5 file (needs ``pypsa``), a
        ``pypsa.Network`` or a :class:`~ce_pypsa.pypsa_csv.PypsaTables`.
    bundle_dir
        When given, the bundle is also written there.
    year
        Investment year. Defaults to the year of the first snapshot, else 2030.
    currency, money_year
        The bundle's ``[money]``: PyPSA does not record a currency, so state
        the one the network's costs are in (default ``EUR``) and its
        constant-money year (default: ``year``).
    voll, prm
        CE scalars PyPSA does not carry: value of lost load (currency/MWh) and
        planning reserve margin. ``prm`` defaults to 0 so the import adds no
        reserve requirement the PyPSA model did not have.
    block_hours
        ``None`` (default) keeps all snapshots in one block. An integer chunks
        them into consecutive blocks of that many periods (e.g. 24 for days).
    inf_capacity_mw
        Build envelope used where PyPSA allows unbounded expansion.
    technology_map
        Extra or overriding PyPSA carrier -> CE technology code entries.
    name
        Bundle name (default: the network's name).
    fmt
        Table format when writing: ``parquet`` (default), ``arrow`` or ``csv``.
    warn
        Also emit a :class:`~ce_pypsa.report.LossyConversionWarning` per note.
    """
    tables, x_eff = load_source(source)
    report = ConversionReport(direction="pypsa -> ce", source=str(source) if isinstance(source, (str, Path))
                              else type(source).__name__, warn=warn)
    tmap = dict(TECHNOLOGY)
    if technology_map:
        tmap.update({k.lower(): v for k, v in technology_map.items()})
    opts = {"block_hours": block_hours, "inf_capacity_mw": float(inf_capacity_mw), "technology_map": tmap}
    c = _Ctx(tables, x_eff, report, opts)

    blocks = _time_axis(c)
    buses, bus_ids, dc_buses = _buses(c)
    lines, dc_rows = _branches(c, bus_ids, dc_buses)
    hvdc = _hvdc(c, bus_ids, dc_rows)
    fleet = _Fleet()
    _generators(c, bus_ids, fleet)
    _storage_units(c, bus_ids, fleet)
    demand = _demand(c, bus_ids, buses)
    policies = _policies(c)
    _unsupported(c)
    report.add("defaulted", "generators", "firm_capacity_frac",
               "PyPSA has no capacity credit; firm_capacity_frac is left empty (it matters only with a reserve "
               "margin)")

    inv_year = int(year if year is not None else _guess_year(c.labels))
    if money_year is None:
        report.add("defaulted", "money", "dollar_year", f"PyPSA records no money year; {inv_year} was written")
    report.add("defaulted", "money", "currency", f"PyPSA records no currency; {currency!r} was written")
    source_text = f"PyPSA network {tables.name!r}" + (
        f" (written by PyPSA {tables.attributes['pypsa_version']})" if "pypsa_version" in tables.attributes else "")
    descriptor = {
        "format": {"name": FORMAT_NAME, "version": FORMAT_VERSION},
        "bundle": {"name": name or tables.name or "pypsa_import",
                   "description": "Converted from a PyPSA network by ce-pypsa.", "source": source_text},
        "money": {"currency": currency, "dollar_year": int(money_year if money_year is not None else inv_year)},
        "time": {"timezone": "UTC", "hour_convention": "hour_ending", "investment_years": [inv_year],
                 "blocks": blocks},
        "scenarios": {"names": ["base"], "probability": [1.0]},
        "system": {"voll_per_mwh": float(voll), "planning_reserve_margin": float(prm), "discount_factors": [1.0]},
        "x_pypsa": {"converter": f"ce-pypsa {converter_version()}", "horizon_h": c.H, "annual_scale": c.s,
                    "susceptance_base_mva": BASE_MVA},
    }
    if policies:
        descriptor["x_policy"] = policies

    out: dict[str, pd.DataFrame] = {"buses": buses, "demand": demand}
    for tname, df in (("lines", lines), ("hvdc", hvdc), ("generators", pd.DataFrame(fleet.gens)),
                      ("storage", pd.DataFrame(fleet.storage)), ("candidates", pd.DataFrame(fleet.cands)),
                      ("availability", c.long("site_name", "cf", fleet.avail))):
        if len(df):
            out[tname] = with_defaults(tname, df)
    out["x_pypsa_snapshots"] = pd.DataFrame({
        "block": c.block_of, "period": c.period_of, "label": c.labels,
        "weight_objective": c.w_obj, "weight_generators": c.w_gen, "weight_stores": c.w_sto})
    report.stats.update({f"{k}_rows": int(len(v)) for k, v in out.items()})
    bundle = Bundle(descriptor=descriptor, tables=out, report=report,
                    provenance={"tool": "ce-pypsa", "tool_version": converter_version(), "source": source_text})
    if bundle_dir is not None:
        bundle.write(bundle_dir, fmt=fmt, overwrite=overwrite)
    return bundle
