# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Scenario delta bundles: load from a directory, pre-check, build the request body.

A bundle is a directory::

    my_delta/
      delta.toml            # required: name, region, scope, run settings
      add_candidates.csv    # optional tables, CSV or Parquet
      add_lines.csv
      add_loads.csv
      add_substations.csv
      force_builds.csv
      remove_assets.csv

``delta.toml``::

    [scenario]
    name = "toy_flat_load_with_battery"
    scope_id = "toy_region"          # a served region (``ce-studio scopes``)

    [scope]
    buses = ["north", "south", "coast"]   # bus names (or integer ids) the scenario may touch

    [run]
    rigor_tier = "interactive"       # or "deliverable"

The tables' columns, types and units are in ``data/delta_tables.toml`` (and the
README). Columns use the CE data format 1.0 names where one exists; the client
renames them to the service's wire names. Capacity-factor profiles are not
uploaded: the service builds them for drawn wind and solar sites itself.

On submit the bundle travels as ONE JSON request body (the Studio's HTTP wire
format — JSON never touches disk): the ``delta.toml`` sections are lifted to
top-level keys and each table becomes a list of row objects.
:meth:`DeltaBundle.check` repeats the cheap, certain refusals locally; the
service validates everything again and remains the authority.
"""

from __future__ import annotations

import csv
import datetime as _dt
import functools
import io
import json
import math
import re
import secrets
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from .errors import LocalValidationError
from .refusals import Refusal

TABLE_SUFFIXES = (".csv", ".parquet")
RIGOR_TIERS = ("interactive", "deliverable")
SERVICE_SET_RUN_KEYS = ("run_qa", "assumptions_register")
BASE_PATH_KEYS = ("base_bundle", "base_run", "weather_dir")
#: delta.toml sections passed to the service verbatim (it validates them)
PASS_THROUGH_SECTIONS = ("costs", "policies", "hosting", "reinforce", "bus_limits", "financing", "screening")
KNOWN_SECTIONS = ("scenario", "scope", "run", "base", *PASS_THROUGH_SECTIONS)
#: the service reads at most this many bytes of a submit body (a larger body reads as empty)
MAX_BODY_BYTES = 256 * 1024
#: an idempotency key: 8 to 64 letters, digits, dashes or underscores
SUBMISSION_REF_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_INT_RE = re.compile(r"^[+-]?\d+$")

BusResolver = Callable[[str], int]


# ------------------------------------------------------------------ the delta table reference


@dataclass(frozen=True)
class DeltaColumn:
    """One column of a delta table (see ``data/delta_tables.toml``)."""

    name: str
    type: str
    wire: str
    required: bool = False
    unit: str = ""
    description: str = ""


@dataclass(frozen=True)
class DeltaTable:
    name: str
    description: str
    columns: tuple[DeltaColumn, ...]
    refused: Mapping[str, str] = field(default_factory=dict)  # spelling -> what to write instead

    def lookup(self, spelling: str) -> DeltaColumn | None:
        """The column a file header names: its canonical name or its wire name."""
        for c in self.columns:
            if spelling in (c.name, c.wire):
                return c
        return None


@functools.lru_cache(maxsize=1)
def _reference() -> tuple[dict[str, DeltaTable], dict[str, str], dict[str, str]]:
    raw = tomllib.loads(resources.files("ce_studio").joinpath("data/delta_tables.toml").read_text("utf-8"))
    refused: dict[str, dict[str, str]] = {}
    for r in raw.get("refused", []):
        refused.setdefault(r["table"], {})[r["name"]] = r["use"]
    tables = {}
    for tname, t in raw["table"].items():
        cols = tuple(DeltaColumn(name=c["name"], type=c["type"], wire=c.get("wire", c["name"]),
                                 required=c.get("required", False), unit=c.get("unit", ""),
                                 description=c.get("description", "")) for c in t["column"])
        tables[tname] = DeltaTable(tname, t.get("description", ""), cols, refused.get(tname, {}))
    tech = raw.get("technology", {})
    return tables, dict(tech.get("to_wire", {})), dict(tech.get("from_results", {}))


def delta_tables() -> dict[str, DeltaTable]:
    """Every delta table the client knows, by name."""
    return _reference()[0]


def technology_to_wire(code: str) -> str:
    """The service's technology code for a CE format code (a service code is returned as given)."""
    return _reference()[1].get(code, code)


