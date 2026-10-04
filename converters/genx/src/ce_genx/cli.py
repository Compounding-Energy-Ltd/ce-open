# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Command line: ``ce-genx-import CASE_DIR OUT_DIR [options]`` (or ``python -m ce_genx``)."""

from __future__ import annotations

import argparse
import datetime as _dt
import sys

from . import __version__, import_case
from .bundle import REPORT_FILE, check_bundle
from .convert import ImportOptions


def _tech(text: str) -> tuple[str, str]:
    if "=" not in text:
        raise argparse.ArgumentTypeError("expected RESOURCE=TECHNOLOGY")
    name, tech = text.split("=", 1)
    return name.strip(), tech.strip()


def _instant(text: str) -> _dt.datetime:
    value = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ce-genx-import",
        description="Import a GenX case folder as a CE data-format bundle.",
    )
    p.add_argument("case_dir", help="GenX case folder (holding resources/ or Generators_data.csv)")
    p.add_argument("out_dir", help="bundle directory to write")
    p.add_argument("--csv", action="store_true", help="write CSV tables instead of Parquet")
    p.add_argument("--overwrite", action="store_true", help="replace bundle files in a non-empty OUT_DIR")
    p.add_argument("--stage", type=int, help="stage to import from a multi-stage case")
    p.add_argument("--tdr", action="store_true", help="read time series from the case's TDR_results folder")
    p.add_argument("--year", type=int, default=2030, help="investment year (default 2030)")
    p.add_argument("--currency", default="USD", help="ISO 4217 code of the case's money (default USD)")
    p.add_argument("--dollar-year", type=int, help="money year (default: the investment year)")
    p.add_argument("--network", choices=["transport", "dc"], default="transport",
                   help="transport: lines as hvdc corridors (GenX default); dc: lines with susceptance")
    p.add_argument("--unbounded-cap-mw", type=float,
                   help="build limit for resources with Max_Cap_MW = -1 (default: 2 x peak demand)")
    p.add_argument("--start", type=_instant,
                   help="start instant of a full-year case, e.g. 2019-01-01T00:00:00Z (marks it chronological)")
    p.add_argument("--tech", type=_tech, action="append", default=[], metavar="RESOURCE=TECH",
                   help="override the technology code of one resource (repeatable)")
    p.add_argument("--name", help="bundle name (default: the case folder name)")
    p.add_argument("--version", action="version", version=f"ce-genx {__version__}")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    options = ImportOptions(
        investment_year=args.year,
        currency=args.currency,
        dollar_year=args.dollar_year,
        network=args.network,
        unbounded_cap_mw=args.unbounded_cap_mw,
        start=args.start,
        technologies=dict(args.tech),
        name=args.name,
    )
    try:
        bundle = import_case(
            args.case_dir,
            args.out_dir,
            stage=args.stage,
            tdr=args.tdr,
            fmt="csv" if args.csv else "parquet",
            overwrite=args.overwrite,
            options=options,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"ce-genx-import: error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {args.out_dir} ({', '.join(sorted(bundle.tables))})")
    text = bundle.report.format()
    if text:
        print(f"mapping notes (also in {REPORT_FILE}):")
        print(text)
    problems = check_bundle(args.out_dir)
    for problem in problems:
        print(f"check: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
