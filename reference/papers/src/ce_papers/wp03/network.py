# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Problem data and DC network sensitivities for CE-WP-2026-03.

Notation follows Section 2 of the paper: zones (buses) ``N``, corridors
(lines) ``L``, candidate/existing generating units ``U`` (the paper indexes
generation by zone and technology, ``(n, k)``; here each unit *is* one
``(n, k)`` pair), scenarios ``S`` with probabilities ``p_s`` and operating
periods ``T`` with duration weights ``delta_t``.

The first-stage decision vector is ``x = [x_gen (U), x_line (L)]``:
``x_gen[u]`` is new MW for unit ``u`` and ``x_line[l]`` in ``[0, 1]`` is the
reinforcement level of line ``l`` (a reinforced line gains
``reinforcement * x_line[l]`` MW of rating).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class Line:
    """A transmission corridor (paper: ``l in L``)."""

    name: str
    from_bus: str
    to_bus: str
    susceptance: float
    """``B_ll`` in MW per radian (the per-unit base is folded in)."""
    rating: float
    """``f_l^max`` in MW."""
    reinforcement: float = 0.0
    """``Delta f_l``: MW of extra rating when ``x_line = 1``."""
    reinforcement_cost: float = 0.0
    """``c^line_l``: cost of full reinforcement (currency per unit ``x_line``)."""


@dataclass(frozen=True)
class Unit:
    """A generating unit at one bus with one technology (paper: ``(n, k)``)."""

    name: str
    bus: str
    tech: str
    existing: float
    """``e^exist_nk`` in MW."""
    max_build: float
    """``xbar^gen_nk`` in MW (0 for existing-only units)."""
    build_cost: float
    """``c^gen_nk`` per MW built, already annualised to the study period."""
    var_cost: float
    """``nu_k`` per MWh."""


@dataclass
class Scenario:
    """One operating scenario ``s``."""

    name: str
    probability: float
    demand: np.ndarray
    """``D_snt`` with shape ``(T, N)``, MW."""
    availability: np.ndarray
    """``a_snkt`` with shape ``(T, U)``, in ``[0, 1]``."""
    n1_shed_limit: float = 0.0
    """``M_s``: permissible post-contingency load shedding (MWh over the
    scenario, duration-weighted). The paper notes this is often zero."""


