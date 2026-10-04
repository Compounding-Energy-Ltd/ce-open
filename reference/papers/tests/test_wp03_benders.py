# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
import csv

import numpy as np
import pytest

from ce_papers.wp03 import (
    ContingencyLPs,
    dispatch_lp,
    evaluate,
    five_bus,
    magnanti_wong_cut,
    max_n1_violation,
    n1_oracle,
    n1_shed_table,
    random_case,
    screen,
    solve_benders,
    solve_monolithic,
    standard_cut,
)

REL = 1e-5  # relative agreement required between decomposition and monolithic


@pytest.fixture(scope="module")
def case():
    return five_bus()


@pytest.fixture(scope="module")
def mono(case):
    return {n1: solve_monolithic(case, n1=n1) for n1 in (False, True)}


def _rel(a, b):
    return abs(a - b) / abs(b)


# -- cuts --------------------------------------------------------------------
def test_cuts_are_valid_and_tight(case):
    rng = np.random.default_rng(3)
    x_hi = case.x_upper()
    lp = dispatch_lp(case, 0)
    x_hat = rng.uniform(0, 1, case.nx) * x_hi
    sub = lp.solve(x_hat)
    core = 0.5 * x_hi
    plain = standard_cut(0, sub, x_hat)
    pareto = magnanti_wong_cut(0, lp, x_hat, sub.value, core)
    for cut in (plain, pareto):
        assert cut.value(x_hat) == pytest.approx(sub.value, rel=1e-6)  # tight at x_hat
        for _ in range(20):  # valid everywhere (Q convex, cut = supporting hyperplane)
            x = rng.uniform(0, 1, case.nx) * x_hi
            assert cut.value(x) <= lp.solve(x).value * (1 + 1e-7) + 1e-3
    # Pareto-optimal: at least as strong as the plain cut at the core point.
    assert pareto.value(core) >= plain.value(core) - 1e-6 * abs(sub.value)


# -- N-1 oracle --------------------------------------------------------------
def test_screen_is_sound(case, mono):
    """Every contingency the LODF screen clears really is secure."""
    rng = np.random.default_rng(4)
    points = [mono[True].x, mono[False].x] + [rng.uniform(0, 1, case.nx) * case.x_upper() for _ in range(4)]
    for x in points:
        table = n1_shed_table(case, x)
        for s in range(case.S):
            lp = dispatch_lp(case, s)
            cand, _ = screen(case, s, x, lp.solve(x), lp)
            cleared = [c for c in case.contingency_indices() if c not in cand]
            for c in cleared:
                assert table[s, c] <= case.scenarios[s].n1_shed_limit + 1e-6


def test_security_cuts_separate_and_are_valid(case, mono):
    x_bad = mono[False].x  # cheapest plan, ignores N-1
    lp = dispatch_lp(case, 0)
    rep = n1_oracle(case, 0, x_bad, lp.solve(x_bad), lp, ContingencyLPs(case))
    assert rep.cuts, "the unsecured optimum should violate N-1"
    for cut in rep.cuts:
        assert cut.violation(x_bad) > 0  # cuts off the trial point
        assert cut.violation(mono[True].x) <= 1e-6  # keeps the secure optimum
    literal = n1_oracle(case, 0, x_bad, lp.solve(x_bad), lp, ContingencyLPs(case), max_cuts=1)
    assert len(literal.cuts) == 1


# -- end to end ----------------------------------------------------------------
def test_n1_changes_the_answer(case, mono):
    assert mono[True].objective > mono[False].objective
    assert max_n1_violation(case, mono[False].x) > 1.0
    assert max_n1_violation(case, mono[True].x) <= 1e-6


@pytest.mark.parametrize("stabilisation", ["none", "level"])
@pytest.mark.parametrize("cut_selection", ["standard", "magnanti-wong"])
def test_benders_n1_matches_monolithic_scopf(case, mono, stabilisation, cut_selection):
    res = solve_benders(case, stabilisation=stabilisation, cut_selection=cut_selection, n1=True)
    assert res.converged
    assert _rel(res.objective, mono[True].objective) <= REL
    assert res.lower_bound <= mono[True].objective * (1 + 1e-9)
    assert max_n1_violation(case, res.x) <= 1e-3  # the returned plan is N-1 secure
    assert evaluate(case, res.x) == pytest.approx(res.objective, rel=1e-9)
    assert res.security_cuts, "N-1 should bind on this case"


@pytest.mark.parametrize("stabilisation", ["none", "level"])
def test_benders_without_n1_matches_monolithic(case, mono, stabilisation):
    res = solve_benders(case, stabilisation=stabilisation, n1=False)
    assert res.converged
    assert _rel(res.objective, mono[False].objective) <= REL


def test_level_bundle_bounds_are_monotone(case):
    res = solve_benders(case, stabilisation="level", n1=False)
    lbs = [h.lower_bound for h in res.history]
    ubs = [h.upper_bound for h in res.history]
    assert all(b >= a - 1e-6 * abs(a) for a, b in zip(lbs, lbs[1:]))
    assert all(b <= a for a, b in zip(ubs, ubs[1:]))
    assert any(h.step == "level" for h in res.history)


def test_screen_saves_lps_without_changing_the_answer(case):
    with_screen = solve_benders(case, n1=True, use_screen=True)
    without = solve_benders(case, n1=True, use_screen=False)
    assert with_screen.objective == pytest.approx(without.objective, rel=REL)
    assert with_screen.contingency_lps < without.contingency_lps


def test_integer_line_master_matches_milp(case):
    ref = solve_monolithic(case, n1=True, integer_lines=True)
    res = solve_benders(case, stabilisation="none", n1=True, integer_lines=True)
    assert res.converged
    assert _rel(res.objective, ref.objective) <= REL
    x_line = res.x[case.U:]
    np.testing.assert_allclose(x_line, np.round(x_line), atol=1e-6)
    with pytest.raises(ValueError):
        solve_benders(case, stabilisation="level", integer_lines=True)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_cases(seed):
    case = random_case(seed)
    ref = solve_monolithic(case, n1=True)
    for stabilisation in ("none", "level"):
        res = solve_benders(case, stabilisation=stabilisation, n1=True)
        assert res.converged
        assert _rel(res.objective, ref.objective) <= REL


def test_history_csv(case, tmp_path):
    res = solve_benders(case, n1=True)
    path = res.write_history_csv(tmp_path / "history.csv")
    rows = list(csv.DictReader(path.open()))
    assert len(rows) == res.iterations
    assert float(rows[-1]["gap"]) <= 1e-6
