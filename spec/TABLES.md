# CE data format 1.0 — column and key reference

<!-- GENERATED from schemas/ by `python -m ce_spec render-docs`. Do not edit by hand. -->

The normative rules (versioning, units, signs, time axis, money, provenance) are in SPEC.md; this
file lists every table, column and TOML key the format defines. `currency` in a unit means the
bundle's declared currency in its declared dollar year.

## Bundle (inputs)

### `bundle.toml`

*required · since 1.0*

The bundle descriptor: format version, money, time axis, scenarios and system-wide parameters.

| key | type | req. | range / values | default | description |
|---|---|---|---|---|---|
| `format` | table | yes |  |  | Format identity. |
| `format.name` | string | yes | one of `ce-bundle` |  | Always `ce-bundle`. |
| `format.version` | string | yes |  |  | `MAJOR.MINOR` of this specification the bundle was written against (e.g. `1.0`). |
| `bundle` | table | yes |  |  | Bundle identity. |
| `bundle.name` | string | yes |  |  | Bundle name. |
| `bundle.description` | string |  |  |  | Free-text description. |
| `bundle.created_utc` | datetime |  |  |  | Creation instant, an offset date-time in UTC (e.g. `2026-10-01T12:00:00Z`). |
| `bundle.license` | string |  |  |  | SPDX licence identifier of the bundle's data (e.g. `CC-BY-4.0`). |
| `bundle.source` | string |  |  |  | Free-text statement of where the data comes from. |
| `money` | table | yes |  |  | The denomination of EVERY monetary value in the bundle. |
| `money.currency` | string | yes |  |  | ISO 4217 code, three upper-case letters (e.g. `USD`, `GBP`, `EUR`). |
| `money.dollar_year` | int | yes | [1900, 2200] |  | The constant-money (real) year every monetary value is expressed in. |
| `money.default_annualisation_rate` | float |  | [0, 1] |  | Real discount rate used to annualise overnight costs on rows that give none. |
| `money.default_lifetime_years` | float |  | (0, ∞) |  | Economic life used to annualise overnight costs on rows that give none. |
| `money.converted_from` | string |  |  |  | When the bundle was converted from another currency: the source ISO 4217 code. |
| `money.conversion_rate` | float |  | (0, ∞) |  | Target units per source unit applied to every monetary value. |
| `money.conversion_source` | string |  |  |  | Where the conversion rate comes from. |
| `time` | table | yes |  |  | The time axis. |
| `time.timezone` | string | yes | one of `UTC` |  | Always `UTC` in 1.0. Local time is a presentation concern. |
| `time.hour_convention` | string | yes | one of `hour_ending` |  | Always `hour_ending` in 1.0: period p covers [start + (p−1)·dt, start + p·dt). |
| `time.investment_years` | array[int] | yes |  |  | Investment (planning) years, strictly increasing. |
| `time.weather_years` | array[int] |  |  |  | Weather-year labels the series may carry, as an explicit list (not a range). |
| `time.leap_day` | string |  | one of `keep`, `drop` | `keep` | `keep`: a chronological full year on a leap year has 8784 hourly periods. `drop`: 29 February is removed, every year has 8760. |
| `time.blocks` | array_of_tables | yes |  |  | The time blocks every series is cut into (chronological or representative). |
| `time.blocks[].id` | int | yes | [1, ∞) |  | Block id; ids are 1..B in order. |
| `time.blocks[].nperiods` | int | yes | [1, ∞) |  | Number of periods in the block. |
| `time.blocks[].dt_h` | float or array[float] | yes |  |  | Period length in hours: one value for all periods, or one per period (length nperiods). |
| `time.blocks[].weight_h` | float | yes | (0, ∞) |  | Hours of the year the block stands for. A chronological block has weight_h = Σ dt_h. |
| `time.blocks[].kind` | string | yes | one of `chronological`, `representative` |  | `chronological`: real contiguous time from `start`. `representative`: a sample standing for `weight_h` hours. |
| `time.blocks[].start` | datetime |  |  |  | Start instant of period 1 (UTC offset date-time). Required for chronological blocks. |
| `time.blocks[].name` | string |  |  |  | Display name (e.g. `winter_peak_day`). |
| `scenarios` | table |  |  |  | Scenario set (absent = one scenario `base` with probability 1). |
| `scenarios.names` | array[string] | yes |  |  | Scenario names; the `scenario` column of a series is the 1-based index into this list. |
| `scenarios.probability` | array[float] | yes |  |  | Scenario probabilities, same length as `names`, summing to 1. |
| `system` | table |  |  |  | System-wide parameters. |
| `system.voll_per_mwh` | float |  | (0, ∞) |  | Value of lost load: the price of unserved energy (currency/MWh). |
| `system.planning_reserve_margin` | float |  | [0, ∞) |  | Planning reserve margin as a fraction of peak demand. |
| `system.discount_factors` | array[float] |  |  |  | One present-value factor per investment year (same length as `investment_years`). |
| `system.co2_price_per_t` | float |  | [0, ∞) |  | Carbon price applied to direct emissions (currency/tCO2). |

Legacy spellings: `system.voll_gbp_per_mwh` → `system.voll_per_mwh`, `system.prm` → `system.planning_reserve_margin`.

### `buses`

*required · one row per bus · since 1.0*

The electrical nodes of the network. Every component table references a bus by its integer `id`; result tables reference a bus by its `name`.

Primary key: (`id`).
Unique: (`name`).

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `id` | int32 | yes |  | [1, ∞) |  | Bus identifier, unique within the bundle. Integer ids are load-order keys and may be re-assigned when a bundle is rebuilt; join across bundles by `name`. |
| `name` | string | yes |  |  |  | Stable bus name, unique within the bundle. The key result tables use (`bus` columns in results hold this value). |
| `zone` | string |  |  |  | `default` | Zone (balancing area, price zone or reporting region) the bus belongs to. |
| `lat` | float64 |  | degree | [-90, 90] |  | Latitude, WGS 84. |
| `lon` | float64 |  | degree | [-180, 180] |  | Longitude, WGS 84. |
| `voltage_kv` | float64 |  | kV | (0, ∞) |  | Nominal voltage of the bus. |
| `label` | string |  |  |  |  | Human-readable display name (e.g. a substation name). Never used as a key. |
| `hosting_mw` | float64 |  | MW | [0, ∞) |  | Hosting limit: the most NEW injection capacity that may connect at the bus. Null = no limit. |
| `hosting_expandable` | bool |  |  |  | `false` | True when the hosting limit may be raised at a cost (`hosting_expand_cost_per_mw_yr`). |
| `hosting_expand_cost_per_mw_yr` | float64 |  | currency/MW-yr | [0, ∞) |  | Annualised cost of raising the hosting limit by 1 MW. Required when `hosting_expandable` is true. |
| `hosting_expand_max_mw` | float64 |  | MW | [0, ∞) |  | Largest permitted expansion of the hosting limit. Null = unbounded. |

