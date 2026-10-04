# ce-genx — GenX case importer

`ce-genx` reads a [GenX](https://github.com/GenXProject/GenX.jl) case folder
and writes it as a **CE data-format 1.0 bundle** (`bundle.toml` plus Parquet or
CSV tables; see the `spec/` component of the CE Open repository). GenX is an
open-source capacity-expansion model; this importer lets a study set up in
GenX be loaded into any tool that reads CE bundles.

Every mapping that is not exact is reported, never hidden: the importer
returns (and writes next to the tables) a report listing each gap with a
stable code, explained [below](#lossy-mappings-and-limitations).

Licensed under the Apache License 2.0. Copyright 2026 Compounding Energy Ltd.

## Install

```bash
pip install -e converters/genx          # from the repository root
pip install -e "converters/genx[test]"  # with pytest
pip install ./spec "./converters/genx[validate]"   # + the CE format validator, from a checkout
```

Requires Python 3.11+, pandas, numpy and pyarrow. No GenX or Julia install is
needed: the importer reads GenX's input CSV files directly.

## Quick start

Command line:

```bash
ce-genx-import path/to/genx_case path/to/bundle
ce-genx-import path/to/genx_case path/to/bundle --csv --year 2035 --network dc
python -m ce_genx --help
```

Python:

```python
from ce_genx import import_case

bundle = import_case("path/to/genx_case", "path/to/bundle", investment_year=2035)
print(bundle.report.format())          # the mapping notes
bundle.tables["generators"]            # pandas DataFrames, one per table
bundle.descriptor["time"]["blocks"]    # the bundle.toml content as a dict
```

`import_case(case, out_dir=None, ...)` with no `out_dir` converts in memory
only. The steps are also available separately: `read_case` (GenX folder →
`GenXCase`), `convert` (→ `CEBundle`), `write_bundle` and `check_bundle`.

A runnable example using the synthetic case in
`examples/sample_case` (in the repository):

```bash
cd converters/genx
python examples/import_sample.py            # writes to a temporary folder
```

## Supported GenX inputs

Both GenX input layouts are recognised automatically:

| Layout | GenX releases | Recognised by | Resource table(s) | Demand |
|---|---|---|---|---|
| `legacy` | up to the 0.3 series | `Generators_data.csv` in the case folder | one table; type from the `THERM`, `VRE`, `STOR`, `HYDRO`, `MUST_RUN`, `FLEX` flags | `Load_data.csv` (`Load_MW_z<n>`) |
| `modular` | 0.4 and later | a `resources/` folder | `Thermal.csv`, `Vre.csv`, `Storage.csv`, `Hydro.csv`, `Must_run.csv`, `Flex_demand.csv`, ... | `system/Demand_data.csv` (`Demand_MW_z<n>`) |

Files read (names matched case-insensitively, column names lower-cased):

* resources, as above, plus `resources/policy_assignments/Resource_capacity_reserve_margin.csv`
  (modular) or the `CapRes_<k>` columns (legacy) for capacity-reserve deratings;
* `Demand_data.csv` / `Load_data.csv`: `Voll`, `Demand_Segment`, `Rep_Periods`,
  `Timesteps_per_Rep_Period`, `Sub_Weights` and the zonal demand columns;
* `Generators_variability.csv`: one column per resource;
* `Fuels_data.csv`: the `Time_Index = 0` row of CO2 contents (tCO2/MMBtu) and
  one price row per timestep;
* `Network.csv`: lines given either by `Start_Zone`/`End_Zone` or by `z<n>`
  incidence columns (+1 start, -1 end);
* `CO2_cap.csv`, `Capacity_reserve_margin.csv`, `Energy_share_requirement.csv`,
  `Minimum_capacity_requirement.csv`, `Maximum_capacity_requirement.csv`;
* `genx_settings.yml` (`settings/` or `Settings/`): `NetworkExpansion`,
  `Trans_Loss_Segments`, `UCommit`, `CO2Cap`, `CapacityReserveMargin`,
  `EnergyShareRequirement`, `MinCapReq`, `MaxCapReq`. Without a settings
  file, network expansion and losses are taken as on and every policy file
  present as active (note `settings_not_found`).

Also supported: multi-stage cases (`inputs/inputs_p<N>`; import one stage with
`--stage N`) and time-domain-reduced series (`--tdr` reads `TDR_results/`).

## How GenX maps onto the bundle

| GenX | CE bundle |
|---|---|
| zone `n` | `buses` row `id = n`, `name = zone = "z<n>"` |
| `Network.csv` line (transport model, default) | `hvdc` corridor: `existing_mw = Line_Max_Flow_MW`, `candidate_max_mw = Line_Max_Reinforcement_MW`, `capex_per_mw_yr = Line_Reinforcement_Cost_per_MWyr`, `converter_efficiency = 1 − Line_Loss_Percentage` |
| `Network.csv` line (`--network dc`) | `lines` row: `susceptance_pu = kV² / (100 MVA × Line_Reactance_Ohms)`, `rating_mw`, one reinforcement candidate, `loss_frac`, `length_km` |
| existing capacity (`Existing_Cap_MW > 0`) | a `generators` row (thermal, VRE, hydro, must-run) or a `storage` row, named after the GenX `Resource` |
| new-build option (`New_Build = 1`) | a `candidates` row `<Resource>_new` with `max_mw = Max_Cap_MW − Existing_Cap_MW` |
| `Inv_Cost_per_MWyr`, `Fixed_OM_Cost_per_MWyr` | `capex_per_mw_yr`, `fom_per_mw_yr` (both already annualised in GenX) |
| `Var_OM_Cost_per_MWh` + `Heat_Rate_MMBTU_per_MWh` × fuel price | `varcost_per_mwh` (fuel price averaged over the year) |
| `Heat_Rate_MMBTU_per_MWh` × fuel CO2 content | `co2_t_per_mwh` (net of `CO2_Capture_Fraction`) |
| `Min_Power`, ramps, `Up_Time`, `Down_Time`, start costs (THERM/Model 1) | `pmin_frac`, `ramp_frac_per_h`, `min_up_h`, `min_down_h`, `startup_cost` on existing thermal rows |
| `Can_Retire` (legacy: `New_Build ≥ 0`) | `generators.retirable` |
| `Eff_Up`, `Eff_Down`, `Self_Disch`, `Existing_Cap_MWh` | `eta_charge`, `eta_discharge`, `self_discharge_frac_per_h`, `duration_h = MWh / MW` on existing storage |
| storage with `Min_Duration = Max_Duration` | storage candidate with that `duration_h`; per-MWh costs folded into per-MW costs |
| storage with a free duration | storage candidate with `max_mwh` and `capex_per_mwh_yr` |
| `Generators_variability.csv` | `availability` for every renewable generator and candidate (`site_name`) |
| demand columns | `demand` (`bus`, `scenario = 1`, `block`, `period`, `mw`) |
| `Rep_Periods` × `Timesteps_per_Rep_Period`, `Sub_Weights` | one `[[time.blocks]]` per representative period, `weight_h = Sub_Weights` |
| `Voll` | `[system] voll_per_mwh` |
| capacity reserve margin + deratings | `[system] planning_reserve_margin` + `firm_capacity_frac` |
| mass-based `CO2_cap.csv` (`CO2Cap = 1`) | `[[x_policy]]` emission cap (see below) |

Technology codes are the CE format's recommended codes (`spec/SPEC.md`
section 6.4 — the same codes the PyPSA converter uses), taken from keywords in
the resource name (or a `technology` / `Resource_Type` column): e.g.
`combined_cycle` → `gas_cc`, `combustion_turbine` / `peaker` → `gas_ct`,
`gas_steam` / `steam_turbine` → `gas_steam` (after coal, oil and biomass
keywords), any other natural gas → `gas_cc`, `hydrogen`
(thermal) → `hydrogen_ct`, `offshore` → `wind_offshore`, `wind` →
`wind_onshore`, `solar`/`pv` → `solar_pv`, `pumped` → `pumped_hydro`,
`hydrogen` (storage) → `hydrogen_storage`. Reservoir hydro becomes
`hydro_reservoir`, small-hydro must-run `hydro_ror`. A VRE
resource whose technology cannot be told from its name is refused; pass an
override (`--tech NAME=CODE`, or `technologies={...}` in Python).

## Options

| CLI | Python (`ImportOptions`) | Default | Meaning |
|---|---|---|---|
| `--year` | `investment_year` | 2030 | the bundle's investment year |
| `--currency` | `currency` | `USD` | ISO 4217 code of the case's money (GenX does not record it) |
| `--dollar-year` | `dollar_year` | investment year | money year |
| `--network` | `network` | `transport` | `transport` → `hvdc`; `dc` → `lines` with susceptance |
| `--unbounded-cap-mw` | `unbounded_cap_mw` | 2 × coincident peak | build limit for `Max_Cap_MW = -1` |
| `--start` | `start` | — | start instant of a full-year case (marks the block chronological) |
| `--tech NAME=CODE` | `technologies` | — | technology overrides |
| `--name` | `name` | case folder name | bundle name |
| `--stage` | `import_case(stage=)` | — | stage of a multi-stage case |
| `--tdr` | `import_case(tdr=)` | off | read time series from `TDR_results/` |
| `--csv` | `import_case(fmt="csv")` | Parquet | table file format |
| `--overwrite` | `import_case(overwrite=True)` | off | write into a non-empty folder |

## Output

```
bundle/
├── bundle.toml                 format, money, time blocks, scenarios, system, [x_genx]
├── buses.parquet
├── hvdc.parquet | lines.parquet
├── generators.parquet          existing thermal and renewable fleet
├── storage.parquet             existing storage
├── candidates.parquet          build options
├── demand.parquet
├── availability.parquet
└── x_genx_import_report.toml   the mapping notes (an x_ extension file)
```

No JSON is written. The `[x_genx]` table in `bundle.toml` is a format
extension holding the importer version, the network representation, the
unbounded build limit used and long-duration-storage resources.

CO2 caps are written in the `[[x_policy]]` extension shape that the PyPSA
converter in this repository also reads and writes, so a cap survives
GenX → CE → PyPSA (it becomes a PyPSA CO2 global constraint):

```toml
[[x_policy]]
name = "genx_co2_cap_1"
kind = "emission_cap_t"      # an emission cap in tonnes per year
pollutant = "co2"
value = 2000000.0            # tCO2 per year (GenX's CO_2_Max_Mtons x 1e6)
zones = ["z1"]               # only when the cap covers some zones; absent = system-wide
```
The command line also runs a light self-check (`check_bundle`). The output
passes the format validator in `spec/` (exercised by the tests when it is
importable).

## Lossy mappings and limitations

GenX and the CE bundle do not describe the same things, so some information
is approximated or left out. Each case is reported with one of three levels:
`info` (an assumption or a faithful re-expression), `lossy` (mapped
approximately) or `dropped` (not carried). Items name the affected resources.

### Assumptions (info)

| Code | Meaning |
|---|---|
| `settings_not_found` | No settings file: expansion and losses on, every policy file present active. |
| `transport_as_hvdc` | GenX transport lines are controllable corridors, exactly the bundle's `hvdc` model; loss becomes `converter_efficiency = 1 − loss`. |
| `network_expansion_disabled` | `NetworkExpansion = 0`: reinforcement options are not written. |
| `line_losses_disabled` | `Trans_Loss_Segments = 0`: losses are not written. |
| `line_loss_read_as_percent` | A `Line_Loss_Percentage` above 1 was read as a percentage. |
| `line_resistance_unused` | `Line_Resistance_Ohms` is not used by the DC power flow. |
| `long_duration_storage` | GenX LDS resources link state of charge across representative periods; the bundle has no per-unit flag, so they are listed in `x_genx.long_duration_storage` and inter-period linking is a run choice. |
| `ccs_netted` | CO2 capture netted into `co2_t_per_mwh`, disposal cost into `varcost_per_mwh`. |
| `vre_bins_flattened` | VRE bins become separate resources. |
| `storage_energy_fom_folded` | Per-MWh fixed O&M of new storage is added to `capex_per_mwh_yr`. |
| `dollar_year_assumed` | The money year is taken as the investment year. |
| `full_year_as_representative` | A full-year case without `--start` is one representative block weighted 8760 h. |
| `no_reserve_margin` | The case has no active capacity reserve margin and the bundle sets none: run without a planning-reserve constraint to match GenX. |
| `reserve_margin_disabled` | `Capacity_reserve_margin.csv` present but switched off. |
| `co2_cap_disabled` | `CO2_cap.csv` present but `CO2Cap = 0`. |

### Approximations (lossy)

| Code | Meaning |
|---|---|
| `unbounded_build_capped` | `Max_Cap_MW = -1` (no limit) needs a finite `max_mw`; the importer uses `--unbounded-cap-mw` (default twice the coincident peak). |
| `unit_cluster_aggregated` | GenX commits clusters in whole units of `Cap_Size`; a bundle row is one aggregate unit, so `pmin_frac` and the start cost apply to the whole row (`startup_cost = per-MW start cost × existing_mw`). |
| `ramp_symmetric` | Different ramp-up and ramp-down limits; the tighter is kept. |
| `fuel_price_time_averaged` | Time-varying fuel prices are averaged, weighted by the hours each timestep represents, into one variable cost. |
| `availability_clipped` | Profile values outside [0, 1] are clipped (hydro inflow above turbine capacity; the clipped energy share is reported). |
| `hydro_reservoir_as_availability` | Reservoir hydro becomes a renewable generator bounded by its normalised inflow: no storage of inflow between periods; `Hydro_Energy_to_Power_Ratio`, `Min_Power` and ramps are not carried. |
| `must_run_as_renewable` | Must-run output (exactly capacity × profile in GenX) becomes availability-bounded and curtailable. |
| `storage_charge_cost_folded` | `Var_OM_Cost_per_MWh_In` is folded into the discharge cost as `Var_OM_In / (eta_charge × eta_discharge)`. |
| `asymmetric_charge_capacity` | Asymmetric storage (STOR/Model 2): charge capacity equals discharge capacity in the bundle, charge-capacity costs are added to the power costs. |
| `storage_energy_assumed` | Existing storage without `Existing_Cap_MWh`: energy taken as `Min_Duration` (or 1 h) × power. |
| `reserve_margin_system_wide` | Several or zone-specific reserve constraints collapse to one system-wide margin (the largest). |
| `reserve_derating_first_only` | Only the first reserve constraint's derating becomes `firm_capacity_frac`. |
| `line_reinforcement_lumpy` | (`--network dc`) GenX's continuous reinforcement becomes one all-or-nothing upgrade of `delta_rating_mw`. |

### Not carried (dropped)

| Code | Meaning |
|---|---|
| `thermal_availability_dropped` | GenX thermal variability (derates, maintenance) has no bundle table; those units are available at full capacity. |
| `candidate_operating_limits_dropped` | The `candidates` table has no minimum-output, ramp or commitment columns, so new thermal capacity carries none. |
| `candidate_storage_efficiency_dropped` | The `candidates` table has no efficiency or self-discharge columns; new storage takes the consumer's defaults. |
| `storage_duration_bounds_dropped` | `Min_Duration`/`Max_Duration` of new storage with a free duration are not enforced (an unbounded `Max_Cap_MWh` becomes `max_mw × Max_Duration`). |
| `storage_retirement_dropped` | The `storage` table has no retirement flag. |
| `storage_existing_fom_dropped` | The `storage` table has no fixed O&M column (a sunk cost while storage cannot retire). |
| `min_capacity_dropped` | `Min_Cap_MW` is not carried. |
| `operating_reserves_dropped` | Regulation and reserve parameters (`Reg_*`, `Rsv_*`) are not carried. |
| `fuel_options_dropped` | Multi-fuel and piecewise fuel-use inputs; the primary `Fuel` and heat rate are used. |
| `resource_kind_dropped` | Flexible demand, co-located VRE-storage, electrolyzers and other resource types have no bundle equivalent. |
| `line_reserve_terms_dropped` | Reserve-margin transfer terms on lines (`DerateCapRes_*`, `CapRes_Excl_*`). |
| `angle_limits_dropped` | `Angle_Limit_Rad`. |
| `co2_cap_as_extension` | Mass-based CO2 caps have no core bundle field; they are kept as `[[x_policy]]` emission caps (the shape the PyPSA converter reads) for a consumer that applies them. |
| `co2_rate_cap_dropped` | Rate-based CO2 caps (`CO2Cap = 2` or `3`). |
| `energy_share_requirement_dropped` | Energy share requirements (ESR). |
| `minimum_capacity_requirement_dropped` | Minimum capacity requirements. |
| `maximum_capacity_requirement_dropped` | Maximum capacity requirements. |
| `demand_curtailment_segments_dropped` | Price-responsive demand segments beyond the first; `Voll` is kept. |

### Other limitations

* One investment year per bundle: a multi-stage case is imported one stage at a time.
* GenX's money units are not recorded in the case; declare them with
  `--currency` / `--dollar-year`. No currency conversion is applied.
* Buses have no coordinates (GenX zones are not geographic).
* Only the first demand-curtailment segment's `Voll` is used.
* Column names and conventions follow GenX's published documentation for
  the two layouts; a GenX feature added later than these may be ignored
  without a note.

## Tests

```bash
cd converters/genx
python -m pytest
```

The tests write the synthetic sample case (in both layouts) into temporary
folders, check every mapping value by value, check that both layouts give
the same bundle, write Parquet and CSV bundles, run the command line, and —
when the format validator in `spec/` is importable — validate the output.
All data in this component is synthetic.
