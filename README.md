# CE Open

Open-edge tooling for Compounding Energy (CE) capacity-expansion and
production-cost studies, released under the Apache License 2.0.

CE Open covers the *edges* of the CE workflow — the data formats that go in
and come out, converters from other open modelling frameworks, and clients
for reading results. The optimisation engine itself is not part of this
repository.

## Components

| Path | What it is |
|------|------------|
| `spec/` | The CE data format 1.0 for bundles and results (Parquet / Arrow / CSV / TOML), schemas and validator. |
| `converters/pypsa/` | PyPSA <-> CE bundle converters. |
| `converters/genx/` | GenX -> CE bundle importer. |
| `results/` | Reader for CE results directories. |
| `client/` | Scenario Studio API client and CLI. |
| `reference/papers/` | Reference code accompanying CE white papers. |

Each component has its own README, tests and examples. Every row above is
populated; components are added to the table only once they exist. An agent
(MCP) interface is planned and is not part of this repository yet.

## Data-format conventions

* Tabular data is Parquet (preferred) or CSV; metadata is TOML.
* JSON is not used as a CE data format, and no tool here writes a JSON file.
  It appears only on the wire: where a third-party API requires it, and as
  the request and reply encoding of the Scenario Studio HTTP API, which the
  client parses in memory (`spec/SPEC.md` section 2.4).
* All sample data in this repository is synthetic.

## Tests

Each component runs its own suite from its directory (`python -m pytest`);
repository-wide checks (licence headers, licence files, cross-component
drift) run from the root. `.github/workflows/ci.yml` runs both on the newest
supported Python and again at the minimum dependency versions the components
declare, with `CE_OPEN_STRICT=1` so a cross-component test that cannot find
its sibling fails instead of skipping.

## Licence

Apache License 2.0 — see `LICENSE` and `NOTICE`.
Copyright 2026 Compounding Energy Ltd.
