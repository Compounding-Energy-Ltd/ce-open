# CE data format — specification 1.0

| | |
|---|---|
| Version | 1.0 |
| Status | Released |
| Licence | Apache-2.0 (this text, the schemas and the reference validator) |
| Column reference | [TABLES.md](TABLES.md) (generated from [`schemas/`](schemas)) |
| Reference validator | [`src/ce_spec`](src/ce_spec) (`python -m ce_spec validate-bundle DIR`) |

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be read as in RFC 2119.

---

## 1. Scope

The CE data format describes two kinds of directory:

* a **bundle** — everything a capacity-expansion or production-cost model reads: the network, the
  existing fleet, the build options, demand and resource availability, and the declarations (money, time
  axis, scenarios) that give those numbers meaning;
* a **results directory** — what a run writes: capacity decisions, per-period dispatch, prices, flows,
  curtailment, a summary, a quality report and an index of the tables written.

The format is deliberately plain: tables are Parquet (or Arrow, or CSV), descriptors are TOML, every
number has one declared unit, and every rule a reader needs is written down here. The schemas in
[`schemas/`](schemas) are the machine-readable form of sections 8 and 9; where this text and a schema
disagree, this text wins and the schema is a defect.

Out of scope for 1.0 (a bundle MAY carry these as `x_` extension tables, section 3.4): demand-response
cohorts, fuel supply networks, hydro energy budgets, reserve products, unit-commitment state, market or
policy instruments, time-varying line ratings, and scenario deltas (edits applied on top of a bundle).

## 2. Files, encodings and names

### 2.1 Tables

A table is ONE file named `<table><ext>`:

| ext | format | notes |
|---|---|---|
| `.parquet` | Apache Parquet | canonical; any compression; dictionary encoding allowed |
| `.arrow` | Arrow IPC **file** format | |
| `.csv` | CSV | see 2.2 |
| `.csv.gz` | gzip-compressed CSV | for large result tables |

A directory MUST NOT hold the same table in two formats (`ambiguous_table`). Columns are matched by
name; their order carries no meaning. Integer columns MAY use any integer width and float columns any
float width (an integer column is accepted where a float is expected); the schema's type is what a
writer SHOULD use.

### 2.2 CSV dialect

UTF-8, a single header row, comma separator, `.` decimal point, RFC 4180 quoting (quote only where
needed). An **empty cell is null**. Booleans are `true` / `false` (case-insensitive on read). Floats MAY
be `NaN`, `inf`, `-inf`. No thousands separators, no units in cells, no trailing comment lines.

