# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The three-level error decomposition (CE-WP-2026-04 eq. 4).

For a maximisation over a feasible set ``F`` with true optimum ``x*`` and the
surrogate MILP's answer ``x_hat``::

    f*(x*) - f*(x_hat)  <=  2 sup_{x in F} |f*(x) - f_hat(x)|  +  MILP gap

(the triangle inequality at ``x*`` and ``x_hat``). The third source -- the
gap between the oracle and physical truth -- is the simulator's own
documented uncertainty and is reported alongside, not bounded here.

The supremum and ``x*`` are *estimated* by dense sampling of ``F``, so this
report is an empirical illustration of eq. (4), not a certificate: the
sampled supremum can only under-estimate the true one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .mlp import ReluMLP, TrainReport
from .milp import CurtailmentResult
from .oracle import WakeRow


@dataclass
class ErrorReport:
    val_mse: float
    """(i) surrogate fit: validation MSE on held-out samples."""
    val_r2: float
    sup_error: float
    """(i) sampled ``sup |f* - f_hat|`` over the optimisation's feasible set."""
    milp_gap: float
    """(ii) absolute MILP optimality gap reported by the solver."""
    simulator_uncertainty: float | None
    """(iii) the oracle's documented uncertainty, if supplied."""
    bound: float
    """``2 * sup_error + milp_gap``: the eq. (4) bound on induced sub-optimality."""
    regret: float
    """``f*(x_best) - f*(x_hat)`` with ``x_best`` the best sampled point."""
    f_true_at_xhat: float
    f_true_best: float
    x_best: np.ndarray

    @property
    def within_bound(self) -> bool:
        return self.regret <= self.bound + 1e-12


def error_report(
    farm: WakeRow,
    net: ReluMLP,
    train: TrainReport,
    opt: CurtailmentResult,
    *,
    budget: float,
    n_samples: int = 50_000,
    seed: int = 0,
    simulator_uncertainty: float | None = None,
) -> ErrorReport:
    """Evaluate eq. (4) for the curtailment case study."""
    rng = np.random.default_rng(seed)
    n_c = farm.n_turbines
    C = rng.random((n_samples, n_c)) * farm.c_max
    C = C[C.sum(axis=1) <= budget]
    C = np.vstack([C, np.zeros(n_c), opt.x[1:]])
    X = np.column_stack([np.full(len(C), opt.x[0]), C])
    f_true, f_hat = farm.capacity_factor(X), net(X)
    best = int(np.argmax(f_true))
    f_xhat = float(farm.capacity_factor(opt.x[None, :])[0])
    sup = float(np.max(np.abs(f_true - f_hat)))
    gap = float(opt.mip_gap * abs(opt.value))
    return ErrorReport(
        val_mse=train.val_mse,
        val_r2=train.val_r2,
        sup_error=sup,
        milp_gap=gap,
        simulator_uncertainty=simulator_uncertainty,
        bound=2.0 * sup + gap,
        regret=float(f_true[best] - f_xhat),
        f_true_at_xhat=f_xhat,
        f_true_best=float(f_true[best]),
        x_best=X[best],
    )
