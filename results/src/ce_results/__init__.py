# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Read CE run directories into pandas (and optionally xarray).

>>> from ce_results import Run, compare_runs
>>> run = Run("path/to/run")                          # doctest: +SKIP
>>> run.stamps.gap, run.provenance.register_version   # doctest: +SKIP
>>> run.builds(by=["technology", "year"])             # doctest: +SKIP
"""

from .analysis import (
    capture_prices,
    congestion_rent,
    curtailment_summary,
    generation_by_technology,
    mean_lmp_by_bus,
)
from .compare import RunComparison, compare_runs
from .run import Run
from .schema import TABLE_SPECS, SchemaError, TableSpec
from .stamps import Provenance, QAReport, SolveStamps

__version__ = "0.1.0"

__all__ = [
    "Run",
    "SolveStamps",
    "Provenance",
    "QAReport",
    "TableSpec",
    "TABLE_SPECS",
    "SchemaError",
    "RunComparison",
    "compare_runs",
    "capture_prices",
    "congestion_rent",
    "curtailment_summary",
    "generation_by_technology",
    "mean_lmp_by_bus",
    "__version__",
]
