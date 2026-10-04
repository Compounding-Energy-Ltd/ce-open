# ce-results

Read CE capacity-expansion (CEM) and production-cost (PCM) run directories into
pandas DataFrames, with optional xarray views. Typed accessors cover builds,
dispatch, prices, flows, curtailment, cost lines, solve stamps and provenance;
helpers compute capture prices, congestion rent and run-to-run deltas.

The reader follows the **CE data format 1.0** (the schemas under `spec/` at the
repository root) and also reads run directories written by older engine
releases, renaming their columns to the 1.0 vocabulary on load.

Licensed under the Apache License 2.0. Copyright 2026 Compounding Energy Ltd.

## Install

```sh
pip install ./results            # pandas, numpy, pyarrow
pip install './results[xarray]'  # adds xarray for Run.to_xarray
pip install ./spec './results[validate]'   # + the CE format validator, from a checkout
```

Python 3.11 or newer (the standard-library `tomllib` reads the TOML documents).

## Quick start

```python
from ce_results import Run, capture_prices, congestion_rent, compare_runs

run = Run("runs/my_study", bundle="bundles/my_study")   # bundle is optional

run.stamps.gap, run.stamps.stop_cause, run.stamps.certified(1e-3)
run.provenance.engine_version, run.provenance.currency, run.provenance.dollar_year
run.qa.status if run.qa else None

run.builds(by=["bus", "technology", "year"])   # cumulative MW
run.builds(incremental=True)                    # year-on-year additions
run.dispatch("renewable", with_time=True)       # per-timestep dispatch + period_start/period_end
run.lmp()                                       # lmp_per_mwh, lmp_energy, lmp_congestion, lmp_loss
run.flows()                                     # + from_bus, to_bus, rating_mw, loading
run.curtailment()                               # per site per timestep
run.cost_ledger()                               # every numeric cost line, long format
run.weights()                                   # annual hours each timestep stands for

capture_prices(run)                             # energy-weighted price per asset per year
congestion_rent(run)                            # surplus, congestion and loss parts per line per year

cmp = compare_runs(run, Run("runs/variant"))
cmp.costs, cmp.builds, cmp.prices, cmp.generation, cmp.warnings
```

Runnable examples, which generate their own synthetic data when given no
arguments:

```sh
python examples/quickstart.py [RUN_DIR [BUNDLE_DIR]]
python examples/compare_runs.py [RUN_A RUN_B]
```

## What a run directory holds

| File | Read as |
|------|---------|
| `manifest.toml` | `run.manifest`, `run.provenance`, `run.time_axis` |
| `summary.toml` | `run.summary` (legacy money keys renamed), `run.stamps` |
| `qa_report.toml`, `qa_checks.*` | `run.qa` |
| `results_manifest.toml` | `run.results_manifest`, `run.table_info(name)`, `run.missing_tables` |
| `investment.*` | `run.builds()`, `run.storage_energy()` |
| `generation.*` | `run.dispatch()` |
| `storage_state.*` | `run.storage()` |
| `lmp.*` | `run.lmp()` |
| `flows.*` | `run.flows()` |
| `curtailment.*` (optional) | `run.curtailment()` |
| `convergence.*` (older runs) or the `x_convergence.*` extension | `run.convergence()` |
| any other table | `run.table(name)` (returned as read; `x_<name>` is checked as `<name>` when known) |

Tables are found as `<name>.parquet` (canonical), `<name>.arrow`,
`<name>.csv.gz` or `<name>.csv`, in that order of preference. Every known
table is checked against a column contract (`ce_results.schema.TABLE_SPECS`):
a missing required column or an unreadable value raises `SchemaError`.
`Run(path, validate=False)` skips the required-column check for forensic reads.

From the **input bundle** (`bundle=`) the reader uses `bundle.toml`, `buses`,
`lines`, `generators`, `availability` and `weather_weights`, and only for the
derived metrics that need them: line endpoints and ratings (flows, congestion
rent), availability and existing capacity (curtailment), weather weights and
discount factors (weights).

## Units and conventions

* **Money** is in the run's currency and constant-money year
  (`run.provenance.currency`, `.dollar_year`). Prices are undiscounted;
  `summary.toml` cost lines are present values over the horizon.
