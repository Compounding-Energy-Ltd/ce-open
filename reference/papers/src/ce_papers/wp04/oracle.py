# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""A toy non-linear oracle: a single row of wind turbines with Jensen wakes
(CE-WP-2026-04 eq. 5) and per-turbine derating.

The paper's case study uses a 3 x 3 farm with wind speed, wind direction and
nine curtailment fractions (12 inputs). This reference keeps the same physics
on a smaller, direction-aligned row so that everything runs in seconds:

inputs ``x = (u, c_1, ..., c_n)``
    ``u`` free-stream wind speed (m/s) aligned with the row, ``c_j`` the
    derating (curtailment) fraction of turbine ``j``.
output
    farm capacity factor in ``[0, 1]``.

Physics, all textbook:

* available power (in units of rating) ``A(u) = (u^3 - u_ci^3) / (u_r^3 - u_ci^3)``
  between cut-in and cut-out, zero outside; a derated turbine produces
  ``min(1, (1 - c) A(u))``;
* thrust coefficient ``C_T = C_T0 (1 - c)`` (derating sheds thrust);
* Jensen deficit from upstream ``i`` at downstream ``j``:
  ``(1 - sqrt(1 - C_T,i)) * (D / (D + 2 alpha d_ij))^2`` -- identical to the
  paper's ``(D / (2 (D/2 + alpha d)))^2`` form;
* Katic quadratic-sum superposition of the deficits.

Derating an upstream turbine above rated wind speed costs it nothing while it
still makes rated power, but it reduces the wake on the turbines behind it.
That trade-off is what the optimiser in :mod:`ce_papers.wp04.milp` exploits.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class WakeRow:
    """Parameters of the toy farm (defaults follow the paper's case study)."""

    n_turbines: int = 3
    rotor_diameter: float = 178.0
    """``D`` in metres."""
    spacing: float = 7.0
    """Turbine spacing in rotor diameters."""
    ct0: float = 0.8
    """Thrust coefficient at zero derating."""
    alpha: float = 0.075
    """Wake-decay constant (offshore)."""
    cut_in: float = 3.0
    rated_speed: float = 11.4
    cut_out: float = 25.0
    u_range: tuple[float, float] = (4.0, 18.0)
    c_max: float = 0.5

    @property
    def n_inputs(self) -> int:
        return 1 + self.n_turbines

    @property
    def input_names(self) -> list[str]:
        return ["wind_speed"] + [f"curtail_{j + 1}" for j in range(self.n_turbines)]

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        """The input domain ``X = [x_lo, x_hi]``."""
        lo = np.array([self.u_range[0]] + [0.0] * self.n_turbines)
        hi = np.array([self.u_range[1]] + [self.c_max] * self.n_turbines)
        return lo, hi

    def available(self, u):
        u = np.asarray(u, dtype=float)
        a = (u**3 - self.cut_in**3) / (self.rated_speed**3 - self.cut_in**3)
        return np.where((u >= self.cut_in) & (u < self.cut_out), np.maximum(a, 0.0), 0.0)

    def capacity_factor(self, X) -> np.ndarray:
        """Farm capacity factor for each row of ``X`` (shape ``(m, n_inputs)``)."""
        X = np.atleast_2d(np.asarray(X, dtype=float))
        u0, c = X[:, 0], X[:, 1:]
        n = self.n_turbines
        D = self.rotor_diameter
        ct = self.ct0 * (1.0 - c)
        strength = 1.0 - np.sqrt(1.0 - ct)  # (m, n)
        power = np.zeros_like(c)
        for j in range(n):
            deficit_sq = np.zeros_like(u0)
            for i in range(j):
                d = (j - i) * self.spacing * D
                deficit_sq += (strength[:, i] * (D / (D + 2.0 * self.alpha * d)) ** 2) ** 2
            u_j = u0 * (1.0 - np.sqrt(deficit_sq))
            power[:, j] = np.minimum(1.0, (1.0 - c[:, j]) * self.available(u_j))
        return power.mean(axis=1)

    def sample(self, n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
        """Uniform samples of the input box and the oracle's values (paper Sec. 3.2)."""
        lo, hi = self.box()
        X = lo + (hi - lo) * rng.random((n, self.n_inputs))
        return X, self.capacity_factor(X)
