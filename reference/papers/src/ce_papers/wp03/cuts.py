# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Benders optimality cuts (CE-WP-2026-03 eq. 5) and Magnanti-Wong
Pareto-optimal cut selection (Section 4.1).

A cut for scenario ``s`` is stored as ``(alpha, beta)`` meaning
``theta_s >= alpha + beta'x``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp

from ..solver import INF, solve
from .dispatch import ParamLP, SubResult


@dataclass(frozen=True)
class Cut:
    """``theta_s >= alpha + beta'x`` for scenario ``scenario``."""

    scenario: int
    alpha: float
    beta: np.ndarray

    def value(self, x) -> float:
        return float(self.alpha + self.beta @ np.asarray(x, dtype=float))


def standard_cut(s: int, sub: SubResult, x_hat) -> Cut:
    """Eq. (5): ``alpha = Q(x_hat) - beta'x_hat``, ``beta`` from the duals the
    LP solver happened to return."""
    x_hat = np.asarray(x_hat, dtype=float)
    return Cut(s, sub.value - sub.slope @ x_hat, sub.slope.copy())


def magnanti_wong_cut(s: int, plp: ParamLP, x_hat, q_hat: float, x_core, rel_tol: float = 1e-7) -> Cut:
    """Pareto-optimal cut (Magnanti & Wong; paper Section 4.1).

    When the dispatch LP is degenerate it has many optimal dual solutions,
    each giving a valid cut that is tight at ``x_hat``. Among them we pick the
    one that is highest at a core point ``x_core`` (a relative-interior point
    of the master feasible region) by solving a second LP over the dual
    polyhedron::

        max   d(x_core; pi)
        s.t.  pi dual-feasible,   d(x_hat; pi) >= Q(x_hat) - tol

    where ``d(x; pi)`` is the dual objective at right-hand side ``x``. With
    duals ``pi = (lam, mu, alpha, beta)`` for the ``<=`` rows, the equality
    rows, and the finite lower / upper column bounds::

        d(x; pi) = (b_ub + B_ub x)'lam + b_eq'mu + lb'alpha + ub'beta
        A_ub'lam + A_eq'mu + alpha + beta = c,
        lam <= 0,  alpha >= 0,  beta <= 0.

    Any dual-feasible ``pi`` gives a globally valid cut by weak duality:
    ``Q(x) >= d(x; pi)``.
    """
    x_hat = np.asarray(x_hat, dtype=float)
    x_core = np.asarray(x_core, dtype=float)
    n = plp.c.size
    m_ub, m_eq = plp.A_ub.shape[0], plp.A_eq.shape[0]
    lb_fin = np.isfinite(plp.lb) & (plp.lb > -INF)
    ub_fin = np.isfinite(plp.ub) & (plp.ub < INF)
    lbv = np.where(lb_fin, plp.lb, 0.0)
    ubv = np.where(ub_fin, plp.ub, 0.0)

    # Dual variables v = [lam (m_ub), mu (m_eq), alpha (n), beta (n)].
    I = sp.identity(n, format="csr")
    stationarity = sp.hstack([plp.A_ub.T, plp.A_eq.T, I, I]).tocsr()
    h_hat = np.concatenate([plp.rhs(x_hat), plp.b_eq, lbv, ubv])
    h_core = np.concatenate([plp.rhs(x_core), plp.b_eq, lbv, ubv])
    A = sp.vstack([stationarity, sp.csr_matrix(h_hat)]).tocsr()
    tol = rel_tol * max(1.0, abs(q_hat))
    row_lo = np.concatenate([plp.c, [q_hat - tol]])
    row_hi = np.concatenate([plp.c, [INF]])
    v_lo = np.concatenate([np.full(m_ub, -INF), np.full(m_eq, -INF), np.zeros(n), np.where(ub_fin, -INF, 0.0)])
    v_hi = np.concatenate([np.zeros(m_ub), np.full(m_eq, INF), np.where(lb_fin, INF, 0.0), np.zeros(n)])
    res = solve(h_core, A, row_lo, row_hi, v_lo, v_hi, maximise=True)
    if not res.ok:
        raise RuntimeError(f"Magnanti-Wong LP not solved: {res.status}")
    lam = res.x[:m_ub]
    beta = plp.B_ub.T @ lam
    d_hat = float(h_hat @ res.x)  # dual objective at x_hat (<= Q(x_hat), ~ equal)
    return Cut(s, d_hat - beta @ x_hat, beta)