**NaN counts as null** in every format: wherever this specification or a schema requires a value (a
required column, a non-nullable column, a value required in a row's situation), a `NaN` is reported exactly
like an empty cell or an Arrow null (`null_value` / `missing_value`). `inf` and `-inf` are values and are
subject to the column's range.

### 2.3 TOML

Descriptors are TOML 1.0, UTF-8. Date-times are **offset** date-times (`2026-10-01T00:00:00Z`); a local
date-time without an offset is a `bad_type`.

### 2.4 No JSON

JSON is not a CE data format. A `*.json` file in a bundle is refused (`json_forbidden`); in a results
directory it is reported and ignored (a serving layer may drop JSON twins beside the canonical files,
but no CE reader reads them as data). JSON appears only outside this format: where a third-party
interface forces it, and as the wire encoding of HTTP APIs — for example the request and reply bodies of
the Scenario Studio API. A client of such an API parses the JSON in memory and keeps what it needs in the
formats of this specification (CSV, Parquet, Arrow, TOML); it never writes JSON files.

### 2.5 Names

Table, column and key names are lower-case snake_case (`^[a-z][a-z0-9_]*$`). Money columns carry no
currency in their name (section 5.2). Besides tables, documents and `.prov` sidecars, a directory MAY
contain documentation files whose names start with `README`, `LICENSE`, `NOTICE` or `CHANGELOG`; files
and directories starting with `.` are ignored. Anything else is an `unknown_table` / `unknown_file`
(section 3.3).

## 3. Versioning and compatibility

### 3.1 The version string

Both directory kinds declare `[format] name` and `[format] version` (`MAJOR.MINOR`) in their descriptor
(`bundle.toml` / `manifest.toml`). This document is version **1.0**. A reader implementing `1.Y` reads
every `1.X` with `X ≤ Y`.

### 3.2 Additive-only within a major version

A MINOR version MAY only **add**:

* a new optional table, column or TOML key — with a default (or a stated meaning of "absent") that
  reproduces the behaviour of the previous minor version exactly;
* a new enumeration value;
* a new issue code;
* a new legacy spelling (section 3.5).

A MINOR version MUST NOT remove or rename a table, column or key; change a type family, a unit, a sign
convention, a default or the meaning of a value; make an optional item required; or tighten a
constraint so that a previously valid directory becomes invalid. Any of these needs a new MAJOR version.

### 3.3 Refusal on unknown

Ignoring something a writer meant to bind changes the answer without notice, so:

* **Bundles (inputs) — refuse.** A reader MUST refuse a bundle that has an unknown table, column, TOML key
  or section, an unknown enumeration value, a different MAJOR version, or a NEWER MINOR version than the
  reader implements. (The newer-minor refusal is what keeps additive-only safe: an old reader never
  silently drops a column added after it was written.)
* **Results (outputs) — report.** Readers select result columns by name, so an extra output cannot
  change a number a reader reads. Unknown tables, columns, keys, files and a newer minor version are
  WARNINGS by default; a reader MAY validate strictly and refuse them. Unknown **enumeration values**
  are always errors (an aggregation by `component` or `source_type` would otherwise drop rows silently),
  as is a different MAJOR version.

### 3.4 Extensions

Tables, columns, keys, sections and directories whose name starts with `x_` are extensions. Readers
MUST ignore them and MUST NOT require them; an extension MUST NOT change the meaning of any standard
item (if it would, it is not an extension and needs a version of this specification). Extensions are
how private or experimental data travels without breaking refusal-on-unknown.

### 3.5 Legacy spellings

Some columns and keys have a historical spelling (for example `varcost_gbp_per_mwh` for `varcost_per_mwh`,
or `lmp_gbp_per_mwh` for `lmp_per_mwh`). Each schema lists them. A reader MUST accept a legacy spelling
as the canonical item (warning `deprecated_spelling`) and MUST refuse a table or document that carries
BOTH spellings of one item (`duplicate_spelling`). A legacy spelling is a spelling only: it never
changes the unit or the denomination, which always come from the declarations (section 5). Legacy
spellings are accepted until the next MAJOR version.

A historical **cost** spelling whose name does not say whether the cost is annualised or overnight
(`capex_gbp_per_mw`, `capex_gbp_per_mwh`) is NOT a legacy spelling: the same name has held both kinds of
cost, and reading an overnight cost as annualised overstates it many times over. A reader MUST refuse such
a column (`ambiguous_legacy_cost`) rather than guess; the writer renames it to the annualised
(`*_per_mw_yr`, `*_per_mwh_yr`) or overnight (`capex_overnight_per_mw`) column after checking the values.
Each schema lists these refused spellings.

## 4. Time axis

### 4.1 Investment years

`[time] investment_years` lists the planning years, strictly increasing. Every per-year value
(`year` columns, discount factors) refers to one of them.

### 4.2 Blocks and periods

Every series is cut into **blocks** declared by `[[time.blocks]]` (ids `1..B` in order). A block has
`nperiods` periods of length `dt_h` hours (one value, or one per period) and stands for `weight_h` hours
of the year:

* `kind = "chronological"`: real, contiguous time starting at `start`; `weight_h` MUST equal Σ `dt_h`.
  A full hourly year is one chronological block of 8760 (or 8784, section 4.4) periods.
* `kind = "representative"`: a sample (e.g. a typical winter day) standing for `weight_h` hours.

Σ `weight_h` over the blocks SHOULD be a full year (8760 or 8784 h; otherwise
`block_weights_not_full_year`, a warning — a deliberately partial horizon is allowed).

`period` is a **1-based** integer within its block.

### 4.3 Clock: UTC, hour-ending

All instants are **UTC** (`[time] timezone = "UTC"`, the only value in 1.0; local time is a
presentation concern). Periods are **hour-ending** (`hour_convention = "hour_ending"`): for a block
starting at instant `T0`, period `p` covers `[T0 + Σ_{q<p} dt_q, T0 + Σ_{q≤p} dt_q)` and is labelled by
the END of that interval. With hourly periods starting at midnight, period 1 is 00:00–01:00, labelled
hour-ending 01:00. A value in a period is the AVERAGE over the interval (MW), never an instantaneous
reading.

### 4.4 Leap days

`[time] leap_day` declares the policy: `keep` (default) — a chronological full year starting 1 January of
a leap year has 8784 hourly periods; `drop` — 29 February is removed and every year has 8760. A
chronological full-year block that disagrees is a `leap_day_mismatch`.

### 4.5 Scenarios and weather years

`[scenarios] names` and `probability` (summing to 1) define the scenario set; the `scenario` column of
every series is a 1-based index into `names`. Absent `[scenarios]` means one scenario, `base`,
probability 1.

`[time] weather_years` lists the weather-year **labels** (e.g. `2018`, `2019`) as an explicit list, not a
range. A series with a `weather` column carries one realisation per label; operations are evaluated
over every (scenario, weather year) pair. The optional `weather_weights` table weights the
realisations of one investment year (normalised per year; absent = equal weights).

### 4.6 Series tables, broadcasting and completeness

`demand` and `availability` are long-format series: one row per (id, scenario, block, period), with
optional `year` and `weather` columns:

| columns present | meaning |
|---|---|
| neither | the same profile in every investment year and every weather year |
| `year` | one profile per investment year, the same in every weather year |
| `year` + `weather` | one profile per (investment year, weather year) |
| `weather` only | not allowed (`weather_without_year`) |

Every series MUST be **complete**, because a missing cell would otherwise be read as zero (a zero-demand
bus, or a resource that never generates, converges cleanly to a wrong answer):

1. each (id, scenario, block) cell carries exactly the periods `1..nperiods`;
2. each id carries every (scenario, block) cell;
3. every (year, weather) group present carries the same set of ids;
4. a `year` column covers every investment year;
5. when both `demand` and `availability` carry `weather`, they carry the same (year, weather) groups.

Violations are `incomplete_series`. Every renewable generator with capacity and every renewable or
hybrid candidate MUST have an availability series (`missing_availability`).

### 4.7 From periods to annual quantities

For a per-period quantity `x` (MW) in investment year `y`, the expected annual energy (MWh) is

```
E_y = Σ_k π_k · Σ_w ω_{y,w} · Σ_b (weight_h_b / Σ_p dt_{b,p}) · Σ_p x_{k,w,b,p} · dt_{b,p}
```

with `π_k` the scenario probability and `ω_{y,w}` the normalised weather weight. A time-weighted average
price uses the same weights on `λ · dt` over Σ of the same weights on `dt`; a load-weighted price weights
by demand energy instead. Readers MUST say which average they report.

## 5. Money

### 5.1 Declaration

`[money] currency` (ISO 4217, e.g. `USD`, `GBP`, `EUR`) and `[money] dollar_year` (the constant-money
year) in `bundle.toml` declare the denomination of EVERY monetary value in the bundle; a results
`manifest.toml` repeats them for every monetary value in the run, and they MUST match the input bundle's
(`currency_mismatch`). All money is **real** (constant `dollar_year` terms). There is no per-column or
per-row currency. A reader MUST NOT combine directories with different declarations without an explicit
conversion.

### 5.2 Neutral names

Money columns are named by what they measure, not by the currency: `capex_per_mw_yr`, `varcost_per_mwh`,
`lmp_per_mwh`. `currency` in a unit (TABLES.md) means the declared currency in the declared dollar year.

### 5.3 Capital costs

A capital cost is either **annualised** (`*_per_mw_yr`, `*_per_mwh_yr`, `capex_per_yr`: currency per
unit per year, charged in every investment year the capacity is in service) or **overnight**
(`capex_overnight_per_mw`, `interconnection_cost_per_kw`: a one-off amount). A model works with annualised
costs; an overnight cost is annualised with the capital recovery factor

```
CRF(r, n) = r (1 + r)^n / ((1 + r)^n − 1)        (CRF = 1/n when r = 0)
```

using the row's `annualisation_rate` and `lifetime_years`, or, when the row gives none, the bundle's
`[money] default_annualisation_rate` and `default_lifetime_years`. An overnight cost with neither is
refused (`overnight_cost_without_annualisation`) — it is never silently priced from somewhere else. A
candidate with no capital cost in either form is refused (`missing_capex`): a free build option is
almost always a data error. When a row gives both forms, the annualised one is used.

### 5.4 Discounting

`[system] discount_factors` gives one present-value factor per investment year. Result costs in
`summary.toml` are present values over the horizon computed with these factors; per-period prices
(`lmp`) and per-row costs are undiscounted.

### 5.5 Conversion

A bundle converted from another currency records `[money] converted_from`, `conversion_rate` (target
units per source unit, applied to every monetary value) and `conversion_source`.

## 6. Units, signs and vocabularies

### 6.1 Units

| quantity | unit | column suffix |
|---|---|---|
| power, capacity | MW | `_mw` |
| energy, storage capacity | MWh | `_mwh` |
| price, variable cost | currency/MWh | `_per_mwh` |
| annualised capital / fixed cost | currency/MW-yr, currency/MWh-yr | `_per_mw_yr`, `_per_mwh_yr` |
| overnight cost | currency/MW, currency/kW | `_overnight_per_mw`, `_per_kw` |
| emissions intensity | tCO2/MWh | `co2_t_per_mwh` |
| duration, time | hours | `_h` |
| fraction | dimensionless in [0, 1] | `_frac` |
| susceptance | per unit on a 100 MVA base | `_pu` |
| voltage | kV | `_kv` |
| location | degrees, WGS 84 | `lat`, `lon` |
| length | km | `_km` |

### 6.2 Signs

* **Flows** are signed: positive from the line's `from_bus` to its `to_bus` as declared in the bundle.
* **Interface flow** = Σ `orientation` × member-line flow, held within `[−max_rev_mw, max_fwd_mw]`; both
  limits are non-negative magnitudes, null = unbounded.
* **Dispatch** values are non-negative. Direction is carried by the row kind: storage charging is a
  `storage_charge` row whose `dispatch_mw` is the power withdrawn. Net injection at a bus is
  Σ thermal + renewable + storage_discharge − storage_charge.
* **Demand** is a non-negative withdrawal.
* **Investment** values are cumulative capacity in service: builds ≥ 0; retirements of existing capacity
  are rows with `component = "retirement"` and `built_mw ≤ 0`.
* **Costs** are positive; a negative variable cost is a credit (e.g. a production incentive).
* **LMP** is the price of withdrawal at the bus: `lmp_per_mwh = lmp_energy + lmp_congestion + lmp_loss`.

### 6.3 Identifiers and references

Component tables reference buses (and interfaces reference lines) by integer `id`. Integer ids are
load-order keys: a rebuilt bundle MAY re-assign them, so nothing outside one bundle may key on them.
Names (`buses.name`, `lines.name`, `generators.name`, `storage.name`, `candidates.candidate_id`) are the
stable keys: availability is keyed by name, and result tables reference buses, lines and components by
name. Generator names, storage names and candidate ids share one name space, and so do line names and
HVDC corridor names (`flows.line` and network `investment` rows reference either), so a name may not be
used twice within either group (`name_collision`). A key
that must survive a rebuild SHOULD be stamped with the `generation` of the build it belongs to
(section 7).

### 6.4 Technology codes

`technology` is an open vocabulary (new codes need no new version). Writers SHOULD use these codes where
they apply: `gas_cc`, `gas_ct`, `gas_steam`, `coal`, `oil`, `nuclear`, `biomass`, `geothermal`,
`hydro_reservoir`, `hydro_ror`, `pumped_hydro`, `wind_onshore`, `wind_offshore`, `solar_pv`,
`solar_thermal`, `battery`, `solar_pv_battery`, `hydrogen_ct`, `hydrogen_storage`, `compressed_air`,
`ac_line`, `hvdc`. A code says what the plant is; `kind` (a closed vocabulary) says how it is modelled.
Converters into this format SHOULD map their own vocabularies onto these codes (for example
`ccgt` → `gas_cc`, `ocgt` → `gas_ct`, `solar` → `solar_pv`), so that a bundle passed between tools keeps one
vocabulary.

## 7. Provenance sidecars (`.prov`)

Any file MAY have a provenance sidecar named `<file>.prov` next to it (`candidates.parquet.prov`
describes `candidates.parquet`).

### 7.1 Grammar (normative)

* UTF-8 text, one entry per line.
* A **key line** is `key=value`. The key is one or more characters other than whitespace, `=` and `#`.
  Leading blanks and blanks around the FIRST `=` are ignored. The value is the rest of the line with
  surrounding blanks removed, and with ONE surrounding pair of double quotes removed if present. There is
  no escape processing and no inline comment (a `#` after the `=` is part of the value); a value
  cannot span lines.
* Blank lines and lines whose first non-blank character is `#` are comments.
* Any other line is ignored by readers (validators report `prov_unparsed_line`).
* If a key appears more than once, the FIRST value wins (validators report `prov_duplicate_key`).
* All values are strings; each standard key below says how to read it.

```text
# provenance of candidates.parquet
source=synthetic construction for the examples
license=Apache-2.0
built_utc=2026-10-01T00:00:00Z
generation=toy3-g1
rows=4
sha256=9f2c4e6a8b0d1f3e5a7c9b1d3f5e7a9c0b2d4f6e8a1c3e5b7d9f0a2c4e6b8d0f
notes="  leading blanks kept because the value is quoted"
```

### 7.2 Standard keys

| key | meaning | checked |
|---|---|---|
| `source` | where the data comes from | |
| `license` | SPDX identifier of the data's licence | |
| `built_utc` | instant the file was written, ISO 8601 UTC ending in `Z` | `prov_bad_timestamp` |
| `tool`, `tool_version` | the writer | |
| `sha256` | lower-case hex SHA-256 of the described file | `prov_hash_mismatch` |
| `rows` | data rows of the described table | `prov_rows_mismatch` |
| `generation` | identifier of the build the file belongs to | |
| `derived_from`, `source_generation` | a derived file names its source file and the source's `generation` it was cut from | `stale_derived_table` |
| `notes` | free text | |

Any other key is allowed and carried as text. A sidecar whose file does not exist is a `prov_orphan`.

### 7.3 The vintage rule

A table cut from another (an availability store cut from a candidate table, say) records the source's
`generation` as `source_generation` and the source file as `derived_from`. When the source's own sidecar
records a different `generation`, the derived table is stale — keyed against a build that no longer
exists — and is refused (`stale_derived_table`).

## 8. The bundle

### 8.1 Layout

```text
<bundle>/
  bundle.toml                    required   descriptor (format, bundle, money, time, scenarios, system)
  buses.parquet                  required
  demand.parquet                 required
  lines.parquet                  optional   AC branches (DC power flow)
  hvdc.parquet                   optional   controllable corridors
  interfaces.parquet             optional   path / flowgate limits on groups of lines
  interface_limits_monthly.parquet optional seasonal interface limits
  generators.parquet             optional   existing generating fleet
  storage.parquet                optional   existing storage fleet
  candidates.parquet             optional   build options
  availability.parquet           optional*  capacity factors (*required when anything is availability-bounded)
  weather_weights.parquet        optional
  peak_demand.parquet            optional
  <any file>.prov                optional   provenance sidecars
  x_*                            optional   extensions (ignored)
```

Every column, type, unit and range is in [TABLES.md](TABLES.md). The rules beyond a single column:

### 8.2 `bundle.toml`

Declares the format (section 3), the money (section 5), the time axis and scenarios (section 4) and
system-wide parameters: `voll_per_mwh` (the price of unserved energy), `planning_reserve_margin`
(fraction of peak), `discount_factors` (one per investment year) and `co2_price_per_t`.

### 8.3 Network

* `lines`: a positive flow runs `from_bus → to_bus`; `from_bus ≠ to_bus` (`self_loop`). A row is
  existing, a candidate, or both: an existing line with `candidate = true` and `delta_rating_mw > 0` is a
  reinforcement; a line with `existing = false, candidate = true` is greenfield and adds `rating_mw` when
  built. A line that is neither is reported (`inert_line`). `capex_per_yr` is the annualised cost of the
  whole build.
* `loss_frac` (written ℓ) defines the line's losses as a function of its flow `f` (MW): the reference model
  is **linear**, `loss(f) = ℓ · |f|`. A model with a quadratic loss curve MUST calibrate it to the same loss
  at rated flow, `loss(f) = ℓ · f² / rating_mw` (so both give `ℓ · rating_mw` at `|f| = rating_mw`), and
  MAY linearise it piecewise. `ℓ = 0` (the default) is a lossless line. (For comparison, GenX's
  `Line_Loss_Percentage` is the linear form.)
* `hvdc`: controllable corridors; flow is a decision bounded by the installed capacity, scaled by
  `converter_efficiency`.
* `interfaces`: one row per (interface, member line); the interface's name, limits and source are
  identical on all its rows (`inconsistent_interface`). `interface_limits_monthly` replaces the limits
  in the calendar months it lists (UTC month of the period's start).
* `buses.hosting_*`: an optional cap on new injection capacity at a bus, optionally expandable at an
  annualised cost (`missing_value` when expandable without a cost).

### 8.4 Existing fleet

`generators` holds existing capacity only. `kind = "thermal"`: dispatchable within `[pmin, capacity]`
(unit-commitment columns optional); `kind = "renewable"`: output in a period at most capacity × `cf` from
`availability`. `storage` holds existing storage: energy capacity = `existing_mw × duration_h`;
round-trip efficiency = `eta_charge × eta_discharge`.

### 8.5 Candidates

One row per build option, keyed by `candidate_id`. The model chooses any capacity in `[0, max_mw]`
(whole units of `unit_size_mw` when `integer_build`). Costs per section 5.3. `kind`:

* `thermal`, `renewable` — as in the fleet; renewable needs an availability series keyed by
  `candidate_id`;
* `storage` — needs `duration_h`, or `max_mwh` with `capex_per_mwh_yr` to size energy independently;
* `hybrid` — a renewable part (`generation_mw`) and a storage part (`storage_mw`, `duration_h`) behind
  one point of interconnection limited to `max_mw`; needs an availability series for the renewable part.

`lead_time_years` makes the earliest in-service year the first investment year plus the lead.

### 8.6 Series

`demand` (MW per bus) and `availability` (`cf` per site name) follow section 4.6. `weather_weights`
weights weather realisations; `peak_demand` gives the peak a reserve margin is measured against
(default: the maximum of the demand series).

## 9. Results

### 9.1 Layout

```text
<run>/
  manifest.toml             required   format, run identity, money, time axis, inputs
  summary.toml              required   cost, solve status, headline builds
  investment.parquet        required   capacity decisions
  generation.parquet        optional   per-period dispatch
  lmp.parquet               optional   nodal prices (+ decomposition)
  flows.parquet             optional   per-period line flows
  curtailment.parquet       optional   available-but-unused output
  storage_state.parquet     optional   state of charge
  transmission_losses.parquet optional per-line losses
  qa_checks.parquet         optional   quality checks
  qa_report.toml            optional   overall quality status
  results_manifest.toml     optional   index of the tables written (written last)
  *.html|*.png|*.svg|*.pdf  optional   visual artefacts
```

### 9.2 `manifest.toml`

Repeats the input bundle's money and time axis so the run directory is self-describing.
`[time] weather_axis` declares how the `weather` column of result series is spelled: `label` (the weather
year itself, the default) or `ordinal` (its 1-based position in `weather_years`). A writer whose engine
counts weather realisations rather than labelling them MUST declare `ordinal` rather than leave a reader
to guess.

### 9.3 `summary.toml`

`[cost] total = investment_capex + operational_opex` (`cost_identity_mismatch`), the present value of the
objective (section 5.4). `[solve] stop_cause` says why the run stopped: `gap` (the target was met),
`iteration_cap` or `wall_cap` (a budget ran out — the result is then a feasible plan whose optimality is
bounded only by `relative_gap`). `converged` is true exactly when `stop_cause = "gap"`.

### 9.4 `investment`

Cumulative capacity in service per (component, year) — section 6.2. Storage energy sized independently of
power is a `storage_energy` row in `built_mwh`. Components with nothing built MAY be omitted.

**Hybrids.** A built `hybrid` candidate is reported as one row per part, every row named by its
`candidate_id` and carrying its technology code:

* a `renewable` row — the renewable part's capacity (`built_mw`, at most `generation_mw`);
* a `storage` row — the storage part's power (`built_mw`, at most `storage_mw`);
* a `storage_energy` row (`built_mwh`) only when the storage part's energy is sized independently of its
  power; otherwise its energy is `built_mw × duration_h` of the storage row.

There is no `hybrid` component: the rows aggregate with the other renewable and storage rows, and a reader
that needs the hybrid as a whole groups them by name (or joins `candidates.kind`). The per-period tables
follow the same naming: the renewable part's output is a `renewable` generation row and a `curtailment` row
with `site_name = candidate_id`; the storage part's charge and discharge are `storage_charge` /
`storage_discharge` generation rows and its state of charge a `storage_state` row, all under the same name.
The point-of-interconnection limit `max_mw` bounds the net export of the two parts together.

### 9.5 Per-period tables

`generation`, `lmp`, `flows`, `curtailment`, `storage_state` and `transmission_losses` share the axis
columns `year, scenario, weather, block, period` (section 4) and reference buses, lines and components by
name. Rows that are zero MAY be omitted (a reader treats an absent row as zero), except in `lmp`, where
every (bus, period) the run priced is present.

**LMP decomposition.** `lmp_per_mwh` is the dual of the bus energy balance — the marginal cost of one more
MWh withdrawn at the bus in the period, undiscounted, in currency/MWh. The optional decomposition is an
ATTRIBUTION that sums EXACTLY to the total (`lmp_decomposition_mismatch`, relative tolerance 1e-6):

* `lmp_energy` — the price at the reference bus;
* `lmp_loss` — `−lmp_energy × MLF`, with MLF the bus's marginal loss factor relative to the reference
  bus under the run's loss model;
* `lmp_congestion` — the remainder. Cost terms the loss model does not attribute (losses on controllable
  corridors, wheeling charges, interface costs) land here.

All three components or none (`partial_decomposition`). `floor_slack_at_bus` marks a period in which a
must-run unit at the bus was below its floor: the price there is not a clean marginal cost.

**Curtailment.** `curtailed_mw = available_mw − dispatch_mw` exactly (`curtailment_identity_mismatch`),
with `available_mw` = capacity in service × `cf`.

### 9.6 Quality report

`qa_checks` has one row per check the run performed on its own results. A check that examined nothing
(`population = 0`) is `NA`, never `PASS` (`qa_vacuous_pass`): a check that cannot fail must not report
success. The overall `qa_report.toml [qa] status` is derived, in this order:

1. `FAIL` if any check is FAIL;
2. else `WARN` if any check is WARN;
3. else `INCOMPLETE` if any **required** check is NA;
4. else `PASS`.

The counts `n_pass`, `n_warn`, `n_fail`, `n_na`, `n_required_na` MUST agree with the rows
(`qa_status_inconsistent`).

### 9.7 `results_manifest.toml`

The index of the tables a run actually wrote, written last: name, formats on disk, row count and columns.
It is the closed list a server may offer for download — a file not listed is never served. Every table on
disk is listed and every listed table is on disk with the listed row count and columns
(`index_mismatch`).

## 10. Validation and issue codes

A conforming validator reports every problem as an issue with a stable snake_case **code**, a severity
(`error` or `warning`), a message and, where it applies, the file, table, column and up to five 0-based
data-row indices (row 0 is the first data row; in a CSV that is line 2). A directory is **valid** when it
has no error; warnings never fail validation. Codes are part of this specification: within a major
version a code is never renamed, removed or given a new meaning. The full catalogue, with default
severities, is generated into [TABLES.md](TABLES.md#issue-codes); section 3.3 says which codes change
severity for results.

The reference validator implements every rule of this document:

```text
python -m ce_spec validate-bundle  <bundle-dir>
python -m ce_spec validate-results <run-dir> [--bundle <bundle-dir>] [--strict]
```

With `--bundle`, results are also checked against their inputs: matching money, every referenced bus,
line and component name exists, and no flow exceeds `rating_mw + delta_rating_mw` (`flow_exceeds_rating`,
a warning — the check cannot see time-varying ratings).

## 11. Mapping from existing layouts, and known limitations

The format was derived from the layouts CE tooling already reads and writes. Mapping those layouts onto
1.0 is mostly renaming (the legacy spellings of section 3.5), but some mappings lose or reshape
information:

| existing layout | in 1.0 | what changes |
|---|---|---|
| separate thermal and renewable tables; one row may hold existing capacity AND a build range (`existing_mw` + `candidate_max_mw`) | `generators` (existing, `kind`) + `candidates` (build options) | one source row may become two rows with different names; their results are reported under two names |
| series keyed by integer site id | `availability.site_name` only | name-keying is required |
| result `weather` = 1-based ordinal of the sorted weather labels | `weather` = label by default | write `[time] weather_axis = "ordinal"` to keep the ordinal |
| `storage_energy_mwh` investment rows with MWh in `built_mw` | `storage_energy` rows with `built_mwh` | the value moves column |
| interface members keyed by a line key string | `interfaces.line_id` | needs the line id lookup |
| currency declared beside the bundle (registry or manifest), cost year declared elsewhere | `[money] currency` + `dollar_year` in `bundle.toml` | the dollar year becomes mandatory |
| a `summary.toml` with many engine-specific sections, a `manifest.toml` without `[format]` / `[money]` | 1.0 documents | engine-specific sections are unknown keys: warnings by default, refusals with `--strict`; the missing sections must be added |
| `summary.toml [builds_mw_by_technology]` nested by category | flat technology → MW map | category level dropped |
| no per-period curtailment table | `curtailment` defined | readers derive it from availability, investment and generation |
| hosting limits as overnight expansion prices | annualised `hosting_expand_cost_per_mw_yr` | needs the capital recovery factor |

Known limitations of 1.0:

* demand-response cohorts, hydro energy budgets, fuels, reserves, unit-commitment state, market and
  policy instruments, time-varying line ratings and contingency definitions beyond the `contingency` flag
  have no standard tables (carry them as `x_` extensions). The converters in this repository share one
  informative convention for emission caps, a `bundle.toml` array `[[x_policy]]` with `name`,
  `kind = "emission_cap_t"`, `pollutant`, `value` (t/yr) and optional `zones` (`buses.zone` values;
  absent = system-wide), `technologies` and `years`; it is a candidate for a standard table in a later
  minor version;
* the clock is UTC hour-ending only; sub-hourly periods are expressed through `dt_h`;
* `lmp_loss` is meaningful only for lines in the DC power-flow model; other losses land in congestion;
* `flow_exceeds_rating` ignores time-varying ratings and treats a candidate's reinforcement as built;
* the validator checks structure and declared identities, not physics: it does not re-solve the energy
  balance (that is what `qa_checks` records from inside the run).

## Appendix A. Changelog

* **1.0** (2026-10-01) — first release.
