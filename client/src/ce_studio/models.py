# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Typed views of what the service returns.

Every model keeps the original record in ``raw`` and parses leniently: the
contract is append-only and adds keys over time, so an unknown key is kept,
never an error, and a missing optional key is ``None`` — never a guessed
default. (The service drops a measure it did not compute rather than
sending ``null``; ``None`` here means exactly "not reported".)
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .refusals import ParsedDetail, parse_detail

# Queue lifecycle: ``queued`` (or ``withdrawn`` by its owner), then the service
# moves a run ``ingested`` -> ``running`` -> ``completed`` | ``failed``.
QUEUED = "queued"
INGESTED = "ingested"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
WITHDRAWN = "withdrawn"
TERMINAL_STATUSES = frozenset({COMPLETED, FAILED, WITHDRAWN})

# stop_cause vocabulary: only "gap" means the solver converged.
STOP_GAP = "gap"

# A run whose objective is more than this share unserved-energy cost "bought
# its feasibility with shed load" and is flagged even when converged.
ENS_SHARE_FLAG = 0.02


def _f(x: Any) -> float | None:
    """A finite float, or None."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _i(x: Any) -> int | None:
    v = _f(x)
    return int(v) if v is not None and v == int(v) else None


def _b(x: Any) -> bool | None:
    if isinstance(x, bool):
        return x
    if isinstance(x, str) and x.lower() in ("true", "false"):
        return x.lower() == "true"
    return None


def _s(x: Any) -> str | None:
    if x is None:
        return None
    s = str(x)
    return s if s != "" else None


def _strs(x: Any) -> tuple[str, ...]:
    """A list, or a comma/whitespace-separated string, as a tuple of strings."""
    if x is None:
        return ()
    if isinstance(x, str):
        return tuple(p for p in (q.strip() for q in x.replace(";", ",").split(",")) if p)
    if isinstance(x, Iterable):
        return tuple(str(p) for p in x if p is not None and str(p) != "")
    return (str(x),)


# ----------------------------------------------------------------- scenarios


@dataclass(frozen=True)
class SubmitReceipt:
    """What a successful submit returns.

    ``duplicate`` is True when the service recognised the submission reference
    and answered with the run it had already queued (nothing new was queued).
    """

    scenario_id: str
    status: str
    submission_ref: str | None = None
    duplicate: bool = False
    warnings: tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_wire(cls, doc: Any, submission_ref: str | None = None) -> "SubmitReceipt":
        doc = doc if isinstance(doc, Mapping) else {}
        sid = _scenario_id(doc)
        if not sid:
            from .errors import StudioError

            raise StudioError("submit succeeded but the reply carried no scenario id")
        warns = doc.get("warnings")
        return cls(scenario_id=sid, status=_s(doc.get("status")) or QUEUED,
                   submission_ref=_s(doc.get("submission_ref")) or submission_ref,
                   duplicate=doc.get("duplicate") is True,
                   warnings=tuple(str(w) for w in warns) if isinstance(warns, list) else (),
                   raw=dict(doc))


def _scenario_id(row: Mapping[str, Any]) -> str:
    for key in ("scenario_id", "id", "sid", "region_id"):
        v = row.get(key)
        if v:
            return str(v)
    return ""


@dataclass(frozen=True)
class Scenario:
    """One row of the queue listing.

    ``progress`` is the service's one-line progress text for a running solve
    (e.g. ``iteration 42 · gap 12.3%``); ``detail`` is the failure reason on a
    failed row — use :attr:`refusals` for the parsed form.
    """

    scenario_id: str
    status: str
    kind: str = "scenario"
    rigor_tier: str | None = None
    detail: str | None = None
    progress: str | None = None
    scope_id: str | None = None
    scope_class: str | None = None
    expected_wall_s: float | None = None
    converged: bool | None = None
    stop_cause: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_wire(cls, row: Mapping[str, Any]) -> "Scenario":
        progress = row.get("progress")
        if isinstance(progress, Mapping):  # some listings nest it
            progress = progress.get("detail") or progress.get("text")
        return cls(
            scenario_id=_scenario_id(row),
            status=_s(row.get("status")) or "unknown",
            kind=_s(row.get("kind")) or "scenario",
            rigor_tier=_s(row.get("rigor_tier")),
            detail=_s(row.get("detail")),
            progress=_s(progress),
            scope_id=_s(row.get("scope_id")),
            scope_class=_s(row.get("scope_class")),
            expected_wall_s=_f(row.get("expected_wall_s")),
            converged=_b(row.get("converged")),
            stop_cause=_s(row.get("stop_cause")),
            raw=dict(row),
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def succeeded(self) -> bool:
        return self.status == COMPLETED

    @property
    def refusals(self) -> ParsedDetail:
        """Refusals parsed from ``detail`` (empty unless the row failed)."""
        if self.status not in (FAILED, WITHDRAWN):
            return ParsedDetail([], 0, "")
        if self.status == WITHDRAWN and not self.detail:
            return parse_detail("withdrawn: the scenario was withdrawn before it ran")
        return parse_detail(self.detail)

    @property
    def headline(self) -> str:
        """One human line: id, status and whatever is most useful next to it."""
        bits = [self.scenario_id, self.status]
        if self.rigor_tier:
            bits.append(f"[{self.rigor_tier}]")
        if self.status == RUNNING and self.progress:
            bits.append(f"— {self.progress}")
        elif self.status == RUNNING and self.expected_wall_s:
            bits.append(f"— budget {self.expected_wall_s:.0f} s")
        if self.status == COMPLETED and self.converged is False:
            bits.append("— NOT CONVERGED")
        return " ".join(bits)


# ----------------------------------------------------------------- summary


@dataclass(frozen=True)
class WallClock:
    queue_s: float | None = None
    solve_s: float | None = None


@dataclass(frozen=True)
class Summary:
    """The headline card of a finished run (``summary.json``).

    Read :meth:`caveats` before quoting a number: a run can complete without
    converging, stop on a budget, buy feasibility with unserved energy, or
    be an exploration on representative days rather than the full year.
    """

    converged: bool | None = None
    gap: float | None = None
    gap_tolerance: float | None = None
    gap_tolerance_source: str | None = None
    objective: float | None = None
    currency: str | None = None
    stop_reason: str | None = None
    stop_cause: str | None = None
    wall_clock: WallClock = field(default_factory=WallClock)
    ens_cost_share: float | None = None
    comfort_cost_share: float | None = None
    representative_blocks: int | None = None
    maxrss_gb: float | None = None
    qa_status: str | None = None
    qa_failing: tuple[str, ...] = ()
    qa_blocking: bool | None = None
    register_version: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_wire(cls, doc: Mapping[str, Any]) -> "Summary":
        wc = doc.get("wall_clock") if isinstance(doc.get("wall_clock"), Mapping) else {}
        gap = _f(doc.get("gap"))
        if gap is None:
            gap = _f(doc.get("achieved_gap"))
        return cls(
            converged=_b(doc.get("converged")),
            gap=gap,
            gap_tolerance=_f(doc.get("gap_tolerance")),
            gap_tolerance_source=_s(doc.get("gap_tolerance_source")),
            objective=_f(doc.get("objective")),
            currency=_s(doc.get("currency")),
            stop_reason=_s(doc.get("stop_reason")),
            stop_cause=_s(doc.get("stop_cause")),
            wall_clock=WallClock(_f(wc.get("queue_s")), _f(wc.get("solve_s"))),
            ens_cost_share=_f(doc.get("ens_cost_share")),
            comfort_cost_share=_f(doc.get("comfort_cost_share")),
            representative_blocks=_i(doc.get("representative_blocks")),
            maxrss_gb=_f(doc.get("maxrss_gb")),
            qa_status=_s(doc.get("qa_status")),
            qa_failing=_strs(doc.get("qa_failing")),
            qa_blocking=_b(doc.get("qa_blocking")),
            register_version=_s(doc.get("register_asrun_version_12")),
            raw=dict(doc),
        )

    @property
    def is_exploration(self) -> bool:
        """Solved on representative days (the interactive tier), not the full year."""
        return bool(self.representative_blocks)

    @property
    def converged_to_gap(self) -> bool:
        """True only when the solver says it converged AND stopped on the gap."""
        if self.converged is not True:
            return False
        return self.stop_cause in (None, STOP_GAP)

    def caveats(self, ens_share_flag: float = ENS_SHARE_FLAG) -> list[str]:
        """Plain-language reasons to treat the numbers with care (empty = none found)."""
        out: list[str] = []
        if self.converged is False:
            out.append(
                "not converged: these are a feasible plan's numbers, not an optimum's"
                + (f" (gap {self.gap:.1%})" if self.gap is not None else "")
            )
        elif self.converged is None:
            out.append("convergence not reported")
        if self.stop_cause and self.stop_cause != STOP_GAP:
            out.append(f"stopped on a budget ({self.stop_cause}), not on the gap")
        if self.ens_cost_share is not None and self.ens_cost_share > ens_share_flag:
            out.append(
                f"{self.ens_cost_share:.1%} of the objective is unserved-energy cost: "
                "the plan buys feasibility with shed load"
            )
        if self.is_exploration:
            out.append(
                f"exploration result: solved on {self.representative_blocks} representative "
                "days, not the full year — not a result of record"
            )
        if self.qa_status and self.qa_status != "PASS":
            failing = f" ({', '.join(self.qa_failing)})" if self.qa_failing else ""
            out.append(f"quality report {self.qa_status}{failing}")
        return out


# ----------------------------------------------------------------- tables


@dataclass(frozen=True)
class TableEntry:
    """One entry of the result-table index (``tables.json``)."""

    name: str
    file: str | None = None
    offered: bool = True
    rows: int | None = None
    bytes: int | None = None
    gzip: bool = False
    note: str | None = None
    reason: str | None = None  # why a withheld table is missing

    @classmethod
    def from_wire(cls, e: Mapping[str, Any]) -> "TableEntry":
        offered = e.get("offered")
        offered = True if offered is None else bool(offered)
        return cls(
            name=str(e.get("name") or ""),
            file=_s(e.get("file")),
            offered=offered and bool(e.get("file")),
            rows=_i(e.get("rows")),
            bytes=_i(e.get("bytes")),
            gzip=bool(e.get("gzip")),
            note=_s(e.get("note")),
            reason=_s(e.get("reason")),
        )


@dataclass(frozen=True)
class TableIndex:
    """The result tables a run offers for download."""

    entries: tuple[TableEntry, ...] = ()
    register: Mapping[str, Any] | None = None  # assumptions-register stamp, when present

    @classmethod
    def from_wire(cls, doc: Any) -> "TableIndex":
        files = doc.get("files", []) if isinstance(doc, Mapping) else doc
        reg = doc.get("register") if isinstance(doc, Mapping) else None
        entries = tuple(TableEntry.from_wire(e) for e in (files or []) if isinstance(e, Mapping))
        return cls(entries, dict(reg) if isinstance(reg, Mapping) else None)

    def offered(self) -> list[TableEntry]:
        return [e for e in self.entries if e.offered]

    def withheld(self) -> list[TableEntry]:
        return [e for e in self.entries if not e.offered]

    def get(self, name: str) -> TableEntry | None:
        for e in self.entries:
            if e.name == name:
                return e
        return None

    def __iter__(self):
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


# ----------------------------------------------------------------- QA report

QA_STATUSES = ("PASS", "INCOMPLETE", "WARN", "FAIL")


@dataclass(frozen=True)
class QACheck:
    """One leg of the per-run quality report (a row of ``qa_checks.csv``).

    ``count`` is the number of items that failed or warned and ``population``
    the number examined. A ``PASS`` that examined nothing (``population ==
    0``) is :attr:`vacuous`: it could not have failed, so it proves nothing
    (the CE data format calls this ``qa_vacuous_pass`` and requires ``NA``).
    """

    check: str
    family: str = ""
    status: str = ""
    value: float | None = None
    tolerance: float | None = None
    unit: str = ""
    worst_where: str = ""
    count: int | None = None
    population: int | None = None
    note: str = ""
    required: bool | None = None
    raw: Mapping[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_row(cls, row: Mapping[str, str]) -> "QACheck":
        return cls(
            check=row.get("check", ""),
            family=row.get("family", ""),
            status=(row.get("status") or "").upper(),
            value=_f(row.get("value")),
            tolerance=_f(row.get("tolerance")),
            unit=row.get("unit", "") or "",
            worst_where=row.get("worst_where", "") or "",
            count=_i(row.get("count")),
            population=_i(row.get("population")),
            note=row.get("note", "") or "",
            required=_b(row.get("required")),
            raw=dict(row),
        )

    @property
    def vacuous(self) -> bool:
        """A PASS that examined nothing."""
        return self.status == "PASS" and self.population == 0


@dataclass(frozen=True)
class QAReport:
    """The run's quality report: overall status plus the per-check table.

    ``enabled`` is False when the service did not produce a report for this
    run (the report is switched on per service, not per scenario).
    """

    enabled: bool
    status: str | None = None
    failing: tuple[str, ...] = ()
    blocking: bool | None = None
    register_version: str | None = None
    checks: tuple[QACheck, ...] = ()

    @classmethod
    def from_parts(cls, summary: Summary, checks_csv: str | None) -> "QAReport":
        checks: tuple[QACheck, ...] = ()
        if checks_csv:
            reader = csv.DictReader(io.StringIO(checks_csv))
            checks = tuple(QACheck.from_row(r) for r in reader)
        enabled = summary.qa_status is not None or bool(checks)
        return cls(
            enabled=enabled,
            status=summary.qa_status,
            failing=summary.qa_failing,
            blocking=summary.qa_blocking,
            register_version=summary.register_version,
            checks=checks,
        )

    def by_status(self, status: str) -> list[QACheck]:
        return [c for c in self.checks if c.status == status.upper()]

    @property
    def passed(self) -> bool:
        """The service's overall status is PASS (see :meth:`problems` before relying on it)."""
        return self.status == "PASS"

    @property
    def vacuous_passes(self) -> list[QACheck]:
        """Checks reported PASS that examined nothing (``population == 0``)."""
        return [c for c in self.checks if c.vacuous]

    @property
    def derived_status(self) -> str | None:
        """The overall status the per-check rows imply (CE data format 1.0, section 9.6).

        FAIL if any check failed; else WARN if any warned; else INCOMPLETE if a
        required check is NA or a vacuous PASS; else PASS. ``None`` without rows.
        """
        if not self.checks:
            return None
        statuses = {c.status for c in self.checks}
        if "FAIL" in statuses:
            return "FAIL"
        if "WARN" in statuses:
            return "WARN"
        if any(c.required and (c.status == "NA" or c.vacuous) for c in self.checks):
            return "INCOMPLETE"
        return "PASS"

    def problems(self) -> list[str]:
        """Plain-language reasons not to take the overall status at face value (empty = none found)."""
        out = []
        for c in self.vacuous_passes:
            out.append(f"{c.family}/{c.check} reports PASS but examined nothing (population 0): it is not evidence")
        derived = self.derived_status
        if derived is not None and self.status is not None and derived != self.status:
            out.append(f"the overall status is {self.status} but the per-check rows imply {derived}")
        if self.checks and all(c.population is None for c in self.checks):
            out.append("the checks carry no population, so a vacuous PASS cannot be detected")
        return out


