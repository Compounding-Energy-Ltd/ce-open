# ce-studio — Scenario Studio API client

A typed Python client and command-line tool for the **CE Scenario Studio**
HTTP API. The Studio takes a *scenario delta* — a small bundle of changes
drawn against a served base region (new candidate sites, loads, lines, pinned
builds, removals) — solves it, and publishes a results diff with CSV tables
and a per-run quality report.

With this package you can:

* submit a delta bundle (bus names resolved, technology codes mapped, an
  idempotency key attached), withdraw it while queued, poll or wait for it;
* read the headline summary — with honest caveats — the result-table index,
  CSV tables and the QA report (with vacuous passes flagged);
* download a finished run as a directory the CE results reader (`results/`)
  opens: CSV tables plus TOML documents, no JSON;
* list the served regions and read a region's manifest and bus listing;
* turn every refusal into a code, a category and a suggested fix.

Standard-library Python only (3.11+). `pandas` and `pyarrow` are optional and
used only when you ask for a DataFrame or read or write Parquet.

## Install

```bash
pip install ./client                 # from a checkout of this repository
pip install './client[parquet]'      # + pyarrow, for Parquet tables and artefacts
pip install './client[dataframe]'    # + pandas, for read_table(as_dataframe=True)
```

## Access

The Studio is not yet open for self-serve sign-up. The service URL and an
access token come with a Studio account; there is no self-serve token
issuance yet. Submitting, listing your runs and reading results need the
token; the client also sends it, when configured, with the region requests
(index, manifest, bus listing), which it does not require.

| Variable | Meaning |
|---|---|
| `CE_STUDIO_BASE_URL` | Service root, e.g. `https://studio.example.com`. **Required** — the built-in default is a reserved `.invalid` placeholder that is refused. |
| `CE_STUDIO_API_KEY` | Your account's access token, sent as `Authorization: Bearer <token>`. An expired or revoked token is an `AuthError` (401/403). |
| `CE_STUDIO_TIMEOUT` | Per-request timeout in seconds (default 30). |

The token is never taken from the command line (so it never lands in shell
history), never shown by `repr`, and scrubbed from every error message.
Plain `http://` is refused for anything but a loopback host, because the
token travels in a header. Redirects are followed only on the same origin.

## Python

```python
from ce_studio import StudioClient, ScenarioFailed

client = StudioClient.from_env()

for scope in client.list_scopes():
    print(scope.scope_id, scope.label)

receipt = client.submit("examples/toy_delta")        # pre-checked locally first
try:
    final = client.wait(receipt.scenario_id, poll_s=10,
                        on_update=lambda s: print(s.headline))
except ScenarioFailed as err:
    for r in err.refusals:
        print(r.code, r.location, r.message, "->", r.fix)
    raise

summary = client.summary(final.scenario_id)
print(summary.objective, summary.currency)
for caveat in summary.caveats():          # read these before quoting a number
    print("caveat:", caveat)

qa = client.qa_report(final.scenario_id)
print(qa.status, qa.derived_status, qa.problems())

rows = client.read_table(final.scenario_id, "investment")
run_dir = client.download_run(final.scenario_id, "results/")   # CSV + TOML

from ce_results import Run                 # the results reader in results/
run = Run(run_dir)
print(run.builds(by=["technology"]))
```

`Summary.caveats()` flags, in plain words: a run that completed without
converging; a stop on an iteration or wall budget rather than the gap; an
objective that is more than 2 % unserved-energy cost; an *exploration*
result solved on representative days rather than the full year; and a QA
status other than PASS. `QAReport.problems()` flags a check that reports
PASS having examined nothing (`population == 0`), and an overall status the
per-check rows do not support (`QAReport.derived_status`).

## Command line

```bash
ce-studio check   DELTA_DIR                      # local pre-check, no network
ce-studio submit  DELTA_DIR [--wait] [--poll-s 10] [--max-wait-s 3600]
ce-studio status  SCENARIO_ID [--watch]
ce-studio list
ce-studio results SCENARIO_ID [--qa] [--out DIR] [--table NAME ...] [--decompress] [--run-dir DIR]
ce-studio withdraw SCENARIO_ID
ce-studio scopes  [SCOPE_ID] [--buses]
```

