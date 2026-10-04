# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Compare two runs: headline deltas plus the reasons a delta may not be meaningful.

Deltas are always ``b - a``. :func:`compare_runs` never refuses to compare; it
records anything that makes the comparison questionable (different years,
cost basis, register version, an unconverged run, a cost delta inside the
optimality gap) in :attr:`RunComparison.warnings`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .analysis import generation_by_technology, mean_lmp_by_bus
from .run import Run

__all__ = ["RunComparison", "compare_runs"]


@dataclass
class RunComparison:
    """The result of :func:`compare_runs`. Every frame has ``*_a``, ``*_b`` and ``delta`` columns."""

    a: str
    b: str
    stamps: pd.DataFrame
    costs: pd.DataFrame
    builds: pd.DataFrame | None = None
    prices: pd.DataFrame | None = None
    generation: pd.DataFrame | None = None
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def comparable(self) -> bool:
        """False when anything in :attr:`warnings` was recorded."""
        return not self.warnings

    def headline(self) -> pd.Series:
        """Total cost delta, its relative size, and the larger of the two gaps."""
        c = self.costs[self.costs["section"] == "cost"].set_index("item")
        s = self.stamps.set_index("metric")
        total = c.loc["total"] if "total" in c.index else None
        gaps = [s.loc["gap", col] for col in ("a", "b")] if "gap" in s.index else []
        gaps = [g for g in gaps if g is not None and not pd.isna(g)]
        return pd.Series({
            "total_cost_delta": None if total is None else total["delta"],
            "total_cost_rel_delta": None if total is None else total["rel_delta"],
            "max_gap": max(gaps) if gaps else None,
        }, dtype=object)


def _pair(a: pd.DataFrame, b: pd.DataFrame, keys: list[str], value: str,
          fill: float | None = 0.0) -> pd.DataFrame:
    """Outer-join one measure of two runs; rows absent from a run read as ``fill``
    (``None`` leaves them NaN, so their delta is NaN)."""
    m = a[keys + [value]].merge(b[keys + [value]], on=keys, how="outer", suffixes=("_a", "_b"))
    if fill is not None:
        m[[f"{value}_a", f"{value}_b"]] = m[[f"{value}_a", f"{value}_b"]].fillna(fill)
    m["delta"] = m[f"{value}_b"] - m[f"{value}_a"]
    return m.sort_values(keys).reset_index(drop=True)


def _rel(a: Any, b: Any) -> float:
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return np.nan
    return (b - a) / abs(a) if a != 0 else np.nan


def _stamps_frame(a: Run, b: Run) -> pd.DataFrame:
    sa, sb = a.stamps.as_series(), b.stamps.as_series()
    rows = []
    for k in sa.index:
        va, vb = sa[k], sb[k]
        num = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (va, vb))
        rows.append((k, va, vb, (vb - va) if num else None, _rel(va, vb) if num else None))
    return pd.DataFrame(rows, columns=["metric", "a", "b", "delta", "rel_delta"])


def _costs_frame(a: Run, b: Run) -> pd.DataFrame:
    la, lb = a.cost_ledger(), b.cost_ledger()
    m = la.merge(lb, on=["section", "item"], how="outer", suffixes=("_a", "_b"))
    m = m.rename(columns={"value_a": "a", "value_b": "b"})
    m["delta"] = m["b"] - m["a"]
    m["rel_delta"] = [_rel(x, y) for x, y in zip(m["a"], m["b"])]
    order = {"cost": 0, "cost_decomposition": 1, "qa_cost_ledger": 2}
    m["_o"] = m["section"].map(order).fillna(9)
    return m.sort_values(["_o", "item"]).drop(columns="_o").reset_index(drop=True)