### `demand`

*required · one row per bus-period · since 1.0*

Electricity demand per bus and period, in long format. The optional `year` column gives each investment year its own profile; the optional `weather` column (which needs `year`) gives several weather-year realisations per investment year. A table without `year` applies to every investment year; a table without `weather` applies to every weather year. Every series must be complete (SPEC.md §4.6).

Primary key: (`bus`, `scenario`, `block`, `period`, `year`, `weather`) — key columns absent from a file are left out of the key.
Reference: `bus` → `buses.id`.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `bus` | int32 | yes |  |  |  | Bus (`buses.id`). |
| `scenario` | int16 | yes |  | [1, ∞) |  | Scenario index, 1-based, into `[scenarios] names` of bundle.toml. |
| `block` | int16 | yes |  | [1, ∞) |  | Time block id (`[[time.blocks]] id`). |
| `period` | int32 | yes |  | [1, ∞) |  | Period within the block, 1-based (hour-ending: period 1 is the first interval). |
| `mw` | float64 | yes | MW | [0, ∞) |  | Average withdrawal over the period. |
| `year` | int16 |  | year |  |  | Investment year (one of `[time] investment_years`). Absent = every investment year. |
| `weather` | int16 |  | year |  |  | Weather-year label (one of `[time] weather_years`). Needs `year`. Absent = every weather year. |

### `availability`

*optional · one row per site-period · since 1.0*

Per-period availability (capacity factor) of every availability-bounded resource: renewable generators and renewable / hybrid candidates. Output in a period is at most capacity × cf. Same axis and completeness rules as `demand`. Sites are keyed by NAME, never by integer id, so a table survives an id re-assignment.

Primary key: (`site_name`, `scenario`, `block`, `period`, `year`, `weather`) — key columns absent from a file are left out of the key.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `site_name` | string | yes |  |  |  | A `generators.name` (kind renewable) or a `candidates.candidate_id` (kind renewable or hybrid). |
| `scenario` | int16 | yes |  | [1, ∞) |  | Scenario index, 1-based. |
| `block` | int16 | yes |  | [1, ∞) |  | Time block id. |
| `period` | int32 | yes |  | [1, ∞) |  | Period within the block, 1-based. |
| `cf` | float32 | yes | fraction | [0, 1] |  | Available output as a fraction of installed capacity, averaged over the period. |
| `year` | int16 |  | year |  |  | Investment year. Absent = every investment year. |
| `weather` | int16 |  | year |  |  | Weather-year label. Needs `year`. Absent = every weather year. |

### `candidates`

*optional · one row per candidate · since 1.0*

Build options: the capacity a model MAY add, where, and at what cost. The model chooses how much of `max_mw` to build (0 is always allowed). Capital cost is given either annualised (`capex_per_mw_yr`) or overnight (`capex_overnight_per_mw`) together with a capital recovery factor's inputs; see SPEC.md §5.3. Renewable and hybrid candidates need an `availability` series keyed by `candidate_id`.

