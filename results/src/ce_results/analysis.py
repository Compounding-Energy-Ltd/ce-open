# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Derived metrics computed from a run's tables.

All annual figures are *undiscounted* and probability-weighted over scenarios
and weather draws, using the weights from :meth:`ce_results.Run.weights`:
``energy_mwh = sum(mw * annual_h)``.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

from .run import Run
from .schema import TIME_KEYS

__all__ = [
    "mean_lmp_by_bus",
    "capture_prices",
    "congestion_rent",
    "generation_by_technology",
    "curtailment_summary",
]

_K = list(TIME_KEYS)


def mean_lmp_by_bus(run: Run) -> pd.DataFrame:
    """Time-weighted annual average price per bus.

    Columns: ``year, bus, mean_lmp, min_lmp, max_lmp, hours``.
    """
    p = run.lmp(with_weights=True)
    p["pw"] = p["lmp_per_mwh"] * p["annual_h"]
    g = p.groupby(["year", "bus"])
    out = pd.DataFrame({
        "pw": g["pw"].sum(),
        "hours": g["annual_h"].sum(),
        "min_lmp": g["lmp_per_mwh"].min(),
        "max_lmp": g["lmp_per_mwh"].max(),
    }).reset_index()
    out["mean_lmp"] = out["pw"] / out["hours"].where(out["hours"] > 0)
    return out[["year", "bus", "mean_lmp", "min_lmp", "max_lmp", "hours"]]


def capture_prices(run: Run, source_types: Sequence[str] | None = None) -> pd.DataFrame:
    """Energy-weighted price each asset sees, per year.

    For every (year, source_type, unit)::

        capture_price = sum(mw * lmp_at_bus * annual_h) / sum(mw * annual_h)

    ``capture_ratio`` divides by the time-weighted average price at the same
    bus. For ``storage_charge`` rows the "capture" price is the average price
    paid to charge; ``revenue`` is then a cost.

    Columns: ``year, source_type, component_name, technology, bus,
    energy_mwh, revenue, capture_price, bus_mean_lmp, capture_ratio``.
    """
    g = run.dispatch(source_types)
    p = run.lmp()[_K + ["bus", "lmp_per_mwh"]]
    m = g.merge(p, on=_K + ["bus"], how="left")
    if m["lmp_per_mwh"].isna().any():
        missing = m.loc[m["lmp_per_mwh"].isna(), "bus"].unique()[:5]
        raise ValueError(f"dispatch at bus(es) with no LMP: {', '.join(map(str, missing))}")
    m = m.merge(run.weights(m[_K]), on=_K, how="left")
    m["energy_mwh"] = m["dispatch_mw"] * m["annual_h"]
    m["revenue"] = m["energy_mwh"] * m["lmp_per_mwh"]
    ident = ["year", "source_type", "component_name", "technology", "bus"]
    out = m.groupby(ident, as_index=False)[["energy_mwh", "revenue"]].sum()
    out["capture_price"] = out["revenue"] / out["energy_mwh"].where(out["energy_mwh"] > 0)
    bus = mean_lmp_by_bus(run)[["year", "bus", "mean_lmp"]].rename(columns={"mean_lmp": "bus_mean_lmp"})
    out = out.merge(bus, on=["year", "bus"], how="left")
    with np.errstate(divide="ignore", invalid="ignore"):
        out["capture_ratio"] = out["capture_price"] / out["bus_mean_lmp"].where(out["bus_mean_lmp"] != 0)
    return out.sort_values(ident).reset_index(drop=True)


