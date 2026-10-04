# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""A tiny synthetic GenX-style case, in either input layout.

Every number here is invented for illustration and testing; it describes no
real system. The case has two zones joined by one line, two representative
periods of four hours (weighted 6570 h and 2190 h), and one resource of each
kind the importer handles:

======================================  ===========  =====================================
resource                                kind         exercises
======================================  ===========  =====================================
``z1_natural_gas_combined_cycle``       thermal      commitment, unbounded build, fuel cost
``z2_natural_gas_combustion_turbine``   thermal      no commitment, derate, unequal ramps
``z1_solar_photovoltaic``               VRE          existing + unbounded build
``z2_onshore_wind_turbine``             VRE          existing + bounded build
``z2_offshore_wind_turbine``            VRE          build only
``z1_battery``                          storage      fixed 4 h duration
``z2_hydrogen_storage``                 storage      asymmetric, LDS, free duration
``z2_conventional_hydroelectric``       hydro        inflow above 1 (clipped)
``z1_small_hydroelectric``              must-run     must-run profile
``z1_flexible_demand``                  flex         dropped by the importer
======================================  ===========  =====================================
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

__all__ = ["write_sample_case", "SAMPLE_RESOURCES"]

NAN = float("nan")

#: (kind, model, columns) for every sample resource, in modular-layout column names
SAMPLE_RESOURCES = [
    ("thermal", 1, dict(Resource="z1_natural_gas_combined_cycle", Zone=1, New_Build=1, Can_Retire=1,
                        Existing_Cap_MW=300.0, Max_Cap_MW=-1.0, Inv_Cost_per_MWyr=65000.0,
                        Fixed_OM_Cost_per_MWyr=11000.0, Var_OM_Cost_per_MWh=3.5,
                        Heat_Rate_MMBTU_per_MWh=7.0, Fuel="NG", Cap_Size=250.0, Start_Cost_per_MW=90.0,
                        Start_Fuel_MMBTU_per_MW=2.0, Up_Time=6, Down_Time=6, Ramp_Up_Percentage=0.64,
                        Ramp_Dn_Percentage=0.64, Min_Power=0.4, CapRes_1=0.93)),
    ("thermal", 2, dict(Resource="z2_natural_gas_combustion_turbine", Zone=2, New_Build=1, Can_Retire=1,
                        Existing_Cap_MW=100.0, Max_Cap_MW=400.0, Inv_Cost_per_MWyr=40000.0,
                        Fixed_OM_Cost_per_MWyr=9000.0, Var_OM_Cost_per_MWh=5.0,
                        Heat_Rate_MMBTU_per_MWh=10.5, Fuel="NG", Cap_Size=100.0, Start_Cost_per_MW=120.0,
                        Start_Fuel_MMBTU_per_MW=3.5, Up_Time=1, Down_Time=1, Ramp_Up_Percentage=1.0,
                        Ramp_Dn_Percentage=0.8, Min_Power=0.25, CapRes_1=0.93)),
    ("vre", 0, dict(Resource="z1_solar_photovoltaic", Zone=1, New_Build=1, Can_Retire=1, Existing_Cap_MW=50.0,
                    Max_Cap_MW=-1.0, Inv_Cost_per_MWyr=50000.0, Fixed_OM_Cost_per_MWyr=15000.0,
                    Num_VRE_Bins=1, CapRes_1=0.8)),
    ("vre", 0, dict(Resource="z2_onshore_wind_turbine", Zone=2, New_Build=1, Can_Retire=1, Existing_Cap_MW=200.0,
                    Max_Cap_MW=1000.0, Inv_Cost_per_MWyr=90000.0, Fixed_OM_Cost_per_MWyr=40000.0,
                    Num_VRE_Bins=1, CapRes_1=0.8)),
    ("vre", 0, dict(Resource="z2_offshore_wind_turbine", Zone=2, New_Build=1, Can_Retire=1, Existing_Cap_MW=0.0,
                    Max_Cap_MW=500.0, Inv_Cost_per_MWyr=150000.0, Fixed_OM_Cost_per_MWyr=70000.0,
                    Num_VRE_Bins=1, CapRes_1=0.8)),
    ("storage", 1, dict(Resource="z1_battery", Zone=1, New_Build=1, Can_Retire=1, Existing_Cap_MW=20.0,
                        Existing_Cap_MWh=80.0, Max_Cap_MW=-1.0, Max_Cap_MWh=-1.0, Inv_Cost_per_MWyr=15000.0,
                        Inv_Cost_per_MWhyr=20000.0, Fixed_OM_Cost_per_MWyr=4000.0, Fixed_OM_Cost_per_MWhyr=1000.0,
                        Var_OM_Cost_per_MWh=0.15, Var_OM_Cost_per_MWh_In=0.15, Eff_Up=0.92, Eff_Down=0.92,
                        Self_Disch=0.0, Min_Duration=4.0, Max_Duration=4.0, LDS=0, CapRes_1=0.95)),
    ("storage", 2, dict(Resource="z2_hydrogen_storage", Zone=2, New_Build=1, Can_Retire=1, Existing_Cap_MW=0.0,
                        Existing_Cap_MWh=0.0, Max_Cap_MW=300.0, Max_Cap_MWh=-1.0, Inv_Cost_per_MWyr=30000.0,
                        Inv_Cost_per_MWhyr=500.0, Inv_Cost_Charge_per_MWyr=20000.0, Fixed_OM_Cost_per_MWyr=1000.0,
                        Fixed_OM_Cost_per_MWhyr=0.0, Fixed_OM_Cost_Charge_per_MWyr=500.0,
                        Var_OM_Cost_per_MWh_In=0.0, Eff_Up=0.65, Eff_Down=0.55, Self_Disch=0.0,
                        Min_Duration=10.0, Max_Duration=200.0, LDS=1, CapRes_1=0.95)),
    ("hydro", 0, dict(Resource="z2_conventional_hydroelectric", Zone=2, New_Build=0, Can_Retire=0,
                      Existing_Cap_MW=150.0, Max_Cap_MW=-1.0, Fixed_OM_Cost_per_MWyr=30000.0,
                      Hydro_Energy_to_Power_Ratio=12.0, LDS=0, Eff_Up=1.0, Eff_Down=1.0, Min_Power=0.1,
                      Ramp_Up_Percentage=1.0, Ramp_Dn_Percentage=1.0, CapRes_1=0.9)),
    ("must_run", 0, dict(Resource="z1_small_hydroelectric", Zone=1, New_Build=0, Can_Retire=0,
                         Existing_Cap_MW=10.0, Max_Cap_MW=-1.0, Fixed_OM_Cost_per_MWyr=20000.0, CapRes_1=0.9)),
    ("flex", 0, dict(Resource="z1_flexible_demand", Zone=1, New_Build=0, Can_Retire=0, Existing_Cap_MW=30.0,
                     Max_Cap_MW=-1.0, Max_Flexible_Demand_Delay=2, Max_Flexible_Demand_Advance=2,
                     Flexible_Demand_Energy_Eff=1.0, CapRes_1=0.0)),
]

