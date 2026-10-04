# ce-pypsa — PyPSA ↔ CE bundle converters

Convert a [PyPSA](https://pypsa.org) network into a **CE bundle** (CE data format 1.0, see
`spec/` component of the CE Open repository) and a CE bundle back into a PyPSA network.

* **Import** reads a PyPSA CSV folder (no PyPSA installation needed), a netCDF/HDF5 file or a live
  `pypsa.Network` (both need `pypsa`).
* **Export** writes a PyPSA CSV folder (no PyPSA needed) or netCDF (needs `pypsa`).
* **Nothing is lost silently.** Every approximation, default and omission is recorded in a conversion
  report, returned by the API, printed by the CLI and written next to the output as
  `x_conversion_report.toml`.

Licensed under the Apache License 2.0. Copyright 2026 Compounding Energy Ltd.

## Install

```bash
pip install ./converters/pypsa            # pandas, numpy, pyarrow
pip install "./converters/pypsa[pypsa]"   # + PyPSA, for netCDF and standard line types
pip install ./spec "./converters/pypsa[validate]"   # + the CE format validator, from a checkout
```

## Use

```python
from ce_pypsa import import_network, export_bundle

bundle = import_network("my_network/", "my_bundle/", currency="EUR", money_year=2025)
print(bundle.report)              # what was approximated, defaulted or dropped
assert bundle.validate() == []    # structural checks (the reference validator lives in spec/)

tables, report = export_bundle("my_bundle/", "back_to_pypsa/")   # or "network.nc"
```

```bash
ce-pypsa import my_network/ my_bundle/ --currency EUR --money-year 2025
ce-pypsa import my_network/ my_bundle/ --block-hours 24        # daily blocks instead of one block
ce-pypsa export my_bundle/ back_to_pypsa/                       # or back.nc
python -m ce_spec validate-bundle my_bundle/                    # reference validator (spec/)
```

`import_network` options: `year` (default: first snapshot's year), `currency` / `money_year` (PyPSA records
neither), `voll` and `prm` (CE scalars PyPSA lacks; `prm` defaults to 0), `block_hours`,
`inf_capacity_mw` (envelope for unbounded expansion, default 100 000 MW), `technology_map`, `fmt`
(`parquet`, `arrow` or `csv`). `export_bundle` options: `year`, `scenario`, `weather` (one of each is
exported) and `unit_commitment` (`None`: committable where the bundle flags it, `True`: every thermal
unit, `False`: pure LP).

## Conventions

**Time.** PyPSA snapshots become CE periods; the `stores` weighting is the period length `dt_h`. By
default all snapshots form one block; `block_hours=N` cuts them into consecutive blocks of N periods.
A block is `chronological` when the snapshots are contiguous timestamps whose weights equal their
durations (a full hourly year), otherwise `representative`. Snapshot timestamps are read as UTC.

**Horizon → year.** PyPSA charges `capital_cost` once over the modelled horizon `H` (the sum of the
`objective` weightings); a CE bundle is annual with block weights adding up to a full year `Y` (8760 h,
or 8784 h for a network already spanning a leap year). The importer multiplies block weights *and*
every per-MW fixed cost by `s = Y / H`, so the CE objective is exactly `s` times the PyPSA objective and
the optimal decisions are unchanged. For the usual `H = 8760`, `s = 1`. Overnight costs
(`overnight_cost`, `discount_rate`, `lifetime`, PyPSA ≥ 1.1) are annualised with PyPSA's own annuity,
which equals the specification's capital recovery factor.

**Network.** `susceptance_pu` is on the specification's 100 MVA base:
`b = v_nom² / (100 · x)` for a line (`x` in Ω, `v_nom` of `bus0` in kV) and `b = s_nom / (100 · x)` for
a transformer (`x` per unit on `s_nom`). Lines defined by a standard `type` are resolved through PyPSA
(`x_pu_eff`, which also folds in `num_parallel` and tap ratios). Ratings are `s_nom · s_max_pu`.

**Money.** PyPSA is currency-agnostic. The bundle's `[money]` table states the currency and money year
you pass (defaults `EUR` and the investment year, both reported as defaults). Values are never
converted.

**Names.** CE generators, storage and candidates share one name space and key the `availability`
series. An extendable PyPSA asset with `p_nom_min > 0` becomes an existing unit `<name>` plus a
candidate `<name>__expansion`; the exporter merges such a pair back into one extendable component.

## Mapping

### Import (PyPSA → CE)

