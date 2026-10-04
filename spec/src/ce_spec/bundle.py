# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Validate a CE bundle directory (the inputs a model reads) against the specification."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

from .common import Axis, check_format, check_money, check_series_axis, check_sidecars, parse_axis, take_inventory
from .documents import get_path, load_document, validate_document
from .issues import Collector, Report
from .schema import doc_schema, table_schemas
from .tables import MAX_ROWS_REPORTED, check_columns, check_keys, check_reference, normalize, read_raw, rows_of


@dataclass
class LoadedBundle:
    """What a validation pass read: the descriptor, the axis and every table normalised to its canonical
    columns and types (legacy spellings renamed, defaults filled)."""

    path: Path
    descriptor: dict | None = None
    axis: Axis | None = None
    tables: dict[str, pa.Table] = field(default_factory=dict)


def _col(t: pa.Table | None, name: str):
    return t.column(name) if t is not None and name in t.column_names else None


def _check_lines(t: pa.Table, ctx: Collector, table: str, file: str) -> None:
    f, to = _col(t, "from_bus"), _col(t, "to_bus")
    if f is not None and to is not None:
        rows, n = rows_of(pc.equal(f, to))
        if n:
            ctx.add("self_loop", f"{n} branch(es) start and end at the same bus", file=file, table=table,
                    column="to_bus", rows=rows, count=n)
    ex, cand = _col(t, "existing"), _col(t, "candidate")
    if ex is not None and cand is not None:
        rows, n = rows_of(pc.and_(pc.invert(ex), pc.invert(cand)))
        if n:
            ctx.add("inert_line", f"{n} line(s) are neither existing nor candidates", file=file, table=table,
                    rows=rows, count=n)


def _check_buses(t: pa.Table, ctx: Collector, file: str) -> None:
    exp, cost = _col(t, "hosting_expandable"), _col(t, "hosting_expand_cost_per_mw_yr")
    if exp is None:
        return
    nocost = pc.is_null(cost, nan_is_null=True) if cost is not None else pa.array([True] * t.num_rows)
    rows, n = rows_of(pc.and_(exp, nocost))
    if n:
        ctx.add("missing_value", f"{n} expandable bus(es) give no hosting_expand_cost_per_mw_yr", file=file,
                table="buses", column="hosting_expand_cost_per_mw_yr", rows=rows, count=n)


def _missing(t: pa.Table, name: str) -> pa.ChunkedArray | pa.Array:
    c = _col(t, name)
    if c is None:
        return pa.array([True] * t.num_rows)
    return pc.is_null(c, nan_is_null=True)


def _check_candidates(t: pa.Table, desc: dict, ctx: Collector, file: str) -> None:
    kind = _col(t, "kind")
    if kind is None:
        return
    no_ann, no_ovn = _missing(t, "capex_per_mw_yr"), _missing(t, "capex_overnight_per_mw")
    rows, n = rows_of(pc.and_(no_ann, no_ovn))
    if n:
        ctx.add("missing_capex", f"{n} candidate(s) give neither capex_per_mw_yr nor capex_overnight_per_mw",
                file=file, table="candidates", column="capex_per_mw_yr", rows=rows, count=n)
    ic = _col(t, "interconnection_cost_per_kw")
    has_ic = pc.fill_null(pc.greater(ic, 0.0), False) if ic is not None else pa.array([False] * t.num_rows)
    needs_crf = pc.or_(pc.and_(no_ann, pc.invert(no_ovn)), has_ic)
    defaults = (get_path(desc, "money.default_annualisation_rate") is not None
                and get_path(desc, "money.default_lifetime_years") is not None)
    if not defaults:
        row_crf = pc.and_(pc.invert(_missing(t, "annualisation_rate")), pc.invert(_missing(t, "lifetime_years")))
        rows, n = rows_of(pc.and_(needs_crf, pc.invert(row_crf)))
        if n:
            ctx.add("overnight_cost_without_annualisation",
                    f"{n} candidate(s) carry an overnight cost but no annualisation_rate + lifetime_years "
                    "(row or [money] defaults)", file=file, table="candidates", rows=rows, count=n)
    for k, cols in (("hybrid", ("generation_mw", "storage_mw", "duration_h")),):
        is_k = pc.equal(kind, k)
        for c in cols:
            rows, n = rows_of(pc.and_(is_k, _missing(t, c)))
            if n:
                ctx.add("missing_value", f"{n} {k} candidate(s) give no {c}", file=file, table="candidates",
                        column=c, rows=rows, count=n)
    is_sto = pc.equal(kind, "storage")
    rows, n = rows_of(pc.and_(is_sto, pc.and_(_missing(t, "duration_h"), _missing(t, "max_mwh"))))
    if n:
        ctx.add("missing_value", f"{n} storage candidate(s) give neither duration_h nor max_mwh", file=file,
                table="candidates", column="duration_h", rows=rows, count=n)
    ib = _col(t, "integer_build")
    if ib is not None:
        rows, n = rows_of(pc.and_(ib, _missing(t, "unit_size_mw")))
        if n:
            ctx.add("missing_value", f"{n} integer-build candidate(s) give no unit_size_mw", file=file,
                    table="candidates", column="unit_size_mw", rows=rows, count=n)