REP_PERIODS, TIMESTEPS = 2, 4
SUB_WEIGHTS = [6570.0, 2190.0]

VARIABILITY = {
    "z1_natural_gas_combined_cycle": [1.0] * 8,
    "z2_natural_gas_combustion_turbine": [1, 1, 1, 1, 1, 1, 0.9, 1],
    "z1_solar_photovoltaic": [0.0, 0.45, 0.85, 0.30, 0.0, 0.25, 0.55, 0.10],
    "z2_onshore_wind_turbine": [0.40, 0.35, 0.30, 0.45, 0.60, 0.70, 0.65, 0.55],
    "z2_offshore_wind_turbine": [0.55, 0.50, 0.45, 0.60, 0.75, 0.80, 0.85, 0.70],
    "z1_battery": [1.0] * 8,
    "z2_hydrogen_storage": [1.0] * 8,
    "z2_conventional_hydroelectric": [0.50, 0.55, 0.60, 0.50, 0.90, 1.10, 1.00, 0.95],
    "z1_small_hydroelectric": [0.6] * 4 + [0.8] * 4,
    "z1_flexible_demand": [1.0] * 8,
}

DEMAND = {
    1: [900.0, 1000.0, 1150.0, 1050.0, 1100.0, 1250.0, 1300.0, 1200.0],
    2: [500.0, 550.0, 600.0, 580.0, 650.0, 700.0, 720.0, 680.0],
}

