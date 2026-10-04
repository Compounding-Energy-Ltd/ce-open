# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Named validation issues and the catalogue of every issue code.

Every problem the validator reports carries a stable snake_case ``code`` from :data:`CODES`. Codes are part
of the specification (SPEC.md section 10): tools may branch on them, so a code is never renamed or re-used
within a major version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator

ERROR = "error"
WARNING = "warning"

#: code -> (default severity, one-line meaning). Severity of the ``unknown_*`` and version codes depends on
#: scope: bundles refuse (error); results warn unless validated strictly (see SPEC.md section 3.3).
CODES: dict[str, tuple[str, str]] = {
    # documents (bundle.toml, manifest.toml, summary.toml, qa_report.toml, results_manifest.toml)
    "missing_document": (ERROR, "A required TOML document is absent."),
    "toml_parse_error": (ERROR, "A TOML document does not parse."),
    "bad_format_name": (ERROR, "`[format] name` is not the expected format name."),
    "bad_version": (ERROR, "`[format] version` is not of the form MAJOR.MINOR."),
    "unsupported_major_version": (ERROR, "The format's major version is not one this validator implements."),
    "newer_minor_version": (ERROR, "The format's minor version is newer than this validator; a reader cannot know what the additions mean."),
    "unknown_key": (ERROR, "A TOML key or section the specification does not define (and not an `x_` extension)."),
    "missing_key": (ERROR, "A required TOML key is absent."),
    "bad_currency": (ERROR, "The currency is not an ISO 4217 code of three upper-case letters."),
    "bad_axis": (ERROR, "The time or scenario axis declaration is inconsistent (order, ids, lengths)."),
    "probabilities_not_normalised": (ERROR, "Scenario probabilities do not sum to 1."),
    "block_weights_not_full_year": (WARNING, "Block weights do not add up to a full year (8760 or 8784 h)."),
    "leap_day_mismatch": (ERROR, "A chronological full-year block disagrees with `[time] leap_day`."),
    # files
    "missing_table": (ERROR, "A required table is absent."),
    "unknown_table": (ERROR, "A table file whose name the specification does not define (and not `x_`)."),
    "unknown_file": (ERROR, "A file that is neither a table, a document, a provenance sidecar nor documentation."),
    "ambiguous_table": (ERROR, "The same table is present in more than one file format."),
    "json_forbidden": (ERROR, "A JSON file: JSON is not a CE data format."),
    "unreadable_table": (ERROR, "A table file cannot be read."),
    # columns and values
    "missing_column": (ERROR, "A required column is absent."),
    "unknown_column": (ERROR, "A column the table's schema does not define (and not `x_`)."),
    "deprecated_spelling": (WARNING, "A legacy column or key spelling, accepted and renamed to the canonical one."),
    "duplicate_spelling": (ERROR, "Both the legacy and the canonical spelling of one column or key are present."),
    "ambiguous_legacy_cost": (ERROR, "A historical cost column whose name does not say whether the cost is annualised or overnight; it must be renamed explicitly."),
    "bad_type": (ERROR, "A value of the wrong type (column family or TOML type)."),
    "null_value": (ERROR, "A null (or NaN) in a column or key that does not allow one."),
    "bad_enum": (ERROR, "A value outside the column's or key's closed vocabulary."),
    "out_of_range": (ERROR, "A value outside the column's or key's permitted range."),
    "duplicate_key": (ERROR, "Two rows share a primary key or a unique column set."),
    "dangling_reference": (ERROR, "A reference to a row that does not exist in the referenced table."),
    "name_collision": (ERROR, "One name keys two different things (e.g. a generator and a candidate)."),
    "missing_value": (ERROR, "A value that is required in this row's situation is absent."),
    # bundle semantics
    "self_loop": (ERROR, "A branch whose two ends are the same bus."),
    "inert_line": (WARNING, "A line that is neither existing nor a candidate."),
    "missing_capex": (ERROR, "A candidate with no capital cost in either form."),
    "overnight_cost_without_annualisation": (ERROR, "An overnight cost with no capital recovery factor inputs (row or `[money]` defaults)."),
    "weather_without_year": (ERROR, "A series table carries `weather` without `year`."),
    "year_not_in_horizon": (ERROR, "A `year` value that is not a declared investment year."),
    "weather_not_declared": (ERROR, "A `weather` value that is not a declared weather year (or ordinal)."),
    "axis_out_of_range": (ERROR, "A scenario, block or period beyond the declared axis."),
    "incomplete_series": (ERROR, "A series with missing cells (periods, blocks, scenarios or weather groups)."),
    "missing_availability": (ERROR, "An availability-bounded resource with no availability series."),
    "inconsistent_interface": (ERROR, "Rows of one interface disagree on its name, limits or source."),
    # provenance sidecars
    "prov_unparsed_line": (WARNING, "A `.prov` line that is neither `key=value`, a comment nor blank."),
    "prov_duplicate_key": (WARNING, "A `.prov` key given more than once (the first wins)."),
    "prov_orphan": (WARNING, "A `.prov` sidecar whose described file does not exist."),
    "prov_bad_timestamp": (WARNING, "A `.prov` `built_utc` value that is not an ISO 8601 UTC instant."),
    "prov_hash_mismatch": (ERROR, "A `.prov` `sha256` that does not match the described file."),
    "prov_rows_mismatch": (ERROR, "A `.prov` `rows` that does not match the described table."),
    "stale_derived_table": (ERROR, "A derived table records a different source generation than its source's `.prov`."),
    # results semantics
    "currency_mismatch": (ERROR, "Results and input bundle declare different money."),
    "cost_identity_mismatch": (ERROR, "summary `[cost] total` differs from investment_capex + operational_opex."),
    "lmp_decomposition_mismatch": (ERROR, "lmp_per_mwh differs from lmp_energy + lmp_congestion + lmp_loss."),
    "partial_decomposition": (ERROR, "Some but not all of the three LMP components are present."),
    "curtailment_identity_mismatch": (ERROR, "curtailed_mw differs from available_mw − dispatch_mw."),
    "sign_convention": (ERROR, "A value with the wrong sign for its row kind (e.g. a positive retirement)."),
    "stop_cause_inconsistent": (WARNING, "`converged` and `stop_cause` disagree."),
    "index_mismatch": (ERROR, "results_manifest.toml disagrees with the files on disk."),
    "qa_status_inconsistent": (ERROR, "qa_report.toml's status or counts disagree with the qa_checks rows."),
    "qa_vacuous_pass": (ERROR, "A check reports PASS having examined nothing (population 0): it must be NA."),
    "flow_exceeds_rating": (WARNING, "A flow above the line's rating plus any reinforcement."),
}