Primary key: (`candidate_id`).
Reference: `bus` → `buses.id`.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `candidate_id` | string | yes |  |  |  | Candidate identifier, unique within the table and distinct from every `generators.name`. Keys `availability.site_name` and the results' component names. |
| `technology` | string | yes |  |  |  | Technology code (open vocabulary; see SPEC.md §6.4). |
| `kind` | string | yes |  | one of `thermal`, `renewable`, `storage`, `hybrid` |  | Build model: `thermal`, `renewable` (availability-bounded), `storage`, or `hybrid` (a renewable part and a storage part behind one point of interconnection). |
| `bus` | int32 | yes |  |  |  | Connection bus (`buses.id`). |
| `max_mw` | float64 | yes | MW | (0, ∞) |  | Largest buildable capacity. For a hybrid, the point-of-interconnection export limit. |
| `capex_per_mw_yr` | float64 |  | currency/MW-yr | [0, ∞) |  | Annualised capital cost per MW built. Takes precedence over the overnight form when both are given. |
| `capex_overnight_per_mw` | float64 |  | currency/MW | [0, ∞) |  | Overnight capital cost per MW built. Needs a capital recovery factor (row `annualisation_rate` + `lifetime_years`, or the bundle's `[money]` defaults). |
| `annualisation_rate` | float64 |  | fraction/yr | [0, 1] |  | Real discount rate r of the capital recovery factor CRF = r(1+r)^n / ((1+r)^n − 1). |
| `lifetime_years` | float64 |  | year | (0, ∞) |  | Economic life n of the capital recovery factor. |
| `fom_per_mw_yr` | float64 |  | currency/MW-yr | [0, ∞) | `0.0` | Fixed O&M cost per MW built. |
| `varcost_per_mwh` | float64 |  | currency/MWh |  | `0.0` | Variable cost of energy. May be negative where a production credit applies. |
| `interconnection_cost_per_kw` | float64 |  | currency/kW | [0, ∞) | `0.0` | Overnight network-connection cost per kW built; annualised with the same capital recovery factor as the overnight capex. |
| `lead_time_years` | int16 |  | year | [0, ∞) |  | Construction lead time: the earliest in-service year is the first investment year plus this. |
| `duration_h` | float64 |  | h | (0, ∞) |  | Storage energy-to-power ratio (storage and hybrid rows; required there unless `max_mwh` sizes the energy separately). |
| `max_mwh` | float64 |  | MWh | [0, ∞) |  | Largest buildable energy capacity when energy is sized independently of power (storage rows). Null = energy fixed by `duration_h`. |
| `capex_per_mwh_yr` | float64 |  | currency/MWh-yr | [0, ∞) | `0.0` | Annualised capital cost per MWh of independently sized energy capacity. |
| `generation_mw` | float64 |  | MW | [0, ∞) |  | Hybrid rows: largest buildable renewable (generating) part. Required on hybrid rows. |
| `storage_mw` | float64 |  | MW | [0, ∞) |  | Hybrid rows: largest buildable storage part. Required on hybrid rows. |
| `co2_t_per_mwh` | float64 |  | tCO2/MWh | [0, ∞) | `0.0` | Direct CO2 emission intensity of output (thermal rows). |
| `firm_capacity_frac` | float64 |  | fraction | [0, 1] |  | Capacity credit toward the planning reserve margin. |
| `integer_build` | bool |  |  |  | `false` | True when the candidate is built in whole units of `unit_size_mw`. |
| `unit_size_mw` | float64 |  | MW | (0, ∞) |  | Unit size for integer builds. Required when `integer_build` is true. |
| `cf_mean` | float64 |  | fraction | [0, 1] |  | Mean capacity factor of the availability series. Informational; never used in place of the series. |
| `lat` | float64 |  | degree | [-90, 90] |  | Latitude of the site, WGS 84. |
| `lon` | float64 |  | degree | [-180, 180] |  | Longitude of the site, WGS 84. |

Legacy spellings (accepted with a `deprecated_spelling` warning): `site_id` → `candidate_id`, `bus_id` → `bus`, `capex_per_mw_annualised` → `capex_per_mw_yr`, `fom_gbp_per_mw` → `fom_per_mw_yr`, `varcost_gbp_per_mwh` → `varcost_per_mwh`, `interconnection_gbp_per_kw` → `interconnection_cost_per_kw`.

Refused historical spellings (`ambiguous_legacy_cost`, an error): `capex_gbp_per_mw` — rename it to capex_per_mw_yr if the values are annualised (per MW per year) or to capex_overnight_per_mw if they are overnight (per MW, one-off); `capex_gbp_per_mwh` — rename it to capex_per_mwh_yr if the values are annualised (per MWh of energy capacity per year); the format has no overnight energy-capacity cost column, so annualise an overnight value first (SPEC.md §5.3).

### `generators`

*optional · one row per unit · since 1.0*

The EXISTING generating fleet. Build options live in `candidates`, never here. `kind = "thermal"` rows are dispatchable up to their capacity; `kind = "renewable"` rows are bounded per period by capacity × availability, so each needs a series in `availability` keyed by its `name`.

Primary key: (`id`).
Unique: (`name`).
Reference: `bus` → `buses.id`.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `id` | int32 | yes |  | [1, ∞) |  | Generator identifier, unique within the table. |
| `name` | string | yes |  |  |  | Stable generator name, unique within the table and distinct from every `candidates.candidate_id` (both key `availability.site_name`). |
| `bus` | int32 | yes |  |  |  | Connection bus (`buses.id`). |
| `technology` | string | yes |  |  |  | Technology code (open vocabulary; see SPEC.md §6.4 for the recommended codes). |
| `kind` | string | yes |  | one of `thermal`, `renewable` |  | Operating model: `thermal` (dispatchable) or `renewable` (availability-bounded; includes run-of-river and reservoir hydro modelled by an availability series). |
| `existing_mw` | float64 | yes | MW | [0, ∞) |  | Installed capacity at the start of the horizon. |
| `varcost_per_mwh` | float64 |  | currency/MWh |  | `0.0` | Variable cost of energy (fuel + variable O&M). May be negative where a production credit applies. |
| `fom_per_mw_yr` | float64 |  | currency/MW-yr | [0, ∞) | `0.0` | Fixed O&M cost. |
| `pmin_frac` | float64 |  | fraction | [0, 1] |  | Minimum stable output as a fraction of capacity when committed (thermal). |
| `ramp_frac_per_h` | float64 |  | fraction/h | [0, ∞) |  | Ramp limit as a fraction of capacity per hour (thermal). |
| `min_up_h` | int16 |  | h | [0, ∞) |  | Minimum up time (thermal, unit commitment). |
| `min_down_h` | int16 |  | h | [0, ∞) |  | Minimum down time (thermal, unit commitment). |
| `startup_cost` | float64 |  | currency/start | [0, ∞) |  | Cost of one start (thermal, unit commitment). |
| `noload_cost_per_h` | float64 |  | currency/h | [0, ∞) |  | No-load cost while committed (thermal, unit commitment). |
| `co2_t_per_mwh` | float64 |  | tCO2/MWh | [0, ∞) | `0.0` | Direct CO2 emission intensity of output. |
| `firm_capacity_frac` | float64 |  | fraction | [0, 1] |  | Capacity credit toward the planning reserve margin. |
| `retirable` | bool |  |  |  | `false` | True when the model may retire the unit before the end of the horizon. |
| `retirement_year` | int16 |  | year |  |  | Announced calendar year after which the unit is no longer available. Null = none. |
| `lat` | float64 |  | degree | [-90, 90] |  | Latitude, WGS 84. Informational. |
| `lon` | float64 |  | degree | [-180, 180] |  | Longitude, WGS 84. Informational. |

Legacy spellings (accepted with a `deprecated_spelling` warning): `varcost_gbp_per_mwh` → `varcost_per_mwh`, `fom_gbp_per_mw` → `fom_per_mw_yr`, `startup_gbp` → `startup_cost`, `noload_gbp_per_h` → `noload_cost_per_h`.

### `hvdc`

*optional · one row per corridor · since 1.0*

Controllable (HVDC) transfer corridors between two buses. Flow is a decision, bounded by the installed capacity in each direction; it does not obey the DC power-flow law.

Primary key: (`id`).
Unique: (`name`).
Reference: `from_bus` → `buses.id`.
Reference: `to_bus` → `buses.id`.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `id` | int32 | yes |  | [1, ∞) |  | Corridor identifier, unique within the bundle. |
| `name` | string | yes |  |  |  | Stable corridor name, unique within the bundle. |
| `from_bus` | int32 | yes |  |  |  | Sending-end bus (`buses.id`); defines the positive direction. |
| `to_bus` | int32 | yes |  |  |  | Receiving-end bus (`buses.id`). |
| `existing_mw` | float64 |  | MW | [0, ∞) | `0.0` | Capacity in service at the start of the horizon. |
| `candidate_max_mw` | float64 |  | MW | [0, ∞) | `0.0` | Largest capacity the model may add. |
| `converter_efficiency` | float64 |  | fraction | (0, 1] | `0.985` | End-to-end converter efficiency applied to the transferred energy. |
| `capex_per_mw_yr` | float64 |  | currency/MW-yr | [0, ∞) | `0.0` | Annualised cost per MW of added capacity. |
| `lead_time_years` | int16 |  | year | [0, ∞) |  | Construction lead time of added capacity. |

Refused historical spellings (`ambiguous_legacy_cost`, an error): `capex_gbp_per_mw` — rename it to capex_per_mw_yr after checking the values are annualised (per MW per year); annualise an overnight cost first (SPEC.md §5.3).

### `interface_limits_monthly`

*optional · one row per interface-month · since 1.0*

Seasonal interface limits. A row REPLACES the interface's base limits in that calendar month (months are UTC calendar months of the period's start instant). Months without a row keep the base limits.

Primary key: (`interface_id`, `month`).
Reference: `interface_id` → `interfaces.interface_id`.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `interface_id` | int32 | yes |  |  |  | Interface (`interfaces.interface_id`). |
| `month` | int8 | yes |  | [1, 12] |  | Calendar month, 1 = January. |
| `max_fwd_mw` | float64 |  | MW | [0, ∞) |  | Forward limit in this month. Null = unbounded. |
| `max_rev_mw` | float64 |  | MW | [0, ∞) |  | Reverse limit in this month. Null = unbounded. |

### `interfaces`

*optional · one row per interface-line · since 1.0*

