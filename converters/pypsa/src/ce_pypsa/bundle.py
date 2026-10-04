# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The CE bundle (CE data format 1.0, see ``spec/`` at the repository root).

A bundle is a directory::

    <bundle>/
    ├── bundle.toml                  # format, identity, money, time axis, scenarios, system scalars
    ├── buses.parquet                # required
    ├── demand.parquet               # required: bus, scenario, block, period, mw
    ├── lines.parquet                # AC branches (DC power flow)
    ├── hvdc.parquet                 # controllable corridors
    ├── generators.parquet           # EXISTING generating fleet (kind thermal | renewable)
    ├── storage.parquet              # EXISTING storage fleet
    ├── candidates.parquet           # build options (thermal | renewable | storage | hybrid)
    ├── availability.parquet         # cf per renewable generator / candidate, keyed by name
    ├── x_pypsa_snapshots.parquet    # extension: source snapshot labels and weightings
    ├── x_conversion_report.toml     # extension: what the converter approximated or dropped
    └── <table>.parquet.prov         # provenance sidecars

Tables are Parquet by default; ``.arrow`` (Arrow IPC file) and ``.csv`` are
also written and read. JSON is never used. Names starting ``x_`` are format
extensions: the specification never checks them, and readers that do not
know them ignore them.

This module is the one place that lists the tables and columns this package
reads and writes, with their canonical types. It mirrors the schema files in
``spec/schemas/bundle``; if the specification changes, update ``SCHEMA``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import _toml

__all__ = ["Column", "SCHEMA", "ALIASES", "REFUSED", "FORMAT_VERSION", "Bundle", "read_table", "write_table", "conform",
           "with_defaults"]

FORMAT_NAME = "ce-bundle"
FORMAT_VERSION = "1.0"
FULL_YEAR_HOURS = (8760.0, 8784.0)

_ARROW = {"int8": pa.int8(), "int16": pa.int16(), "int32": pa.int32(), "int64": pa.int64(),
          "float32": pa.float32(), "float64": pa.float64(), "string": pa.string(), "bool": pa.bool_()}


@dataclass(frozen=True)
class Column:
    name: str
    type: str                 # a spec type: int16/int32/float32/float64/string/bool
    required: bool = False
    default: Any = None       # filled when the column is absent (None = leave absent / null)

    @property
    def arrow_type(self) -> pa.DataType:
        return _ARROW[self.type]


def _c(name, type_, required=False, default=None):
    return Column(name, type_, required, default)


_SERIES_AXIS = [_c("scenario", "int16", True), _c("block", "int16", True), _c("period", "int32", True),
                _c("year", "int16"), _c("weather", "int16")]