Global options: `--base-url`, `--timeout-s`, `--api-key-env VAR` (read the
token from another variable), and `--toml` for machine-readable output (TOML,
in keeping with the CE convention of not using JSON as a data format).

Exit codes: `0` success · `1` the scenario failed or was refused (locally or
by the service), or a wait timed out · `2` configuration or usage error ·
`3` other API error · `4` network error.

## The delta bundle

```
my_delta/
  delta.toml            # required
  add_candidates.csv    # optional tables — CSV or Parquet
  add_lines.csv
  add_loads.csv
  add_substations.csv
  force_builds.csv
  remove_assets.csv
```

```toml
[scenario]
name = "toy_flat_load_with_battery"
scope_id = "toy_region"          # a served region: `ce-studio scopes`

[scope]
buses = ["north", "south", "coast"]   # the buses this scenario may touch — required

[run]
rigor_tier = "interactive"       # fast exploration; "deliverable" = full year
```

**Tables.** Every column — its type, unit, meaning and the name the service
uses on the wire — is listed in `DELTA_TABLES.md` (generated from
`src/ce_studio/data/delta_tables.toml`, which ships with the package and is
what the client checks against). Columns use the CE data format 1.0 names
where one exists (`candidate_id`, `bus`, `capex_per_mw_yr` annualised,
`capex_overnight_per_mw` overnight, `fom_per_mw_yr`, `annualisation_rate`);
the client renames them for the service. The service's own spellings are
accepted too, except overnight cost columns whose names do not say so
(`capex_per_mw`, `capex_per_mwh`, ...), which are refused
(`ambiguous_cost_column`) so an overnight cost is never read as annualised.
Technology codes may be the CE format's (`solar_pv`, `wind_onshore`,
`battery`, `gas_cc`, ...) or the service's (`solar`, `wind_on`, `storage`,
`ccgt`, ...). Money is in the region's currency and dollar year
(`ce-studio scopes SCOPE_ID`). Capacity-factor profiles are not uploaded:
the service builds them for drawn wind and solar sites.

**Buses.** Name buses by their names in the region (`ce-studio scopes
SCOPE_ID --buses`, or `StudioClient.scope_buses()`); the client resolves them
to the region's integer ids when it submits. Integer ids (digits only) are
accepted too, but they are load-order keys that a rebuilt region may
reassign: stamp a bundle that uses them with the build they were read from,
`[scope] build = "..."` (shown by `ce-studio scopes SCOPE_ID`). The client
refuses to send ids stamped with another build (`stale_bus_ids`) and warns
when ids carry no stamp.

**Other sections.** `[run]` takes `rigor_tier`, `gap_tolerance` (a fraction
strictly between 0 and 1) and `notify`; `[scenario]` may name a `sweep_id`
and `variant_of`. The sections `[costs]`, `[[policies]]`, `[hosting]`,
`[reinforce]`, `[[bus_limits]]`, `[financing]` and `[screening]` are passed to
the service as written; it validates them and its refusals name what to fix.
Any other section is refused locally (`unknown_section`), because the service
would ignore it.

`DeltaBundle.check()` / `ce-studio check` repeat the cheap, certain refusals
locally — missing region or scope, bad rigor tier or gap tolerance,
operator-only settings, unknown files, sections and columns, ambiguous cost
columns, cells that do not fit their type — so those mistakes cost no round
trip. The service validates everything again and is the authority.

**On the wire.** A submit is ONE JSON object (the HTTP API's wire format;
nothing is written to disk as JSON): `delta.toml` sections become top-level
keys (`scope_id`, `name`, `scope.bus_set`, `rigor_tier`, ...), each table a
list of row objects under `tables`, plus a `submission_ref`. The service reads
at most 256 KiB of it; a larger body is refused locally
(`payload_too_large`). `tests/test_wire_contract.py` pins the request built
for the example bundle and the reply shape.

## Endpoints

