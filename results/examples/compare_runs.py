# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Compare two run directories: cost, build, price and generation deltas.

Usage::

    python examples/compare_runs.py [RUN_A RUN_B]

Without arguments two synthetic variants are generated and compared. The
comparison needs only the run directories (no input bundle).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd

from ce_results import Run, compare_runs
from ce_results.synthetic import write_synthetic_run


def main(a: str | None = None, b: str | None = None):
    if a is None or b is None:
        tmp = Path(tempfile.mkdtemp())
        a = write_synthetic_run(tmp / "base", variant="base")[0]
        b = write_synthetic_run(tmp / "alt", variant="alt")[0]
    pd.set_option("display.width", 160)
    cmp = compare_runs(Run(a), Run(b))

    print(f"{cmp.b} minus {cmp.a}")
    h = cmp.headline()
    print(f"total cost delta {h['total_cost_delta']:,.0f} ({h['total_cost_rel_delta']:+.2%}); "
          f"largest gap {h['max_gap']:.1e}")

    print("\nCost lines:")
    print(cmp.costs[cmp.costs["section"] == "cost"].to_string(index=False))
    if cmp.builds is not None:
        print("\nBuilds (final year, MW):")
        print(cmp.builds[cmp.builds["delta"] != 0].to_string(index=False))
    if cmp.prices is not None:
        print("\nMean price by bus:")
        print(cmp.prices.to_string(index=False))

    if cmp.warnings:
        print("\nComparability warnings:")
        for w in cmp.warnings:
            print(f"  - {w}")
    else:
        print("\nNo comparability warnings.")
    return cmp


if __name__ == "__main__":
    args = sys.argv[1:]
    main(*args) if len(args) == 2 else main()
