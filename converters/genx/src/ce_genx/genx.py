# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Read a GenX case folder into one normalised, layout-independent structure.

GenX has used two input layouts:

``legacy`` (GenX up to the 0.3 series)
    Every input sits in the case folder. All resources share one table,
    ``Generators_data.csv``, and the resource type is given by flag columns
    (``THERM``, ``VRE``, ``STOR``, ``HYDRO``, ``MUST_RUN``, ``FLEX``, ...).
    Demand is ``Load_data.csv`` with ``Load_MW_z<n>`` columns. Settings live in
    ``Settings/genx_settings.yml``.

``modular`` (GenX 0.4 and later)
    Inputs are split into ``system/`` (``Demand_data.csv``, ``Fuels_data.csv``,
    ``Generators_variability.csv``, ``Network.csv``), ``resources/`` (one file
    per resource type: ``Thermal.csv``, ``Vre.csv``, ``Storage.csv``,
    ``Hydro.csv``, ``Must_run.csv``, ``Flex_demand.csv``, ...), ``policies/``
    and ``settings/``. Demand columns are ``Demand_MW_z<n>`` and the resource
    type is the file it appears in.

Both layouts are read into :class:`GenXCase`. Column names are lower-cased so
the converter does not depend on capitalisation, which has varied between
GenX releases. File names are matched case-insensitively for the same reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

__all__ = ["GenXCase", "TimeStructure", "read_case", "detect_layout"]

#: resource kinds, in the order their resources are numbered
KINDS = ("thermal", "vre", "storage", "hydro", "must_run", "flex", "vre_stor", "electrolyzer")

#: modular-layout resource files -> resource kind
_MODULAR_FILES = {
    "thermal.csv": "thermal",
    "vre.csv": "vre",
    "storage.csv": "storage",
    "hydro.csv": "hydro",
    "must_run.csv": "must_run",
    "flex_demand.csv": "flex",
    "vre_stor.csv": "vre_stor",
    "electrolyzer.csv": "electrolyzer",
}

#: legacy flag columns, in priority order, -> resource kind
_LEGACY_FLAGS = (
    ("stor", "storage"),
    ("therm", "thermal"),
    ("vre_stor", "vre_stor"),
    ("vre", "vre"),
    ("hydro", "hydro"),
    ("must_run", "must_run"),
    ("flex", "flex"),
    ("electrolyzer", "electrolyzer"),
)

_POLICY_FILES = {
    "co2_cap": "CO2_cap.csv",
    "capacity_reserve_margin": "Capacity_reserve_margin.csv",
    "energy_share_requirement": "Energy_share_requirement.csv",
    "minimum_capacity_requirement": "Minimum_capacity_requirement.csv",
    "maximum_capacity_requirement": "Maximum_capacity_requirement.csv",
}


@dataclass
class TimeStructure:
    """GenX's time axis: ``rep_periods`` sub-periods of ``timesteps`` hours each.

    ``weights_h[p]`` is the number of hours in a year that representative
    period ``p`` stands for (GenX's ``Sub_Weights``). A full-year,
    non-reduced case has one period of 8760 timesteps weighted 8760 h.
    """

    rep_periods: int
    timesteps: int
    weights_h: list[float]

    @property
    def T(self) -> int:
        return self.rep_periods * self.timesteps

    def hour_weights(self) -> np.ndarray:
        """Annual hours represented by each of the ``T`` timesteps."""
        per_step = np.asarray(self.weights_h, dtype=float) / self.timesteps
        return np.repeat(per_step, self.timesteps)


@dataclass
class GenXCase:
    """A GenX case, normalised across input layouts.

    All tables keep GenX's own units and values; the only normalisation is
    lower-cased column names, a ``kind`` (and, where GenX has one, ``model``)
    column on ``resources``, and a ``start_zone``/``end_zone`` pair on
    ``network`` whichever way the case wrote its topology.
    """

    root: Path
    layout: str
    settings: dict
    resources: pd.DataFrame
    demand: pd.DataFrame  # T rows; integer zone columns
    voll: float
    demand_segments: int
    time: TimeStructure
    variability: pd.DataFrame  # T rows; one column per resource name
    fuel_co2: dict  # fuel name -> tCO2 per MMBtu
    fuel_prices: pd.DataFrame  # T rows; one column per fuel
    network: pd.DataFrame | None = None
    policies: dict = field(default_factory=dict)

    @property
    def zones(self) -> list[int]:
        found = set(int(z) for z in self.resources["zone"]) if len(self.resources) else set()
        found |= set(int(z) for z in self.demand.columns)
        if self.network is not None and len(self.network):
            found |= set(int(z) for z in self.network["start_zone"])
            found |= set(int(z) for z in self.network["end_zone"])
        return sorted(found)

    def setting(self, key: str, default=None):
        return self.settings.get(key.lower(), default)


