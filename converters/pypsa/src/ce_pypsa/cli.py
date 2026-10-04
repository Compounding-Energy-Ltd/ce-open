# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Command line: ``ce-pypsa import`` and ``ce-pypsa export``."""

from __future__ import annotations

import argparse
import sys

from .exporter import export_bundle
from .importer import import_network


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ce-pypsa", description="Convert between PyPSA networks and CE bundles.")
    sub = p.add_subparsers(dest="cmd", required=True)

    imp = sub.add_parser("import", help="PyPSA CSV folder or .nc -> CE bundle directory")
    imp.add_argument("source", help="PyPSA CSV folder, or a .nc file (needs pypsa)")
    imp.add_argument("bundle", help="output bundle directory")
    imp.add_argument("--year", type=int, default=None, help="investment year (default: first snapshot's year)")
    imp.add_argument("--currency", default="EUR", help="ISO 4217 code of the network's costs (default EUR)")
    imp.add_argument("--money-year", type=int, default=None, help="constant-money year (default: --year)")
    imp.add_argument("--voll", type=float, default=6000.0, help="value of lost load per MWh (default 6000)")
    imp.add_argument("--prm", type=float, default=0.0, help="planning reserve margin (default 0)")
    imp.add_argument("--block-hours", type=int, default=None, help="chunk snapshots into blocks of N periods")
    imp.add_argument("--inf-capacity-mw", type=float, default=1e5, help="cap for unbounded expansion")
    imp.add_argument("--format", choices=("parquet", "arrow", "csv"), default="parquet")
    imp.add_argument("--overwrite", action="store_true")

    exp = sub.add_parser("export", help="CE bundle directory -> PyPSA CSV folder (or .nc, needs pypsa)")
    exp.add_argument("bundle", help="CE bundle directory")
    exp.add_argument("out", help="output CSV folder, or a path ending in .nc")
    exp.add_argument("--year", type=int, default=None)
    exp.add_argument("--scenario", type=int, default=1)
    exp.add_argument("--weather", type=int, default=None)
    uc = exp.add_mutually_exclusive_group()
    uc.add_argument("--unit-commitment", dest="uc", action="store_const", const=True, default=None,
                    help="export every thermal unit as committable")
    uc.add_argument("--no-unit-commitment", dest="uc", action="store_const", const=False,
                    help="export a pure LP (no commitment data)")

    a = p.parse_args(argv)
    if a.cmd == "import":
        bundle = import_network(a.source, a.bundle, year=a.year, currency=a.currency, money_year=a.money_year,
                                voll=a.voll, prm=a.prm, block_hours=a.block_hours,
                                inf_capacity_mw=a.inf_capacity_mw, fmt=a.format, overwrite=a.overwrite)
        print(bundle.report)
        problems = bundle.validate()
        for msg in problems:
            print(f"validation: {msg}", file=sys.stderr)
        return 1 if problems else 0
    _, report = export_bundle(a.bundle, a.out, year=a.year, scenario=a.scenario, weather=a.weather,
                              unit_commitment=a.uc)
    print(report)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
