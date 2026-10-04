# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The GenX -> CE mapping, value by value, on the synthetic sample case."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from ce_genx import ImportOptions, convert, read_case
from conftest import set_setting

NG_MEAN = (3.0 * 6570 + 5.0 * 2190) / 8760  # time-weighted mean gas price = 3.5
PEAK = 1300.0 + 720.0


def _bundle(root, **kw):
    return convert(read_case(root), ImportOptions(**kw))


def _row(df, col, key):
    hit = df[df[col] == key]
    assert len(hit) == 1, f"{key} not found once in {col}"
    return hit.iloc[0]


@pytest.fixture
def bundle(modular_case):
    return _bundle(modular_case)


def test_tables_present(bundle):
    assert set(bundle.tables) == {"buses", "hvdc", "generators", "storage", "candidates", "demand", "availability"}


def test_buses_from_zones(bundle):
    b = bundle.tables["buses"]
    assert list(b["id"]) == [1, 2]
    assert list(b["name"]) == ["z1", "z2"]
    assert list(b["zone"]) == ["z1", "z2"]


def test_transport_lines_become_hvdc(bundle):
    h = bundle.tables["hvdc"].iloc[0]
    assert (h["from_bus"], h["to_bus"]) == (1, 2)
    assert h["name"] == "z1_to_z2"
    assert h["existing_mw"] == 400.0
    assert h["candidate_max_mw"] == 500.0
    assert h["capex_per_mw_yr"] == 20000.0
    assert h["converter_efficiency"] == pytest.approx(0.98)
    assert "transport_as_hvdc" in bundle.report.codes()


def test_dc_lines(modular_case):
    b = _bundle(modular_case, network="dc")
    assert "hvdc" not in b.tables
    line = b.tables["lines"].iloc[0]
    assert line["susceptance_pu"] == pytest.approx(345.0**2 / (100.0 * 30.0))
    assert line["rating_mw"] == 400.0 and bool(line["candidate"])
    assert line["delta_rating_mw"] == 500.0
    assert line["capex_per_yr"] == pytest.approx(20000.0 * 500.0)
    assert line["loss_frac"] == pytest.approx(0.02)
    assert line["length_km"] == pytest.approx(160.9344)
    assert "line_reinforcement_lumpy" in b.report.codes()


def test_dc_needs_reactance(modular_case):
    path = modular_case / "system" / "Network.csv"
    df = pd.read_csv(path).drop(columns=["Line_Reactance_Ohms"])
    df.to_csv(path, index=False)
    with pytest.raises(ValueError, match="Line_Reactance_Ohms"):
        _bundle(modular_case, network="dc")


def test_network_switches(modular_case):
    set_setting(modular_case, "NetworkExpansion", 0)
    set_setting(modular_case, "Trans_Loss_Segments", 0)
    b = _bundle(modular_case)
    h = b.tables["hvdc"].iloc[0]
    assert h["candidate_max_mw"] == 0.0 and h["converter_efficiency"] == 1.0
    assert {"network_expansion_disabled", "line_losses_disabled"} <= b.report.codes()


def test_thermal_existing(bundle):
    g = _row(bundle.tables["generators"], "name", "z1_natural_gas_combined_cycle")
    assert g["kind"] == "thermal" and g["technology"] == "gas_cc" and g["bus"] == 1
    assert g["existing_mw"] == 300.0
    assert g["varcost_per_mwh"] == pytest.approx(3.5 + 7.0 * NG_MEAN)
    assert g["co2_t_per_mwh"] == pytest.approx(7.0 * 0.05306)
    assert g["fom_per_mw_yr"] == 11000.0
    assert g["pmin_frac"] == 0.4 and g["ramp_frac_per_h"] == 0.64
    assert (g["min_up_h"], g["min_down_h"]) == (6, 6)
    assert g["startup_cost"] == pytest.approx((90.0 + 2.0 * NG_MEAN) * 300.0)
    assert g["noload_cost_per_h"] == 0.0
    assert g["firm_capacity_frac"] == pytest.approx(0.93)
    assert bool(g["retirable"])


def test_thermal_without_commitment(bundle):
    g = _row(bundle.tables["generators"], "name", "z2_natural_gas_combustion_turbine")
    assert g["technology"] == "gas_ct"
    assert (g["min_up_h"], g["min_down_h"], g["startup_cost"]) == (0, 0, 0.0)
    assert g["ramp_frac_per_h"] == 0.8  # the tighter of 1.0 up / 0.8 down
    assert "ramp_symmetric" in bundle.report.codes()
    assert "z2_natural_gas_combustion_turbine" in bundle.report.get("thermal_availability_dropped").items


