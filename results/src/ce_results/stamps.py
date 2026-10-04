# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Typed views of the TOML documents in a run directory.

* :class:`SolveStamps` -- how the solve ended: objective, bound, gap, why it stopped.
* :class:`Provenance` -- what produced the run: format and engine version, money,
  time axis, input bundle digest, assumptions-register version.
* :class:`QAReport` -- the run's quality report, when it carries one.

Every field is optional in the files, so every field here may be ``None``.
Readers should test for ``None`` rather than assume a default.

Key names follow the CE data format 1.0; legacy spellings (money keys with a
currency suffix, ``stop_reason`` words) are accepted and mapped.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

import pandas as pd

__all__ = ["SolveStamps", "Provenance", "QAReport", "canonical_summary", "LEGACY_STOP_REASON"]

# legacy `stop_reason` words -> 1.0 `stop_cause` words
LEGACY_STOP_REASON = {"gap": "gap", "iterations": "iteration_cap", "wall": "wall_cap"}

_MONEY_SUFFIX = re.compile(r"_gbp(?=_|$)")
_MONEY_SECTIONS = ("cost", "solve", "cost_decomposition")


def canonical_summary(doc: Mapping[str, Any] | None) -> dict[str, Any]:
    """``summary.toml`` with legacy money keys renamed (``total_gbp`` -> ``total``).

    Only the ``cost``, ``solve`` and ``cost_decomposition`` sections are
    renamed; a canonical key already present wins over its legacy twin.
    """
    out = {k: v for k, v in (doc or {}).items()}
    for sec in _MONEY_SECTIONS:
        body = out.get(sec)
        if not isinstance(body, Mapping):
            continue
        new: dict[str, Any] = {}
        for k, v in body.items():
            if isinstance(v, Mapping):
                new[k] = v
                continue
            ck = _MONEY_SUFFIX.sub("", k)
            if ck != k and ck in body:
                continue  # the canonical spelling is present too
            new[ck] = v
        out[sec] = new
    return out


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _int(v: Any) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


def _str(v: Any) -> str | None:
    return None if v is None else str(v)


def _bool(v: Any) -> bool | None:
    return v if isinstance(v, bool) else None


def _first(*candidates: Any) -> Any:
    for c in candidates:
        if c is not None:
            return c
    return None


def _section(doc: Mapping[str, Any] | None, name: str) -> dict[str, Any]:
    v = (doc or {}).get(name)
    return dict(v) if isinstance(v, Mapping) else {}


@dataclass(frozen=True)
class SolveStamps:
    """How the solve ended.

    ``gap`` is the relative optimality gap ``(total - lower_bound) / |total|``.
    ``stop_cause`` says why the outer loop stopped: ``"gap"`` (the target was
    met), ``"iteration_cap"`` or ``"wall_cap"`` (a budget ran out). A run that
    stopped on a budget is not converged even if its gap happens to be small.
    """

    objective: float | None = None
    lower_bound: float | None = None
    gap: float | None = None
    iterations: int | None = None
    converged: bool | None = None
    stop_cause: str | None = None
    wall_s: float | None = None
    mip_master: bool | None = None
    #: older CE runs only (not in the 1.0 specification; ``None`` on a 1.0 run)
    integrality_gap: float | None = None
    #: older CE runs only (not in the 1.0 specification; ``None`` on a 1.0 run)
    invariants_passed: bool | None = None

    @classmethod
    def from_documents(cls, summary: Mapping[str, Any] | None,
                       manifest: Mapping[str, Any] | None) -> "SolveStamps":
        """Read the stamps from ``summary.toml`` ``[solve]`` / ``[cost]``, falling
        back to a legacy ``manifest.toml`` ``[extra]`` table."""
        summary = canonical_summary(summary)
        solve, cost = _section(summary, "solve"), _section(summary, "cost")
        extra = _section(manifest, "extra")
        cause = _first(solve.get("stop_cause"), extra.get("stop_cause"))
        if cause is None:
            reason = _first(solve.get("stop_reason"), extra.get("stop_reason"))
            cause = None if reason is None else LEGACY_STOP_REASON.get(str(reason), str(reason))
        return cls(
            objective=_num(_first(cost.get("total"), extra.get("objective_gbp"), extra.get("objective"))),
            lower_bound=_num(_first(solve.get("lower_bound"), extra.get("lower_bound_gbp"),
                                    extra.get("lower_bound"))),
            gap=_num(_first(solve.get("relative_gap"), extra.get("gap"))),
            iterations=_int(_first(solve.get("iterations"), extra.get("iterations"))),
            converged=_bool(_first(solve.get("converged"), extra.get("converged"))),
            stop_cause=_str(cause),
            wall_s=_num(_first(solve.get("wall_s"), extra.get("wall_s"))),
            mip_master=_bool(solve.get("mip_master")),
            integrality_gap=_num(solve.get("integrality_gap")),
            invariants_passed=_bool(solve.get("invariants_passed")),
        )

    def certified(self, tol: float = 1e-3) -> bool:
        """True when the run says it converged and its reported gap is within ``tol``.

        Both facts are required: a run without a gap stamp is not certified.
        """
        if self.gap is None or not math.isfinite(self.gap):
            return False
        return bool(self.converged) and self.gap <= tol

    def as_series(self) -> pd.Series:
        return pd.Series({k: getattr(self, k) for k in self.__dataclass_fields__}, dtype=object)


