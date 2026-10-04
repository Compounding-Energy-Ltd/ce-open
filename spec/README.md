# ce-spec — the CE data format

The open specification of the data a CE capacity-expansion or production-cost run reads (a **bundle**)
and writes (a **results directory**), with machine-readable schemas and a reference validator.

| | |
|---|---|
| `SPEC.md` | The normative specification 1.0: files and encodings, versioning (additive-only, refusal on unknown), time axis (UTC, hour-ending, blocks, weather years), money (currency + dollar year, annualised vs overnight), units and sign conventions, the `.prov` provenance grammar, bundle and results semantics, issue codes, and how existing layouts map onto it. |
| `TABLES.md` | The column and key reference — generated from `schemas/`. |
| `schemas/` | One TOML file per table (`*.schema.toml`: columns, Arrow types, units, ranges, keys, references, legacy and refused spellings) and per TOML document (`*.doc.toml`). |
| `src/ce_spec/` | Python package: schema loader, Arrow schemas, `.prov` parser, bundle and results validators, synthetic fixtures, CLI. |
| `examples/` | A synthetic three-bus bundle and matching results directory (CSV), descriptor and provenance snippets, a Python API example. |
| `tests/` | pytest suite on synthetic fixtures. |

Tables are Parquet (canonical), Arrow IPC or CSV; descriptors are TOML. JSON is not a CE data format.

## Install

```bash
pip install ./spec            # from the repository root; needs Python ≥ 3.11 and pyarrow
pip install './spec[test]'    # with pytest
```

or use it from a checkout with `PYTHONPATH=spec/src`.

## Validate

```bash
python -m ce_spec validate-bundle  path/to/bundle
python -m ce_spec validate-results path/to/run --bundle path/to/bundle            # lenient (default)
python -m ce_spec validate-results path/to/run --bundle path/to/bundle --strict   # refuse unknowns
```

Exit status 0 = valid (warnings allowed), 1 = at least one error. Every issue carries a stable code:

```text
bundle examples/toy3: INVALID (1 error(s), 0 warning(s))
  ERROR dangling_reference [lines.to_bus] rows 1 (1 total): 1 row(s) reference buses.id values that do not exist: 99
```

From Python:

```python
from ce_spec import validate_bundle, load_bundle, table_schema
from ce_spec.issues import Collector

report = validate_bundle("path/to/bundle")
report.raise_if_errors()                 # or inspect report.errors / report.warnings / report.codes()

lb = load_bundle("path/to/bundle", Collector())
demand = lb.tables["demand"]             # pyarrow.Table, canonical names and types, defaults filled
schema = table_schema("bundle", "lines").arrow_schema()   # canonical Arrow schema with units in metadata
```

## Other commands

```bash
python -m ce_spec arrow-schema bundle candidates     # print a canonical Arrow schema
python -m ce_spec render-docs > TABLES.md            # regenerate the column reference
python -m ce_spec make-toy /tmp/toy --format parquet # write the synthetic fixtures
```

## Test

```bash
cd spec && python -m pytest
```

## What the validator checks

Structure (required documents and tables, unknown or ambiguous files, JSON), types and nulls, closed
vocabularies, ranges, primary and unique keys, references between tables, the declared axis (scenarios,
blocks, periods, investment and weather years), series completeness (no silent zero cells), availability
coverage, capital-cost forms, interface consistency, provenance sidecars (grammar, hash, row count,
vintage), and in results the declared identities (cost split, LMP decomposition, curtailment), sign
conventions, the QA status derivation and the table index. It checks structure and declared identities,
not physics.

## Limitations

See SPEC.md section 11: the tables 1.0 does not cover (carried as `x_` extensions), and the existing
layouts whose mapping onto 1.0 reshapes or loses information.

## Licence

Apache License 2.0 — see `LICENSE` and `NOTICE`. Copyright 2026 Compounding Energy Ltd.