def _names(t: pa.Table | None, col: str) -> list[str]:
    c = _col(t, col)
    return [] if c is None else [v for v in c.to_pylist() if v is not None]


#: Tables whose names share one name space, and why. Generators, storage and candidates key availability and
#: the results' component names; lines and HVDC corridors are both referenced by ``flows.line`` and by
#: ``investment.name`` (network rows), so a line and a corridor with one name would be indistinguishable.
NAME_SPACES: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("generators / storage / candidates", (("generators", "name"), ("storage", "name"), ("candidates", "candidate_id"))),
    ("lines / hvdc", (("lines", "name"), ("hvdc", "name"))),
)


def _check_name_space(lb: LoadedBundle, ctx: Collector, files: dict[str, str]) -> None:
    """Names that key results must be unique across the tables of each name space (SPEC.md section 6.3)."""
    for label, members in NAME_SPACES:
        owners: dict[str, str] = {}
        clashes: dict[str, set[str]] = {}
        for table, col in members:
            for v in set(_names(lb.tables.get(table), col)):
                if v in owners and owners[v] != table:
                    clashes.setdefault(v, {owners[v]}).add(table)
                owners.setdefault(v, table)
        if clashes:
            last = members[-1][0]
            ex = sorted(clashes)[:MAX_ROWS_REPORTED]
            ctx.add("name_collision", f"{len(clashes)} name(s) are used by more than one of {label}: "
                    f"{', '.join(ex)}", file=files.get(last, ""), table=last, count=len(clashes))


def _check_availability_coverage(lb: LoadedBundle, ctx: Collector, files: dict[str, str]) -> None:
    need: list[str] = []
    g = lb.tables.get("generators")
    if g is not None and {"kind", "name", "existing_mw"} <= set(g.column_names):
        mask = pc.and_(pc.equal(g.column("kind"), "renewable"), pc.greater(g.column("existing_mw"), 0.0))
        need += [v for v in pc.filter(g.column("name"), pc.fill_null(mask, False)).to_pylist() if v is not None]
    c = lb.tables.get("candidates")
    if c is not None and {"kind", "candidate_id"} <= set(c.column_names):
        mask = pc.is_in(c.column("kind"), value_set=pa.array(["renewable", "hybrid"]))
        need += [v for v in pc.filter(c.column("candidate_id"), pc.fill_null(mask, False)).to_pylist() if v is not None]
    if not need:
        return
    a = lb.tables.get("availability")
    have = set(_names(a, "site_name"))
    missing = sorted(set(need) - have)
    if missing:
        ctx.add("missing_availability", f"{len(missing)} availability-bounded resource(s) have no series and would "
                f"produce nothing: {', '.join(missing[:MAX_ROWS_REPORTED])}"
                f"{' ...' if len(missing) > MAX_ROWS_REPORTED else ''}",
                file=files.get("availability", ""), table="availability", column="site_name", count=len(missing))


