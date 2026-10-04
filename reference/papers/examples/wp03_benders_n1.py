# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""CE-WP-2026-03 on a toy: stabilised Benders with lazy N-1 security cuts.

Runs the four algorithmic variants of the paper's correctness check
(naive / level-bundle, each with plain and Magnanti-Wong cuts) with the
LODF-screened N-1 oracle, plus the monolithic SCOPF reference, and writes one
iteration log per variant as CSV.

    python examples/wp03_benders_n1.py --out wp03_out
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from ce_papers.wp03 import five_bus, max_n1_violation, random_case, solve_benders, solve_monolithic


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="wp03_out", help="directory for the CSV iteration logs")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    case = five_bus()
    print(f"case {case.name}: {case.N} buses, {case.L} lines, {case.U} units, "
          f"{case.S} scenarios x {case.T} periods, |C| = {len(case.contingency_indices())}")

    t = time.perf_counter()
    plain = solve_monolithic(case, n1=False)
    scopf = solve_monolithic(case, n1=True)
    print(f"\nmonolithic, no N-1      : {plain.objective:,.0f}  "
          f"(worst post-contingency shed {max_n1_violation(case, plain.x):,.0f} MWh)")
    print(f"monolithic SCOPF (eq. 3): {scopf.objective:,.0f}  "
          f"[{scopf.n_variables} variables, {scopf.n_rows} rows, {time.perf_counter() - t:.2f} s]")

    print("\nvariant                            iters  objective        rel.err   cont.LPs  sec.cuts")
    for stab in ("none", "level"):
        for cuts in ("standard", "magnanti-wong"):
            res = solve_benders(case, stabilisation=stab, cut_selection=cuts, n1=True)
            err = abs(res.objective - scopf.objective) / abs(scopf.objective)
            name = f"{stab} + {cuts} + N-1 oracle"
            print(f"{name:34s} {res.iterations:5d}  {res.objective:15,.0f}  {err:8.1e}  "
                  f"{res.contingency_lps:8d}  {len(res.security_cuts):8d}")
            res.write_history_csv(out / f"history_{stab}_{cuts}.csv")

    full = solve_benders(case, n1=True, use_screen=False)
    screened = solve_benders(case, n1=True, use_screen=True)
    print(f"\nLODF screen: {screened.contingency_lps} post-contingency LPs instead of "
          f"{full.contingency_lps} with no screen (same optimum).")

    x_gen, x_line = case.split_x(scopf.x)
    print("\nN-1 secure plan:")
    for u, v in zip(case.units, x_gen):
        if v > 1e-6:
            print(f"  build {v:7.1f} MW of {u.tech} at {u.bus}")
    for ln, v in zip(case.lines, x_line):
        if v > 1e-6:
            print(f"  reinforce {ln.name} by {v * ln.reinforcement:6.1f} MW")

    # Without N-1 (so the level bundle actually takes steps) on a larger toy.
    big = random_case(1, n_bus=12, T=8, S=4)
    ref = solve_monolithic(big, n1=False)
    print(f"\n{big.name} (12 buses, no N-1), reference {ref.objective:,.0f}:")
    for stab in ("none", "level"):
        res = solve_benders(big, stabilisation=stab, n1=False)
        steps = sum(h.step == "level" for h in res.history)
        print(f"  {stab:5s}: {res.iterations:3d} iterations ({steps} level steps), "
              f"rel.err {abs(res.objective - ref.objective) / abs(ref.objective):.1e}")
    print(f"\niteration logs written to {out}/")


if __name__ == "__main__":
    main()
