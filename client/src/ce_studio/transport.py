# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""HTTP transport: one place for auth, retries, backoff and error mapping.

Built on :mod:`urllib.request` so the package has no runtime dependencies.
"""

from __future__ import annotations

import email.utils
import json
import os
import random
import re
import socket
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

from . import __version__
from .config import ClientConfig
from .errors import APIError, ConfigError, TransportError, error_class_for

_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_CHUNK = 1 << 16

_UNAVAILABLE_HINT = (
    "the service is restarting or unreachable (a deploy in progress?) — try again in a minute"
)


@dataclass
class Response:
    """A completed HTTP exchange (status < 400)."""

    status: int
    headers: Mapping[str, str]
    body: bytes = b""
    path: Path | None = None  # set when the body was streamed to disk

    def json(self) -> Any:
        """Decode the body as JSON (the API's wire format)."""
        return decode_json(self.body, self.status)


def decode_json(body: bytes, status: int) -> Any:
    if not body.strip():
        raise TransportError(f"HTTP {status} with an empty body — {_UNAVAILABLE_HINT}")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransportError(
            f"HTTP {status} with a body that is not JSON — {_UNAVAILABLE_HINT}"
        ) from exc


def quote_segment(value: str) -> str:
    """Percent-encode one path segment (ids never contain a raw '/')."""
    value = str(value)
    if not value or value in (".", ".."):
        raise ConfigError(f"invalid path segment {value!r}")
    return quote(value, safe="")


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Seconds from a ``Retry-After`` header (delta-seconds or HTTP-date)."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(when.timestamp() - (time.time() if now is None else now), 0.0)


class Transport:
    """Sends requests for a :class:`StudioClient`.

    ``sleep`` and ``rng`` are injectable so tests run instantly and
    deterministically; ``opener`` lets tests or proxies replace urllib's.
    """

    def __init__(
        self,
        config: ClientConfig,
        *,
        sleep: Callable[[float], None] | None = None,
        rng: random.Random | None = None,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        self.config = config
        self._sleep = sleep or time.sleep
        self._rng = rng or random.Random()
        self._opener = opener or urllib.request.build_opener(_NoRedirectAuthLeak())

    # ------------------------------------------------------------ public

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        auth_required: bool = True,
        stream_to: Path | None = None,
        idempotent: bool = False,
    ) -> Response:
        """Send one request with retries; return a :class:`Response` or raise.

        ``stream_to`` writes a successful body to that path atomically
        (temporary file + rename) instead of holding it in memory.
        ``idempotent=True`` marks a POST that is safe to repeat (a submit that
        carries a submission reference: the service answers a repeat with the
        run it already queued), so it is retried like a GET.
        """
        base = self.config.check_target()
        key = self.config.require_key() if auth_required else self.config.api_key
        url = base + path
        hdrs = {
            "User-Agent": self.config.user_agent or f"ce-studio/{__version__} (python-urllib)",
            "Accept": "application/json, text/csv, application/octet-stream;q=0.9, */*;q=0.5",
        }
        if key:
            hdrs["Authorization"] = f"Bearer {key}"
        if headers:
            hdrs.update(headers)

        safe = idempotent or method.upper() in ("GET", "HEAD")
        policy = self.config.retry
        retry_statuses = policy.retry_statuses if safe else policy.retry_statuses_unsafe
        attempt = 0
        while True:
            attempt += 1
            last = attempt >= policy.max_attempts
            try:
                return self._send(method, url, path, body, hdrs, stream_to)
            except APIError as err:
                if last or err.status not in retry_statuses:
                    raise
                wait = err.retry_after
                if wait is not None and wait > policy.max_retry_after_s:
                    raise
                self._sleep(wait if wait is not None else policy.backoff_s(attempt, self._rng))
            except _NetworkFailure as fail:
                if last or not (safe or fail.never_sent):
                    raise TransportError(self._scrub(fail.message)) from fail.cause
                self._sleep(policy.backoff_s(attempt, self._rng))

    def get_json(self, path: str, *, auth_required: bool = True) -> Any:
        return self.request("GET", path, auth_required=auth_required).json()

    # ------------------------------------------------------------ internals

    def _send(
        self,
        method: str,
        url: str,
        path: str,
        body: bytes | None,
        headers: Mapping[str, str],
        stream_to: Path | None,
    ) -> Response:
        req = urllib.request.Request(url, data=body, method=method.upper(), headers=dict(headers))
        try:
            resp = self._opener.open(req, timeout=self.config.timeout_s)
        except urllib.error.HTTPError as exc:
            try:
                payload = exc.read()
            except Exception:  # pragma: no cover - broken connection mid-error
                payload = b""
            finally:
                exc.close()
            raise self._api_error(method, path, exc.code, exc.headers, payload) from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            never_sent = isinstance(reason, (ConnectionRefusedError, socket.gaierror))
            raise _NetworkFailure(
                f"{method} {path}: cannot reach the service ({reason})", never_sent, exc
            ) from None
        except (TimeoutError, socket.timeout) as exc:
            raise _NetworkFailure(
                f"{method} {path}: timed out after {self.config.timeout_s:g} s", False, exc
            ) from None
        except (ConnectionError, OSError) as exc:
            raise _NetworkFailure(f"{method} {path}: connection failed ({exc})", False, exc) from None

        with resp:
            status = resp.status
            hdrs = {k: v for k, v in resp.headers.items()}
            try:
                if stream_to is None:
                    return Response(status, hdrs, resp.read())
                return Response(status, hdrs, b"", _stream(resp, Path(stream_to)))
            except (TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
                raise _NetworkFailure(
                    f"{method} {path}: connection dropped while reading the body ({exc})", False, exc
                ) from None

    def _api_error(
        self, method: str, path: str, status: int, headers: Any, payload: bytes
    ) -> APIError:
        retry_after = parse_retry_after(headers.get("Retry-After") if headers else None)
        error: str | None = None
        code: str | None = None
        rows: list = []
        excerpt = ""
        doc: Any = None
        if payload.strip():
            try:
                doc = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                doc = None
        if isinstance(doc, Mapping):
            if doc.get("error") is not None:
                error = self._scrub(str(doc["error"]))
            if isinstance(doc.get("code"), str):
                code = doc["code"]
            if isinstance(doc.get("rows"), list):
                rows = [r for r in doc["rows"] if isinstance(r, Mapping)]
            if error is None and doc.get("message"):
                error = self._scrub(str(doc["message"]))
        elif payload.strip():
            excerpt = self._scrub(payload[:200].decode("utf-8", "replace")).strip()

        if error:
            text = error
        elif status in (502, 503, 504) or not payload.strip():
            text = _UNAVAILABLE_HINT if status >= 500 else "no error text in the reply"
        else:
            text = excerpt or "no error text in the reply"
        message = f"HTTP {status} on {method} {path}: {text}"
        if rows:
            message += f" ({len(rows)} row-level refusal{'s' if len(rows) != 1 else ''})"
        cls = error_class_for(status)
        return cls(
            message,
            status=status,
            error=error,
            code=code,
            rows=rows,
            retry_after=retry_after,
            body_excerpt=excerpt,
            method=method,
            path=path,
        )

    def _scrub(self, text: str) -> str:
        """Remove bearer tokens and the literal access token from text bound for an error."""
        out = _BEARER_RE.sub(r"\1***", text)
        key = self.config.api_key
        if key:
            out = out.replace(key, "***")
        return out


class _NetworkFailure(Exception):
    def __init__(self, message: str, never_sent: bool, cause: BaseException) -> None:
        super().__init__(message)
        self.message = message
        self.never_sent = never_sent
        self.cause = cause


class _NoRedirectAuthLeak(urllib.request.HTTPRedirectHandler):
    """Follow redirects only on the same origin, so the bearer header never leaks."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        from urllib.parse import urlsplit

        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if (old.scheme, old.netloc) != (new.scheme, new.netloc):
            return None  # surfaces as an HTTPError with the 3xx status
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _stream(resp: Any, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".part", dir=dest.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            while True:
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                fh.write(chunk)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return dest


def path_for(template: str, **params: str) -> str:
    """Fill a route template, percent-encoding every parameter."""
    return template.format(**{k: quote_segment(v) for k, v in params.items()})
