# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Write a :class:`~ce_genx.convert.CEBundle` to disk, and check one that was written.

A bundle is a directory holding ``bundle.toml`` and one table per file, in
Parquet (default) or CSV. Column types follow the CE data format: integer ids
are ``int32``, ``scenario``/``block`` are ``int16``, ``period`` is ``int32`` and
``availability.cf`` is ``float32``. No JSON is written.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

from ._toml import dumps
from .convert import CEBundle

__all__ = ["write_bundle", "check_bundle", "REPORT_FILE"]

REPORT_FILE = "x_genx_import_report.toml"

_DTYPES = {
    "id": "int32",
    "bus": "int32",
    "from_bus": "int32",
    "to_bus": "int32",
    "scenario": "int16",
    "block": "int16",
    "period": "int32",
    "min_up_h": "int16",
    "min_down_h": "int16",
    "cf": "float32",
}

#: the tables this importer can write, in writing order
TABLES = ("buses", "hvdc", "lines", "generators", "storage", "candidates", "demand", "availability")


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col, dtype in _DTYPES.items():
        if col in df.columns:
            if dtype.startswith("int") and df[col].isna().any():
                dtype = dtype.capitalize()  # pandas' nullable integer (written as null)
            df[col] = df[col].astype(dtype)
    for col in df.columns:
        if df[col].dtype == object and df[col].map(lambda v: isinstance(v, str)).all():
            df[col] = df[col].astype(str)
    return df


def write_bundle(bundle: CEBundle, out_dir, *, fmt: str = "parquet", overwrite: bool = False) -> list[Path]:
    """Write ``bundle`` into ``out_dir`` and return the paths written.

    ``fmt`` is ``"parquet"`` or ``"csv"``. An existing non-empty ``out_dir`` is
    refused unless ``overwrite`` is true, in which case only the files this
    function writes are replaced.
    """
    if fmt not in ("parquet", "csv"):
        raise ValueError("fmt must be 'parquet' or 'csv'")
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()) and not overwrite:
        raise FileExistsError(f"{out} is not empty (pass overwrite=True to replace the bundle files)")
    out.mkdir(parents=True, exist_ok=True)

    written = []
    for name in TABLES:
        # never leave a stale copy of a table in the other format
        for ext in (".parquet", ".csv", ".arrow"):
            stale = out / f"{name}{ext}"
            if stale.exists():
                stale.unlink()
    for name in TABLES:
        df = bundle.tables.get(name)
        if df is None:
            continue
        df = _typed(df)
        path = out / f"{name}.{fmt}"
        if fmt == "parquet":
            df.to_parquet(path, index=False)
        else:
            df.to_csv(path, index=False)
        written.append(path)

    path = out / "bundle.toml"
    path.write_text(dumps(bundle.descriptor))
    written.insert(0, path)

    path = out / REPORT_FILE
    path.write_text(bundle.report.to_toml())
    written.append(path)
    return written


def _read(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)


def read_tables(bundle_dir) -> dict:
    """Read every table of a bundle written by :func:`write_bundle`."""
    d = Path(bundle_dir)
    tables = {}
    for name in TABLES:
        for ext in (".parquet", ".csv"):
            p = d / f"{name}{ext}"
            if p.exists():
                tables[name] = _read(p)
                break
    return tables


def check_bundle(bundle_dir) -> list[str]:
    """Structural checks on a written bundle; returns a list of problems (empty = fine).

    This is a light self-check for the importer's own output: required files
    and columns, references between tables, complete series and value ranges.
    The authoritative validator for the data format lives with the format
    specification.
    """
    d = Path(bundle_dir)
    problems: list[str] = []
    if any(p.suffix.lower() == ".json" for p in d.iterdir()):
        problems.append("bundle contains a .json file")
    try:
        desc = tomllib.loads((d / "bundle.toml").read_text())
    except FileNotFoundError:
        return ["bundle.toml missing"]
    for key in ("format", "bundle", "money", "time"):
        if key not in desc:
            problems.append(f"bundle.toml: [{key}] missing")
    blocks = {b["id"]: b["nperiods"] for b in desc.get("time", {}).get("blocks", [])}

    t = read_tables(d)
    missing = [name for name in ("buses", "demand") if name not in t]
    if missing:
        return problems + [f"{name} table missing" for name in missing]
    bus_ids = set(t["buses"]["id"])
    if t["buses"]["id"].duplicated().any() or t["buses"]["name"].duplicated().any():
        problems.append("buses: duplicate id or name")

    def refs(table, col):
        if table in t and col in t[table].columns:
            bad = set(t[table][col]) - bus_ids
            if bad:
                problems.append(f"{table}.{col} references unknown buses {sorted(bad)}")

    for table, col in (("hvdc", "from_bus"), ("hvdc", "to_bus"), ("lines", "from_bus"), ("lines", "to_bus"),
                       ("generators", "bus"), ("storage", "bus"), ("candidates", "bus"), ("demand", "bus")):
        refs(table, col)

    gen_names = set(t["generators"]["name"]) if "generators" in t else set()
    cand_ids = set(t["candidates"]["candidate_id"]) if "candidates" in t else set()
    if gen_names & cand_ids:
        problems.append("candidate ids clash with generator names")
    if "candidates" in t and (t["candidates"]["max_mw"] <= 0).any():
        problems.append("candidates.max_mw must be > 0")
    needs_series = set()
    if "generators" in t:
        g = t["generators"]
        needs_series |= set(g.loc[g["kind"] == "renewable", "name"])
    if "candidates" in t:
        c = t["candidates"]
        needs_series |= set(c.loc[c["kind"].isin(["renewable", "hybrid"]), "candidate_id"])
    have = set(t["availability"]["site_name"]) if "availability" in t else set()
    if needs_series - have:
        problems.append(f"availability missing for {sorted(needs_series - have)}")
    if have - needs_series:
        problems.append(f"availability for unknown sites {sorted(have - needs_series)}")

    for table, key, value, lo, hi in (("demand", "bus", "mw", 0.0, np.inf), ("availability", "site_name", "cf", 0.0, 1.0)):
        if table not in t:
            continue
        df = t[table]
        if df[value].isna().any() or (df[value] < lo - 1e-6).any() or (df[value] > hi + 1e-6).any():
            problems.append(f"{table}.{value} outside [{lo}, {hi}] or missing")
        for (k, s, b), grp in df.groupby([key, "scenario", "block"]):
            n = blocks.get(int(b))
            if n is None:
                problems.append(f"{table}: block {b} not declared in bundle.toml")
                break
            if sorted(grp["period"]) != list(range(1, n + 1)):
                problems.append(f"{table}: series ({k}, {s}, {b}) is not periods 1..{n}")
                break
    if "storage" in t:
        s = t["storage"]
        if (s["duration_h"] <= 0).any() or ((s["eta_charge"] <= 0) | (s["eta_charge"] > 1)).any():
            problems.append("storage: duration_h must be > 0 and eta_charge in (0, 1]")
    return problems