Transfer limits on groups of lines (paths, flowgates, cut-sets). One row per (interface, member line). The interface flow is Σ orientation × flow over its member lines, and is held within [−max_rev_mw, max_fwd_mw]. `name`, `max_fwd_mw`, `max_rev_mw` and `source` must be identical on every row of one interface.

Primary key: (`interface_id`, `line_id`).
Reference: `line_id` → `lines.id`.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `interface_id` | int32 | yes |  | [1, ∞) |  | Interface identifier. |
| `name` | string | yes |  |  |  | Interface name. |
| `line_id` | int32 | yes |  |  |  | Member line (`lines.id`). |
| `orientation` | int8 | yes |  | one of `1`, `-1` |  | +1 when the line's from→to direction is the interface's forward direction, −1 when it is the reverse. |
| `max_fwd_mw` | float64 |  | MW | [0, ∞) |  | Forward limit (a non-negative magnitude). Null = unbounded. |
| `max_rev_mw` | float64 |  | MW | [0, ∞) |  | Reverse limit (a non-negative magnitude). Null = unbounded. |
| `source` | string |  |  |  |  | Where the limit comes from (free text, e.g. a published rating). |

### `lines`

*optional · one row per line · since 1.0*

AC branches (lines and transformers) under the DC power-flow approximation. A positive flow runs from `from_bus` to `to_bus`. A row may be existing, a build candidate, or both (an existing line that can be reinforced by `delta_rating_mw`).

Primary key: (`id`).
Unique: (`name`).
Reference: `from_bus` → `buses.id`.
Reference: `to_bus` → `buses.id`.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `id` | int32 | yes |  | [1, ∞) |  | Line identifier, unique within the bundle. |
| `name` | string | yes |  |  |  | Stable line name, unique within the bundle. The key result tables use (`line` columns in results hold this value). |
| `from_bus` | int32 | yes |  |  |  | Sending-end bus (`buses.id`). Defines the positive flow direction. |
| `to_bus` | int32 | yes |  |  |  | Receiving-end bus (`buses.id`). Must differ from `from_bus`. |
| `susceptance_pu` | float64 | yes | p.u. (100 MVA base) | (0, ∞) |  | Series susceptance magnitude b = 1/x on a 100 MVA base. |
| `rating_mw` | float64 | yes | MW | (0, ∞) |  | Thermal rating (both directions). For a greenfield candidate, the rating once built. |
| `existing` | bool |  |  |  | `true` | True when the line is in service at the start of the horizon. |
| `candidate` | bool |  |  |  | `false` | True when the model may build (or reinforce) the line. |
| `delta_rating_mw` | float64 |  | MW | [0, ∞) | `0.0` | Capacity added by building the candidate on an existing line (a reinforcement). 0 on a greenfield candidate, whose built capacity is `rating_mw`. |
| `capex_per_yr` | float64 |  | currency/yr | [0, ∞) | `0.0` | Annualised cost of building the candidate (whole line, not per MW). |
| `loss_frac` | float64 |  | fraction | [0, 1) | `0.0` | Loss coefficient ℓ: the reference loss model is linear, loss(f) = ℓ·\|f\| MW; a quadratic model must give the same loss at rated flow, loss(f) = ℓ·f²/rating_mw (SPEC.md §8.3). 0 = lossless. |
| `contingency` | bool |  |  |  | `false` | True when the line's outage is a monitored N-1 contingency. |
| `length_km` | float64 |  | km | [0, ∞) |  | Route length. Informational. |
| `lead_time_years` | int16 |  | year | [0, ∞) |  | Construction lead time of the candidate: the earliest in-service year is the first investment year plus this. |
| `label` | string |  |  |  |  | Human-readable display name. Never used as a key. |

Legacy spellings (accepted with a `deprecated_spelling` warning): `capex_gbp` → `capex_per_yr`.

### `peak_demand`

*optional · one row per bus-year · since 1.0*

The peak demand a planning reserve margin is measured against, per bus and investment year. Absent = the maximum of the `demand` series.

Primary key: (`bus`, `year`).
Reference: `bus` → `buses.id`.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `bus` | int32 | yes |  |  |  | Bus (`buses.id`). |
| `year` | int16 | yes | year |  |  | Investment year. |
| `mw` | float64 | yes | MW | [0, ∞) |  | Peak demand. |

### `storage`

*optional · one row per unit · since 1.0*

The EXISTING storage fleet (batteries, pumped hydro, ...). Storage build options live in `candidates` (`kind = "storage"`).

Primary key: (`id`).
Unique: (`name`).
Reference: `bus` → `buses.id`.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `id` | int32 | yes |  | [1, ∞) |  | Storage identifier, unique within the table. |
| `name` | string | yes |  |  |  | Stable storage name, unique within the table. |
| `bus` | int32 | yes |  |  |  | Connection bus (`buses.id`). |
| `technology` | string |  |  |  | `battery` | Technology code (e.g. `battery`, `pumped_hydro`). |
| `existing_mw` | float64 | yes | MW | [0, ∞) |  | Installed power (charge and discharge) at the start of the horizon. |
| `duration_h` | float64 | yes | h | (0, ∞) |  | Energy-to-power ratio: usable energy capacity = existing_mw × duration_h. |
| `eta_charge` | float64 |  | fraction | (0, 1] | `0.95` | Charging efficiency (grid MWh in → stored MWh). |
| `eta_discharge` | float64 |  | fraction | (0, 1] | `0.95` | Discharging efficiency (stored MWh → grid MWh out). Round-trip efficiency = eta_charge × eta_discharge. |
| `self_discharge_frac_per_h` | float64 |  | fraction/h | [0, 1) | `0.0` | Fraction of the stored energy lost per hour. |
| `soc_init_frac` | float64 |  | fraction | [0, 1] | `0.5` | State of charge at the start of each block, as a fraction of energy capacity (cyclic blocks end where they start). |
| `dod_floor_frac` | float64 |  | fraction | [0, 1) | `0.0` | Lowest permitted state of charge, as a fraction of energy capacity. |
| `varcost_per_mwh` | float64 |  | currency/MWh |  | `0.0` | Cost per MWh discharged (cycling cost). |
| `firm_capacity_frac` | float64 |  | fraction | [0, 1] |  | Capacity credit toward the planning reserve margin. |

Legacy spellings (accepted with a `deprecated_spelling` warning): `varcost_gbp_per_mwh` → `varcost_per_mwh`.

### `weather_weights`

*optional · one row per year-weather · since 1.0*

Relative weight of each weather-year realisation within an investment year. Readers normalise the weights of one investment year to sum to 1. Absent table, or an absent (year, weather) pair = weight 1 (equal weighting).

Primary key: (`year`, `weather`).

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `year` | int16 | yes | year |  |  | Investment year. |
| `weather` | int16 | yes | year |  |  | Weather-year label. |
| `weight` | float64 | yes |  | [0, ∞) |  | Relative weight (non-negative; normalised per investment year). |

## Results (outputs)