def test_thermal_candidates(bundle):
    c = bundle.tables["candidates"]
    ccgt = _row(c, "candidate_id", "z1_natural_gas_combined_cycle_new")
    assert ccgt["kind"] == "thermal" and ccgt["max_mw"] == pytest.approx(2 * PEAK)
    assert ccgt["capex_per_mw_yr"] == 65000.0 and ccgt["fom_per_mw_yr"] == 11000.0
    ct = _row(c, "candidate_id", "z2_natural_gas_combustion_turbine_new")
    assert ct["max_mw"] == 300.0  # Max_Cap_MW 400 - existing 100
    assert "z1_natural_gas_combined_cycle" in bundle.report.get("unbounded_build_capped").items
    assert "integer_build" not in c.columns  # UCommit = 2 in the sample


def test_integer_builds_under_integer_commitment(modular_case):
    set_setting(modular_case, "UCommit", 1)
    c = _bundle(modular_case).tables["candidates"]
    ccgt = _row(c, "candidate_id", "z1_natural_gas_combined_cycle_new")
    assert bool(ccgt["integer_build"]) and ccgt["unit_size_mw"] == 250.0
    ct = _row(c, "candidate_id", "z2_natural_gas_combustion_turbine_new")
    assert not bool(ct["integer_build"])  # Model 2: no commitment


def test_unbounded_override(modular_case):
    c = _bundle(modular_case, unbounded_cap_mw=1234.0).tables["candidates"]
    assert _row(c, "candidate_id", "z1_solar_photovoltaic_new")["max_mw"] == 1234.0


def test_renewables(bundle):
    g = bundle.tables["generators"]
    techs = dict(zip(g["name"], g["technology"]))
    assert techs["z1_solar_photovoltaic"] == "solar_pv"
    assert techs["z2_onshore_wind_turbine"] == "wind_onshore"
    assert techs["z2_conventional_hydroelectric"] == "hydro_reservoir"
    assert techs["z1_small_hydroelectric"] == "hydro_ror"
    assert "z2_offshore_wind_turbine" not in techs  # no existing capacity
    c = bundle.tables["candidates"]
    off = _row(c, "candidate_id", "z2_offshore_wind_turbine_new")
    assert off["technology"] == "wind_offshore" and off["max_mw"] == 500.0
    assert off["cf_mean"] == pytest.approx((0.525 * 6570 + 0.775 * 2190) / 8760)
    wind = _row(c, "candidate_id", "z2_onshore_wind_turbine_new")
    assert wind["max_mw"] == 800.0
    assert "z1_flexible_demand" not in set(g["name"])
    assert bundle.report.get("resource_kind_dropped").items == ["z1_flexible_demand (flex)"]


def test_availability(bundle):
    a = bundle.tables["availability"]
    assert set(a["site_name"]) == {
        "z1_solar_photovoltaic",
        "z1_solar_photovoltaic_new",
        "z2_onshore_wind_turbine",
        "z2_onshore_wind_turbine_new",
        "z2_offshore_wind_turbine_new",
        "z2_conventional_hydroelectric",
        "z1_small_hydroelectric",
    }
    solar = a[a["site_name"] == "z1_solar_photovoltaic"].sort_values(["block", "period"])
    assert list(solar["cf"]) == [0.0, 0.45, 0.85, 0.30, 0.0, 0.25, 0.55, 0.10]
    assert list(solar["block"]) == [1] * 4 + [2] * 4
    assert list(solar["period"]) == [1, 2, 3, 4] * 2
    hydro = a[a["site_name"] == "z2_conventional_hydroelectric"]
    assert hydro["cf"].max() == 1.0  # the 1.10 inflow is clipped
    assert any("z2_conventional_hydroelectric" in i for i in bundle.report.get("availability_clipped").items)


def test_storage(bundle):
    s = _row(bundle.tables["storage"], "name", "z1_battery")
    assert s["existing_mw"] == 20.0 and s["duration_h"] == 4.0
    assert (s["eta_charge"], s["eta_discharge"]) == (0.92, 0.92)
    assert s["varcost_per_mwh"] == pytest.approx(0.15 + 0.15 / 0.92**2)
    c = bundle.tables["candidates"]
    bat = _row(c, "candidate_id", "z1_battery_new")
    assert bat["duration_h"] == 4.0 and np.isnan(bat["max_mwh"])
    assert bat["capex_per_mw_yr"] == pytest.approx(15000.0 + 4 * 20000.0)
    assert bat["fom_per_mw_yr"] == pytest.approx(4000.0 + 4 * 1000.0)
    h2 = _row(c, "candidate_id", "z2_hydrogen_storage_new")
    assert np.isnan(h2["duration_h"])
    assert h2["max_mw"] == 300.0 and h2["max_mwh"] == pytest.approx(300.0 * 200.0)
    assert h2["capex_per_mw_yr"] == pytest.approx(30000.0 + 20000.0)  # power + charge capacity
    assert h2["capex_per_mwh_yr"] == 500.0
    codes = bundle.report.codes()
    assert {"asymmetric_charge_capacity", "storage_duration_bounds_dropped", "long_duration_storage"} <= codes
    assert bundle.descriptor["x_genx"]["long_duration_storage"] == ["z2_hydrogen_storage"]


