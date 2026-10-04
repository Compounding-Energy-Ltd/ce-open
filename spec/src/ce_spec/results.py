# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Validate a CE results directory (what a run writes) against the specification."""

from __future__ import annotations

import math
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

from .bundle import load_bundle
from .common import Axis, check_format, check_money, check_series_axis, check_sidecars, parse_axis, take_inventory
from .documents import get_path, load_document, validate_document
from .issues import WARNING, Collector, Report
from .schema import doc_schemas, table_schemas
from .tables import check_columns, check_keys, check_reference, normalize, read_raw, rows_of

#: Codes that are warnings, not errors, when results are validated leniently (the default): a reader selects
#: result columns by name, so an extra output cannot change a number it reads (SPEC.md section 3.3).
LENIENT = {"unknown_key": WARNING, "unknown_column": WARNING, "unknown_table": WARNING, "unknown_file": WARNING,
           "newer_minor_version": WARNING, "json_forbidden": WARNING}

ARTEFACT_EXTS = (".html", ".png", ".svg", ".pdf")
REL_TOL = 1e-6


def _check_summary(doc: dict, ctx: Collector) -> None:
    tot, cap, opx = (get_path(doc, f"cost.{k}") for k in ("total", "investment_capex", "operational_opex"))
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (tot, cap, opx)):
        if not math.isclose(tot, cap + opx, rel_tol=REL_TOL, abs_tol=REL_TOL * max(1.0, abs(tot))):
            ctx.add("cost_identity_mismatch", f"total {tot!r} ≠ investment_capex {cap!r} + operational_opex {opx!r}",
                    file="summary.toml", column="cost.total")
    conv, cause = get_path(doc, "solve.converged"), get_path(doc, "solve.stop_cause")
    if isinstance(conv, bool) and isinstance(cause, str) and conv != (cause == "gap"):
        ctx.add("stop_cause_inconsistent", f"converged = {conv} but stop_cause = {cause!r}", file="summary.toml",
                column="solve.stop_cause")


def _check_investment(t: pa.Table, ctx: Collector, file: str) -> None:
    if "component" not in t.column_names:
        return
    comp = t.column("component")
    is_ret = pc.equal(comp, "retirement")
    is_se = pc.equal(comp, "storage_energy")
    if "built_mw" in t.column_names:
        mw = t.column("built_mw")
        rows, n = rows_of(pc.and_(is_ret, pc.greater(mw, 1e-6)))
        if n:
            ctx.add("sign_convention", f"{n} retirement row(s) carry a positive built_mw (retirements are ≤ 0)",
                    file=file, table="investment", column="built_mw", rows=rows, count=n)
        rows, n = rows_of(pc.and_(pc.invert(is_ret), pc.less(mw, -1e-6)))
        if n:
            ctx.add("sign_convention", f"{n} build row(s) carry a negative built_mw (only retirements are < 0)",
                    file=file, table="investment", column="built_mw", rows=rows, count=n)
        rows, n = rows_of(pc.and_(pc.invert(is_se), pc.is_null(mw, nan_is_null=True)))
        if n:
            ctx.add("missing_value", f"{n} row(s) other than storage_energy give no built_mw", file=file,
                    table="investment", column="built_mw", rows=rows, count=n)
    mwh = t.column("built_mwh") if "built_mwh" in t.column_names else None
    missing_mwh = pc.is_null(mwh, nan_is_null=True) if mwh is not None else pa.array([True] * t.num_rows)
    rows, n = rows_of(pc.and_(is_se, missing_mwh))
    if n:
        ctx.add("missing_value", f"{n} storage_energy row(s) give no built_mwh", file=file, table="investment",
                column="built_mwh", rows=rows, count=n)