# ----------------------------------------------------------------- scopes


@dataclass(frozen=True)
class ScopeInfo:
    """A served region (scope) from the region index."""

    scope_id: str
    label: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)


def scopes_from_wire(doc: Any) -> list[ScopeInfo]:
    """Parse the region index, tolerating the shapes a static index may take.

    Accepted: a list of ids; a list of objects with an id key (``scope_id``,
    ``id``, ``region_id``) and an optional label (``label``, ``name``,
    ``title``); an object wrapping such a list under ``scopes`` / ``regions``
    / ``served``; or a plain ``{id: label}`` mapping.
    """
    if isinstance(doc, Mapping):
        for key in ("scopes", "regions", "served"):
            if key in doc and isinstance(doc[key], (list, Mapping)):
                return scopes_from_wire(doc[key])
        return [
            ScopeInfo(str(k), _label_of(v), dict(v) if isinstance(v, Mapping) else {"label": v})
            for k, v in doc.items()
        ]
    out: list[ScopeInfo] = []
    for item in doc or []:
        if isinstance(item, Mapping):
            sid = item.get("scope_id") or item.get("id") or item.get("region_id")
            if sid:
                out.append(ScopeInfo(str(sid), _label_of(item), dict(item)))
        elif item is not None:
            out.append(ScopeInfo(str(item)))
    return out