# ---------------------------------------------------------------------------
# file helpers


def _find(directory: Path, name: str) -> Path | None:
    """Case-insensitive lookup of ``name`` inside ``directory``."""
    if not directory.is_dir():
        return None
    exact = directory / name
    if exact.exists():
        return exact
    lowered = name.lower()
    for child in directory.iterdir():
        if child.name.lower() == lowered:
            return child
    return None


def _find_any(directories, names) -> Path | None:
    for d in directories:
        for n in names:
            p = _find(d, n)
            if p is not None:
                return p
    return None


def _read_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    # drop fully empty trailing columns some spreadsheet exports leave behind
    df = df.loc[:, [c for c in df.columns if not c.startswith("unnamed:")]]
    return df


def _first(df: pd.DataFrame, col: str, default=None):
    if col not in df.columns:
        return default
    s = df[col].dropna()
    return default if s.empty else s.iloc[0]


def read_settings(path: Path | None) -> dict:
    """Read the flat ``key: value`` pairs of a GenX settings YAML file.

    GenX settings files are flat mappings of scalars, so a full YAML parser is
    not needed (and is not a dependency). Nested blocks, lists and anything
    else that is not a ``key: scalar`` line are skipped. Keys are lower-cased.
    """
    if path is None or not path.exists():
        return {}
    out: dict = {}
    pattern = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")
    for raw in path.read_text().splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or raw[:1].isspace():
            continue
        line = raw.split(" #", 1)[0]
        m = pattern.match(line)
        if not m or m.group(2) == "":
            continue
        key, value = m.group(1).lower(), m.group(2).strip().strip("'\"")
        try:
            out[key] = int(value)
        except ValueError:
            try:
                out[key] = float(value)
            except ValueError:
                lowered = value.lower()
                out[key] = {"true": True, "false": False}.get(lowered, value)
    return out


# ---------------------------------------------------------------------------
# layout


def detect_layout(root: Path) -> str:
    """Return ``"modular"`` or ``"legacy"`` for a single-stage case folder."""
    root = Path(root)
    if _find(root, "resources") is not None and _find(root, "resources").is_dir():
        return "modular"
    if _find(root, "Generators_data.csv") is not None:
        return "legacy"
    raise FileNotFoundError(
        f"{root} is not a GenX case folder: expected a 'resources/' directory "
        "(GenX 0.4+) or 'Generators_data.csv' (earlier releases)"
    )


def _resolve_root(case_dir: Path, stage: int | None) -> Path:
    inputs = _find(case_dir, "inputs")
    stages = []
    if inputs is not None and inputs.is_dir():
        stages = sorted(p for p in inputs.iterdir() if p.is_dir() and p.name.lower().startswith("inputs_p"))
    if stages:
        if stage is None:
            raise ValueError(
                f"{case_dir} is a multi-stage GenX case ({len(stages)} stages); "
                "pass stage=N to import one stage"
            )
        chosen = _find(inputs, f"inputs_p{stage}")
        if chosen is None:
            raise FileNotFoundError(f"stage {stage} not found under {inputs}")
        return chosen
    if stage is not None:
        raise ValueError(f"stage={stage} given but {case_dir} is not a multi-stage case")
    return case_dir


# ---------------------------------------------------------------------------
# resources


def _read_resources(root: Path, layout: str) -> pd.DataFrame:
    if layout == "legacy":
        df = _read_csv(_find(root, "Generators_data.csv"))
        kinds, models = [], []
        for _, row in df.iterrows():
            kind, model = "other", 0
            for flag, k in _LEGACY_FLAGS:
                if flag in df.columns and pd.notna(row[flag]) and int(row[flag]) > 0:
                    kind, model = k, int(row[flag])
                    break
            kinds.append(kind)
            models.append(model)
        df["kind"] = kinds
        df["model"] = models
        if "can_retire" not in df.columns:
            # legacy New_Build: 1 build+retire, 0 retire only, -1 neither
            if "new_build" in df.columns:
                nb = pd.to_numeric(df["new_build"], errors="coerce").fillna(0)
            else:
                nb = pd.Series(0, index=df.index)
            df["can_retire"] = (nb >= 0).astype(int)
            df["new_build"] = (nb > 0).astype(int)
        return df.reset_index(drop=True)

    resdir = _find(root, "resources")
    frames = []
    seen = {}
    for child in sorted(resdir.iterdir()):
        if child.is_file() and child.suffix.lower() == ".csv":
            seen[child.name.lower()] = child
    order = list(_MODULAR_FILES) + sorted(n for n in seen if n not in _MODULAR_FILES)
    for fname in order:
        if fname not in seen:
            continue
        df = _read_csv(seen[fname])
        if df.empty:
            continue
        df["kind"] = _MODULAR_FILES.get(fname, Path(fname).stem)
        if "model" not in df.columns:
            df["model"] = 0
        frames.append(df)
    if not frames:
        raise ValueError(f"no resource tables found in {resdir}")
    df = pd.concat(frames, ignore_index=True, sort=False)

    # capacity-reserve deratings live in a side file in the modular layout;
    # bring them onto the resource rows as the legacy CapRes_<k> columns
    crm = _find_any([resdir / "policy_assignments", resdir], ["Resource_capacity_reserve_margin.csv"])
    if crm is not None:
        side = _read_csv(crm)
        side = side.rename(
            columns={c: "capres_" + c.rsplit("_", 1)[1] for c in side.columns if c.startswith("derating_factor_")}
        )
        side["resource"] = side["resource"].astype(str).str.strip()
        df["resource"] = df["resource"].astype(str).str.strip()
        df = df.merge(side, on="resource", how="left", suffixes=("", "_assigned"))
    return df


