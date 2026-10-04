# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Reference code for CE-WP-2026-03: stabilised Benders decomposition with
embedded N-1 contingency cuts for two-stage capacity expansion.

Quick start::

    from ce_papers.wp03 import five_bus, solve_benders, solve_monolithic
    case = five_bus()
    res = solve_benders(case, stabilisation="level", n1=True)
    ref = solve_monolithic(case, n1=True)
    assert abs(res.objective - ref.objective) <= 1e-5 * abs(ref.objective)
"""

from .benders import BendersResult, Iteration, Master, solve_benders
from .cases import five_bus, random_case
from .contingency import ContingencyLPs, OracleReport, SecurityCut, n1_oracle, screen
from .cuts import Cut, magnanti_wong_cut, standard_cut
from .dispatch import ParamLP, SubResult, dispatch_lp, shed_energy
from .monolithic import MonolithicResult, evaluate, max_n1_violation, n1_shed_table, solve_monolithic
from .network import Case, Line, Scenario, Unit, lodf, ptdf

__all__ = [
    "BendersResult", "Case", "ContingencyLPs", "Cut", "Iteration", "Line", "Master",
    "MonolithicResult", "OracleReport", "ParamLP", "Scenario", "SecurityCut", "SubResult",
    "Unit", "dispatch_lp", "evaluate", "five_bus", "lodf", "magnanti_wong_cut",
    "max_n1_violation", "n1_oracle", "n1_shed_table", "ptdf", "random_case", "screen",
    "shed_energy", "solve_benders", "solve_monolithic", "standard_cut",
]
