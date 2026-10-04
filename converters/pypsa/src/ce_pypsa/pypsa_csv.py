# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Read and write PyPSA CSV folders without depending on PyPSA.

A PyPSA CSV folder (``Network.export_to_csv_folder``) holds:

* ``network.csv`` - network attributes (``name``, ``pypsa_version``, ...);
* ``snapshots.csv`` - the time axis and snapshot weightings;
* ``<list_name>.csv`` - one static table per component type, indexed by name
  (``buses.csv``, ``generators.csv``, ``lines.csv``, ...);
* ``<list_name>-<attr>.csv`` - one time-varying attribute per file, one row per
  snapshot and one column per component (``generators-p_max_pu.csv``, ...).

Recent PyPSA versions index the time-series files by snapshot *position* and
older ones by snapshot *label*; both are accepted here. Files written here use
the snapshot label as the index, which both conventions read correctly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

__all__ = ["PypsaTables", "read_csv_folder", "write_csv_folder", "UnsupportedNetworkError", "LIST_NAMES"]

#: PyPSA component list names this package reads or writes
LIST_NAMES = ("buses", "carriers", "generators", "loads", "lines", "transformers", "links",
              "storage_units", "stores", "global_constraints", "shunt_impedances")

WEIGHT_COLUMNS = ("objective", "generators", "stores")


class UnsupportedNetworkError(ValueError):
    """The network uses a PyPSA feature this converter does not translate."""


@dataclass
class PypsaTables:
    """A PyPSA network as plain DataFrames.

    ``snapshots``: index = snapshot label (str), columns ``objective``,
    ``generators``, ``stores`` (the snapshot weightings).
    ``static[list_name]``: index = component name (str).
    ``series[list_name][attr]``: index = snapshot label, columns = component names.
    """

    name: str = ""
    snapshots: pd.DataFrame = field(default_factory=lambda: _weightings(["now"]))
    static: dict[str, pd.DataFrame] = field(default_factory=dict)
    series: dict[str, dict[str, pd.DataFrame]] = field(default_factory=dict)
    attributes: dict = field(default_factory=dict)
    unread: list[str] = field(default_factory=list)  # CSV files present but not translated

    def get(self, list_name: str) -> pd.DataFrame:
        df = self.static.get(list_name)
        return df if df is not None else pd.DataFrame(index=pd.Index([], name="name", dtype=object))

    def get_series(self, list_name: str, attr: str) -> pd.DataFrame | None:
        return self.series.get(list_name, {}).get(attr)


def _weightings(labels, objective=None, generators=None, stores=None) -> pd.DataFrame:
    n = len(labels)
    ones = np.ones(n)
    return pd.DataFrame(
        {"objective": ones if objective is None else np.asarray(objective, float),
         "generators": ones if generators is None else np.asarray(generators, float),
         "stores": ones if stores is None else np.asarray(stores, float)},
        index=pd.Index([str(s) for s in labels], name="snapshot"),
    )


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, index_col=0)


def _read_snapshots(folder: Path) -> pd.DataFrame | None:
    fn = folder / "snapshots.csv"
    if not fn.is_file():
        return None
    df = pd.read_csv(fn, index_col=0)
    if {"period", "timestep"} & set(df.columns):
        raise UnsupportedNetworkError(
            "multi-investment-period networks (snapshots with 'period'/'timestep') are not supported; "
            "convert one investment period at a time")
    labels = df["snapshot"].astype(str).tolist() if "snapshot" in df.columns else [str(i) for i in df.index]
    if set(WEIGHT_COLUMNS) & set(df.columns):
        w = {c: df[c].to_numpy(float) if c in df.columns else None for c in WEIGHT_COLUMNS}
        return _weightings(labels, **w)
    if "weightings" in df.columns:  # very old PyPSA: a single weighting column
        w = df["weightings"].to_numpy(float)
        return _weightings(labels, w, w, w)
    return _weightings(labels)


