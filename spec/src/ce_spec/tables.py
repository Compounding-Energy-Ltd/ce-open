# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Reading table files and the column-level checks every table gets.

A table is one file named ``<table><ext>`` where ext is ``.parquet`` (canonical), ``.arrow`` (Arrow IPC file
format), ``.csv`` or ``.csv.gz``. CSV is read with every column as text and then cast column by column, so a
bad cell is reported against its column and row instead of failing the whole file.
"""

from __future__ import annotations

import csv
import gzip
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pv
import pyarrow.parquet as pq

from .issues import Collector
from .schema import Column, TableSchema

TABLE_EXTS = (".parquet", ".arrow", ".csv.gz", ".csv")
MAX_ROWS_REPORTED = 5

_INT_RE = r"^[+-]?[0-9]+$"
_FLOAT_RE = r"^[+-]?(([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?|[Nn][Aa][Nn]|[Ii]nf(inity)?|INF)$"


def split_table_name(filename: str) -> tuple[str, str] | None:
    """``"lmp.csv.gz"`` -> ``("lmp", ".csv.gz")``; None when the extension is not a table extension."""
    for ext in TABLE_EXTS:
        if filename.endswith(ext) and len(filename) > len(ext):
            return filename[: -len(ext)], ext
    return None


def _csv_header(path: Path) -> list[str]:
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", newline="") as fh:
        return next(csv.reader(fh), [])


def read_raw(path: Path) -> pa.Table:
    """Read a table file as stored (CSV: every column as text, empty cell = null)."""
    path = Path(path)
    name = path.name
    if name.endswith(".parquet"):
        return pq.read_table(path)
    if name.endswith(".arrow"):
        with pa.memory_map(str(path), "r") as src:
            return pa.ipc.open_file(src).read_all()
    if name.endswith(".csv") or name.endswith(".csv.gz"):
        header = _csv_header(path)
        if not header:
            return pa.table({})
        opts = pv.ConvertOptions(column_types={h: pa.string() for h in header}, strings_can_be_null=True,
                                 null_values=[""], quoted_strings_can_be_null=True)
        return pv.read_csv(path, convert_options=opts)
    raise ValueError(f"not a table file: {path}")


def rows_of(mask: pa.Array | pa.ChunkedArray) -> tuple[list[int], int]:
    """First row indices (0-based data rows) where ``mask`` is true, and how many there are in total."""
    m = pc.fill_null(mask, False)
    idx = pc.indices_nonzero(m)
    n = len(idx)
    return [int(x) for x in idx[:MAX_ROWS_REPORTED].to_pylist()], n


def _decode(col: pa.ChunkedArray) -> pa.ChunkedArray:
    if pa.types.is_dictionary(col.type):
        return pa.chunked_array([c.dictionary_decode() for c in col.chunks], type=col.type.value_type)
    return col


def _cast_column(col: pa.ChunkedArray, spec: Column, ctx: Collector, table: str, file: str,
                 text: bool) -> pa.ChunkedArray | None:
    """Cast a column to its canonical Arrow type, or report ``bad_type`` and return None. Text is parsed
    into numbers and booleans only when the file is text (CSV); a typed file must store the right family."""
    col = _decode(col)
    t = col.type
    target = spec.arrow_type
    fam = spec.family
    if pa.types.is_null(t):
        return pa.chunked_array([pa.nulls(len(col), type=target)])
    if (pa.types.is_string(t) or pa.types.is_large_string(t)) and (text or fam == "string"):
        if fam == "string":
            return col.cast(target)
        s = pc.utf8_trim_whitespace(col)
        if fam == "bool":
            low = pc.utf8_lower(s)
            ok = pc.or_(pc.equal(low, "true"), pc.equal(low, "false"))
        else:
            ok = pc.match_substring_regex(s, _INT_RE if fam == "int" else _FLOAT_RE)
        bad = pc.and_(pc.invert(pc.fill_null(ok, True)), pc.is_valid(s))
        rows, n = rows_of(bad)
        if n:
            sample = pc.filter(s, pc.fill_null(bad, False))[0].as_py()
            ctx.add("bad_type", f"{n} value(s) are not {spec.type} (e.g. {sample!r})",
                    file=file, table=table, column=spec.name, rows=rows, count=n)
            return None
        if fam == "bool":
            return pc.equal(pc.utf8_lower(s), "true")
        try:
            return s.cast(target)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
            ctx.add("bad_type", f"cannot cast to {spec.type}: {exc}", file=file, table=table, column=spec.name)
            return None
    ok_family = {
        "int": pa.types.is_integer(t),
        "float": pa.types.is_floating(t) or pa.types.is_integer(t),
        "string": False,
        "bool": pa.types.is_boolean(t),
    }[fam]
    if not ok_family:
        ctx.add("bad_type", f"stored as {t}, expected the {fam} family ({spec.type})",
                file=file, table=table, column=spec.name)
        return None
    try:
        return col.cast(target)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
        ctx.add("bad_type", f"values do not fit {spec.type}: {exc}", file=file, table=table, column=spec.name)
        return None


def is_text_file(file: str) -> bool:
    return file.endswith(".csv") or file.endswith(".csv.gz")


def normalize(raw: pa.Table, schema: TableSchema, ctx: Collector, file: str = "", *, text: bool | None = None) -> pa.Table:
    """Rename legacy spellings, drop unknown and extension columns, cast known columns to their canonical
    types and add absent optional columns that have a default. Reports every column-level issue.
    ``text`` (default: inferred from ``file``) says whether values are CSV text to be parsed."""
    text = is_text_file(file) if text is None else text
    tname = schema.name
    names = list(raw.column_names)
    seen: set[str] = set()
    for n in names:
        if n in seen:
            ctx.add("duplicate_spelling", f"column {n!r} appears more than once", file=file, table=tname, column=n)
        seen.add(n)
    cols: dict[str, pa.ChunkedArray] = {}
    for i, n in enumerate(names):
        if n not in cols:            # by position: a repeated name cannot be looked up by name
            cols[n] = raw.column(i)
    for legacy, reason in schema.refused.items():
        if legacy in cols:
            ctx.add("ambiguous_legacy_cost", f"column {legacy!r} does not say whether its cost is annualised or "
                    f"overnight, so it is not read: {reason}", file=file, table=tname, column=legacy)
            del cols[legacy]
    for legacy, canonical in schema.aliases.items():
        if legacy not in cols:
            continue
        if canonical in cols:
            ctx.add("duplicate_spelling", f"both {legacy!r} (legacy) and {canonical!r} are present",
                    file=file, table=tname, column=canonical)
        else:
            ctx.add("deprecated_spelling", f"legacy column {legacy!r} read as {canonical!r}",
                    file=file, table=tname, column=legacy)
            cols[canonical] = cols[legacy]
        del cols[legacy]
    out: dict[str, pa.ChunkedArray] = {}
    for n, col in cols.items():
        spec = schema.column(n)
        if spec is None:
            if not n.startswith("x_"):
                ctx.add("unknown_column", f"column {n!r} is not defined for table {tname!r}",
                        file=file, table=tname, column=n)
            continue
        cast = _cast_column(col, spec, ctx, tname, file, text)
        if cast is not None:
            out[n] = cast if isinstance(cast, pa.ChunkedArray) else pa.chunked_array([cast])
    for spec in schema.columns:
        if spec.name in cols:
            continue
        if spec.required:
            ctx.add("missing_column", f"required column {spec.name!r} is absent", file=file, table=tname,
                    column=spec.name)
        elif spec.default is not None:
            out[spec.name] = pa.chunked_array([pa.array([spec.default] * raw.num_rows, type=spec.arrow_type)])
    order = [c.name for c in schema.columns if c.name in out]
    return pa.table({n: out[n] for n in order})


def is_missing(col: pa.ChunkedArray) -> pa.ChunkedArray:
    """Null, or NaN for floating columns."""
    if pa.types.is_floating(col.type):
        return pc.is_null(col, nan_is_null=True)
    return pc.is_null(col)


def check_columns(t: pa.Table, schema: TableSchema, ctx: Collector, file: str = "") -> None:
    """Null, enum and range checks of every present column."""
    for spec in schema.columns:
        if spec.name not in t.column_names:
            continue
        col = t.column(spec.name)
        if not spec.allows_null:
            rows, n = rows_of(is_missing(col))
            if n:
                ctx.add("null_value", f"{n} null/NaN value(s) in a column that requires a value",
                        file=file, table=schema.name, column=spec.name, rows=rows, count=n)
        if spec.enum is not None:
            allowed = pa.array(list(spec.enum), type=spec.arrow_type)
            bad = pc.and_(pc.invert(pc.is_in(col, value_set=allowed)), pc.is_valid(col))
            rows, n = rows_of(bad)
            if n:
                sample = pc.filter(col, pc.fill_null(bad, False))[0].as_py()
                ctx.add("bad_enum", f"{n} value(s) outside {list(spec.enum)} (e.g. {sample!r})",
                        file=file, table=schema.name, column=spec.name, rows=rows, count=n)
        if spec.family in ("int", "float") and (spec.min is not None or spec.max is not None):
            bad = None
            if spec.min is not None:
                lo = pc.less_equal(col, spec.min) if spec.min_exclusive else pc.less(col, spec.min - spec.atol)
                bad = lo
            if spec.max is not None:
                hi = pc.greater_equal(col, spec.max) if spec.max_exclusive else pc.greater(col, spec.max + spec.atol)
                bad = hi if bad is None else pc.or_kleene(bad, hi)
            rows, n = rows_of(bad)
            if n:
                rng = (("(" if spec.min_exclusive or spec.min is None else "[")
                       + ("-inf" if spec.min is None else f"{spec.min:g}") + ", "
                       + ("inf" if spec.max is None else f"{spec.max:g}")
                       + (")" if spec.max_exclusive or spec.max is None else "]"))
                sample = pc.filter(col, pc.fill_null(bad, False))[0].as_py()
                ctx.add("out_of_range", f"{n} value(s) outside {rng} (e.g. {sample!r})",
                        file=file, table=schema.name, column=spec.name, rows=rows, count=n)


def check_keys(t: pa.Table, schema: TableSchema, ctx: Collector, file: str = "") -> None:
    """Primary-key and unique-set duplicates. Key columns absent from the table are left out of the key."""
    sets = []
    if schema.primary_key:
        sets.append(("primary key", tuple(c for c in schema.primary_key if c in t.column_names)))
    for u in schema.unique:
        sets.append(("unique set", tuple(c for c in u if c in t.column_names)))
    for label, cols in sets:
        if not cols or t.num_rows == 0:
            continue
        g = t.select(list(cols)).group_by(list(cols)).aggregate([(cols[0], "count", pc.CountOptions(mode="all"))])
        dup = g.filter(pc.greater(g.column(f"{cols[0]}_count"), 1))
        if dup.num_rows:
            ex = dup.slice(0, 1).to_pylist()[0]
            ex.pop(f"{cols[0]}_count", None)
            ctx.add("duplicate_key", f"{dup.num_rows} {label} value(s) of {list(cols)} occur more than once "
                    f"(e.g. {ex})", file=file, table=schema.name, column=",".join(cols), count=dup.num_rows)


def check_reference(child: pa.ChunkedArray, parent_values, ctx: Collector, *,
                    table: str, column: str, target: str, file: str = "") -> None:
    """Every non-null child value must occur in ``parent_values`` (any iterable or Arrow array)."""
    if isinstance(parent_values, (pa.Array, pa.ChunkedArray)):
        parent_values = parent_values.to_pylist()
    value_set = pa.array([v for v in parent_values if v is not None], type=child.type)
    bad = pc.and_(pc.invert(pc.is_in(child, value_set=value_set)), pc.is_valid(child))
    rows, n = rows_of(bad)
    if n:
        missing = sorted({str(v) for v in pc.filter(child, pc.fill_null(bad, False)).to_pylist()})
        ctx.add("dangling_reference", f"{n} row(s) reference {target} values that do not exist: "
                f"{', '.join(missing[:MAX_ROWS_REPORTED])}{' ...' if len(missing) > MAX_ROWS_REPORTED else ''}",
                file=file, table=table, column=column, rows=rows, count=n)


def _csv_field(value: str | None) -> str:
    """One CSV cell in the SPEC.md section 2.2 dialect: null is the empty cell; a value is quoted only when it
    must be (an empty string, or one containing a comma, a double quote or a line break)."""
    if value is None:
        return ""
    if value == "" or any(c in value for c in ',"\r\n'):
        return '"' + value.replace('"', '""') + '"'
    return value


def _csv_bytes(t: pa.Table) -> bytes:
    """Render a table as CSV text. Values are formatted by Arrow's own cast to string (the same text Arrow's
    CSV writer produces), so numbers, booleans and instants round-trip exactly."""
    cols = [pc.cast(t.column(i), pa.string()).to_pylist() for i in range(t.num_columns)]
    lines = [",".join(_csv_field(n) for n in t.column_names)]
    lines += [",".join(_csv_field(col[r]) for col in cols) for r in range(t.num_rows)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_table(t: pa.Table, path: Path) -> None:
    """Write a table by extension (.parquet / .arrow / .csv). CSV (SPEC.md section 2.2): one header row,
    empty cell = null, booleans as ``true``/``false``, quotes only where needed."""
    path = Path(path)
    if path.name.endswith(".parquet"):
        pq.write_table(t, path)
    elif path.name.endswith(".arrow"):
        with pa.OSFile(str(path), "wb") as sink, pa.ipc.new_file(sink, t.schema) as w:
            w.write_table(t)
    elif path.name.endswith(".csv"):
        path.write_bytes(_csv_bytes(t))
    else:
        raise ValueError(f"unsupported table extension: {path}")
