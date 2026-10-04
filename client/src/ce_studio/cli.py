# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""``ce-studio`` — command-line access to the Scenario Studio.

Commands::

    ce-studio check   DELTA_DIR              # local pre-check, no network
    ce-studio submit  DELTA_DIR [--wait]     # upload; optionally wait for the result
    ce-studio status  SCENARIO_ID [--watch]  # one row of the queue
    ce-studio list                           # this account's scenarios
    ce-studio results SCENARIO_ID [--out DIR] [--table NAME ...] [--qa] [--run-dir DIR]
    ce-studio withdraw SCENARIO_ID           # cancel while still queued
    ce-studio scopes  [SCOPE_ID] [--buses]   # served regions / one region's manifest (and buses)

Configuration comes from ``CE_STUDIO_BASE_URL`` and ``CE_STUDIO_API_KEY``
(or the variable named by ``--api-key-env``). The key is never accepted as a
command-line argument, so it cannot end up in shell history.

``--toml`` prints machine-readable TOML instead of text.

Exit codes: 0 success; 1 the scenario failed or was refused (locally or by
the service), or a wait timed out; 2 configuration or usage error; 3 other
API error; 4 network error.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from pathlib import Path
from typing import Any, Sequence, TextIO

from . import __version__
from ._toml import dumps as toml_dumps
from .bundle import DeltaBundle
from .client import StudioClient
from .config import ENV_API_KEY, ClientConfig
from .errors import (
    APIError,
    ConfigError,
    LocalValidationError,
    ScenarioFailed,
    ScenarioNotFound,
    StudioError,
    TransportError,
    ValidationError,
    WaitTimeout,
)
from .models import QAReport, Scenario, Summary, TableIndex
from .refusals import Refusal

EXIT_OK, EXIT_FAILED, EXIT_CONFIG, EXIT_API, EXIT_NETWORK = 0, 1, 2, 3, 4


# ------------------------------------------------------------------ argument parsing


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ce-studio",
        allow_abbrev=False,
        description="Submit scenario deltas to the CE Scenario Studio and fetch results.",
    )
    p.add_argument("--version", action="version", version=f"ce-studio {__version__}")
    p.add_argument("--base-url", help="service root URL (default: $CE_STUDIO_BASE_URL)")
    p.add_argument("--api-key-env", default=ENV_API_KEY, metavar="VAR",
                   help=f"environment variable holding the access token (default: {ENV_API_KEY})")
    p.add_argument("--timeout-s", type=float, help="per-request timeout in seconds")
    p.add_argument("--toml", action="store_true", help="machine-readable TOML output")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    c = sub.add_parser("check", help="pre-check a delta bundle locally (no network)")
    c.add_argument("delta", type=Path, help="delta bundle directory")

    s = sub.add_parser("submit", help="upload a delta bundle")
    s.add_argument("delta", type=Path, help="delta bundle directory")
    s.add_argument("--no-check", action="store_true", help="skip the local pre-check")
    _wait_args(s, "--wait", "wait for the scenario to finish")

    st = sub.add_parser("status", help="show a scenario's status")
    st.add_argument("scenario_id")
    _wait_args(st, "--watch", "keep polling until the scenario finishes")

    sub.add_parser("list", help="list this account's scenarios")

    r = sub.add_parser("results", help="show a run's summary and tables; optionally download")
    r.add_argument("scenario_id")
    r.add_argument("--out", type=Path, help="download tables into this directory")
    r.add_argument("--table", action="append", default=None, metavar="NAME",
                   help="table to download (repeatable; default with --out: every offered table)")
    r.add_argument("--decompress", action="store_true", help="write .csv.gz tables as plain .csv")
    r.add_argument("--qa", action="store_true", help="show the run's quality report")
    r.add_argument("--run-dir", type=Path, metavar="DIR",
                   help="download the run as a directory the CE results reader opens (CSV tables + TOML)")

    w = sub.add_parser("withdraw", help="cancel a scenario that is still queued")
    w.add_argument("scenario_id")

    sc = sub.add_parser("scopes", help="list served regions, or show one region's manifest")
    sc.add_argument("scope_id", nargs="?")
    sc.add_argument("--buses", action="store_true", help="also list the region's buses (id and name)")
    return p


