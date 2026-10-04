# White-paper reference code (`ce-papers`)

Small, standalone reference implementations of the methods in two
Compounding Energy working papers:

| Paper | Title | Subpackage |
|-------|-------|------------|
| CE-WP-2026-03 | *Stabilised Benders Decomposition with Embedded N-1 Contingency Cuts* | `ce_papers.wp03` |
| CE-WP-2026-04 | *Pruned ReLU Surrogates: embedding non-linear physics into MILP-based energy system optimisation with explicit error bounds* | `ce_papers.wp04` |

## Citing the papers

Both papers are in the Compounding Energy Working Paper Series, released
under CC BY 4.0, with the latest versions at
<https://compoundingenergy.com/papers>:

* Clack, C. T. M. (2026). *Stabilised Benders Decomposition with Embedded N-1
  Contingency Cuts: a scalable framework for security-constrained stochastic
  capacity expansion.* Compounding Energy Working Paper CE-WP-2026-03,
  Compounding Energy Ltd, June 2026.
* Clack, C. T. M. (2026). *Pruned ReLU Surrogates: embedding non-linear
  physics into MILP-based energy system optimisation with explicit error
  bounds.* Compounding Energy Working Paper CE-WP-2026-04, Compounding Energy
  Ltd, June 2026.

Equation, algorithm and section numbers below refer to those versions.

The code runs on toy systems (a five-bus network, a three-turbine wind farm)
in a few seconds. It is written to be read next to the papers: every function
below names the equation, algorithm or section it implements. It is **not**
the production engine, and it does not reproduce the papers' own case-study
numbers.

## Install and run