* **Builds are cumulative.** `built_mw` is the new capacity in service in that
  investment year. Retirements are `component == "retirement"` rows with
  negative `built_mw`. Storage energy sized separately from power is a
  `storage_energy` row with `built_mwh`.
* **Dispatch is non-negative.** Direction is carried by `source_type`;
  storage charging is a `storage_charge` row. Rows with zero dispatch may be
  absent: treat them as 0 MW.
* **Flows are signed**: positive from `from_bus` to `to_bus`.
* **Periods are hour-ending.** Period *p* of a block covers
  `[start + offset_p, start + offset_p + dt_p)`; `with_time=True` adds both
  ends as `period_start` and `period_end`. Representative blocks have no
  wall-clock time and get `NaT` — the reader never fabricates a calendar.
* **Annual weights.** `run.weights()` gives each timestep's `annual_h`:
  `P(scenario) × w(weather) × weight_h / span_h × dt_h`, undiscounted.
  Annual energy is `Σ mw × annual_h`; an annual mean price is
  `Σ price × annual_h / Σ annual_h`. Weather weights are normalised within
  each investment year; an absent `weather_weights` table means equal weights.
* **Capture price** = `Σ mw × lmp × annual_h / Σ mw × annual_h` at the unit's
  bus. On `storage_charge` rows it is the average price paid to charge.
* **Congestion rent** = `Σ flow × (cong_to − cong_from) × annual_h`; with the
  matching loss part it sums exactly to the merchandising surplus
  `Σ flow × (lmp_to − lmp_from) × annual_h`, because the energy component is
  the same at every bus.
* **Comparisons** are `b − a`. `compare_runs` never refuses; it lists in
  `warnings` anything that makes a delta questionable: different investment
  years, time modes, currencies or money years, cost basis or
  assumptions-register version, an uncertified run, or a total-cost delta that
  lies inside the optimality-gap band (the ranking of the two runs is then not
  established).

## Older run directories

Runs written by earlier engine releases load through the same API. On load:

| Older spelling | 1.0 spelling |
|----------------|--------------|
| `lmp_gbp_per_mwh` | `lmp_per_mwh` |
| `varcost_gbp_per_mwh` | `varcost_per_mwh` |
| `bus_name`, `line_name` (display labels) | `bus_label`, `line_label` |
| investment `component = "storage_energy_mwh"`, MWh in `built_mw` | `component = "storage_energy"`, `built_mwh` |
| `availability_profile`: `renewable`, `availability_cf` | `site_name`, `cf` |
| `summary.toml` `*_gbp` money keys (`total_gbp` ...) | unsuffixed (`total` ...) |
| `stop_reason` = `gap` / `iterations` / `wall` | `stop_cause` = `gap` / `iteration_cap` / `wall_cap` |
| time axis in `blocks`, `block_periods`, `scenarios`, `years`, `timeindex` tables | `manifest.toml` `[time]`, `[scenarios]` |
| line endpoints in a `network` table | bundle `lines` + `buses` |

`run.document("summary")` returns any TOML document exactly as written.

## Fields beyond the 1.0 specification (older CE runs only)

The reader also surfaces stamps that only runs written by earlier CE engine
releases carry. They are not defined by the CE data format 1.0, so on a 1.0
run they are always `None` (or empty), and no code should depend on them:

| Field | Read from (older runs) |
|-------|------------------------|
| `run.provenance.register`, `.register_version` | an assumptions-register stamp (`[register]` / `[x_register]`) |
| `run.provenance.system_name`, `.system_counts`, `.system_meta`, `.labels` | an older `manifest.toml [system]` and `labels` |
| `run.stamps.integrality_gap`, `.invariants_passed` | older `summary.toml [solve]` keys |
| `run.qa.mode`, `run.qa.cost_ledger` | older `qa_report.toml` sections |
| `compare_runs` warning on register versions | only when both runs carry a register stamp |
| tables such as `congestion_rent_by_line` (`*_gbp_annual` columns), `convergence` | older run directories; read them with `run.table(name)` |

## Where the reader is more lenient than the specification

