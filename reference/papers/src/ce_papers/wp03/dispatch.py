# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Second-stage dispatch LPs (CE-WP-2026-03 eq. 2 and eq. 3) in a parametric
form that exposes their dependence on the first-stage decision ``x``.

Every second-stage problem here is written as::

    Q(x) = min  c'y
           s.t. A_ub y <= b_ub + B_ub x        (capacity and line-limit rows)
                A_eq y  = b_eq                 (power balance, DC flow)
                lb <= y <= ub

Only the right-hand side of the ``<=`` rows depends on ``x``. By LP
sensitivity, if ``lam = d(objective)/d(b_ub)`` are the row duals at ``x_hat``
then ``B_ub' lam`` is a subgradient of ``Q`` at ``x_hat``, so

    Q(x) >= Q(x_hat) + (B_ub' lam)' (x - x_hat)          for every x.

That is the Benders cut of eq. (5): the paper's
``beta^gen = -sum_t delta_t sigma^gen a`` and
``beta^line = -sum_t delta_t sigma^line Delta f`` are exactly ``B_ub' lam``
(the paper writes the capacity duals ``sigma`` with the opposite sign and
the duration weight outside; here ``delta_t`` sits in the objective, so it is
already inside ``lam``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp

from ..solver import INF, solve_split
from .network import Case


@dataclass
class SubResult:
    """Solution of a parametric LP at one trial point."""

    value: float
    slope: np.ndarray
    """``dQ/dx`` (a subgradient), length ``case.nx``."""
    y: np.ndarray
    dual_ub: np.ndarray
    dual_eq: np.ndarray


@dataclass
class ParamLP:
    """``min c'y s.t. A_ub y <= b_ub + B_ub x, A_eq y = b_eq, lb <= y <= ub``."""

    c: np.ndarray
    A_ub: sp.csr_matrix
    b_ub: np.ndarray
    B_ub: sp.csr_matrix
    A_eq: sp.csr_matrix
    b_eq: np.ndarray
    lb: np.ndarray
    ub: np.ndarray
    layout: dict
    """Name -> (offset, shape) for unpacking ``y``."""

    def rhs(self, x) -> np.ndarray:
        return self.b_ub + self.B_ub @ np.asarray(x, dtype=float)

    def solve(self, x) -> SubResult:
        res, d_ub, d_eq = solve_split(
            self.c, self.A_ub, self.rhs(x), self.A_eq, self.b_eq, self.lb, self.ub
        )
        if not res.ok:
            raise RuntimeError(f"second-stage LP not solved: {res.status}")
        return SubResult(res.objective, self.B_ub.T @ d_ub, res.x, d_ub, d_eq)

    def unpack(self, y) -> dict[str, np.ndarray]:
        y = np.asarray(y)
        return {k: y[o : o + int(np.prod(shape))].reshape(shape) for k, (o, shape) in self.layout.items()}


def dispatch_lp(case: Case, s: int, outage: int | None = None, objective: str = "cost") -> ParamLP:
    """Build the dispatch LP of scenario ``s``.

    ``objective="cost"`` gives the per-scenario dispatch problem ``Q_s`` of
    eq. (2): duration-weighted variable cost plus VOLL times shed energy.

    ``objective="shed"`` with ``outage=c`` gives the post-contingency
    feasibility problem behind eq. (3): line ``c`` is removed (its flow is
    fixed to zero and its DC-flow row dropped), generation may be fully
    re-dispatched (corrective action) and the objective is the
    duration-weighted shed energy in MWh, so N-1 security reads
    ``Q_s^c(x) <= M_s``.

    Variables (each stored ``t``-major): ``g (T, U)``, ``f (T, L)``,
    ``theta (T, N)``, ``r (T, N)``.
    """
    if objective not in ("cost", "shed"):
        raise ValueError("objective must be 'cost' or 'shed'")
    T, N, L, U = case.T, case.N, case.L, case.U
    scen = case.scenarios[s]
    A_inc = case.incidence()
    delta = case.duration

    og, of, oth, orr = 0, T * U, T * U + T * L, T * U + T * L + T * N
    nvar = orr + T * N

    def g(t, u):
        return og + t * U + u

    def f(t, l):
        return of + t * L + l

    def th(t, n):
        return oth + t * N + n

    def r(t, n):
        return orr + t * N + n

    in_service = [l for l in range(L) if l != outage]
    unit_bus = [case.bus_index(u.bus) for u in case.units]
    existing = np.array([u.existing for u in case.units])
    var_cost = np.array([u.var_cost for u in case.units])
    rating = np.array([ln.rating for ln in case.lines])
    extra = np.array([ln.reinforcement for ln in case.lines])
    susc = np.array([ln.susceptance for ln in case.lines])

    # Objective.
    c = np.zeros(nvar)
    for t in range(T):
        if objective == "cost":
            c[[g(t, u) for u in range(U)]] = delta[t] * var_cost
            c[[r(t, n) for n in range(N)]] = delta[t] * case.voll
        else:
            c[[r(t, n) for n in range(N)]] = delta[t]

    # Bounds.
    lb = np.zeros(nvar)
    ub = np.full(nvar, INF)
    for t in range(T):
        for l in range(L):
            lb[f(t, l)], ub[f(t, l)] = (-INF, INF) if l != outage else (0.0, 0.0)
        for n in range(N):
            lb[th(t, n)], ub[th(t, n)] = -INF, INF
            ub[r(t, n)] = scen.demand[t, n]
        ref = case.bus_index(case.ref_bus)
        lb[th(t, ref)] = ub[th(t, ref)] = 0.0

    # <= rows whose right-hand side depends on x.
    ub_rows, ub_cols, ub_vals, b_ub = [], [], [], []
    B_rows, B_cols, B_vals = [], [], []
    row = 0
    for t in range(T):
        for u in range(U):  # g - a x_gen <= a e      (capacity, eq. 2)
            a = scen.availability[t, u]
            ub_rows.append(row), ub_cols.append(g(t, u)), ub_vals.append(1.0)
            b_ub.append(a * existing[u])
            if a != 0.0:
                B_rows.append(row), B_cols.append(u), B_vals.append(a)
            row += 1
        for l in in_service:  # +/- f - Delta f x_line <= f^max   (line limits, eq. 2)
            for sign in (1.0, -1.0):
                ub_rows.append(row), ub_cols.append(f(t, l)), ub_vals.append(sign)
                b_ub.append(rating[l])
                if extra[l] != 0.0:
                    B_rows.append(row), B_cols.append(U + l), B_vals.append(extra[l])
                row += 1
    m_ub = row
    A_ub = sp.csr_matrix((ub_vals, (ub_rows, ub_cols)), shape=(m_ub, nvar))
    B_ub = sp.csr_matrix((B_vals, (B_rows, B_cols)), shape=(m_ub, case.nx))

    # Equality rows.
    eq_rows, eq_cols, eq_vals, b_eq = [], [], [], []
    row = 0
    for t in range(T):
        for n in range(N):  # sum g - sum_l A_ln f + r = D   (balance, eq. 2)
            for u in range(U):
                if unit_bus[u] == n:
                    eq_rows.append(row), eq_cols.append(g(t, u)), eq_vals.append(1.0)
            for l in range(L):
                if A_inc[l, n] != 0.0:
                    eq_rows.append(row), eq_cols.append(f(t, l)), eq_vals.append(-A_inc[l, n])
            eq_rows.append(row), eq_cols.append(r(t, n)), eq_vals.append(1.0)
            b_eq.append(scen.demand[t, n])
            row += 1
        for l in in_service:  # f - B_ll sum_n A_ln theta = 0   (DC flow, eq. 2)
            eq_rows.append(row), eq_cols.append(f(t, l)), eq_vals.append(1.0)
            for n in range(N):
                if A_inc[l, n] != 0.0:
                    eq_rows.append(row), eq_cols.append(th(t, n)), eq_vals.append(-susc[l] * A_inc[l, n])
            b_eq.append(0.0)
            row += 1
    A_eq = sp.csr_matrix((eq_vals, (eq_rows, eq_cols)), shape=(row, nvar))

    layout = {"g": (og, (T, U)), "f": (of, (T, L)), "theta": (oth, (T, N)), "r": (orr, (T, N))}
    return ParamLP(c, A_ub, np.array(b_ub, float), B_ub, A_eq, np.array(b_eq, float), lb, ub, layout)


def shed_energy(case: Case, r: np.ndarray) -> float:
    """Duration-weighted shed energy (MWh) of an ``r`` array of shape ``(T, N)``."""
    return float(case.duration @ np.asarray(r).sum(axis=1))
