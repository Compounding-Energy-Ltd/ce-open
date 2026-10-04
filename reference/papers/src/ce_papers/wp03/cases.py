# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Synthetic toy instances. All numbers are invented for illustration."""

from __future__ import annotations

import numpy as np

from .network import Case, Line, Scenario, Unit


def five_bus() -> Case:
    """A meshed 5-bus, 6-line system with 4 representative hours and 2 scenarios.

    An existing gas unit sits at B1; wind can be built at B2, solar at B4, a
    CCGT at B3 and a peaker at B5. Load sits at B3, B4 and B5. Every line can
    be reinforced. The cheapest plan without N-1 security sheds load after
    several single-line outages; the N-1 secure plan costs more because it
    must reinforce lines (or build local capacity) so that every single-line
    outage can be absorbed by corrective re-dispatch without shedding.
    """
    buses = ["B1", "B2", "B3", "B4", "B5"]
    lines = [
        Line("L12", "B1", "B2", 1000.0, 300.0, 150.0, 2.0e6),
        Line("L13", "B1", "B3", 800.0, 250.0, 150.0, 2.5e6),
        Line("L23", "B2", "B3", 1000.0, 200.0, 150.0, 2.0e6),
        Line("L24", "B2", "B4", 700.0, 200.0, 150.0, 2.0e6),
        Line("L35", "B3", "B5", 900.0, 150.0, 150.0, 1.5e6),
        Line("L45", "B4", "B5", 600.0, 120.0, 150.0, 1.5e6),
    ]
    units = [
        Unit("G1", "B1", "gas", 450.0, 0.0, 0.0, 60.0),
        Unit("W2", "B2", "wind", 0.0, 800.0, 120_000.0, 0.0),
        Unit("C3", "B3", "ccgt", 0.0, 300.0, 70_000.0, 65.0),
        Unit("S4", "B4", "solar", 0.0, 600.0, 60_000.0, 0.0),
        Unit("P5", "B5", "peaker", 0.0, 250.0, 50_000.0, 140.0),
    ]
    T = 4
    profile = np.array([0.75, 0.95, 1.00, 0.85])
    peak = np.array([0.0, 0.0, 220.0, 120.0, 180.0])
    demand = profile[:, None] * peak[None, :]

    def availability(wind, solar):
        a = np.ones((T, len(units)))
        a[:, 1] = wind
        a[:, 3] = solar
        a[:, [0, 2, 4]] = 0.95
        return a

    scenarios = [
        Scenario("windy", 0.5, demand, availability([0.85, 0.60, 0.45, 0.70], [0.0, 0.45, 0.70, 0.20])),
        Scenario("calm", 0.5, demand * 1.05, availability([0.25, 0.15, 0.10, 0.30], [0.0, 0.55, 0.80, 0.25])),
    ]
    return Case(buses, lines, units, scenarios, duration=np.full(T, 8760.0 / T), name="five_bus")


def random_case(seed: int, n_bus: int = 5, T: int = 3, S: int = 2) -> Case:
    """A random meshed toy: a ring plus one chord, an existing gas unit at
    bus 0, wind/solar candidates elsewhere and a peaker candidate at every
    load bus (which guarantees the N-1 problem is feasible)."""
    rng = np.random.default_rng(seed)
    buses = [f"B{i}" for i in range(n_bus)]
    pairs = [(i, (i + 1) % n_bus) for i in range(n_bus)]
    pairs.append((0, n_bus // 2))
    lines = [
        Line(
            f"L{i}{j}", buses[i], buses[j],
            float(rng.uniform(500, 1200)), float(rng.uniform(80, 200)),
            100.0, float(rng.uniform(1.0e6, 3.0e6)),
        )
        for i, j in pairs
    ]
    load_buses = list(range(1, n_bus))
    peak = np.zeros(n_bus)
    peak[load_buses] = rng.uniform(40, 120, size=len(load_buses))
    units = [Unit("G0", buses[0], "gas", float(peak.sum() * 1.1), 0.0, 0.0, 55.0)]
    wind_bus = int(rng.integers(1, n_bus))
    solar_bus = int(rng.integers(1, n_bus))
    units.append(Unit("W", buses[wind_bus], "wind", 0.0, 300.0, 90_000.0, 0.0))
    units.append(Unit("S", buses[solar_bus], "solar", 0.0, 200.0, 45_000.0, 0.0))
    for b in load_buses:
        units.append(Unit(f"P{b}", buses[b], "peaker", 0.0, float(1.2 * peak[b]), 50_000.0, 150.0))
    U = len(units)
    profile = rng.uniform(0.7, 1.0, size=T)
    scenarios = []
    for s in range(S):
        a = np.full((T, U), 0.95)
        a[:, 1] = rng.uniform(0.05, 0.9, size=T)
        a[:, 2] = rng.uniform(0.0, 0.8, size=T)
        scale = rng.uniform(0.95, 1.05)
        scenarios.append(Scenario(f"s{s}", 1.0 / S, scale * profile[:, None] * peak[None, :], a))
    return Case(buses, lines, units, scenarios, duration=np.full(T, 8760.0 / T), name=f"random_{seed}")
