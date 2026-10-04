# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""A thin wrapper around HiGHS (``highspy``) for the LPs, QPs and MILPs used
by the reference code.

Everything is expressed in the HiGHS-native "ranged row" form::

    minimise    c'x + 0.5 * sum_i h_i x_i^2
    subject to  row_lo <= A x <= row_hi
                lb     <=   x <= ub
                x_i integer for i in the integrality mask

Only a *diagonal* Hessian is supported: that is all the level-bundle master
(CE-WP-2026-03, eq. 6) needs.

Dual convention
---------------
``row_dual[i]`` is the sensitivity of the optimal objective to the active
bound of row ``i``: increasing that bound by one unit changes the optimum by
``row_dual[i]`` (for a minimisation). For a ``<=`` row that binds this is
non-positive; for a ``>=`` row it is non-negative. This is the convention
HiGHS reports and the one the Benders cut formula relies on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import highspy
import numpy as np
import scipy.sparse as sp

INF = highspy.kHighsInf


@dataclass
class Result:
    """Outcome of one solve."""

    status: str
    """``"optimal"`` or the HiGHS model-status string otherwise."""
    x: np.ndarray
    objective: float
    row_dual: np.ndarray = field(default_factory=lambda: np.zeros(0))
    mip_gap: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "optimal"


def _as_array(v, n, fill):
    if v is None:
        return np.full(n, fill, dtype=float)
    a = np.asarray(v, dtype=float).ravel()
    if a.size == 1 and n != 1:
        return np.full(n, float(a[0]))
    if a.size != n:
        raise ValueError(f"expected length {n}, got {a.size}")
    return a


def solve(
    c,
    A=None,
    row_lo=None,
    row_hi=None,
    lb=None,
    ub=None,
    *,
    integrality=None,
    hessian_diag=None,
    maximise: bool = False,
    relax: bool = False,
    options: dict | None = None,
) -> Result:
    """Solve an LP, diagonal QP or MILP with HiGHS.

    Parameters
    ----------
    c : array (n,)
        Linear objective.
    A : sparse or dense (m, n), optional
        Constraint matrix.
    row_lo, row_hi : array (m,), optional
        Row bounds; ``-INF`` / ``INF`` for one-sided rows.
    lb, ub : array (n,), optional
        Column bounds (default ``0`` and ``INF``).
    integrality : bool array (n,), optional
        ``True`` marks an integer column.
    hessian_diag : array (n,), optional
        Diagonal of the (positive semi-definite) Hessian. Cannot be combined
        with integer columns (HiGHS does not solve MIQPs).
    maximise : bool
        Maximise instead of minimise.
    relax : bool
        Ignore ``integrality`` and solve the continuous relaxation.
    options : dict, optional
        Extra HiGHS options, e.g. ``{"mip_rel_gap": 0.0}``.
    """
    c = np.asarray(c, dtype=float).ravel()
    n = c.size
    if A is None:
        A = sp.csc_matrix((0, n))
    A = sp.csc_matrix(A, dtype=float)
    if A.shape[1] != n:
        raise ValueError("A has the wrong number of columns")
    m = A.shape[0]
    row_lo = _as_array(row_lo, m, -INF)
    row_hi = _as_array(row_hi, m, INF)
    lb = _as_array(lb, n, 0.0)
    ub = _as_array(ub, n, INF)
    # highspy expects +/- kHighsInf, not numpy inf.
    for arr in (row_lo, row_hi, lb, ub):
        arr[arr >= INF] = INF
        arr[arr <= -INF] = -INF

    integer = None
    if integrality is not None and not relax:
        integer = np.asarray(integrality, dtype=bool).ravel()
        if integer.size != n:
            raise ValueError("integrality has the wrong length")
        if not integer.any():
            integer = None
    if integer is not None and hessian_diag is not None:
        raise ValueError("HiGHS does not solve mixed-integer QPs")

    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    for key, val in (options or {}).items():
        h.setOptionValue(key, val)

    lp = highspy.HighsLp()
    lp.num_col_ = n
    lp.num_row_ = m
    lp.col_cost_ = c
    lp.col_lower_ = lb
    lp.col_upper_ = ub
    lp.row_lower_ = row_lo
    lp.row_upper_ = row_hi
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.start_ = A.indptr.astype(np.int32)
    lp.a_matrix_.index_ = A.indices.astype(np.int32)
    lp.a_matrix_.value_ = A.data
    lp.sense_ = highspy.ObjSense.kMaximize if maximise else highspy.ObjSense.kMinimize
    if integer is not None:
        lp.integrality_ = [
            highspy.HighsVarType.kInteger if flag else highspy.HighsVarType.kContinuous
            for flag in integer
        ]
    h.passModel(lp)

    if hessian_diag is not None:
        hd = _as_array(hessian_diag, n, 0.0)
        if (hd < 0).any():
            raise ValueError("Hessian diagonal must be non-negative")
        nz = np.flatnonzero(hd)
        start = np.zeros(n + 1, dtype=np.int32)
        start[nz + 1] = 1
        start = np.cumsum(start).astype(np.int32)
        hess = highspy.HighsHessian()
        hess.dim_ = n
        hess.format_ = highspy.HessianFormat.kTriangular
        hess.start_ = start
        hess.index_ = nz.astype(np.int32)
        hess.value_ = hd[nz]
        h.passHessian(hess)

    h.run()
    status = h.getModelStatus()
    if status != highspy.HighsModelStatus.kOptimal:
        return Result(h.modelStatusToString(status), np.full(n, np.nan), np.nan)
    sol = h.getSolution()
    info = h.getInfo()
    x = np.asarray(sol.col_value, dtype=float)
    duals = np.asarray(sol.row_dual, dtype=float) if integer is None else np.zeros(m)
    gap = float(info.mip_gap) if integer is not None else 0.0
    return Result("optimal", x, float(info.objective_function_value), duals, gap)


def solve_split(
    c,
    A_ub=None,
    b_ub=None,
    A_eq=None,
    b_eq=None,
    lb=None,
    ub=None,
    **kwargs,
) -> tuple[Result, np.ndarray, np.ndarray]:
    """Solve ``min c'x s.t. A_ub x <= b_ub, A_eq x = b_eq, lb <= x <= ub``.

    Returns ``(result, dual_ub, dual_eq)`` where the duals are the objective
    sensitivities d(objective)/d(b_ub) and d(objective)/d(b_eq).
    """
    c = np.asarray(c, dtype=float).ravel()
    n = c.size
    blocks, lo, hi = [], [], []
    m_ub = m_eq = 0
    if A_ub is not None:
        A_ub = sp.csr_matrix(A_ub)
        m_ub = A_ub.shape[0]
        blocks.append(A_ub)
        lo.append(np.full(m_ub, -INF))
        hi.append(np.asarray(b_ub, dtype=float))
    if A_eq is not None:
        A_eq = sp.csr_matrix(A_eq)
        m_eq = A_eq.shape[0]
        blocks.append(A_eq)
        b_eq = np.asarray(b_eq, dtype=float)
        lo.append(b_eq)
        hi.append(b_eq)
    A = sp.vstack(blocks) if blocks else sp.csr_matrix((0, n))
    res = solve(
        c,
        A,
        np.concatenate(lo) if lo else None,
        np.concatenate(hi) if hi else None,
        lb,
        ub,
        **kwargs,
    )
    if not res.ok or res.row_dual.size == 0:
        return res, np.zeros(m_ub), np.zeros(m_eq)
    return res, res.row_dual[:m_ub], res.row_dual[m_ub : m_ub + m_eq]
