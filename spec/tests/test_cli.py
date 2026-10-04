# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The command line."""

from __future__ import annotations

from conftest import EXAMPLES, SPEC_ROOT, table_file
from ce_spec.__main__ import main


def test_validate_commands_exit_codes(bundle, run, capsys):
    assert main(["validate-bundle", str(bundle)]) == 0
    assert main(["validate-results", str(run), "--bundle", str(bundle), "--strict"]) == 0
    table_file(bundle, "demand").unlink()
    assert main(["validate-bundle", str(bundle)]) == 1
    out = capsys.readouterr().out
    assert "INVALID" in out and "missing_table" in out


def test_arrow_schema_command(capsys):
    assert main(["arrow-schema", "results", "lmp"]) == 0
    out = capsys.readouterr().out
    assert "lmp_per_mwh: double" in out and "currency/MWh" in out


def test_render_docs_matches_committed(capsys):
    assert main(["render-docs"]) == 0
    assert capsys.readouterr().out == (SPEC_ROOT / "TABLES.md").read_text(encoding="utf-8")


def test_make_toy(tmp_path, capsys):
    assert main(["make-toy", str(tmp_path), "--format", "arrow"]) == 0
    assert (tmp_path / "toy3" / "buses.arrow").is_file()
    assert main(["validate-bundle", str(tmp_path / "toy3")]) == 0


def test_example_script_runs():
    import runpy
    ns = runpy.run_path(str(EXAMPLES / "python_api.py"))
    assert ns["main"]() == 0


def test_committed_examples_equal_the_generator_output(tmp_path):
    """examples/toy3 and examples/toy3_run are exactly what `make-toy --format csv` writes."""
    assert main(["make-toy", str(tmp_path), "--format", "csv"]) == 0
    for d in ("toy3", "toy3_run"):
        made = {p.name: p.read_bytes() for p in (tmp_path / d).iterdir()}
        committed = {p.name: p.read_bytes() for p in (EXAMPLES / d).iterdir()}
        assert made.keys() == committed.keys(), d
        for name in made:
            assert made[name] == committed[name], f"{d}/{name} differs from the generator output"


def test_every_example_sidecar_states_a_licence():
    from ce_spec.prov import read_prov

    sidecars = sorted(EXAMPLES.rglob("*.prov"))
    assert sidecars
    for p in sidecars:
        assert read_prov(p).get("license"), f"{p.relative_to(EXAMPLES)} has no license="


def test_csv_writer_quotes_only_where_needed(tmp_path):
    import pyarrow as pa

    from ce_spec.tables import read_raw, write_table

    t = pa.table({"name": ["plain", "a,b", 'say "hi"', "", None], "x": [1.5, None, 3.0, 4.0, 5.0],
                  "flag": [True, False, None, True, False]})
    p = tmp_path / "t.csv"
    write_table(t, p)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "name,x,flag"
    assert lines[1:] == ["plain,1.5,true", '"a,b",,false', '"say ""hi""",3,', '"",4,true', ",5,false"]
    back = read_raw(p)
    assert back.column("name").to_pylist()[:3] == ["plain", "a,b", 'say "hi"']


def test_example_csvs_carry_no_needless_quotes():
    for p in sorted(EXAMPLES.rglob("*.csv")):
        for line in p.read_text(encoding="utf-8").splitlines():
            assert '"' not in line, f"{p.relative_to(EXAMPLES)}: {line}"
