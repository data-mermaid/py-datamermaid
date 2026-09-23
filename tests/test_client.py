from __future__ import annotations

import threading

import httpx
import pytest
import respx

from datamermaid import DEFAULT_BASE_URL, MermaidClient
from datamermaid.client import DEV_BASE_URL, default_user_agent, resolve_base_url

from .conftest import BASE_URL


def test_default_base_url():
    assert resolve_base_url() == DEFAULT_BASE_URL


def test_base_url_from_environment(monkeypatch):
    monkeypatch.setenv("MERMAID_API_URL", DEV_BASE_URL)
    assert MermaidClient().base_url == DEV_BASE_URL


def test_explicit_base_url_wins_over_environment(monkeypatch):
    monkeypatch.setenv("MERMAID_API_URL", DEV_BASE_URL)
    assert MermaidClient(base_url="https://example.test/v1/").base_url == "https://example.test/v1/"


def test_base_url_gets_a_trailing_slash():
    assert resolve_base_url("https://example.test/v1") == "https://example.test/v1/"


def test_user_agent_mentions_the_sdk_and_httpx():
    agent = default_user_agent()
    assert agent.startswith("datamermaid/")
    assert "httpx/" in agent


@respx.mock
def test_requests_use_the_configured_base_url():
    route = respx.get("https://example.test/v1/me/").mock(
        return_value=httpx.Response(200, json={"id": "1"})
    )
    with MermaidClient(base_url="https://example.test/v1", api_key="mmd_x.y") as client:
        client.me()
    assert route.called


@respx.mock
def test_me_parses_the_profile(client):
    respx.get(f"{BASE_URL}me/").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "33333333-3333-3333-3333-333333333333",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "full_name": "Ada Lovelace",
                "email": "ada@example.test",
                "picture": "https://example.test/ada.png",
                "created_on": "2024-01-02T03:04:05.678901Z",
                "updated_on": "2024-02-02T03:04:05.678901Z",
                "projects": [
                    {
                        "id": "44444444-4444-4444-4444-444444444444",
                        "name": "Reef Watch",
                        "role": 90,
                        "num_active_sample_units": 2,
                    }
                ],
            },
        )
    )
    me = client.me()
    assert me.full_name == "Ada Lovelace"
    assert me.email == "ada@example.test"
    assert me.created_on is not None
    assert me.created_on.year == 2024
    assert len(me.projects) == 1
    assert me.projects[0].name == "Reef Watch"
    assert me.projects[0].role == 90


@respx.mock
def test_custom_headers_are_sent():
    route = respx.get(f"{BASE_URL}me/").mock(return_value=httpx.Response(200, json={"id": "1"}))
    with MermaidClient(api_key="mmd_x.y", headers={"X-Trace": "abc"}) as client:
        client.me()
    assert route.calls.last.request.headers["X-Trace"] == "abc"


def test_context_manager_closes_the_transport():
    with MermaidClient(api_key="mmd_x.y") as client:
        assert not client._http.is_closed
    assert client._http.is_closed


def test_projects_resource_is_cached():
    with MermaidClient(api_key="mmd_x.y") as client:
        assert client.projects is client.projects


# -- the cooperative throttle gate -------------------------------------------


class FakeClock:
    """A controllable `time.monotonic` and a `time.sleep` that only advances it."""

    def __init__(self, now=1000.0):
        self.now = now
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr("datamermaid.client.time.monotonic", fake.monotonic)
    monkeypatch.setattr("datamermaid.client.time.sleep", fake.sleep)
    return fake


def test_a_fresh_client_is_not_throttled(clock):
    with MermaidClient() as client:
        assert client._throttled_until == 0.0
        client._wait_if_throttled()
    assert clock.sleeps == []


def test_note_throttle_sets_a_deadline_every_request_waits_on(clock):
    with MermaidClient() as client:
        client._note_throttle(2.0)
        assert client._throttled_until == 1002.0

        client._wait_if_throttled()
        assert clock.sleeps == [2.0]
        assert clock.now == 1002.0

        # Once the deadline has passed nothing sleeps.
        client._wait_if_throttled()
        assert clock.sleeps == [2.0]


