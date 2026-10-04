# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""CE-WP-2026-04 on a toy: a pruned ReLU surrogate of a Jensen wake model,
embedded in a MILP.

Stages (the paper's Appendix B): data -> train -> prune -> optimise -> report.
Artefacts are written as TOML + CSV:

    python examples/wp04_relu_wake.py --out wp04_out
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from ce_papers.wp04 import (
    ACTIVE,
    AMBIGUOUS,
    INACTIVE,
    MODES,
    WakeRow,
    error_report,
    optimise_curtailment,
    pruning_report,
    save_network,
    summarise,
    train_mlp,
    write_rows_csv,
)

SEED = 20260629


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="wp04_out", help="directory for the artefacts")
    ap.add_argument("--samples", type=int, default=4000)
    ap.add_argument("--epochs", type=int, default=800)
    ap.add_argument("--wind-speed", type=float, default=13.0)
    ap.add_argument("--budget", type=float, default=0.3, help="max total curtailment, sum of c_j")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # 1. data
    farm = WakeRow()
    X, y = farm.sample(args.samples, np.random.default_rng(SEED))
    print(f"data   : {args.samples} samples of {farm.input_names}, CF in [{y.min():.3f}, {y.max():.3f}]")

    # 2. train
    t = time.perf_counter()
    net, rep = train_mlp(X, y, hidden=(16, 8), epochs=args.epochs, lr=0.01, seed=SEED)
    print(f"train  : architecture {net.architecture}, {args.epochs} epochs in {time.perf_counter() - t:.1f} s; "
          f"train MSE {rep.train_mse:.2e}, validation MSE {rep.val_mse:.2e}, R^2 {rep.val_r2:.4f}")
    write_rows_csv(
        [{"epoch": e, "train_mse": a, "val_mse": b} for e, a, b in rep.history], out / "training_history.csv"
    )

    # 3. prune -- on the optimisation's input box (wind speed fixed).
    lo = np.array([args.wind_speed] + [0.0] * farm.n_turbines)
    hi = np.array([args.wind_speed] + [farm.c_max] * farm.n_turbines)
    rows = pruning_report(net, lo, hi)
    write_rows_csv(rows, out / "pruning.csv")
    print("prune  : layer  neurons  active  inactive  ambiguous   max M (IBP)   M (naive)")
    for s in summarise(rows):
        print(f"         {s['layer']:5d}  {s['neurons']:7d}  {s[ACTIVE]:6d}  {s[INACTIVE]:8d}  {s[AMBIGUOUS]:9d}"
              f"   {s['max_big_m_ibp']:11.2f}   {s['big_m_naive']:9.2f}")
    save_network(
        net, out / "surrogate", input_names=farm.input_names, x_lo=farm.box()[0], x_hi=farm.box()[1],
        metadata={"seed": SEED, "samples": args.samples, "epochs": args.epochs,
                  "val_mse": rep.val_mse, "val_r2": rep.val_r2, "oracle": "Jensen wake, 3-turbine row"},
    )

    # 4. optimise -- MILP and its LP relaxation for each encoding.
    print(f"optim  : max f_hat(u={args.wind_speed}, c) s.t. sum(c) <= {args.budget}, c in [0, {farm.c_max}]")
    print("         encoding    binaries  rows   MILP optimum   LP relaxation")
    results = []
    for mode in MODES:
        milp = optimise_curtailment(net, wind_speed=args.wind_speed, budget=args.budget, mode=mode)
        lp = optimise_curtailment(net, wind_speed=args.wind_speed, budget=args.budget, mode=mode, relax=True)
        results.append({"mode": mode, "binaries": milp.binaries, "rows": milp.rows,
                        "milp_value": milp.value, "lp_relaxation": lp.value,
                        **{f"c_{j + 1}": v for j, v in enumerate(milp.x[1:])}})
        print(f"         {mode:10s}  {milp.binaries:8d}  {milp.rows:4d}   {milp.value:12.4f}   {lp.value:13.4f}")
    write_rows_csv(results, out / "optimisation.csv")
    best = optimise_curtailment(net, wind_speed=args.wind_speed, budget=args.budget)
    print(f"         curtailment c = {np.round(best.x[1:], 3)}")

    # 5. report -- eq. (4).
    er = error_report(farm, net, rep, best, budget=args.budget)
    write_rows_csv([{
        "val_mse": er.val_mse, "val_r2": er.val_r2, "sup_error_sampled": er.sup_error,
        "milp_gap": er.milp_gap, "bound_2sup_plus_gap": er.bound, "regret_sampled": er.regret,
        "f_true_at_xhat": er.f_true_at_xhat, "f_true_best_sampled": er.f_true_best,
    }], out / "error_report.csv")
    print(f"report : true CF at the surrogate optimum {er.f_true_at_xhat:.4f}; best sampled true CF {er.f_true_best:.4f}")
    print(f"         regret {er.regret:.4f} <= 2 sup|f*-f_hat| + gap = 2 x {er.sup_error:.4f} + {er.milp_gap:.1e}"
          f" = {er.bound:.4f}  ({'holds' if er.within_bound else 'VIOLATED'})")
    print(f"\nartefacts written to {out}/")


if __name__ == "__main__":
    main()
