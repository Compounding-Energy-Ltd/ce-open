# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
import csv

import numpy as np
import pytest

from ce_papers.wp04 import (
    ACTIVE,
    AMBIGUOUS,
    INACTIVE,
    MODES,
    Model,
    ReluMLP,
    WakeRow,
    embed_relu,
    error_report,
    interval_bounds,
    load_network,
    naive_big_m,
    optimise_curtailment,
    pruning_report,
    save_network,
    summarise,
    train_mlp,
    write_rows_csv,
)

WIND, BUDGET = 13.0, 0.3


@pytest.fixture(scope="module")
def farm():
    return WakeRow()


@pytest.fixture(scope="module")
def trained(farm):
    X, y = farm.sample(2000, np.random.default_rng(7))
    return train_mlp(X, y, epochs=150, seed=7)


def _case_box(farm):
    lo = np.array([WIND] + [0.0] * farm.n_turbines)
    hi = np.array([WIND] + [farm.c_max] * farm.n_turbines)
    return lo, hi


# -- oracle -----------------------------------------------------------------
def test_oracle_physics(farm):
    X, y = farm.sample(500, np.random.default_rng(0))
    assert y.min() >= 0.0 and y.max() <= 1.0
    # A single turbine sees no wake.
    solo = WakeRow(n_turbines=1)
    assert solo.capacity_factor([[9.0, 0.0]])[0] == pytest.approx(float(solo.available(9.0)))
    # Above rated, derating the front turbine is free for it and helps the others ...
    assert farm.capacity_factor([[13.0, 0.3, 0.0, 0.0]])[0] > farm.capacity_factor([[13.0, 0.0, 0.0, 0.0]])[0]
    # ... below rated it only costs energy.
    assert farm.capacity_factor([[8.0, 0.3, 0.0, 0.0]])[0] < farm.capacity_factor([[8.0, 0.0, 0.0, 0.0]])[0]


# -- network ------------------------------------------------------------------
def test_forward_pass_matches_eq1():
    net = ReluMLP([np.array([[1.0, -1.0], [0.5, 2.0]])], [np.array([0.0, -1.0])], np.array([2.0, -1.0]), 0.5)
    x = np.array([[1.0, 3.0]])
    z = np.array([1.0 - 3.0, 0.5 + 6.0 - 1.0])
    assert net(x)[0] == pytest.approx(2.0 * max(z[0], 0) - 1.0 * max(z[1], 0) + 0.5)
    assert net.architecture == (2, 2, 1)


def test_training_fits_the_oracle(trained):
    net, rep = trained
    assert rep.val_r2 > 0.95
    assert rep.history[-1][1] < rep.history[0][1] or len(rep.history) == 1
    assert net.architecture == (4, 16, 8, 1)


# -- bounds and pruning ---------------------------------------------------------
@pytest.mark.parametrize("box", ["full", "case"])
def test_ibp_is_sound(farm, trained, box):
    """Lemma 2.1: sampled pre-activations never leave the IBP intervals."""
    net, _ = trained
    lo, hi = farm.box() if box == "full" else _case_box(farm)
    X = lo + (hi - lo) * np.random.default_rng(1).random((5000, lo.size))
    bounds = interval_bounds(net, lo, hi)
    naive = naive_big_m(net, lo, hi)
    for z, b, m in zip(net.preactivations(X), bounds, naive):
        assert (z >= b.z_lo - 1e-9).all() and (z <= b.z_hi + 1e-9).all()
        assert (b.big_m <= m + 1e-9).all()  # IBP is never looser than the naive M


def test_pruning_classification(farm, trained):
    net, _ = trained
    lo, hi = _case_box(farm)
    rows = pruning_report(net, lo, hi)
    assert len(rows) == 16 + 8
    for r in rows:
        if r["status"] == INACTIVE:
            assert r["z_hi"] <= 0
        elif r["status"] == ACTIVE:
            assert r["z_lo"] >= 0
        else:
            assert r["status"] == AMBIGUOUS and r["z_lo"] < 0 < r["z_hi"]
    summary = summarise(rows)
    pruned = sum(s[ACTIVE] + s[INACTIVE] for s in summary)
    assert pruned > 0, "fixing the wind speed should make some neurons stable"


