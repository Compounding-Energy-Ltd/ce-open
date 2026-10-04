# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Pre-activation bounds, big-M values and pruning (CE-WP-2026-04 Sec. 2.3,
eq. 3, Lemma 2.1, Proposition 2.3).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .mlp import ReluMLP

INACTIVE, AMBIGUOUS, ACTIVE = "always-inactive", "ambiguous", "always-active"


@dataclass
class LayerBounds:
    z_lo: np.ndarray
    z_hi: np.ndarray

    @property
    def big_m(self) -> np.ndarray:
        """Smallest valid symmetric big-M per neuron: ``max(|z_lo|, |z_hi|)``."""
        return np.maximum(np.abs(self.z_lo), np.abs(self.z_hi))

    @property
    def status(self) -> np.ndarray:
        """Proposition 2.3 classification of every neuron in the layer."""
        s = np.full(self.z_lo.shape, AMBIGUOUS, dtype=object)
        s[self.z_hi <= 0.0] = INACTIVE
        s[self.z_lo >= 0.0] = ACTIVE
        return s


def interval_bounds(net: ReluMLP, x_lo, x_hi) -> list[LayerBounds]:
    """Interval-bound propagation, eq. (3).

    With ``W+ = max(W, 0)`` and ``W- = min(W, 0)``::

        z_lo = W+ a_lo + W- a_hi + b,     z_hi = W+ a_hi + W- a_lo + b,
        a_lo = max(z_lo, 0),               a_hi = max(z_hi, 0),

    starting from the input box ``[x_lo, x_hi]``. Sound by Lemma 2.1: every
    reachable pre-activation lies in ``[z_lo, z_hi]``.
    """
    a_lo, a_hi = np.asarray(x_lo, float), np.asarray(x_hi, float)
    out = []
    for W, b in zip(net.weights, net.biases):
        Wp, Wm = np.maximum(W, 0.0), np.minimum(W, 0.0)
        z_lo = Wp @ a_lo + Wm @ a_hi + b
        z_hi = Wp @ a_hi + Wm @ a_lo + b
        out.append(LayerBounds(z_lo, z_hi))
        a_lo, a_hi = np.maximum(z_lo, 0.0), np.maximum(z_hi, 0.0)
    return out


def naive_big_m(net: ReluMLP, x_lo, x_hi) -> list[float]:
    """A valid but loose layer-wide big-M, the kind of bound one writes down
    without propagating intervals: ``M_l = max_i sum_j |W_l,ij| * A_{l-1} + max_i |b_l,i|``
    with ``A_0 = max |x|`` over the box and ``A_l = M_l`` (a ReLU output never
    exceeds ``|z|``)."""
    A = float(np.max(np.maximum(np.abs(x_lo), np.abs(x_hi))))
    out = []
    for W, b in zip(net.weights, net.biases):
        M = float(np.abs(W).sum(axis=1).max() * A + np.abs(b).max())
        out.append(M)
        A = M
    return out


def pruning_report(net: ReluMLP, x_lo, x_hi) -> list[dict]:
    """One row per neuron: IBP bounds, status, IBP and naive big-M (the
    per-network table the paper recommends publishing with every surrogate)."""
    ibp = interval_bounds(net, x_lo, x_hi)
    naive = naive_big_m(net, x_lo, x_hi)
    rows = []
    for l, lb in enumerate(ibp):
        for j, (lo, hi, st, m) in enumerate(zip(lb.z_lo, lb.z_hi, lb.status, lb.big_m)):
            rows.append(
                {"layer": l + 1, "neuron": j + 1, "z_lo": float(lo), "z_hi": float(hi),
                 "status": st, "big_m_ibp": float(m), "big_m_naive": naive[l]}
            )
    return rows


def summarise(rows: list[dict]) -> list[dict]:
    """Per-layer counts of always-active / always-inactive / ambiguous neurons."""
    layers = sorted({r["layer"] for r in rows})
    out = []
    for l in layers:
        rs = [r for r in rows if r["layer"] == l]
        out.append({
            "layer": l,
            "neurons": len(rs),
            ACTIVE: sum(r["status"] == ACTIVE for r in rs),
            INACTIVE: sum(r["status"] == INACTIVE for r in rs),
            AMBIGUOUS: sum(r["status"] == AMBIGUOUS for r in rs),
            "max_big_m_ibp": max(r["big_m_ibp"] for r in rs),
            "big_m_naive": rs[0]["big_m_naive"],
        })
    return out
