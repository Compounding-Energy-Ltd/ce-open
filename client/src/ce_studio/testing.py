# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""An in-process mock of the Scenario Studio API, for tests and offline demos.

It speaks the same routes and wire shapes as the service, with a scripted
lifecycle instead of a solver: every queue poll moves each scenario one step
along ``queued -> ingested -> running -> completed``. All results are small,
synthetic toy tables in the service's published layout — no real system data.

The submit route reads what the service reads: ONE JSON object (at most
256 KiB; anything else reads as an empty object and is refused
``scope_id required``), with ``scope.bus_set`` as the region's integer bus ids,
``tables`` as lists of row objects in the service's column names and
technology codes, and an optional ``submission_ref`` that makes a repeat
answer the run already queued (``duplicate = true``).

Scripting hooks:

* scenario ``name`` starting ``fail`` — ends ``failed`` with row-level refusals;
* name starting ``qa-block`` — ends ``failed`` with ``qa_failed: ...`` and a QA report;
* name starting ``slow`` — completes unconverged, on a budget stop;
* :meth:`MockStudio.inject` — answer the next matching request(s) with a
  given status, body and headers (rate limits, outages, proxies);
* ``auto_advance = False`` — freeze the lifecycle (e.g. to test withdraw).

Usage::

    with MockStudio(api_key="test-key") as mock:
        client = StudioClient(ClientConfig(base_url=mock.url, api_key="test-key"))
        ...
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

LIFECYCLE = ("queued", "ingested", "running", "completed")
TOY_SCOPE = "toy_region"
TOY_SCOPE_NO_BASE = "toy_region_unrun"
#: the toy region's buses: (integer id, name)
TOY_BUSES = ((1, "north"), (2, "south"), (3, "coast"))
TOY_BUILD = "2026-01-01T00:00:00Z"
MAX_BODY_BYTES = 256 * 1024
#: the service's technology codes for add_candidates
WIRE_TECHNOLOGIES = ("solar", "wind_on", "wind_off", "solar_storage_hybrid", "storage", "ocgt", "ccgt",
                     "nuclear", "biomass", "geothermal", "coal", "lignite", "oil")
_REF_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@dataclass
class Fault:
    method: str
    path_prefix: str
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    times: int = 1
    after: bool = False  # process the request first, then answer with this reply (a lost response)


@dataclass
class _Scenario:
    sid: str
    name: str
    scope_id: str
    rigor_tier: str
    body: dict[str, Any]
    step: int = 0
    status: str = "queued"
    detail: str | None = None
    progress: str | None = None
    outcome: str = "ok"  # ok | fail | qa_block | slow

    def row(self) -> dict[str, Any]:
        r: dict[str, Any] = {
            "scenario_id": self.sid,
            "status": self.status,
            "kind": "scenario",
            "label": self.name,
            "scope_id": self.scope_id,
            "rigor_tier": self.rigor_tier,
        }
        if self.detail is not None:
            r["detail"] = self.detail
        if self.progress is not None and self.status == "running":
            r["progress"] = self.progress
        if self.status == "running":
            r["scope_class"] = "pocket"
            r["expected_wall_s"] = 900
        if self.status == "completed":
            r["converged"] = self.outcome != "slow"
            r["stop_cause"] = "iteration_cap" if self.outcome == "slow" else "gap"
        return r