Python 3.11 or newer. Dependencies: NumPy, SciPy (sparse matrices) and
[HiGHS](https://highs.dev) through `highspy`, which solves the LPs, the
level-bundle QP and the MILPs.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
pytest                                   # 38 tests, a few seconds
python examples/wp03_benders_n1.py       # writes wp03_out/*.csv
python examples/wp04_relu_wake.py        # writes wp04_out/*.csv and a surrogate/ directory
```

Without installing, `PYTHONPATH=src` works too.

All data is synthetic. Outputs are CSV, surrogate metadata is TOML; nothing
is written as JSON.

---

## CE-WP-2026-03: stabilised Benders with N-1 contingency cuts

Two-stage capacity expansion: first-stage investment
`x = [x_gen, x_line]` (new MW per unit, reinforcement level per line in
`[0, 1]`), second-stage DC-network dispatch per scenario, and N-1 security
(every single-line outage must be survivable by corrective re-dispatch with
shed energy at most `M_s`).

### Where each piece of the paper lives

| Paper | Code |
|-------|------|
| Sec. 2 notation: zones, corridors, technologies, scenarios, periods, incidence `A`, susceptance `B` | `wp03.network.Case`, `Line`, `Unit`, `Scenario`; `Case.incidence()` |
| Eq. (1), deterministic equivalent | `wp03.monolithic.solve_monolithic(case, n1=False)` |
| Eq. (1) + all of eq. (3) embedded: the "SCOPF master" | `solve_monolithic(case, n1=True)` (ground truth for the tests) |
| Eq. (2), per-scenario dispatch `Q_s(x)` | `wp03.dispatch.dispatch_lp(case, s, objective="cost")` |
| Eq. (3), post-contingency problem `Q_s^c(x) <= M_s` | `dispatch_lp(case, s, outage=c, objective="shed")`; `Scenario.n1_shed_limit` is `M_s` |
| Eq. (4), relaxed multi-cut master, one `theta_s` per scenario | `wp03.benders.Master.solve_lower` |
| Eq. (5), cut coefficients from capacity and line-limit duals | `ParamLP.solve` returns `SubResult.slope = B_ub' lam`; `wp03.cuts.standard_cut` |
| Algorithm 1, naive Benders | `wp03.benders.solve_benders(case, stabilisation="none")` |
| Theorem 3.1, integer master (binary `x_line`) | `solve_benders(..., stabilisation="none", integer_lines=True)` |
| Sec. 4.1, Magnanti-Wong Pareto-optimal cuts (second LP over the dual polyhedron, core point `x^0`) | `wp03.cuts.magnanti_wong_cut`; `solve_benders(..., cut_selection="magnanti-wong", core_point=...)` |
| Eq. (6), level-bundle master; `L_k = LB_k + kappa (UB_k - LB_k)` | `Master.solve_level`; `solve_benders(..., stabilisation="level", kappa=0.6)` |
| Remark 4.2, `kappa = 0.6` | default of `kappa` |
| Stability-centre update on sufficient descent | `solve_benders(..., descent=0.1)` |
| Algorithm 2, N-1 separation oracle | `wp03.contingency.n1_oracle` |
| Algorithm 2 steps 2-3, pre-screen `f_hat_l^c = f*_l + Phi_lc f*_c` | `wp03.contingency.screen`; `Case.ptdf()`, `Case.lodf()` |
| Algorithm 2 acceptance tolerance `tau` | `shed_tol` (see the mapping notes) |
| Theorem 5.1, the oracle converges to the SCOPF optimum with an N-1 feasible `x` | `tests/test_wp03_benders.py::test_benders_n1_matches_monolithic_scopf` |
| Convergence test `(UB_k - LB_k) / |UB_k| < eps` | `solve_benders(..., tol=1e-6)`; `BendersResult.history` |
| The paper's correctness check (all variants reach the same optimum) | `examples/wp03_benders_n1.py`, parametrised tests |

The four components are independent switches, as the paper describes:

```python
from ce_papers.wp03 import five_bus, solve_benders, solve_monolithic

case = five_bus()
res = solve_benders(case, stabilisation="level", cut_selection="magnanti-wong", n1=True)
ref = solve_monolithic(case, n1=True)          # every (s, c) block embedded
print(res.objective, ref.objective, res.iterations, res.contingency_lps)
res.write_history_csv("history.csv")           # LB, UB, gap, cuts, screen counts per iteration
```

### Example output

`python examples/wp03_benders_n1.py` (numbers may differ in the last digits
across platforms and HiGHS versions):

```
monolithic, no N-1      : 132,113,450  (worst post-contingency shed 356,872 MWh)
monolithic SCOPF (eq. 3): 134,874,856  [1187 variables, 1436 rows, 0.03 s]

variant                            iters  objective        rel.err   cont.LPs  sec.cuts
none + standard + N-1 oracle           9      134,874,856   2.2e-16        91        48
none + magnanti-wong + N-1 oracle      9      134,874,857   1.1e-08        91        48
level + standard + N-1 oracle          9      134,874,856   2.2e-16        91        48
level + magnanti-wong + N-1 oracle     9      134,874,857   1.1e-08        91        48

LODF screen: 91 post-contingency LPs instead of 108 with no screen (same optimum).
```

### Mapping notes and limitations

* **Shed-energy form of `Q_s^c`.** The paper defines `M_s` as the
  permissible post-contingency load shedding. We make that literal: the
  post-contingency LP minimises duration-weighted shed energy (MWh) with
  line `c` removed, and N-1 reads `Q_s^c(x) <= M_s`. The oracle's acceptance
  test is therefore "shed > `M_s` + `shed_tol`" (absolute MWh) rather than
  the paper's cost test "post-contingency cost > `Q_s (1 + tau)`".
* **Corrective re-dispatch is unrestricted.** After an outage every unit can
  move anywhere within its available capacity; there is no ramp coupling to
  the base dispatch and no preventive constraint. Security then depends on
  `x` only, which keeps the security cut a pure constraint on `x`.
* **`Phi_lc` is the LODF.** The paper's pre-screen factor is the line
  outage distribution factor, computed here from the PTDF matrix. The screen
  clears a contingency only when the base dispatch sheds at most `M_s` and its
  exact post-outage DC flows respect every rating, so a cleared contingency is
  provably secure (tested). Outages that island the network are always
  solved. Reinforcement changes ratings, not susceptances, so one PTDF matrix
  serves every iteration.
* **Bounds.** `LB_k` is recomputed from the Kelley master every iteration;
  `UB_k` only counts trial points the oracle accepts, so the returned plan is
  N-1 secure. `theta_s >= 0` bounds the first master (dispatch costs are
  non-negative).
* **Level-bundle QP.** Solved with HiGHS in scaled coordinates
  (`x / xbar`, objective divided by a fixed scale, cut rows normalised), i.e.
  a diagonal metric in the original units. The paper's `rho` only scales the
  objective and does not change the projection. If the QP fails the
  iteration falls back to the Kelley point, which preserves convergence.
* **Integer lines** are supported with naive Benders only (HiGHS has no
  mixed-integer QP); the level bundle uses `x_line` in `[0, 1]`, as eq. (6)
  does.
* **Oracle output.** By default every violated contingency found in one
  pass becomes a cut; `max_security_cuts=1` reproduces the literal
  Algorithm 2 (return the first cut).
* **Scope.** Two-stage, DC power flow, no storage or other inter-temporal
  coupling. Not implemented: SDDP / multi-stage trees, N-k, SOCP AC
  relaxations, parallel scenario solves.
* **Speed is not the point of the toy.** With a handful of investment
  variables naive Benders is often as fast as, or faster than, the level
  bundle (on `five_bus` with N-1 both stop at the first secure point); the
  stabilisation's value is its worst-case rate (Theorem 4.1) on larger,
  degenerate masters. The LODF screen saves little on a six-line system where
  most lines run hot. The paper's 30-zone case, iteration counts and
  speed-ups are not reproduced.

---

## CE-WP-2026-04: pruned ReLU surrogates in MILPs

A toy non-linear oracle (a row of three turbines with Jensen wakes and
per-turbine derating) is sampled, fitted with a ReLU MLP, bounded with
interval-bound propagation, pruned, and embedded as a MILP whose optimum
chooses the derating that maximises the predicted farm capacity factor.

### Where each piece of the paper lives

| Paper | Code |
|-------|------|
| Eq. (1), ReLU MLP forward pass, architecture `(n, h_1, ..., h_L, 1)` | `wp04.mlp.ReluMLP.__call__`, `preactivations`, `architecture` |
| Sec. 3.2, training: uniform samples, He init, mini-batch SGD, 80/20 split, lr 0.01, 800 epochs, 16-8 hidden | `wp04.oracle.WakeRow.sample`, `wp04.mlp.train_mlp` |
| Eq. (2), big-M encoding, one binary per ReLU | `wp04.milp.embed_relu` |
| Sec. 2.3, tight per-neuron `M = max(|l_z|, |u_z|)` | `wp04.bounds.LayerBounds.big_m`; `embed_relu(..., mode="ibp")` |
| A naive, layer-wide `M` for comparison | `wp04.bounds.naive_big_m`; `mode="naive"` |
| Eq. (3), interval-bound propagation | `wp04.bounds.interval_bounds` |
| Lemma 2.1, soundness of IBP | `tests/test_wp04_relu.py::test_ibp_is_sound` |
| Proposition 2.3, pruning (`a = 0` / `a = z`, no binary) | `LayerBounds.status`; `embed_relu(..., mode="ibp+prune")` |
| Proposition 2.3, exact equivalence of the pruned MILP | `test_pruned_milp_is_exactly_equivalent` |
| The per-network pruning table | `wp04.bounds.pruning_report`, `summarise` |
| Eq. (4), error decomposition (surrogate error, MILP gap, simulator uncertainty) | `wp04.report.error_report` |
| Eq. (5), Jensen wake deficit with quadratic-sum superposition | `wp04.oracle.WakeRow.capacity_factor` |
| Sec. 3.4, case-study optimisation (fixed wind, curtailment budget) | `wp04.milp.optimise_curtailment` |
| Sec. 4.1, surrogate inside a larger optimisation model | `embed_relu` on any `Model`; `test_embedding_two_periods_with_a_shared_budget` |
| Appendix B, stages data / train / prune / optimise / report | `examples/wp04_relu_wake.py` |
| Surrogate artefacts (weights, bounds, pruning status) stored, not recomputed | `wp04.io.save_network` / `load_network` (TOML + CSV) |

```python
import numpy as np
from ce_papers.wp04 import WakeRow, train_mlp, optimise_curtailment, error_report

farm = WakeRow()
X, y = farm.sample(4000, np.random.default_rng(20260629))
net, rep = train_mlp(X, y)                                   # 4-16-8-1
best = optimise_curtailment(net, wind_speed=13.0, budget=0.3, mode="ibp+prune")
lp = optimise_curtailment(net, wind_speed=13.0, budget=0.3, mode="ibp+prune", relax=True)
report = error_report(farm, net, rep, best, budget=0.3)      # eq. (4)
```

### Example output

`python examples/wp04_relu_wake.py`:

```
train  : architecture (4, 16, 8, 1), 800 epochs in 3.0 s; train MSE 2.07e-04, validation MSE 2.04e-04, R^2 0.9986
prune  : layer  neurons  active  inactive  ambiguous   max M (IBP)   M (naive)
             1       16       4         5          7          2.95      112.97
             2        8       0         0          8          3.35      615.02
optim  : max f_hat(u=13.0, c) s.t. sum(c) <= 0.3, c in [0, 0.5]
         encoding    binaries  rows   MILP optimum   LP relaxation
         naive             24    74         0.9918        674.2654
         ibp               24    74         0.9918          2.8715
         ibp+prune         15    51         0.9918          2.6517
report : true CF at the surrogate optimum 0.9796; best sampled true CF 0.9988
         regret 0.0192 <= 2 sup|f*-f_hat| + gap = 2 x 0.0327 + 0.0e+00 = 0.0654  (holds)
```

All three encodings reach the same MILP optimum (Proposition 2.3); IBP cuts
`M` by one to two orders of magnitude, and pruning removes 9 of 24 binaries
and tightens the LP relaxation further.

### Mapping notes and limitations

* **Toy oracle.** The paper's case is a 3 x 3 farm with wind speed,
  direction and nine curtailments (12 inputs). Here: a three-turbine row
  aligned with the wind (4 inputs: speed and three derating fractions). The
  derating model (`C_T = C_T0 (1 - c)`, output `min(1, (1 - c) A(u))`) is an
  illustrative assumption, not a validated turbine model.
* **Symmetric big-M**, as in the paper. Using the two sides `-z_lo` and
  `z_hi` separately would be tighter; not implemented. IBP only: no CROWN or
  other linear-bound propagation.
* **Training** uses SGD with momentum 0.9 (the paper states mini-batch SGD);
  numbers differ from the paper's case study and are not meant to match it.
* **Pruning box.** Bounds are propagated over the optimisation's input box
  (wind speed fixed). Over the whole training box few neurons are stable;
  fixing inputs is what makes pruning pay.
* **Eq. (4) is evaluated empirically.** The supremum `sup |f* - f_hat|` and
  the true optimum are estimated by dense sampling of the feasible set, so
  the report illustrates the bound rather than certifying it.
* **Surrogate optimum vs true optimum.** The MILP finds the exact optimum of
  the surrogate; that point is near, not at, the oracle's optimum because the
  optimiser exploits surrogate error. That gap is exactly what eq. (4) bounds.
* Not implemented: uncertainty quantification at data collection (conformal
  prediction, ensembles), deeper networks for discontinuous oracles.

---

## Layout

```
src/ce_papers/solver.py      HiGHS wrapper (LP, diagonal QP, MILP; duals as objective sensitivities)
src/ce_papers/wp03/          network.py  dispatch.py  cuts.py  contingency.py  benders.py  monolithic.py  cases.py
src/ce_papers/wp04/          oracle.py  mlp.py  bounds.py  milp.py  report.py  io.py
tests/                       pytest suite
examples/                    one runnable script per paper
```

## Licence

Apache License 2.0 — see `LICENSE` and `NOTICE` (shipped with this package).
Copyright 2026 Compounding Energy Ltd.