def check_series_complete(t: pa.Table, id_col: str, ax: Axis, ctx: Collector, table: str, file: str) -> None:
    """Every series must be complete: within each (year, weather) group present, every id carries every
    (scenario, block) cell, each with exactly periods 1..nperiods; every group carries the same ids; a
    `year` column covers every investment year. A missing cell would otherwise read as zero."""
    need = {id_col, "scenario", "block", "period"}
    if not need <= set(t.column_names) or t.num_rows == 0 or not ax.blocks:
        return
    gk = [c for c in ("year", "weather") if c in t.column_names]
    agg = t.group_by([id_col, "scenario", "block", *gk]).aggregate(
        [("period", "count"), ("period", "min"), ("period", "max")])
    blk = pc.cast(agg.column("block"), pa.int64())
    top = max(ax.blocks)
    lookup = [0] * (top + 1)
    for b, n in ax.blocks.items():
        lookup[b] = n
    safe = pc.if_else(pc.and_(pc.greater_equal(blk, 0), pc.less_equal(blk, top)), blk, 0)
    exp = pc.take(pa.array(lookup, type=pa.int64()), safe)
    bad = pc.and_(pc.greater(exp, 0), pc.or_(pc.or_(pc.not_equal(agg.column("period_count"), exp),
                                                     pc.not_equal(agg.column("period_min"), 1)),
                                             pc.not_equal(pc.cast(agg.column("period_max"), pa.int64()), exp)))
    _, n = rows_of(bad)
    if n:
        ex = agg.filter(pc.fill_null(bad, False)).slice(0, 1).to_pylist()[0]
        ctx.add("incomplete_series", f"{n} (id, scenario, block) cell(s) do not carry exactly periods 1..nperiods "
                f"(e.g. {id_col}={ex[id_col]!r} scenario={ex['scenario']} block={ex['block']}: "
                f"{ex['period_count']} periods)", file=file, table=table, count=n)
    cells = agg.group_by([id_col, *gk]).aggregate([("block", "count")])
    expected_cells = ax.n_scen * len(ax.blocks)
    bad = pc.not_equal(cells.column("block_count"), expected_cells)
    _, n = rows_of(bad)
    if n:
        ex = cells.filter(pc.fill_null(bad, False)).slice(0, 1).to_pylist()[0]
        ctx.add("incomplete_series", f"{n} series carry fewer or more than the {expected_cells} (scenario, block) "
                f"cells declared (e.g. {id_col}={ex[id_col]!r}: {ex['block_count']})", file=file, table=table,
                count=n)
    if gk:
        ids_total = len(pc.unique(cells.column(id_col)))
        per_group = cells.group_by(gk).aggregate([(id_col, "count")])
        bad = pc.not_equal(per_group.column(f"{id_col}_count"), ids_total)
        _, n = rows_of(bad)
        if n:
            ex = per_group.filter(pc.fill_null(bad, False)).slice(0, 1).to_pylist()[0]
            ex.pop(f"{id_col}_count", None)
            ctx.add("incomplete_series", f"{n} {'/'.join(gk)} group(s) do not carry every {id_col} "
                    f"(e.g. {ex})", file=file, table=table, count=n)
    if "year" in gk and ax.years:
        absent = sorted(set(ax.years) - set(pc.unique(agg.column("year")).to_pylist()))
        if absent:
            ctx.add("incomplete_series", f"the table has a `year` column but no rows for investment year(s) "
                    f"{absent}", file=file, table=table, column="year", count=len(absent))


def _year_weather_pairs(t: pa.Table | None) -> set[tuple[int, int]] | None:
    if t is None or not {"year", "weather"} <= set(t.column_names):
        return None
    g = t.group_by(["year", "weather"]).aggregate([])
    return set(zip(g.column("year").to_pylist(), g.column("weather").to_pylist()))


def _check_interfaces(t: pa.Table, ctx: Collector, file: str) -> None:
    if "interface_id" not in t.column_names:
        return
    cols = [c for c in ("name", "max_fwd_mw", "max_rev_mw", "source") if c in t.column_names]
    if not cols:
        return
    agg = t.group_by(["interface_id"]).aggregate([(c, "count_distinct", pc.CountOptions(mode="all")) for c in cols])
    for c in cols:
        bad = pc.greater(agg.column(f"{c}_count_distinct"), 1)
        ids = pc.filter(agg.column("interface_id"), bad).to_pylist()
        if ids:
            ctx.add("inconsistent_interface", f"interface(s) {ids[:MAX_ROWS_REPORTED]} carry different `{c}` "
                    "values on different member rows", file=file, table="interfaces", column=c, count=len(ids))


def _check_weather_weights(t: pa.Table, ctx: Collector, file: str) -> None:
    if not {"year", "weight"} <= set(t.column_names) or t.num_rows == 0:
        return
    s = t.group_by(["year"]).aggregate([("weight", "sum")])
    years = pc.filter(s.column("year"), pc.less_equal(s.column("weight_sum"), 0.0)).to_pylist()
    if years:
        ctx.add("out_of_range", f"weights of investment year(s) {years} sum to zero", file=file,
                table="weather_weights", column="weight", count=len(years))


