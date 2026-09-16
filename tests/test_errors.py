from __future__ import annotations

import httpx
import pytest
import respx

from datamermaid import (
    AuthenticationError,
    MermaidAPIError,
    MermaidClient,
    MermaidConnectionError,
    NotFoundError,
    RateLimitError,
    ServerError,
)

from .conftest import BASE_URL


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AuthenticationError),
        (403, AuthenticationError),
        (404, NotFoundError),
        (429, RateLimitError),
        (500, ServerError),
        (503, ServerError),
        (400, MermaidAPIError),
    ],
)
@respx.mock
def test_status_codes_map_to_exceptions(client, status, expected):
    respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(status, json={"detail": "nope"}))
    with pytest.raises(expected) as excinfo:
        client.me()
    assert excinfo.value.status_code == status
    assert "nope" in str(excinfo.value)
    assert isinstance(excinfo.value, MermaidAPIError)


@respx.mock
def test_retryable_statuses_are_retried_then_raised(client):
    route = respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(429, json={}))
    with pytest.raises(RateLimitError):
        client.me()
    assert route.call_count == client.max_retries + 1


@respx.mock
def test_retry_succeeds_after_a_429(client):
    route = respx.get(f"{BASE_URL}me/").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"id": "abc"}),
        ]
    )
    assert client.me().id == "abc"
    assert route.call_count == 2


@respx.mock
def test_not_found_is_not_retried(client):
    route = respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(404, json={}))
    with pytest.raises(NotFoundError):
        client.me()
    assert route.call_count == 1


@respx.mock
def test_rate_limit_error_exposes_retry_after():
    respx.get(f"{BASE_URL}me/").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "12"})
    )
    # max_retries=0 so the 12 second Retry-After is reported, never slept on.
    with (
        MermaidClient(api_key="mmd_x.y", max_retries=0) as client,
        pytest.raises(RateLimitError) as excinfo,
    ):
        client.me()
    assert excinfo.value.retry_after == 12.0


def test_retry_after_accepts_an_http_date():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    from datamermaid.exceptions import parse_retry_after

    when = datetime.now(timezone.utc) + timedelta(seconds=30)
    delay = parse_retry_after(format_datetime(when, usegmt=True))
    assert delay is not None
    assert 25 <= delay <= 31
    # A date in the past means "retry now", not "unknown".
    past = format_datetime(datetime.now(timezone.utc) - timedelta(seconds=30), usegmt=True)
    assert parse_retry_after(past) == 0.0
    assert parse_retry_after("not a date") is None
    assert parse_retry_after(None) is None


@respx.mock
def test_transport_errors_are_wrapped_and_retried(client):
    route = respx.get(f"{BASE_URL}me/").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(MermaidConnectionError, match="boom"):
        client.me()
    assert route.call_count == client.max_retries + 1


@respx.mock
def test_retries_can_be_disabled():
    respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(500, json={}))
    with MermaidClient(api_key="mmd_x.y", max_retries=0) as client, pytest.raises(ServerError):
        client.me()
    assert respx.calls.call_count == 1


def test_negative_retries_are_rejected():
    with pytest.raises(ValueError, match="max_retries"):
        MermaidClient(max_retries=-1)


@respx.mock
def test_non_json_body_raises_a_clear_error(client):
    respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(200, text="<html>nope</html>"))
    with pytest.raises(MermaidConnectionError, match="non-JSON"):
        client.me()