def congestion_rent(run: Run, *, at_limit_tol: float = 1e-3) -> pd.DataFrame:
    """Annual merchandising surplus and congestion rent per line.

    With positive flow from ``from_bus`` to ``to_bus``::

        merchandising_surplus = sum(flow * (lmp_to - lmp_from) * annual_h)
        congestion_rent       = sum(flow * (cong_to - cong_from) * annual_h)
        loss_surplus          = sum(flow * (loss_to - loss_from) * annual_h)

    The energy component is the same at every bus, so the surplus splits
    exactly into its congestion and loss parts. When the run reports no price
    components, ``congestion_rent`` equals the surplus and ``loss_surplus`` is
    NaN.

    Also reports ``energy_mwh`` (sum of |flow| * annual_h), ``mean_loading``
    (hour-weighted |flow| / rating) and ``hours_at_limit`` (annual hours with
    |flow| >= rating * (1 - at_limit_tol)).
    """
    f = run.flows(with_rating=True)
    if "from_bus" not in f.columns:
        raise KeyError("congestion rent needs the network table (line endpoints)")
    p = run.lmp()
    comps = {"lmp_congestion", "lmp_loss"} <= set(p.columns)
    pcols = ["lmp_per_mwh"] + (["lmp_congestion", "lmp_loss"] if comps else [])
    pf = p[_K + ["bus"] + pcols].rename(columns={"bus": "from_bus", **{c: c + "_from" for c in pcols}})
    pt = p[_K + ["bus"] + pcols].rename(columns={"bus": "to_bus", **{c: c + "_to" for c in pcols}})
    m = f.merge(pf, on=_K + ["from_bus"], how="left").merge(pt, on=_K + ["to_bus"], how="left")
    m = m.merge(run.weights(m[_K]), on=_K, how="left")
    fh = m["flow_mw"] * m["annual_h"]
    m["merchandising_surplus"] = fh * (m["lmp_per_mwh_to"] - m["lmp_per_mwh_from"])
    if comps:
        m["congestion_rent"] = fh * (m["lmp_congestion_to"] - m["lmp_congestion_from"])
        m["loss_surplus"] = fh * (m["lmp_loss_to"] - m["lmp_loss_from"])
    else:
        m["congestion_rent"] = m["merchandising_surplus"]
        m["loss_surplus"] = np.nan
    m["energy_mwh"] = m["flow_mw"].abs() * m["annual_h"]
    m["loading_h"] = m["loading"].fillna(0.0) * m["annual_h"]
    at = m["flow_mw"].abs() >= m["rating_mw"] * (1.0 - at_limit_tol)
    m["at_limit_h"] = np.where(at & (m["rating_mw"] > 0), m["annual_h"], 0.0)
    g = m.groupby(["year", "line", "from_bus", "to_bus"], as_index=False)
    out = g[["merchandising_surplus", "congestion_rent", "loss_surplus", "energy_mwh",
             "loading_h", "at_limit_h", "annual_h"]].sum(min_count=1)
    out["mean_loading"] = out["loading_h"] / out["annual_h"].where(out["annual_h"] > 0)
    out = out.rename(columns={"at_limit_h": "hours_at_limit"})
    cols = ["year", "line", "from_bus", "to_bus", "merchandising_surplus", "congestion_rent",
            "loss_surplus", "energy_mwh", "mean_loading", "hours_at_limit"]
    return out[cols].sort_values(["year", "line"]).reset_index(drop=True)


def generation_by_technology(run: Run) -> pd.DataFrame:
    """Annual energy (MWh) by year, source type and technology."""
    g = run.dispatch(with_weights=True)
    g["energy_mwh"] = g["dispatch_mw"] * g["annual_h"]
    return (g.groupby(["year", "source_type", "technology"], as_index=False)["energy_mwh"].sum()
            .sort_values(["year", "source_type", "technology"]).reset_index(drop=True))


def curtailment_summary(run: Run, existing_mw=None) -> pd.DataFrame:
    """Annual available, dispatched and curtailed energy per renewable site.

    Columns: ``year, site_name, technology, bus, available_mwh, dispatch_mwh,
    curtailed_mwh, curtailment_rate, capacity_short_h``. See
    :meth:`ce_results.Run.curtailment` for how curtailment is derived.
    """
    c = run.curtailment(existing_mw)
    c = c.merge(run.weights(c[_K]), on=_K, how="left")
    for col in ("available", "dispatch", "curtailed"):
        c[f"{col}_mwh"] = c[f"{col}_mw"] * c["annual_h"]
    if "capacity_short" in c.columns:
        c["capacity_short_h"] = np.where(c["capacity_short"], c["annual_h"], 0.0)
    else:
        c["capacity_short_h"] = 0.0
    ident = ["year", "site_name"] + [x for x in ("technology", "bus") if x in c.columns]
    out = c.groupby(ident, as_index=False, dropna=False)[
        ["available_mwh", "dispatch_mwh", "curtailed_mwh", "capacity_short_h"]].sum()
    out["curtailment_rate"] = out["curtailed_mwh"] / out["available_mwh"].where(out["available_mwh"] > 0)
    return out.sort_values(["year", "site_name"]).reset_index(drop=True)