def technology_from_results(code: str) -> str:
    """The CE format code for a technology code in the service's published tables."""
    return _reference()[2].get(code, code)


KNOWN_TABLES = tuple(delta_tables())


# ------------------------------------------------------------------ cell values


class _BadValue(ValueError):
    pass


def _is_null(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "") or (isinstance(v, float) and math.isnan(v))


def coerce_cell(value: Any, kind: str) -> Any:
    """A file cell as the JSON value the service expects; ``None`` for an empty cell.

    CSV cells arrive as text, Parquet cells as Python values. ``bus`` cells
    stay names (``str``) unless they are integer ids (digits only).
    """
    if _is_null(value):
        return None
    if kind == "string":
        return str(value)
    if kind == "bool":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("true", "false"):
            return s == "true"
        raise _BadValue(f"{value!r} is not true or false")
    if kind in ("int", "bus"):
        if isinstance(value, bool):
            raise _BadValue(f"{value!r} is not a number")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if value.is_integer():
                return int(value)
            raise _BadValue(f"{value!r} is not a whole number")
        s = str(value).strip()
        if _INT_RE.match(s):
            return int(s)
        if kind == "bus":
            return s  # a bus name, resolved at submit time
        try:
            f = float(s)
        except ValueError:
            raise _BadValue(f"{value!r} is not a whole number") from None
        if math.isfinite(f) and f.is_integer():
            return int(f)
        raise _BadValue(f"{value!r} is not a whole number")
    if kind == "float":
        if isinstance(value, bool):
            raise _BadValue(f"{value!r} is not a number")
        try:
            f = float(value)
        except (TypeError, ValueError):
            raise _BadValue(f"{value!r} is not a number") from None
        if not math.isfinite(f):
            raise _BadValue(f"{value!r} is not a finite number")
        return f
    raise _BadValue(f"unknown column type {kind!r}")


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Header and rows of a CSV (text cells) or Parquet (typed cells, needs pyarrow) table."""
    if path.suffix.lower() == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise LocalValidationError(f"{path.name}: reading Parquet needs pyarrow "
                                       "(pip install 'ce-studio[parquet]')") from exc
        t = pq.read_table(path)
        return list(t.column_names), t.to_pylist()
    text = path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    return list(reader.fieldnames or []), rows


# ------------------------------------------------------------------ the bundle


@dataclass
class DeltaBundle:
    """A scenario delta read from disk.

    ``files`` are bundle-relative POSIX paths, ``delta.toml`` first.
    Construct with :meth:`load`.
    """

    root: Path
    files: list[str]
    delta: dict[str, Any] = field(default_factory=dict)
    load_problems: list[Refusal] = field(default_factory=list)

    # ------------------------------------------------------------ loading

    @classmethod
    def load(cls, path: str | Path) -> "DeltaBundle":
        """Read a bundle directory. Problems are recorded, not raised (see :meth:`check`)."""
        root = Path(path)
        if not root.is_dir():
            raise LocalValidationError(f"{root} is not a directory")
        problems: list[Refusal] = []
        files: list[str] = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if any(part.startswith(".") for part in PurePosixPath(rel).parts):
                continue  # hidden files and editor droppings are never read
            if p.is_symlink():
                problems.append(Refusal.of("unsupported_file", f"{rel}: symbolic links are not read", table=rel))
                continue
            files.append(rel)
        delta: dict[str, Any] = {}
        if "delta.toml" not in files:
            problems.append(Refusal.of("delta_toml_missing", f"no delta.toml in {root.name}/"))
        else:
            files.remove("delta.toml")
            files.insert(0, "delta.toml")
            try:
                delta = tomllib.loads((root / "delta.toml").read_text(encoding="utf-8"))
            except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
                problems.append(Refusal.of("delta_toml_unreadable", str(exc)))
        return cls(root=root, files=files, delta=delta, load_problems=problems)

    # ------------------------------------------------------------ accessors

    def _section(self, name: str) -> dict[str, Any]:
        v = self.delta.get(name)
        return v if isinstance(v, dict) else {}

    @property
    def name(self) -> str | None:
        v = self._section("scenario").get("name")
        return str(v) if v else None

    @property
    def scope_id(self) -> str | None:
        v = self._section("scenario").get("scope_id")
        return str(v) if v else None

    @property
    def buses(self) -> list[Any]:
        """The scope's buses as written: names (``str``) and/or integer ids."""
        scope = self._section("scope")
        v = scope.get("buses", scope.get("bus_set", []))
        return list(v) if isinstance(v, list) else []

    @property
    def bus_set(self) -> list[Any]:  # the wire name; kept for callers of earlier releases
        return self.buses

    @property
    def build(self) -> str | None:
        """The region build the bundle's integer bus ids belong to (``[scope] build``)."""
        v = self._section("scope").get("build")
        return str(v) if v not in (None, "") else None

    @property
    def rigor_tier(self) -> str | None:
        v = self._section("run").get("rigor_tier")
        return None if v is None else str(v)

    @property
    def table_files(self) -> dict[str, str]:
        """Known table name -> bundle-relative file."""
        out = {}
        for rel in self.files[1:] if self.files[:1] == ["delta.toml"] else self.files:
            p = PurePosixPath(rel)
            if len(p.parts) == 1 and p.suffix.lower() in TABLE_SUFFIXES and p.stem in delta_tables():
                out[p.stem] = rel
        return out

    @property
    def size_bytes(self) -> int:
        return sum((self.root / f).stat().st_size for f in self.files)

    def uses_bus_ids(self) -> bool:
        """True when the scope or any bus cell gives an integer id rather than a name."""
        if any(isinstance(b, int) and not isinstance(b, bool) for b in self.buses):
            return True
        for tname, rel in self.table_files.items():
            spec = delta_tables()[tname]
            try:
                header, rows = _read_rows(self.root / rel)
            except Exception:  # noqa: BLE001 - reported by check()
                continue
            for h in header:
                col = spec.lookup(h)
                if col is None or col.type != "bus":
                    continue
                for r in rows:
                    try:
                        if isinstance(coerce_cell(r.get(h), "bus"), int):
                            return True
                    except _BadValue:
                        pass
        return False

    # ------------------------------------------------------------ checks

    def check(self) -> list[Refusal]:
        """Local pre-check (no network): blocking refusals and warnings, in file order."""
        out = list(self.load_problems)
        if not self.delta:
            return out
        out += self._check_sections()
        for rel in self.files[1:] if self.files[:1] == ["delta.toml"] else self.files:
            out += self._check_file(rel)
        if self.uses_bus_ids() and not self.build:
            out.append(Refusal.of(
                "warn_bus_ids_unstamped",
                "integer bus ids are valid only for the region build they were read from; use bus names, "
                "or stamp the ids with [scope] build (shown by `ce-studio scopes SCOPE_ID`)",
                table="delta.toml", column="scope.build"))
        return out

    def _check_sections(self) -> list[Refusal]:
        out: list[Refusal] = []
        for key, val in self.delta.items():
            if key in BASE_PATH_KEYS:
                continue  # reported below
            if key not in KNOWN_SECTIONS:
                out.append(Refusal.of("unknown_section", f"delta.toml has [{key}], which the service does not read",
                                      table="delta.toml", column=key))
        if not self.scope_id:
            out.append(Refusal.of("scope_id_missing", "delta.toml has no [scenario] scope_id",
                                  table="delta.toml", column="scenario.scope_id"))
        scope = self._section("scope")
        if "buses" in scope and "bus_set" in scope:
            out.append(Refusal.of("bad_scope", "give [scope] buses or bus_set, not both",
                                  table="delta.toml", column="scope"))
        elif not self.buses:
            out.append(Refusal.of("scope_missing",
                                  "delta.toml has no [scope] buses: the run would solve every bus of the base",
                                  table="delta.toml", column="scope.buses"))
        else:
            bad = [b for b in self.buses if isinstance(b, bool) or not isinstance(b, (int, str)) or b == ""]
            if bad:
                out.append(Refusal.of("bad_scope", f"[scope] buses must be bus names or integer ids; got {bad[:3]}",
                                      table="delta.toml", column="scope.buses"))
        run = self._section("run")
        tier = run.get("rigor_tier")
        if tier is not None and tier not in RIGOR_TIERS:
            out.append(Refusal.of("bad_rigor_tier", f"got {tier!r}; expected one of {RIGOR_TIERS}",
                                  table="delta.toml", column="run.rigor_tier"))
        if "gap_tolerance" in run:
            g = run["gap_tolerance"]
            ok = isinstance(g, (int, float)) and not isinstance(g, bool) and math.isfinite(g) and 0 < g < 1
            if not ok:
                out.append(Refusal.of("bad_gap_tolerance", f"got {g!r}; need a number strictly between 0 and 1",
                                      table="delta.toml", column="run.gap_tolerance"))
        for k in SERVICE_SET_RUN_KEYS:
            if k in run:
                out.append(Refusal.of("bad_run_qa", f"[run] {k} is set by the service operator",
                                      table="delta.toml", column=f"run.{k}"))
        base = self._section("base")
        named = [k for k in BASE_PATH_KEYS if base.get(k) or self.delta.get(k) or run.get(k)]
        if named:
            out.append(Refusal.of("warn_base_path_in_delta", f"delta.toml names {', '.join(named)}",
                                  table="delta.toml"))
        return out

    def _check_file(self, rel: str) -> list[Refusal]:
        p = PurePosixPath(rel)
        if len(p.parts) > 1:
            return [Refusal.of("unsupported_file", f"{rel}: only top-level tables are read (the service builds "
                               "capacity-factor profiles itself; profiles are not uploaded)", table=rel)]
        if p.suffix.lower() not in TABLE_SUFFIXES:
            return [Refusal.of("unsupported_file", f"{rel}: tables are CSV or Parquet", table=rel)]
        if p.stem not in delta_tables():
            return [Refusal.of("warn_unknown_table", f"{rel}: not one of {', '.join(KNOWN_TABLES)}; not sent",
                               table=rel)]
        return self._check_table(p.stem, rel)

    def _check_table(self, tname: str, rel: str) -> list[Refusal]:
        spec = delta_tables()[tname]
        try:
            header, rows = _read_rows(self.root / rel)
        except LocalValidationError as err:
            return [Refusal.of("unsupported_file", str(err), table=rel)]
        except (UnicodeDecodeError, csv.Error, OSError, ValueError) as exc:
            return [Refusal.of("unsupported_file", f"{rel}: cannot read the table ({exc})", table=rel)]
        out: list[Refusal] = []
        seen: dict[str, str] = {}
        for h in header:
            if h in spec.refused:
                out.append(Refusal.of("ambiguous_cost_column",
                                      f"{h!r} does not say whether the cost is overnight or annualised; "
                                      f"write {spec.refused[h]}", table=tname, column=h))
                continue
            col = spec.lookup(h)
            if col is None:
                out.append(Refusal.of("warn_unknown_column", f"{h!r} is not a column of {tname}; not sent",
                                      table=tname, column=h))
                continue
            if col.name in seen:
                out.append(Refusal.of("duplicate_column", f"{seen[col.name]!r} and {h!r} are the same column",
                                      table=tname, column=h))
                continue
            seen[col.name] = h
        for col in spec.columns:
            if col.required and col.name not in seen:
                out.append(Refusal.of("missing_column", f"{tname} needs a {col.name!r} column",
                                      table=tname, column=col.name))
        for name, h in seen.items():
            col = spec.lookup(name)
            assert col is not None
            for i, r in enumerate(rows):
                v = r.get(h)
                try:
                    cell = coerce_cell(v, col.type)
                except _BadValue as exc:
                    out.append(Refusal.of("bad_value", str(exc), table=tname, row=i, column=h))
                    continue
                if cell is None and col.required:
                    out.append(Refusal.of("bad_value", "empty cell in a required column", table=tname, row=i,
                                          column=h))
        return out

    def errors(self) -> list[Refusal]:
        """Blocking findings of :meth:`check` (warnings excluded)."""
        return [r for r in self.check() if not r.is_warning]

    def raise_for_errors(self) -> None:
        errs = self.errors()
        if errs:
            raise LocalValidationError(
                f"the delta bundle failed {len(errs)} local check(s); nothing was sent", errs
            )

    # ------------------------------------------------------------ the request body

    def to_wire(self, resolve_bus: BusResolver | None = None, *, submission_ref: str | None = None) -> dict[str, Any]:
        """The submit request as the service reads it (a JSON object, as a ``dict``).

        ``resolve_bus`` maps a bus NAME to the region's integer id; it is needed
        only when the bundle uses names (:meth:`StudioClient.submit` supplies it
        from the region's bus listing). ``submission_ref`` is the idempotency
        key sent with the request.
        """
        problems: list[Refusal] = []

        def bus_id(value: Any, where: dict[str, Any]) -> int | None:
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if resolve_bus is None:
                problems.append(Refusal.of("unknown_bus_name", f"bus name {value!r} needs the region's bus "
                                           "listing to resolve (submit through StudioClient)", **where))
                return None
            try:
                return int(resolve_bus(str(value)))
            except LocalValidationError as err:
                problems.extend(r if r.table else Refusal.of(r.code, r.message, **where) for r in err.refusals)
                if not err.refusals:
                    problems.append(Refusal.of("unknown_bus_name", str(err), **where))
            except KeyError:
                problems.append(Refusal.of("unknown_bus_name", f"{value!r} is not a bus of the region", **where))
            return None

        scen, run = self._section("scenario"), self._section("run")
        body: dict[str, Any] = {"scope_id": self.scope_id or ""}
        if self.name:
            body["name"] = self.name
        ids = [bus_id(b, {"table": "delta.toml", "column": "scope.buses", "id": str(b)}) for b in self.buses]
        body["scope"] = {"bus_set": [i for i in ids if i is not None]}
        for k in ("sweep_id", "variant_of"):
            if scen.get(k) not in (None, ""):
                body[k] = scen[k]
        for k in ("rigor_tier", "gap_tolerance", "notify"):
            if k in run:
                body[k] = run[k]
        for k in PASS_THROUGH_SECTIONS:
            if k in self.delta:
                body[k] = self.delta[k]

        tables: dict[str, list[dict[str, Any]]] = {}
        for tname, rel in self.table_files.items():
            spec = delta_tables()[tname]
            header, rows = _read_rows(self.root / rel)
            cols = [(h, spec.lookup(h)) for h in header if h not in spec.refused]
            out_rows = []
            for i, r in enumerate(rows):
                row: dict[str, Any] = {}
                for h, col in cols:
                    if col is None:
                        continue  # unknown column: warned by check(), never sent
                    try:
                        v = coerce_cell(r.get(h), col.type)
                    except _BadValue as exc:
                        problems.append(Refusal.of("bad_value", str(exc), table=tname, row=i, column=h))
                        continue
                    if v is None:
                        continue
                    if col.type == "bus":
                        v = bus_id(v, {"table": tname, "row": i, "column": h, "id": str(v)})
                        if v is None:
                            continue
                    if tname == "add_candidates" and col.name == "technology":
                        v = technology_to_wire(v)
                    row[col.wire] = v
                out_rows.append(row)
            if out_rows:
                tables[tname] = out_rows
        if tables:
            body["tables"] = tables
        if submission_ref is not None:
            if not SUBMISSION_REF_RE.match(submission_ref):
                problems.append(Refusal.of("bad_submission_ref", f"{submission_ref!r}: 8 to 64 letters, digits, "
                                           "dashes or underscores"))
            body["submission_ref"] = submission_ref
        if problems:
            raise LocalValidationError(f"the delta bundle cannot be encoded ({len(problems)} problem(s)); "
                                       "nothing was sent", problems)
        return body

    def encode(self, resolve_bus: BusResolver | None = None, *,
               submission_ref: str | None = None) -> tuple[bytes, str]:
        """The UTF-8 JSON request body and its content type.

        Raises :class:`LocalValidationError` (``payload_too_large``) when the
        body exceeds :data:`MAX_BODY_BYTES`, the most the service reads.
        """
        body = json.dumps(self.to_wire(resolve_bus, submission_ref=submission_ref), allow_nan=False,
                          separators=(",", ":"), ensure_ascii=False, default=_json_default).encode("utf-8")
        if len(body) > MAX_BODY_BYTES:
            raise LocalValidationError(
                f"the request body is {len(body):,} bytes; the service reads at most {MAX_BODY_BYTES:,}",
                [Refusal.of("payload_too_large", f"{len(body):,} bytes > {MAX_BODY_BYTES:,}")])
        return body, "application/json"


