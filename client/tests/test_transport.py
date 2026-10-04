# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
import time
from email.utils import formatdate

import pytest
from studio_helpers import KEY, make_client

from ce_studio import (
    APIError,
    AuthError,
    NotFoundError,
    RateLimitedError,
    RetryPolicy,
    ServerError,
    TransportError,
)
from ce_studio.transport import parse_retry_after


def test_get_retries_transient_errors_then_succeeds(client, mock, sleeps):
    mock.inject("GET", "/api/scenario/queue", 503, times=2)
    assert client.list_scenarios() == []
    assert mock.count("GET", "/api/scenario/queue") == 3
    assert sleeps == [0.5, 1.0]  # backoff, no jitter in the test policy


def test_get_retries_an_empty_proxy_502(client, mock, sleeps):
    mock.inject("GET", "/api/scenario/queue", 502, body=b"")
    assert client.list_scenarios() == []
    assert len(sleeps) == 1


def test_retry_after_header_is_honoured(client, mock, sleeps):
    mock.inject("GET", "/api/scenario/queue", 429, headers={"Retry-After": "7"},
                body={"error": "rate limited", "status": 429})
    client.list_scenarios()
    assert sleeps == [7.0]


def test_long_retry_after_is_raised_not_waited(client, mock, sleeps):
    mock.inject("GET", "/api/scenario/queue", 429, headers={"Retry-After": "3600"},
                body={"error": "rate limited", "status": 429})
    with pytest.raises(RateLimitedError) as exc:
        client.list_scenarios()
    assert exc.value.retry_after == 3600 and exc.value.error == "rate limited"
    assert sleeps == []


def test_get_gives_up_after_max_attempts(client, mock, sleeps):
    mock.inject("GET", "/api/scenario/queue", 502, body=b"<html>bad gateway</html>", times=10)
    with pytest.raises(ServerError) as exc:
        client.list_scenarios()
    assert exc.value.status == 502
    assert "restarting or unreachable" in str(exc.value)
    assert mock.count("GET", "/api/scenario/queue") == 4
    assert len(sleeps) == 3


def test_submit_retried_after_a_lost_reply_is_queued_once(client, mock, toy_delta, sleeps):
    """The submission reference makes a submit safe to repeat after an ambiguous failure."""
    mock.inject("POST", "/api/scenario/submit", 502, body=b"", after=True)  # queued, but the reply is lost
    receipt = client.submit(toy_delta)
    assert mock.count("POST", "/api/scenario/submit") == 2 and len(sleeps) == 1
    assert receipt.duplicate and len(mock.submissions) == 1
    assert receipt.scenario_id == next(iter(mock.submissions))


def test_post_without_a_reference_is_not_retried_after_an_ambiguous_502(client, mock, sleeps):
    mock.inject("POST", "/api/scenario/withdraw/", 502, body=b"")
    with pytest.raises(ServerError):
        client.withdraw("scn-1")
    assert mock.count("POST", "/api/scenario/withdraw/") == 1
    assert sleeps == []


def test_submit_is_retried_when_rate_limited(client, mock, toy_delta, sleeps):
    mock.inject("POST", "/api/scenario/submit", 429, headers={"Retry-After": "2"},
                body={"error": "rate limited", "status": 429})
    receipt = client.submit(toy_delta)
    assert receipt.status == "queued"
    assert mock.count("POST", "/api/scenario/submit") == 2
    assert len(mock.submissions) == 1  # queued exactly once
    assert sleeps == [2.0]


def test_wrong_key_is_an_auth_error(mock, sleeps):
    c = make_client(mock.url, sleeps, api_key="not-the-key")
    with pytest.raises(AuthError) as exc:
        c.list_scenarios()
    assert exc.value.status == 401
    assert "invalid" in str(exc.value)
    assert sleeps == []


def test_html_404_becomes_not_found_with_excerpt(client, mock):
    with pytest.raises(NotFoundError) as exc:
        client.scope_manifest("no_such_region")
    assert exc.value.status == 404
    assert "Not Found" in exc.value.body_excerpt


def test_secrets_are_scrubbed_from_error_text(client, mock):
    mock.inject("GET", "/api/scenario/queue", 400,
                body={"error": f"bad header Authorization: Bearer {KEY} (key {KEY})", "status": 400})
    with pytest.raises(APIError) as exc:
        client.list_scenarios()
    assert KEY not in str(exc.value)
    assert KEY not in (exc.value.error or "")
    assert "Bearer ***" in str(exc.value)


def test_non_json_success_body_is_explained(client, mock):
    mock.inject("GET", "/api/scenario/queue", 200, body=b"")
    with pytest.raises(TransportError, match="empty body"):
        client.list_scenarios()
    mock.inject("GET", "/api/scenario/queue", 200, body=b"<html>maintenance</html>")
    with pytest.raises(TransportError, match="not JSON"):
        client.list_scenarios()


def test_cross_origin_redirect_is_not_followed(client, mock):
    mock.inject("GET", "/api/scenario/queue", 302,
                headers={"Location": "https://elsewhere.example.invalid/steal"})
    with pytest.raises(APIError) as exc:
        client.list_scenarios()
    assert exc.value.status == 302


def test_connection_refused_is_retried_then_raised(closed_port, sleeps):
    c = make_client(f"http://127.0.0.1:{closed_port}", sleeps)
    with pytest.raises(TransportError, match="cannot reach"):
        c.list_scenarios()
    assert len(sleeps) == 3


def test_connection_refused_on_post_is_retried_because_nothing_was_sent(closed_port, sleeps, tmp_path):
    from studio_helpers import delta_toml, write_delta

    path = write_delta(tmp_path / "d", delta_toml(buses="[1, 2, 3]"))  # integer ids: no bus listing needed
    c = make_client(f"http://127.0.0.1:{closed_port}", sleeps,
                    retry=RetryPolicy(max_attempts=2, jitter=0.0))
    with pytest.raises(TransportError):
        c.submit(path)
    assert len(sleeps) == 1


def test_parse_retry_after():
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("-3") == 0.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None
    now = time.time()
    when = formatdate(now + 30, usegmt=True)
    assert 25 <= parse_retry_after(when, now=now) <= 31