The format validator in `spec/` is the authority on conformance. To read
older runs, this reader deliberately accepts some directories the
specification would refuse:

* **No `manifest.toml`.** `Run()` opens a directory without one (an older
  run); money and the time axis then come from whatever the run does carry,
  and every assumption is recorded in `run.notes`.
* **`qa_checks` columns.** Only `check`, `family` and `status` are required,
  so older QA tables load; a 1.0 table must also carry `count`, `population`
  and `required` (and a `PASS` with `population = 0` is `qa_vacuous_pass`).
  Use the validator to check a run's QA table.
* **Doubled spellings in documents.** In `summary.toml` a canonical money key
  wins over its legacy twin (`total` over `total_gbp`) instead of being
  refused. In *tables* a doubled spelling is refused (`SchemaError`, the
  specification's `duplicate_spelling`) unless `validate=False`, which keeps
  the canonical column.
* **Value checks.** Column contracts are coarse kinds; ranges, enumerations
  and cross-table references are not checked (see the last limitation below).

## Limitations and lossy mappings

* **Existing capacity is not in the results.** Derived curtailment needs each
  site's capacity in service: cumulative new build (from `investment`) plus
  existing capacity, taken from `existing_mw=` or the bundle's `generators`.
  Without either, existing sites read as zero capacity and their timesteps are
  flagged `capacity_short`. A written `curtailment` table always wins.
* **Hybrid sites** (a renewable and a storage part behind one connection)
  follow SPEC.md section 9.4: one `renewable` and one `storage` investment row
  (plus `storage_energy` when energy is sized separately) under the candidate's
  name, so `builds()` sums them with other renewables and storage and derived
  curtailment counts the renewable part. Group by `name` to see a hybrid as a
  whole. An older run that reports a hybrid under some other component is not
  split, and its renewable part reads as zero capacity in derived curtailment.
  Sites with no capacity and no dispatch in a year are left out of derived
  curtailment.
* **Older availability echoes have no weather dimension**: one series is
  applied to every weather draw, so derived curtailment on multi-weather older
  runs is approximate.
* **Line ratings exclude line builds.** `loading` and `hours_at_limit` use the
  rating in the network / bundle table; capacity added by `ac_line` builds is
  not added to it.
* **Congestion rent follows the run's price attribution.** Where the run's
  LMP decomposition books a loss effect in the congestion component (for
  example on controllable corridors), so does the rent. Without a
  decomposition the whole surplus is reported as congestion rent and
  `loss_surplus` is NaN. An older run's own `congestion_rent_by_line` table
  (not part of 1.0), when present, uses its own loss rule and may differ.
* **Missing time axis.** A run with neither a `[time]` section, a bundle, nor
  calendar tables is weighted one hour per period (a note is recorded in
  `run.notes`). Every assumption of this kind is listed in `run.notes`.
* **Summary sections outside `[cost]`, `[solve]` and `[cost_decomposition]`**
  are passed through as parsed; only those three have money keys renamed.
* **Legacy timestamps are time-zone naive** (the older time index did not
  state a zone); 1.0 timestamps are UTC.
* The column contracts are coarse (int / float / str / bool / datetime); value
  ranges, enumerations and cross-table references are left to the format
  validator in `spec/`.

## Synthetic data

`ce_results.synthetic.write_synthetic_run(path, ...)` writes an invented
three-bus study — a run directory plus its input bundle in the 1.0 layout, or a
single directory in the older layout (`layout="legacy"`). It is generated from
formulas, not solved, but is consistent where the reader relies on it (price
components sum to the price, dispatch never exceeds availability, storage state
follows charge and discharge, the summary's curtailed energy equals the derived
curtailment). Variants `base`, `alt` and `unconverged` give comparisons
something to find. The tests and examples use only this data.

## Tests

```sh
cd results
pytest
```

All tests run on synthetic data generated by the tests themselves. When the
`spec/` component sits alongside, `tests/test_spec_interop.py` also checks that
the synthetic 1.0 output passes the specification's validator and that
curtailment derived from the specification's example bundle reproduces the
example run's own `curtailment` table; without `spec/` those tests are skipped.
