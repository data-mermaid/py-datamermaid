from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from datamermaid.auth import OAuth
from datamermaid.auth.flows import DEVICE_GRANT_TYPE, DeviceFlow, ManualPasteFlow
from datamermaid.exceptions import AuthFlowError, AuthTimeoutError

from .conftest import DEVICE_CODE_URL, TOKEN_URL, Recorder

DEVICE_RESPONSE = {
    "device_code": "device-code",
    "user_code": "WDJB-MJHT",
    "verification_uri": "https://datamermaid.auth0.com/activate",
    "verification_uri_complete": "https://datamermaid.auth0.com/activate?user_code=WDJB-MJHT",
    "expires_in": 900,
    "interval": 5,
}

TOKEN_RESPONSE = {
    "access_token": "device-access-token",
    "refresh_token": "device-refresh-token",
    "expires_in": 86400,
}


class FakeClock:
    """A monotonic clock that only moves when something sleeps on it."""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def device_auth(recorder, clock, **kwargs):
    return OAuth(
        flow="device",
        printer=recorder.write,
        clock=clock,
        sleep=clock.sleep,
        cache=False,
        **kwargs,
    )


@respx.mock
def test_device_code_request_asks_for_the_api_audience():
    clock = FakeClock()
    recorder = Recorder()
    route = respx.post(DEVICE_CODE_URL).mock(return_value=httpx.Response(200, json=DEVICE_RESPONSE))
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))

    device_auth(recorder, clock).login()

    sent = {k: v[0] for k, v in parse_qs(route.calls.last.request.content.decode()).items()}
    assert sent["audience"] == "https://api.datamermaid.org"
    assert "offline_access" in sent["scope"]


@respx.mock
def test_the_user_code_and_url_are_shown():
    clock = FakeClock()
    recorder = Recorder()
    respx.post(DEVICE_CODE_URL).mock(return_value=httpx.Response(200, json=DEVICE_RESPONSE))
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))

    device_auth(recorder, clock).login()

    assert "WDJB-MJHT" in recorder.text
    assert "https://datamermaid.auth0.com/activate" in recorder.text


@respx.mock
def test_polling_waits_for_the_user_and_returns_the_token():
    clock = FakeClock()
    recorder = Recorder()
    respx.post(DEVICE_CODE_URL).mock(return_value=httpx.Response(200, json=DEVICE_RESPONSE))
    route = respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(403, json={"error": "authorization_pending"}),
            httpx.Response(403, json={"error": "authorization_pending"}),
            httpx.Response(200, json=TOKEN_RESPONSE),
        ]
    )

    tokens = device_auth(recorder, clock).login()

    assert tokens.access_token == "device-access-token"
    assert route.call_count == 3
    assert clock.slept == [5.0, 5.0, 5.0]  # the advertised interval, honoured
    sent = {k: v[0] for k, v in parse_qs(route.calls.last.request.content.decode()).items()}
    assert sent["grant_type"] == DEVICE_GRANT_TYPE
    assert sent["device_code"] == "device-code"


@respx.mock
def test_slow_down_lengthens_the_polling_interval():
    clock = FakeClock()
    recorder = Recorder()
    respx.post(DEVICE_CODE_URL).mock(return_value=httpx.Response(200, json=DEVICE_RESPONSE))
    respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(429, json={"error": "slow_down"}),
            httpx.Response(200, json=TOKEN_RESPONSE),
        ]
    )

    device_auth(recorder, clock).login()

    assert clock.slept == [5.0, 10.0]


@respx.mock
def test_the_default_interval_is_used_when_the_tenant_omits_one():
    clock = FakeClock()
    recorder = Recorder()
    response = {key: value for key, value in DEVICE_RESPONSE.items() if key != "interval"}
    respx.post(DEVICE_CODE_URL).mock(return_value=httpx.Response(200, json=response))
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))

    device_auth(recorder, clock).login()

    assert clock.slept == [5.0]


@respx.mock
def test_polling_stops_when_the_device_code_expires():
    clock = FakeClock()
    recorder = Recorder()
    respx.post(DEVICE_CODE_URL).mock(
        return_value=httpx.Response(200, json={**DEVICE_RESPONSE, "expires_in": 12})
    )
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(403, json={"error": "authorization_pending"})
    )

    with pytest.raises(AuthTimeoutError, match="device code"):
        device_auth(recorder, clock).login()

    assert route.call_count == 3  # 5s, 10s, then the 12s deadline has passed


@respx.mock
def test_a_denied_request_is_not_retried():
    clock = FakeClock()
    recorder = Recorder()
    respx.post(DEVICE_CODE_URL).mock(return_value=httpx.Response(200, json=DEVICE_RESPONSE))
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            403, json={"error": "access_denied", "error_description": "User declined"}
        )
    )

    with pytest.raises(AuthFlowError, match="access_denied: User declined"):
        device_auth(recorder, clock).login()

    assert route.call_count == 1


@respx.mock
def test_an_expired_device_code_error_is_reported():
    clock = FakeClock()
    recorder = Recorder()
    respx.post(DEVICE_CODE_URL).mock(return_value=httpx.Response(200, json=DEVICE_RESPONSE))
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(403, json={"error": "expired_token"}))

    with pytest.raises(AuthFlowError, match="expired_token"):
        device_auth(recorder, clock).login()


