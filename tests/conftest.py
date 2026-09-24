"""Shared fixtures.  No test in this suite touches the network."""

from __future__ import annotations

import base64
import json
import pathlib
import socket
import time

import pytest

from datamermaid import MermaidClient
from datamermaid.auth.config import DEFAULT_AUTH0_DOMAIN

BASE_URL = "https://api.datamermaid.org/v1/"
ZONAL_STATS_URL = "https://api.zonalstats.datamermaid.org/api/v1/zonal-stats/"
COVARIATES_URL = "https://mermaid.prescient.earth/stac/"
TOKEN_URL = f"https://{DEFAULT_AUTH0_DOMAIN}/oauth/token"
DEVICE_CODE_URL = f"https://{DEFAULT_AUTH0_DOMAIN}/oauth/device/code"
AUTHORIZE_URL = f"https://{DEFAULT_AUTH0_DOMAIN}/authorize"


def _b64(payload):
    raw = json.dumps(payload).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_jwt(expires_in=3600, **claims):
    """An unsigned JWT with a readable payload, which is all the SDK decodes."""

    payload = {"sub": "auth0|1", **claims}
    if expires_in is not None:
        payload["exp"] = int(time.time() + expires_in)
    return f"{_b64({'alg': 'RS256', 'typ': 'JWT'})}.{_b64(payload)}.signature"


class FakeServer:
    """Stands in for the loopback redirect server, with no socket at all."""

    def __init__(self, params=None, *, port=8123, raises=None):
        self.params = params or {}
        self.port = port
        self.raises = raises
        self.mode = None
        self.closed = False
        self.waited = False

    def __call__(self, *, mode="query", port=0, redirect_host="localhost", timeout=300.0):
        self.mode = mode
        self.redirect_host = redirect_host
        self.timeout = timeout
        if port:
            self.port = port
        return self

    @property
    def redirect_uri(self):
        return f"http://localhost:{self.port}/"

    def wait(self):
        self.waited = True
        if self.raises is not None:
            raise self.raises
        return dict(self.params)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True


class Recorder:
    """Collects the URLs a flow would have opened or printed."""

    def __init__(self, opens=True):
        self.opened = []
        self.printed = []
        self._opens = opens

    def open(self, url):
        self.opened.append(url)
        return self._opens

    def write(self, message):
        self.printed.append(message)

    @property
    def text(self):
        return "\n".join(self.printed)


AMBIENT_ENV_VARS = (
    "MERMAID_API_KEY",
    "MERMAID_API_URL",
    "MERMAID_ZONAL_STATS_URL",
    "MERMAID_COVARIATES_URL",
    "MERMAID_AUTH0_DOMAIN",
    "MERMAID_CLIENT_ID",
    "MERMAID_AUDIENCE",
    "SSH_CONNECTION",
    "SSH_TTY",
)


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch, tmp_path):
    """Keep developer environment variables and token caches out of the tests."""

    for name in AMBIENT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # Point the OAuth token cache at a throwaway directory so a real login on
    # the developer's machine cannot leak into (or be clobbered by) a test.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    """A test that reaches for a real browser is a bug in the test."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a browser")

    monkeypatch.setattr("webbrowser.open", refuse)


@pytest.fixture(autouse=True)
def _no_outbound_network(monkeypatch):
    """Let the loopback callback server through, block everything else."""

    connect = socket.socket.connect

    def guard(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError(f"a test tried to connect to {address!r}")
        return connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guard)


@pytest.fixture
def client():
    """A client with retries that do not sleep."""

    with MermaidClient(api_key="mmd_key.secret", backoff_factor=0.0) as instance:
        yield instance


def project_payload(index: int = 1, **overrides):
    payload = {
        "id": f"0000000{index}-0000-0000-0000-000000000000",
        "name": f"Project {index}",
        "notes": "",
        "status": 90,
        "countries": ["Fiji"],
        "tags": ["WCS Fiji"],
        "members": ["11111111-1111-1111-1111-111111111111"],
        "project_admins": [{"id": "11111111-1111-1111-1111-111111111111", "name": "A Person"}],
        "num_sites": 31,
        "num_sample_units": 279.0,
        "num_active_sample_units": 0,
        "is_demo": False,
        "includes_gfcr": False,
        "suggested_citation": "Someone. 2024. MERMAID.",
        "bbox": None,
        "data_policy_beltfish": 50,
        "data_policy_benthiclit": 50,
        "data_policy_benthicpit": 50,
        "data_policy_benthicpqt": 50,
        "data_policy_bleachingqc": 50,
        "data_policy_habitatcomplexity": 50,
        "data_policy_macroinvertebrate": 50,
        "created_on": "2025-09-04T21:20:58.632480Z",
        "updated_on": "2025-10-01T01:06:25.209402Z",
        "created_by": "22222222-2222-2222-2222-222222222222",
        "updated_by": "22222222-2222-2222-2222-222222222222",
    }
    payload.update(overrides)
    return payload


def page(results, next_url=None, count=None):
    return {
        "count": len(results) if count is None else count,
        "next": next_url,
        "previous": None,
        "results": results,
    }


#: The repository root, for the tests that read files outside the package.
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(name):
    """Load a captured API payload from ``tests/fixtures``."""

    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def reference_payload(attribute):
    """One record as the API returns it, keyed by client attribute name."""

    return load_fixture("reference_responses")[attribute]


#: The project the payloads in ``project_responses.json`` belong to.
PROJECT_ID = "9bf0538e-99c7-405b-a54b-a1568c8a757e"


def project_scoped_payload(attribute):
    """One project-scoped record, keyed by :class:`ProjectContext` attribute name."""

    return load_fixture("project_responses")[attribute]


def aggregated_payload(view):
    """One row from an aggregated view, keyed by the resource method name."""

    return load_fixture("aggregated_responses")[view]