def _wait_args(p: argparse.ArgumentParser, flag: str, help_text: str) -> None:
    p.add_argument(flag, dest="wait", action="store_true", help=help_text)
    p.add_argument("--poll-s", type=float, default=10.0, help="seconds between polls (default 10)")
    p.add_argument("--max-wait-s", dest="wait_timeout_s", type=float, default=None,
                   metavar="SECONDS", help="give up waiting after this long (exit 1)")


# ------------------------------------------------------------------ output helpers


class _Out:
    def __init__(self, toml: bool, stdout: TextIO, stderr: TextIO) -> None:
        self.toml, self.stdout, self.stderr = toml, stdout, stderr

    def line(self, text: str = "") -> None:
        if not self.toml:
            print(text, file=self.stdout)

    def doc(self, doc: dict[str, Any]) -> None:
        if self.toml:
            self.stdout.write(toml_dumps(doc))

    def err(self, text: str) -> None:
        print(text, file=self.stderr)


def _scenario_doc(s: Scenario) -> dict[str, Any]:
    d = {k: v for k, v in s.raw.items() if not isinstance(v, (dict, list))}
    d.update({"scenario_id": s.scenario_id, "status": s.status})
    if s.refusals:
        d["refusal"] = [r.as_dict() for r in s.refusals.refusals]
        if s.refusals.omitted:
            d["refusals_omitted"] = s.refusals.omitted
    return d


def _print_refusals(out: _Out, refusals: Sequence[Refusal], omitted: int = 0) -> None:
    for r in refusals:
        tag = "warning" if r.is_warning else r.category
        out.line(f"  - [{tag}] {r}")
        if r.fix:
            out.line(f"      fix: {r.fix}")
    if omitted:
        out.line(f"  (+{omitted} more refusal(s) not listed by the service)")


def _print_scenario(out: _Out, s: Scenario) -> None:
    out.line(s.headline)
    if s.status in ("failed", "withdrawn"):
        parsed = s.refusals
        _print_refusals(out, parsed.refusals, parsed.omitted)


def _summary_doc(sm: Summary) -> dict[str, Any]:
    d = dict(sm.raw)
    d["caveats"] = sm.caveats()
    return d


def _print_summary(out: _Out, sm: Summary) -> None:
    unit = f" {sm.currency}" if sm.currency else ""
    if sm.objective is not None:
        out.line(f"objective      {sm.objective:,.0f}{unit}")
    out.line(f"converged      {sm.converged}" + (f" (stop: {sm.stop_cause})" if sm.stop_cause else ""))
    if sm.gap is not None:
        tol = f" (target {sm.gap_tolerance:.2%})" if sm.gap_tolerance is not None else ""
        out.line(f"gap            {sm.gap:.2%}{tol}")
    if sm.wall_clock.solve_s is not None:
        out.line(f"solve wall     {sm.wall_clock.solve_s:,.0f} s")
    if sm.qa_status:
        out.line(f"quality report {sm.qa_status}")
    cav = sm.caveats()
    if cav:
        out.line("caveats:")
        for c in cav:
            out.line(f"  ! {c}")


def _print_tables(out: _Out, idx: TableIndex) -> None:
    if not len(idx):
        out.line("no result tables offered")
        return
    out.line("tables:")
    for e in idx:
        if e.offered:
            rows = f"{e.rows:,} rows" if e.rows is not None else "rows ?"
            size = f"{e.bytes:,} B" if e.bytes is not None else ""
            out.line(f"  {e.name:<28} {e.file:<32} {rows:>14} {size:>14}")
        else:
            out.line(f"  {e.name:<28} withheld: {e.reason or 'no reason given'}")


def _print_qa(out: _Out, qa: QAReport) -> None:
    if not qa.enabled:
        out.line("quality report: not produced for this run")
        return
    out.line(f"quality report: {qa.status}" + (f" — failing: {', '.join(qa.failing)}" if qa.failing else ""))
    for c in qa.checks:
        val = "" if c.value is None else f" value={c.value:g}"
        tol = "" if c.tolerance is None else f" tol={c.tolerance:g}"
        where = f" at {c.worst_where}" if c.worst_where else ""
        pop = "" if c.population is None else f" ({c.population} examined)"
        out.line(f"  {c.status:<5} {c.family}/{c.check}{val}{tol}{(' ' + c.unit) if c.unit else ''}{where}{pop}")
    for p in qa.problems():
        out.line(f"  ! {p}")