| Method | Route (default) | Client call |
|---|---|---|
| POST | `/api/scenario/submit` | `submit()` |
| POST | `/api/scenario/withdraw/{sid}` | `withdraw()` |
| GET | `/api/scenario/queue` | `list_scenarios()`, `status()`, `wait()` |
| GET | `/api/scenario/results/{sid}/{file}` | `summary()`, `tables()`, `fetch()`, `download*()`, `read_table()`, `qa_report()`, `artifact_table()` |
| GET | `/data/scenario_studio/served.json` | `list_scopes()` |
| GET | `/data/scenario_studio/{scope_id}/manifest.json` | `scope_manifest()` |
| GET | `/data/scenario_studio/{scope_id}/buses.geojson` | `scope_buses()`, `bus_resolver()`, bus names in `submit()` |

Every route is a template in `Routes`, so a deployment that mounts the API
elsewhere needs a config change, not a code change. The JSON documents these
routes serve (`summary.json`, `tables.json`, the region index, manifest and
bus listing) are the API's wire format: the client parses them into typed
objects and never writes them to disk — `download()` refuses `*.json` names.

## Downloading a run for the results reader

`StudioClient.download_run(sid, dest)` (CLI: `results SID --run-dir DIR`)
writes a directory that `ce_results.Run` opens:

| From the Studio (HTTP API) | Written | Read by `ce_results` as |
|---|---|---|
| every table offered in `tables.json` (CSV, gzip on the wire) | `<table>.csv` (decompressed) | `run.table()`, `run.builds()`, `run.lmp()`, ... |
| `technology` values in those tables (`solar`, `bess`, `ccgt`, ...) | CE format codes (`solar_pv`, `battery`, `gas_cc`, ...) | `technology` |
| `summary.json` `objective` | `summary.toml [cost] total` | `run.stamps.objective` |
| `gap`, `converged`, `stop_cause`, `wall_clock.solve_s` | `[solve] relative_gap`, `converged`, `stop_cause`, `wall_s` | `run.stamps.*` |
| the client's caveats, `representative_blocks` | `[x_studio]` (an extension) | `run.summary["x_studio"]` |
| `currency` | `manifest.toml [money] currency` | `run.provenance.currency` |
| `qa_status`, `qa_failing` | `qa_report.toml [qa]` | `run.qa` |

The tables keep the service's published layout (integer `bus` with a
`bus_name` label, `lmp_gbp_per_mwh`, ...), which `ce_results` reads the way
it reads runs of earlier CE releases (`bus_name` becomes `bus_label`,
`lmp_gbp_per_mwh` becomes `lmp_per_mwh`). `manifest.toml` therefore carries
no `[format]` table: the directory is readable, but it is not a CE data
format 1.0 results directory and the format validator does not treat it as
one. The money year is the region's (`ce-studio scopes SCOPE_ID`).

## Errors and refusals

All errors derive from `StudioError`. HTTP failures map by status:

