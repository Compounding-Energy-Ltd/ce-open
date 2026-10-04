# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Fixtures: synthetic run directories generated fresh for each test session."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ce_results import Run
from ce_results import _toml
from ce_results.synthetic import write_synthetic_run


@pytest.fixture(scope="session")
def runs_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("runs")


@pytest.fixture(scope="session")
def base_paths(runs_dir):
    """(run_dir, bundle_dir) of the 1.0-layout base variant."""
    return write_synthetic_run(runs_dir / "base", variant="base")


@pytest.fixture()
def base_run(base_paths) -> Run:
    return Run(base_paths[0], bundle=base_paths[1])


@pytest.fixture(scope="session")
def alt_paths(runs_dir):
    return write_synthetic_run(runs_dir / "alt", variant="alt")


@pytest.fixture(scope="session")
def chrono_paths(runs_dir):
    return write_synthetic_run(runs_dir / "chrono", chronological=True)


@pytest.fixture(scope="session")
def legacy_path(runs_dir) -> Path:
    return write_synthetic_run(runs_dir / "legacy", layout="legacy")[0]


@pytest.fixture()
def legacy_run(legacy_path) -> Run:
    return Run(legacy_path)


def write_tiny_run(d: Path) -> tuple[Path, Path]:
    """A hand-sized 1.0 run whose derived numbers are worked out in the tests.

    One investment year, one scenario, one weather year, one representative
    block of two periods lasting 1 h and 3 h with an annual weight of 8 h, so
    each modelled hour stands for 2 h: annual_h = [2, 6].
    Buses A (reference) and B; one line L from A to B rated 100 MW.
    """
    run, bundle = d / "run", d / "bundle"
    run.mkdir(parents=True)
    bundle.mkdir(parents=True)
    key = dict(year=[2030, 2030], scenario=[1, 1], weather=[2012, 2012], block=[1, 1], period=[1, 2])

    def frame(**cols):
        return pd.DataFrame({**key, **cols})

    _toml.dump({
        "format": {"name": "ce-results", "version": "1.0"},
        "run": {"name": "tiny", "mode": "cem"},
        "money": {"currency": "GBP", "dollar_year": 2025},
        "time": {"timezone": "UTC", "hour_convention": "hour_ending", "investment_years": [2030],
                 "weather_years": [2012],
                 "blocks": [{"id": 1, "nperiods": 2, "dt_h": [1.0, 3.0], "weight_h": 8.0,
                             "kind": "representative"}]},
    }, run / "manifest.toml")
    _toml.dump({"cost": {"total": 100.0, "investment_capex": 40.0, "operational_opex": 60.0}},
               run / "summary.toml")
    pd.concat([
        frame(bus="A", lmp_per_mwh=[10.0, 20.0], lmp_energy=[10.0, 20.0],
              lmp_congestion=[0.0, 0.0], lmp_loss=[0.0, 0.0]),
        frame(bus="B", lmp_per_mwh=[30.0, 20.0], lmp_energy=[10.0, 20.0],
              lmp_congestion=[15.0, 0.0], lmp_loss=[5.0, 0.0]),
    ]).to_parquet(run / "lmp.parquet", index=False)
    frame(line="L", flow_mw=[100.0, -50.0]).to_parquet(run / "flows.parquet", index=False)
    pd.concat([
        frame(source_type="thermal", component_name="G", technology="ccgt", bus="A", dispatch_mw=[50.0, 100.0]),
        frame(source_type="renewable", component_name="W", technology="wind", bus="B", dispatch_mw=[40.0, 10.0]),
    ]).to_parquet(run / "generation.parquet", index=False)
    pd.DataFrame({"year": [2030], "component": ["renewable"], "name": ["W"], "technology": ["wind"],
                  "bus": ["B"], "built_mw": [60.0]}).to_parquet(run / "investment.parquet", index=False)

    pd.DataFrame({"id": [1, 2], "name": ["A", "B"]}).to_parquet(bundle / "buses.parquet", index=False)
    pd.DataFrame({"id": [1], "name": ["L"], "from_bus": [1], "to_bus": [2], "rating_mw": [100.0]}) \
        .to_parquet(bundle / "lines.parquet", index=False)
    pd.DataFrame({"site_name": ["W", "W"], "scenario": [1, 1], "block": [1, 1], "period": [1, 2],
                  "cf": [0.5, 0.2]}).to_parquet(bundle / "availability.parquet", index=False)
    return run, bundle


@pytest.fixture()
def tiny_paths(tmp_path):
    return write_tiny_run(tmp_path / "tiny")


@pytest.fixture()
def tiny_run(tiny_paths) -> Run:
    return Run(tiny_paths[0], bundle=tiny_paths[1])