| PyPSA | CE bundle | Notes |
|---|---|---|
| snapshots, weightings | `[time]` blocks: `nperiods`, `dt_h` (= `stores`), `weight_h` (= `s ·` Σ `objective`), `start` | labels and original weightings kept in `x_pypsa_snapshots` |
| `Bus` (carrier AC/DC/blank) | `buses`: `name`, `lat`=`y`, `lon`=`x`, `voltage_kv`=`v_nom`, `zone`=`country` or `location` | ids 1..N in PyPSA order |
| `Line` | `lines` (DC power flow) | `x` → susceptance, `length` → `length_km` |
| `Transformer` | `lines` | per-unit `x` on `s_nom` → susceptance |
| `Line` between two DC buses | `hvdc` | controllable corridor |
| `Link` (two electric buses) | `hvdc`: `existing_mw`, `candidate_max_mw`, `converter_efficiency` = `efficiency`, `capex_per_mw_yr` | |
| `Generator`, renewable carrier | `generators` (`kind = renewable`) + `availability` (`p_max_pu`) | carriers in `carriers.py` |
| `Generator`, other carriers | `generators` (`kind = thermal`) | `co2_t_per_mwh` = carrier `co2_emissions` / `efficiency` |
| `p_nom_extendable` | `candidates` (`max_mw` = `p_nom_max − p_nom_min`, `capex_per_mw_yr`, `fom_per_mw_yr`) | `p_nom_min` part stays in the existing fleet |
| `committable`, `p_min_pu`, `min_up_time`, `min_down_time`, `start_up_cost`, `stand_by_cost`, `ramp_limit_*` | `pmin_frac`, `min_up_h`, `min_down_h`, `startup_cost`, `noload_cost_per_h`, `ramp_frac_per_h` | flagged `x_committable` / `x_must_run` |
| `StorageUnit` (can charge) | `storage`: `duration_h` = `max_hours`, `eta_charge`/`eta_discharge`, `self_discharge_frac_per_h` = `standing_loss` | extendable part → `candidates` (`kind = storage`) |
| `StorageUnit` that cannot charge, with `inflow` | `generators` (`kind = renewable`, `hydro_reservoir`) with cf = `efficiency_dispatch · inflow / p_nom` | see lossy list |
| `Load` | `demand` per bus (`mw = −sign · p_set`) | |
| `GlobalConstraint` CO2 (`primary_energy`) | `[[x_policy]]` `emission_cap_t`, t/yr = `constant · Y / Σ generators weighting` | extension key; the same shape the GenX importer writes (`name`, `kind`, `pollutant`, `value`, optional `zones` / `technologies` / `years`) |
| capital cost of non-extendable assets | `fom_per_mw_yr` (generators), `x_fom_per_mw_yr` (storage) | a constant in both models |

### Export (CE → PyPSA)

The inverse of the table above, plus:

| CE bundle | PyPSA |
|---|---|
| blocks | snapshots laid end to end; `objective` = `generators` = `dt_h · weight_h / Σ dt_h`, `stores` = `dt_h`; labels from `start` (else `b<block>p<period>`), or the original labels and weightings when `x_pypsa_snapshots` still matches |
| line `existing & candidate` | extendable line, `s_nom_min` = `rating_mw`, `s_nom_max` = `rating_mw + delta_rating_mw`, `capital_cost` = `capex_per_yr / delta_rating_mw` |
| line greenfield candidate | extendable line from 0 to `rating_mw`, `capital_cost` = `capex_per_yr / rating_mw` |
| `hvdc` | bidirectional `Link` (`p_min_pu = −1`) |
| `candidates` | extendable `Generator` / `StorageUnit`; annual cost = `capex_per_mw_yr` (or overnight × CRF) + `fom_per_mw_yr` + interconnection × 1000 × CRF |
| `[system] co2_price_per_t` | added to thermal `marginal_cost` |
| `co2_t_per_mwh` | carrier `co2_emissions` (maximum within the carrier) and generator `efficiency` = max / own intensity |

## Lossy mappings

Each item below is reported (kind in brackets) whenever it applies.

**PyPSA → CE**

* *(dropped)* Non-electric buses (carrier other than AC/DC) and every component attached to them;
  sector-coupling and multi-port links; `Store`s; shunt impedances; unknown component files.
* *(dropped)* Line/transformer resistance `r`, conductance `g`, shunt `b`, phase shift (and tap ratio
  when PyPSA is not installed): the DC power-flow model uses reactance only.
* *(dropped)* Quadratic costs, shut-down costs, start-up/shut-down ramp limits, energy-sum limits
  (`e_sum_min/max`), link operating costs, delays and ramp limits, `marginal_cost_storage`,
  `spill_cost`, reactive power, `build_year`/`lifetime` vintages, negative-sign generators, and
  time-varying `p_min_pu`, `p_set`, ramp limits, stand-by costs, storage `p_max_pu`/`p_min_pu`,
  efficiencies, standing loss and `state_of_charge_set`.
* *(dropped)* Inflow into storage that can also charge from the grid.
* *(approximated)* Time-varying `marginal_cost` and `efficiency` → their objective-weighted means;
  time-varying line `s_max_pu` → its minimum.
* *(approximated)* Per-snapshot objective weights within a block are kept only when
  `objective / stores` is constant in the block; `generators` weightings that differ from `objective`
  weightings are not kept separately.
* *(approximated)* A horizon other than a full year is scaled to one (see *Horizon → year*): decisions
  are unchanged, the objective is scaled by `s`.
