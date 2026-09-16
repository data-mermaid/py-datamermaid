"""Shared fixtures.  No test in this suite touches the network."""

from __future__ import annotations

import pytest

from datamermaid import MermaidClient

BASE_URL = "https://api.datamermaid.org/v1/"


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch):
    """Keep developer environment variables out of the tests."""

    monkeypatch.delenv("MERMAID_API_KEY", raising=False)
    monkeypatch.delenv("MERMAID_API_URL", raising=False)


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
