# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The committed example case stays in step with the sample generator, and the example script runs."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ce_genx.samples import write_sample_case

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _files(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def test_committed_sample_case_is_current(tmp_path):
    fresh = write_sample_case(tmp_path / "sample_case", "modular")
    assert _files(EXAMPLES / "sample_case") == _files(fresh), (
        "examples/sample_case is stale: regenerate it with "
        "`python -m ce_genx.samples examples/sample_case modular`"
    )


def test_example_script_runs(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location("import_sample", EXAMPLES / "import_sample.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = module.main(str(tmp_path / "bundle"))
    assert (out / "bundle.toml").exists()
    assert "self-check: clean" in capsys.readouterr().out
