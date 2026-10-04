# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Exception hierarchy.

Every exception raised on purpose by this package derives from
:class:`StudioError`, so ``except StudioError`` is a complete catch.

HTTP failures become an :class:`APIError` subclass chosen by status code.
The service answers every ``/api/*`` error as ``{"error": ..., "status": N}``
(and a 422 may add ``"rows"`` — row-level refusals); those fields are kept on
the exception so callers never need to re-parse the body.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:  # pragma: no cover
    from .models import Scenario
    from .refusals import ParsedDetail, Refusal


class StudioError(Exception):
    """Base class for every error this package raises."""


class ConfigError(StudioError):
    """The client is not configured well enough to make the request.

    Raised for a missing access token, the placeholder base URL, an unparseable
    URL, or plain ``http://`` to a non-loopback host.
    """


class LocalValidationError(StudioError):
    """A delta bundle failed the client-side pre-check; nothing was sent.

    ``refusals`` holds the blocking findings (warnings are not included).
    The service remains the authority — the pre-check only catches the
    cheap, certain cases before a network round trip.
    """

    def __init__(self, message: str, refusals: Sequence["Refusal"] = ()) -> None:
        super().__init__(message)
        self.refusals = list(refusals)


class TransportError(StudioError):
    """The request could not be completed at the network level (after retries)."""


class APIError(StudioError):
    """The service answered with an HTTP error status.

    Attributes:
        status: the HTTP status code.
        error: the server's ``error`` field (a refusal code or a sentence), if any.
        code: the server's ``code`` field (a refusal code), if any.
        rows: row-level refusal records from the body (422 answers), if any.
        retry_after: seconds from a ``Retry-After`` header, if any.
        body_excerpt: a short, secret-scrubbed excerpt of a non-JSON body.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        error: str | None = None,
        code: str | None = None,
        rows: Sequence[Mapping[str, Any]] = (),
        retry_after: float | None = None,
        body_excerpt: str = "",
        method: str = "",
        path: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error = error
        self.code = code
        self.rows = [dict(r) for r in rows]
        self.retry_after = retry_after
        self.body_excerpt = body_excerpt
        self.method = method
        self.path = path

    @property
    def refusals(self) -> list["Refusal"]:
        """Row-level refusals carried by the body, mapped through the catalog.

        When the body has no ``rows`` but carries a refusal code — its
        ``code`` field, or an ``error`` field that is itself a code (e.g.
        ``base_run_missing``) — that single code is returned, with the
        ``error`` sentence as its message. A row without its own ``code``
        inherits the top-level one.
        """
        from .refusals import Refusal, from_row, looks_like_code

        default = None
        for cand in (self.code, self.error):
            if cand and looks_like_code(cand):
                default = cand
                break
        if self.rows:
            return [from_row(r, default_code=default) for r in self.rows]
        if default:
            message = self.error if self.error and self.error != default else ""
            return [Refusal.of(default, message)]
        return []


class BadRequestError(APIError):
    """HTTP 400 — a malformed request (e.g. an invalid id)."""


class AuthError(APIError):
    """HTTP 401/403 — the access token is missing, wrong, expired, or lacks access."""


class NotFoundError(APIError):
    """HTTP 404 — no such resource (or results not published yet)."""


class ConflictError(APIError):
    """HTTP 409 — e.g. withdrawing a scenario the service has already started."""


class PayloadTooLargeError(APIError):
    """HTTP 413 — the upload exceeds the service's size cap."""


class ValidationError(APIError):
    """HTTP 422 — the service refused the submission; see :attr:`refusals`."""


class RateLimitedError(APIError):
    """HTTP 429 — too many requests; see :attr:`retry_after`."""


class ServerError(APIError):
    """HTTP 5xx — the service failed or is restarting."""


class ScenarioNotFound(StudioError):
    """The scenario id is not in this account's queue listing."""

    def __init__(self, scenario_id: str) -> None:
        super().__init__(
            f"scenario {scenario_id!r} is not in the queue listing for this account "
            "(wrong id, another account's run, or retired from the listing)"
        )
        self.scenario_id = scenario_id


class ScenarioFailed(StudioError):
    """A waited-on scenario reached ``failed`` (or ``withdrawn``).

    ``scenario`` is the final queue row; ``parsed`` the refusals recovered
    from its ``detail`` (see :func:`ce_studio.refusals.parse_detail`).
    """

    def __init__(self, scenario: "Scenario", parsed: "ParsedDetail") -> None:
        lines = [f"scenario {scenario.scenario_id} ended {scenario.status}"]
        for r in parsed.refusals[:10]:
            lines.append(f"  - {r}")
        if parsed.omitted:
            lines.append(f"  (+{parsed.omitted} more not shown by the service)")
        super().__init__("\n".join(lines))
        self.scenario = scenario
        self.parsed = parsed

    @property
    def refusals(self) -> list["Refusal"]:
        return list(self.parsed.refusals)


class WaitTimeout(StudioError):
    """:meth:`StudioClient.wait` gave up before the scenario finished."""

    def __init__(self, scenario: "Scenario", timeout_s: float) -> None:
        super().__init__(
            f"scenario {scenario.scenario_id} still {scenario.status} after {timeout_s:g} s"
        )
        self.scenario = scenario
        self.timeout_s = timeout_s


_STATUS_CLASSES: dict[int, type[APIError]] = {
    400: BadRequestError,
    401: AuthError,
    403: AuthError,
    404: NotFoundError,
    409: ConflictError,
    413: PayloadTooLargeError,
    422: ValidationError,
    429: RateLimitedError,
}


def error_class_for(status: int) -> type[APIError]:
    """The :class:`APIError` subclass for an HTTP status code."""
    if status in _STATUS_CLASSES:
        return _STATUS_CLASSES[status]
    if status >= 500:
        return ServerError
    return APIError