def test_demand_long_format(bundle):
    d = bundle.tables["demand"]
    assert len(d) == 2 * 8
    z1 = d[d["bus"] == 1].sort_values(["block", "period"])
    assert list(z1["mw"]) == [900.0, 1000.0, 1150.0, 1050.0, 1100.0, 1250.0, 1300.0, 1200.0]
    assert set(d["scenario"]) == {1}


def test_descriptor(bundle):
    d = bundle.descriptor
    assert d["format"] == {"name": "ce-bundle", "version": "1.0"}
    assert d["money"] == {"currency": "USD", "dollar_year": 2030}
    assert d["time"]["investment_years"] == [2030]
    blocks = d["time"]["blocks"]
    assert [b["weight_h"] for b in blocks] == [6570.0, 2190.0]
    assert all(b["kind"] == "representative" and b["nperiods"] == 4 for b in blocks)
    assert d["system"]["voll_per_mwh"] == 50000.0
    assert d["system"]["planning_reserve_margin"] == pytest.approx(0.156)
    # both zones are members, so the cap is system-wide: no `zones` key
    assert d["x_policy"] == [{"name": "genx_co2_cap_1", "kind": "emission_cap_t", "pollutant": "co2",
                              "value": 2.0e6}]
    assert "co2_cap" not in d["x_genx"]


def test_options_reach_descriptor(modular_case):
    b = _bundle(modular_case, investment_year=2035, currency="EUR", dollar_year=2024, name="toy")
    assert b.descriptor["money"] == {"currency": "EUR", "dollar_year": 2024}
    assert b.descriptor["time"]["investment_years"] == [2035]
    assert b.descriptor["bundle"]["name"] == "toy"
    assert "dollar_year_assumed" not in b.report.codes()


def test_reserve_margin_disabled(modular_case):
    set_setting(modular_case, "CapacityReserveMargin", 0)
    b = _bundle(modular_case)
    assert "planning_reserve_margin" not in b.descriptor["system"]
    assert "firm_capacity_frac" not in b.tables["generators"].columns
    assert {"no_reserve_margin", "reserve_margin_disabled"} <= b.report.codes()


def test_rate_based_co2_cap_is_dropped(modular_case):
    set_setting(modular_case, "CO2Cap", 2)
    b = _bundle(modular_case)
    assert "x_policy" not in b.descriptor
    assert "co2_rate_cap_dropped" in b.report.codes()


def test_technology_override_and_unknown_vre(modular_case):
    path = modular_case / "resources" / "Vre.csv"
    df = pd.read_csv(path)
    df.loc[0, "Resource"] = "z1_mystery_resource"
    df.to_csv(path, index=False)
    var = modular_case / "system" / "Generators_variability.csv"
    v = pd.read_csv(var).rename(columns={"z1_solar_photovoltaic": "z1_mystery_resource"})
    v.to_csv(var, index=False)
    with pytest.raises(ValueError, match="z1_mystery_resource"):
        _bundle(modular_case)
    b = _bundle(modular_case, technologies={"z1_mystery_resource": "solar"})
    assert _row(b.tables["generators"], "name", "z1_mystery_resource")["technology"] == "solar"


def test_full_year_block(tmp_path):
    from ce_genx.samples import write_sample_case

    root = write_sample_case(tmp_path / "c", "modular")
    path = root / "system" / "Demand_data.csv"
    df = pd.read_csv(path)
    df.loc[0, "Rep_Periods"] = 1
    df.loc[0, "Timesteps_per_Rep_Period"] = 8
    df.loc[0, "Sub_Weights"] = 8.0
    df.loc[1, "Sub_Weights"] = np.nan
    df.to_csv(path, index=False)
    start = dt.datetime(2019, 1, 1, tzinfo=dt.timezone.utc)
    b = _bundle(root, start=start)
    (block,) = b.descriptor["time"]["blocks"]
    assert block["kind"] == "chronological" and block["start"] == start and block["nperiods"] == 8
    b = _bundle(root)
    assert b.descriptor["time"]["blocks"][0]["kind"] == "representative"
    assert "full_year_as_representative" in b.report.codes()


def test_layouts_give_the_same_bundle(modular_case, legacy_case):
    a, b = _bundle(modular_case), _bundle(legacy_case)
    assert set(a.tables) == set(b.tables)
    for name in a.tables:
        left = a.tables[name].reset_index(drop=True)
        right = b.tables[name][left.columns].reset_index(drop=True)
        pd.testing.assert_frame_equal(left, right, check_dtype=False, obj=name)
    assert a.report.codes() == b.report.codes()
