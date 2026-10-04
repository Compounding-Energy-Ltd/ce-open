# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Benders decomposition for two-stage, N-1 secure capacity expansion
(CE-WP-2026-03 Algorithm 1, eqs. 4-6, Algorithm 2).

Four pieces, each toggleable, composed as the paper describes:

* the multi-cut Benders master (eq. 4) and dispatch subproblems (eq. 2);
* cut selection: plain duals (eq. 5) or Magnanti-Wong (Section 4.1);
* stabilisation: none (Kelley / naive Benders) or the level bundle (eq. 6);
* N-1 security: off, or lazy security cuts from the LODF-screened
  separation oracle (Algorithm 2).

The lower bound ``LB_k`` is always the optimal value of the cutting-plane
master (eq. 4) over all cuts collected so far. The upper bound is
``UB_k = c'x_k + sum_s p_s Q_s(x_k)`` taken over trial points that the
N-1 oracle accepts, so the returned ``x`` is N-1 secure.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import scipy.sparse as sp

from ..solver import INF, solve
from .contingency import ContingencyLPs, SecurityCut, n1_oracle
from .cuts import Cut, magnanti_wong_cut, standard_cut
from .dispatch import ParamLP, dispatch_lp
from .network import Case


class Master:
    """Relaxed Benders master over ``z = [x (nx), theta (S)]``.

    ``theta_s`` is bounded below by ``theta_lb`` (dispatch costs are
    non-negative, so 0 is valid); without it the cut-free first master would
    be unbounded.

    Numerics: cut coefficients are in currency per MW and right-hand sides
    in currency, which spans many orders of magnitude. The master is
    therefore solved in a scaled space -- ``xi = x / xbar`` in ``[0, 1]``,
    ``theta' = theta / objective_scale`` -- with every cut row normalised to
    unit infinity-norm. Results are mapped back to original units.
    """

    def __init__(self, case: Case, *, integer_lines: bool = False, theta_lb: float = 0.0):
        self.case = case
        self.nx, self.S = case.nx, case.S
        self.c_inv = case.investment_cost()
        self.prob = np.array([s.probability for s in case.scenarios])
        self.x_hi = case.x_upper()
        self.x_scale = np.where(self.x_hi > 0, self.x_hi, 1.0)
        self.theta_lb = theta_lb
        self.objective_scale = 1.0
        self.integrality = None
        if integer_lines:
            self.integrality = np.concatenate(
                [np.zeros(case.U, bool), np.ones(case.L, bool), np.zeros(self.S, bool)]
            )
        self.opt_cuts: list[Cut] = []
        self.sec_cuts: list[SecurityCut] = []

    def add_cut(self, cut: Cut) -> None:
        self.opt_cuts.append(cut)

    def add_security_cut(self, cut: SecurityCut) -> None:
        self.sec_cuts.append(cut)

    # -- scaled model ------------------------------------------------------
    def _cost(self) -> np.ndarray:
        return np.concatenate([self.c_inv * self.x_scale / self.objective_scale, self.prob])

    def _bounds(self):
        lb = np.concatenate([np.zeros(self.nx), np.full(self.S, self.theta_lb / self.objective_scale)])
        ub = np.concatenate([self.x_hi / self.x_scale, np.full(self.S, INF)])
        return lb, ub

    def _rows(self):
        n = self.nx + self.S
        sig = self.objective_scale
        rows, hi = [], []
        for cut in self.opt_cuts:  # beta'x - theta_s <= -alpha
            r = np.zeros(n)
            r[: self.nx] = cut.beta * self.x_scale / sig
            r[self.nx + cut.scenario] = -1.0
            rows.append(r), hi.append(-cut.alpha / sig)
        for cut in self.sec_cuts:  # g'x <= rhs
            r = np.zeros(n)
            r[: self.nx] = cut.g * self.x_scale
            rows.append(r), hi.append(cut.rhs)
        if not rows:
            return sp.csr_matrix((0, n)), np.zeros(0), np.zeros(0)
        R, hi = np.array(rows), np.array(hi, float)
        norm = np.abs(R).max(axis=1)
        norm[norm == 0] = 1.0
        return sp.csr_matrix(R / norm[:, None]), np.full(len(hi), -INF), hi / norm

    def _unscale(self, z) -> np.ndarray:
        return np.clip(z[: self.nx] * self.x_scale, 0.0, self.x_hi)

    def solve_lower(self) -> tuple[np.ndarray, float]:
        """Kelley master, eq. (4). Returns ``(x, LB)``."""
        A, lo, hi = self._rows()
        lb, ub = self._bounds()
        res = solve(
            self._cost(), A, lo, hi, lb, ub,
            integrality=self.integrality, options={"mip_rel_gap": 1e-9},
        )
        if not res.ok:
            raise RuntimeError(f"Benders master not solved: {res.status} (is the problem N-1 feasible?)")
        return self._unscale(res.x), res.objective * self.objective_scale

    def solve_level(self, centre, level: float) -> np.ndarray | None:
        """Level-bundle projection, eq. (6)::

            min  0.5 * ||xi - xi_cen||^2
            s.t. all cuts,  c'x + sum_s p_s theta_s <= level,  bounds,

        in the scaled coordinates ``xi = x / xbar`` (a diagonal metric in the
        original units, so MW and line-fraction coordinates are comparable).
        The paper writes ``0.5 * rho * ||x - x_cen||^2``; ``rho`` only scales
        the objective, so it does not change the projection. Returns ``None``
        if the QP is not solved, and the caller falls back to the Kelley point.
        """
        A, lo, hi = self._rows()
        cost = self._cost()
        A = sp.vstack([A, sp.csr_matrix(cost / np.abs(cost).max())]).tocsr()
        lo = np.concatenate([lo, [-INF]])
        hi = np.concatenate([hi, [level / self.objective_scale / np.abs(cost).max()]])
        w = np.concatenate([np.ones(self.nx), np.zeros(self.S)])
        xi_c = np.concatenate([np.asarray(centre, float) / self.x_scale, np.zeros(self.S)])
        lb, ub = self._bounds()
        res = solve(
            -w * xi_c, A, lo, hi, lb, ub, hessian_diag=w,
            options={"qp_iteration_limit": 20_000, "time_limit": 30.0},
        )
        if not res.ok:
            return None
        return self._unscale(res.x)