def new_submission_ref() -> str:
    """A fresh idempotency key for one submission."""
    return f"ces-{secrets.token_hex(12)}"


def _json_default(v: Any) -> Any:
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    raise TypeError(f"{type(v).__name__} cannot be sent")


def render_delta_reference() -> str:
    """The delta table reference as Markdown (``DELTA_TABLES.md`` is generated from it)."""
    lines = ["# Scenario delta tables", "",
             "Generated from `src/ce_studio/data/delta_tables.toml` by "
             "`python -c \"from ce_studio.bundle import render_delta_reference as r; print(r(), end='')\"`.",
             "Do not edit by hand.", "",
             "Column names follow the CE data format 1.0 where it has one; the client sends each column under "
             "its *wire* name. A column's wire name is also accepted in a file, except the refused spellings "
             "listed per table. `bus` cells are bus names of the region (resolved by the client) or integer "
             "ids valid for the region build named in `[scope] build`. Money is in the region's currency and "
             "dollar year.", ""]
    for t in delta_tables().values():
        lines += [f"## `{t.name}`", "", t.description, "",
                  "| column | wire name | type | required | unit | description |", "|---|---|---|---|---|---|"]
        for c in t.columns:
            wire = "" if c.wire == c.name else f"`{c.wire}`"
            lines.append(f"| `{c.name}` | {wire} | {c.type} | {'yes' if c.required else ''} | {c.unit} | "
                         f"{c.description.replace('|', '/')} |")
        if t.refused:
            lines += ["", "Refused spellings (`ambiguous_cost_column`): "
                      + "; ".join(f"`{k}` — write {v}" for k, v in t.refused.items()) + "."]
        lines.append("")
    _, to_wire, from_results = _reference()
    lines += ["## Technology codes", "",
              "In `add_candidates.technology`, a CE format code is sent as the service's code; the service's "
              "own codes are accepted as given.", "", "| CE format code | service code |", "|---|---|"]
    lines += [f"| `{k}` | `{v}` |" for k, v in to_wire.items()]
    lines += ["", "In downloaded result tables (`StudioClient.download_run`), the service's codes are "
              "mapped back:", "", "| service code | CE format code |", "|---|---|"]
    lines += [f"| `{k}` | `{v}` |" for k, v in from_results.items()]
    return "\n".join(lines) + "\n"
