# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The N-1 separation oracle (CE-WP-2026-03 Algorithm 2, Section 5).

N-1 security (eq. 3) requires ``Q_s^c(x) <= M_s`` for every scenario ``s``
and contingency ``c``, where ``Q_s^c`` is the post-contingency problem with
line ``c`` removed. We use the shed-energy form of ``Q_s^c`` (see
:func:`ce_papers.wp03.dispatch.dispatch_lp` with ``objective="shed"``):
after the outage, generation may be re-dispatched freely and the objective is
the shed energy, so ``M_s`` is literally the permissible post-contingency
shedding.

Rather than embedding every ``(s, c)`` post-contingency block in the master
(the "SCOPF master"), the oracle is called at each trial point ``x_hat``:

1. take the base dispatch of scenario ``s`` at ``x_hat`` and its flows ``f*``;
2. pre-screen every contingency with line outage distribution factors:
   ``f_hat_l^c = f*_l + LODF[l, c] f*_c`` (the paper's ``Phi_lc``);
3. contingencies whose screened flows overload any line form ``C^cand``;
4. solve the post-contingency LP only for ``c in C^cand``; when its shed
   exceeds ``M_s`` (plus tolerance) emit a *security cut*.

Soundness of the screen: if the base dispatch sheds at most ``M_s`` and its
post-outage flows (exact in DC power flow) respect every remaining rating,
the base dispatch itself is a feasible post-contingency dispatch, so
``Q_s^c(x_hat) <= M_s`` and the LP can be skipped. A contingency that islands
the network (``LODF`` column undefined) is always a candidate.

Security cut: ``Q_s^c`` is convex and piecewise linear in ``x`` (its ``x``
dependence is only through the right-hand side), so with value ``v`` and
subgradient ``g`` at ``x_hat``::

    v + g'(x - x_hat) <= Q_s^c(x) <= M_s

is valid for every N-1 secure ``x`` and cuts off ``x_hat`` because ``v > M_s``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .dispatch import ParamLP, SubResult, dispatch_lp, shed_energy
from .network import Case


@dataclass(frozen=True)
class SecurityCut:
    """``g'x <= rhs`` for scenario ``scenario`` and outaged line ``line``."""

    scenario: int
    line: int
    g: np.ndarray
    rhs: float

    def violation(self, x) -> float:
        return float(self.g @ np.asarray(x, dtype=float) - self.rhs)


@dataclass
class OracleReport:
    """What one oracle call did for one scenario."""

    cuts: list[SecurityCut] = field(default_factory=list)
    candidates: list[int] = field(default_factory=list)
    screened_out: int = 0
    lps_solved: int = 0

    @property
    def feasible(self) -> bool:
        return not self.cuts


class ContingencyLPs:
    """Lazily-built post-contingency LPs, one per ``(scenario, line)``."""

    def __init__(self, case: Case):
        self.case = case
        self._lps: dict[tuple[int, int], ParamLP] = {}

    def get(self, s: int, c: int) -> ParamLP:
        key = (s, c)
        if key not in self._lps:
            self._lps[key] = dispatch_lp(self.case, s, outage=c, objective="shed")
        return self._lps[key]


def screen(case: Case, s: int, x_hat, base: SubResult, base_lp: ParamLP, tol: float = 1e-6) -> tuple[list[int], int]:
    """LODF pre-screen (Algorithm 2 steps 2-3).

    Returns ``(candidates, screened_out)``. ``tol`` is the MW overload
    tolerance (and the MWh tolerance on base shedding).
    """
    parts = base_lp.unpack(base.y)
    F = parts["f"]  # (T, L) base flows
    limits = case.line_limits(x_hat)
    lodf = case.lodf()
    conts = case.contingency_indices()
    if shed_energy(case, parts["r"]) > case.scenarios[s].n1_shed_limit + tol:
        return list(conts), 0  # the screen's soundness argument needs base shed <= M_s
    cand = []
    for c in conts:
        col = lodf[:, c]
        if np.isnan(col).any():
            cand.append(c)
            continue
        post = F + np.outer(F[:, c], col)  # f_l + LODF[l, c] f_c, per period
        post[:, c] = 0.0
        if (np.abs(post) > limits[None, :] + tol).any():
            cand.append(c)
    return cand, len(conts) - len(cand)


def n1_oracle(
    case: Case,
    s: int,
    x_hat,
    base: SubResult,
    base_lp: ParamLP,
    lps: ContingencyLPs,
    *,
    use_screen: bool = True,
    shed_tol: float = 1e-4,
    max_cuts: int | None = None,
) -> OracleReport:
    """Algorithm 2 for one scenario.

    Parameters
    ----------
    use_screen : bool
        If ``False`` every contingency is solved (useful to measure what the
        screen saves).
    shed_tol : float
        A contingency is violated when its shed exceeds ``M_s + shed_tol``
        MWh. (The paper states the acceptance test on cost with a relative
        tolerance ``tau``; with the shed-energy form an absolute MWh
        tolerance is the natural equivalent.)
    max_cuts : int, optional
        Stop after this many cuts. ``max_cuts=1`` is the literal Algorithm 2
        ("generate cut ... and return it"); the default returns every
        violated contingency found in one pass.
    """
    x_hat = np.asarray(x_hat, dtype=float)
    report = OracleReport()
    if use_screen:
        report.candidates, report.screened_out = screen(case, s, x_hat, base, base_lp)
    else:
        report.candidates = list(case.contingency_indices())
    m_s = case.scenarios[s].n1_shed_limit
    for c in report.candidates:
        sub = lps.get(s, c).solve(x_hat)
        report.lps_solved += 1
        if sub.value > m_s + shed_tol:
            rhs = m_s - sub.value + sub.slope @ x_hat
            report.cuts.append(SecurityCut(s, c, sub.slope.copy(), float(rhs)))
            if max_cuts is not None and len(report.cuts) >= max_cuts:
                break
    return report
