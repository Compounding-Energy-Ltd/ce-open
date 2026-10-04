# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Reference code for CE-WP-2026-04: pruned ReLU surrogates embedded in MILPs.

Pipeline (the paper's Appendix B stages, each a function here):

1. data     -- :meth:`WakeRow.sample` draws uniform samples from the toy oracle;
2. train    -- :func:`train_mlp` fits a ReLU MLP (He init, mini-batch SGD);
3. prune    -- :func:`interval_bounds` (IBP) and :func:`pruning_report`;
4. optimise -- :func:`optimise_curtailment` / :func:`embed_relu` build and solve
               the big-M MILP (naive, IBP, or IBP + pruning);
5. report   -- :func:`error_report` evaluates the eq. (4) decomposition.
"""

from .bounds import (
    ACTIVE,
    AMBIGUOUS,
    INACTIVE,
    LayerBounds,
    interval_bounds,
    naive_big_m,
    pruning_report,
    summarise,
)
from .io import load_network, save_network, write_rows_csv
from .milp import MODES, CurtailmentResult, Embedding, Model, embed_relu, optimise_curtailment
from .mlp import ReluMLP, TrainReport, train_mlp
from .oracle import WakeRow
from .report import ErrorReport, error_report

__all__ = [
    "ACTIVE", "AMBIGUOUS", "INACTIVE", "MODES", "CurtailmentResult", "Embedding", "ErrorReport",
    "LayerBounds", "Model", "ReluMLP", "TrainReport", "WakeRow", "embed_relu", "error_report",
    "interval_bounds", "load_network", "naive_big_m", "optimise_curtailment", "pruning_report",
    "save_network", "summarise", "train_mlp", "write_rows_csv",
]