NG_CO2 = 0.05306  # tCO2 per MMBtu
NG_PRICE = [3.0] * 4 + [5.0] * 4  # per MMBtu, by timestep

SETTINGS = """\
# synthetic sample case settings
NetworkExpansion: 1
Trans_Loss_Segments: 1
UCommit: 2
CO2Cap: 1
CapacityReserveMargin: 1
EnergyShareRequirement: 0
MinCapReq: 0
MaxCapReq: 0
TimeDomainReduction: 0
ParameterScale: 1
"""

_MODULAR_FILES = {
    "thermal": "Thermal.csv",
    "vre": "Vre.csv",
    "storage": "Storage.csv",
    "hydro": "Hydro.csv",
    "must_run": "Must_run.csv",
    "flex": "Flex_demand.csv",
}

_LEGACY_FLAGS = {"thermal": "THERM", "vre": "VRE", "storage": "STOR", "hydro": "HYDRO",
                 "must_run": "MUST_RUN", "flex": "FLEX"}


def _demand_table(prefix: str) -> pd.DataFrame:
    T = REP_PERIODS * TIMESTEPS
    head = {
        "Voll": [50000.0] + [NAN] * (T - 1),
        "Demand_Segment": [1] + [NAN] * (T - 1),
        "Cost_of_Demand_Curtailment_per_MW": [1.0] + [NAN] * (T - 1),
        "Max_Demand_Curtailment": [1.0] + [NAN] * (T - 1),
        "Rep_Periods": [REP_PERIODS] + [NAN] * (T - 1),
        "Timesteps_per_Rep_Period": [TIMESTEPS] + [NAN] * (T - 1),
        "Sub_Weights": SUB_WEIGHTS + [NAN] * (T - len(SUB_WEIGHTS)),
        "Time_Index": list(range(1, T + 1)),
    }
    for z, values in DEMAND.items():
        head[f"{prefix}_MW_z{z}"] = values
    df = pd.DataFrame(head)
    for col in ("Demand_Segment", "Rep_Periods", "Timesteps_per_Rep_Period"):
        df[col] = df[col].astype("Int64")
    return df


def _variability() -> pd.DataFrame:
    T = REP_PERIODS * TIMESTEPS
    data = {"Time_Index": list(range(1, T + 1))}
    data.update({name: VARIABILITY[name] for _, _, row in SAMPLE_RESOURCES for name in [row["Resource"]]})
    return pd.DataFrame(data)


def _fuels() -> pd.DataFrame:
    T = REP_PERIODS * TIMESTEPS
    return pd.DataFrame({"Time_Index": list(range(0, T + 1)), "NG": [NG_CO2] + NG_PRICE, "None": [0.0] * (T + 1)})


def _network(legacy: bool) -> pd.DataFrame:
    net = {
        "Network_zones": ["z1", "z2"],
        "Network_Lines": [1, NAN],
    }
    if legacy:
        net.update({"z1": [1, NAN], "z2": [-1, NAN]})
    else:
        net.update({"Start_Zone": [1, NAN], "End_Zone": [2, NAN]})
    net.update(
        {
            "Line_Max_Flow_MW": [400.0, NAN],
            "transmission_path_name": ["z1_to_z2", NAN],
            "distance_mile": [100.0, NAN],
            "Line_Loss_Percentage": [0.02, NAN],
            "Line_Max_Reinforcement_MW": [500.0, NAN],
            "Line_Reinforcement_Cost_per_MWyr": [20000.0, NAN],
            "DerateCapRes_1": [0.95, NAN],
            "CapRes_Excl_1": [0, NAN],
            "Line_Voltage_kV": [345.0, NAN],
            "Line_Reactance_Ohms": [30.0, NAN],
            "Angle_Limit_Rad": [0.0, NAN],
        }
    )
    df = pd.DataFrame(net)
    for col in ("Network_Lines", "Start_Zone", "End_Zone", "z1", "z2", "CapRes_Excl_1"):
        if col in df.columns:
            df[col] = df[col].astype("Int64")  # whole numbers, blank where absent
    return df