def _align_series(df: pd.DataFrame, labels: list[str], fn: Path) -> pd.DataFrame:
    idx = [str(i) for i in df.index]
    if len(idx) != len(labels):
        raise ValueError(f"{fn.name}: {len(idx)} rows but the network has {len(labels)} snapshots")
    if set(idx) == set(labels):
        df.index = idx
        df = df.loc[labels]
    df.index = pd.Index(labels, name="snapshot")  # positional otherwise
    df.columns = [str(c) for c in df.columns]
    return df.astype(float)


def read_csv_folder(folder: str | Path) -> PypsaTables:
    """Read a PyPSA CSV folder into :class:`PypsaTables`."""
    folder = Path(folder)
    if not (folder / "buses.csv").is_file():
        raise FileNotFoundError(f"{folder} is not a PyPSA CSV folder (no buses.csv)")
    attrs: dict = {}
    if (folder / "network.csv").is_file():
        net = pd.read_csv(folder / "network.csv")
        if len(net):
            attrs = {k: v for k, v in net.iloc[0].items() if pd.notna(v)}
    if int(float(attrs.get("_multi_invest", 0) or 0)):
        raise UnsupportedNetworkError("multi-investment-period networks are not supported")
    if (folder / "scenarios.csv").is_file():
        raise UnsupportedNetworkError("stochastic (multi-scenario) PyPSA networks are not supported")

    snapshots = _read_snapshots(folder)
    static: dict[str, pd.DataFrame] = {}
    series: dict[str, dict[str, pd.DataFrame]] = {}
    unread: list[str] = []
    for fn in sorted(folder.glob("*.csv")):
        m = re.match(r"^([a-z_]+)-(.+)\.csv$", fn.name)
        if m and not fn.name.endswith("-pw.csv"):
            series.setdefault(m.group(1), {})[m.group(2)] = _read_csv(fn)
        elif fn.stem in LIST_NAMES:
            df = _read_csv(fn)
            df.index = pd.Index([str(i) for i in df.index], name="name")
            static[fn.stem] = df
        elif fn.stem not in ("network", "snapshots", "investment_periods"):
            unread.append(fn.name)

    if snapshots is None:  # no snapshots.csv: infer from a series file, else PyPSA's single "now"
        first = next((df for attrs_ in series.values() for df in attrs_.values()), None)
        snapshots = _weightings([str(i) for i in first.index] if first is not None else ["now"])
    labels = list(snapshots.index)
    for list_name, by_attr in series.items():
        for attr, df in by_attr.items():
            by_attr[attr] = _align_series(df, labels, folder / f"{list_name}-{attr}.csv")

    name = str(attrs.pop("name", "")) if "name" in attrs else ""
    return PypsaTables(name=name, snapshots=snapshots, static=static, series=series, attributes=attrs,
                       unread=unread)


def _fmt_bool_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == bool:
            out[c] = out[c].map({True: "True", False: "False"})
    return out


def write_csv_folder(tables: PypsaTables, folder: str | Path, pypsa_version: str | None = None) -> Path:
    """Write :class:`PypsaTables` as a PyPSA CSV folder readable by ``pypsa.Network(folder)``."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    attrs = {"name": tables.name or "ce_bundle", "_multi_invest": 0}
    if pypsa_version:
        attrs["pypsa_version"] = pypsa_version
    pd.DataFrame([attrs]).to_csv(folder / "network.csv", index=False)

    snap = tables.snapshots.reset_index()
    snap.columns = ["snapshot", *WEIGHT_COLUMNS]
    snap.index.name = ""
    snap.to_csv(folder / "snapshots.csv")

    for list_name, df in tables.static.items():
        if df is None or len(df) == 0:
            continue
        out = _fmt_bool_columns(df)
        out.index.name = "name"
        out.to_csv(folder / f"{list_name}.csv")
    for list_name, by_attr in tables.series.items():
        for attr, df in by_attr.items():
            if df is None or df.shape[1] == 0:
                continue
            out = df.copy()
            out.index = pd.Index(list(tables.snapshots.index), name="snapshot")
            out.to_csv(folder / f"{list_name}-{attr}.csv")
    return folder
