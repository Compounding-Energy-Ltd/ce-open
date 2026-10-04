# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""ce_studio — a typed Python client for the CE Scenario Studio HTTP API.

The Scenario Studio takes a *scenario delta* (a small bundle of changes drawn
against a served base region: new candidate sites, loads, lines, pins and
removals), solves it, and publishes a results diff. This package wraps the
documented HTTP surface:

* submit a delta bundle (sent as the API's JSON wire format, with bus names
  resolved and an idempotency key), withdraw a queued one, poll status / wait;
* fetch the headline summary, the result-table index, CSV tables and the
  per-run QA report, or download a run as a directory the CE results reader
  opens (``download_run``);
* list served regions ("scopes"), read a scope's manifest and bus listing.

Quick start::

    from ce_studio import StudioClient

    client = StudioClient.from_env()          # CE_STUDIO_BASE_URL, CE_STUDIO_API_KEY
    receipt = client.submit("examples/toy_delta")
    final = client.wait(receipt.scenario_id)
    print(client.summary(final.scenario_id).caveats())

Everything here is standard-library Python; ``pandas`` / ``pyarrow`` are used
only when you ask for a DataFrame or a Parquet artefact.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .bundle import DeltaBundle, DeltaColumn, DeltaTable, delta_tables
from .client import StudioClient
from .config import ClientConfig, RetryPolicy, Routes
from .errors import (
    APIError,
    AuthError,
    BadRequestError,
    ConfigError,
    ConflictError,
    LocalValidationError,
    NotFoundError,
    PayloadTooLargeError,
    RateLimitedError,
    ScenarioFailed,
    ScenarioNotFound,
    ServerError,
    StudioError,
    TransportError,
    ValidationError,
    WaitTimeout,
)
from .models import (
    QACheck,
    QAReport,
    Scenario,
    ScopeBus,
    ScopeInfo,
    ScopeManifest,
    SubmitReceipt,
    Summary,
    TableEntry,
    TableIndex,
)
from .refusals import CodeInfo, ParsedDetail, Refusal, describe, parse_detail

__all__ = [
    "__version__",
    "StudioClient",
    "ClientConfig",
    "RetryPolicy",
    "Routes",
    "DeltaBundle",
    "DeltaColumn",
    "DeltaTable",
    "delta_tables",
    # models
    "Scenario",
    "SubmitReceipt",
    "Summary",
    "TableEntry",
    "TableIndex",
    "QACheck",
    "QAReport",
    "ScopeBus",
    "ScopeInfo",
    "ScopeManifest",
    # refusals
    "Refusal",
    "CodeInfo",
    "ParsedDetail",
    "describe",
    "parse_detail",
    # errors
    "StudioError",
    "ConfigError",
    "LocalValidationError",
    "TransportError",
    "APIError",
    "BadRequestError",
    "AuthError",
    "NotFoundError",
    "ConflictError",
    "PayloadTooLargeError",
    "ValidationError",
    "RateLimitedError",
    "ServerError",
    "ScenarioNotFound",
    "ScenarioFailed",
    "WaitTimeout",
]