def _policies() -> dict:
    return {
        "CO2_cap.csv": pd.DataFrame(
            {
                "Network_zones": ["z1", "z2"],
                "CO_2_Cap_Zone_1": [1, 1],
                "CO_2_Max_tons_MWh_1": [0.05, 0.05],
                "CO_2_Max_Mtons_1": [1.5, 0.5],
            }
        ),
        "Capacity_reserve_margin.csv": pd.DataFrame({"Network_zones": ["z1", "z2"], "CapRes_1": [0.156, 0.156]}),
    }


def write_sample_case(path, layout: str = "modular") -> Path:
    """Write the synthetic sample case into ``path`` and return it.

    ``layout`` is ``"modular"`` (GenX 0.4+ folders) or ``"legacy"`` (one
    ``Generators_data.csv``, ``Load_data.csv`` and an incidence-form
    ``Network.csv``). Both layouts describe the same system.
    """
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    if layout == "modular":
        for sub in ("system", "resources/policy_assignments", "policies", "settings"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        (root / "settings" / "genx_settings.yml").write_text(SETTINGS)
        _demand_table("Demand").to_csv(root / "system" / "Demand_data.csv", index=False)
        _variability().to_csv(root / "system" / "Generators_variability.csv", index=False)
        _fuels().to_csv(root / "system" / "Fuels_data.csv", index=False)
        _network(legacy=False).to_csv(root / "system" / "Network.csv", index=False)
        derating = []
        for kind, fname in _MODULAR_FILES.items():
            rows = []
            for k, model, cols in SAMPLE_RESOURCES:
                if k != kind:
                    continue
                row = {c: v for c, v in cols.items() if c != "CapRes_1"}
                if kind in ("thermal", "storage"):
                    row = {"Resource": row.pop("Resource"), "Zone": row.pop("Zone"), "Model": model, **row}
                rows.append(row)
                derating.append({"Resource": cols["Resource"], "Derating_factor_1": cols["CapRes_1"]})
            pd.DataFrame(rows).to_csv(root / "resources" / fname, index=False)
        pd.DataFrame(derating).to_csv(
            root / "resources" / "policy_assignments" / "Resource_capacity_reserve_margin.csv", index=False
        )
        for fname, df in _policies().items():
            df.to_csv(root / "policies" / fname, index=False)
    elif layout == "legacy":
        (root / "Settings").mkdir(exist_ok=True)
        (root / "Settings" / "genx_settings.yml").write_text(SETTINGS)
        _demand_table("Load").to_csv(root / "Load_data.csv", index=False)
        _variability().to_csv(root / "Generators_variability.csv", index=False)
        _fuels().to_csv(root / "Fuels_data.csv", index=False)
        _network(legacy=True).to_csv(root / "Network.csv", index=False)
        columns: list[str] = []
        for _, _, cols in SAMPLE_RESOURCES:
            columns += [c for c in cols if c not in columns]
        rows = []
        for i, (kind, model, cols) in enumerate(SAMPLE_RESOURCES, start=1):
            row = {c: cols.get(c, 0) for c in columns if c not in ("New_Build", "Can_Retire")}
            if "Fuel" in row and row["Fuel"] == 0:
                row["Fuel"] = "None"
            for k, flag in _LEGACY_FLAGS.items():
                row[flag] = model if (k == kind and model) else int(k == kind)
            # legacy New_Build: 1 build+retire, 0 retire only, -1 neither
            row["New_Build"] = 1 if cols["New_Build"] else (0 if cols["Can_Retire"] else -1)
            row["R_ID"] = i
            rows.append(row)
        pd.DataFrame(rows).to_csv(root / "Generators_data.csv", index=False)
        for fname, df in _policies().items():
            df.to_csv(root / fname, index=False)
    else:
        raise ValueError("layout must be 'modular' or 'legacy'")
    return root


if __name__ == "__main__":  # pragma: no cover
    import sys

    write_sample_case(sys.argv[1] if len(sys.argv) > 1 else "sample_case", *(sys.argv[2:3] or ["modular"]))
