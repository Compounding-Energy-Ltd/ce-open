# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Using ce_spec from Python: validate, read a table in its canonical form, inspect a schema.

Run from the spec/ directory:

    python examples/python_api.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))   # not needed once the package is installed

from ce_spec import load_bundle, read_prov, table_schema, validate_bundle, validate_results  # noqa: E402
from ce_spec.issues import Collector  # noqa: E402


def main() -> int:
    bundle, run = HERE / "toy3", HERE / "toy3_run"

    # 1. validate both directories; a report lists named issues
    b = validate_bundle(bundle)
    r = validate_results(run, bundle=bundle, strict=True)
    print(b.summary())
    print(r.summary())

    # 2. read every table in canonical form (legacy spellings renamed, types cast, defaults filled)
    lb = load_bundle(bundle, Collector())
    demand = lb.tables["demand"]
    print(f"demand: {demand.num_rows} rows, columns {demand.column_names}")
    print(f"investment years {lb.axis.years}, weather years {lb.axis.weather_years}, blocks {lb.axis.blocks}")

    # 3. the canonical Arrow schema of a table, with units in the field metadata
    sch = table_schema("bundle", "candidates").arrow_schema(["candidate_id", "max_mw", "capex_per_mw_yr"])
    for f in sch:
        print(f"  {f.name}: {f.type} [{f.metadata[b'unit'].decode() or '-'}]")

    # 4. provenance
    prov = read_prov(bundle / "candidates.csv.prov")
    print(f"candidates generation {prov.get('generation')}, sha256 {prov.get('sha256')[:12]}…")
    return 0 if (b.ok and r.ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