def _check_lmp(t: pa.Table, ctx: Collector, file: str) -> None:
    parts = [c for c in ("lmp_energy", "lmp_congestion", "lmp_loss") if c in t.column_names]
    if not parts:
        return
    if len(parts) < 3:
        ctx.add("partial_decomposition", f"only {parts} of the three LMP components are present", file=file,
                table="lmp")
        return
    if "lmp_per_mwh" not in t.column_names:
        return
    total = t.column("lmp_per_mwh")
    s = pc.add(pc.add(t.column("lmp_energy"), t.column("lmp_congestion")), t.column("lmp_loss"))
    tol = pc.multiply(pc.max_element_wise(pc.abs(total), 1.0), REL_TOL)
    rows, n = rows_of(pc.greater(pc.abs(pc.subtract(total, s)), tol))
    if n:
        ctx.add("lmp_decomposition_mismatch", f"{n} row(s) where lmp_per_mwh ≠ energy + congestion + loss",
                file=file, table="lmp", column="lmp_per_mwh", rows=rows, count=n)


def _check_curtailment(t: pa.Table, ctx: Collector, file: str) -> None:
    if not {"available_mw", "dispatch_mw", "curtailed_mw"} <= set(t.column_names):
        return
    d = pc.subtract(t.column("available_mw"), t.column("dispatch_mw"))
    c = t.column("curtailed_mw")
    tol = pc.multiply(pc.max_element_wise(pc.abs(t.column("available_mw")), 1.0), REL_TOL)
    rows, n = rows_of(pc.greater(pc.abs(pc.subtract(c, d)), tol))
    if n:
        ctx.add("curtailment_identity_mismatch", f"{n} row(s) where curtailed_mw ≠ available_mw − dispatch_mw",
                file=file, table="curtailment", column="curtailed_mw", rows=rows, count=n)


def qa_overall(rows: list[dict]) -> dict:
    """The overall QA status and counts derived from qa_checks rows (SPEC.md section 9.6)."""
    st = [r.get("status") for r in rows]
    req_na = [r for r in rows if r.get("status") == "NA" and r.get("required") is True]
    n_fail, n_warn = st.count("FAIL"), st.count("WARN")
    status = "FAIL" if n_fail else "WARN" if n_warn else "INCOMPLETE" if req_na else "PASS"
    return {"status": status, "n_pass": st.count("PASS"), "n_warn": n_warn, "n_fail": n_fail,
            "n_na": st.count("NA"), "n_required_na": len(req_na)}


def _check_qa(t: pa.Table | None, report: dict | None, ctx: Collector, file: str) -> None:
    if t is not None and {"status", "population"} <= set(t.column_names):
        rows, n = rows_of(pc.and_(pc.equal(t.column("status"), "PASS"), pc.equal(t.column("population"), 0)))
        if n:
            ctx.add("qa_vacuous_pass", f"{n} check(s) report PASS with population 0 (must be NA)", file=file,
                    table="qa_checks", column="status", rows=rows, count=n)
    if report is None:
        return
    if t is None:
        ctx.add("missing_table", "qa_report.toml is present but the qa_checks table is not", table="qa_checks")
        return
    want = qa_overall(t.select([c for c in ("status", "required") if c in t.column_names]).to_pylist())
    got = report.get("qa", {})
    diffs = [f"{k}: report {got.get(k)!r} vs checks {v!r}" for k, v in want.items() if k in got and got.get(k) != v]
    if diffs:
        ctx.add("qa_status_inconsistent", "; ".join(diffs), file="qa_report.toml", column="qa")


