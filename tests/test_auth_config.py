from __future__ import annotations

import pytest

from datamermaid.auth import Auth0Config
from datamermaid.auth.config import (
    DEFAULT_AUDIENCE,
    DEFAULT_AUTH0_DOMAIN,
    DEFAULT_CLIENT_ID,
)


def test_defaults_point_at_the_production_tenant():
    config = Auth0Config.resolve()
    assert config.domain == DEFAULT_AUTH0_DOMAIN == "datamermaid.auth0.com"
    assert config.client_id == DEFAULT_CLIENT_ID
    assert config.audience == DEFAULT_AUDIENCE == "https://api.datamermaid.org"


def test_endpoints_are_derived_from_the_domain():
    config = Auth0Config.resolve(domain="dev.auth0.com")
    assert config.authorize_url == "https://dev.auth0.com/authorize"
    assert config.token_url == "https://dev.auth0.com/oauth/token"
    assert config.device_code_url == "https://dev.auth0.com/oauth/device/code"
    assert config.issuer == "https://dev.auth0.com/"


def test_environment_overrides_the_defaults(monkeypatch):
    monkeypatch.setenv("MERMAID_AUTH0_DOMAIN", "dev.auth0.com")
    monkeypatch.setenv("MERMAID_CLIENT_ID", "env-client")
    monkeypatch.setenv("MERMAID_AUDIENCE", "https://dev-api.datamermaid.org")

    config = Auth0Config.resolve()
    assert (config.domain, config.client_id, config.audience) == (
        "dev.auth0.com",
        "env-client",
        "https://dev-api.datamermaid.org",
    )


def test_keyword_arguments_win_over_the_environment(monkeypatch):
    monkeypatch.setenv("MERMAID_CLIENT_ID", "env-client")
    assert Auth0Config.resolve(client_id="explicit").client_id == "explicit"


def test_environment_can_be_ignored(monkeypatch):
    monkeypatch.setenv("MERMAID_CLIENT_ID", "env-client")
    assert Auth0Config.resolve(env=False).client_id == DEFAULT_CLIENT_ID


def test_a_domain_given_as_a_url_is_normalised():
    assert Auth0Config(domain="https://tenant.auth0.com/").domain == "tenant.auth0.com"


def test_blank_configuration_is_rejected():
    with pytest.raises(ValueError, match="domain"):
        Auth0Config(domain="   ")
    with pytest.raises(ValueError, match="client_id"):
        Auth0Config(client_id=" ")


def test_scopes_request_a_refresh_token_by_default():
    assert "offline_access" in Auth0Config.resolve().scopes()


def test_offline_access_is_dropped_for_grants_that_reject_it():
    scopes = Auth0Config.resolve().scopes(offline_access=False)
    assert "offline_access" not in scopes
    assert "openid" in scopes


def test_cache_key_separates_tenants_and_audiences():
    production = Auth0Config.resolve().cache_key
    development = Auth0Config.resolve(domain="dev.auth0.com").cache_key
    assert production != development
    assert Auth0Config.resolve().cache_key == production