def _check(a: Run, b: Run, gap_tol: float, out: list[str]) -> None:
    pa, pb = a.provenance, b.provenance
    if pa.investment_years and pb.investment_years and pa.investment_years != pb.investment_years:
        out.append(f"investment years differ: {list(pa.investment_years)} vs {list(pb.investment_years)}")
    if a.time_mode != b.time_mode:
        out.append(f"time modes differ: {a.time_mode} vs {b.time_mode}")
    if pa.currency and pb.currency and pa.currency != pb.currency:
        out.append(f"currencies differ: {pa.currency} vs {pb.currency}")
    if pa.dollar_year and pb.dollar_year and pa.dollar_year != pb.dollar_year:
        out.append(f"money years differ: {pa.dollar_year} vs {pb.dollar_year}")
    if pa.register_version != pb.register_version:
        out.append(f"assumptions register versions differ: {pa.register_version} vs {pb.register_version}")
    cba, cbb = a.summary.get("cost_basis"), b.summary.get("cost_basis")
    if cba is not None and cbb is not None and cba != cbb:
        diff = sorted(k for k in set(cba) | set(cbb) if cba.get(k) != cbb.get(k))
        out.append("cost basis differs in: " + ", ".join(diff))
    for tag, r in (("a", a), ("b", b)):
        s = r.stamps
        if s.converged is False or (s.gap is not None and s.gap > gap_tol):
            out.append(f"run {tag} ({r.name}) is not certified: converged={s.converged}, "
                       f"gap={s.gap}, stop_cause={s.stop_cause}")
    ga, gb = a.stamps, b.stamps
    if None not in (ga.objective, gb.objective, ga.gap, gb.gap):
        band = max(abs(ga.objective) * ga.gap, abs(gb.objective) * gb.gap)
        if abs(gb.objective - ga.objective) < band:
            out.append(f"total cost delta {gb.objective - ga.objective:.6g} is inside the optimality "
                       f"gap band {band:.6g}: the ranking of the two runs is not established")


def compare_runs(a: Run, b: Run, *, builds_by: Sequence[str] = ("component", "technology"),
                 final_year_only: bool = True, gap_tol: float = 1e-3) -> RunComparison:
    """Compare run ``b`` against run ``a``.

    ``builds_by`` sets the grain of the build comparison (add ``"bus"`` for a
    nodal view). With ``final_year_only`` (the default) builds are compared in
    each run's final year -- the full new-build fleet, since builds are
    cumulative; otherwise every year is compared.
    """
    warnings: list[str] = []
    skipped: list[str] = []
    _check(a, b, gap_tol, warnings)
    res = RunComparison(a=a.name, b=b.name, stamps=_stamps_frame(a, b), costs=_costs_frame(a, b),
                        warnings=warnings, skipped=skipped)

    if a.has("investment") and b.has("investment"):
        keys = list(builds_by)
        ba, bb = a.builds(), b.builds()
        if final_year_only:
            ba = ba[ba["year"] == ba["year"].max()] if len(ba) else ba
            bb = bb[bb["year"] == bb["year"].max()] if len(bb) else bb
        else:
            keys = ["year"] + [k for k in keys if k != "year"]
        ga = ba.groupby(keys, as_index=False)["built_mw"].sum()
        gb = bb.groupby(keys, as_index=False)["built_mw"].sum()
        res.builds = _pair(ga, gb, keys, "built_mw")
    else:
        skipped.append("builds: investment table missing in one run")

    if a.has("lmp") and b.has("lmp"):
        pa = mean_lmp_by_bus(a)
        pb = mean_lmp_by_bus(b)
        if set(pa["bus"]) != set(pb["bus"]):
            warnings.append("bus sets differ: buses present in one run only have a NaN price delta")
        res.prices = _pair(pa, pb, ["year", "bus"], "mean_lmp", fill=None)
    else:
        skipped.append("prices: lmp table missing in one run")

    if a.has("generation") and b.has("generation"):
        res.generation = _pair(generation_by_technology(a), generation_by_technology(b),
                               ["year", "source_type", "technology"], "energy_mwh")
    else:
        skipped.append("generation: generation table missing in one run")
    return res