@respx.mock
def test_an_incomplete_device_response_is_reported():
    clock = FakeClock()
    recorder = Recorder()
    respx.post(DEVICE_CODE_URL).mock(return_value=httpx.Response(200, json={"user_code": "X"}))

    with pytest.raises(AuthFlowError, match="incomplete"):
        device_auth(recorder, clock).login()


# -- automatic flow selection --------------------------------------------


@respx.mock
def test_auto_uses_the_device_grant_on_a_headless_machine(monkeypatch):
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.1 22 10.0.0.2 22")
    clock = FakeClock()
    recorder = Recorder()
    device = respx.post(DEVICE_CODE_URL).mock(
        return_value=httpx.Response(200, json=DEVICE_RESPONSE)
    )
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))

    auth = OAuth(printer=recorder.write, clock=clock, sleep=clock.sleep, cache=False)
    assert auth.login().access_token == "device-access-token"
    # The probe doubles as the flow's own request: one code, shown once.
    assert device.call_count == 1


@respx.mock
def test_auto_falls_back_to_pasting_when_the_device_grant_is_disabled(monkeypatch):
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.1 22 10.0.0.2 22")
    recorder = Recorder()
    respx.post(DEVICE_CODE_URL).mock(
        return_value=httpx.Response(403, json={"error": "unauthorized_client"})
    )
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))

    def answer(_message):
        # The user copies the address the browser could not load, state and all.
        state = parse_qs(urlsplit(recorder.printed[1].strip()).query)["state"][0]
        return f"http://localhost:1410/?code=pasted-code&state={state}"

    auth = OAuth(
        printer=recorder.write,
        prompt=answer,
        cache=False,
    )
    assert isinstance(auth._select_flow(auth._context(httpx.Client())), ManualPasteFlow)
    assert auth.login().access_token == "device-access-token"


def test_auto_prefers_the_browser_when_one_is_reachable(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    auth = OAuth(cache=False)
    flow = auth._select_flow(auth._context(httpx.Client()))
    assert flow.name == "pkce"


def test_headless_detection(monkeypatch):
    from datamermaid.auth import is_headless

    monkeypatch.setenv("SSH_CONNECTION", "x")
    assert is_headless() is True

    monkeypatch.delenv("SSH_CONNECTION")
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert is_headless() is True

    monkeypatch.setenv("DISPLAY", ":0")
    assert is_headless() is False

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.delenv("DISPLAY")
    assert is_headless() is False


def test_a_prefetched_device_code_is_not_requested_twice():
    flow = DeviceFlow({"device_code": "d", "user_code": "u"})
    assert flow._authorization["device_code"] == "d"


# -- the manual paste fallback -------------------------------------------


def paste_auth(recorder, answers, **kwargs):
    answers = iter(answers)
    return OAuth(
        flow="manual",
        printer=recorder.write,
        prompt=lambda _message: next(answers),
        cache=False,
        **kwargs,
    )


@respx.mock
def test_a_pasted_redirect_url_completes_the_flow():
    recorder = Recorder()
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
    auth = paste_auth(recorder, ["ignored"])

    def answer(_message):
        # The user copies the address the browser could not load.
        state = parse_qs(urlsplit(recorder.printed[1].strip()).query)["state"][0]
        return f"http://localhost:1410/?code=pasted-code&state={state}"

    auth._prompt = answer
    tokens = auth.login()

    assert tokens.access_token == "device-access-token"
    sent = {k: v[0] for k, v in parse_qs(route.calls.last.request.content.decode()).items()}
    assert sent["code"] == "pasted-code"
    assert sent["redirect_uri"] == "http://localhost:1410/"
    assert "code_verifier" in sent


@respx.mock
def test_a_bare_code_may_be_pasted_instead_of_the_url():
    recorder = Recorder()
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))

    paste_auth(recorder, ["just-the-code"]).login()

    sent = {k: v[0] for k, v in parse_qs(route.calls.last.request.content.decode()).items()}
    assert sent["code"] == "just-the-code"


def test_the_authorization_url_is_printed_for_the_user_to_open():
    recorder = Recorder()
    with pytest.raises(AuthFlowError):
        paste_auth(recorder, [""]).login()
    assert "https://datamermaid.auth0.com/authorize" in recorder.text


def test_a_pasted_url_with_the_wrong_state_is_rejected():
    recorder = Recorder()
    with pytest.raises(AuthFlowError, match="state mismatch"):
        paste_auth(recorder, ["http://localhost:1410/?code=c&state=wrong"]).login()


def test_a_pasted_error_redirect_is_reported():
    recorder = Recorder()
    with pytest.raises(AuthFlowError, match="access_denied"):
        paste_auth(recorder, ["http://localhost:1410/?error=access_denied"]).login()


def test_the_redirect_port_can_be_pinned():
    recorder = Recorder()
    with pytest.raises(AuthFlowError):
        paste_auth(recorder, [""], redirect_port=9999).login()
    assert "localhost%3A9999" in recorder.text


def test_a_pasted_url_without_a_state_is_rejected():
    recorder = Recorder()
    with pytest.raises(AuthFlowError, match="no state"):
        paste_auth(recorder, ["http://localhost:1410/?code=c"]).login()