def test_note_throttle_keeps_the_later_deadline(clock):
    with MermaidClient() as client:
        client._note_throttle(5.0)
        client._note_throttle(1.0)
        assert client._throttled_until == 1005.0
        clock.now = 1003.0
        client._wait_if_throttled()
    assert clock.sleeps == [2.0]


def test_a_deadline_extended_during_the_sleep_is_waited_out_too(clock, monkeypatch):
    with MermaidClient() as client:
        client._note_throttle(10.0)

        def sleep(seconds):
            clock.sleep(seconds)
            if len(clock.sleeps) == 1:
                # Another worker's 429 lands while this one sleeps.
                client._note_throttle(20.0)

        monkeypatch.setattr("datamermaid.client.time.sleep", sleep)
        client._wait_if_throttled()

    assert clock.sleeps == [10.0, 20.0]
    assert clock.now == 1030.0


def test_a_zero_delay_does_not_throttle(clock):
    with MermaidClient() as client:
        client._note_throttle(0.0)
        client._wait_if_throttled()
    assert clock.sleeps == []


@respx.mock
def test_a_429_records_the_retry_after_as_the_throttle_deadline(clock):
    route = respx.get(f"{BASE_URL}projects/").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"count": 0, "next": None, "results": []}),
        ]
    )
    with MermaidClient(backoff_factor=0.0) as client:
        client.request("GET", "projects/")
        # The retry slept the delay out, so the deadline is already behind us.
        assert client._throttled_until == 1002.0
        assert clock.now >= 1002.0
    assert route.call_count == 2
    assert clock.sleeps == [2.0]


@respx.mock
def test_a_5xx_retries_without_throttling_other_requests(clock):
    respx.get(f"{BASE_URL}projects/").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={})]
    )
    with MermaidClient(backoff_factor=0.5) as client:
        client.request("GET", "projects/")
        assert client._throttled_until == 0.0
    assert len(clock.sleeps) == 1


@respx.mock
def test_a_429_on_one_thread_makes_another_thread_wait(monkeypatch):
    """The scenario the gate exists for: two workers, one 429, both back off."""

    lock = threading.Lock()
    clock = FakeClock()
    sends = []

    def monotonic():
        with lock:
            return clock.now

    def sleep(seconds):
        with lock:
            clock.sleeps.append(seconds)
            clock.now += seconds

    monkeypatch.setattr("datamermaid.client.time.monotonic", monotonic)
    monkeypatch.setattr("datamermaid.client.time.sleep", sleep)

    # Thread A's first attempt is answered 429 while thread B is held at the
    # door.  B only knocks after A has noted the throttle, so B must wait.
    throttle_noted = threading.Event()
    b_may_send = threading.Event()

    def respond(request):
        with lock:
            sends.append((request.url.path, clock.now))
        if request.url.path == "/v1/a/" and not throttle_noted.is_set():
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={})

    respx.get(f"{BASE_URL}a/").mock(side_effect=respond)
    respx.get(f"{BASE_URL}b/").mock(side_effect=respond)

    with MermaidClient(backoff_factor=0.0) as client:
        original_note = client._note_throttle

        def note_and_release(delay):
            original_note(delay)
            throttle_noted.set()
            b_may_send.wait(5.0)

        monkeypatch.setattr(client, "_note_throttle", note_and_release)

        def run_a():
            client.request("GET", "a/")

        def run_b():
            throttle_noted.wait(5.0)
            client.request("GET", "b/")

        a = threading.Thread(target=run_a)
        b = threading.Thread(target=run_b)
        a.start()
        b.start()
        # B waits at the gate (sleeping on the fake clock) before A's own sleep.
        b.join(5.0)
        b_may_send.set()
        a.join(5.0)
        assert not a.is_alive() and not b.is_alive()

    first_a = next(at for path, at in sends if path == "/v1/a/")
    sent_b = next(at for path, at in sends if path == "/v1/b/")
    retry_a = [at for path, at in sends if path == "/v1/a/"][1]
    assert 2.0 in clock.sleeps
    assert sent_b >= first_a + 2.0
    assert retry_a >= first_a + 2.0