@dataclass
class Case:
    """A complete two-stage capacity-expansion instance."""

    buses: list[str]
    lines: list[Line]
    units: list[Unit]
    scenarios: list[Scenario]
    duration: np.ndarray
    """``delta_t`` with shape ``(T,)``: hours represented by each period."""
    voll: float = 6000.0
    """``nu^voll`` per MWh (the paper's calibration uses 6000)."""
    ref_bus: str | None = None
    """Angle reference bus (default: the first bus)."""
    contingencies: list[str] | None = None
    """Line names in the N-1 candidate set ``C`` (default: every line)."""
    name: str = "case"
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self):
        self.duration = np.asarray(self.duration, dtype=float)
        if self.ref_bus is None:
            self.ref_bus = self.buses[0]
        T, N, U = self.T, self.N, self.U
        for s in self.scenarios:
            s.demand = np.asarray(s.demand, dtype=float)
            s.availability = np.asarray(s.availability, dtype=float)
            if s.demand.shape != (T, N):
                raise ValueError(f"scenario {s.name}: demand must be {(T, N)}")
            if s.availability.shape != (T, U):
                raise ValueError(f"scenario {s.name}: availability must be {(T, U)}")
        total = sum(s.probability for s in self.scenarios)
        if not np.isclose(total, 1.0):
            raise ValueError(f"scenario probabilities sum to {total}, not 1")
        names = {b: i for i, b in enumerate(self.buses)}
        for ln in self.lines:
            if ln.from_bus not in names or ln.to_bus not in names:
                raise ValueError(f"line {ln.name} references an unknown bus")
        for u in self.units:
            if u.bus not in names:
                raise ValueError(f"unit {u.name} references an unknown bus")

    # -- sizes -----------------------------------------------------------
    @property
    def T(self) -> int:
        return int(self.duration.size)

    @property
    def N(self) -> int:
        return len(self.buses)

    @property
    def L(self) -> int:
        return len(self.lines)

    @property
    def U(self) -> int:
        return len(self.units)

    @property
    def S(self) -> int:
        return len(self.scenarios)

    @property
    def nx(self) -> int:
        """Length of the first-stage vector ``x = [x_gen, x_line]``."""
        return self.U + self.L

    # -- first-stage data -----------------------------------------------
    def investment_cost(self) -> np.ndarray:
        """Cost vector ``c`` over ``x`` (eq. 1 first two sums)."""
        return np.concatenate(
            [[u.build_cost for u in self.units], [ln.reinforcement_cost for ln in self.lines]]
        ).astype(float)

    def x_upper(self) -> np.ndarray:
        """Upper bounds on ``x``: ``xbar^gen`` then ``1`` for each line."""
        return np.concatenate([[u.max_build for u in self.units], np.ones(self.L)]).astype(float)

    def split_x(self, x) -> tuple[np.ndarray, np.ndarray]:
        x = np.asarray(x, dtype=float)
        return x[: self.U], x[self.U :]

    def line_limits(self, x) -> np.ndarray:
        """Post-investment ratings ``f^max + Delta f * x_line`` (MW)."""
        _, x_line = self.split_x(x)
        rating = np.array([ln.rating for ln in self.lines])
        extra = np.array([ln.reinforcement for ln in self.lines])
        return rating + extra * x_line

    def bus_index(self, bus: str) -> int:
        return self.buses.index(bus)

    def line_index(self, name: str) -> int:
        return [ln.name for ln in self.lines].index(name)

    def contingency_indices(self) -> list[int]:
        if self.contingencies is None:
            return list(range(self.L))
        return [self.line_index(n) for n in self.contingencies]

    # -- network matrices -------------------------------------------------
    def incidence(self) -> np.ndarray:
        """Incidence ``A`` (L x N): +1 where the line leaves a bus, -1 where it enters."""
        A = np.zeros((self.L, self.N))
        for l, ln in enumerate(self.lines):
            A[l, self.bus_index(ln.from_bus)] = 1.0
            A[l, self.bus_index(ln.to_bus)] = -1.0
        return A

    def ptdf(self) -> np.ndarray:
        """Power transfer distribution factors (L x N), slack at ``ref_bus``.

        ``ptdf[l, n]`` is the MW flow on line ``l`` per MW injected at bus
        ``n`` and withdrawn at the reference bus.
        """
        if "ptdf" not in self._cache:
            self._cache["ptdf"] = ptdf(self.incidence(), self._susceptance(), self.bus_index(self.ref_bus))
        return self._cache["ptdf"]

    def lodf(self) -> np.ndarray:
        """Line outage distribution factors (L x L); see :func:`lodf`."""
        if "lodf" not in self._cache:
            self._cache["lodf"] = lodf(self.ptdf(), self.incidence())
        return self._cache["lodf"]

    def _susceptance(self) -> np.ndarray:
        return np.array([ln.susceptance for ln in self.lines], dtype=float)


def ptdf(A: np.ndarray, b: np.ndarray, ref: int) -> np.ndarray:
    """PTDF matrix ``diag(b) A X`` where ``X`` is the reduced inverse of
    the bus susceptance matrix ``A' diag(b) A`` with the reference bus removed.

    Assumes the network is connected.
    """
    L, N = A.shape
    Bbus = A.T @ (b[:, None] * A)
    keep = [n for n in range(N) if n != ref]
    X = np.zeros((N, N))
    X[np.ix_(keep, keep)] = np.linalg.inv(Bbus[np.ix_(keep, keep)])
    return (b[:, None] * A) @ X


def lodf(ptdf_matrix: np.ndarray, A: np.ndarray, tol: float = 1e-9) -> np.ndarray:
    """Line outage distribution factors.

    After line ``c`` trips, the DC flow on line ``l != c`` becomes
    ``f_l + lodf[l, c] * f_c`` (and ``f_c`` becomes 0). This is the factor
    the paper writes as ``Phi_lc`` in the pre-screen of Algorithm 2 (step 2).
    Columns of outages that island part of the network (bridges, where the
    factor is undefined) are set to ``NaN``; ``lodf[c, c] = -1``.
    """
    # Sensitivity of each line's flow to a 1 MW transfer from c.from to c.to.
    transfer = ptdf_matrix @ A.T  # (L, L): column c is the flow pattern of that transfer
    L = A.shape[0]
    out = np.full((L, L), np.nan)
    for c in range(L):
        denom = 1.0 - transfer[c, c]
        if abs(denom) < tol:
            continue  # bridge: outage islands the network
        out[:, c] = transfer[:, c] / denom
        out[c, c] = -1.0
    return out