def _register_version(reg: Mapping[str, Any] | None) -> str | None:
    if not reg:
        return None
    for key in ("version", "register_version", "as_run_version", "declared_version"):
        if reg.get(key) not in (None, ""):
            return str(reg[key])
    return None


@dataclass(frozen=True)
class Provenance:
    """What produced the run, read from ``manifest.toml`` and ``results_manifest.toml``.

    ``inputs`` identifies the input bundle (name, format version, digest).

    Older CE runs only (not defined by the 1.0 specification, so ``None`` or
    empty on a 1.0 run): ``register`` is an assumptions-register stamp
    (``[register]`` or ``[x_register]``) and ``register_version`` its headline
    version string; ``system_name``, ``system_counts``, ``system_meta`` and
    ``labels`` are data-vintage stamps of older engine releases.
    """

    format_version: str | None = None
    run_name: str | None = None
    created: str | None = None
    engine: str | None = None
    engine_version: str | None = None
    mode: str | None = None
    seed: int | None = None
    currency: str | None = None
    dollar_year: int | None = None
    investment_years: tuple[int, ...] = ()
    weather_years: tuple[int, ...] = ()
    inputs: dict[str, Any] = field(default_factory=dict)
    system_name: str | None = None
    system_counts: dict[str, int] = field(default_factory=dict)
    system_meta: dict[str, Any] = field(default_factory=dict)
    register: dict[str, Any] = field(default_factory=dict)
    register_version: str | None = None
    labels: tuple[str, ...] = ()
    results_schema_version: int | None = None

    @classmethod
    def from_documents(cls, manifest: Mapping[str, Any] | None,
                       results_manifest: Mapping[str, Any] | None,
                       summary: Mapping[str, Any] | None = None) -> "Provenance":
        man = dict(manifest or {})
        fmt, run, money = _section(man, "format"), _section(man, "run"), _section(man, "money")
        time, system = _section(man, "time"), _section(man, "system")
        rman = dict(results_manifest or {})
        reg = dict(_first(man.get("register"), man.get("x_register"), rman.get("register"),
                          rman.get("x_register"), (summary or {}).get("register")) or {})
        counts = {k: int(v) for k, v in system.items() if isinstance(v, int) and not isinstance(v, bool)}
        years = _first(time.get("investment_years"), system.get("years")) or ()
        labels = _first(man.get("labels"), (summary or {}).get("labels")) or ()
        created = _first(run.get("created_utc"), run.get("timestamp"))
        return cls(
            format_version=_str(fmt.get("version")),
            run_name=_str(_first(run.get("name"), rman.get("run"))),
            created=None if created is None else str(created),
            engine=_str(run.get("engine")),
            engine_version=_str(_first(run.get("engine_version"), run.get("cenovasage_version"))),
            mode=_str(run.get("mode")),
            seed=_int(run.get("seed")),
            currency=_str(_first(money.get("currency"), run.get("currency"))),
            dollar_year=_int(money.get("dollar_year")),
            investment_years=tuple(int(y) for y in years),
            weather_years=tuple(int(y) for y in (time.get("weather_years") or ())),
            inputs=_section(man, "inputs"),
            system_name=_str(system.get("name")),
            system_counts=counts,
            system_meta=dict(system.get("meta", {}) or {}),
            register=reg,
            register_version=_register_version(reg),
            labels=tuple(str(x) for x in labels),
            results_schema_version=_int(rman.get("schema_version")),
        )


@dataclass(frozen=True)
class QAReport:
    """The run's quality report (``qa_report.toml`` plus the ``qa_checks`` table).

    ``status`` is ``"PASS"``, ``"WARN"``, ``"FAIL"`` or ``"INCOMPLETE"`` (a
    required check could not be evaluated); ``failing`` lists the legs that did
    not pass as ``"<check>:<status>"``.
    """

    status: str | None = None
    mode: str | None = None
    n_pass: int | None = None
    n_warn: int | None = None
    n_fail: int | None = None
    n_na: int | None = None
    n_required_na: int | None = None
    failing: tuple[str, ...] = ()
    tolerances: dict[str, Any] = field(default_factory=dict)
    checks: pd.DataFrame | None = None
    cost_ledger: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_documents(cls, qa_report: Mapping[str, Any] | None,
                       summary: Mapping[str, Any] | None,
                       checks: pd.DataFrame | None) -> "QAReport | None":
        doc = dict(qa_report or {})
        head = _section(doc, "qa") or _section(summary, "qa")
        if not head and checks is None and not doc:
            return None
        if checks is None and doc.get("checks"):
            checks = pd.DataFrame(list(doc["checks"]))
        return cls(
            status=_str(head.get("status")),
            mode=_str(head.get("mode")),
            n_pass=_int(head.get("n_pass")),
            n_warn=_int(head.get("n_warn")),
            n_fail=_int(head.get("n_fail")),
            n_na=_int(head.get("n_na")),
            n_required_na=_int(head.get("n_required_na")),
            failing=tuple(str(x) for x in head.get("failing", ()) or ()),
            tolerances=_section(doc, "tolerances"),
            checks=checks,
            cost_ledger=_section(doc, "cost_ledger") or _section(doc, "x_cost_ledger"),
        )

    @property
    def passed(self) -> bool:
        return self.status == "PASS"