def _qa_doc(qa: QAReport) -> dict[str, Any]:
    return {
        "enabled": qa.enabled,
        "status": qa.status,
        "failing": list(qa.failing),
        "blocking": qa.blocking,
        "derived_status": qa.derived_status,
        "problems": qa.problems(),
        "check": [dict(c.raw) for c in qa.checks],
    }


# ------------------------------------------------------------------ commands


def _client(args: argparse.Namespace) -> StudioClient:
    key = os.environ.get(args.api_key_env) or None
    cfg = ClientConfig.from_env(base_url=args.base_url, timeout_s=args.timeout_s, api_key=key)
    return StudioClient(cfg)


def _cmd_check(args, out: _Out) -> int:
    bundle = DeltaBundle.load(args.delta)
    found = bundle.check()
    errors = [r for r in found if not r.is_warning]
    out.doc({"delta": str(args.delta), "files": bundle.files, "ok": not errors,
             "finding": [r.as_dict() for r in found]})
    out.line(f"{args.delta}: {len(bundle.files)} file(s), {bundle.size_bytes:,} bytes"
             + (f", scenario {bundle.name!r}" if bundle.name else "")
             + (f" on region {bundle.scope_id!r}" if bundle.scope_id else ""))
    if found:
        _print_refusals(out, found)
    out.line("ok" if not errors else f"{len(errors)} blocking problem(s)")
    return EXIT_OK if not errors else EXIT_FAILED


def _wait(client: StudioClient, args, out: _Out, sid: str) -> int:
    final = client.wait(
        sid,
        poll_s=args.poll_s,
        timeout_s=args.wait_timeout_s,
        on_update=lambda s: out.line(s.headline),
        raise_on_failure=False,
    )
    out.doc(_scenario_doc(final))
    if final.status in ("failed", "withdrawn"):
        _print_refusals(out, final.refusals.refusals, final.refusals.omitted)
        return EXIT_FAILED
    return EXIT_OK


def _cmd_submit(args, out: _Out) -> int:
    client = _client(args)
    bundle = DeltaBundle.load(args.delta)
    if not args.no_check:
        warnings = [r for r in bundle.check() if r.is_warning]
        for w in warnings:
            out.err(f"warning: {w}")
    receipt = client.submit(bundle, check=not args.no_check)
    note = " (already submitted: the service answered with the existing run)" if receipt.duplicate else ""
    for w in receipt.warnings:
        out.err(f"service warning: {w}")
    if not args.wait:
        out.doc({"scenario_id": receipt.scenario_id, "status": receipt.status,
                 "submission_ref": receipt.submission_ref, "duplicate": receipt.duplicate})
        out.line(f"{receipt.scenario_id} {receipt.status}{note}")
        return EXIT_OK
    out.line(f"{receipt.scenario_id} {receipt.status}{note}")
    return _wait(client, args, out, receipt.scenario_id)


def _cmd_status(args, out: _Out) -> int:
    client = _client(args)
    if args.wait:
        return _wait(client, args, out, args.scenario_id)
    s = client.status(args.scenario_id)
    out.doc(_scenario_doc(s))
    _print_scenario(out, s)
    return EXIT_FAILED if s.status in ("failed", "withdrawn") else EXIT_OK


def _cmd_list(args, out: _Out) -> int:
    rows = _client(args).list_scenarios()
    out.doc({"scenario": [_scenario_doc(s) for s in rows]})
    if not rows:
        out.line("no scenarios")
    for s in rows:
        out.line(s.headline)
    return EXIT_OK


