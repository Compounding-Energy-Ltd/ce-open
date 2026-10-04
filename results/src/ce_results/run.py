# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Open a CE run directory and read its tables and stamps.

A run directory holds result tables (Parquet canonical; Arrow IPC, CSV and
gzipped CSV accepted) and TOML documents: ``manifest.toml``, ``summary.toml``
and optionally ``results_manifest.toml``, ``qa_report.toml`` and
``validation.toml``. :class:`Run` loads tables lazily, renames legacy columns
to the CE data format 1.0 vocabulary, checks them against the column contracts
in :mod:`ce_results.schema` and exposes typed accessors.

Some derived metrics need facts that live in the *input bundle* rather than
the results (line endpoints, availability series, existing capacity). Pass the
bundle directory as ``bundle=`` to make them available; runs written by older
engine releases echo what is needed into the run directory itself.

Example::

    from ce_results import Run

    run = Run("path/to/run", bundle="path/to/bundle")
    run.stamps.gap
    run.builds(by=["technology", "year"])
    run.lmp(with_time=True)
"""

from __future__ import annotations

import tomllib
from functools import cached_property
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .schema import BUNDLE_SPECS, TABLE_SPECS, TIME_KEYS, SchemaError, coerce
from .stamps import Provenance, QAReport, SolveStamps, canonical_summary
from .timeaxis import TimeAxis

__all__ = ["Run", "TABLE_EXTS"]

# Search order for a table's file: the canonical format first.
TABLE_EXTS = (".parquet", ".arrow", ".csv.gz", ".csv")
_K = list(TIME_KEYS)


def _read_file(path: Path) -> pd.DataFrame:
    name = path.name.lower()
    if name.endswith(".parquet"):
        return pd.read_parquet(path)
    if name.endswith(".arrow"):
        import pyarrow.feather as feather
        import pyarrow.ipc as ipc

        try:
            return feather.read_table(path).to_pandas()
        except Exception:  # an IPC *stream* rather than an IPC file
            with open(path, "rb") as fh:
                return ipc.open_stream(fh).read_all().to_pandas()
    if name.endswith(".csv") or name.endswith(".csv.gz"):
        return pd.read_csv(path)
    raise ValueError(f"unsupported table format: {path.name}")


def _read_toml(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _scan(directory: Path) -> dict[str, Path]:
    """Table name -> file, preferring the canonical format when several exist."""
    found: dict[str, Path] = {}
    for p in directory.iterdir() if directory.is_dir() else ():
        if not p.is_file():
            continue
        for rank, ext in enumerate(TABLE_EXTS):
            if p.name.lower().endswith(ext):
                stem = p.name[: -len(ext)]
                if stem not in found or rank < TABLE_EXTS.index(_ext(found[stem])):
                    found[stem] = p
                break
    return found


def _ext(p: Path) -> str:
    return next(e for e in TABLE_EXTS if p.name.lower().endswith(e))


def _flatten(prefix: str, value: Any, out: list[tuple[str, float]]) -> None:
    if isinstance(value, Mapping):
        for k in sorted(value):
            _flatten(f"{prefix}.{k}" if prefix else str(k), value[k], out)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        out.append((prefix, float(value)))


class Run:
    """A CE run directory.

    Parameters
    ----------
    path:
        The run directory.
    bundle:
        Optional input-bundle directory. Supplies line endpoints, availability
        series, existing capacity and weather weights for derived metrics.
    validate:
        When true (the default) every known table is checked against its
        column contract on load and a :class:`~ce_results.schema.SchemaError`
        is raised if a required column is missing.
    """

    def __init__(self, path: str | Path, *, bundle: str | Path | None = None, validate: bool = True):
        self.path = Path(path)
        if not self.path.is_dir():
            raise FileNotFoundError(f"not a run directory: {self.path}")
        self.bundle_path = None if bundle is None else Path(bundle)
        if self.bundle_path is not None and not self.bundle_path.is_dir():
            raise FileNotFoundError(f"not a bundle directory: {self.bundle_path}")
        self.validate = validate
        self._cache: dict[str, pd.DataFrame] = {}
        self._bcache: dict[str, pd.DataFrame] = {}
        self.notes: list[str] = []
        """Plain-language notes on assumptions the reader made (e.g. equal weather weights)."""

    def _note(self, msg: str) -> None:
        if msg not in self.notes:
            self.notes.append(msg)

    def __repr__(self) -> str:
        return f"Run({str(self.path)!r}, tables={len(self.tables)})"

    # ------------------------------------------------------------------ documents

    @cached_property
    def manifest(self) -> dict[str, Any]:
        """``manifest.toml``: run identity, money, time axis and inputs."""
        return _read_toml(self.path / "manifest.toml") or {}

    @cached_property
    def summary(self) -> dict[str, Any]:
        """``summary.toml`` with legacy money keys renamed (``total_gbp`` -> ``total``)."""
        return canonical_summary(_read_toml(self.path / "summary.toml") or {})

    @cached_property
    def results_manifest(self) -> dict[str, Any]:
        """``results_manifest.toml``: the inventory of tables the run produced."""
        return _read_toml(self.path / "results_manifest.toml") or {}

    @cached_property
    def qa_report_document(self) -> dict[str, Any] | None:
        """``qa_report.toml`` as parsed TOML, or ``None`` when the run has no QA report."""
        return _read_toml(self.path / "qa_report.toml")

    @cached_property
    def bundle_descriptor(self) -> dict[str, Any] | None:
        """The input bundle's ``bundle.toml``, when a bundle was given."""
        return None if self.bundle_path is None else _read_toml(self.bundle_path / "bundle.toml")

    def document(self, name: str) -> dict[str, Any] | None:
        """Any TOML document in the run directory by base name, exactly as written."""
        return _read_toml(self.path / f"{name}.toml")

    # ------------------------------------------------------------------ stamps

    @cached_property
    def stamps(self) -> SolveStamps:
        """Objective, lower bound, gap, iterations and stop cause."""
        return SolveStamps.from_documents(self.summary, self.manifest)

    @cached_property
    def provenance(self) -> Provenance:
        """Format and engine version, money, time axis, inputs and register version."""
        return Provenance.from_documents(self.manifest, self.results_manifest, self.summary)

    @cached_property
    def qa(self) -> QAReport | None:
        """The run's QA report, or ``None`` when the run was produced without one."""
        checks = self.table("qa_checks") if self.has("qa_checks") else None
        return QAReport.from_documents(self.qa_report_document, self.summary, checks)

    # ------------------------------------------------------------------ tables

    @cached_property
    def _catalog(self) -> dict[str, dict[str, Any]]:
        return {str(t.get("name")): dict(t) for t in self.results_manifest.get("tables", []) or []}

    @cached_property
    def _files(self) -> dict[str, Path]:
        return _scan(self.path)

    @cached_property
    def _bundle_files(self) -> dict[str, Path]:
        return {} if self.bundle_path is None else _scan(self.bundle_path)

    @property
    def name(self) -> str:
        """The run's name (from the manifests, else the directory name)."""
        return self.provenance.run_name or self.path.name

    @property
    def tables(self) -> list[str]:
        """Names of the tables present in the run directory."""
        return sorted(self._files)

    @property
    def missing_tables(self) -> list[str]:
        """Tables the results manifest lists but whose files are absent."""
        return sorted(n for n in self._catalog if n not in self._files)

    def has(self, name: str) -> bool:
        return name in self._files

    def has_bundle_table(self, name: str) -> bool:
        return name in self._bundle_files

    def table_info(self, name: str) -> dict[str, Any]:
        """The results-manifest entry for ``name`` (family, grain, columns...), or ``{}``."""
        return dict(self._catalog.get(name, {}))

    def table(self, name: str, *, columns: Sequence[str] | None = None) -> pd.DataFrame:
        """Load a result table by name.

        Known tables have legacy columns renamed and are validated and coerced
        (see :mod:`ce_results.schema`); an ``x_<name>`` extension table is
        checked as ``<name>`` when that name is known; other tables are
        returned as read.
        ``columns`` restricts the columns returned (canonical names). The
        returned frame is a copy and may be modified freely.
        """
        if name not in self._files:
            raise KeyError(f"run {self.name!r} has no table {name!r} (has: {', '.join(self.tables)})")
        if name not in self._cache:
            df = _read_file(self._files[name])
            spec = TABLE_SPECS.get(name[2:] if name.startswith("x_") else name)
            if spec is not None:
                df = coerce(df, spec, strict=self.validate)
            self._cache[name] = df
        df = self._cache[name]
        return df[list(columns)].copy() if columns is not None else df.copy()

    def bundle_table(self, name: str) -> pd.DataFrame:
        """Load a table from the input bundle (``buses``, ``lines``, ``availability``...)."""
        if name not in self._bundle_files:
            where = "no bundle was given" if self.bundle_path is None else f"bundle has no {name!r} table"
            raise KeyError(f"{name!r} unavailable: {where}")
        if name not in self._bcache:
            df = _read_file(self._bundle_files[name])
            spec = BUNDLE_SPECS.get(name)
            if spec is not None:
                df = coerce(df, spec, strict=self.validate)
            self._bcache[name] = df
        return self._bcache[name].copy()

    def _require(self, name: str) -> pd.DataFrame:
        if not self.has(name):
            raise KeyError(f"run {self.name!r} has no {name!r} table")
        return self.table(name)

    # ------------------------------------------------------------------ time axis and weights

    @cached_property
    def time_axis(self) -> TimeAxis | None:
        """Blocks, period lengths, scenario probabilities and discount factors, or ``None``."""
        ax = TimeAxis.from_descriptor(self.manifest, "manifest")
        bdoc = self.bundle_descriptor or {}
        if ax is None and bdoc:
            ax = TimeAxis.from_descriptor(bdoc, "bundle")
        if ax is None:
            legacy = {n: self.table(n) for n in ("blocks", "block_periods", "scenarios", "years", "timeindex")
                      if self.has(n)}
            ax = TimeAxis.from_legacy_tables(legacy)
        if ax is None:
            return None
        if ax.scenarios is None:  # manifest / bundle without [scenarios]: try the other sources
            for doc in (self.manifest, bdoc):
                s = TimeAxis.scenarios_from_section(doc.get("scenarios"))
                if s is not None:
                    ax.scenarios = s
                    break
            else:
                if self.has("scenarios"):
                    ax.scenarios = self.table("scenarios")[["scenario", "probability"]]
        if not ax.discount:
            if self.has("years") and "discount" in self.table("years").columns:
                y = self.table("years")
                ax.discount = {int(a): float(b) for a, b in zip(y["year"], y["discount"])}
            elif bdoc:
                b = TimeAxis.from_descriptor(bdoc, "bundle")
                if b is not None:
                    ax.discount = b.discount
        if not ax.weather_years and bdoc:
            t = bdoc.get("time") or {}
            ax.weather_years = [int(y) for y in t.get("weather_years", []) or []]
        return ax

    @property
    def time_mode(self) -> str:
        """``"chronological"`` (every block is contiguous real time) or ``"representative"``."""
        ax = self.time_axis
        return "representative" if ax is None else ax.mode

    def _key_source(self) -> pd.DataFrame | None:
        """Distinct (year, scenario, weather) triples of the run, from the most complete table."""
        for name in ("lmp", "flows", "generation", "storage_state"):
            if self.has(name):
                return self.table(name, columns=["year", "scenario", "weather"]).drop_duplicates()
        return None

    def _weather_weights(self, keys: pd.DataFrame) -> np.ndarray:
        """Normalised weight of each row's weather draw within its investment year."""
        pairs = keys[["year", "weather"]].drop_duplicates()
        src = self._key_source()
        if src is not None:
            pairs = pd.concat([pairs, src[["year", "weather"]]]).drop_duplicates()
        table = None
        if self.has("weather_weights"):
            table = self.table("weather_weights")
        elif self.has_bundle_table("weather_weights"):
            table = self.bundle_table("weather_weights")
        if table is None:
            if (pairs.groupby("year")["weather"].nunique() > 1).any():
                self._note("no weather_weights table: weather draws weighted equally within each year")
            raw = pd.Series(1.0, index=pairs.index)
        else:
            ax = self.time_axis
            lookup = table[["year", "weather", "weight"]]
            if ax is not None and ax.weather_axis == "ordinal" and ax.weather_years:
                ordinal = {lab: i for i, lab in enumerate(ax.weather_years, start=1)}
                lookup = lookup.assign(weather=lookup["weather"].map(ordinal))
            raw = pairs.merge(lookup, on=["year", "weather"], how="left")["weight"].fillna(1.0)
            raw.index = pairs.index
        pairs = pairs.assign(raw=raw.to_numpy())
        pairs["w"] = pairs["raw"] / pairs.groupby("year")["raw"].transform("sum")
        return keys[["year", "weather"]].merge(pairs[["year", "weather", "w"]], on=["year", "weather"],
                                               how="left")["w"].to_numpy()

    def weights(self, keys: pd.DataFrame | None = None) -> pd.DataFrame:
        """Annual hours represented by each timestep.

        Returns the distinct time keys of ``keys`` (default: every timestep of
        the run, from the LMP table) with three added columns:

        ``dt_h``
            the physical length of the modelled period;
        ``annual_h``
            hours of the year it stands for, *undiscounted* and
            probability-weighted:
            ``P(scenario) * w(weather) * weight_h / span_h * dt_h``.
            Summed over one year of a complete run this is the year's hours.
        ``discount``
            the year's discount factor when the run or bundle reports one (else NaN).

        Annual energy is ``sum(mw * annual_h)``; an annual average price is
        ``sum(price * annual_h) / sum(annual_h)``.
        """
        if keys is None:
            src = next((n for n in ("lmp", "flows", "generation") if self.has(n)), None)
            if src is None:
                raise KeyError("run has no per-timestep table to take timestep keys from")
            keys = self.table(src, columns=_K)
        out = keys[_K].drop_duplicates().reset_index(drop=True)
        ax = self.time_axis

        if ax is None or ax.periods.empty:
            self._note("no time axis: every period taken as 1 h representing 1 h")
            out["dt_h"] = 1.0
            scale = np.ones(len(out))
        else:
            per = ax.periods[["block", "period", "dt_h"]]
            dt = out[["block", "period"]].merge(per, on=["block", "period"], how="left")["dt_h"]
            if dt.isna().any():
                raise SchemaError("the time axis does not cover every (block, period) in the run")
            out["dt_h"] = dt.to_numpy()
            scale = out["block"].map(ax.block_scale()).to_numpy(dtype=float)

        if ax is not None and ax.scenarios is not None:
            prob = out[["scenario"]].merge(ax.scenarios[["scenario", "probability"]], on="scenario",
                                           how="left")["probability"]
            if prob.isna().any():
                raise SchemaError("the scenario set does not cover every scenario in the run")
            prob = prob.to_numpy()
        else:
            src = self._key_source()
            n = (src if src is not None else out)["scenario"].nunique()
            if n > 1:
                self._note("no scenario probabilities: scenarios weighted equally")
            prob = np.full(len(out), 1.0 / max(n, 1))

        out["annual_h"] = prob * self._weather_weights(out) * scale * out["dt_h"].to_numpy()
        disc = {} if ax is None else ax.discount
        out["discount"] = out["year"].map(disc).astype(float) if disc else np.nan
        return out

    def _weighted(self, df: pd.DataFrame) -> pd.DataFrame:
        """``df`` with ``dt_h``, ``annual_h`` and ``discount`` joined on the time keys."""
        return df.merge(self.weights(df[_K]), on=_K, how="left")

    def with_time(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add ``period_start`` and ``period_end`` (the period's interval) on (year, block, period).

        Periods are hour-ending: a value labelled by its end time belongs to
        ``period_end``. Representative blocks have no wall-clock time and get
        ``NaT``.
        """
        ax = self.time_axis
        if ax is None:
            raise KeyError(f"run {self.name!r} has no time axis")
        times = ax.period_times(df)
        out = df.reset_index(drop=True).copy()
        out["period_start"] = times["period_start"].array  # .array keeps a time zone, .to_numpy() would not
        out["period_end"] = times["period_end"].array
        return out

    # ------------------------------------------------------------------ typed accessors

    def builds(self, *, by: Sequence[str] | None = None, component: str | Sequence[str] | None = None,
               incremental: bool = False) -> pd.DataFrame:
        """Capacity built, in MW, by bus / technology / year.

        ``built_mw`` is *cumulative*: the new-build capacity in service in that
        year. ``incremental=True`` returns year-on-year additions instead.
        Retirements are rows with ``component == "retirement"`` and negative
        ``built_mw``. Storage energy (MWh) is reported by :meth:`storage_energy`.

        ``by`` aggregates (sums) ``built_mw`` over the named columns, e.g.
        ``by=["bus", "technology", "year"]``.
        """
        inv = self._require("investment")
        if "built_mw" not in inv.columns:
            raise SchemaError("investment table has no built_mw column")
        inv = inv[inv["component"] != "storage_energy"]
        if component is not None:
            comps = {component} if isinstance(component, str) else set(component)
            inv = inv[inv["component"].isin(comps)]
        inv = inv.drop(columns=[c for c in ("built_mwh",) if c in inv.columns])
        return self._shape(inv, "built_mw", by, incremental)

    def storage_energy(self, *, by: Sequence[str] | None = None, incremental: bool = False) -> pd.DataFrame:
        """Storage energy capacity built (MWh, cumulative) where energy is sized separately from power."""
        inv = self._require("investment")
        inv = inv[inv["component"] == "storage_energy"]
        if "built_mwh" not in inv.columns:
            inv = inv.assign(built_mwh=pd.Series(dtype=float))
        inv = inv.drop(columns=[c for c in ("built_mw",) if c in inv.columns])
        return self._shape(inv, "built_mwh", by, incremental)

    def _shape(self, inv: pd.DataFrame, value: str, by, incremental: bool) -> pd.DataFrame:
        if incremental:
            inv = self._incremental(inv, value)
        if by:
            by = list(by)
            inv = inv.groupby(by, as_index=False, dropna=False)[value].sum()
            return inv.sort_values(by).reset_index(drop=True)
        return inv.sort_values(["year", "component", "name"]).reset_index(drop=True)

    def _incremental(self, inv: pd.DataFrame, value: str) -> pd.DataFrame:
        ax = self.time_axis
        years = (ax.investment_years if ax is not None and ax.investment_years else
                 self.provenance.investment_years or sorted(inv["year"].unique()))
        years = sorted(set(years) | set(int(y) for y in inv["year"].unique()))
        ident = [c for c in ("component", "name", "technology", "bus") if c in inv.columns]
        if inv.empty:
            return inv
        wide = inv.pivot_table(index=ident, columns="year", values=value, aggfunc="sum")
        wide = wide.reindex(columns=years).fillna(0.0)
        inc = wide.diff(axis=1)
        inc[years[0]] = wide[years[0]]
        out = inc.stack().rename(value).reset_index()
        out = out[out[value].abs() > 1e-9]
        if "bus_label" in inv.columns:
            labels = inv[ident + ["bus_label"]].drop_duplicates(subset=ident)
            out = out.merge(labels, on=ident, how="left")
        return out.reset_index(drop=True)

    def dispatch(self, source_type: str | Sequence[str] | None = None, *,
                 with_time: bool = False, with_weights: bool = False) -> pd.DataFrame:
        """Per-timestep dispatch by unit.

        ``source_type`` filters on ``"thermal"``, ``"renewable"``,
        ``"storage_discharge"`` or ``"storage_charge"``. Every MW value is
        non-negative; charging is a ``storage_charge`` row. Rows with zero
        dispatch may be omitted: treat an absent row as 0 MW.
        """
        g = self._require("generation")
        if source_type is not None:
            st = {source_type} if isinstance(source_type, str) else set(source_type)
            g = g[g["source_type"].isin(st)].reset_index(drop=True)
        if with_weights:
            g = self._weighted(g)
        return self.with_time(g) if with_time else g

    def storage(self, *, with_time: bool = False) -> pd.DataFrame:
        """Storage state of charge (end of period), charge and discharge per timestep."""
        s = self._require("storage_state")
        return self.with_time(s) if with_time else s

    def lmp(self, *, with_time: bool = False, with_weights: bool = False) -> pd.DataFrame:
        """Nodal prices per timestep (``lmp_per_mwh``, currency/MWh, undiscounted).

        Where the run reports the attribution,
        ``lmp_per_mwh = lmp_energy + lmp_congestion + lmp_loss`` exactly.
        """
        p = self._require("lmp")
        if with_weights:
            p = self._weighted(p)
        return self.with_time(p) if with_time else p

    def lmp_component_residual(self) -> float:
        """Largest |price - (energy + congestion + loss)| over the LMP table (0 without components)."""
        p = self._require("lmp")
        if not {"lmp_energy", "lmp_congestion", "lmp_loss"} <= set(p.columns):
            return 0.0
        r = p["lmp_per_mwh"] - (p["lmp_energy"] + p["lmp_congestion"] + p["lmp_loss"])
        return float(r.abs().max()) if len(r) else 0.0

    def network(self) -> pd.DataFrame:
        """Line endpoints (bus names) and ratings: ``line, from_bus, to_bus, rating_mw``.

        From the run's ``network`` table (older runs) or the bundle's ``lines``
        and ``buses`` tables.
        """
        if self.has("network"):
            return self.table("network")
        if self.has_bundle_table("lines") and self.has_bundle_table("buses"):
            lines, buses = self.bundle_table("lines"), self.bundle_table("buses")
            name = buses.set_index("id")["name"]
            out = pd.DataFrame({"line": lines["name"], "from_bus": lines["from_bus"].map(name),
                                "to_bus": lines["to_bus"].map(name), "rating_mw": lines["rating_mw"]})
            if out[["from_bus", "to_bus"]].isna().any().any():
                raise SchemaError("bundle lines reference bus ids absent from buses")
            if "label" in lines.columns:
                out["line_label"] = lines["label"]
            return out
        raise KeyError("line endpoints unavailable: the run has no network table and no bundle "
                       "lines/buses were given")

    def flows(self, *, with_rating: bool = True, with_time: bool = False,
              with_weights: bool = False) -> pd.DataFrame:
        """Line flows per timestep; ``with_rating`` adds endpoints, ``rating_mw`` and
        ``loading`` (|flow| / rating) when the network is known."""
        f = self._require("flows")
        if with_rating:
            try:
                net = self.network()[["line", "from_bus", "to_bus", "rating_mw"]]
            except KeyError:
                net = None
            if net is not None:
                f = f.merge(net, on="line", how="left")
                f["loading"] = f["flow_mw"].abs() / f["rating_mw"].where(f["rating_mw"] > 0)
        if with_weights:
            f = self._weighted(f)
        return self.with_time(f) if with_time else f

    def availability(self) -> pd.DataFrame:
        """Availability series: ``site_name, scenario, block, period, cf`` (+ ``year``, ``weather``).

        From the run's echo (older runs) or the bundle's ``availability`` table.
        """
        if self.has("availability_profile"):
            return self.table("availability_profile")
        if self.has_bundle_table("availability"):
            return self.bundle_table("availability")
        raise KeyError("availability unavailable: no availability_profile table and no bundle given")

    def demand(self) -> pd.DataFrame:
        """Input demand: ``bus`` (name), ``scenario``, ``block``, ``period``, ``demand_mw``
        (+ ``year``, ``weather`` when the bundle gives per-year profiles).

        From the run's echo (older runs) or the bundle's ``demand`` and ``buses`` tables.
        """
        if self.has("demand_profile"):
            return self.table("demand_profile")
        if self.has_bundle_table("demand") and self.has_bundle_table("buses"):
            d = self.bundle_table("demand")
            names = self.bundle_table("buses").set_index("id")["name"]
            d["bus"] = d["bus"].map(names)
            if d["bus"].isna().any():
                raise SchemaError("bundle demand references bus ids absent from buses")
            return d.rename(columns={"mw": "demand_mw"})
        raise KeyError("demand unavailable: no demand_profile table and no bundle demand/buses given")

    def _existing_renewables(self) -> dict[str, float]:
        if not self.has_bundle_table("generators"):
            return {}
        g = self.bundle_table("generators")
        g = g[g["kind"] == "renewable"]
        return dict(zip(g["name"], g["existing_mw"].astype(float)))

    def curtailment(self, existing_mw: Mapping[str, float] | None = None) -> pd.DataFrame:
        """Curtailment per availability-bounded site per timestep.

        If the run wrote a ``curtailment`` table it is returned as is.
        Otherwise curtailment is *derived*::

            available_mw = cf * capacity_mw
            curtailed_mw = max(available_mw - dispatch_mw, 0)

        ``capacity_mw`` is the site's cumulative new build in that year plus its
        existing capacity: from ``existing_mw[site]`` when given, else from the
        bundle's ``generators`` table, else zero. Sites with no capacity in
        service and no dispatch in a year are left out. A ``capacity_short``
        flag marks timesteps where dispatch exceeded the derived availability --
        the sign that existing capacity is missing.
        """
        if self.has("curtailment"):
            return self.table("curtailment")
        av = self.availability()
        existing = self._existing_renewables()
        existing.update(existing_mw or {})
        if not existing:
            self._note("curtailment derived from new-build capacity only (no existing capacity known)")
        keys = self._key_source()
        if keys is None:
            raise KeyError("run has no per-timestep table to take years and weather draws from")
        years = sorted(keys["year"].unique())

        inv = self.builds(component="renewable") if self.has("investment") else pd.DataFrame(
            columns=["year", "name", "built_mw", "bus", "technology"])
        sites = sorted(av["site_name"].unique())
        cap = pd.DataFrame([(y, s) for y in years for s in sites], columns=["year", "site_name"])
        built = inv.groupby(["year", "name"])["built_mw"].sum().rename("built")
        cap = cap.merge(built, left_on=["year", "site_name"], right_index=True, how="left")
        cap["capacity_mw"] = cap["built"].fillna(0.0) + cap["site_name"].map(existing).fillna(0.0)
        cap = cap.drop(columns="built")

        meta = [pd.DataFrame(columns=["site_name", "bus", "technology"])]
        if len(inv):
            meta.append(inv.rename(columns={"name": "site_name"})[["site_name", "bus", "technology"]])
        gen = self.dispatch("renewable") if self.has("generation") else None
        if gen is not None and len(gen):
            meta.append(gen.rename(columns={"component_name": "site_name"})[["site_name", "bus", "technology"]])
        meta = pd.concat(meta).drop_duplicates(subset="site_name")

        grid = self._broadcast_availability(av, keys)
        grid = grid.merge(cap, on=["year", "site_name"], how="left")
        grid["available_mw"] = grid["cf"] * grid["capacity_mw"]
        if gen is not None and len(gen):
            d = (gen.groupby(_K + ["component_name"], as_index=False)["dispatch_mw"].sum()
                 .rename(columns={"component_name": "site_name"}))
            grid = grid.merge(d, on=_K + ["site_name"], how="left")
        else:
            grid["dispatch_mw"] = 0.0
        grid["dispatch_mw"] = grid["dispatch_mw"].fillna(0.0)
        grid["curtailed_mw"] = (grid["available_mw"] - grid["dispatch_mw"]).clip(lower=0.0)
        grid["capacity_short"] = grid["dispatch_mw"] > grid["available_mw"] + 1e-6
        # a site with nothing in service and nothing dispatched has nothing to curtail
        grid = grid[(grid["capacity_mw"] > 0) | (grid["dispatch_mw"] > 0)]
        grid = grid.merge(meta, on="site_name", how="left")
        cols = _K + ["site_name", "technology", "bus", "capacity_mw", "cf", "available_mw", "dispatch_mw",
                     "curtailed_mw", "capacity_short"]
        return grid[cols].sort_values(_K + ["site_name"]).reset_index(drop=True)

    def _broadcast_availability(self, av: pd.DataFrame, keys: pd.DataFrame) -> pd.DataFrame:
        """Expand availability rows to every (year, weather) of the run.

        Rows without ``year`` apply to every year; rows without ``weather``
        apply to every weather draw. A row naming a specific year / weather
        overrides a general one for the same site and period.
        """
        base = ["site_name", "scenario", "block", "period"]
        av = av.copy()
        for c in ("year", "weather"):
            if c not in av.columns:
                av[c] = pd.NA
        ax = self.time_axis
        if ax is not None and ax.weather_axis == "ordinal" and ax.weather_years:
            ordinal = {lab: i for i, lab in enumerate(ax.weather_years, start=1)}
            av["weather"] = av["weather"].map(lambda w: pd.NA if pd.isna(w) else ordinal.get(int(w), pd.NA))
        av["_rank"] = av["year"].notna().astype(int) + av["weather"].notna().astype(int)
        parts = []
        for (has_y, has_w), sub in av.groupby([av["year"].notna(), av["weather"].notna()]):
            on = (["year"] if has_y else []) + (["weather"] if has_w else [])
            sub = sub.drop(columns=[c for c in ("year", "weather") if c not in on])
            if on:
                sub = sub.astype({c: "int64" for c in on})
                parts.append(keys.merge(sub, on=["scenario"] + on, how="inner"))
            else:
                parts.append(keys.merge(sub, on="scenario", how="inner"))
        grid = pd.concat(parts, ignore_index=True)
        grid = grid.sort_values("_rank").drop_duplicates(subset=_K + ["site_name"], keep="last")
        return grid.drop(columns="_rank")[_K + ["site_name", "cf"]]

    def convergence(self) -> pd.DataFrame:
        """Lower bound, upper bound and gap per outer iteration.

        Read from ``convergence`` (older runs) or the ``x_convergence`` extension table.
        """
        return self._require("convergence" if self.has("convergence") else "x_convergence")

    def cost_ledger(self) -> pd.DataFrame:
        """Every numeric cost line the run reports, long format.

        Columns: ``section`` (``cost``, ``cost_decomposition`` or
        ``qa_cost_ledger``), ``item`` (dotted for nested entries) and
        ``value``. Item names use the 1.0 spelling (legacy currency suffixes
        removed). Amounts are in the run's currency (``provenance.currency``);
        ``cost`` lines are present values over the horizon.
        """
        rows: list[tuple[str, str, float]] = []
        for section in ("cost", "cost_decomposition"):
            items: list[tuple[str, float]] = []
            _flatten("", self.summary.get(section, {}) or {}, items)
            rows += [(section, k, v) for k, v in items]
        qdoc = self.qa_report_document or {}
        qa = canonical_summary({"cost": qdoc.get("cost_ledger") or qdoc.get("x_cost_ledger") or {}})
        items = []
        _flatten("", qa["cost"], items)
        rows += [("qa_cost_ledger", k, v) for k, v in items]
        return pd.DataFrame(rows, columns=["section", "item", "value"])

    # ------------------------------------------------------------------ xarray

    def to_xarray(self, table: str | pd.DataFrame, value: str, dims: Sequence[str] | None = None):
        """A table column as an :class:`xarray.DataArray` indexed by ``dims``.

        ``dims`` defaults to the table's key columns (e.g. year, scenario,
        weather, block, period, bus for ``lmp``). Requires ``xarray``.
        """
        try:
            import xarray  # noqa: F401
        except ImportError as exc:  # pragma: no cover - exercised only without xarray
            raise ImportError("to_xarray needs the optional dependency xarray "
                              "(pip install 'ce-results[xarray]')") from exc
        if isinstance(table, str):
            df = self.table(table)
            dims = list(dims or TABLE_SPECS[table].keys)
        else:
            df = table
            if dims is None:
                raise ValueError("dims is required when passing a DataFrame")
            dims = list(dims)
        series = df.set_index(dims)[value]
        if series.index.has_duplicates:
            raise ValueError(f"dims {dims} do not identify rows of the table uniquely")
        return series.to_xarray()
