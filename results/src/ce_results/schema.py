# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Column contracts for the tables a run directory (and its input bundle) holds.

Column names follow the CE data format 1.0 (see ``spec/`` at the repository
root). Older run directories spell some columns differently -- money columns
carried a currency suffix, display labels were called ``*_name`` -- and the
reader renames those *legacy* columns to their canonical names on load, so
code written against this package sees one vocabulary.

Each table has a :class:`TableSpec` listing the columns a reader can rely on
(``required``), the columns it understands when present (``optional``) and
the legacy spellings it accepts (``aliases``). Columns not listed pass through
untouched, so a run that adds a column still loads.

Column kinds are deliberately coarse:

``int``       64-bit integer (nullable ``Int64`` if the column has gaps)
``float``     64-bit float
``str``       text
``bool``      boolean
``datetime``  timestamp
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd

__all__ = ["TableSpec", "SchemaError", "TABLE_SPECS", "BUNDLE_SPECS", "TIME_KEYS", "coerce"]

# Keys shared by every per-timestep result table.
TIME_KEYS = ("year", "scenario", "weather", "block", "period")


class SchemaError(ValueError):
    """A table is missing a required column or a column cannot take its kind."""


@dataclass(frozen=True)
class TableSpec:
    """The column contract of one table."""

    name: str
    description: str
    required: Mapping[str, str]
    optional: Mapping[str, str] = field(default_factory=dict)
    keys: tuple[str, ...] = ()
    aliases: Mapping[str, str] = field(default_factory=dict)  # legacy name -> canonical name

    def kinds(self) -> dict[str, str]:
        """All understood columns and their kinds."""
        out = dict(self.required)
        out.update(self.optional)
        return out


def _spec(name, description, required, optional=None, keys=(), aliases=None):
    return TableSpec(name, description, dict(required), dict(optional or {}), tuple(keys), dict(aliases or {}))


_T = {k: "int" for k in TIME_KEYS}
_BUS_LABEL = {"bus_name": "bus_label"}

