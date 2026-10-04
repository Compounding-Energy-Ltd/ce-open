# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Command line: ``python -m ce_spec <command> ...``

Commands:
  validate-bundle DIR                       validate a bundle directory (exit 1 on any error)
  validate-results DIR [--bundle B] [--strict]
                                            validate a results directory
  arrow-schema SCOPE TABLE                  print a table's canonical Arrow schema
  render-docs                               print TABLES.md (generated column reference)
  make-toy DIR [--format parquet|csv|arrow] write the synthetic toy bundle (DIR/toy3) and results (DIR/toy3_run)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .bundle import validate_bundle
from .render import render_tables_md
from .results import validate_results
from .schema import table_schema
from .toy import write_toy_bundle, write_toy_results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ce_spec", description="CE data-format tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    vb = sub.add_parser("validate-bundle", help="validate a bundle directory")
    vb.add_argument("dir")
    vr = sub.add_parser("validate-results", help="validate a results directory")
    vr.add_argument("dir")
    vr.add_argument("--bundle", help="input bundle to cross-check names and money against")
    vr.add_argument("--strict", action="store_true", help="refuse unknown tables/columns/keys")
    asch = sub.add_parser("arrow-schema", help="print a table's canonical Arrow schema")
    asch.add_argument("scope", choices=["bundle", "results"])
    asch.add_argument("table")
    sub.add_parser("render-docs", help="print the generated column reference (TABLES.md)")
    mt = sub.add_parser("make-toy", help="write the synthetic toy bundle and results")
    mt.add_argument("dir")
    mt.add_argument("--format", default="parquet", choices=["parquet", "csv", "arrow"])
    a = ap.parse_args(argv)

    if a.cmd == "validate-bundle":
        rep = validate_bundle(a.dir)
        print(rep.summary())
        return 0 if rep.ok else 1
    if a.cmd == "validate-results":
        rep = validate_results(a.dir, bundle=a.bundle, strict=a.strict)
        print(rep.summary())
        return 0 if rep.ok else 1
    if a.cmd == "arrow-schema":
        print(table_schema(a.scope, a.table).arrow_schema().to_string(show_field_metadata=True))
        return 0
    if a.cmd == "render-docs":
        sys.stdout.write(render_tables_md())
        return 0
    if a.cmd == "make-toy":
        root = Path(a.dir)
        write_toy_bundle(root / "toy3", a.format)
        write_toy_results(root / "toy3_run", a.format)
        print(f"wrote {root / 'toy3'} and {root / 'toy3_run'}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