def _check_index(index: dict, run_name, raw_meta: dict[str, tuple[int, list[str], set[str]]],
                 ctx: Collector) -> None:
    f = "results_manifest.toml"
    if run_name is not None and index.get("run") is not None and index.get("run") != run_name:
        ctx.add("index_mismatch", f"run {index.get('run')!r} ≠ manifest run.name {run_name!r}", file=f, column="run")
    listed = set()
    for i, e in enumerate(index.get("tables", []) or []):
        if not isinstance(e, dict) or not isinstance(e.get("name"), str):
            continue
        name = e["name"]
        listed.add(name)
        meta = raw_meta.get(name)
        if meta is None:
            ctx.add("index_mismatch", f"tables[{i}] lists {name!r} but no such table was read", file=f, table=name)
            continue
        nrows, cols, formats = meta
        if isinstance(e.get("nrows"), int) and e["nrows"] != nrows:
            ctx.add("index_mismatch", f"{name!r}: nrows {e['nrows']} listed, {nrows} on disk", file=f, table=name)
        if isinstance(e.get("columns"), list) and e["columns"] != cols:
            ctx.add("index_mismatch", f"{name!r}: listed columns differ from the file's", file=f, table=name)
        if isinstance(e.get("formats"), list) and not set(e["formats"]) <= formats:
            ctx.add("index_mismatch", f"{name!r}: listed formats {e['formats']} but on disk {sorted(formats)}",
                    file=f, table=name)
    unlisted = sorted(set(raw_meta) - listed)
    if unlisted:
        ctx.add("index_mismatch", f"table(s) on disk but not listed: {unlisted}", file=f)


def _ext_format(name: str) -> str:
    for ext, fmt in ((".parquet", "parquet"), (".arrow", "arrow"), (".csv.gz", "csv"), (".csv", "csv")):
        if name.endswith(ext):
            return fmt
    return ""


def _check_against_bundle(bundle_path: Path, manifest: dict, tables: dict[str, pa.Table],
                          files: dict[str, str], ctx: Collector) -> None:
    bctx = Collector()
    lb = load_bundle(bundle_path, bctx)
    if lb.descriptor is None:
        ctx.add("missing_document", f"the input bundle at {bundle_path} could not be read", file="bundle.toml")
        return
    for k in ("currency", "dollar_year"):
        a, b = get_path(manifest, f"money.{k}"), get_path(lb.descriptor, f"money.{k}")
        if a is not None and b is not None and a != b:
            ctx.add("currency_mismatch", f"results money.{k} = {a!r} but the bundle declares {b!r}",
                    file="manifest.toml", column=f"money.{k}")
    def names(t, c):
        return [] if t is None or c not in t.column_names else t.column(c).to_pylist()
    buses = names(lb.tables.get("buses"), "name")
    lines = names(lb.tables.get("lines"), "name") + names(lb.tables.get("hvdc"), "name")
    comps = (names(lb.tables.get("generators"), "name") + names(lb.tables.get("storage"), "name")
             + names(lb.tables.get("candidates"), "candidate_id") + lines)
    refs = {"bus": (buses, "buses.name"), "line": (lines, "lines.name / hvdc.name")}
    for tname, t in tables.items():
        for col, (vals, target) in refs.items():
            if col in t.column_names and vals:
                check_reference(t.column(col), vals, ctx, table=tname, column=col, target=target, file=files[tname])
        for col in ("component_name", "site_name", "storage"):
            if col in t.column_names and comps:
                check_reference(t.column(col), comps, ctx, table=tname, column=col, target="bundle component names",
                                file=files[tname])
    fl, ln = tables.get("flows"), lb.tables.get("lines")
    if fl is not None and ln is not None and {"line", "flow_mw"} <= set(fl.column_names) and \
            {"name", "rating_mw"} <= set(ln.column_names):
        cap = pc.add(ln.column("rating_mw"), pc.fill_null(ln.column("delta_rating_mw"), 0.0)) \
            if "delta_rating_mw" in ln.column_names else ln.column("rating_mw")
        idx = pc.index_in(fl.column("line"), value_set=ln.column("name").combine_chunks())
        lim = pc.take(cap.combine_chunks() if isinstance(cap, pa.ChunkedArray) else cap, idx)
        bad = pc.greater(pc.abs(fl.column("flow_mw")), pc.add(lim, 1e-6))
        rows, n = rows_of(bad)
        if n:
            ctx.add("flow_exceeds_rating", f"{n} flow(s) exceed rating_mw + delta_rating_mw", file=files["flows"],
                    table="flows", column="flow_mw", rows=rows, count=n)