@dataclass(frozen=True)
class Issue:
    """One validation finding."""

    code: str
    severity: str
    message: str
    file: str = ""
    table: str = ""
    column: str = ""
    rows: tuple[int, ...] = ()
    count: int = 0

    def __str__(self) -> str:
        where = self.table or self.file
        if self.column:
            where = f"{where}.{self.column}" if where else self.column
        loc = f" [{where}]" if where else ""
        rows = ""
        if self.rows:
            shown = ",".join(str(r) for r in self.rows)
            more = f" ({self.count} total)" if self.count > len(self.rows) else ""
            rows = f" rows {shown}{more}"
        return f"{self.severity.upper()} {self.code}{loc}{rows}: {self.message}"


@dataclass
class Report:
    """The outcome of validating one directory."""

    path: str
    kind: str
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def ok(self) -> bool:
        """True when there is no error (warnings do not fail validation)."""
        return not self.errors

    def codes(self, severity: str | None = None) -> set[str]:
        return {i.code for i in self.issues if severity is None or i.severity == severity}

    def __iter__(self) -> Iterator[Issue]:
        return iter(self.issues)

    def summary(self) -> str:
        head = f"{self.kind} {self.path}: {'OK' if self.ok else 'INVALID'} " \
               f"({len(self.errors)} error(s), {len(self.warnings)} warning(s))"
        return "\n".join([head, *(f"  {i}" for i in self.issues)])

    def raise_if_errors(self) -> None:
        if not self.ok:
            raise ValidationError(self)


class ValidationError(Exception):
    """Raised by :meth:`Report.raise_if_errors`."""

    def __init__(self, report: Report):
        super().__init__(report.summary())
        self.report = report


class Collector:
    """Accumulates issues for one validation pass, applying scope-specific severity overrides."""

    def __init__(self, overrides: dict[str, str] | None = None):
        self.issues: list[Issue] = []
        self.overrides = dict(overrides or {})

    def add(self, code: str, message: str, *, file: str = "", table: str = "", column: str = "",
            rows: Iterable[int] = (), count: int = 0, severity: str | None = None) -> None:
        if code not in CODES:
            raise KeyError(f"unregistered issue code {code!r}")
        sev = severity or self.overrides.get(code) or CODES[code][0]
        rows = tuple(int(r) for r in rows)
        self.issues.append(Issue(code, sev, message, file, table, column, rows, max(count, len(rows))))

    def has_error(self, *codes: str) -> bool:
        return any(i.severity == ERROR and (not codes or i.code in codes) for i in self.issues)