### `manifest.toml`

*required · since 1.0*

Run identity and the declarations every result table depends on: money, time axis, inputs.

| key | type | req. | range / values | default | description |
|---|---|---|---|---|---|
| `format` | table | yes |  |  | Format identity. |
| `format.name` | string | yes | one of `ce-results` |  | Always `ce-results`. |
| `format.version` | string | yes |  |  | `MAJOR.MINOR` of this specification the run directory was written against. |
| `run` | table | yes |  |  | Run identity. |
| `run.name` | string | yes |  |  | Run name. |
| `run.created_utc` | datetime |  |  |  | Instant the run finished writing, UTC offset date-time. |
| `run.engine` | string |  |  |  | Name of the software that produced the results. |
| `run.engine_version` | string |  |  |  | Its version. |
| `run.mode` | string |  | one of `cem`, `pcm` |  | `cem`: capacity expansion (investment decided). `pcm`: production cost (capacity fixed). |
| `run.seed` | int |  |  |  | Random seed, when the run used one. |
| `money` | table | yes |  |  | Denomination of every monetary value in the run directory. |
| `money.currency` | string | yes |  |  | ISO 4217 code. Must equal the input bundle's. |
| `money.dollar_year` | int | yes | [1900, 2200] |  | Constant-money year. Must equal the input bundle's. |
| `time` | table | yes |  |  | Time axis of the result series. |
| `time.timezone` | string | yes | one of `UTC` |  | Always `UTC`. |
| `time.hour_convention` | string | yes | one of `hour_ending` |  | Always `hour_ending`. |
| `time.investment_years` | array[int] | yes |  |  | Investment years of the run, strictly increasing. |
| `time.weather_years` | array[int] |  |  |  | Weather-year labels of the run, in the order their ordinals count. |
| `time.weather_axis` | string |  | one of `label`, `ordinal` | `label` | How the `weather` column of result series is spelled: `label` = the weather year itself; `ordinal` = its 1-based position in `weather_years`. |
| `time.blocks` | array_of_tables |  |  |  | The block axis the series use (same keys as bundle.toml `[[time.blocks]]`). Recommended. |
| `time.blocks[].id` | int | yes | [1, ∞) |  | Block id. |
| `time.blocks[].nperiods` | int | yes | [1, ∞) |  | Number of periods. |
| `time.blocks[].dt_h` | float or array[float] | yes |  |  | Period length(s) in hours. |
| `time.blocks[].weight_h` | float | yes | (0, ∞) |  | Hours of the year the block stands for. |
| `time.blocks[].kind` | string | yes | one of `chronological`, `representative` |  | Block kind. |
| `time.blocks[].start` | datetime |  |  |  | Start of period 1 (chronological blocks). |
| `time.blocks[].name` | string |  |  |  | Display name. |
| `scenarios` | table |  |  |  | Scenario set of the run (absent = one scenario). |
| `scenarios.names` | array[string] | yes |  |  | Scenario names; `scenario` columns index this list from 1. |
| `scenarios.probability` | array[float] | yes |  |  | Scenario probabilities, summing to 1. |
| `inputs` | table |  |  |  | What the run read. |
| `inputs.bundle_name` | string |  |  |  | `[bundle] name` of the input bundle. |
| `inputs.bundle_format_version` | string |  |  |  | `[format] version` of the input bundle. |
| `inputs.bundle_digest` | string |  |  |  | A digest identifying the exact input bundle (e.g. `sha256:<hex>` over its files in name order). |

### `summary.toml`

*required · since 1.0*

Headline numbers of a run: cost, solve status, builds.

