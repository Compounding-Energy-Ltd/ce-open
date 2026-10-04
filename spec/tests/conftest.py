# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures: synthetic toy bundles/results and helpers that mutate them."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Callable

import pytest

SPEC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPEC_ROOT / "src"))

import pyarrow as pa  # noqa: E402

from ce_spec.prov import format_prov, read_prov, sha256_file  # noqa: E402
from ce_spec.schema import table_schema  # noqa: E402
from ce_spec.tables import read_raw, write_table  # noqa: E402
from ce_spec.toy import write_toy_bundle, write_toy_results  # noqa: E402

EXAMPLES = SPEC_ROOT / "examples"


@pytest.fixture(scope="session")
def _toy_cache(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("toy_cache")
    for fmt in ("parquet", "csv", "arrow"):
        write_toy_bundle(root / fmt / "toy3", fmt)
        write_toy_results(root / fmt / "toy3_run", fmt)
    return root


def _copy(cache: Path, fmt: str, name: str, dest: Path) -> Path:
    out = dest / f"{name}_{fmt}"
    shutil.copytree(cache / fmt / name, out)
    return out


@pytest.fixture
def bundle(_toy_cache, tmp_path) -> Path:
    """A fresh, valid Parquet toy bundle the test may modify."""
    return _copy(_toy_cache, "parquet", "toy3", tmp_path)


@pytest.fixture
def csv_bundle(_toy_cache, tmp_path) -> Path:
    return _copy(_toy_cache, "csv", "toy3", tmp_path)


@pytest.fixture
def run(_toy_cache, tmp_path) -> Path:
    """A fresh, valid Parquet toy results directory the test may modify."""
    return _copy(_toy_cache, "parquet", "toy3_run", tmp_path)


@pytest.fixture
def run_and_bundle(_toy_cache, tmp_path) -> tuple[Path, Path]:
    return _copy(_toy_cache, "parquet", "toy3_run", tmp_path), _copy(_toy_cache, "parquet", "toy3", tmp_path)


@pytest.fixture(params=["parquet", "csv", "arrow"])
def any_format(request, _toy_cache, tmp_path) -> tuple[Path, Path]:
    fmt = request.param
    return _copy(_toy_cache, fmt, "toy3", tmp_path), _copy(_toy_cache, fmt, "toy3_run", tmp_path)


def table_file(root: Path, name: str) -> Path:
    hits = [p for p in root.iterdir() if p.name.split(".")[0] == name and not p.name.endswith(".prov")]
    assert len(hits) == 1, f"{name} in {root}: {hits}"
    return hits[0]


def refresh_prov(root: Path) -> None:
    """Re-stamp sha256 / rows of every sidecar after a mutation, so a test sees only the issue it planted."""
    for sc in root.glob("*.prov"):
        target = root / sc.name[: -len(".prov")]
        if not target.is_file():
            continue
        prov = read_prov(sc)
        vals = dict(prov.values)
        if "sha256" in vals:
            vals["sha256"] = sha256_file(target)
        if "rows" in vals:
            vals["rows"] = read_raw(target).num_rows
        sc.write_text(format_prov(vals), encoding="utf-8")


def mutate(root: Path, name: str, fn: Callable[[list[dict]], list[dict]], *, scope: str = "bundle",
           types: dict[str, pa.DataType] | None = None) -> None:
    """Rewrite one table through ``fn`` (rows as dicts), keeping canonical types for known columns."""
    p = table_file(root, name)
    rows = fn(read_raw(p).to_pylist()) if not p.name.endswith(".csv") else None
    if rows is None:
        raise NotImplementedError("mutate() works on parquet/arrow fixtures")
    ts = table_schema(scope, name)
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    data = {}
    for c in cols:
        spec = ts.column(c)
        t = (types or {}).get(c) or (spec.arrow_type if spec is not None else None)
        data[c] = pa.array([r.get(c) for r in rows], type=t)
    write_table(pa.table(data), p)
    refresh_prov(root)


def edit_text(path: Path, old: str, new: str, count: int = 1) -> None:
    s = path.read_text(encoding="utf-8")
    assert old in s, f"{old!r} not in {path.name}"
    path.write_text(s.replace(old, new, count), encoding="utf-8")


def errors(report) -> set[str]:
    return report.codes("error")


def warnings(report) -> set[str]:
    return report.codes("warning")
