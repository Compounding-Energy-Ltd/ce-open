# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The time axis of a run: blocks, period lengths, scenario and weather weights.

A CE run cuts every year into *blocks*. A block is either chronological (real,
contiguous time from a start instant) or representative (a sample standing for
``weight_h`` hours of the year). Each block has ``nperiods`` periods of length
``dt_h`` (one value, or one per period). Periods are 1-based and hour-ending:
period ``p`` covers ``[start + offset_p, start + offset_p + dt_p)``.

The axis is read, in order of preference, from

1. ``manifest.toml`` ``[time]`` / ``[scenarios]`` (CE data format 1.0);
2. the input bundle's ``bundle.toml`` (when the reader was given the bundle);
3. the ``blocks`` / ``block_periods`` / ``scenarios`` / ``years`` /
   ``timeindex`` tables older engine releases wrote into the run directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np
import pandas as pd

__all__ = ["TimeAxis"]


@dataclass
class TimeAxis:
    """Blocks, periods and the weights that turn modelled hours into annual hours.

    ``blocks`` columns: ``block, nperiods, weight_h, span_h, kind, start``.
    ``periods`` columns: ``block, period, dt_h, offset_h`` (and, for legacy
    runs, a per-year ``period_start`` map in :attr:`legacy_starts`).
    """

    blocks: pd.DataFrame
    periods: pd.DataFrame
    source: str
    scenarios: pd.DataFrame | None = None  # scenario, probability (+ name)
    investment_years: list[int] = field(default_factory=list)
    discount: dict[int, float] = field(default_factory=dict)
    weather_years: list[int] = field(default_factory=list)
    weather_axis: str = "label"
    legacy_starts: pd.DataFrame | None = None  # year, block, period, period_start

    # ------------------------------------------------------------------ construction

    @staticmethod
    def _from_time_section(time: Mapping[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
        brows, prows = [], []
        for b in time.get("blocks", []) or []:
            bid, n = int(b["id"]), int(b["nperiods"])
            dt = b["dt_h"]
            dts = [float(x) for x in dt] if isinstance(dt, (list, tuple)) else [float(dt)] * n
            if len(dts) != n:
                raise ValueError(f"block {bid}: dt_h has {len(dts)} values for {n} periods")
            start = b.get("start")
            brows.append((bid, n, float(b["weight_h"]), float(sum(dts)), str(b.get("kind", "")) or None,
                          pd.Timestamp(start) if start is not None else pd.NaT, b.get("name")))
            off = np.concatenate([[0.0], np.cumsum(dts)[:-1]])
            prows += [(bid, p, dts[p - 1], float(off[p - 1])) for p in range(1, n + 1)]
        blocks = pd.DataFrame(brows, columns=["block", "nperiods", "weight_h", "span_h", "kind", "start", "name"])
        periods = pd.DataFrame(prows, columns=["block", "period", "dt_h", "offset_h"])
        return blocks, periods

    @staticmethod
    def scenarios_from_section(section: Mapping[str, Any] | None) -> pd.DataFrame | None:
        """``scenario, probability, name`` from a ``[scenarios]`` table, or ``None``."""
        if not section or "probability" not in section:
            return None
        probs = [float(p) for p in section["probability"]]
        names = list(section.get("names") or [f"s{i}" for i in range(1, len(probs) + 1)])
        return pd.DataFrame({"scenario": range(1, len(probs) + 1), "probability": probs, "name": names[:len(probs)]})

    @classmethod
    def from_descriptor(cls, doc: Mapping[str, Any], source: str) -> "TimeAxis | None":
        """From a 1.0 descriptor (``manifest.toml`` or ``bundle.toml``) carrying ``[[time.blocks]]``."""
        time = doc.get("time") or {}
        if not time.get("blocks"):
            return None
        blocks, periods = cls._from_time_section(time)
        years = [int(y) for y in time.get("investment_years", []) or []]
        system = doc.get("system") or {}
        df = system.get("discount_factors") or []
        discount = {y: float(f) for y, f in zip(years, df)} if len(df) == len(years) else {}
        return cls(blocks=blocks, periods=periods, source=source,
                   scenarios=cls.scenarios_from_section(doc.get("scenarios")),
                   investment_years=years, discount=discount,
                   weather_years=[int(y) for y in time.get("weather_years", []) or []],
                   weather_axis=str(time.get("weather_axis", "label")))

    @classmethod
    def from_legacy_tables(cls, tables: Mapping[str, pd.DataFrame]) -> "TimeAxis | None":
        """From the calendar tables older engine releases wrote into the run directory."""
        b = tables.get("blocks")
        if b is None:
            return None
        bp = tables.get("block_periods")
        if bp is not None:
            periods = bp[["block", "period", "dt_h"]].sort_values(["block", "period"]).reset_index(drop=True)
        elif {"nperiods", "sum_dt_h"} <= set(b.columns):
            periods = pd.DataFrame([(int(r.block), p, float(r.sum_dt_h) / int(r.nperiods))
                                    for r in b.itertuples() for p in range(1, int(r.nperiods) + 1)],
                                   columns=["block", "period", "dt_h"])
        else:
            periods = None
        if periods is not None:
            periods["offset_h"] = periods.groupby("block")["dt_h"].cumsum() - periods["dt_h"]
            span = periods.groupby("block")["dt_h"].sum()
        else:
            periods = pd.DataFrame(columns=["block", "period", "dt_h", "offset_h"])
            span = pd.Series(dtype=float)
        blocks = pd.DataFrame({"block": b["block"].astype(int), "weight_h": b["weight_h"].astype(float)})
        blocks["span_h"] = blocks["block"].map(span) if len(span) else (
            b["sum_dt_h"] if "sum_dt_h" in b.columns else np.nan)
        blocks["nperiods"] = blocks["block"].map(periods.groupby("block").size()) if len(periods) else (
            b["nperiods"] if "nperiods" in b.columns else np.nan)
        ti = tables.get("timeindex")
        modes = None
        if ti is not None and "time_mode" in ti.columns:
            modes = ti.dropna(subset=["time_mode"]).groupby("block")["time_mode"].first()
        chrono = np.isclose(blocks["weight_h"], blocks["span_h"], rtol=1e-6)
        blocks["kind"] = [str(modes[blk]) if modes is not None and blk in modes.index
                          else ("chronological" if c else "representative")
                          for blk, c in zip(blocks["block"], chrono)]
        blocks["start"] = pd.NaT
        blocks["name"] = None
        starts = None
        if ti is not None and "timestamp" in ti.columns:
            starts = ti[["year", "block", "period", "timestamp"]].rename(columns={"timestamp": "period_start"})
        scen = tables.get("scenarios")
        yrs = tables.get("years")
        years, discount = [], {}
        if yrs is not None:
            years = sorted(int(y) for y in yrs["year"])
            if "discount" in yrs.columns:
                discount = {int(y): float(d) for y, d in zip(yrs["year"], yrs["discount"])}
        return cls(blocks=blocks, periods=periods, source="legacy tables",
                   scenarios=None if scen is None else scen[["scenario", "probability"]].copy(),
                   investment_years=years, discount=discount, legacy_starts=starts)

    # ------------------------------------------------------------------ queries

    @property
    def mode(self) -> str:
        """``"chronological"`` if every block is chronological, else ``"representative"``."""
        kinds = set(self.blocks["kind"].dropna())
        return "chronological" if kinds == {"chronological"} else "representative"

    def block_scale(self) -> pd.Series:
        """Annual hours represented per modelled hour, by block (``weight_h / span_h``)."""
        return (self.blocks["weight_h"] / self.blocks["span_h"]).set_axis(self.blocks["block"])

    def period_times(self, keys: pd.DataFrame) -> pd.DataFrame:
        """``period_start`` and ``period_end`` for each (year, block, period) row of ``keys``.

        Representative blocks have no wall-clock time: their rows get ``NaT``.
        """
        k = keys[["year", "block", "period"]]
        per = self.periods[["block", "period", "dt_h", "offset_h"]]
        m = k.merge(per, on=["block", "period"], how="left")
        if self.legacy_starts is not None:
            m = m.merge(self.legacy_starts, on=["year", "block", "period"], how="left")
        else:
            st = self.blocks.set_index("block")["start"]
            start = m["block"].map(st)
            is_chrono = m["block"].map(self.blocks.set_index("block")["kind"]) == "chronological"
            m["period_start"] = start + pd.to_timedelta(m["offset_h"], unit="h")
            m.loc[~is_chrono.fillna(False), "period_start"] = pd.NaT
        m["period_end"] = m["period_start"] + pd.to_timedelta(m["dt_h"], unit="h")
        return m[["period_start", "period_end"]]

    def discount_frame(self) -> pd.DataFrame:
        return pd.DataFrame({"year": list(self.discount), "discount": list(self.discount.values())})