def load_bundle(path: str | Path, ctx: Collector | None = None) -> LoadedBundle:
    """Read and check a bundle directory, reporting into ``ctx``. Most callers want :func:`validate_bundle`."""
    ctx = ctx if ctx is not None else Collector()
    root = Path(path)
    lb = LoadedBundle(root)
    if not root.is_dir():
        ctx.add("missing_document", f"{root} is not a directory", file=str(root))
        return lb
    dschema = doc_schema("bundle", "bundle.toml")
    desc = load_document(root / "bundle.toml", dschema, ctx)
    if desc is None or not check_format(desc, "ce-bundle", ctx, "bundle.toml"):
        return lb
    validate_document(desc, dschema, ctx)
    check_money(desc, ctx, "bundle.toml")
    lb.descriptor = desc
    lb.axis = ax = parse_axis(desc, ctx, "bundle.toml", blocks_required=True)

    schemas = table_schemas("bundle")
    inv = take_inventory(root, set(schemas), {"bundle.toml"}, ctx)
    for name, ts in schemas.items():
        if ts.required and name not in inv.tables:
            ctx.add("missing_table", f"required table {name!r} is absent", table=name)
    files = {n: p.name for n, p in inv.tables.items()}
    nrows: dict[str, int] = {}
    for name, p in sorted(inv.tables.items()):
        try:
            raw = read_raw(p)
        except Exception as exc:  # noqa: BLE001 — any reader failure is reported by name
            ctx.add("unreadable_table", f"{type(exc).__name__}: {exc}", file=p.name, table=name)
            continue
        nrows[p.name] = raw.num_rows
        t = normalize(raw, schemas[name], ctx, p.name)
        check_columns(t, schemas[name], ctx, p.name)
        check_keys(t, schemas[name], ctx, p.name)
        lb.tables[name] = t

    for name, t in lb.tables.items():
        for fk in schemas[name].foreign_keys:
            parent = lb.tables.get(fk.table)
            if fk.column in t.column_names and parent is not None and fk.ref_column in parent.column_names:
                check_reference(t.column(fk.column), parent.column(fk.ref_column), ctx, table=name,
                                column=fk.column, target=f"{fk.table}.{fk.ref_column}", file=files[name])

    if "lines" in lb.tables:
        _check_lines(lb.tables["lines"], ctx, "lines", files["lines"])
    if "hvdc" in lb.tables:
        _check_lines(lb.tables["hvdc"], ctx, "hvdc", files["hvdc"])
    if "buses" in lb.tables:
        _check_buses(lb.tables["buses"], ctx, files["buses"])
    if "candidates" in lb.tables:
        _check_candidates(lb.tables["candidates"], desc, ctx, files["candidates"])
    _check_name_space(lb, ctx, files)
    _check_availability_coverage(lb, ctx, files)
    if "availability" in lb.tables:
        sites = set(_names(lb.tables.get("generators"), "name")) | set(_names(lb.tables.get("candidates"), "candidate_id"))
        a = lb.tables["availability"]
        if "site_name" in a.column_names:
            check_reference(a.column("site_name"), sorted(sites), ctx, table="availability", column="site_name",
                            target="generators.name / candidates.candidate_id", file=files["availability"])
    for name in ("demand", "availability"):
        t = lb.tables.get(name)
        if t is None:
            continue
        check_series_axis(t, ax, ctx, name, files[name], weather_needs_year=True)
        check_series_complete(t, schemas[name].series_id, ax, ctx, name, files[name])
    pd, pa_ = _year_weather_pairs(lb.tables.get("demand")), _year_weather_pairs(lb.tables.get("availability"))
    if pd is not None and pa_ is not None and pd != pa_:
        ctx.add("incomplete_series", f"demand and availability carry different (year, weather) groups: "
                f"{sorted(pd ^ pa_)[:MAX_ROWS_REPORTED]}", table="availability", column="weather")
    for name in ("weather_weights", "peak_demand"):
        t = lb.tables.get(name)
        if t is not None:
            check_series_axis(t, ax, ctx, name, files[name], weather_needs_year=False)
    if "weather_weights" in lb.tables:
        _check_weather_weights(lb.tables["weather_weights"], ctx, files["weather_weights"])
    if "interfaces" in lb.tables:
        _check_interfaces(lb.tables["interfaces"], ctx, files["interfaces"])
    check_sidecars(root, inv.sidecars, nrows, ctx)
    return lb


def validate_bundle(path: str | Path) -> Report:
    """Validate a bundle directory. Bundles are inputs, so every unknown table, column, key or newer
    version is REFUSED (SPEC.md section 3.3)."""
    ctx = Collector()
    load_bundle(path, ctx)
    return Report(str(path), "bundle", ctx.issues)