TABLE_SPECS: dict[str, TableSpec] = {
    s.name: s
    for s in (
        # ---- CE data format 1.0 result tables
        _spec("investment",
              "Capacity built (cumulative to each year) or retired, by component",
              {"year": "int", "component": "str", "name": "str", "technology": "str", "bus": "str"},
              {"id": "int", "built_mw": "float", "built_mwh": "float", "bus_label": "str"},
              keys=("year", "component", "name"), aliases=_BUS_LABEL),
        _spec("lmp",
              "Nodal price per timestep with optional energy / congestion / loss attribution",
              {**_T, "bus": "str", "lmp_per_mwh": "float"},
              {"bus_label": "str", "lmp_energy": "float", "lmp_congestion": "float", "lmp_loss": "float",
               "floor_slack_at_bus": "bool"},
              keys=TIME_KEYS + ("bus",),
              aliases={"lmp_gbp_per_mwh": "lmp_per_mwh", **_BUS_LABEL}),
        _spec("flows",
              "Line flow per timestep (positive = from_bus to to_bus)",
              {**_T, "line": "str", "flow_mw": "float"},
              {"line_label": "str"},
              keys=TIME_KEYS + ("line",), aliases={"line_name": "line_label"}),
        _spec("generation",
              "Dispatch per unit per timestep (thermal, renewable, storage charge and discharge)",
              {**_T, "source_type": "str", "component_name": "str", "technology": "str", "bus": "str",
               "dispatch_mw": "float"},
              {"component_id": "int", "bus_label": "str", "floor_slack_mw": "float",
               "varcost_per_mwh": "float"},
              keys=TIME_KEYS + ("source_type", "component_name"),
              aliases={"varcost_gbp_per_mwh": "varcost_per_mwh", **_BUS_LABEL}),
        _spec("storage_state",
              "Storage state of charge (end of period), charge and discharge per timestep",
              {**_T, "storage": "str", "technology": "str", "bus": "str", "soc_mwh": "float",
               "charge_mw": "float", "discharge_mw": "float"},
              keys=TIME_KEYS + ("storage",)),
        _spec("curtailment",
              "Availability-bounded output left unused per site per timestep",
              {**_T, "site_name": "str", "technology": "str", "bus": "str", "available_mw": "float",
               "dispatch_mw": "float", "curtailed_mw": "float"},
              keys=TIME_KEYS + ("site_name",)),
        _spec("transmission_losses",
              "Line losses per timestep",
              {**_T, "line": "str", "loss_mw": "float"},
              keys=TIME_KEYS + ("line",)),
        _spec("qa_checks",
              "Per-run quality checks: PASS / WARN / FAIL / NA per leg",
              {"check": "str", "family": "str", "status": "str"},
              {"value": "float", "tolerance": "float", "unit": "str", "worst_where": "str", "count": "int",
               "population": "int", "year": "int", "scope": "str", "source": "str", "note": "str",
               "required": "bool"},
              keys=("check", "year", "scope")),
        _spec("weather_weights",
              "Relative weight of each weather year within an investment year (normalised per year)",
              {"year": "int", "weather": "int", "weight": "float"},
              keys=("year", "weather")),
        # ---- tables written by older engine releases (read for compatibility)
        _spec("convergence",
              "Decomposition bounds and gap per outer iteration",
              {"iteration": "int", "lb": "float", "ub": "float", "gap": "float"},
              {"phase": "str", "ops_cuts": "int", "ctg_cuts": "int", "elcc_cuts": "int",
               "active_contingencies": "int", "wall_s": "float"},
              keys=("iteration",)),
        _spec("network",
              "Line endpoints (bus names) and thermal rating",
              {"line": "str", "from_bus": "str", "to_bus": "str", "rating_mw": "float"},
              {"line_label": "str", "from_bus_label": "str", "to_bus_label": "str"},
              keys=("line",),
              aliases={"line_name": "line_label", "from_bus_name": "from_bus_label",
                       "to_bus_name": "to_bus_label"}),
        _spec("blocks",
              "Per-block annual weight (hours represented) and modelled span",
              {"block": "int", "weight_h": "float"},
              {"nperiods": "int", "sum_dt_h": "float"},
              keys=("block",)),
        _spec("block_periods",
              "Per-period duration within each block",
              {"block": "int", "period": "int", "dt_h": "float"},
              {"cum_h_in_block": "float"},
              keys=("block", "period")),
        _spec("scenarios", "Scenario probabilities",
              {"scenario": "int", "probability": "float"}, keys=("scenario",)),
        _spec("years", "Investment years and discount factors",
              {"year": "int"}, {"year_index": "int", "discount": "float"}, keys=("year",)),
        _spec("timeindex",
              "Map from (year, block, period) to the period's start time and calendar parts",
              {"year": "int", "block": "int", "period": "int"},
              {"hour_in_block_h": "float", "time_mode": "str", "calendar_complete": "bool",
               "timestamp": "datetime", "date": "datetime", "month": "int", "iso_week": "int",
               "iso_week_year": "int", "day_of_year": "int", "day_of_week": "int", "hour": "int",
               "season": "str", "season_year": "int"},
              keys=("year", "block", "period")),
        _spec("demand_profile", "Input demand per bus per timestep (echo of the input)",
              {"scenario": "int", "block": "int", "bus": "str", "period": "int", "demand_mw": "float"},
              keys=("scenario", "block", "bus", "period")),
        _spec("availability_profile",
              "Input renewable availability per site per timestep (echo of the input)",
              {"scenario": "int", "block": "int", "site_name": "str", "period": "int", "cf": "float"},
              keys=("scenario", "block", "site_name", "period"),
              aliases={"renewable": "site_name", "availability_cf": "cf"}),
        _spec("congestion_rent_by_line",
              "Annual merchandising surplus and congestion rent per line, as the engine computed them",
              {"line": "str", "merchandising_surplus_gbp_annual": "float", "congestion_rent_gbp_annual": "float"},
              {"from_bus": "str", "to_bus": "str"},
              keys=("line",)),
    )
}

