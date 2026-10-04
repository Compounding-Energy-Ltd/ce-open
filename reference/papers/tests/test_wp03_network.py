# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
import numpy as np
import pytest

from ce_papers.wp03 import Case, Line, Scenario, Unit, five_bus, lodf, ptdf


def test_ptdf_flows_balance_injections():
    case = five_bus()
    rng = np.random.default_rng(0)
    p = rng.normal(size=case.N)
    p[case.bus_index(case.ref_bus)] -= p.sum()  # slack absorbs the imbalance
    f = case.ptdf() @ p
    # Net outflow at every bus equals its injection.
    np.testing.assert_allclose(case.incidence().T @ f, p, atol=1e-9)


def test_lodf_matches_resolving_the_outaged_network():
    case = five_bus()
    A = case.incidence()
    b = np.array([ln.susceptance for ln in case.lines])
    ref = case.bus_index(case.ref_bus)
    rng = np.random.default_rng(1)
    p = rng.normal(size=case.N)
    p[ref] -= p.sum()
    f = ptdf(A, b, ref) @ p
    L = lodf(ptdf(A, b, ref), A)
    for c in range(case.L):
        keep = [l for l in range(case.L) if l != c]
        f_out = ptdf(A[keep], b[keep], ref) @ p
        predicted = f + L[:, c] * f[c]
        np.testing.assert_allclose(predicted[keep], f_out, atol=1e-9)
        assert L[c, c] == -1.0


def test_lodf_marks_bridges():
    # A triangle plus a dangling bus: the spur line is a bridge.
    A = np.array([[1, -1, 0, 0], [0, 1, -1, 0], [-1, 0, 1, 0], [0, 0, 1, -1]], dtype=float)
    b = np.ones(4)
    L = lodf(ptdf(A, b, 0), A)
    assert np.isnan(L[:, 3]).all()
    assert not np.isnan(L[:, :3]).any()


def test_case_validation():
    good = five_bus()
    with pytest.raises(ValueError):
        Case(good.buses, good.lines, good.units,
             [Scenario("bad", 0.4, good.scenarios[0].demand, good.scenarios[0].availability)],
             good.duration)
    with pytest.raises(ValueError):
        Case(good.buses, good.lines + [Line("X", "B1", "nowhere", 1.0, 1.0)], good.units,
             good.scenarios, good.duration)
    with pytest.raises(ValueError):
        Case(good.buses, good.lines, good.units + [Unit("U", "B1", "gas", 1, 0, 0, 1)],
             good.scenarios, good.duration)