| Status | Exception |
|---|---|
| 400 | `BadRequestError` |
| 401 / 403 | `AuthError` |
| 404 | `NotFoundError` |
| 409 | `ConflictError` (e.g. withdrawing a scenario the service already started) |
| 413 | `PayloadTooLargeError` |
| 422 | `ValidationError` — `.refusals` holds the row-level refusals (or the reply's single `code`) |
| 429 | `RateLimitedError` — `.retry_after` in seconds |
| 5xx | `ServerError` |

Others: `ConfigError`, `LocalValidationError` (pre-check), `TransportError`
(network, after retries), `ScenarioNotFound`, `ScenarioFailed` (a waited-on
run failed — `.refusals`), `WaitTimeout`.

A failed run's `detail` comes back as one compact line; `parse_detail()`
recovers each refusal's table, row, column, id, code and message, plus the
count the service summarised as `(+N more)`. Each code is annotated from a
catalog (`src/ce_studio/data/refusal_codes.toml`) with a category, a summary
and the usual fix. Codes not in the catalog fall back to a prefix family
(`bad_*`, `warn_*`, `policy_*`, `regulation_*`, `bus_limit*`) or to
`unknown` — they are always surfaced verbatim, never dropped.

## Retries

Capped exponential backoff with jitter (`RetryPolicy`: 5 attempts,
0.5 s doubling to 30 s). GETs retry on 429/500/502/503/504 and on network
failures. **Submits** carry a submission reference (an idempotency key —
generated, or yours via `submit(..., submission_ref=...)`): the service
answers a repeat with the run it already queued, so a submit is retried like
a GET, after a timeout or a 502/504 too, and is never queued twice
(`SubmitReceipt.duplicate` says when a repeat was recognised). **Withdraws**
retry only when the request certainly was not processed — 429, 503,
connection refused, name resolution. `Retry-After` is honoured; one longer
than `max_retry_after_s` (120 s) is raised as `RateLimitedError` instead of
waited out.

## Testing against the mock

`ce_studio.testing.MockStudio` is an in-process server with the same routes
and wire shapes — it reads a submit exactly as the service does (one JSON
object, at most 256 KiB; anything else reads as empty and is refused
`scope_id required`) — a scripted lifecycle (one step per queue poll),
synthetic toy results in the service's published layout, and fault injection
(`after=True` processes the request and then loses the reply):

```python
from ce_studio import ClientConfig, StudioClient
from ce_studio.testing import MockStudio

with MockStudio(api_key="test-key") as mock:
    mock.inject("GET", "/api/scenario/queue", 503, times=2)    # an outage
    mock.inject("POST", "/api/scenario/submit", 504, after=True)  # queued, reply lost
    client = StudioClient(ClientConfig(base_url=mock.url, api_key="test-key"))
    ...
```

Scenario names starting `fail`, `qa-block` or `slow` script a refused run,
a QA-blocked run and an unconverged run. `examples/mock_studio.py` serves the
mock on a fixed port for trying the CLI by hand; `examples/submit_and_wait.py`
runs the whole flow (against the mock when `CE_STUDIO_BASE_URL` is unset).

```bash
cd client
python -m pytest            # pythonpath is set in pyproject.toml
```

## Limitations and lossy mappings

* **Results route.** Results are read from `/api/scenario/results/{sid}/{file}`.
  A deployment that serves published results under another path sets
  `Routes(results=...)`.
* **Bus names need the region's bus listing.** Resolving names costs one
  extra request per submit; a bundle that uses only integer ids (stamped with
  `[scope] build`) skips it.
* **Submit reply.** The scenario id is read from `scenario_id`, `id` or `sid`
  (first present); a reply without one is an error.
* **Status is read from the queue listing.** There is no per-scenario status
  route, so `status()` scans the listing (one request per poll). A row that
  has aged out of the listing raises `ScenarioNotFound`, even though its
  published results may still be downloadable with `summary()` / `tables()`.
* **Region index shape.** The route of the served-region index is a default,
  not a contract guarantee, and its shape is parsed leniently (a list of ids;
  objects with `scope_id`/`id`/`region_id` and `label`/`name`/`title`; or a
  mapping wrapped under `scopes`/`regions`/`served`).
* **Region creation requests** (drawing a new region) are not submitted by
  this client; such rows still appear in `list_scenarios()` with
  `kind = "region"`.
* **Detail parsing is best effort.** The service clips a failure's `detail` at
  about 2 KB and 20 rows; clipped rows are reported only as a count
  (`ParsedDetail.omitted`). A message that itself contains text shaped like
  `; table[n] column code:` would be split there.
* **TOML output** (`--toml`) leaves out `None` values (TOML has no null) and
  null positions inside arrays.
* **JSON** is used only because the HTTP API speaks it on the wire (the
  submit body, `summary.json`, `tables.json`, the region documents). The
  client never writes JSON files: `download()` refuses `*.json`, tables
  download as CSV (or `.csv.gz`), artefacts as Parquet, `download_run()`
  writes TOML, and CLI output is text or TOML.
* **Local pre-check is a small subset** of the service's validation; it
  catches certain mistakes early but a clean pre-check does not mean the
  service will accept the bundle.
* **Capability flags** (feature negotiation inside the service) are not
  surfaced; they do not affect what a client can send.

## Licence

Apache License 2.0 — see `LICENSE` and `NOTICE`.
Copyright 2026 Compounding Energy Ltd.