# ---------------------------------------------------------------------------
# time series


def _read_demand(path: Path):
    raw = _read_csv(path)
    if "time_index" in raw.columns:
        raw = raw[raw["time_index"].notna()].sort_values("time_index").reset_index(drop=True)
    zone_cols = {}
    for c in raw.columns:
        m = re.fullmatch(r"(?:demand|load)_mw_z(\d+)", c)
        if m:
            zone_cols[c] = int(m.group(1))
    if not zone_cols:
        raise ValueError(f"{path.name}: no Demand_MW_z<n> / Load_MW_z<n> columns")
    demand = raw[list(zone_cols)].rename(columns=zone_cols).astype(float)

    rep = int(_first(raw, "rep_periods", 1))
    steps = int(_first(raw, "timesteps_per_rep_period", len(demand)))
    if "sub_weights" in raw.columns:
        weights = [float(w) for w in raw["sub_weights"].dropna().iloc[:rep]]
    else:
        weights = []
    if len(weights) != rep:
        weights = [8760.0 / rep] * rep
    time = TimeStructure(rep, steps, weights)
    if time.T != len(demand):
        raise ValueError(
            f"{path.name}: {len(demand)} timesteps but Rep_Periods x Timesteps_per_Rep_Period = "
            f"{rep} x {steps} = {time.T}"
        )
    voll = float(_first(raw, "voll", np.nan))
    segments = int(raw["demand_segment"].notna().sum()) if "demand_segment" in raw.columns else 1
    return demand, voll, max(segments, 1), time


def _read_variability(path: Path, T: int) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [str(c).strip() for c in df.columns]
    tcol = next((c for c in df.columns if c.lower() == "time_index"), None)
    if tcol is not None:
        df = df[df[tcol].notna()].sort_values(tcol).drop(columns=[tcol])
    df = df.loc[:, [c for c in df.columns if not c.lower().startswith("unnamed:")]]
    if len(df) != T:
        raise ValueError(f"{path.name}: {len(df)} rows, expected {T}")
    return df.reset_index(drop=True).astype(float)


def _read_fuels(path: Path | None, T: int):
    if path is None:
        return {}, pd.DataFrame(index=range(T))
    df = pd.read_csv(path)
    df.columns = [str(c).strip() for c in df.columns]
    tcol = next((c for c in df.columns if c.lower() == "time_index"), None)
    if tcol is not None:
        df = df[df[tcol].notna()].sort_values(tcol)
        co2_row = df[df[tcol] == 0]
        prices = df[df[tcol] > 0].drop(columns=[tcol])
        co2_row = co2_row.drop(columns=[tcol])
    else:  # first data row is the CO2 content row
        co2_row, prices = df.iloc[:1], df.iloc[1:]
    if co2_row.empty:
        raise ValueError(f"{path.name}: missing the Time_Index = 0 row of CO2 contents")
    if len(prices) != T:
        raise ValueError(f"{path.name}: {len(prices)} price rows, expected {T}")
    co2 = {c: float(co2_row[c].iloc[0]) for c in co2_row.columns}
    return co2, prices.reset_index(drop=True).astype(float)


# ---------------------------------------------------------------------------
# network and policies