class MockStudio:
    """A threaded HTTP server imitating the Scenario Studio API."""

    def __init__(self, api_key: str = "test-key", *, host: str = "127.0.0.1", port: int = 0) -> None:
        self.api_key = api_key
        self.auto_advance = True
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.submissions: dict[str, dict[str, Any]] = {}  # scenario id -> the JSON body as received
        self.published: dict[str, dict[str, bytes]] = {}
        self._refs: dict[str, str] = {}  # submission_ref -> scenario id
        self._scenarios: dict[str, _Scenario] = {}
        self._faults: list[Fault] = []
        self._lock = threading.Lock()
        self.scopes: dict[str, dict[str, Any]] = {
            TOY_SCOPE: _toy_manifest(TOY_SCOPE, with_base_run=True),
            TOY_SCOPE_NO_BASE: _toy_manifest(TOY_SCOPE_NO_BASE, with_base_run=False),
        }
        self.served = [
            {"scope_id": TOY_SCOPE, "label": "Toy region — 3 buses"},
            {"scope_id": TOY_SCOPE_NO_BASE, "label": "Toy region (no base run yet)"},
        ]
        handler = type("_Handler", (_Handler,), {"mock": self})
        self._server = ThreadingHTTPServer((host, port), handler)
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ lifecycle

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> "MockStudio":
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> "MockStudio":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # ------------------------------------------------------------ scripting

    def inject(self, method: str, path_prefix: str, status: int, *, body: bytes | str | dict = b"",
               headers: dict[str, str] | None = None, times: int = 1, after: bool = False) -> None:
        """Answer the next ``times`` requests matching method + path prefix with this reply.

        With ``after=True`` the request is processed first and only its reply is
        replaced — a response lost on the way back (e.g. a gateway timeout).
        """
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        with self._lock:
            self._faults.append(Fault(method.upper(), path_prefix, status, body, headers or {}, times, after))

    def scenario_status(self, sid: str) -> str:
        return self._scenarios[sid].status

    def count(self, method: str, path_prefix: str) -> int:
        return sum(1 for m, p, _ in self.requests if m == method and p.startswith(path_prefix))

    # ------------------------------------------------------------ internals

    def _take_fault(self, method: str, path: str) -> Fault | None:
        with self._lock:
            for f in self._faults:
                if f.method == method and path.startswith(f.path_prefix) and f.times > 0:
                    f.times -= 1
                    if f.times == 0:
                        self._faults.remove(f)
                    return f
        return None

    def _advance_all(self) -> None:
        if not self.auto_advance:
            return
        for s in self._scenarios.values():
            if s.status in ("completed", "failed", "withdrawn"):
                continue
            s.step += 1
            nxt = LIFECYCLE[min(s.step, len(LIFECYCLE) - 1)]
            if nxt == "running":
                s.status, s.progress = "running", f"iteration {3 * s.step} · gap {40 / s.step:.1f}%"
            elif nxt == "completed":
                self._finish(s)
            else:
                s.status = nxt

    def _finish(self, s: _Scenario) -> None:
        s.progress = None
        if s.outcome == "fail":
            s.status = "failed"
            s.detail = (
                "add_candidates[1] bus_id `site-a` unmappable_bus: bus 99 is not a bus of the base; "
                "add_loads[2] start_year `dc-1` load_starts_after_horizon: start_year 2090 is after "
                "every horizon year; delta.toml[0]  scope_missing: no bus set; (+3 more)"
            )
            return
        files = _toy_results(s)
        if s.outcome == "qa_block":
            s.status = "failed"
            s.detail = "qa_failed: balance_residual"
            files = {k: v for k, v in files.items() if k in ("summary.json", "qa_checks.csv")}
        else:
            s.status = "completed"
        self.published[s.sid] = files

    # request handlers (called from the server thread, under the lock)

    def handle(self, method: str, raw_path: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
        path = urlsplit(raw_path).path
        self.requests.append((method, path, headers))
        fault = self._take_fault(method, path)
        if fault is not None and not fault.after:
            return fault.status, fault.headers, fault.body
        reply = self._handle(method, path, headers, body)
        if fault is not None:
            return fault.status, fault.headers, fault.body
        return reply

    def _handle(self, method: str, path: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
        with self._lock:
            if path.startswith("/api/"):
                if headers.get("authorization") != f"Bearer {self.api_key}":
                    return _err(401, "missing or invalid access token")
                if method == "GET" and path == "/api/scenario/queue":
                    self._advance_all()
                    return _ok({"scenarios": [s.row() for s in self._scenarios.values()]})
                if method == "POST" and path == "/api/scenario/submit":
                    return self._submit(headers, body)
                if method == "POST" and path.startswith("/api/scenario/withdraw/"):
                    return self._withdraw(unquote(path.rsplit("/", 1)[1]))
                if method == "GET" and path.startswith("/api/scenario/results/"):
                    sid, _, fname = path[len("/api/scenario/results/"):].partition("/")
                    data = self.published.get(unquote(sid), {}).get(unquote(fname))
                    if data is None:
                        return _err(404, "not found")
                    return 200, {"Content-Type": _ctype(fname)}, data
                return _err(404, "no such endpoint")
            if method == "GET" and path == "/data/scenario_studio/served.json":
                return _ok({"scopes": self.served})
            if method == "GET" and path.startswith("/data/scenario_studio/"):
                parts = path.split("/")
                scope = unquote(parts[3]) if len(parts) > 4 else ""
                if scope in self.scopes and parts[4:] == ["manifest.json"]:
                    return _ok(self.scopes[scope])
                if scope in self.scopes and parts[4:] == ["buses.geojson"]:
                    return _ok(_toy_buses())
            return 404, {"Content-Type": "text/html"}, b"<html>Not Found</html>"

    def _submit(self, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
        # what the service does: read at most 256 KiB as JSON; anything else is an empty object
        doc: Any = None
        if len(body) <= MAX_BODY_BYTES:
            try:
                doc = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                doc = None
        doc = doc if isinstance(doc, dict) else {}
        ref = str(doc.get("submission_ref") or "").strip()
        if ref and not _REF_RE.match(ref):
            return _refuse(400, "bad submission_ref", code="bad_submission_ref")
        if ref and ref in self._refs:
            prior = self._scenarios[self._refs[ref]]
            return _ok({"scenario_id": prior.sid, "status": prior.status, "duplicate": True,
                        "submission_ref": ref})
        scope_id = str(doc.get("scope_id") or "").strip()
        if not re.fullmatch(r"[\w-]{1,64}", scope_id):
            return _refuse(400, "scope_id required")
        if scope_id not in self.scopes:
            return _refuse(422, f"Region {scope_id} is not a served region. Pick one from the region list.",
                           code="scope_unserved")
        if not self.scopes[scope_id].get("base_run", {}).get("path"):
            return 422, {"Content-Type": "application/json"}, json.dumps({
                "error": "base_run_missing",
                "status": 422,
                "rows": [{"table": "scope", "row": 0, "column": "scope_id", "id": scope_id,
                          "message": f"region {scope_id} has no base run yet"}],
            }).encode()
        bus_set = (doc.get("scope") or {}).get("bus_set") or []
        if not isinstance(bus_set, list) or not all(isinstance(b, int) and not isinstance(b, bool) for b in bus_set):
            return _refuse(422, "scope.bus_set must be integers", code="bus_set_invalid")
        if not bus_set:
            return _refuse(422, "scope.bus_set is empty", code="bus_set_missing")
        rows = []
        known = {b for b, _ in TOY_BUSES}
        tables = doc.get("tables") or {}
        for i, r in enumerate(tables.get("add_candidates") or []):
            if r.get("technology") not in WIRE_TECHNOLOGIES:
                rows.append({"table": "add_candidates", "row": i, "code": "bad_technology",
                             "error": "technology must be one of " + "|".join(WIRE_TECHNOLOGIES)})
            if not (isinstance(r.get("max_mw"), (int, float)) and 0 < r["max_mw"] <= 100000):
                rows.append({"table": "add_candidates", "row": i, "error": "max_mw out of range"})
        for tname in ("add_candidates", "add_loads"):
            for i, r in enumerate(tables.get(tname) or []):
                if r.get("bus_id") not in known:
                    rows.append({"table": tname, "row": i, "error": "bus not in region scope"})
        if rows:
            return 422, {"Content-Type": "application/json"}, json.dumps(
                {"error": f"{len(rows)} row(s) refused", "rows": rows}).encode()
        name = str(doc.get("name") or "scenario")[:60]
        outcome = "ok"
        for prefix, oc in (("fail", "fail"), ("qa-block", "qa_block"), ("slow", "slow")):
            if name.startswith(prefix):
                outcome = oc
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        sid = f"scn-{stamp}-{secrets.token_hex(2)}"
        tier = str(doc.get("rigor_tier") or "deliverable")
        self._scenarios[sid] = _Scenario(sid, name, scope_id, tier, doc, outcome=outcome)
        self.submissions[sid] = doc
        if ref:
            self._refs[ref] = sid
        return _ok({"scenario_id": sid, "status": "queued", "policies": [], "policies_n": 0,
                    "engine_contract": "mock"})

    def _withdraw(self, sid: str) -> tuple[int, dict[str, str], bytes]:
        s = self._scenarios.get(sid)
        if s is None:
            return _err(404, f"no scenario {sid}")
        if s.status != "queued":
            return _err(409, f"scenario {sid} is {s.status}; only queued scenarios can be withdrawn")
        s.status = "withdrawn"
        return _ok({"scenario_id": sid, "status": "withdrawn"})


class _Handler(BaseHTTPRequestHandler):
    mock: MockStudio
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # keep test output quiet
        pass

    def _dispatch(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        status, hdrs, payload = self.mock.handle(method, self.path, headers, body)
        self.send_response(status)
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")


# ------------------------------------------------------------------ helpers


def _ok(doc: Any) -> tuple[int, dict[str, str], bytes]:
    return 200, {"Content-Type": "application/json"}, json.dumps(doc, allow_nan=False).encode()


def _err(status: int, message: str) -> tuple[int, dict[str, str], bytes]:
    return status, {"Content-Type": "application/json"}, json.dumps(
        {"error": message, "status": status}).encode()


def _refuse(status: int, message: str, *, code: str | None = None) -> tuple[int, dict[str, str], bytes]:
    doc: dict[str, Any] = {"error": message}
    if code:
        doc["code"] = code
    return status, {"Content-Type": "application/json"}, json.dumps(doc).encode()


def _toy_buses() -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [20.0 + i / 10, 10.0 - i / 10]},
         "properties": {"bus_id": bid, "name": name, "in_scope": True, "boundary": False}}
        for i, (bid, name) in enumerate(TOY_BUSES)]}


