# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Client configuration: where the service is, how to authenticate, how to retry.

Nothing here talks to the network. :meth:`ClientConfig.from_env` reads:

``CE_STUDIO_BASE_URL``
    The service root, e.g. ``https://studio.example.com``. There is no real
    default: the built-in value is a reserved ``.invalid`` placeholder and
    any request against it raises :class:`~ce_studio.errors.ConfigError`.
``CE_STUDIO_API_KEY``
    The account's access token, sent as ``Authorization: Bearer <token>``.
    Tokens are issued with a Studio account (there is no self-serve issuance
    yet). Never logged and never shown by ``repr``; scrubbed from any error
    text.
``CE_STUDIO_TIMEOUT``
    Per-request timeout in seconds (default 30).
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field, replace
from typing import Mapping
from urllib.parse import urlsplit

from .errors import ConfigError

PLACEHOLDER_BASE_URL = "https://studio.example.invalid"
ENV_BASE_URL = "CE_STUDIO_BASE_URL"
ENV_API_KEY = "CE_STUDIO_API_KEY"
ENV_TIMEOUT = "CE_STUDIO_TIMEOUT"

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class RetryPolicy:
    """Retries with capped exponential backoff and jitter.

    A request is retried only when repeating it cannot do harm:

    * **Safe requests** (GET) retry on ``retry_statuses`` and on any
      connection-level failure.
    * **Unsafe requests** (POST submit / withdraw) retry only on
      ``retry_statuses_unsafe`` — statuses that mean "not processed" (429
      rate limit, 503 unavailable) — and on failures where the request
      provably never left (connection refused, name resolution). A timeout
      or a 502/504 after a POST is ambiguous, so it is raised, not repeated.

    A ``Retry-After`` header is honoured; one longer than
    ``max_retry_after_s`` is not waited out — the error is raised with
    ``retry_after`` set so the caller can decide.
    """

    max_attempts: int = 5
    base_delay_s: float = 0.5
    multiplier: float = 2.0
    max_delay_s: float = 30.0
    jitter: float = 0.2
    max_retry_after_s: float = 120.0
    retry_statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504})
    retry_statuses_unsafe: frozenset[int] = frozenset({429, 503})

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ConfigError("RetryPolicy.max_attempts must be >= 1")
        if self.base_delay_s < 0 or self.max_delay_s < 0 or self.multiplier < 1:
            raise ConfigError("RetryPolicy delays must be >= 0 and multiplier >= 1")
        if not 0 <= self.jitter <= 1:
            raise ConfigError("RetryPolicy.jitter must lie in [0, 1]")

    def backoff_s(self, attempt: int, rng: random.Random | None = None) -> float:
        """Delay before retry number ``attempt`` (1-based), jittered and capped."""
        raw = self.base_delay_s * self.multiplier ** max(attempt - 1, 0)
        delay = min(raw, self.max_delay_s)
        if self.jitter and delay > 0:
            r = (rng or random).uniform(-self.jitter, self.jitter)
            delay = min(max(delay * (1 + r), 0.0), self.max_delay_s)
        return delay


@dataclass(frozen=True)
class Routes:
    """URL path templates for each endpoint.

    They are data, not code, so a deployment that mounts the API elsewhere
    needs no client change — pass ``Routes(results="/v1/runs/{sid}/{fname}")``
    etc. The JSON documents these routes serve are the API's wire format; the
    client turns them into typed objects and never writes them to disk.
    """

    submit: str = "/api/scenario/submit"
    withdraw: str = "/api/scenario/withdraw/{sid}"
    queue: str = "/api/scenario/queue"
    results: str = "/api/scenario/results/{sid}/{fname}"
    scopes_index: str = "/data/scenario_studio/served.json"
    scope_manifest: str = "/data/scenario_studio/{scope_id}/manifest.json"
    scope_buses: str = "/data/scenario_studio/{scope_id}/buses.geojson"


@dataclass(frozen=True)
class ClientConfig:
    """Everything the client needs to reach the service.

    ``allow_insecure_http`` permits plain ``http://`` to a non-loopback host.
    It is off by default because the access token travels in a header; loopback
    hosts (local mocks, tunnels) are always allowed.
    """

    base_url: str = PLACEHOLDER_BASE_URL
    api_key: str | None = field(default=None, repr=False)
    timeout_s: float = 30.0
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    routes: Routes = field(default_factory=Routes)
    allow_insecure_http: bool = False
    user_agent: str = ""

    def __repr__(self) -> str:  # never print the key
        key = "***" if self.api_key else None
        return (
            f"ClientConfig(base_url={self.base_url!r}, api_key={key!r}, "
            f"timeout_s={self.timeout_s!r}, allow_insecure_http={self.allow_insecure_http!r})"
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, **overrides) -> "ClientConfig":
        """Build a config from environment variables; keyword overrides win.

        An override of ``None`` is ignored, so CLI flags can be passed
        through unconditionally.
        """
        env = os.environ if environ is None else environ
        values: dict = {}
        if env.get(ENV_BASE_URL):
            values["base_url"] = env[ENV_BASE_URL].strip()
        if env.get(ENV_API_KEY):
            values["api_key"] = env[ENV_API_KEY].strip()
        if env.get(ENV_TIMEOUT):
            try:
                values["timeout_s"] = float(env[ENV_TIMEOUT])
            except ValueError as exc:
                raise ConfigError(f"{ENV_TIMEOUT} must be a number of seconds") from exc
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)

    def with_(self, **changes) -> "ClientConfig":
        """A copy with some fields replaced."""
        return replace(self, **changes)

    def check_target(self) -> str:
        """Validate the base URL for sending a request; return it without a trailing slash."""
        url = (self.base_url or "").strip().rstrip("/")
        if not url or url == PLACEHOLDER_BASE_URL:
            raise ConfigError(
                f"no service URL configured — set {ENV_BASE_URL} (or pass base_url=...); "
                f"{PLACEHOLDER_BASE_URL} is a placeholder"
            )
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ConfigError(f"base URL must be http(s)://host[:port][/prefix], got {url!r}")
        if parts.query or parts.fragment:
            raise ConfigError("base URL must not carry a query string or fragment")
        if (
            parts.scheme == "http"
            and parts.hostname not in _LOOPBACK_HOSTS
            and not self.allow_insecure_http
        ):
            raise ConfigError(
                "refusing plain http:// to a non-loopback host (the access token would travel "
                "unencrypted); use https:// or set allow_insecure_http=True"
            )
        if self.timeout_s <= 0:
            raise ConfigError("timeout_s must be > 0")
        return url

    def require_key(self) -> str:
        """The access token, or a :class:`ConfigError` naming the variable to set."""
        if not self.api_key:
            raise ConfigError(f"this call needs an access token — set {ENV_API_KEY}")
        return self.api_key