SCHEMA: dict[str, list[Column]] = {
    "buses": [
        _c("id", "int32", True), _c("name", "string", True), _c("zone", "string", default="default"),
        _c("lat", "float64"), _c("lon", "float64"), _c("voltage_kv", "float64"), _c("label", "string"),
    ],
    "lines": [
        _c("id", "int32", True), _c("name", "string", True), _c("from_bus", "int32", True),
        _c("to_bus", "int32", True), _c("susceptance_pu", "float64", True), _c("rating_mw", "float64", True),
        _c("existing", "bool", default=True), _c("candidate", "bool", default=False),
        _c("delta_rating_mw", "float64", default=0.0), _c("capex_per_yr", "float64", default=0.0),
        _c("loss_frac", "float64", default=0.0), _c("contingency", "bool", default=False),
        _c("length_km", "float64"), _c("lead_time_years", "int16"), _c("label", "string"),
    ],
    "hvdc": [
        _c("id", "int32", True), _c("name", "string", True), _c("from_bus", "int32", True),
        _c("to_bus", "int32", True), _c("existing_mw", "float64", default=0.0),
        _c("candidate_max_mw", "float64", default=0.0), _c("converter_efficiency", "float64", default=0.985),
        _c("capex_per_mw_yr", "float64", default=0.0), _c("lead_time_years", "int16"),
    ],
    "generators": [
        _c("id", "int32", True), _c("name", "string", True), _c("bus", "int32", True),
        _c("technology", "string", True), _c("kind", "string", True), _c("existing_mw", "float64", True),
        _c("varcost_per_mwh", "float64", default=0.0), _c("fom_per_mw_yr", "float64", default=0.0),
        _c("pmin_frac", "float64"), _c("ramp_frac_per_h", "float64"), _c("min_up_h", "int16"),
        _c("min_down_h", "int16"), _c("startup_cost", "float64"), _c("noload_cost_per_h", "float64"),
        _c("co2_t_per_mwh", "float64", default=0.0), _c("firm_capacity_frac", "float64"),
        _c("retirable", "bool", default=False), _c("retirement_year", "int16"),
        _c("lat", "float64"), _c("lon", "float64"),
    ],
    "storage": [
        _c("id", "int32", True), _c("name", "string", True), _c("bus", "int32", True),
        _c("technology", "string", default="battery"), _c("existing_mw", "float64", True),
        _c("duration_h", "float64", True), _c("eta_charge", "float64", default=0.95),
        _c("eta_discharge", "float64", default=0.95), _c("self_discharge_frac_per_h", "float64", default=0.0),
        _c("soc_init_frac", "float64", default=0.5), _c("dod_floor_frac", "float64", default=0.0),
        _c("varcost_per_mwh", "float64", default=0.0), _c("firm_capacity_frac", "float64"),
    ],
    "candidates": [
        _c("candidate_id", "string", True), _c("technology", "string", True), _c("kind", "string", True),
        _c("bus", "int32", True), _c("max_mw", "float64", True), _c("capex_per_mw_yr", "float64"),
        _c("capex_overnight_per_mw", "float64"), _c("annualisation_rate", "float64"),
        _c("lifetime_years", "float64"), _c("fom_per_mw_yr", "float64", default=0.0),
        _c("varcost_per_mwh", "float64", default=0.0), _c("interconnection_cost_per_kw", "float64", default=0.0),
        _c("lead_time_years", "int16"), _c("duration_h", "float64"), _c("max_mwh", "float64"),
        _c("capex_per_mwh_yr", "float64", default=0.0), _c("generation_mw", "float64"),
        _c("storage_mw", "float64"), _c("co2_t_per_mwh", "float64", default=0.0),
        _c("firm_capacity_frac", "float64"), _c("integer_build", "bool", default=False),
        _c("unit_size_mw", "float64"), _c("cf_mean", "float64"), _c("lat", "float64"), _c("lon", "float64"),
    ],
    "demand": [_c("bus", "int32", True), *_SERIES_AXIS[:3], _c("mw", "float64", True), *_SERIES_AXIS[3:]],
    "availability": [_c("site_name", "string", True), *_SERIES_AXIS[:3], _c("cf", "float32", True),
                     *_SERIES_AXIS[3:]],
    # extension table written by this package
    "x_pypsa_snapshots": [
        _c("block", "int16", True), _c("period", "int32", True), _c("label", "string", True),
        _c("weight_objective", "float64", True), _c("weight_generators", "float64", True),
        _c("weight_stores", "float64", True),
    ],
}

#: legacy column spellings accepted on read (spec ``[[alias]]`` entries for the tables used here)
ALIASES: dict[str, dict[str, str]] = {
    "lines": {"capex_gbp": "capex_per_yr"},
    "generators": {"varcost_gbp_per_mwh": "varcost_per_mwh", "fom_gbp_per_mw": "fom_per_mw_yr",
                   "startup_gbp": "startup_cost", "noload_gbp_per_h": "noload_cost_per_h"},
    "storage": {"varcost_gbp_per_mwh": "varcost_per_mwh"},
    "candidates": {"site_id": "candidate_id", "bus_id": "bus",
                   "capex_per_mw_annualised": "capex_per_mw_yr",
                   "fom_gbp_per_mw": "fom_per_mw_yr", "varcost_gbp_per_mwh": "varcost_per_mwh",
                   "interconnection_gbp_per_kw": "interconnection_cost_per_kw"},
}

#: historical cost spellings that do not say whether a cost is annualised or overnight: refused on read,
#: never renamed (spec ``[[refused_spelling]]``, issue ``ambiguous_legacy_cost``)
REFUSED: dict[str, tuple[str, ...]] = {
    "hvdc": ("capex_gbp_per_mw",),
    "candidates": ("capex_gbp_per_mw", "capex_gbp_per_mwh"),
}

#: table -> (series id column, value column)
SERIES = {"demand": ("bus", "mw"), "availability": ("site_name", "cf")}

_EXTS = (".parquet", ".arrow", ".csv.gz", ".csv")