def _cmd_results(args, out: _Out) -> int:
    client = _client(args)
    sid = args.scenario_id
    summary = client.summary(sid)
    doc: dict[str, Any] = {"scenario_id": sid, "summary": _summary_doc(summary)}
    out.line(f"results for {sid}")
    _print_summary(out, summary)
    try:
        index = client.tables(sid)
    except APIError as err:
        if err.status != 404:
            raise
        index = TableIndex()
    doc["table"] = [dataclasses.asdict(e) for e in index]
    _print_tables(out, index)
    if args.qa:
        qa = client.qa_report(sid)
        doc["qa"] = _qa_doc(qa)
        _print_qa(out, qa)
    if args.out:
        paths = client.download_results(sid, args.out, tables=args.table, decompress=args.decompress)
        doc["downloaded"] = [str(p) for p in paths]
        for p in paths:
            out.line(f"saved {p}")
    if args.run_dir:
        run_dir = client.download_run(sid, args.run_dir)
        doc["run_dir"] = str(run_dir)
        out.line(f"run directory {run_dir} (open it with ce_results.Run)")
    out.doc(doc)
    return EXIT_OK


def _cmd_withdraw(args, out: _Out) -> int:
    row = _client(args).withdraw(args.scenario_id)
    status = row.status if row else "withdrawn"
    out.doc({"scenario_id": args.scenario_id, "status": status})
    out.line(f"{args.scenario_id} {status}")
    return EXIT_OK


def _cmd_scopes(args, out: _Out) -> int:
    client = _client(args)
    if args.scope_id:
        m = client.scope_manifest(args.scope_id)
        buses = client.scope_buses(args.scope_id) if args.buses else []
        doc: dict[str, Any] = {"scope": dict(m.raw)}
        if args.buses:
            doc["bus"] = [{"bus_id": b.bus_id, "name": b.name, "in_scope": b.in_scope} for b in buses]
        out.doc(doc)
        out.line(f"{m.scope_id}: currency {m.currency} (dollar year {m.dollar_year})")
        out.line(f"  build          : {m.build or '?'}   (stamp integer bus ids with [scope] build)")
        out.line(f"  buses in scope : {m.n_buses_in_scope}")
        out.line(f"  study years    : {', '.join(map(str, m.horizon_years)) or '?'}")
        out.line(f"  weather years  : {', '.join(map(str, m.weather_years)) or '?'}")
        out.line(f"  archetypes     : {', '.join(m.archetypes) or '-'}")
        out.line(f"  takes scenarios: {'yes' if m.has_base_run else 'no (no base run yet)'}")
        for b in buses:
            out.line(f"  bus {b.bus_id:>6}  {b.name or ''}{'' if b.in_scope is not False else '  (outside the region)'}")
        return EXIT_OK
    scopes = client.list_scopes()
    out.doc({"scope": [{"scope_id": s.scope_id, "label": s.label} for s in scopes]})
    for s in scopes:
        out.line(f"{s.scope_id:<32} {s.label or ''}")
    if not scopes:
        out.line("no served regions")
    return EXIT_OK


_COMMANDS = {
    "check": _cmd_check,
    "submit": _cmd_submit,
    "status": _cmd_status,
    "list": _cmd_list,
    "results": _cmd_results,
    "withdraw": _cmd_withdraw,
    "scopes": _cmd_scopes,
}


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None,
         stderr: TextIO | None = None) -> int:
    """Entry point; returns the exit code (see the module docstring)."""
    args = build_parser().parse_args(argv)
    out = _Out(args.toml, stdout or sys.stdout, stderr or sys.stderr)
    try:
        return _COMMANDS[args.command](args, out)
    except LocalValidationError as err:
        out.err(f"error: {err}")
        _print_refusals(_Out(False, out.stderr, out.stderr), err.refusals)
        return EXIT_FAILED
    except ValidationError as err:
        out.err(f"error: {err}")
        _print_refusals(_Out(False, out.stderr, out.stderr), err.refusals)
        return EXIT_FAILED
    except (ScenarioFailed, WaitTimeout) as err:
        out.err(f"error: {err}")
        return EXIT_FAILED
    except ConfigError as err:
        out.err(f"error: {err}")
        return EXIT_CONFIG
    except (APIError, ScenarioNotFound) as err:
        out.err(f"error: {err}")
        return EXIT_API
    except TransportError as err:
        out.err(f"error: {err}")
        return EXIT_NETWORK
    except StudioError as err:
        out.err(f"error: {err}")
        return EXIT_API


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
