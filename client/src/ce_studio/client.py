# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The Scenario Studio client."""

from __future__ import annotations

import csv
import gzip
import io
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from . import _toml
from .bundle import DeltaBundle, new_submission_ref, technology_from_results
from .config import ClientConfig
from .errors import LocalValidationError, NotFoundError, ScenarioFailed, ScenarioNotFound, StudioError, WaitTimeout
from .models import (
    COMPLETED,
    QAReport,
    Scenario,
    ScopeBus,
    ScopeInfo,
    ScopeManifest,
    SubmitReceipt,
    Summary,
    TableIndex,
    scope_buses_from_wire,
    scopes_from_wire,
)
from .refusals import Refusal
from .transport import Response, Transport, path_for

# Published file names are plain: lower-case stem, one or two extensions.
_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}$")

# The run's headline card and table index are HTTP API documents (JSON on the
# wire). They are parsed into Summary / TableIndex; they are never written to disk.
SUMMARY_FILE = "summary.json"
TABLES_FILE = "tables.json"
QA_TABLE = "qa_checks"
_WIRE_ONLY_SUFFIXES = (".json", ".geojson")


class StudioClient:
    """Typed access to the Scenario Studio HTTP API.

    Every method maps to one documented interaction; nothing is cached, so
    each call reflects the service's current state.

    >>> client = StudioClient.from_env()                       # doctest: +SKIP
    >>> sid = client.submit("my_delta").scenario_id            # doctest: +SKIP
    >>> client.wait(sid, on_update=lambda s: print(s.headline))  # doctest: +SKIP
    """

    def __init__(self, config: ClientConfig | None = None, *, transport: Transport | None = None) -> None:
        self.config = config or ClientConfig()
        self.transport = transport or Transport(self.config)

    @classmethod
    def from_env(cls, **overrides: Any) -> "StudioClient":
        """A client configured from ``CE_STUDIO_*`` environment variables."""
        return cls(ClientConfig.from_env(**overrides))

    @property
    def routes(self):
        return self.config.routes

    # ================================================================ scopes

    def list_scopes(self) -> list[ScopeInfo]:
        """The regions this service currently serves for scenarios."""
        doc = self.transport.get_json(self.routes.scopes_index, auth_required=False)
        return scopes_from_wire(doc)

    def scope_manifest(self, scope_id: str) -> ScopeManifest:
        """A served region's manifest (currency, horizon, weather years, archetypes, ...)."""
        doc = self.transport.get_json(
            path_for(self.routes.scope_manifest, scope_id=scope_id), auth_required=False
        )
        if not isinstance(doc, dict):
            raise StudioError(f"manifest for {scope_id!r} is not an object")
        return ScopeManifest.from_wire(scope_id, doc)

    def scope_buses(self, scope_id: str) -> list[ScopeBus]:
        """A served region's buses: integer id, name and whether the bus is inside the region."""
        doc = self.transport.get_json(path_for(self.routes.scope_buses, scope_id=scope_id), auth_required=False)
        return scope_buses_from_wire(doc)

    def bus_resolver(self, scope_id: str):
        """A function mapping a bus name of the region to its integer id (built from :meth:`scope_buses`).

        An unknown name raises :class:`LocalValidationError` (``unknown_bus_name``);
        a name two buses share raises it with ``ambiguous_bus_name``.
        """
        by_name: dict[str, list[int]] = {}
        for b in self.scope_buses(scope_id):
            if b.name:
                by_name.setdefault(b.name, []).append(b.bus_id)

        def resolve(name: str) -> int:
            ids = by_name.get(name)
            if not ids:
                raise LocalValidationError(f"{name!r} is not a bus of region {scope_id}", [
                    Refusal.of("unknown_bus_name", f"{name!r} is not a bus of region {scope_id}", id=name)])
            if len(ids) > 1:
                raise LocalValidationError(f"{name!r} names {len(ids)} buses of region {scope_id}", [
                    Refusal.of("ambiguous_bus_name", f"{name!r} names buses {sorted(ids)}", id=name)])
            return ids[0]

        return resolve

    # ================================================================ scenarios

    def submit(self, delta: DeltaBundle | str | Path, *, check: bool = True,
               submission_ref: str | None = None) -> SubmitReceipt:
        """Send a scenario delta bundle; returns the new scenario id.

        The bundle travels as one JSON request body (the API's wire format):
        ``delta.toml`` sections become top-level keys, each table a list of
        rows, bus names are resolved to the region's integer ids (from
        :meth:`scope_buses`) and technology codes mapped to the service's.

        With ``check=True`` (the default) the bundle is pre-checked locally
        and :class:`~ce_studio.errors.LocalValidationError` is raised before
        anything is sent if a blocking problem is found; a bundle that uses
        integer bus ids stamped with ``[scope] build`` is also checked against
        the region's current build (``stale_bus_ids``). A refusal by the
        service raises :class:`~ce_studio.errors.ValidationError` (422) with
        row-level :attr:`~ce_studio.errors.APIError.refusals`.

        Every submit carries a submission reference (``submission_ref``, or a
        fresh one). The service answers a repeat of the same reference with
        the run it already queued, so a submit is retried like a read —
        after a timeout or a gateway error too — and a scenario is never
        queued twice. Pass your own reference to retry safely across
        processes; :attr:`SubmitReceipt.duplicate` says when a repeat was
        recognised.
        """
        bundle = delta if isinstance(delta, DeltaBundle) else DeltaBundle.load(delta)
        if check:
            bundle.raise_for_errors()
        scope_id = bundle.scope_id or ""
        if check and bundle.build and bundle.uses_bus_ids():
            current = self.scope_manifest(scope_id).build
            if current is not None and current != bundle.build:
                raise LocalValidationError(
                    "the bundle's bus ids belong to another build of the region; nothing was sent",
                    [Refusal.of("stale_bus_ids", f"[scope] build = {bundle.build!r}, region {scope_id} is "
                                f"build {current!r}", table="delta.toml", column="scope.build")])
        needs_names = any(isinstance(b, str) for b in bundle.buses) or _has_bus_names(bundle)
        resolve = None
        if needs_names:
            try:
                resolve = self.bus_resolver(scope_id)
            except NotFoundError:
                raise LocalValidationError(
                    f"region {scope_id!r} publishes no bus listing to resolve bus names; nothing was sent",
                    [Refusal.of("unknown_scope", f"no bus listing for region {scope_id!r} — is it a served "
                                "region? (`ce-studio scopes`)", table="delta.toml", column="scenario.scope_id")]
                ) from None
        ref = submission_ref or new_submission_ref()
        body, ctype = bundle.encode(resolve, submission_ref=ref)
        resp = self.transport.request(
            "POST", self.routes.submit, body=body, headers={"Content-Type": ctype}, idempotent=True
        )
        return SubmitReceipt.from_wire(resp.json(), submission_ref=ref)

    def withdraw(self, scenario_id: str) -> Scenario | None:
        """Cancel a scenario that is still ``queued``.

        Raises :class:`~ce_studio.errors.ConflictError` (409) once the service
        has started it; returns the updated row when the service sends one.
        """
        resp = self.transport.request(
            "POST", path_for(self.routes.withdraw, sid=scenario_id), body=b""
        )
        doc = resp.json() if resp.body.strip() else None
        if isinstance(doc, dict) and any(k in doc for k in ("scenario_id", "id", "status")):
            row = dict(doc)
            row.setdefault("scenario_id", scenario_id)
            return Scenario.from_wire(row)
        return None

    def list_scenarios(self) -> list[Scenario]:
        """This account's scenarios, as the queue listing shows them."""
        doc = self.transport.get_json(self.routes.queue)
        rows = doc.get("scenarios", []) if isinstance(doc, dict) else doc
        return [Scenario.from_wire(r) for r in (rows or []) if isinstance(r, dict)]

    def status(self, scenario_id: str) -> Scenario:
        """One scenario's current row; :class:`ScenarioNotFound` if it is not listed."""
        for s in self.list_scenarios():
            if s.scenario_id == scenario_id:
                return s
        raise ScenarioNotFound(scenario_id)

    def wait(
        self,
        scenario_id: str,
        *,
        poll_s: float = 10.0,
        timeout_s: float | None = None,
        on_update: Callable[[Scenario], None] | None = None,
        raise_on_failure: bool = True,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> Scenario:
        """Poll until the scenario is ``completed``, ``failed`` or ``withdrawn``.

        ``on_update`` is called whenever the status or progress text changes.
        A failed or withdrawn run raises :class:`ScenarioFailed` (with parsed
        refusals) unless ``raise_on_failure=False``. ``timeout_s`` bounds the
        whole wait and raises :class:`WaitTimeout`.
        """
        if poll_s <= 0:
            raise ValueError("poll_s must be > 0")
        sleep = sleep or time.sleep
        clock = clock or time.monotonic
        start = clock()
        seen: tuple | None = None
        while True:
            s = self.status(scenario_id)
            key = (s.status, s.progress, s.detail)
            if on_update is not None and key != seen:
                on_update(s)
            seen = key
            if s.is_terminal:
                if s.status != COMPLETED and raise_on_failure:
                    raise ScenarioFailed(s, s.refusals)
                return s
            if timeout_s is not None and clock() - start + poll_s > timeout_s:
                raise WaitTimeout(s, timeout_s)
            sleep(poll_s)

    # ================================================================ results

    def _results_path(self, scenario_id: str, fname: str) -> str:
        if not _FILE_NAME_RE.match(fname) or ".." in fname:
            raise ValueError(f"not a published file name: {fname!r}")
        return path_for(self.routes.results, sid=scenario_id, fname=fname)

    def fetch(self, scenario_id: str, fname: str) -> bytes:
        """Raw bytes of one published file, in memory (``lmp.csv.gz``, ``asset_economics.parquet``, ...)."""
        try:
            return self.transport.request("GET", self._results_path(scenario_id, fname)).body
        except NotFoundError as err:
            err.args = (f"{err.args[0]} — not published for {scenario_id} (yet?)",)
            raise

    def summary(self, scenario_id: str) -> Summary:
        """The run's headline card. Check :meth:`Summary.caveats` before quoting it."""
        resp = self.transport.request("GET", self._results_path(scenario_id, SUMMARY_FILE))
        doc = resp.json()
        if not isinstance(doc, dict):
            raise StudioError("summary.json is not an object")
        return Summary.from_wire(doc)

    def tables(self, scenario_id: str) -> TableIndex:
        """The index of result tables offered for download (and any withheld, with reasons)."""
        resp = self.transport.request("GET", self._results_path(scenario_id, TABLES_FILE))
        return TableIndex.from_wire(resp.json())

    def download(self, scenario_id: str, fname: str, dest: str | Path) -> Path:
        """Stream one published file to ``dest`` (a file path, or a directory to put it in).

        JSON documents (``summary.json``, ``tables.json``, ...) are the API's
        wire format, not data files, and are refused here: read them with
        :meth:`summary` / :meth:`tables`, or write a TOML copy with
        :meth:`download_run`.
        """
        if fname.lower().endswith(_WIRE_ONLY_SUFFIXES):
            raise StudioError(f"{fname}: JSON is the API's wire format, not a CE data file, and is not written "
                              "to disk — use summary() / tables(), or download_run() for TOML documents")
        dest = Path(dest)
        if dest.is_dir():
            dest = dest / fname
        resp: Response = self.transport.request(
            "GET", self._results_path(scenario_id, fname), stream_to=dest
        )
        assert resp.path is not None
        return resp.path

    def download_table(
        self,
        scenario_id: str,
        name: str,
        dest_dir: str | Path,
        *,
        decompress: bool = False,
        index: TableIndex | None = None,
        verify: bool = True,
    ) -> Path:
        """Download one result table as CSV into ``dest_dir``.

        The table's wire file comes from the index (``<name>.csv`` or, for a
        large table, ``<name>.csv.gz``). ``decompress=True`` writes a plain
        ``.csv``. With ``verify=True`` the byte count is checked against the
        index entry.
        """
        index = index or self.tables(scenario_id)
        entry = index.get(name)
        if entry is None:
            raise StudioError(f"{scenario_id} has no table {name!r}; offered: "
                              + ", ".join(e.name for e in index.offered()))
        if not entry.offered or not entry.file:
            raise StudioError(f"table {name!r} was withheld: {entry.reason or 'no reason given'}")
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = self.download(scenario_id, entry.file, dest_dir / entry.file)
        if verify and entry.bytes is not None and path.stat().st_size != entry.bytes:
            size = path.stat().st_size
            path.unlink()
            raise StudioError(
                f"{entry.file}: got {size} bytes, the index says {entry.bytes} — download discarded"
            )
        if decompress and entry.gzip:
            plain = dest_dir / f"{entry.name}.csv"
            with gzip.open(path, "rb") as src, open(plain, "wb") as dst:
                shutil.copyfileobj(src, dst)
            path.unlink()
            path = plain
        return path

    def read_table(self, scenario_id: str, name: str, *, as_dataframe: bool = False,
                   index: TableIndex | None = None) -> Any:
        """A result table in memory: ``list[dict]`` of strings, or a pandas DataFrame."""
        index = index or self.tables(scenario_id)
        entry = index.get(name)
        if entry is None or not entry.offered or not entry.file:
            raise StudioError(f"table {name!r} is not offered for {scenario_id}")
        data = self.fetch(scenario_id, entry.file)
        if entry.gzip or entry.file.endswith(".gz"):
            data = gzip.decompress(data)
        if as_dataframe:
            try:
                import pandas as pd
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise StudioError("as_dataframe=True needs pandas (pip install pandas)") from exc
            return pd.read_csv(io.BytesIO(data))
        return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))

    def download_results(
        self,
        scenario_id: str,
        dest_dir: str | Path,
        *,
        tables: Sequence[str] | None = None,
        decompress: bool = False,
    ) -> list[Path]:
        """Download offered result tables (all, or the named ones) into ``dest_dir``."""
        index = self.tables(scenario_id)
        names = [e.name for e in index.offered()] if tables is None else list(tables)
        return [
            self.download_table(scenario_id, n, dest_dir, decompress=decompress, index=index)
            for n in names
        ]

    def qa_report(self, scenario_id: str) -> QAReport:
        """The per-run quality report: overall status plus the per-check table.

        Works for completed runs and for runs the QA gate failed. ``enabled``
        is False when the service produced no report for this run.
        """
        summary = self.summary(scenario_id)
        checks_csv: str | None = None
        try:
            index = self.tables(scenario_id)
            entry = index.get(QA_TABLE)
            fname = entry.file if entry is not None and entry.offered else None
        except NotFoundError:
            fname = None
        if fname is None and summary.qa_status is not None:
            fname = f"{QA_TABLE}.csv"  # the index may be absent on a QA-blocked run
        if fname:
            try:
                data = self.fetch(scenario_id, fname)
                if fname.endswith(".gz"):
                    data = gzip.decompress(data)
                checks_csv = data.decode("utf-8")
            except NotFoundError:
                checks_csv = None
        return QAReport.from_parts(summary, checks_csv)

    def artifact_table(self, scenario_id: str, stem: str) -> Any:
        """A Parquet results-diff artefact (e.g. ``asset_economics``) as a pyarrow Table.

        Needs the optional ``pyarrow`` dependency (``pip install ce-studio[parquet]``).
        """
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise StudioError("artifact_table needs pyarrow (pip install 'ce-studio[parquet]')") from exc
        data = self.fetch(scenario_id, f"{stem}.parquet")
        return pq.read_table(io.BytesIO(data))

    # ================================================================ a run directory

    def download_run(self, scenario_id: str, dest_dir: str | Path, *, normalise_technology: bool = True) -> Path:
        """Download a finished run as a directory the CE results reader opens.

        Writes every offered result table as plain CSV, plus TOML documents
        made from the API's JSON ones (see the README's mapping table):

        * ``summary.toml`` — ``[cost] total`` (the objective), ``[solve]``
          ``relative_gap``, ``converged``, ``stop_cause``, ``wall_s``, and the
          service's caveats under ``[x_studio]``;
        * ``manifest.toml`` — the run name and ``[money] currency``. It has no
          ``[format]`` table: the tables are the service's layout, which the
          CE results reader (``ce_results.Run``) reads like an older CE run,
          not a CE data format 1.0 directory;
        * ``qa_report.toml`` — the overall quality status and failing checks.

        With ``normalise_technology`` the ``technology`` column of every table
        is mapped from the service's codes to the CE format's (``solar`` ->
        ``solar_pv``, ``ccgt`` -> ``gas_cc``, ...). No JSON is written.
        """
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        summary = self.summary(scenario_id)
        index = self.tables(scenario_id)
        for entry in index.offered():
            path = self.download_table(scenario_id, entry.name, dest, decompress=True, index=index)
            if normalise_technology:
                _normalise_technology_csv(path)
        s_doc: dict[str, Any] = {}
        if summary.objective is not None:
            s_doc["cost"] = {"total": summary.objective}
        solve = {k: v for k, v in (("relative_gap", summary.gap), ("converged", summary.converged),
                                   ("stop_cause", summary.stop_cause), ("wall_s", summary.wall_clock.solve_s))
                 if v is not None}
        if solve:
            s_doc["solve"] = solve
        s_doc["x_studio"] = {"scenario_id": scenario_id, "caveats": summary.caveats(),
                             **({"representative_blocks": summary.representative_blocks}
                                if summary.representative_blocks else {})}
        _toml.dump(s_doc, dest / "summary.toml")
        manifest: dict[str, Any] = {"run": {"name": scenario_id, "engine": "CE Scenario Studio"}}
        if summary.currency:
            manifest["money"] = {"currency": summary.currency}
        _toml.dump(manifest, dest / "manifest.toml")
        if summary.qa_status is not None:
            _toml.dump({"qa": {"status": summary.qa_status, "failing": list(summary.qa_failing)}},
                       dest / "qa_report.toml")
        return dest


def _has_bus_names(bundle: DeltaBundle) -> bool:
    """True when any bus cell of the bundle's tables is a name rather than an integer id."""
    from .bundle import _read_rows, coerce_cell, delta_tables

    for tname, rel in bundle.table_files.items():
        spec = delta_tables()[tname]
        header, rows = _read_rows(bundle.root / rel)
        for h in header:
            col = spec.lookup(h)
            if col is not None and col.type == "bus":
                for r in rows:
                    try:
                        if isinstance(coerce_cell(r.get(h), "bus"), str):
                            return True
                    except ValueError:
                        continue
    return False


def _normalise_technology_csv(path: Path) -> None:
    """Rewrite a CSV's ``technology`` column in CE format codes (other columns byte-for-byte as text)."""
    if path.suffix != ".csv":
        return
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if not rows or "technology" not in rows[0]:
        return
    i = rows[0].index("technology")
    changed = False
    for r in rows[1:]:
        if i < len(r):
            new = technology_from_results(r[i])
            changed |= new != r[i]
            r[i] = new
    if changed:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh, lineterminator="\n").writerows(rows)