@dataclass
class Iteration:
    k: int
    step: str
    """How the trial point was produced: ``"kelley"`` or ``"level"``."""
    trial_value: float
    """``c'x_k + sum p_s Q_s(x_k)`` (NaN if the oracle rejected ``x_k``)."""
    lower_bound: float
    upper_bound: float
    gap: float
    serious_step: bool
    opt_cuts: int
    security_cuts_added: int
    candidates: int
    screened_out: int
    contingency_lps: int


@dataclass
class BendersResult:
    x: np.ndarray
    objective: float
    lower_bound: float
    converged: bool
    history: list[Iteration] = field(default_factory=list)
    security_cuts: list[SecurityCut] = field(default_factory=list)

    @property
    def iterations(self) -> int:
        return len(self.history)

    @property
    def gap(self) -> float:
        return self.history[-1].gap if self.history else float("inf")

    @property
    def contingency_lps(self) -> int:
        return sum(h.contingency_lps for h in self.history)

    def write_history_csv(self, path) -> Path:
        """Write the iteration log as CSV (one row per outer iteration)."""
        path = Path(path)
        cols = list(Iteration.__dataclass_fields__)
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for h in self.history:
                w.writerow([getattr(h, c) for c in cols])
        return path


def solve_benders(
    case: Case,
    *,
    stabilisation: str = "level",
    kappa: float = 0.6,
    cut_selection: str = "standard",
    n1: bool = True,
    use_screen: bool = True,
    tol: float = 1e-6,
    max_iter: int = 500,
    shed_tol: float = 1e-4,
    max_security_cuts: int | None = None,
    core_point=None,
    descent: float = 0.1,
    integer_lines: bool = False,
) -> BendersResult:
    """Algorithm 1 with optional Magnanti-Wong cuts, level-bundle
    stabilisation and the N-1 separation oracle.

    Parameters
    ----------
    stabilisation : ``"level"`` or ``"none"``
        ``"none"`` is naive (Kelley) Benders. ``"level"`` projects the
        stability centre onto the level set ``L_k = LB_k + kappa (UB_k - LB_k)``
        (eq. 6) once an N-1 feasible incumbent exists.
    kappa : float
        Level fraction in ``(0, 1)``; the paper's default is 0.6 (Remark 4.2).
    cut_selection : ``"standard"`` or ``"magnanti-wong"``
    n1 : bool
        Enforce N-1 security with the separation oracle.
    use_screen : bool
        LODF pre-screen in the oracle (otherwise every contingency is solved).
    tol : float
        Relative gap ``(UB - LB) / |UB|`` at which to stop.
    shed_tol : float
        Oracle violation tolerance in MWh (see :func:`n1_oracle`).
    max_security_cuts : int, optional
        Per scenario and iteration; ``1`` is the literal Algorithm 2.
    core_point : array, optional
        Magnanti-Wong core point (default: the centre of the ``x`` box).
    descent : float
        Sufficient-descent fraction ``m`` in ``(0, 1)``: the stability centre
        moves to ``x_k`` when ``f(x_k) <= f(centre) - m (f(centre) - L)``.
    integer_lines : bool
        Binary line reinforcement (the integer master of Theorem 3.1). Only
        with ``stabilisation="none"``: HiGHS has no mixed-integer QP.
    """
    if stabilisation not in ("level", "none"):
        raise ValueError("stabilisation must be 'level' or 'none'")
    if cut_selection not in ("standard", "magnanti-wong"):
        raise ValueError("cut_selection must be 'standard' or 'magnanti-wong'")
    if not 0.0 < kappa < 1.0:
        raise ValueError("kappa must lie in (0, 1)")
    if integer_lines and stabilisation != "none":
        raise ValueError("integer_lines needs stabilisation='none' (no MIQP in HiGHS)")

    S = case.S
    probs = np.array([s.probability for s in case.scenarios])
    c_inv = case.investment_cost()
    x_hi = case.x_upper()
    core = 0.5 * x_hi if core_point is None else np.asarray(core_point, float)

    base_lps: list[ParamLP] = [dispatch_lp(case, s, objective="cost") for s in range(S)]
    cont_lps = ContingencyLPs(case)
    master = Master(case, integer_lines=integer_lines)

    upper, x_best = INF, None
    centre, f_centre, level = None, INF, None
    history: list[Iteration] = []
    all_sec: list[SecurityCut] = []

    x_k, lower = master.solve_lower()
    step = "kelley"
    for k in range(max_iter):
        # Algorithm 1 lines 4-8: evaluate every scenario and add one cut each.
        f_k = float(c_inv @ x_k)
        subs = []
        for s in range(S):
            sub = base_lps[s].solve(x_k)
            subs.append(sub)
            f_k += probs[s] * sub.value
            cut = None
            if cut_selection == "magnanti-wong":
                try:
                    cut = magnanti_wong_cut(s, base_lps[s], x_k, sub.value, core)
                except RuntimeError:
                    cut = None  # numerical trouble in the second LP: keep the plain cut
            master.add_cut(cut if cut is not None else standard_cut(s, sub, x_k))
        if k == 0:
            master.objective_scale = max(1.0, abs(f_k))

        # Algorithm 2: the N-1 separation oracle.
        n_sec = n_cand = n_out = n_lp = 0
        if n1:
            for s in range(S):
                rep = n1_oracle(
                    case, s, x_k, subs[s], base_lps[s], cont_lps,
                    use_screen=use_screen, shed_tol=shed_tol, max_cuts=max_security_cuts,
                )
                for cut in rep.cuts:
                    master.add_security_cut(cut)
                all_sec.extend(rep.cuts)
                n_sec += len(rep.cuts)
                n_cand += len(rep.candidates)
                n_out += rep.screened_out
                n_lp += rep.lps_solved
        feasible = n_sec == 0

        serious = False
        if feasible:
            if f_k < upper:
                upper, x_best = f_k, x_k.copy()
            if stabilisation == "level":
                if centre is None or (level is not None and f_k <= f_centre - descent * (f_centre - level)):
                    centre, f_centre, serious = x_best.copy(), upper, True

        x_kelley, lower = master.solve_lower()
        gap = (upper - lower) / abs(upper) if np.isfinite(upper) and upper != 0 else INF
        history.append(
            Iteration(
                k, step, f_k if feasible else float("nan"), lower, upper, gap, serious,
                len(master.opt_cuts), n_sec, n_cand, n_out, n_lp,
            )
        )
        if gap <= tol:
            break

        # Next trial point.
        x_next = None
        if stabilisation == "level" and centre is not None:
            level = lower + kappa * (upper - lower)
            x_next = master.solve_level(centre, level)
            step = "level"
        if x_next is None:
            x_next, step = x_kelley, "kelley"
        x_k = x_next

    converged = bool(history) and history[-1].gap <= tol
    if x_best is None:
        x_best = np.full(case.nx, np.nan)
    return BendersResult(x_best, upper, lower, converged, history, all_sec)
