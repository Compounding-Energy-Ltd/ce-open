# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Import the synthetic sample GenX case in ``examples/sample_case`` and show the result.

    python examples/import_sample.py [OUT_DIR]

With no OUT_DIR the bundle is written to a temporary folder. Run from the
``converters/genx`` folder after ``pip install -e .`` (or with ``src`` on
``PYTHONPATH``).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from ce_genx import check_bundle, import_case

HERE = Path(__file__).resolve().parent


def main(out_dir: str | None = None) -> Path:
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="ce_genx_")) / "sample_bundle"
    bundle = import_case(HERE / "sample_case", out, investment_year=2030, currency="USD", overwrite=True)

    print(f"bundle written to {out}")
    for name, df in bundle.tables.items():
        print(f"  {name:<13} {len(df):>4} rows")

    gens = bundle.tables["generators"][["name", "kind", "technology", "existing_mw", "varcost_per_mwh"]]
    print("\nexisting generators:")
    print(gens.to_string(index=False))

    cands = bundle.tables["candidates"][["candidate_id", "kind", "max_mw", "capex_per_mw_yr"]]
    print("\nbuild candidates:")
    print(cands.to_string(index=False))

    print("\nmapping notes:")
    print(bundle.report.format())

    problems = check_bundle(out)
    print("\nself-check:", "clean" if not problems else problems)
    return out


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