def _label_of(v: Any) -> str | None:
    if isinstance(v, Mapping):
        for key in ("label", "name", "title"):
            if v.get(key):
                return str(v[key])
        return None
    return _s(v)


@dataclass(frozen=True)
class ScopeManifest:
    """A served region's manifest: currency, horizon, weather years, archetypes.

    ``build`` identifies the extract of the region the manifest describes; the
    region's integer bus ids are valid only for that build (stamp a delta that
    uses ids with ``[scope] build``). The fields most callers need are lifted
    out; everything else stays in ``raw``.
    """

    scope_id: str
    currency: str | None = None
    cost_year: int | None = None
    dollar_year: int | None = None
    build: str | None = None
    horizon_years: tuple[int, ...] = ()
    weather_years: tuple[int, ...] = ()
    n_buses_in_scope: int | None = None
    has_base_run: bool = False
    archetypes: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_wire(cls, scope_id: str, doc: Mapping[str, Any]) -> "ScopeManifest":
        def sect(name: str) -> Mapping[str, Any]:
            v = doc.get(name)
            return v if isinstance(v, Mapping) else {}

        base_run = sect("base_run")
        horizon = sect("horizon")
        weather = sect("weather")
        years = horizon.get("horizon_years") or []
        if not years and _i(horizon.get("from_year")) and _i(horizon.get("through_year")):
            years = range(_i(horizon["from_year"]), _i(horizon["through_year"]) + 1)
        tables = doc.get("tables") if isinstance(doc.get("tables"), list) else []
        return cls(
            scope_id=_s(sect("extract").get("scope_id")) or scope_id,
            currency=_s(sect("currency").get("currency")),
            cost_year=_i(sect("currency").get("cost_year")),
            dollar_year=_i(sect("currency").get("dollar_year")) or _i(sect("currency").get("cost_year")),
            build=_s(sect("extract").get("created")),
            horizon_years=tuple(y for y in (_i(v) for v in years) if y is not None),
            weather_years=tuple(y for y in (_i(v) for v in (weather.get("years") or [])) if y is not None),
            n_buses_in_scope=_i(sect("scope").get("n_buses_in_scope")),
            has_base_run=bool(base_run.get("path") or base_run.get("name")),
            archetypes=tuple(str(k) for k in sect("archetypes")),
            tables=tuple(str(t.get("name")) for t in tables if isinstance(t, Mapping) and t.get("name")),
            raw=dict(doc),
        )


@dataclass(frozen=True)
class ScopeBus:
    """One bus of a served region's bus listing."""

    bus_id: int
    name: str | None = None
    in_scope: bool | None = None


def scope_buses_from_wire(doc: Any) -> list[ScopeBus]:
    """Parse a region's bus listing (a GeoJSON feature collection of bus points)."""
    feats = doc.get("features", []) if isinstance(doc, Mapping) else doc
    out = []
    for f in feats or []:
        props = f.get("properties", f) if isinstance(f, Mapping) else {}
        bid = _i(props.get("bus_id", props.get("id")))
        if bid is not None:
            out.append(ScopeBus(bid, _s(props.get("name")), _b(props.get("in_scope"))))
    return out
