# Scenario delta tables

Generated from `src/ce_studio/data/delta_tables.toml` by `python -c "from ce_studio.bundle import render_delta_reference as r; print(r(), end='')"`.
Do not edit by hand.

Column names follow the CE data format 1.0 where it has one; the client sends each column under its *wire* name. A column's wire name is also accepted in a file, except the refused spellings listed per table. `bus` cells are bus names of the region (resolved by the client) or integer ids valid for the region build named in `[scope] build`. Money is in the region's currency and dollar year.

## `add_candidates`

Build options drawn on the region. The solve chooses how much of max_mw to build (0 is always allowed).

| column | wire name | type | required | unit | description |
|---|---|---|---|---|---|
| `candidate_id` | `site_id` | string | yes |  | Identifier, unique within the table. |
| `name` |  | string |  |  | Display name (defaults to candidate_id). |
| `technology` |  | string | yes |  | Technology code: one of the codes under [technology.to_wire] (CE format 1.0 codes) or the service's own codes. |
| `bus` | `bus_id` | bus | yes |  | Connection bus: a bus name of the region, or its integer id. |
| `max_mw` |  | float | yes | MW | Largest buildable capacity (0 < max_mw <= 100000). For a hybrid, the point-of-interconnection limit. |
| `lat` |  | float |  | degree | Latitude of the site, WGS 84. |
| `lon` |  | float |  | degree | Longitude of the site, WGS 84. |
| `in_service_year` |  | int |  | year | First year the build may be in service. |
| `duration_h` |  | float |  | h | Storage duration (energy = power x duration_h) for storage and hybrid rows. |
| `solar_mw` |  | float |  | MW | Hybrid only: the largest solar part. |
| `storage_mw` |  | float |  | MW | Hybrid only: the largest storage part (power). |
| `storage_mwh` |  | float |  | MWh | Storage energy sized independently of power (instead of duration_h). |
| `hybrid_grid_charging` |  | bool |  |  | Hybrid only: whether the storage part may charge from the grid. |
| `nameplate_mw` |  | float |  | MW | Solar DC nameplate, when it differs from the AC rating. |
| `dcac_ratio` |  | float |  |  | Solar DC/AC ratio (> 0). |
| `curtailment_liquidation` |  | bool |  |  | false = must-take output (generation equals availability); absent or true = curtailable. |
| `sizing_mode` |  | string |  |  | optimize (default: the solve sizes the build) or exact (the size is pinned with a force_builds row). |
| `tie_line_id` |  | string |  |  | The add_lines row (a generator tie) this site connects through. |
| `interconnection_cost_per_kw` |  | float |  | currency/kW | Overnight network-connection cost per kW built. |
| `capex_overnight_per_mw` | `capex_per_mw` | float |  | currency/MW | Overnight capital cost per MW (one-off); annualised by the service. |
| `capex_per_mw_yr` | `capex_per_mw_annualised` | float |  | currency/MW-yr | Annualised capital cost per MW (used as given; wins over the overnight form). |
| `capex_overnight_per_mwh` | `capex_per_mwh` | float |  | currency/MWh | Overnight capital cost per MWh of independently sized storage energy. |
| `capex_per_mwh_yr` | `capex_per_mwh_annualised` | float |  | currency/MWh-yr | Annualised capital cost per MWh of independently sized storage energy. |
| `fom_per_mw_yr` | `fom_per_mw` | float |  | currency/MW-yr | Fixed operation and maintenance cost per MW per year. |
| `solar_capex_overnight_per_mw` | `solar_capex_per_mw` | float |  | currency/MW | Hybrid: overnight capital cost of the solar part per MW. |
| `storage_capex_overnight_per_mw` | `storage_capex_per_mw` | float |  | currency/MW | Hybrid or storage: overnight capital cost of the storage power per MW. |
| `storage_capex_overnight_per_mwh` | `storage_capex_per_mwh` | float |  | currency/MWh | Hybrid or storage: overnight capital cost of the storage energy per MWh. |
| `solar_capex_per_mw_yr` | `solar_capex_per_mw_annualised` | float |  | currency/MW-yr | Hybrid: annualised capital cost of the solar part per MW. |
| `storage_capex_per_mw_yr` | `storage_capex_per_mw_annualised` | float |  | currency/MW-yr | Hybrid or storage: annualised capital cost of the storage power per MW. |
| `storage_capex_per_mwh_yr` | `storage_capex_per_mwh_annualised` | float |  | currency/MWh-yr | Hybrid or storage: annualised capital cost of the storage energy per MWh. |
| `solar_fom_per_mw_yr` | `solar_fom_per_mw` | float |  | currency/MW-yr | Hybrid: fixed O&M of the solar part per MW per year. |
| `storage_fom_per_mw_yr` | `storage_fom_per_mw` | float |  | currency/MW-yr | Hybrid or storage: fixed O&M of the storage part per MW per year. |
| `annualisation_rate` | `discount_rate` | float |  | fraction | Rate used to annualise this row's overnight costs (with lifetime_years). |
| `lifetime_years` |  | float |  | year | Economic life used to annualise this row's overnight costs. |
| `dollar_year` |  | int |  | year | Money year of this row's costs; must match the region's (a mismatch is refused). |
| `provenance` |  | string |  |  | Free text: where the row's numbers come from (carried, not used by the solve). |

