# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Monolithic reference solves and an exhaustive N-1 checker.

``solve_monolithic`` builds the deterministic equivalent of eq. (1). With
``n1=True`` it also embeds every post-contingency block ``Q_s^c(x) <= M_s``
(eq. 3) for every scenario and contingency -- the "SCOPF master" the paper
argues is intractable at scale. On a toy it is small, which makes it the
ground truth that the decomposition must reproduce.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp

from ..solver import INF, solve
from .dispatch import ParamLP, dispatch_lp
from .network import Case


@dataclass
class MonolithicResult:
    x: np.ndarray
    objective: float
    n_variables: int
    n_rows: int


def solve_monolithic(case: Case, *, n1: bool = True, integer_lines: bool = False) -> MonolithicResult:
    """Solve eq. (1) (plus all of eq. 3 if ``n1``) as one LP or MILP."""
    nx = case.nx
    blocks: list[tuple[ParamLP, float, float | None]] = []  # (lp, objective weight, shed cap)
    for s, scen in enumerate(case.scenarios):
        blocks.append((dispatch_lp(case, s, objective="cost"), scen.probability, None))
        if n1:
            for c in case.contingency_indices():
                blocks.append((dispatch_lp(case, s, outage=c, objective="shed"), 0.0, scen.n1_shed_limit))

    cost = [case.investment_cost()]
    lb = [np.zeros(nx)]
    ub = [case.x_upper()]
    rows, lo, hi = [], [], []
    ncols = nx + sum(b[0].c.size for b in blocks)
    offset = nx
    for lp, weight, cap in blocks:
        ny = lp.c.size
        cost.append(weight * lp.c)
        lb.append(lp.lb)
        ub.append(lp.ub)

        def place(Ay, Ax=None):
            left = Ax if Ax is not None else sp.csr_matrix((Ay.shape[0], nx))
            pad_l = sp.csr_matrix((Ay.shape[0], offset - nx))
            pad_r = sp.csr_matrix((Ay.shape[0], ncols - offset - ny))
            return sp.hstack([left, pad_l, Ay, pad_r])

        # A_ub y - B_ub x <= b_ub
        rows.append(place(lp.A_ub, -lp.B_ub))
        lo.append(np.full(lp.A_ub.shape[0], -INF))
        hi.append(lp.b_ub)
        # A_eq y = b_eq
        rows.append(place(lp.A_eq))
        lo.append(lp.b_eq)
        hi.append(lp.b_eq)
        if cap is not None:  # shed energy <= M_s
            rows.append(place(sp.csr_matrix(lp.c)))
            lo.append([-INF])
            hi.append([cap])
        offset += ny

    A = sp.vstack(rows).tocsr()
    integrality = None
    if integer_lines:
        integrality = np.zeros(ncols, bool)
        integrality[case.U : nx] = True
    res = solve(
        np.concatenate(cost), A, np.concatenate(lo), np.concatenate(hi),
        np.concatenate(lb), np.concatenate(ub),
        integrality=integrality, options={"mip_rel_gap": 1e-9},
    )
    if not res.ok:
        raise RuntimeError(f"monolithic solve failed: {res.status}")
    return MonolithicResult(res.x[:nx], res.objective, ncols, A.shape[0])


def evaluate(case: Case, x) -> float:
    """Total expected cost ``c'x + sum_s p_s Q_s(x)`` at a given ``x``."""
    x = np.asarray(x, dtype=float)
    total = float(case.investment_cost() @ x)
    for s, scen in enumerate(case.scenarios):
        total += scen.probability * dispatch_lp(case, s, objective="cost").solve(x).value
    return total


def n1_shed_table(case: Case, x) -> np.ndarray:
    """Post-contingency shed energy (MWh) for every scenario and contingency
    at ``x``, shape ``(S, |C|)``, by solving every post-contingency LP."""
    conts = case.contingency_indices()
    out = np.zeros((case.S, len(conts)))
    for s in range(case.S):
        for j, c in enumerate(conts):
            out[s, j] = dispatch_lp(case, s, outage=c, objective="shed").solve(x).value
    return out


def max_n1_violation(case: Case, x) -> float:
    """``max_{s,c} (Q_s^c(x) - M_s)``; ``<= 0`` (up to tolerance) means N-1 secure."""
    table = n1_shed_table(case, x)
    limits = np.array([s.n1_shed_limit for s in case.scenarios])[:, None]
    return float((table - limits).max())
