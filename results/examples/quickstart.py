# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Open a run directory and print its headline results.

Usage::

    python examples/quickstart.py [RUN_DIR [BUNDLE_DIR]]

Without RUN_DIR a synthetic run (and its input bundle) is generated in a
temporary directory. BUNDLE_DIR is needed for line endpoints, availability and
existing capacity when the run directory does not echo them.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd

from ce_results import Run, capture_prices, congestion_rent, curtailment_summary
from ce_results.synthetic import write_synthetic_run


def main(path: str | None = None, bundle: str | None = None) -> Run:
    if path is None:
        path, bundle = write_synthetic_run(Path(tempfile.mkdtemp()) / "synthetic_base")
    run = Run(path, bundle=bundle)
    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", 20)

    s, p = run.stamps, run.provenance
    print(f"run {run.name}  ({run.time_mode}, engine {p.engine_version}, money {p.currency} {p.dollar_year})")
    print(f"objective {s.objective:,.0f} {p.currency or ''}  gap {s.gap:.2e}  stop {s.stop_cause}  "
          f"certified at 1e-3: {s.certified(1e-3)}")
    if run.qa is not None:
        print(f"QA {run.qa.status}  failing: {list(run.qa.failing) or 'none'}")

    print("\nNew build in the final year (MW, cumulative):")
    b = run.builds(by=["year", "technology"])
    print(b[b["year"] == b["year"].max()].to_string(index=False))

    print("\nCapture prices:")
    cp = capture_prices(run, ["renewable", "storage_discharge"])
    print(cp[["year", "component_name", "energy_mwh", "capture_price", "capture_ratio"]].to_string(index=False))

    print("\nCongestion rent by line:")
    print(congestion_rent(run)[["year", "line", "congestion_rent", "hours_at_limit"]].to_string(index=False))

    print("\nCurtailment:")
    print(curtailment_summary(run)[["year", "site_name", "curtailed_mwh", "curtailment_rate"]]
          .to_string(index=False))

    if run.notes:
        print("\nReader notes:")
        for n in run.notes:
            print(f"  - {n}")
    return run


if __name__ == "__main__":
    main(*sys.argv[1:3])
