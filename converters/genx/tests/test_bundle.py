# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Writing bundles to disk and checking them."""

from __future__ import annotations

import tomllib

import pandas as pd
import pyarrow.parquet as pq
import pytest

from ce_genx import check_bundle, import_case, read_tables
from ce_genx.bundle import REPORT_FILE


@pytest.mark.parametrize("fmt", ["parquet", "csv"])
def test_write_and_check(modular_case, tmp_path, fmt):
    out = tmp_path / "bundle"
    bundle = import_case(modular_case, out, fmt=fmt)
    files = {p.name for p in out.iterdir()}
    expected = {f"{t}.{fmt}" for t in bundle.tables} | {"bundle.toml", REPORT_FILE}
    assert files == expected
    assert not any(name.endswith(".json") for name in files)
    assert check_bundle(out) == []

    desc = tomllib.loads((out / "bundle.toml").read_text())
    assert desc["format"]["name"] == "ce-bundle"
    assert len(desc["time"]["blocks"]) == 2

    report = tomllib.loads((out / REPORT_FILE).read_text())
    assert report["summary"]["source_format"] == "GenX"
    assert {n["code"] for n in report["note"]} == bundle.report.codes()

    back = read_tables(out)
    for name, df in bundle.tables.items():
        assert len(back[name]) == len(df), name


def test_parquet_types(modular_case, tmp_path):
    out = tmp_path / "bundle"
    import_case(modular_case, out)
    schema = pq.read_schema(out / "availability.parquet")
    assert str(schema.field("scenario").type) == "int16"
    assert str(schema.field("block").type) == "int16"
    assert str(schema.field("period").type) == "int32"
    assert str(schema.field("cf").type) == "float"
    gens = pq.read_schema(out / "generators.parquet")
    assert str(gens.field("id").type) == "int32"
    assert str(gens.field("min_up_h").type) == "int16"
    # renewable rows carry no commitment data: written as null, not as a number
    g = pd.read_parquet(out / "generators.parquet")
    assert g.loc[g["kind"] == "renewable", "min_up_h"].isna().all()


def test_refuses_non_empty_directory(modular_case, tmp_path):
    out = tmp_path / "bundle"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    with pytest.raises(FileExistsError):
        import_case(modular_case, out)
    import_case(modular_case, out, overwrite=True)
    assert (out / "keep.txt").exists()


def test_overwrite_replaces_other_format(modular_case, tmp_path):
    out = tmp_path / "bundle"
    import_case(modular_case, out, fmt="csv")
    import_case(modular_case, out, fmt="parquet", overwrite=True)
    assert not list(out.glob("*.csv"))
    assert check_bundle(out) == []


def test_check_finds_problems(modular_case, tmp_path):
    out = tmp_path / "bundle"
    import_case(modular_case, out, fmt="csv")
    a = pd.read_csv(out / "availability.csv")
    a = a[~((a["site_name"] == "z1_solar_photovoltaic") & (a["period"] == 3))]
    a.to_csv(out / "availability.csv", index=False)
    g = pd.read_csv(out / "generators.csv")
    g.loc[0, "bus"] = 99
    g.to_csv(out / "generators.csv", index=False)
    (out / "extra.json").write_text("{}")
    problems = "\n".join(check_bundle(out))
    assert "json" in problems
    assert "unknown buses [99]" in problems
    assert "not periods 1..4" in problems
