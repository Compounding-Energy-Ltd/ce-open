# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The examples run end to end on synthetic data."""

from __future__ import annotations

import runpy
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_quickstart(base_paths, capsys):
    mod = runpy.run_path(str(EXAMPLES / "quickstart.py"))
    run = mod["main"](str(base_paths[0]), str(base_paths[1]))
    out = capsys.readouterr().out
    assert "synthetic_base" in out and "Capture prices" in out
    assert run.stamps.certified()


def test_quickstart_without_arguments(capsys):
    runpy.run_path(str(EXAMPLES / "quickstart.py"))["main"]()
    assert "Congestion rent" in capsys.readouterr().out


def test_compare_example(base_paths, alt_paths, capsys):
    cmp = runpy.run_path(str(EXAMPLES / "compare_runs.py"))["main"](str(base_paths[0]), str(alt_paths[0]))
    out = capsys.readouterr().out
    assert "total cost delta" in out and "No comparability warnings" in out
    assert cmp.comparable