* *(approximated)* Transformers become lines; lines between DC buses become HVDC corridors.
* *(approximated)* Extendable branches: PyPSA expands continuously at `capital_cost` per MW, a CE line
  candidate is one build of the whole increment at `capex_per_yr`; an unbuilt greenfield branch is
  absent in CE but still couples bus angles (at zero flow) in PyPSA.
* *(approximated)* Links are imported as symmetric, bidirectional corridors (one-way links and
  `p_min_pu ≠ −p_max_pu` lose their asymmetry); PyPSA applies link efficiency to forward flow only, CE in
  both directions.
* *(approximated)* `p_nom_min > 0` on an extendable asset: the minimum becomes existing capacity whose
  capital cost is carried as fixed O&M (a constant).
* *(approximated)* Thermal `p_max_pu` (static or a series' weighted mean) is folded into capacity, with
  per-MW costs and `pmin`/ramp fractions rescaled to match.
* *(approximated)* Generator `efficiency` is folded into `co2_t_per_mwh` (marginal cost already prices
  the fuel); asymmetric ramp limits → the tighter one; PyPSA carriers → CE technology codes.
* *(approximated)* Non-committable `p_min_pu > 0` is a hard floor in a PyPSA LP; CE applies
  `pmin_frac` to committed units, so these units are flagged `x_must_run`. Committable units keep their
  commitment data (flagged `x_committable`); a CE model applies it when it runs unit commitment.
* *(approximated)* A store that cannot charge from the grid but has inflow (reservoir hydro) becomes an
  availability-bounded generator: its ability to shift energy in time is lost.
* *(approximated)* Storage state of charge: CE starts and ends each block at `soc_init_frac`; PyPSA's
  cyclic option leaves the level free and its non-cyclic option fixes only the start. A charging rating
  different from the discharging rating is not kept.
* *(approximated)* Storage candidates have no efficiency columns in the specification; efficiencies,
  self-discharge and initial state of charge are carried as `x_` extension columns.
* *(approximated)* Several loads on one bus are summed (load names are not kept); negative net demand
  is clipped to 0.
* *(approximated)* The CO2 primary-energy limit counts generator emissions only (PyPSA's form also
  counts changes in stored fuel); `==` becomes a cap.
* *(defaulted)* Unbounded expansion (`p_nom_max = inf`) is capped at `inf_capacity_mw`; currency and
  money year; `firm_capacity_frac` is left empty (PyPSA has no capacity credit); snapshots are read as
  UTC.

**CE → PyPSA**

* *(dropped)* All but one investment year, scenario and weather year; value of lost load; planning
  reserve margin and capacity credits; line `loss_frac` and `contingency`; lead times; retirement
  (`retirable`, `retirement_year`); `dod_floor_frac`; hybrid candidates; non-system-wide or non-CO2
  `x_policy` entries (a `zones` list naming every bus zone counts as system-wide); every other `x_*`
  section of `bundle.toml` (each one is named in the report); unit-commitment data of units not
  exported as committable; currency (PyPSA records none).
* *(approximated)* Line candidates become continuous expansion; integer builds become continuous;
  independently sized storage energy (`max_mwh`) is not kept and `capex_per_mwh_yr` is folded into the
  per-MW cost at `duration_h`; HVDC efficiency applies to forward flow only in PyPSA; storage is
  exported cyclic; a carbon price is added to marginal costs; a spread of CO2 intensities within one
  carrier is carried by generator efficiencies; an existing unit and its `__expansion` candidate are
  merged into one extendable component.
* *(defaulted)* A renewable resource without an availability series is exported with `p_max_pu = 0`
  (the specification's meaning of a missing series).

## Not supported

Multi-investment-period networks (`snapshots` with `period`/`timestep`) and stochastic (multi-scenario)
PyPSA networks are refused with `UnsupportedNetworkError`; convert one period or scenario at a time.

## Files

| Path | Purpose |
|---|---|
| `src/ce_pypsa/importer.py` | PyPSA → CE |
| `src/ce_pypsa/exporter.py` | CE → PyPSA |
| `src/ce_pypsa/bundle.py` | the bundle: tables, canonical column types, read/write, checks |
| `src/ce_pypsa/pypsa_csv.py` | PyPSA CSV folders without PyPSA |
| `src/ce_pypsa/carriers.py` | carrier ↔ technology vocabulary |
| `src/ce_pypsa/report.py` | the conversion report |
| `examples/` | a synthetic toy network and a conversion script |
| `tests/` | round-trip, mapping, CLI, PyPSA and specification-conformance tests |

## Tests

```bash
pip install -e "./converters/pypsa[test,pypsa]"
pytest converters/pypsa
```

Without PyPSA the PyPSA-specific tests are skipped. With PyPSA and HiGHS installed, two tests solve
tiny LPs to check that a round trip preserves the optimum and that horizon scaling scales the
objective by exactly `s`. When the repository's `spec/` package is present, bundles are also checked
with its reference validator. All test data are synthetic.