Refused spellings (`ambiguous_cost_column`): `capex_per_mw` — write capex_overnight_per_mw (overnight) or capex_per_mw_yr (annualised); `capex_per_mwh` — write capex_overnight_per_mwh (overnight) or capex_per_mwh_yr (annualised); `solar_capex_per_mw` — write solar_capex_overnight_per_mw (overnight) or solar_capex_per_mw_yr (annualised); `storage_capex_per_mw` — write storage_capex_overnight_per_mw (overnight) or storage_capex_per_mw_yr (annualised); `storage_capex_per_mwh` — write storage_capex_overnight_per_mwh (overnight) or storage_capex_per_mwh_yr (annualised).

## `add_loads`

New demand drawn on the region.

| column | wire name | type | required | unit | description |
|---|---|---|---|---|---|
| `load_id` |  | string | yes |  | Identifier, unique within the table. |
| `name` |  | string |  |  | Display name. |
| `bus` | `bus_id` | bus | yes |  | Connection bus: a bus name of the region, or its integer id. |
| `lat` |  | float |  | degree | Latitude, WGS 84. |
| `lon` |  | float |  | degree | Longitude, WGS 84. |
| `mw` |  | float | yes | MW | Nameplate demand; the hourly shape comes from the archetype. |
| `archetype` |  | string | yes |  | Hourly load shape: one of the region's archetypes (`ce-studio scopes SCOPE_ID`). |
| `start_year` |  | int |  | year | First year the load is present. |
| `flex_frac` | `flex_fraction` | float |  | fraction | Share of the load that may be curtailed at flex_cost_per_mwh (0 to 1). |
| `flex_cost_per_mwh` |  | float |  | currency/MWh | Price at which the flexible share is curtailed. |
| `tie_line_id` |  | string |  |  | The add_lines row this load connects through. |

## `add_lines`

New transmission paths (and generator or load ties) drawn on the region.