| key | type | req. | range / values | default | description |
|---|---|---|---|---|---|
| `cost` | table | yes |  |  | Objective value and its split (currency per the manifest). |
| `cost.total` | float | yes |  |  | Objective: present value, over the horizon, of annualised investment cost plus expected operating cost (discounted with the bundle's discount factors). total = investment_capex + operational_opex. |
| `cost.investment_capex` | float | yes |  |  | Investment part of `total`. |
| `cost.operational_opex` | float | yes |  |  | Operating part of `total` (including unserved-energy and any penalty terms). |
| `cost.total_ex_policy_penalty` | float |  |  |  | `total` without penalty terms for policy targets the system could not meet. |
| `solve` | table |  |  |  | How the solve ended. |
| `solve.lower_bound` | float |  |  |  | Best proven lower bound on the objective. |
| `solve.relative_gap` | float |  | [0, ∞) |  | (total − lower_bound) / \|total\| at stop. |
| `solve.iterations` | int |  | [0, ∞) |  | Outer iterations performed. |
| `solve.converged` | bool |  |  |  | True when the run stopped because it met its gap target. |
| `solve.stop_cause` | string |  | one of `gap`, `iteration_cap`, `wall_cap` |  | Why the run stopped: the gap target was met, or an iteration or wall-clock budget ran out. |
| `solve.wall_s` | float |  | [0, ∞) |  | Wall-clock seconds of the solve. |
| `solve.mip_master` | bool |  |  |  | True when the investment problem carried integer decisions (the gap then bounds the integrality gap). |
| `builds_mw_by_technology` | table |  |  |  | Cumulative new capacity in the final investment year, MW, keyed by technology code. |
| `builds_mw_by_technology.*` | float |  |  |  | MW built of that technology. |
| `energy` | table |  |  |  | Energy totals over the horizon (probability- and weight-scaled, undiscounted), MWh. |
| `energy.demand_mwh` | float |  | [0, ∞) |  | Demand served plus unserved. |
| `energy.unserved_mwh` | float |  | [0, ∞) |  | Unserved energy. |
| `energy.curtailed_mwh` | float |  | [0, ∞) |  | Curtailed availability-bounded energy. |
| `emissions` | table |  |  |  | Emission totals. |
| `emissions.co2_t` | float |  | [0, ∞) |  | Direct CO2 over the horizon (probability- and weight-scaled, undiscounted), tonnes. |

Legacy spellings: `cost.total_gbp` → `cost.total`, `cost.investment_capex_gbp` → `cost.investment_capex`, `cost.operational_opex_gbp` → `cost.operational_opex`, `cost.total_ex_policy_penalty_gbp` → `cost.total_ex_policy_penalty`, `solve.lower_bound_gbp` → `solve.lower_bound`.

### `qa_report.toml`

*optional · since 1.0*

Overall quality status of a run, derived from the `qa_checks` table (SPEC.md §9.6).

| key | type | req. | range / values | default | description |
|---|---|---|---|---|---|
| `qa` | table | yes |  |  | Status block. |
| `qa.status` | string | yes | one of `PASS`, `INCOMPLETE`, `WARN`, `FAIL` |  | FAIL if any check FAILs; else WARN if any WARNs; else INCOMPLETE if any required check is NA; else PASS. |
| `qa.n_pass` | int | yes | [0, ∞) |  | Checks with status PASS. |
| `qa.n_warn` | int | yes | [0, ∞) |  | Checks with status WARN. |
| `qa.n_fail` | int | yes | [0, ∞) |  | Checks with status FAIL. |
| `qa.n_na` | int | yes | [0, ∞) |  | Checks with status NA. |
| `qa.n_required_na` | int | yes | [0, ∞) |  | Required checks with status NA. |
| `qa.failing` | array[string] |  |  |  | `check:STATUS` (with `@year` when per-year) of every FAIL, WARN and required NA. |
| `qa.table` | string |  |  |  | File name of the checks table (e.g. `qa_checks.csv`). |
| `tolerances` | table |  |  |  | The tolerances the checks used, by name. |
| `tolerances.*` | float |  | [0, ∞) |  | A tolerance. |

### `results_manifest.toml`

*optional · since 1.0*

Inventory of the tables a run actually wrote, written last. It is the closed list a server may offer for download: a file not listed here is never served.

| key | type | req. | range / values | default | description |
|---|---|---|---|---|---|
| `schema_version` | int | yes | one of `1` |  | Index layout version; 1. |
| `run` | string | yes |  |  | Run name (equals manifest `[run] name`). |
| `formats_note` | string |  |  |  | Free text about the formats. |
| `toml` | array[string] |  |  |  | TOML documents present in the run directory. |
| `viz` | array[string] |  |  |  | Visual artefacts present (e.g. an HTML map). |
| `tables` | array_of_tables | yes |  |  | One entry per table written. |
| `tables[].name` | string | yes |  |  | Table name (file stem). |
| `tables[].formats` | array[string] | yes |  |  | Formats present on disk: any of `parquet`, `arrow`, `csv`. |
| `tables[].nrows` | int | yes | [0, ∞) |  | Data rows (header excluded). |
| `tables[].columns` | array[string] | yes |  |  | Column names, in file order. |
| `tables[].family` | string |  |  |  | Table family (e.g. `price`, `dispatch`, `investment`, `flow`, `audit`). |
| `tables[].grain` | string |  |  |  | One row per … (e.g. `bus-period`). |
| `tables[].description` | string |  |  |  | One-line description. |
| `tables[].measures` | array[string] |  |  |  | The value columns. |
| `tables[].provenance` | table |  |  |  | Free-form provenance of the table (string, number or boolean values). |

### `investment`

*required · one row per component-year · since 1.0*

Capacity the run decided, per component and investment year. Values are CUMULATIVE: the new capacity in service in that year (not the increment since the previous year). Retirements of existing capacity are rows with `component = "retirement"` and a NEGATIVE `built_mw` (cumulative capacity removed). Storage energy sized independently of power is a `storage_energy` row carrying `built_mwh`. A built hybrid candidate is one row per part under its `candidate_id` — `renewable` (renewable part), `storage` (storage power) and, only when energy is sized independently, `storage_energy` (SPEC.md §9.4). Components with nothing built in a year may be omitted.

Primary key: (`year`, `component`, `name`).

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `year` | int16 | yes | year |  |  | Investment year. |
| `component` | string | yes |  | one of `thermal`, `renewable`, `storage`, `storage_energy`, `hvdc`, `ac_line`, `retirement` |  | What was built (or retired). |
| `id` | int32 |  |  |  |  | The component's integer id in the input bundle (informational; may be null for built-in-run components). |
| `name` | string | yes |  |  |  | The component's name: a `candidates.candidate_id`, `generators.name` (retirements), `lines.name` or `hvdc.name`. |
| `technology` | string | yes |  |  |  | Technology code (`ac_line` / `hvdc` for network rows). |
| `bus` | string | yes |  |  |  | Bus name (`buses.name`); the sending-end bus for network rows. |
| `built_mw` | float64 |  | MW |  |  | Cumulative capacity built (≥ 0) or, on `retirement` rows, retired (≤ 0). Null on `storage_energy` rows. |
| `built_mwh` | float64 |  | MWh | [0, ∞) |  | Cumulative energy capacity built on `storage_energy` rows. Null elsewhere. |
| `bus_label` | string |  |  |  |  | Display label of `bus` (echoes `bus` when the bundle has none). |

Legacy spellings (accepted with a `deprecated_spelling` warning): `bus_name` → `bus_label`.

### `curtailment`

*optional · one row per site-period · since 1.0*

Available-but-unused output of availability-bounded resources. curtailed_mw = available_mw − dispatch_mw exactly, where available_mw = capacity in service × cf. A writer that does not produce this table leaves it out; a reader may derive it from `availability`, `investment` and `generation`.

Primary key: (`year`, `scenario`, `weather`, `block`, `period`, `site_name`) — key columns absent from a file are left out of the key.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `year` | int16 | yes | year |  |  | Investment year. |
| `scenario` | int16 | yes |  | [1, ∞) |  | Scenario index, 1-based. |
| `weather` | int16 | yes |  |  |  | Weather axis value (label or ordinal, per the manifest). |
| `block` | int16 | yes |  | [1, ∞) |  | Time block id. |
| `period` | int32 | yes |  | [1, ∞) |  | Period within the block, 1-based, hour-ending. |
| `site_name` | string | yes |  |  |  | A `generators.name` or `candidates.candidate_id`. |
| `technology` | string | yes |  |  |  | Technology code. |
| `bus` | string | yes |  |  |  | Bus name (`buses.name`). |
| `available_mw` | float64 | yes | MW | [0, ∞) |  | Capacity in service × cf. |
| `dispatch_mw` | float64 | yes | MW | [0, ∞) |  | Output actually dispatched. |
| `curtailed_mw` | float64 | yes | MW | [0, ∞) |  | available_mw − dispatch_mw. |

### `flows`

*optional · one row per line-period · since 1.0*

Per-period power flow on every AC line and controllable corridor. SIGNED: positive = from the line's `from_bus` to its `to_bus` as declared in the input bundle; negative = the reverse.

Primary key: (`year`, `scenario`, `weather`, `block`, `line`, `period`) — key columns absent from a file are left out of the key.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `year` | int16 | yes | year |  |  | Investment year. |
| `scenario` | int16 | yes |  | [1, ∞) |  | Scenario index, 1-based. |
| `weather` | int16 | yes |  |  |  | Weather axis value (label or ordinal, per the manifest). |
| `block` | int16 | yes |  | [1, ∞) |  | Time block id. |
| `line` | string | yes |  |  |  | Line or corridor name (`lines.name` / `hvdc.name`). |
| `period` | int32 | yes |  | [1, ∞) |  | Period within the block, 1-based, hour-ending. |
| `flow_mw` | float64 | yes | MW |  |  | Average flow over the period, signed from→to. |
| `line_label` | string |  |  |  |  | Display label of `line`. |

Legacy spellings (accepted with a `deprecated_spelling` warning): `line_name` → `line_label`.

### `generation`

*optional · one row per unit-period · since 1.0*

Per-period dispatch of every unit. Every MW value is NON-NEGATIVE; direction is carried by `source_type`: storage charging is a `storage_charge` row whose `dispatch_mw` is the power withdrawn. Net injection at a bus = Σ thermal + renewable + storage_discharge − storage_charge. Rows with zero dispatch may be omitted.

Primary key: (`year`, `scenario`, `weather`, `block`, `period`, `source_type`, `component_name`) — key columns absent from a file are left out of the key.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `year` | int16 | yes | year |  |  | Investment year. |
| `scenario` | int16 | yes |  | [1, ∞) |  | Scenario index, 1-based. |
| `weather` | int16 | yes |  |  |  | Weather axis value: the weather-year label, or its 1-based ordinal (manifest `[time] weather_axis`). |
| `block` | int16 | yes |  | [1, ∞) |  | Time block id. |
| `period` | int32 | yes |  | [1, ∞) |  | Period within the block, 1-based, hour-ending. |
| `source_type` | string | yes |  | one of `thermal`, `renewable`, `storage_discharge`, `storage_charge` |  | Kind of row; `storage_charge` rows are withdrawals. |
| `component_id` | int32 |  |  |  |  | The component's integer id in the input bundle (informational). |
| `component_name` | string | yes |  |  |  | Unit name: a `generators.name`, `storage.name` or `candidates.candidate_id`. |
| `technology` | string | yes |  |  |  | Technology code. |
| `bus` | string | yes |  |  |  | Bus name (`buses.name`). |
| `dispatch_mw` | float64 | yes | MW | [0, ∞) |  | Average power over the period (output, or withdrawal on storage_charge rows). |
| `floor_slack_mw` | float64 |  | MW | [0, ∞) |  | Shortfall below a must-run floor the unit was allowed to take (0 when none). |
| `varcost_per_mwh` | float64 |  | currency/MWh |  |  | The variable cost the run priced this row's energy at. Null/NaN on storage_charge rows. |
| `bus_label` | string |  |  |  |  | Display label of `bus`. |

Legacy spellings (accepted with a `deprecated_spelling` warning): `varcost_gbp_per_mwh` → `varcost_per_mwh`, `bus_name` → `bus_label`.

### `lmp`

*optional · one row per bus-period · since 1.0*

Locational marginal prices: the marginal cost of serving one more MWh of withdrawal at the bus in the period (the dual of the bus energy balance, in currency/MWh, undiscounted). The optional decomposition is an ATTRIBUTION whose three parts sum EXACTLY to the total: lmp_per_mwh = lmp_energy + lmp_congestion + lmp_loss. The energy part is the reference bus's price; the loss part is −energy × the bus's marginal loss factor; the congestion part is the remainder (so cost terms that the loss model does not attribute, such as controllable-corridor losses, land in congestion). Give all three parts or none.

Primary key: (`year`, `scenario`, `weather`, `block`, `bus`, `period`) — key columns absent from a file are left out of the key.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `year` | int16 | yes | year |  |  | Investment year. |
| `scenario` | int16 | yes |  | [1, ∞) |  | Scenario index, 1-based. |
| `weather` | int16 | yes |  |  |  | Weather axis value (label or ordinal, per the manifest). |
| `block` | int16 | yes |  | [1, ∞) |  | Time block id. |
| `bus` | string | yes |  |  |  | Bus name (`buses.name`). |
| `period` | int32 | yes |  | [1, ∞) |  | Period within the block, 1-based, hour-ending. |
| `lmp_per_mwh` | float64 | yes | currency/MWh |  |  | Total nodal price. |
| `lmp_energy` | float64 |  | currency/MWh |  |  | Energy component (the reference bus price). |
| `lmp_congestion` | float64 |  | currency/MWh |  |  | Congestion component (residual of the attribution). |
| `lmp_loss` | float64 |  | currency/MWh |  |  | Marginal-loss component. |
| `floor_slack_at_bus` | bool |  |  |  |  | True when a must-run unit at the bus was below its floor in the period (its price is then not a clean marginal cost). |
| `bus_label` | string |  |  |  |  | Display label of `bus`. |

Legacy spellings (accepted with a `deprecated_spelling` warning): `lmp_gbp_per_mwh` → `lmp_per_mwh`, `bus_name` → `bus_label`.

### `qa_checks`

*optional · one row per check · since 1.0*

One row per quality check the run performed on its own results (energy balance re-sum, flow limits, cost reconciliation, adequacy, ...). A check with nothing to examine is `NA` with `count = 0`, never `PASS`. The overall status in `qa_report.toml` is derived from these rows (SPEC.md §9.6).

Primary key: (`check`, `year`, `scope`).

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `check` | string | yes |  |  |  | Check name (snake_case). |
| `family` | string | yes |  |  |  | Check family (e.g. `balance`, `flows`, `cost`, `adequacy`). |
| `status` | string | yes |  | one of `PASS`, `WARN`, `FAIL`, `NA` |  | Outcome of the check. |
| `value` | float64 |  |  |  |  | The measured value (e.g. the worst residual). Null when not applicable. |
| `tolerance` | float64 |  |  | [0, ∞) |  | The tolerance the value was judged against. |
| `unit` | string |  |  |  | `` | Unit of `value` and `tolerance`. |
| `worst_where` | string |  |  |  | `` | Where the worst value occurred (free text, e.g. `bus=b2 block=1 period=18`). |
| `count` | int64 | yes |  | [0, ∞) |  | Number of items that failed or warned. |
| `population` | int64 | yes |  | [0, ∞) |  | Number of items examined (0 ⇒ the status must be NA). |
| `year` | int16 |  |  |  | `0` | Investment year the check applies to; 0 = the whole run. |
| `scope` | string |  |  |  | `` | Sub-scope of the check (free text; empty = whole run). |
| `source` | string |  |  |  | `` | What the check read (free text). |
| `note` | string |  |  |  | `` | Free-text explanation. |
| `required` | bool | yes |  |  |  | True when the run's configuration requires this check; a required NA makes the run INCOMPLETE. |

### `storage_state`

*optional · one row per storage-period · since 1.0*

Per-period state of charge of every storage unit, with its charge and discharge power (both non-negative). `soc_mwh` is the stored energy at the END of the period.

Primary key: (`year`, `scenario`, `weather`, `block`, `period`, `storage`) — key columns absent from a file are left out of the key.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `year` | int16 | yes | year |  |  | Investment year. |
| `scenario` | int16 | yes |  | [1, ∞) |  | Scenario index, 1-based. |
| `weather` | int16 | yes |  |  |  | Weather axis value (label or ordinal, per the manifest). |
| `block` | int16 | yes |  | [1, ∞) |  | Time block id. |
| `period` | int32 | yes |  | [1, ∞) |  | Period within the block, 1-based, hour-ending. |
| `storage` | string | yes |  |  |  | Storage name (`storage.name` or `candidates.candidate_id`). |
| `technology` | string | yes |  |  |  | Technology code. |
| `bus` | string | yes |  |  |  | Bus name (`buses.name`). |
| `soc_mwh` | float64 | yes | MWh | [0, ∞) |  | Stored energy at the end of the period. |
| `charge_mw` | float64 | yes | MW | [0, ∞) |  | Charging power (withdrawal). |
| `discharge_mw` | float64 | yes | MW | [0, ∞) |  | Discharging power (injection). |

### `transmission_losses`

*optional · one row per line-period · since 1.0*

Physical loss per lossy line and period. Lines without losses may be omitted.

Primary key: (`year`, `scenario`, `weather`, `block`, `line`, `period`) — key columns absent from a file are left out of the key.

| column | type | req. | unit | range / values | default | description |
|---|---|---|---|---|---|---|
| `year` | int16 | yes | year |  |  | Investment year. |
| `scenario` | int16 | yes |  | [1, ∞) |  | Scenario index, 1-based. |
| `weather` | int16 | yes |  |  |  | Weather axis value (label or ordinal, per the manifest). |
| `block` | int16 | yes |  | [1, ∞) |  | Time block id. |
| `line` | string | yes |  |  |  | Line name (`lines.name`). |
| `period` | int32 | yes |  | [1, ∞) |  | Period within the block, 1-based, hour-ending. |
| `loss_mw` | float64 | yes | MW | [0, ∞) |  | Average power lost on the line over the period. |

## Issue codes

| code | default severity | meaning |
|---|---|---|
| `missing_document` | error | A required TOML document is absent. |
| `toml_parse_error` | error | A TOML document does not parse. |
| `bad_format_name` | error | `[format] name` is not the expected format name. |
| `bad_version` | error | `[format] version` is not of the form MAJOR.MINOR. |
| `unsupported_major_version` | error | The format's major version is not one this validator implements. |
| `newer_minor_version` | error | The format's minor version is newer than this validator; a reader cannot know what the additions mean. |
| `unknown_key` | error | A TOML key or section the specification does not define (and not an `x_` extension). |
| `missing_key` | error | A required TOML key is absent. |
| `bad_currency` | error | The currency is not an ISO 4217 code of three upper-case letters. |
| `bad_axis` | error | The time or scenario axis declaration is inconsistent (order, ids, lengths). |
| `probabilities_not_normalised` | error | Scenario probabilities do not sum to 1. |
| `block_weights_not_full_year` | warning | Block weights do not add up to a full year (8760 or 8784 h). |
| `leap_day_mismatch` | error | A chronological full-year block disagrees with `[time] leap_day`. |
| `missing_table` | error | A required table is absent. |
| `unknown_table` | error | A table file whose name the specification does not define (and not `x_`). |
| `unknown_file` | error | A file that is neither a table, a document, a provenance sidecar nor documentation. |
| `ambiguous_table` | error | The same table is present in more than one file format. |
| `json_forbidden` | error | A JSON file: JSON is not a CE data format. |
| `unreadable_table` | error | A table file cannot be read. |
| `missing_column` | error | A required column is absent. |
| `unknown_column` | error | A column the table's schema does not define (and not `x_`). |
| `deprecated_spelling` | warning | A legacy column or key spelling, accepted and renamed to the canonical one. |
| `duplicate_spelling` | error | Both the legacy and the canonical spelling of one column or key are present. |
| `ambiguous_legacy_cost` | error | A historical cost column whose name does not say whether the cost is annualised or overnight; it must be renamed explicitly. |
| `bad_type` | error | A value of the wrong type (column family or TOML type). |
| `null_value` | error | A null (or NaN) in a column or key that does not allow one. |
| `bad_enum` | error | A value outside the column's or key's closed vocabulary. |
| `out_of_range` | error | A value outside the column's or key's permitted range. |
| `duplicate_key` | error | Two rows share a primary key or a unique column set. |
| `dangling_reference` | error | A reference to a row that does not exist in the referenced table. |
| `name_collision` | error | One name keys two different things (e.g. a generator and a candidate). |
| `missing_value` | error | A value that is required in this row's situation is absent. |
| `self_loop` | error | A branch whose two ends are the same bus. |
| `inert_line` | warning | A line that is neither existing nor a candidate. |
| `missing_capex` | error | A candidate with no capital cost in either form. |
| `overnight_cost_without_annualisation` | error | An overnight cost with no capital recovery factor inputs (row or `[money]` defaults). |
| `weather_without_year` | error | A series table carries `weather` without `year`. |
| `year_not_in_horizon` | error | A `year` value that is not a declared investment year. |
| `weather_not_declared` | error | A `weather` value that is not a declared weather year (or ordinal). |
| `axis_out_of_range` | error | A scenario, block or period beyond the declared axis. |
| `incomplete_series` | error | A series with missing cells (periods, blocks, scenarios or weather groups). |
| `missing_availability` | error | An availability-bounded resource with no availability series. |
| `inconsistent_interface` | error | Rows of one interface disagree on its name, limits or source. |
| `prov_unparsed_line` | warning | A `.prov` line that is neither `key=value`, a comment nor blank. |
| `prov_duplicate_key` | warning | A `.prov` key given more than once (the first wins). |
| `prov_orphan` | warning | A `.prov` sidecar whose described file does not exist. |
| `prov_bad_timestamp` | warning | A `.prov` `built_utc` value that is not an ISO 8601 UTC instant. |
| `prov_hash_mismatch` | error | A `.prov` `sha256` that does not match the described file. |
| `prov_rows_mismatch` | error | A `.prov` `rows` that does not match the described table. |
| `stale_derived_table` | error | A derived table records a different source generation than its source's `.prov`. |
| `currency_mismatch` | error | Results and input bundle declare different money. |
| `cost_identity_mismatch` | error | summary `[cost] total` differs from investment_capex + operational_opex. |
| `lmp_decomposition_mismatch` | error | lmp_per_mwh differs from lmp_energy + lmp_congestion + lmp_loss. |
| `partial_decomposition` | error | Some but not all of the three LMP components are present. |
| `curtailment_identity_mismatch` | error | curtailed_mw differs from available_mw − dispatch_mw. |
| `sign_convention` | error | A value with the wrong sign for its row kind (e.g. a positive retirement). |
| `stop_cause_inconsistent` | warning | `converged` and `stop_cause` disagree. |
| `index_mismatch` | error | results_manifest.toml disagrees with the files on disk. |
| `qa_status_inconsistent` | error | qa_report.toml's status or counts disagree with the qa_checks rows. |
| `qa_vacuous_pass` | error | A check reports PASS having examined nothing (population 0): it must be NA. |
| `flow_exceeds_rating` | warning | A flow above the line's rating plus any reinforcement. |
