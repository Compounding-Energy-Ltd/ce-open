# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The ce-genx-import command line."""

from __future__ import annotations

import subprocess
import sys
import tomllib

from ce_genx.cli import main


def test_cli_imports_case(modular_case, tmp_path, capsys):
    out = tmp_path / "bundle"
    code = main([str(modular_case), str(out), "--csv", "--year", "2035", "--currency", "GBP",
                 "--network", "dc", "--start", "2019-01-01T00:00:00Z", "--tech",
                 "z1_solar_photovoltaic=solar_tracking", "--name", "toy"])
    assert code == 0
    text = capsys.readouterr().out
    assert "mapping notes" in text and "candidate_storage_efficiency_dropped" in text
    assert (out / "lines.csv").exists() and not (out / "hvdc.csv").exists()
    desc = tomllib.loads((out / "bundle.toml").read_text())
    assert desc["money"]["currency"] == "GBP"
    assert desc["time"]["investment_years"] == [2035]
    assert desc["bundle"]["name"] == "toy"
    assert "solar_tracking" in (out / "generators.csv").read_text()


def test_cli_error_exit(tmp_path, capsys):
    code = main([str(tmp_path / "missing"), str(tmp_path / "out")])
    assert code == 2
    assert "error" in capsys.readouterr().err


def test_module_entry_point(modular_case, tmp_path):
    out = tmp_path / "bundle"
    run = subprocess.run([sys.executable, "-m", "ce_genx", str(modular_case), str(out)],
                         capture_output=True, text=True, env={"PYTHONPATH": str(_src())})
    assert run.returncode == 0, run.stderr
    assert (out / "bundle.toml").exists()


def _src():
    from pathlib import Path

    return Path(__file__).resolve().parents[1] / "src"
