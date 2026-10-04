# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Reference code for the Compounding Energy working-paper series.

Subpackages
-----------
``ce_papers.wp03``
    CE-WP-2026-03, "Stabilised Benders Decomposition with Embedded N-1
    Contingency Cuts": Benders master/subproblem, Magnanti-Wong cut selection,
    level-bundle stabilisation and an LODF-screened N-1 separation oracle on
    a toy DC network.
``ce_papers.wp04``
    CE-WP-2026-04, "Pruned ReLU Surrogates": a ReLU MLP trained on a toy
    Jensen wake model, interval-bound propagation, provable neuron pruning
    and the big-M MILP embedding.

Both subpackages are small, readable reference implementations meant to show
the mechanics described in the papers. They are not production solvers.
"""

__version__ = "0.1.0"