# -- MILP embedding -------------------------------------------------------------
@pytest.fixture(scope="module")
def solved(trained):
    net, _ = trained
    return {
        (mode, relax): optimise_curtailment(net, wind_speed=WIND, budget=BUDGET, mode=mode, relax=relax)
        for mode in MODES
        for relax in (False, True)
    }


def test_pruned_milp_is_exactly_equivalent(solved):
    """Proposition 2.3: same optimum with or without pruning / tight M."""
    ref = solved[("naive", False)].value
    for mode in MODES:
        assert solved[(mode, False)].value == pytest.approx(ref, abs=1e-6)
    assert solved[("ibp+prune", False)].binaries < solved[("ibp", False)].binaries
    assert solved[("ibp", False)].binaries == solved[("naive", False)].binaries


def test_relaxations_tighten(solved):
    """Tighter M and pruning shrink the LP relaxation towards the MILP optimum."""
    milp = solved[("ibp+prune", False)].value
    naive, ibp, pruned = (solved[(m, True)].value for m in MODES)
    assert naive >= ibp - 1e-7 >= pruned - 2e-7
    assert pruned >= milp - 1e-7
    assert naive > 10 * ibp  # the loose M makes the naive relaxation nearly useless


def test_milp_finds_the_surrogate_optimum(farm, trained, solved):
    net, _ = trained
    res = solved[("ibp+prune", False)]
    x = res.x
    assert x[0] == pytest.approx(WIND)
    assert x[1:].sum() <= BUDGET + 1e-7 and (x[1:] >= -1e-9).all() and (x[1:] <= farm.c_max + 1e-9).all()
    assert net(x[None, :])[0] == pytest.approx(res.value, abs=1e-6)
    # No sampled feasible point beats the MILP on the surrogate.
    rng = np.random.default_rng(2)
    C = rng.random((20000, farm.n_turbines)) * farm.c_max
    C = C[C.sum(axis=1) <= BUDGET]
    X = np.column_stack([np.full(len(C), WIND), C])
    assert net(X).max() <= res.value + 1e-6


def test_embedding_two_periods_with_a_shared_budget(trained):
    """embed_relu composes: one surrogate copy per period inside a larger MILP."""
    net, _ = trained
    model = Model()
    copies = []
    xs = []
    for u in (12.0, 14.0):
        lo = np.array([u, 0.0, 0.0, 0.0])
        hi = np.array([u, 0.5, 0.5, 0.5])
        x = [model.add_var(a, b) for a, b in zip(lo, hi)]
        emb = embed_relu(model, net, x, lo, hi)
        model.cost[emb.output] = 1.0
        copies.append(emb)
        xs.append(x)
    model.add_row(xs[0][1:] + xs[1][1:], [1.0] * 6, hi=0.4)
    res = model.solve(maximise=True)
    assert res.ok
    total = 0.0
    for emb, x in zip(copies, xs):
        y = res.x[emb.output]
        assert y == pytest.approx(net(res.x[x][None, :])[0], abs=1e-6)
        total += y
    assert total == pytest.approx(res.objective, abs=1e-6)


# -- error decomposition and artefacts --------------------------------------------
def test_error_report_eq4(farm, trained, solved):
    net, rep = trained
    er = error_report(farm, net, rep, solved[("ibp+prune", False)], budget=BUDGET, n_samples=20000)
    assert er.sup_error > 0 and er.milp_gap >= 0
    assert er.regret >= -1e-12
    assert er.within_bound


def test_artefact_round_trip(farm, trained, tmp_path):
    net, rep = trained
    lo, hi = farm.box()
    save_network(net, tmp_path / "surrogate", input_names=farm.input_names, x_lo=lo, x_hi=hi,
                 metadata={"seed": 7, "val_r2": rep.val_r2, "oracle": "jensen row"})
    back, meta = load_network(tmp_path / "surrogate")
    X, _ = farm.sample(200, np.random.default_rng(3))
    np.testing.assert_array_equal(back(X), net(X))
    assert meta["network"]["input_names"] == farm.input_names
    assert meta["metadata"]["seed"] == 7
    path = write_rows_csv(pruning_report(net, lo, hi), tmp_path / "pruning.csv")
    assert len(list(csv.DictReader(path.open()))) == 24