def _ctype(fname: str) -> str:
    if fname.endswith(".json"):
        return "application/json"
    if fname.endswith(".csv"):
        return "text/csv"
    if fname.endswith(".gz"):
        return "application/gzip"
    return "application/octet-stream"


def _csv(rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


def _toy_manifest(scope_id: str, *, with_base_run: bool) -> dict[str, Any]:
    return {
        "extract": {"scope_id": scope_id, "tool": "synthetic", "created": TOY_BUILD},
        "base_bundle": {"path": "toy_bundle"},
        "base_run": {"path": "toy_base_run"} if with_base_run else {},
        "currency": {"currency": "USD", "cost_year": 2024, "dollar_year": 2024},
        "horizon": {"from_year": 2030, "through_year": 2030, "horizon_years": [2030]},
        "weather": {"years": [2019]},
        "conventions": {"tz": "UTC", "hour_convention": "hour-ending", "hour_index_base": 0},
        "scope": {"method": "bus_set", "n_buses_in_scope": 3},
        "archetypes": {"flat_95pct": {"shape": "flat", "load_factor": 0.95}},
        "tables": [{"name": "buses", "rows": 3}, {"name": "lines", "rows": 3},
                   {"name": "candidates", "rows": 4}],
    }


def _toy_results(s: _Scenario) -> dict[str, bytes]:
    """Synthetic results for the 3-bus toy region, in the service's published layout: a few CSV
    tables (one gzipped), a QA table and the two JSON wire documents (summary and table index)."""
    names = dict(TOY_BUSES)
    investment = _csv([
        {"year": 2030, "component": "renewable", "id": 1, "name": "solar_north_1", "technology": "solar",
         "bus": 1, "bus_name": names[1], "built_mw": 120.0},
        {"year": 2030, "component": "renewable", "id": 2, "name": "wind_south_1", "technology": "wind_onshore",
         "bus": 2, "bus_name": names[2], "built_mw": 80.0},
        {"year": 2030, "component": "storage", "id": 3, "name": "bess_coast_1", "technology": "bess",
         "bus": 3, "bus_name": names[3], "built_mw": 40.0},
    ])
    lmp_rows = []
    for b in (1, 2, 3):
        for p in range(1, 49):
            energy = round(20 + ((p - 1) % 24), 2)
            congestion = round(3.0 * (b - 1), 2)
            lmp_rows.append({"year": 2030, "scenario": 1, "weather": 2019, "block": 1 + (p - 1) // 24,
                             "bus": b, "bus_name": names[b], "period": 1 + (p - 1) % 24,
                             "lmp_gbp_per_mwh": round(energy + congestion, 2), "lmp_energy": energy,
                             "lmp_congestion": congestion, "lmp_loss": 0.0, "floor_slack_at_bus": False})
    lmp_gz = gzip.compress(_csv(lmp_rows), mtime=0)
    qa_status = "FAIL" if s.outcome == "qa_block" else "PASS"
    qa_checks = _csv([
        {"check": "blocks_solved", "family": "balance", "status": "PASS", "value": 0, "tolerance": 0,
         "unit": "", "worst_where": "", "count": 0, "population": 24, "note": "", "required": "true"},
        {"check": "balance_residual", "family": "balance",
         "status": "FAIL" if s.outcome == "qa_block" else "PASS",
         "value": 2.5 if s.outcome == "qa_block" else 0.0001, "tolerance": 0.001, "unit": "MW",
         "worst_where": "bus 2 hour 17", "count": 1 if s.outcome == "qa_block" else 0, "population": 72,
         "note": "", "required": "true"},
        {"check": "policy_target", "family": "policy", "status": "NA", "value": "", "tolerance": "",
         "unit": "", "worst_where": "", "count": 0, "population": 0, "note": "no policy rows", "required": "false"},
    ])
    tables = {
        "files": [
            {"name": "investment", "file": "investment.csv", "rows": 3, "bytes": len(investment),
             "gzip": False, "note": "csv as written"},
            {"name": "lmp", "file": "lmp.csv.gz", "rows": len(lmp_rows), "bytes": len(lmp_gz),
             "gzip": True, "note": "csv from parquet, gzip"},
            {"name": "flows", "offered": False, "reason": "over the per-file cap"},
            {"name": "qa_checks", "file": "qa_checks.csv", "rows": 3, "bytes": len(qa_checks),
             "gzip": False, "note": "csv as written"},
        ],
        "register": {"asrun_version": "0" * 64},
    }
    slow = s.outcome == "slow"
    summary: dict[str, Any] = {
        "converged": not slow,
        "gap": 0.31 if slow else 0.012,
        "gap_tolerance": 0.02,
        "gap_tolerance_source": "tier_default",
        "objective": 1.2345e7,
        "currency": "USD",
        "stop_reason": "iterations" if slow else "gap",
        "stop_cause": "iteration_cap" if slow else "gap",
        "wall_clock": {"queue_s": 4, "solve_s": 61},
        "ens_cost_share": 0.0,
        "qa_status": qa_status,
        "qa_failing": ["balance_residual"] if s.outcome == "qa_block" else [],
        "qa_blocking": s.outcome == "qa_block",
        "register_asrun_version_12": "000000000000",
    }
    if s.rigor_tier == "interactive":
        summary["representative_blocks"] = 24
    return {
        "summary.json": json.dumps(summary).encode(),
        "tables.json": json.dumps(tables).encode(),
        "investment.csv": investment,
        "lmp.csv.gz": lmp_gz,
        "qa_checks.csv": qa_checks,
    }
