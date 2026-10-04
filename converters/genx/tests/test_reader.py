# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Reading GenX case folders in both input layouts."""

from __future__ import annotations

import shutil
import textwrap

import numpy as np
import pytest

from ce_genx.genx import detect_layout, read_case, read_settings


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip())


@pytest.fixture
def tiny_legacy(tmp_path):
    """A one-zone, three-hour legacy case written by hand (no settings file, no network)."""
    root = tmp_path / "tiny"
    _write(
        root / "Generators_data.csv",
        """
        Resource,Zone,THERM,VRE,STOR,New_Build,Existing_Cap_MW,Max_Cap_MW,Inv_Cost_per_MWyr,Fixed_OM_Cost_per_MWyr,Var_OM_Cost_per_MWh,Heat_Rate_MMBTU_per_MWh,Fuel,Existing_Cap_MWh,Eff_Up,Eff_Down
        gas,1,2,0,0,0,500,-1,0,10000,2.0,8.0,ng,0,0,0
        solar_pv,1,0,1,0,1,0,300,40000,10000,0,0,None,0,0,0
        battery,1,0,0,1,-1,50,-1,0,0,0,0,None,100,0.9,0.9
        """,
    )
    _write(
        root / "Load_data.csv",
        """
        Voll,Demand_Segment,Cost_of_Demand_Curtailment_per_MW,Max_Demand_Curtailment,Rep_Periods,Timesteps_per_Rep_Period,Sub_Weights,Time_Index,Load_MW_z1
        9000,1,1,1,1,3,8760,1,400
        ,,,,,,,2,450
        ,,,,,,,3,420
        """,
    )
    _write(
        root / "Generators_variability.csv",
        """
        Time_Index,gas,solar_pv,battery
        1,1,0.0,1
        2,1,0.6,1
        3,1,0.2,1
        """,
    )
    _write(
        root / "Fuels_data.csv",
        """
        Time_Index,ng,None
        0,0.05,0
        1,2,0
        2,2,0
        3,2,0
        """,
    )
    return root


def test_detect_layout(modular_case, legacy_case, tmp_path):
    assert detect_layout(modular_case) == "modular"
    assert detect_layout(legacy_case) == "legacy"
    with pytest.raises(FileNotFoundError):
        detect_layout(tmp_path)


def test_handwritten_legacy_case(tiny_legacy):
    case = read_case(tiny_legacy)
    assert case.layout == "legacy"
    assert case.settings == {}
    assert case.network is None
    assert case.zones == [1]
    assert list(case.resources["kind"]) == ["thermal", "vre", "storage"]
    assert list(case.resources["model"]) == [2, 1, 1]
    # legacy New_Build: 0 -> retire only, 1 -> build and retire, -1 -> neither
    assert list(case.resources["new_build"]) == [0, 1, 0]
    assert list(case.resources["can_retire"]) == [1, 1, 0]
    assert case.time.T == 3 and case.time.weights_h == [8760.0]
    assert case.voll == 9000
    assert case.fuel_co2 == {"ng": 0.05, "None": 0.0}


def test_resources_and_kinds(modular_case, legacy_case):
    for root in (modular_case, legacy_case):
        case = read_case(root)
        kinds = dict(zip(case.resources["resource"], case.resources["kind"]))
        assert kinds["z1_natural_gas_combined_cycle"] == "thermal"
        assert kinds["z2_hydrogen_storage"] == "storage"
        assert kinds["z2_conventional_hydroelectric"] == "hydro"
        assert kinds["z1_small_hydroelectric"] == "must_run"
        assert kinds["z1_flexible_demand"] == "flex"
        models = dict(zip(case.resources["resource"], case.resources["model"]))
        assert models["z2_natural_gas_combustion_turbine"] == 2
        assert models["z2_hydrogen_storage"] == 2
        # the reserve derating arrives as capres_1 in both layouts
        derate = dict(zip(case.resources["resource"], case.resources["capres_1"]))
        assert derate["z1_battery"] == pytest.approx(0.95)


def test_time_structure_and_series(modular_case):
    case = read_case(modular_case)
    assert (case.time.rep_periods, case.time.timesteps) == (2, 4)
    assert case.time.weights_h == [6570.0, 2190.0]
    w = case.time.hour_weights()
    assert w.sum() == pytest.approx(8760.0)
    assert np.allclose(w[:4], 6570 / 4) and np.allclose(w[4:], 2190 / 4)
    assert list(case.demand.columns) == [1, 2]
    assert case.demand[1].iloc[6] == 1300.0
    assert case.variability.shape == (8, 10)
    assert case.fuel_co2["NG"] == pytest.approx(0.05306)
    assert list(case.fuel_prices["NG"]) == [3.0] * 4 + [5.0] * 4


def test_network_both_forms(modular_case, legacy_case):
    for root in (modular_case, legacy_case):
        net = read_case(root).network
        assert len(net) == 1
        row = net.iloc[0]
        assert (row["start_zone"], row["end_zone"]) == (1, 2)
        assert row["line_max_flow_mw"] == 400.0


def test_policies_and_settings(modular_case):
    case = read_case(modular_case)
    assert case.setting("CO2Cap") == 1
    assert case.setting("NetworkExpansion") == 1
    assert set(case.policies) == {"co2_cap", "capacity_reserve_margin"}
    assert list(case.policies["co2_cap"]["zone"]) == [1, 2]


def test_read_settings_flat_yaml(tmp_path):
    p = tmp_path / "s.yml"
    p.write_text("A: 1\nB: 0.5  # comment\nC: true\nnested:\n  inner: 3\nD: 'text'\n# skipped: 9\n")
    s = read_settings(p)
    assert s == {"a": 1, "b": 0.5, "c": True, "d": "text"}


def test_missing_variability_rows(modular_case):
    path = modular_case / "system" / "Generators_variability.csv"
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n")
    with pytest.raises(ValueError, match="rows, expected 8"):
        read_case(modular_case)


def test_duplicate_resource_names(modular_case):
    path = modular_case / "resources" / "Vre.csv"
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines + [lines[1]]) + "\n")
    with pytest.raises(ValueError, match="duplicate resource names"):
        read_case(modular_case)


def test_multi_stage(tmp_path, modular_case):
    root = tmp_path / "multistage"
    for p in (1, 2):
        shutil.copytree(modular_case, root / "inputs" / f"inputs_p{p}")
    with pytest.raises(ValueError, match="multi-stage"):
        read_case(root)
    case = read_case(root, stage=2)
    assert case.root.name == "inputs_p2"
    with pytest.raises(FileNotFoundError):
        read_case(root, stage=3)
    with pytest.raises(ValueError, match="not a multi-stage case"):
        read_case(modular_case, stage=1)


def test_tdr_folder(modular_case):
    with pytest.raises(FileNotFoundError):
        read_case(modular_case, tdr=True)
    tdr = modular_case / "TDR_results"
    tdr.mkdir()
    for name in ("Demand_data.csv", "Generators_variability.csv", "Fuels_data.csv"):
        shutil.copy(modular_case / "system" / name, tdr / name)
    case = read_case(modular_case, tdr=True)
    assert case.time.T == 8
