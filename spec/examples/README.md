# Examples

Everything here is **synthetic**. The numbers are invented by `ce_spec.toy` (sinusoids and constants);
the results directory is NOT the output of any solve — it is written to be internally consistent so it
validates.

| Path | What it shows |
|---|---|
| `toy3/` | A complete three-bus bundle in CSV: every bundle table, weather years, two scenarios, two representative blocks, a candidate of every kind, an interface with seasonal limits, and `.prov` sidecars (including the vintage rule: `availability.csv.prov` records the candidate generation it was cut from). |
| `toy3_run/` | A matching results directory: `manifest.toml`, `summary.toml`, every result table, a QA report and the `results_manifest.toml` index. The hybrid candidate is built in 2035 and reported per part (SPEC.md §9.4). |
| `snippets/bundle_chronological_8760.toml` | A descriptor for one chronological hourly year on a leap year (8784 periods) with three weather years and an `x_` extension section. |
| `snippets/candidates.parquet.prov` | A provenance sidecar showing the grammar (comments, blanks around `=`, a quoted value). |
| `python_api.py` | Validating, reading tables in canonical form, inspecting Arrow schemas and provenance from Python. |

Validate them from the `spec/` directory:

```bash
python -m ce_spec validate-bundle examples/toy3
python -m ce_spec validate-results examples/toy3_run --bundle examples/toy3 --strict
python examples/python_api.py
```

(Use `PYTHONPATH=src` if the package is not installed.)

Regenerate them (any of `parquet`, `csv`, `arrow`):

```bash
python -m ce_spec make-toy examples --format csv
```

`examples/.gitattributes` marks the files binary so line endings never change: the `.prov` sidecars
record each file's SHA-256.
