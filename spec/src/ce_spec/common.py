# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Checks shared by bundle and results validation: format identity, the time/scenario axis, the directory
inventory and the provenance sidecars."""

from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

from .documents import get_path
from .issues import Collector
from .prov import read_prov, sha256_file, valid_utc_instant
from .schema import SPEC_MAJOR, SPEC_MINOR, SPEC_VERSION
from .tables import rows_of, split_table_name

_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
DOC_FILES = ("README", "LICENSE", "NOTICE", "CHANGELOG")
FULL_YEAR_HOURS = (8760.0, 8784.0)


def check_format(doc: dict, expected: str, ctx: Collector, file: str) -> bool:
    """Format name and version gate. False means stop: the rest of the directory cannot be interpreted."""
    name = get_path(doc, "format.name")
    version = get_path(doc, "format.version")
    if name != expected:
        ctx.add("bad_format_name", f"`[format] name` must be {expected!r}, got {name!r}", file=file,
                column="format.name")
        return False
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        ctx.add("bad_version", f"`[format] version` must be MAJOR.MINOR, got {version!r}", file=file,
                column="format.version")
        return False
    major, minor = (int(x) for x in version.split("."))
    if major != SPEC_MAJOR:
        ctx.add("unsupported_major_version", f"version {version} is not readable by a {SPEC_VERSION} validator",
                file=file, column="format.version")
        return False
    if minor > SPEC_MINOR:
        ctx.add("newer_minor_version", f"version {version} is newer than this validator ({SPEC_VERSION})",
                file=file, column="format.version")
        return not ctx.has_error("newer_minor_version")
    return True


def check_money(doc: dict, ctx: Collector, file: str) -> None:
    cur = get_path(doc, "money.currency")
    if isinstance(cur, str) and not _CURRENCY_RE.match(cur):
        ctx.add("bad_currency", f"currency {cur!r} is not an ISO 4217 code", file=file, column="money.currency")


@dataclass
class Axis:
    """The declared time and scenario axis."""

    years: list[int] = field(default_factory=list)
    weather_years: list[int] | None = None
    n_scen: int = 1
    blocks: dict[int, int] = field(default_factory=dict)      # block id -> nperiods
    weather_axis: str = "label"

    def weather_values(self) -> list[int] | None:
        if self.weather_years is None:
            return None
        if self.weather_axis == "ordinal":
            return list(range(1, len(self.weather_years) + 1))
        return list(self.weather_years)


def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def parse_axis(doc: dict, ctx: Collector, file: str, *, blocks_required: bool) -> Axis:
    """Read and cross-check ``[time]`` and ``[scenarios]``. Type errors were already reported by the
    document check; values of the wrong type are skipped here."""
    ax = Axis()
    years = get_path(doc, "time.investment_years", [])
    if isinstance(years, list) and all(isinstance(y, int) for y in years):
        ax.years = list(years)
        if any(b <= a for a, b in zip(years, years[1:])):
            ctx.add("bad_axis", "`time.investment_years` must be strictly increasing", file=file,
                    column="time.investment_years")
    wy = get_path(doc, "time.weather_years")
    if isinstance(wy, list) and all(isinstance(y, int) for y in wy):
        ax.weather_years = list(wy)
        if len(set(wy)) != len(wy):
            ctx.add("bad_axis", "`time.weather_years` has duplicates", file=file, column="time.weather_years")
    wa = get_path(doc, "time.weather_axis", "label")
    ax.weather_axis = wa if wa in ("label", "ordinal") else "label"
    leap_day = get_path(doc, "time.leap_day", "keep")

    blocks = get_path(doc, "time.blocks")
    if isinstance(blocks, list):
        total_w = 0.0
        for i, b in enumerate(blocks):
            if not isinstance(b, dict):
                continue
            where = f"time.blocks[{i}]"
            bid, n = b.get("id"), b.get("nperiods")
            if isinstance(bid, int) and bid != i + 1:
                ctx.add("bad_axis", f"block ids must be 1..B in order; {where} has id {bid}", file=file, column=where)
            if not (isinstance(bid, int) and isinstance(n, int) and n >= 1):
                continue
            ax.blocks[bid] = n
            dt = b.get("dt_h")
            if isinstance(dt, list):
                if len(dt) != n:
                    ctx.add("bad_axis", f"{where}.dt_h has {len(dt)} values for {n} periods", file=file,
                            column=f"{where}.dt_h")
                dts = [float(x) for x in dt if isinstance(x, (int, float)) and not isinstance(x, bool)]
            elif isinstance(dt, (int, float)) and not isinstance(dt, bool):
                dts = [float(dt)] * n
            else:
                dts = []
            if any(x <= 0 for x in dts):
                ctx.add("out_of_range", f"{where}.dt_h must be positive", file=file, column=f"{where}.dt_h")
            span = sum(dts)
            w = b.get("weight_h")
            if isinstance(w, (int, float)) and not isinstance(w, bool):
                total_w += float(w)
            if b.get("kind") == "chronological":
                start = b.get("start")
                if start is None:
                    ctx.add("missing_value", f"{where} is chronological and needs `start`", file=file,
                            column=f"{where}.start")
                if isinstance(w, (int, float)) and dts and not math.isclose(float(w), span, rel_tol=1e-9, abs_tol=1e-9):
                    ctx.add("bad_axis", f"{where} is chronological: weight_h ({w}) must equal Σ dt_h ({span:g})",
                            file=file, column=f"{where}.weight_h")
                if isinstance(start, _dt.datetime) and any(math.isclose(span, h) for h in FULL_YEAR_HOURS):
                    s = start.astimezone(_dt.timezone.utc)
                    if (s.month, s.day, s.hour, s.minute) == (1, 1, 0, 0):
                        expected = 8784.0 if (_is_leap(s.year) and leap_day == "keep") else 8760.0
                        if not math.isclose(span, expected):
                            ctx.add("leap_day_mismatch", f"{where} spans {span:g} h from {s.date()} but "
                                    f"leap_day = {leap_day!r} makes that year {expected:g} h", file=file,
                                    column=where)
        if ax.blocks and not any(math.isclose(total_w, h, rel_tol=1e-9) for h in FULL_YEAR_HOURS):
            ctx.add("block_weights_not_full_year", f"block weights add up to {total_w:g} h", file=file,
                    column="time.blocks")
    elif blocks_required:
        pass   # missing_key already reported by the document check

    scen = doc.get("scenarios")
    if isinstance(scen, dict):
        names, probs = scen.get("names"), scen.get("probability")
        if isinstance(names, list) and isinstance(probs, list):
            if len(names) != len(probs):
                ctx.add("bad_axis", f"{len(names)} scenario names but {len(probs)} probabilities", file=file,
                        column="scenarios")
            if len(set(names)) != len(names):
                ctx.add("bad_axis", "scenario names must be unique", file=file, column="scenarios.names")
            nums = [p for p in probs if isinstance(p, (int, float)) and not isinstance(p, bool)]
            if any(p < 0 for p in nums):
                ctx.add("out_of_range", "scenario probabilities must be non-negative", file=file,
                        column="scenarios.probability")
            if nums and not math.isclose(sum(nums), 1.0, abs_tol=1e-9):
                ctx.add("probabilities_not_normalised", f"probabilities sum to {sum(nums)!r}", file=file,
                        column="scenarios.probability")
            ax.n_scen = len(names)
    df = get_path(doc, "system.discount_factors")
    if isinstance(df, list) and ax.years and len(df) != len(ax.years):
        ctx.add("bad_axis", f"{len(df)} discount factors for {len(ax.years)} investment years", file=file,
                column="system.discount_factors")
    return ax


def check_series_axis(t: pa.Table, ax: Axis, ctx: Collector, table: str, file: str, *,
                      weather_needs_year: bool) -> None:
    """Axis membership of the series columns: scenario, block, period, year, weather."""
    cols = t.column_names
    if weather_needs_year and "weather" in cols and "year" not in cols:
        ctx.add("weather_without_year", "a `weather` column needs a `year` column", file=file, table=table,
                column="weather")
    if "scenario" in cols:
        rows, n = rows_of(pc.greater(t.column("scenario"), ax.n_scen))
        if n:
            ctx.add("axis_out_of_range", f"{n} row(s) name a scenario beyond the {ax.n_scen} declared", file=file,
                    table=table, column="scenario", rows=rows, count=n)
    if "block" in cols and ax.blocks:
        blk = t.column("block")
        rows, n = rows_of(pc.invert(pc.is_in(blk, value_set=pa.array(sorted(ax.blocks), type=blk.type))))
        if n:
            ctx.add("axis_out_of_range", f"{n} row(s) name an undeclared block", file=file, table=table,
                    column="block", rows=rows, count=n)
        if "period" in cols:
            lookup = [0] * (max(ax.blocks) + 1)
            for b, np_ in ax.blocks.items():
                lookup[b] = np_
            safe = pc.if_else(pc.and_(pc.greater_equal(blk, 0), pc.less_equal(blk, max(ax.blocks))), blk, 0)
            limit = pc.take(pa.array(lookup, type=pa.int64()), pc.cast(safe, pa.int64()))
            bad = pc.and_(pc.greater(limit, 0), pc.greater(pc.cast(t.column("period"), pa.int64()), limit))
            rows, n = rows_of(bad)
            if n:
                ctx.add("axis_out_of_range", f"{n} row(s) have a period beyond their block's nperiods", file=file,
                        table=table, column="period", rows=rows, count=n)
    if "year" in cols and ax.years:
        yr = t.column("year")
        rows, n = rows_of(pc.invert(pc.is_in(yr, value_set=pa.array(ax.years, type=yr.type))))
        if n:
            ctx.add("year_not_in_horizon", f"{n} row(s) carry a year that is not an investment year {ax.years}",
                    file=file, table=table, column="year", rows=rows, count=n)
    if "weather" in cols:
        allowed = ax.weather_values()
        w = t.column("weather")
        if allowed is None:
            if t.num_rows:
                ctx.add("weather_not_declared", "a `weather` column needs `[time] weather_years` to be declared",
                        file=file, table=table, column="weather")
        else:
            rows, n = rows_of(pc.invert(pc.is_in(w, value_set=pa.array(allowed, type=w.type))))
            if n:
                ctx.add("weather_not_declared", f"{n} row(s) carry a weather value outside {allowed} "
                        f"({ax.weather_axis})", file=file, table=table, column="weather", rows=rows, count=n)


@dataclass
class Inventory:
    tables: dict[str, Path] = field(default_factory=dict)
    documents: dict[str, Path] = field(default_factory=dict)
    sidecars: list[Path] = field(default_factory=list)


def take_inventory(root: Path, table_names: set[str], doc_names: set[str], ctx: Collector, *,
                   artefact_exts: tuple[str, ...] = ()) -> Inventory:
    inv = Inventory()
    seen: dict[str, list[Path]] = {}
    for p in sorted(root.iterdir()):
        name = p.name
        if name.startswith("."):
            continue
        if p.is_dir():
            if not name.startswith("x_"):
                ctx.add("unknown_file", f"directory {name!r} is not part of the format (use an `x_` prefix)",
                        file=name)
            continue
        if name in doc_names:
            inv.documents[name] = p
            continue
        if name.endswith(".prov"):
            inv.sidecars.append(p)
            continue
        low = name.lower()
        if low.endswith(".json") or low.endswith(".geojson") or low.endswith(".json.gz"):
            ctx.add("json_forbidden", "JSON is not a CE data format (use Parquet, Arrow, CSV or TOML)", file=name)
            continue
        if any(name.upper().startswith(d) for d in DOC_FILES):
            continue
        split = split_table_name(name)
        if split is not None:
            stem, _ = split
            if stem in table_names:
                seen.setdefault(stem, []).append(p)
            elif not stem.startswith("x_"):
                ctx.add("unknown_table", f"table {stem!r} is not defined by the specification", file=name)
            continue
        if name.startswith("x_") or any(low.endswith(e) for e in artefact_exts):
            continue
        ctx.add("unknown_file", f"{name!r} is not a table, document, sidecar or documentation file", file=name)
    for stem, paths in seen.items():
        if len(paths) > 1:
            ctx.add("ambiguous_table", f"table {stem!r} is present as {', '.join(q.name for q in paths)}",
                    file=paths[0].name, table=stem)
        inv.tables[stem] = paths[0]
    return inv


def check_sidecars(root: Path, sidecars: list[Path], nrows: dict[str, int], ctx: Collector) -> None:
    """Grammar and content checks of every ``.prov`` sidecar. ``nrows`` maps a described FILE name to the
    data-row count of the table read from it."""
    for sc in sidecars:
        target_name = sc.name[: -len(".prov")]
        target = root / target_name
        prov = read_prov(sc)
        for lineno in prov.unparsed:
            ctx.add("prov_unparsed_line", f"line {lineno} is not `key=value`", file=sc.name, rows=[lineno])
        for lineno, key in prov.duplicates:
            ctx.add("prov_duplicate_key", f"key {key!r} repeated on line {lineno}; the first value wins",
                    file=sc.name, rows=[lineno])
        if not target.is_file():
            ctx.add("prov_orphan", f"{target_name!r} does not exist", file=sc.name)
            continue
        ts = prov.get("built_utc")
        if ts is not None and not valid_utc_instant(ts):
            ctx.add("prov_bad_timestamp", f"built_utc {ts!r} is not an ISO 8601 UTC instant ending in Z",
                    file=sc.name)
        digest = prov.get("sha256")
        if digest is not None and digest.lower() != sha256_file(target):
            ctx.add("prov_hash_mismatch", f"sha256 does not match {target_name!r}", file=sc.name)
        rows = prov.get("rows")
        if rows is not None and target_name in nrows:
            if not rows.strip().isdigit() or int(rows) != nrows[target_name]:
                ctx.add("prov_rows_mismatch", f"rows={rows!r} but {target_name!r} has {nrows[target_name]} data rows",
                        file=sc.name)
        src_gen = prov.get("source_generation")
        src_name = prov.get("derived_from")
        if src_gen is not None and src_name is not None:
            src_prov = read_prov(root / (src_name + ".prov"))
            gen = src_prov.get("generation")
            if gen is not None and gen != src_gen:
                ctx.add("stale_derived_table", f"{target_name!r} was cut from generation {src_gen!r} of "
                        f"{src_name!r}, which now records generation {gen!r}", file=sc.name)