# Input-bundle tables the reader consults for derived metrics (only the columns it uses).
BUNDLE_SPECS: dict[str, TableSpec] = {
    s.name: s
    for s in (
        _spec("buses", "Network nodes", {"id": "int", "name": "str"}, {"label": "str", "zone": "str"},
              keys=("id",)),
        _spec("lines", "AC lines", {"id": "int", "name": "str", "from_bus": "int", "to_bus": "int",
                                    "rating_mw": "float"},
              {"existing": "bool", "candidate": "bool", "delta_rating_mw": "float", "loss_frac": "float",
               "label": "str"},
              keys=("id",)),
        _spec("generators", "Existing generating units",
              {"id": "int", "name": "str", "bus": "int", "technology": "str", "kind": "str",
               "existing_mw": "float"},
              keys=("id",)),
        _spec("demand", "Electricity demand per bus and period",
              {"bus": "int", "scenario": "int", "block": "int", "period": "int", "mw": "float"},
              {"year": "int", "weather": "int"},
              keys=("bus", "scenario", "block", "period", "year", "weather")),
        _spec("availability", "Availability (capacity factor) series of availability-bounded sites",
              {"site_name": "str", "scenario": "int", "block": "int", "period": "int", "cf": "float"},
              {"year": "int", "weather": "int"},
              keys=("site_name", "scenario", "block", "period", "year", "weather")),
        TABLE_SPECS["weather_weights"],
    )
}

_BOOL_TEXT = {"true": True, "false": False, "1": True, "0": False, "t": True, "f": False}


def _to_bool(s: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(s) and not s.isna().any():
        return s.astype(bool)
    def conv(v):
        if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA:
            return pd.NA
        if isinstance(v, (bool, np.bool_)):
            return bool(v)
        if isinstance(v, (int, float, np.integer, np.floating)):
            return bool(v)
        r = _BOOL_TEXT.get(str(v).strip().lower())
        if r is None:
            raise ValueError(f"not a boolean: {v!r}")
        return r
    mapped = s.map(conv)
    return mapped.astype("boolean") if mapped.isna().any() else mapped.astype(bool)


def _coerce_column(s: pd.Series, kind: str) -> pd.Series:
    if kind == "float":
        return pd.to_numeric(s, errors="raise").astype("float64")
    if kind == "int":
        v = pd.to_numeric(s, errors="raise")
        if v.isna().any():
            return v.astype("Int64")
        if not (v == v.round()).all():
            raise ValueError("non-integer value in an integer column")
        return v.astype("int64")
    if kind == "str":
        return s.astype("string")
    if kind == "bool":
        return _to_bool(s)
    if kind == "datetime":
        return pd.to_datetime(s, errors="raise")
    raise ValueError(f"unknown column kind {kind!r}")


def _normalise_investment(df: pd.DataFrame) -> pd.DataFrame:
    """Legacy runs carried storage energy as component ``storage_energy_mwh`` with MWh in ``built_mw``."""
    legacy = df["component"].astype("string") == "storage_energy_mwh"
    if not legacy.any():
        return df
    df = df.copy()
    if "built_mwh" not in df.columns:
        df["built_mwh"] = np.nan
    df.loc[legacy, "built_mwh"] = df.loc[legacy, "built_mw"]
    df.loc[legacy, "built_mw"] = np.nan
    df.loc[legacy, "component"] = "storage_energy"
    return df


def coerce(df: pd.DataFrame, spec: TableSpec, *, strict: bool = True) -> pd.DataFrame:
    """Rename legacy columns, check ``df`` against ``spec`` and coerce understood columns.

    Raises :class:`SchemaError` when a required column is absent, when a table
    carries both the legacy and the canonical spelling of one column (the
    specification's ``duplicate_spelling``) — both only when ``strict`` — or
    when a value cannot take its column's kind. Without ``strict`` a doubled
    spelling keeps the canonical column. Unknown columns pass through unchanged
    and column order is preserved.
    """
    doubled = [f"{old!r} and {new!r}" for old, new in spec.aliases.items() if old in df.columns and new in df.columns]
    if doubled and strict:
        raise SchemaError(f"table {spec.name!r} carries both the legacy and the canonical spelling of a column "
                          f"(duplicate_spelling): {'; '.join(doubled)}")
    rename = {old: new for old, new in spec.aliases.items() if old in df.columns and new not in df.columns}
    out = df.rename(columns=rename) if rename else df.copy()
    missing = [c for c in spec.required if c not in out.columns]
    if missing and strict:
        raise SchemaError(f"table {spec.name!r} is missing required column(s): {', '.join(missing)}")
    for col, kind in spec.kinds().items():
        if col not in out.columns:
            continue
        try:
            out[col] = _coerce_column(out[col], kind)
        except (ValueError, TypeError) as exc:
            raise SchemaError(f"table {spec.name!r}, column {col!r}: cannot read as {kind} ({exc})") from exc
    if spec.name == "investment" and "component" in out.columns and "built_mw" in out.columns:
        out = _normalise_investment(out)
    return out