def validate_results(path: str | Path, *, bundle: str | Path | None = None, strict: bool = False) -> Report:
    """Validate a results directory. ``bundle`` (optional) cross-checks names and money against the input
    bundle. ``strict=True`` refuses unknown tables, columns and keys as a bundle validation does; the default
    reports them as warnings."""
    ctx = Collector(None if strict else LENIENT)
    root = Path(path)
    report = Report(str(path), "results", ctx.issues)
    if not root.is_dir():
        ctx.add("missing_document", f"{root} is not a directory", file=str(root))
        return report
    docs = doc_schemas("results")
    manifest = load_document(root / "manifest.toml", docs["manifest.toml"], ctx)
    if manifest is None or not check_format(manifest, "ce-results", ctx, "manifest.toml"):
        return report
    validate_document(manifest, docs["manifest.toml"], ctx)
    check_money(manifest, ctx, "manifest.toml")
    ax: Axis = parse_axis(manifest, ctx, "manifest.toml", blocks_required=False)
    loaded: dict[str, dict] = {}
    for name in ("summary.toml", "qa_report.toml", "results_manifest.toml"):
        d = load_document(root / name, docs[name], ctx)
        if d is not None:
            loaded[name] = validate_document(d, docs[name], ctx)
    if "summary.toml" in loaded:
        _check_summary(loaded["summary.toml"], ctx)

    schemas = table_schemas("results")
    inv = take_inventory(root, set(schemas), set(docs), ctx, artefact_exts=ARTEFACT_EXTS)
    for name, ts in schemas.items():
        if ts.required and name not in inv.tables:
            ctx.add("missing_table", f"required table {name!r} is absent", table=name)
    files = {n: p.name for n, p in inv.tables.items()}
    tables: dict[str, pa.Table] = {}
    nrows: dict[str, int] = {}
    raw_meta: dict[str, tuple[int, list[str], set[str]]] = {}
    for name, p in sorted(inv.tables.items()):
        try:
            raw = read_raw(p)
        except Exception as exc:  # noqa: BLE001
            ctx.add("unreadable_table", f"{type(exc).__name__}: {exc}", file=p.name, table=name)
            continue
        nrows[p.name] = raw.num_rows
        formats = {_ext_format(q.name) for q in root.iterdir() if q.name.startswith(name + ".") and _ext_format(q.name)
                   and q.name[len(name):] in (".parquet", ".arrow", ".csv", ".csv.gz")}
        raw_meta[name] = (raw.num_rows, list(raw.column_names), formats)
        t = normalize(raw, schemas[name], ctx, p.name)
        check_columns(t, schemas[name], ctx, p.name)
        check_keys(t, schemas[name], ctx, p.name)
        tables[name] = t
        if schemas[name].series or name == "investment":
            check_series_axis(t, ax, ctx, name, p.name, weather_needs_year=False)

    if "investment" in tables:
        _check_investment(tables["investment"], ctx, files["investment"])
    if "lmp" in tables:
        _check_lmp(tables["lmp"], ctx, files["lmp"])
    if "curtailment" in tables:
        _check_curtailment(tables["curtailment"], ctx, files["curtailment"])
    if "qa_checks" in tables or "qa_report.toml" in loaded:
        _check_qa(tables.get("qa_checks"), loaded.get("qa_report.toml"), ctx, files.get("qa_checks", ""))
    if "results_manifest.toml" in loaded:
        _check_index(loaded["results_manifest.toml"], get_path(manifest, "run.name"), raw_meta, ctx)
    if bundle is not None:
        _check_against_bundle(Path(bundle), manifest, tables, files, ctx)
    check_sidecars(root, inv.sidecars, nrows, ctx)
    return report
