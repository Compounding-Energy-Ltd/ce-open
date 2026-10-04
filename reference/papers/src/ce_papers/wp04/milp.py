# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Embedding a trained ReLU MLP in a MILP (CE-WP-2026-04 eqs. 1-2,
Sec. 2.3, Proposition 2.3) and the case-study optimisation (Sec. 3.4).

Every ambiguous neuron ``a = ReLU(z)`` gets one binary ``delta`` and the
four big-M inequalities of eq. (2)::

    a >= z,   a >= 0,   a <= z + M (1 - delta),   a <= M delta.

Three encodings are available so their sizes and relaxations can be compared:

``"naive"``
    every neuron binary, one loose layer-wide ``M`` (:func:`naive_big_m`).
``"ibp"``
    every neuron binary, per-neuron ``M = max(|z_lo|, |z_hi|)`` from
    interval-bound propagation.
``"ibp+prune"``
    per-neuron IBP ``M`` and Proposition 2.3: an always-inactive neuron is
    replaced by ``a = 0`` and an always-active one by ``a = z``, neither with
    a binary. Exactly equivalent to the unpruned MILP on the input box.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp

from ..solver import INF, Result, solve
from .bounds import ACTIVE, INACTIVE, interval_bounds, naive_big_m
from .mlp import ReluMLP

MODES = ("naive", "ibp", "ibp+prune")


class Model:
    """A tiny row-wise MILP builder on top of :func:`ce_papers.solver.solve`."""

    def __init__(self):
        self.lb: list[float] = []
        self.ub: list[float] = []
        self.integer: list[bool] = []
        self.cost: list[float] = []
        self._r, self._c, self._v = [], [], []
        self.row_lo: list[float] = []
        self.row_hi: list[float] = []

    @property
    def n_vars(self) -> int:
        return len(self.lb)

    @property
    def n_rows(self) -> int:
        return len(self.row_lo)

    @property
    def n_binaries(self) -> int:
        return int(sum(self.integer))

    def add_var(self, lb: float = 0.0, ub: float = INF, *, integer: bool = False, cost: float = 0.0) -> int:
        self.lb.append(float(lb))
        self.ub.append(float(ub))
        self.integer.append(bool(integer))
        self.cost.append(float(cost))
        return self.n_vars - 1

    def add_row(self, idx, coef, lo: float = -INF, hi: float = INF) -> int:
        r = self.n_rows
        for i, a in zip(idx, coef):
            if a != 0.0:
                self._r.append(r), self._c.append(int(i)), self._v.append(float(a))
        self.row_lo.append(float(lo))
        self.row_hi.append(float(hi))
        return r

    def solve(self, *, maximise: bool = False, relax: bool = False) -> Result:
        A = sp.csr_matrix((self._v, (self._r, self._c)), shape=(self.n_rows, self.n_vars))
        return solve(
            self.cost, A, self.row_lo, self.row_hi, self.lb, self.ub,
            integrality=self.integer, maximise=maximise, relax=relax,
            options={"mip_rel_gap": 1e-9},
        )


@dataclass
class Embedding:
    output: int
    """Index of the variable holding ``f_hat(x)``."""
    mode: str
    status: list[np.ndarray] = field(default_factory=list)
    big_m: list[np.ndarray] = field(default_factory=list)
    binaries: int = 0


def embed_relu(model: Model, net: ReluMLP, x: list[int], x_lo, x_hi, *, mode: str = "ibp+prune") -> Embedding:
    """Add ``y = f_hat(x)`` to ``model``.

    ``x`` are existing variable indices for the network inputs, and
    ``[x_lo, x_hi]`` must contain their feasible range (it is the box the
    bounds are propagated from).
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    ibp = interval_bounds(net, x_lo, x_hi)
    naive = naive_big_m(net, x_lo, x_hi)
    emb = Embedding(output=-1, mode=mode)

    prev = list(x)  # variable index per neuron of the previous layer (None = constant 0)
    for l, (W, b) in enumerate(zip(net.weights, net.biases)):
        status = ibp[l].status if mode == "ibp+prune" else np.full(b.size, "ambiguous", dtype=object)
        bigm = np.full(b.size, naive[l]) if mode == "naive" else ibp[l].big_m
        emb.status.append(status)
        emb.big_m.append(bigm)
        cur = []
        for j in range(b.size):
            terms = [(i, w) for i, w in zip(prev, W[j]) if i is not None]
            idx = [i for i, _ in terms]
            neg_w = [-w for _, w in terms]
            if status[j] == INACTIVE:  # Prop. 2.3: a = 0
                cur.append(None)
                continue
            if status[j] == ACTIVE:  # Prop. 2.3: a = z
                a = model.add_var(-INF, INF)
                model.add_row([a] + idx, [1.0] + neg_w, b[j], b[j])
                cur.append(a)
                continue
            M = float(bigm[j])
            a = model.add_var(0.0, INF)  # a >= 0
            d = model.add_var(0.0, 1.0, integer=True)
            emb.binaries += 1
            model.add_row([a] + idx, [1.0] + neg_w, lo=b[j])  # a >= z
            model.add_row([a, d] + idx, [1.0, M] + neg_w, hi=b[j] + M)  # a <= z + M(1 - delta)
            model.add_row([a, d], [1.0, -M], hi=0.0)  # a <= M delta
            cur.append(a)
        prev = cur

    y = model.add_var(-INF, INF)
    terms = [(i, w) for i, w in zip(prev, net.w_out) if i is not None]
    model.add_row([y] + [i for i, _ in terms], [1.0] + [-w for _, w in terms], net.b_out, net.b_out)
    emb.output = y
    return emb


@dataclass
class CurtailmentResult:
    x: np.ndarray
    """Optimal inputs ``(u, c_1, ..., c_n)``."""
    value: float
    """Surrogate prediction ``f_hat(x)`` at the optimum (or the LP bound)."""
    mode: str
    relaxed: bool
    binaries: int
    rows: int
    variables: int
    mip_gap: float


def optimise_curtailment(
    net: ReluMLP,
    *,
    wind_speed: float,
    budget: float,
    c_max: float = 0.5,
    mode: str = "ibp+prune",
    relax: bool = False,
) -> CurtailmentResult:
    """Case study of Sec. 3.4: hold the wind speed fixed and choose the
    curtailment vector ``c in [0, c_max]^n`` with ``sum(c) <= budget`` that
    maximises the surrogate's farm capacity factor.

    ``relax=True`` solves the LP relaxation of the same encoding instead
    (binaries in ``[0, 1]``), whose value is an upper bound on the MILP.
    """
    n = net.architecture[0]
    model = Model()
    x_lo = np.array([wind_speed] + [0.0] * (n - 1))
    x_hi = np.array([wind_speed] + [c_max] * (n - 1))
    x = [model.add_var(lo, hi) for lo, hi in zip(x_lo, x_hi)]
    model.add_row(x[1:], [1.0] * (n - 1), hi=budget)
    emb = embed_relu(model, net, x, x_lo, x_hi, mode=mode)
    model.cost[emb.output] = 1.0
    res = model.solve(maximise=True, relax=relax)
    if not res.ok:
        raise RuntimeError(f"surrogate MILP not solved: {res.status}")
    return CurtailmentResult(
        x=res.x[x], value=res.objective, mode=mode, relaxed=relax,
        binaries=model.n_binaries, rows=model.n_rows, variables=model.n_vars, mip_gap=res.mip_gap,
    )