| column | wire name | type | required | unit | description |
|---|---|---|---|---|---|
| `line_id` |  | string | yes |  | Identifier, unique within the table. |
| `name` |  | string |  |  | Display name. |
| `from_bus` |  | bus |  |  | Sending-end bus (name or integer id); positive flow runs from_bus -> to_bus. |
| `to_bus` |  | bus |  |  | Receiving-end bus (name or integer id). |
| `from_site_id` |  | string |  |  | For a tie: the candidate or substation at the sending end, instead of from_bus. |
| `to_site_id` |  | string |  |  | For a tie: the candidate or substation at the receiving end, instead of to_bus. |
| `rating_mw` | `mw` | float | yes | MW | Thermal rating of the new path. |
| `length_km` |  | float |  | km | Route length. |
| `voltage_kv` |  | float |  | kV | Nominal voltage. |
| `susceptance` |  | float |  |  | Series susceptance of the new path (absent = derived by the service from voltage and length). |
| `cost_per_mw_km_yr` | `cost_per_mw_km` | float |  | currency/MW-km-yr | Annualised cost per MW per km (the service multiplies by rating_mw and length_km). |
| `capex_per_yr` | `capex` | float |  | currency/yr | Annualised cost of the whole path (instead of cost_per_mw_km_yr). |
| `cost_overnight_per_mw_km` | `cost_per_mw_km_overnight` | float |  | currency/MW-km | The overnight cost the annualised one came from (carried for the record). |
| `annualisation_rate` | `discount_rate` | float |  | fraction | Rate the annualised cost was computed with (carried for the record). |
| `lifetime_years` |  | float |  | year | Life the annualised cost was computed with (carried for the record). |
| `in_service_year` |  | int |  | year | First year the path may be in service. |

Refused spellings (`ambiguous_cost_column`): `cost_per_mw_km` — write cost_per_mw_km_yr (annualised) — the explicit name; `capex` — write capex_per_yr (annualised) — the explicit name.

## `add_substations`

New substations that drawn paths end on.

| column | wire name | type | required | unit | description |
|---|---|---|---|---|---|
| `substation_id` |  | string | yes |  | Identifier, unique within the table (referenced by add_lines from_site_id / to_site_id). |
| `name` |  | string |  |  | Display name. |
| `lat` |  | float |  | degree | Latitude, WGS 84. |
| `lon` |  | float |  | degree | Longitude, WGS 84. |
| `voltage_kv` |  | float |  | kV | Nominal voltage. |
| `bus` | `bus_id` | bus |  |  | An existing bus the substation sits on (name or integer id); absent = a new bus. |

## `force_builds`

Pins: assets that must be built at an exact size.

| column | wire name | type | required | unit | description |
|---|---|---|---|---|---|
| `asset_id` |  | string | yes |  | The candidate_id to pin (for a hybrid's storage part: `<candidate_id>_sto`). |
| `mw` |  | float | yes | MW | Exact capacity to build. |
| `mwh` |  | float |  | MWh | Exact storage energy (independently sized storage). |
| `in_service_year` |  | int | yes | year | Year the pinned build is in service. |

## `remove_assets`

Retirements and exclusions of existing assets.

| column | wire name | type | required | unit | description |
|---|---|---|---|---|---|
| `asset_kind` |  | string | yes |  | What is removed (the service's vocabulary, e.g. a fleet class or a line). |
| `asset_id` |  | string | yes |  | The asset (for a fleet aggregate: its technology class). |
| `bus` | `bus_id` | bus |  |  | The bus of a fleet aggregate (name or integer id). |
| `retire_year` |  | int |  | year | Last year in service; absent = removed from the start. |
| `name` |  | string |  |  | Display name. |
| `provenance` |  | string |  |  | Free text: why the asset is removed. |

## Technology codes

In `add_candidates.technology`, a CE format code is sent as the service's code; the service's own codes are accepted as given.

| CE format code | service code |
|---|---|
| `solar_pv` | `solar` |
| `wind_onshore` | `wind_on` |
| `wind_offshore` | `wind_off` |
| `solar_pv_battery` | `solar_storage_hybrid` |
| `battery` | `storage` |
| `gas_cc` | `ccgt` |
| `gas_ct` | `ocgt` |
| `nuclear` | `nuclear` |
| `biomass` | `biomass` |
| `geothermal` | `geothermal` |
| `coal` | `coal` |
| `oil` | `oil` |

In downloaded result tables (`StudioClient.download_run`), the service's codes are mapped back:

| service code | CE format code |
|---|---|
| `solar` | `solar_pv` |
| `wind_on` | `wind_onshore` |
| `wind_off` | `wind_offshore` |
| `solar_storage_hybrid` | `solar_pv_battery` |
| `storage` | `battery` |
| `bess` | `battery` |
| `ccgt` | `gas_cc` |
| `ocgt` | `gas_ct` |
| `nuclear_lwr` | `nuclear` |
