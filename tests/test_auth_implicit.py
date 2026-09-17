from __future__ import annotations

import threading
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest
import respx

from datamermaid.auth import OAuth, parse_fragment
from datamermaid.auth.callback_server import (
    FRAGMENT_PATH,
    LoopbackCallbackServer,
    parse_redirect,
)
from datamermaid.exceptions import AuthFlowError, AuthTimeoutError

from .conftest import TOKEN_URL, FakeServer, Recorder, make_jwt

# -- fragment parsing -----------------------------------------------------


def test_fragment_of_a_whole_redirect_url_is_parsed():
    params = parse_fragment(
        "http://localhost:8123/#access_token=abc&expires_in=86400&token_type=Bearer&state=s"
    )
    assert params == {
        "access_token": "abc",
        "expires_in": "86400",
        "token_type": "Bearer",
        "state": "s",
    }


def test_a_bare_fragment_is_parsed():
    assert parse_fragment("#access_token=abc")["access_token"] == "abc"
    assert parse_fragment("access_token=abc")["access_token"] == "abc"
    assert parse_fragment("?access_token=abc")["access_token"] == "abc"


def test_an_empty_fragment_parses_to_nothing():
    assert parse_fragment("http://localhost:8123/#") == {}
    assert parse_fragment("") == {}


def test_an_error_fragment_is_parsed():
    params = parse_fragment("#error=unauthorized&error_description=Access%20denied")
    assert params == {"error": "unauthorized", "error_description": "Access denied"}


def test_a_pasted_redirect_is_parsed_from_either_half():
    assert parse_redirect("http://localhost:1410/?code=abc&state=s")["code"] == "abc"
    assert parse_redirect("http://localhost:1410/#access_token=abc")["access_token"] == "abc"
    assert parse_redirect("code=abc&state=s")["code"] == "abc"


# -- the implicit flow ----------------------------------------------------


def implicit_auth(server, recorder, **kwargs):
    return OAuth(
        flow="implicit",
        server_factory=server,
        browser_opener=recorder.opener,
        printer=recorder.write,
        **kwargs,
    )


def echoing_opener(server, params):
    """A browser that hands the fragment back, state included."""

    recorder = Recorder()

    def opener(url):
        server.params.update(params)
        server.params["state"] = parse_qs(urlsplit(url).query)["state"][0]
        return recorder.open(url)

    recorder.opener = opener
    return recorder


def test_the_token_is_read_straight_out_of_the_fragment():
    token = make_jwt(expires_in=7200)
    server = FakeServer()
    recorder = echoing_opener(server, {"access_token": token, "expires_in": "7200"})

    tokens = implicit_auth(server, recorder, cache=False).login()

    assert tokens.access_token == token
    assert tokens.refresh_token is None
    assert server.mode == "fragment"


def test_the_implicit_request_asks_for_a_token_and_no_refresh():
    server = FakeServer()
    recorder = echoing_opener(server, {"access_token": "tok"})

    implicit_auth(server, recorder, cache=False).login()

    query = {k: v[0] for k, v in parse_qs(urlsplit(recorder.opened[0]).query).items()}
    assert query["response_type"] == "token"
    assert "offline_access" not in query["scope"]
    assert "nonce" in query
    assert "code_challenge" not in query


def test_a_mismatched_state_is_rejected():
    server = FakeServer({"access_token": "tok", "state": "wrong"})
    recorder = Recorder()
    recorder.opener = recorder.open

    with pytest.raises(AuthFlowError, match="state mismatch"):
        implicit_auth(server, recorder, cache=False).login()


def test_a_fragment_without_a_token_is_rejected():
    server = FakeServer()
    recorder = echoing_opener(server, {})

    with pytest.raises(AuthFlowError, match="access_token"):
        implicit_auth(server, recorder, cache=False).login()


@respx.mock
def test_the_implicit_token_is_cached_like_any_other(tmp_path):
    from datamermaid.auth import TokenCache

    cache = TokenCache(tmp_path / "tokens.json")
    server = FakeServer()
    recorder = echoing_opener(server, {"access_token": "tok", "expires_in": "3600"})
    auth = implicit_auth(server, recorder, cache=cache)

    auth.login()

    assert cache.load(auth.config.cache_key).access_token == "tok"
    assert respx.calls.call_count == 0  # no token endpoint request at all
    assert TOKEN_URL  # the implicit grant never reaches it


# -- the real loopback server ---------------------------------------------


def fetch(url):
    """Fetch ``url`` over loopback, returning the body even for a 404."""

    try:
        with urllib.request.urlopen(url) as response:
            return response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.read().decode()


def drive(server, paths):
    """Hit ``paths`` on ``server`` from another thread while it waits."""

    bodies = []

    def run():
        for path in paths:
            bodies.append(fetch(f"http://127.0.0.1:{server.port}{path}"))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        return server.wait(), bodies
    finally:
        thread.join(timeout=5)


def test_the_query_callback_is_captured():
    with LoopbackCallbackServer(timeout=5) as server:
        assert server.redirect_uri == f"http://localhost:{server.port}/"
        params, bodies = drive(server, ["/?code=abc&state=xyz"])

    assert params == {"code": "abc", "state": "xyz"}
    assert "Signed in to MERMAID" in bodies[0]


def test_stray_requests_do_not_end_the_wait():
    with LoopbackCallbackServer(timeout=5) as server:
        params, bodies = drive(server, ["/favicon.ico", "/?code=abc"])

    assert params == {"code": "abc"}
    assert "Nothing to do here" in bodies[0]


def test_the_fragment_page_posts_the_hash_back():
    with LoopbackCallbackServer(mode="fragment", timeout=5) as server:
        params, bodies = drive(server, ["/", f"{FRAGMENT_PATH}?access_token=abc&state=xyz"])

    # The page the browser lands on carries the script that does the bounce.
    assert FRAGMENT_PATH in bodies[0]
    assert "window.location.hash" in bodies[0]
    assert params == {"access_token": "abc", "state": "xyz"}


def test_waiting_gives_up_after_the_timeout():
    clock = iter([0.0, 0.0, 999.0])
    with (
        LoopbackCallbackServer(timeout=1, clock=lambda: next(clock)) as server,
        pytest.raises(AuthTimeoutError, match="timed out"),
    ):
        server.wait()


def test_the_redirect_host_and_port_can_be_pinned():
    with LoopbackCallbackServer(port=0, redirect_host="127.0.0.1") as server:
        assert server.redirect_uri.startswith("http://127.0.0.1:")


def test_a_flow_timeout_is_a_login_failure():
    from datamermaid import MermaidError

    assert issubclass(AuthTimeoutError, AuthFlowError)
    assert issubclass(AuthFlowError, MermaidError)


def test_a_callback_without_a_state_is_rejected():
    # The fragment is the only thing the implicit grant gets back, so a
    # response missing the state could have come from anyone who reached the
    # loopback port.
    server = FakeServer({"access_token": "injected"})
    recorder = Recorder()
    recorder.opener = recorder.open

    with pytest.raises(AuthFlowError, match="no state"):
        implicit_auth(server, recorder, cache=False).login()


def test_the_fragment_path_is_ignored_outside_the_implicit_flow():
    with LoopbackCallbackServer(timeout=5) as server:
        params, bodies = drive(
            server,
            [f"{FRAGMENT_PATH}?access_token=injected", "/?code=abc&state=xyz"],
        )

    assert "Nothing to do here" in bodies[0]
    assert params == {"code": "abc", "state": "xyz"}