# --------------------------------------------------------------------------- table helpers
def _arrow_array(values: pd.Series, col: Column) -> pa.Array:
    if col.type.startswith("int"):
        return pa.array(pd.to_numeric(values).astype("Int64"), type=col.arrow_type)
    if col.type.startswith("float"):
        return pa.array(pd.to_numeric(values).astype("float64").to_numpy(), type=col.arrow_type, from_pandas=True)
    if col.type == "bool":
        return pa.array(values.map(_to_bool).astype("boolean"), type=col.arrow_type)
    return pa.array(values.map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else str(v)),
                    type=col.arrow_type)


def _to_bool(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    return str(v).strip().lower() in ("true", "1", "1.0", "yes")


def conform(name: str, df: pd.DataFrame) -> pa.Table:
    """Cast ``df`` to the table's canonical Arrow types.

    Schema columns come first (absent optional columns with a default are
    filled; absent optional columns without one are left out), then ``x_``
    extension columns with inferred types. Any other column is an error.
    """
    spec = SCHEMA.get(name)
    if spec is None:
        return pa.Table.from_pandas(df.reset_index(drop=True), preserve_index=False)
    known = {c.name for c in spec}
    unknown = [c for c in df.columns if c not in known and not str(c).startswith("x_")]
    if unknown:
        raise ValueError(f"table {name!r}: columns {unknown} are not in the specification (prefix extensions x_)")
    arrays, fields = [], []
    n = len(df)
    for col in spec:
        if col.name in df.columns:
            values = df[col.name].reset_index(drop=True)
            if col.default is not None:
                values = values.astype(object).where(values.notna(), col.default)
        elif col.required:
            raise ValueError(f"table {name!r}: required column {col.name!r} is missing")
        elif col.default is not None:
            values = pd.Series([col.default] * n)
        else:
            continue
        arrays.append(_arrow_array(values, col))
        fields.append(pa.field(col.name, col.arrow_type))
    for c in df.columns:
        if str(c).startswith("x_"):
            arr = pa.array(df[c].reset_index(drop=True), from_pandas=True)
            arrays.append(arr)
            fields.append(pa.field(c, arr.type))
    return pa.Table.from_arrays(arrays, schema=pa.schema(fields))


def table_path(bundle_dir: str | Path, name: str) -> Path | None:
    """Path of table ``name`` by precedence .parquet > .arrow > .csv.gz > .csv, or None."""
    for ext in _EXTS:
        p = Path(bundle_dir) / f"{name}{ext}"
        if p.is_file():
            return p
    return None


def read_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.name.endswith(".parquet"):
        return pd.read_parquet(path)
    if path.name.endswith(".arrow"):
        with pa.memory_map(str(path), "r") as src:
            return pa.ipc.open_file(src).read_all().to_pandas()
    if path.name.endswith(".csv") or path.name.endswith(".csv.gz"):
        return pd.read_csv(path)
    raise ValueError(f"unsupported table format: {path}")


def write_table(table: pa.Table, path: str | Path) -> None:
    path = Path(path)
    if path.name.endswith(".parquet"):
        pq.write_table(table, path)
    elif path.name.endswith(".arrow"):
        with pa.OSFile(str(path), "wb") as sink, pa.ipc.new_file(sink, table.schema) as w:
            w.write_table(table)
    elif path.name.endswith(".csv"):
        import pyarrow.csv as pv

        pv.write_csv(table, path, write_options=pv.WriteOptions(quoting_style="needed"))
    else:
        raise ValueError(f"unsupported table format: {path}")


def with_defaults(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Fill absent or blank cells of defaulted columns with the specification's default."""
    df = df.copy()
    for col in SCHEMA.get(name, []):
        if col.default is None:
            continue
        if col.name not in df.columns:
            df[col.name] = col.default
        elif df[col.name].isna().any():
            df[col.name] = df[col.name].astype(object).where(df[col.name].notna(), col.default).infer_objects()
    return df


def _normalise_read(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Rename legacy spellings and fill defaulted columns after a read."""
    refused = [c for c in REFUSED.get(name, ()) if c in df.columns]
    if refused:
        raise ValueError(
            f"table {name!r}: column(s) {refused} do not say whether the cost is annualised or overnight "
            "(ambiguous_legacy_cost); rename to capex_per_mw_yr / capex_per_mwh_yr (annualised) or "
            "capex_overnight_per_mw (overnight) after checking the values")
    for legacy, canonical in ALIASES.get(name, {}).items():
        if legacy in df.columns and canonical not in df.columns:
            df = df.rename(columns={legacy: canonical})
    df = with_defaults(name, df)
    for col in SCHEMA.get(name, []):
        if col.type == "bool" and col.name in df.columns:
            df[col.name] = df[col.name].map(_to_bool)
            if col.default is not None:
                df[col.name] = df[col.name].fillna(col.default).astype(bool)
        if col.type == "string" and col.name in df.columns and col.required:
            df[col.name] = df[col.name].astype(str)
    return df


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _prov_text(values: dict) -> str:
    lines = []
    for k, v in values.items():
        s = str(v).replace("\n", " ")
        if s != s.strip():
            s = f'"{s}"'
        lines.append(f"{k}={s}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- the bundle
@dataclass
class Bundle:
    """An in-memory CE bundle.

    ``descriptor`` mirrors ``bundle.toml``; ``tables`` maps table names
    (without extension) to DataFrames; ``report`` is the conversion report
    (a :class:`~ce_pypsa.report.ConversionReport` after a conversion, a
    ``dict`` after :meth:`read`) written to ``x_conversion_report.toml``.
    """

    descriptor: dict
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    report: Any = None
    provenance: dict = field(default_factory=dict)   # key=value lines for every table's .prov sidecar

    # ---------------------------------------------------------------- access
    def table(self, name: str) -> pd.DataFrame:
        """The named table, or an empty table with the schema's columns when absent."""
        df = self.tables.get(name)
        if df is not None:
            return df
        return pd.DataFrame({c.name: pd.Series(dtype=object) for c in SCHEMA.get(name, [])})

    def has(self, name: str) -> bool:
        df = self.tables.get(name)
        return df is not None and len(df) > 0

    @property
    def blocks(self) -> list[dict]:
        return list(self.descriptor.get("time", {}).get("blocks", []))

    @property
    def years(self) -> list[int]:
        return [int(y) for y in self.descriptor.get("time", {}).get("investment_years", [])]

    @property
    def scenario_names(self) -> list[str]:
        return list(self.descriptor.get("scenarios", {}).get("names", ["base"]))

    def block_dt(self, b: int) -> np.ndarray:
        """Per-period durations (hours) of block ``b`` (1-based)."""
        blk = self.blocks[b - 1]
        dt = blk.get("dt_h", 1.0)
        if isinstance(dt, list):
            return np.asarray(dt, dtype=float)
        return np.full(int(blk["nperiods"]), float(dt))

    # ---------------------------------------------------------------- I/O
    def write(self, path: str | Path, fmt: str = "parquet", overwrite: bool = False, prov: bool = True) -> Path:
        """Write the bundle to directory ``path``; tables as ``fmt`` (parquet, arrow or csv)."""
        path = Path(path)
        ext = {"parquet": ".parquet", "arrow": ".arrow", "csv": ".csv"}[fmt]
        if path.exists() and any(path.iterdir()) and not overwrite:
            raise FileExistsError(f"{path} exists and is not empty (pass overwrite=True)")
        path.mkdir(parents=True, exist_ok=True)
        if overwrite:  # remove stale copies of this package's own outputs only, never unrelated files
            for name in SCHEMA:
                for e in _EXTS:
                    for stale in (path / f"{name}{e}", path / f"{name}{e}.prov"):
                        if stale.is_file():
                            stale.unlink()
        _toml.dump(self.descriptor, path / "bundle.toml", header="CE bundle descriptor (CE data format 1.0)")
        for name, df in self.tables.items():
            target = path / f"{name}{ext}"
            table = conform(name, df)
            write_table(table, target)
            if prov:
                values = {**self.provenance, "rows": table.num_rows, "sha256": _sha256(target)}
                (path / f"{target.name}.prov").write_text(_prov_text(values), encoding="utf-8")
        if self.report is not None:
            rep = self.report.as_dict() if hasattr(self.report, "as_dict") else self.report
            _toml.dump(rep, path / "x_conversion_report.toml",
                       header="Extension: what the converter approximated or dropped. Informational only.")
        return path

    @classmethod
    def read(cls, path: str | Path) -> "Bundle":
        path = Path(path)
        if not (path / "bundle.toml").is_file():
            raise FileNotFoundError(f"{path} has no bundle.toml")
        descriptor = _toml.load(path / "bundle.toml")
        tables = {}
        for name in SCHEMA:
            p = table_path(path, name)
            if p is not None:
                tables[name] = _normalise_read(name, read_table(p))
        report = _toml.load(path / "x_conversion_report.toml") if (path / "x_conversion_report.toml").is_file() \
            else None
        return cls(descriptor=descriptor, tables=tables, report=report)

    # ---------------------------------------------------------------- validation
    def validate(self) -> list[str]:
        """Structural checks mirroring the specification's main rules (the reference validator is
        ``python -m ce_spec validate``). Returns a list of problems; empty when none were found."""
        errs: list[str] = []
        d = self.descriptor
        if d.get("format", {}).get("name") != FORMAT_NAME:
            errs.append("bundle.toml: [format] name must be 'ce-bundle'")
        for key in ("currency", "dollar_year"):
            if key not in d.get("money", {}):
                errs.append(f"bundle.toml: [money] {key} is required")
        if not self.blocks:
            errs.append("bundle.toml: at least one [[time.blocks]] entry is required")
        for i, blk in enumerate(self.blocks, start=1):
            if blk.get("id") != i:
                errs.append(f"time.blocks[{i - 1}]: ids must be 1..B in order")
            if blk.get("kind") == "chronological":
                if "start" not in blk:
                    errs.append(f"time.blocks[{i - 1}]: a chronological block needs start")
                if not np.isclose(float(blk["weight_h"]), self.block_dt(i).sum()):
                    errs.append(f"time.blocks[{i - 1}]: chronological weight_h must equal sum(dt_h)")
        probs = d.get("scenarios", {}).get("probability", [1.0])
        if abs(sum(probs) - 1.0) > 1e-9:
            errs.append("bundle.toml: scenario probabilities must sum to 1")
        for name in ("buses", "demand"):
            if name not in self.tables:
                errs.append(f"{name}: required table is missing")
        for name, key in (("buses", "id"), ("lines", "id"), ("hvdc", "id"), ("generators", "id"),
                          ("storage", "id"), ("candidates", "candidate_id"), ("buses", "name"),
                          ("lines", "name"), ("hvdc", "name"), ("generators", "name"), ("storage", "name")):
            df = self.tables.get(name)
            if df is not None and key in df.columns and df[key].duplicated().any():
                errs.append(f"{name}.{key}: duplicate values")
        names = [*self.table("generators")["name"], *self.table("storage")["name"],
                 *self.table("candidates")["candidate_id"]]
        if len(names) != len(set(names)):
            errs.append("generators / storage / candidates share a name (one name space)")
        bus_ids = set(self.table("buses")["id"])
        for name, cols in (("lines", ("from_bus", "to_bus")), ("hvdc", ("from_bus", "to_bus")),
                           ("generators", ("bus",)), ("storage", ("bus",)), ("candidates", ("bus",)),
                           ("demand", ("bus",))):
            for col in cols:
                bad = set(self.table(name)[col]) - bus_ids
                if bad:
                    errs.append(f"{name}.{col}: unknown bus id(s) {sorted(bad)[:5]}")
        lines = self.table("lines")
        if len(lines) and ((lines["susceptance_pu"] <= 0) | (lines["rating_mw"] <= 0)).any():
            errs.append("lines: susceptance_pu and rating_mw must be > 0")
        gens, cands = self.table("generators"), self.table("candidates")
        need = set(gens.loc[(gens["kind"] == "renewable") & (gens["existing_mw"] > 0), "name"]) | set(
            cands.loc[cands["kind"].isin(["renewable", "hybrid"]), "candidate_id"])
        av = self.tables.get("availability")
        have = set(av["site_name"]) if av is not None else set()
        if need - have:
            errs.append(f"availability: no series for {sorted(need - have)[:5]}")
        if av is not None and len(av) and ((av["cf"] < -1e-6) | (av["cf"] > 1 + 1e-6)).any():
            errs.append("availability: cf outside [0, 1]")
        dem = self.tables.get("demand")
        if dem is not None and len(dem) and (dem["mw"] < 0).any():
            errs.append("demand: mw must be >= 0")
        nscen = len(probs)
        for name, (idcol, _) in SERIES.items():
            df = self.tables.get(name)
            if df is None or not len(df):
                continue
            for (_, k, b), g in df.groupby([idcol, "scenario", "block"]):
                if not (1 <= k <= nscen) or not (1 <= b <= len(self.blocks)):
                    errs.append(f"{name}: scenario {k} / block {b} outside the declared axis")
                    break
                n = len(self.block_dt(int(b)))
                if sorted(g["period"]) != list(range(1, n + 1)):
                    errs.append(f"{name}: block {b} periods are not exactly 1..{n}")
                    break
        return errs
