"""Log records: what the SDK reports, at which level, and what it never logs."""

from __future__ import annotations

import logging

import httpx
import respx

from .conftest import BASE_URL, ZONAL_STATS_URL


def test_the_package_logger_has_a_null_handler():
    handlers = logging.getLogger("datamermaid").handlers
    assert any(isinstance(handler, logging.NullHandler) for handler in handlers)


@respx.mock
def test_each_response_is_logged_at_debug(client, caplog):
    respx.get(f"{BASE_URL}choices/").respond(json=[])
    with caplog.at_level(logging.DEBUG, logger="datamermaid"):
        client.choices()

    [record] = [r for r in caplog.records if r.name == "datamermaid.client"]
    assert record.levelno == logging.DEBUG
    assert record.getMessage().startswith(f"GET {BASE_URL}choices/ -> 200 in ")


@respx.mock
def test_retries_and_throttling_are_logged_at_info(client, caplog):
    respx.get(f"{BASE_URL}choices/").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.ConnectError("reset"),
            httpx.Response(200, json=[]),
        ]
    )
    with caplog.at_level(logging.INFO, logger="datamermaid"):
        client.choices()

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert messages == [
        f"retrying GET {BASE_URL}choices/ after status 503 in 0.0s (retry 1 of 3)",
        "throttled by api.datamermaid.org; every request waits 0.0s",
        f"retrying GET {BASE_URL}choices/ after status 429 in 0.0s (retry 2 of 3)",
        f"retrying GET {BASE_URL}choices/ after ConnectError: reset in 0.0s (retry 3 of 3)",
    ]


@respx.mock
def test_credentials_are_never_logged(client, caplog):
    respx.get(f"{BASE_URL}me/").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"id": "me"})]
    )
    with caplog.at_level(logging.DEBUG, logger="datamermaid"):
        client.me()

    assert caplog.records
    assert "secret" not in caplog.text
    assert "mmd_key" not in caplog.text


@respx.mock
def test_a_zonal_job_logs_its_size_and_cache_hits(client, caplog):
    respx.post(f"{ZONAL_STATS_URL}raster").respond(json={"band_1": {"mean": 28}})
    job = client.zonal_stats.raster.prepare([(1, 2), (3, 4)], url="https://data.test/a.tif")
    job.run(max_workers=2)

    with caplog.at_level(logging.DEBUG, logger="datamermaid"):
        job.run(max_workers=2)

    messages = [r.getMessage() for r in caplog.records]
    assert "running 2 zonal stats requests (2 AOIs x 1 sources) on 2 workers" in messages
    assert sum(message.startswith("cache hit for ") for message in messages) == 2
