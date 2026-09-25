"""Public preparation signatures reject bad options during static checking."""

import inspect
import subprocess
import sys
import typing

import pytest

from datamermaid.resources.zonal_stats import BATCH_OPTIONS, ZONAL_STATS_ENDPOINTS

from .conftest import REPO_ROOT


def test_prepare_and_batch_public_types(tmp_path):
    source = tmp_path / "check_zonal_types.py"
    source.write_text("""
from datamermaid import (
    Batch,
    BatchStream,
    BatchFailure,
    MermaidClient,
    ZonalJob,
    ZonalStatsResult,
    ZonalTask,
)
from typing import Any, Literal
from collections.abc import Iterator

client = MermaidClient()
job: ZonalJob = client.zonal_stats.raster.prepare([], url="https://example.test", bands=[1])
vector: ZonalJob = client.zonal_stats.vector_stac.prepare(
    [], url="https://example.test", columns=["depth"], asset="data"
)
batch: Batch[ZonalTask, ZonalStatsResult] = client.zonal_stats.raster.batch(
    [], url="https://example.test"
)
for result in batch:
    if isinstance(result, BatchFailure):
        error: Exception = result.error
        task: ZonalTask = result.item
    else:
        value: ZonalStatsResult = result
for result in job.run():
    print(result.label, result["band_1"]["mean"])
with job.run(stream=True) as stream:
    for result in stream:
        print(result.label, result["band_1"]["mean"])
for outcome in job.run(errors="return"):
    success_only: ZonalStatsResult = outcome  # type: ignore[assignment]
    if isinstance(outcome, BatchFailure):
        failed_task: ZonalTask = outcome.item
    else:
        success: ZonalStatsResult = outcome


def variable_options(mode: Literal["raise", "return"], stream: bool) -> None:
    for outcome in job.run(errors=mode, stream=stream):
        success_only: ZonalStatsResult = outcome  # type: ignore[assignment]
    for outcome in client.zonal_stats.raster.batch(
        [], url="https://example.test", errors=mode, stream=stream
    ):
        success_only2: ZonalStatsResult = outcome  # type: ignore[assignment]


for number in Batch([1], lambda x: x + 1):
    integer: int = number
for number in BatchStream([1], lambda x: x + 1):
    integer2: int = number
for outcome2 in Batch([1], lambda x: x + 1, errors="return"):
    integer3: int = outcome2  # type: ignore[assignment]
for outcome3 in BatchStream([1], lambda x: x + 1, errors="return"):
    integer4: int = outcome3  # type: ignore[assignment]


class Item:
    def to_dict(self) -> dict[str, Any]:
        return {}


class Search:
    def items_as_dicts(self) -> Iterator[dict[str, Any]]:
        yield {}


client.zonal_stats.raster_stac.prepare([], search=Search())
client.zonal_stats.raster_stac.prepare([], sources=[Item(), {}, "https://example.test"])
client.zonal_stats.raster_stac.prepare([], search=42)  # type: ignore[arg-type]
client.zonal_stats.raster_stac.prepare([], sources=[42])  # type: ignore[list-item]
client.projects.list(limit="five")  # type: ignore[arg-type]
client.projects.list(future_api_filter="allowed")
client.projects("id").beltfishes.sample_units(limit="five")  # type: ignore[arg-type]
for value_raster_False in client.zonal_stats.raster.batch(
    [], url="https://example.test", stream=False
):
    success_raster_False: ZonalStatsResult = value_raster_False
for outcome_raster_False in client.zonal_stats.raster.batch(
    [], url="https://example.test", stream=False, errors="return"
):
    failed_raster_False: ZonalStatsResult = outcome_raster_False  # type: ignore[assignment]
for value_raster_True in client.zonal_stats.raster.batch(
    [], url="https://example.test", stream=True
):
    success_raster_True: ZonalStatsResult = value_raster_True
for outcome_raster_True in client.zonal_stats.raster.batch(
    [], url="https://example.test", stream=True, errors="return"
):
    failed_raster_True: ZonalStatsResult = outcome_raster_True  # type: ignore[assignment]
for value_raster_stac_False in client.zonal_stats.raster_stac.batch(
    [], url="https://example.test", stream=False
):
    success_raster_stac_False: ZonalStatsResult = value_raster_stac_False
for outcome_raster_stac_False in client.zonal_stats.raster_stac.batch(
    [], url="https://example.test", stream=False, errors="return"
):
    fail_rs: ZonalStatsResult = outcome_raster_stac_False  # type: ignore[assignment]
for value_raster_stac_True in client.zonal_stats.raster_stac.batch(
    [], url="https://example.test", stream=True
):
    success_raster_stac_True: ZonalStatsResult = value_raster_stac_True
for outcome_raster_stac_True in client.zonal_stats.raster_stac.batch(
    [], url="https://example.test", stream=True, errors="return"
):
    failed_raster_stac_True: ZonalStatsResult = outcome_raster_stac_True  # type: ignore[assignment]
for value_vector_False in client.zonal_stats.vector.batch(
    [], url="https://example.test", stream=False, columns=["depth"]
):
    success_vector_False: ZonalStatsResult = value_vector_False
for outcome_vector_False in client.zonal_stats.vector.batch(
    [], url="https://example.test", stream=False, errors="return", columns=["depth"]
):
    failed_vector_False: ZonalStatsResult = outcome_vector_False  # type: ignore[assignment]
for value_vector_True in client.zonal_stats.vector.batch(
    [], url="https://example.test", stream=True, columns=["depth"]
):
    success_vector_True: ZonalStatsResult = value_vector_True
for outcome_vector_True in client.zonal_stats.vector.batch(
    [], url="https://example.test", stream=True, errors="return", columns=["depth"]
):
    failed_vector_True: ZonalStatsResult = outcome_vector_True  # type: ignore[assignment]
for value_vector_stac_False in client.zonal_stats.vector_stac.batch(
    [], url="https://example.test", stream=False, columns=["depth"]
):
    success_vector_stac_False: ZonalStatsResult = value_vector_stac_False
for outcome_vector_stac_False in client.zonal_stats.vector_stac.batch(
    [], url="https://example.test", stream=False, errors="return", columns=["depth"]
):
    fail_vs: ZonalStatsResult = outcome_vector_stac_False  # type: ignore[assignment]
for value_vector_stac_True in client.zonal_stats.vector_stac.batch(
    [], url="https://example.test", stream=True, columns=["depth"]
):
    success_vector_stac_True: ZonalStatsResult = value_vector_stac_True
for outcome_vector_stac_True in client.zonal_stats.vector_stac.batch(
    [], url="https://example.test", stream=True, errors="return", columns=["depth"]
):
    failed_vector_stac_True: ZonalStatsResult = outcome_vector_stac_True  # type: ignore[assignment]
client.zonal_stats.raster.prepare([], bands="1")  # type: ignore[arg-type]
client.zonal_stats.raster_stac.prepare([], asset=123)  # type: ignore[arg-type]
client.zonal_stats.vector.prepare([])  # type: ignore[call-arg]
client.zonal_stats.vector_stac.prepare([], columns=["depth"], typo=True)  # type: ignore[call-arg]
client.zonal_stats.raster.batch([], url="https://example.test", bands=[1], max_workers=2)
client.zonal_stats.raster_stac.batch([], url="https://example.test", asset="data", bands=[1])
client.zonal_stats.vector.batch([], url="https://example.test", columns=["depth"], cache=False)
client.zonal_stats.vector_stac.batch(
    [], url="https://example.test", columns=["depth"], asset="data", weighting_method="ratio"
)
URL = "https://example.test"
client.zonal_stats.raster.batch([], url=URL, bandz=[1])  # type: ignore[call-overload]
client.zonal_stats.raster.batch([], url=URL, asset="data")  # type: ignore[call-overload]
client.zonal_stats.raster.batch([], url=URL, bands="1")  # type: ignore[arg-type]
client.zonal_stats.vector.batch([], url=URL)  # type: ignore[call-overload]
client.zonal_stats.vector_stac.batch(  # type: ignore[call-overload]
    [], url="https://example.test", columns=["depth"], typo=True
)
""")
    checked = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--warn-unused-ignores",
            str(source),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize(("name", "endpoint"), ZONAL_STATS_ENDPOINTS)
def test_batch_options_match_prepare(name, endpoint):
    # The typed `batch` options are written out by hand; they must stay the
    # keyword arguments of `prepare`, plus `max_workers`, which only runs take.
    options = BATCH_OPTIONS[endpoint.route]
    parameters = inspect.signature(endpoint.prepare).parameters
    keywords = {key for key, value in parameters.items() if value.kind is value.KEYWORD_ONLY}
    required = {key for key in keywords if parameters[key].default is inspect.Parameter.empty}

    assert options.__required_keys__ | options.__optional_keys__ == keywords | {"max_workers"}
    assert options.__required_keys__ == required


@pytest.mark.parametrize(("name", "endpoint"), ZONAL_STATS_ENDPOINTS)
def test_batch_hints_resolve_at_runtime(name, endpoint):
    assert "return" in typing.get_type_hints(endpoint.batch)
    assert "columns" in (endpoint.batch.__doc__ or "") or "bands" in (endpoint.batch.__doc__ or "")