def _read_network(path: Path | None) -> pd.DataFrame | None:
    if path is None:
        return None
    df = _read_csv(path)
    key = "network_lines" if "network_lines" in df.columns else "line_max_flow_mw"
    if key not in df.columns:
        raise ValueError(f"{path.name}: neither Network_Lines nor Line_Max_Flow_MW present")
    lines = df[df[key].notna()].copy().reset_index(drop=True)
    if "network_lines" not in lines.columns:
        lines["network_lines"] = np.arange(1, len(lines) + 1)
    if {"start_zone", "end_zone"} <= set(lines.columns):
        lines["start_zone"] = lines["start_zone"].astype(int)
        lines["end_zone"] = lines["end_zone"].astype(int)
    else:
        # incidence form: one column per zone, +1 at the start zone, -1 at the end zone
        inc = {c: int(c[1:]) for c in lines.columns if re.fullmatch(r"z\d+", c)}
        if not inc:
            raise ValueError(f"{path.name}: no Start_Zone/End_Zone columns and no z<n> incidence columns")
        starts, ends = [], []
        for i, row in lines.iterrows():
            plus = [z for c, z in inc.items() if pd.notna(row[c]) and float(row[c]) > 0]
            minus = [z for c, z in inc.items() if pd.notna(row[c]) and float(row[c]) < 0]
            if len(plus) != 1 or len(minus) != 1:
                raise ValueError(f"{path.name}: line {row['network_lines']} must have one +1 and one -1 zone")
            starts.append(plus[0])
            ends.append(minus[0])
        lines["start_zone"], lines["end_zone"] = starts, ends
    lines["network_lines"] = lines["network_lines"].astype(int)
    return lines


def _zone_number(label) -> int:
    m = re.fullmatch(r"\s*z?(\d+)\s*", str(label), flags=re.IGNORECASE)
    if not m:
        raise ValueError(f"cannot read a zone number from {label!r}")
    return int(m.group(1))


def _read_policies(dirs) -> dict:
    out = {}
    for key, name in _POLICY_FILES.items():
        path = _find_any(dirs, [name])
        if path is None:
            continue
        df = _read_csv(path)
        if "network_zones" in df.columns:
            df = df[df["network_zones"].notna()].copy()
            df["zone"] = [_zone_number(z) for z in df["network_zones"]]
        out[key] = df
    return out


# ---------------------------------------------------------------------------
# entry point


def read_case(case_dir, *, stage: int | None = None, tdr: bool = False) -> GenXCase:
    """Read a GenX case folder.

    Parameters
    ----------
    case_dir
        The case folder (the one holding ``resources/`` or
        ``Generators_data.csv``; for a multi-stage case, the folder holding
        ``inputs/``).
    stage
        For a multi-stage case, the stage to import (``inputs/inputs_p<stage>``).
    tdr
        Read the time series (demand, variability, fuels) from the case's
        time-domain-reduction output folder (``TDR_results``) instead of the
        full-resolution inputs.
    """
    case_dir = Path(case_dir)
    root = _resolve_root(case_dir, stage)
    layout = detect_layout(root)

    sys_dirs = [root / "system", root] if layout == "modular" else [root]
    settings_path = _find_any(
        [root / "settings", root / "Settings", case_dir / "settings", case_dir / "Settings", root],
        ["genx_settings.yml", "genx_settings.yaml"],
    )
    settings = read_settings(settings_path)

    ts_dirs = sys_dirs
    if tdr:
        tdr_dir = None
        for base in (root, case_dir):
            tdr_dir = _find(base, "TDR_results") or _find(base, "TDR_Results")
            if tdr_dir is not None:
                break
        if tdr_dir is None:
            raise FileNotFoundError(f"tdr=True but no TDR_results folder in {root}")
        ts_dirs = [tdr_dir, tdr_dir / "system"]

    demand_path = _find_any(ts_dirs, ["Demand_data.csv", "Load_data.csv"])
    if demand_path is None:
        raise FileNotFoundError("no Demand_data.csv / Load_data.csv found")
    demand, voll, segments, time = _read_demand(demand_path)

    var_path = _find_any(ts_dirs, ["Generators_variability.csv"])
    if var_path is None:
        raise FileNotFoundError("no Generators_variability.csv found")
    variability = _read_variability(var_path, time.T)

    fuel_co2, fuel_prices = _read_fuels(_find_any(ts_dirs, ["Fuels_data.csv"]), time.T)

    resources = _read_resources(root, layout)
    resources["resource"] = resources["resource"].astype(str).str.strip()
    resources["zone"] = resources["zone"].astype(int)
    dup = resources["resource"][resources["resource"].duplicated()]
    if not dup.empty:
        raise ValueError(f"duplicate resource names: {sorted(set(dup))}")

    network = _read_network(_find_any(sys_dirs, ["Network.csv"]))
    policy_dirs = [root / "policies", root] if layout == "modular" else [root]
    policies = _read_policies(policy_dirs)

    return GenXCase(
        root=root,
        layout=layout,
        settings=settings,
        resources=resources,
        demand=demand,
        voll=voll,
        demand_segments=segments,
        time=time,
        variability=variability,
        fuel_co2=fuel_co2,
        fuel_prices=fuel_prices,
        network=network,
        policies=policies,
    )
