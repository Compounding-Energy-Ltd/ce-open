# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
import numpy as np
import pytest

from ce_papers.solver import INF, solve, solve_split


def test_lp_duals_are_objective_sensitivities():
    # min x0 + 2 x1  s.t.  x0 + x1 >= 3,  x0 <= 2
    c = [1.0, 2.0]
    A = np.array([[1.0, 1.0], [1.0, 0.0]])
    res = solve(c, A, [3.0, -INF], [INF, 2.0])
    assert res.ok
    np.testing.assert_allclose(res.x, [2.0, 1.0])
    # Perturb each active bound and compare with the reported dual.
    for i, (lo, hi) in enumerate([([3.5, -INF], [INF, 2.0]), ([3.0, -INF], [INF, 2.5])]):
        bumped = solve(c, A, lo, hi)
        assert (bumped.objective - res.objective) / 0.5 == pytest.approx(res.row_dual[i])


def test_solve_split_matches_signs():
    # min -x0 - x1  s.t.  x0 + 2 x1 <= 4,  x0 - x1 = 1,  0 <= x <= 3
    res, d_ub, d_eq = solve_split([-1.0, -1.0], [[1.0, 2.0]], [4.0], [[1.0, -1.0]], [1.0], 0.0, 3.0)
    assert res.ok
    np.testing.assert_allclose(res.x, [2.0, 1.0], atol=1e-9)
    assert d_ub[0] <= 0.0  # relaxing a binding <= row cannot increase a minimum
    bumped, _, _ = solve_split([-1.0, -1.0], [[1.0, 2.0]], [4.1], [[1.0, -1.0]], [1.0], 0.0, 3.0)
    assert (bumped.objective - res.objective) / 0.1 == pytest.approx(d_ub[0])


def test_diagonal_qp_projection():
    # Project (2, 2) onto x0 + x1 <= 2: the answer is (1, 1).
    res = solve([-2.0, -2.0], [[1.0, 1.0]], [-INF], [2.0], -INF, INF, hessian_diag=[1.0, 1.0])
    assert res.ok
    np.testing.assert_allclose(res.x, [1.0, 1.0], atol=1e-6)


def test_milp_and_relaxation():
    # max x0 + x1  s.t.  2 x0 + 2 x1 <= 3, x binary
    kw = dict(lb=0.0, ub=1.0, integrality=[True, True], maximise=True)
    milp = solve([1.0, 1.0], [[2.0, 2.0]], [-INF], [3.0], **kw)
    relax = solve([1.0, 1.0], [[2.0, 2.0]], [-INF], [3.0], relax=True, **kw)
    assert milp.objective == pytest.approx(1.0)
    assert relax.objective == pytest.approx(1.5)


def test_mixed_integer_qp_is_refused():
    with pytest.raises(ValueError):
        solve([0.0], None, None, None, 0.0, 1.0, integrality=[True], hessian_diag=[1.0])
