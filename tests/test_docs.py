"""Check all guide snippets for syntax/imports and execute selected workflows.

Runtime tests read the Markdown itself and use real SDK objects with mocked HTTP
responses. They cover public access, API-key authentication, pagination, nested
project data, DataFrame export and zonal batches. Other snippets receive static
syntax/import checks only; these tests do not infer Python types or execution.
No test touches the network, a real token cache, or a browser.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import respx

import datamermaid

from .conftest import (
    BASE_URL,
    REPO_ROOT,
    ZONAL_STATS_URL,
    load_fixture,
    page,
    project_scoped_payload,
)

DOCS = REPO_ROOT / "docs"
MKDOCS_YML = REPO_ROOT / "mkdocs.yml"

PAGES = sorted(DOCS.rglob("*.md"))
PAGE_IDS = [str(path.relative_to(DOCS)) for path in PAGES]

#: A fenced code block, with its language tag.
FENCE = re.compile(
    r"(?m)^(?P<indent> *)```(?P<language>[\w.]*)\n(?P<body>.*?)^(?P=indent)```$", re.S
)
#: An ATX heading.
HEADING = re.compile(r"(?m)^#{1,6} +(?P<title>.+?) *$")
#: An mkdocstrings cross-reference, ``[text][target]``.
CROSS_REFERENCE = re.compile(r"\]\[(?P<target>datamermaid[\w.]*)\]")
#: A ``foo.md`` path in the mkdocs nav.
NAV_ENTRY = re.compile(r"(?m)^ +- [^:]+: +(?P<path>[\w/]+\.md)$")


@dataclass(frozen=True)
class Block:
    """One fenced code block, with enough context to name it in a failure."""

    page: str
    line: int
    language: str
    section: str
    source: str

    def __str__(self) -> str:
        return f"{self.page}:{self.line} (under {self.section!r})"


def blocks(path) -> list[Block]:
    """Every fenced block on one page, tagged with the heading it sits under."""

    text = path.read_text(encoding="utf-8")
    name = str(path.relative_to(DOCS))
    found = []
    for match in FENCE.finditer(text):
        before = text[: match.start()]
        headings = HEADING.findall(before)
        indent = match.group("indent")
        body = match.group("body")
        if indent:
            body = "".join(
                line[len(indent) :] if line.startswith(indent) else line
                for line in body.splitlines(keepends=True)
            )
        found.append(
            Block(
                page=name,
                line=before.count("\n") + 1,
                language=match.group("language"),
                section=headings[-1] if headings else name,
                source=body,
            )
        )
    return found


ALL_BLOCKS = [block for path in PAGES for block in blocks(path)]
PYTHON_BLOCKS = [block for block in ALL_BLOCKS if block.language == "python"]


def test_pages_are_discovered():
    """A wrong directory here would make every other test vacuously pass."""

    assert PAGE_IDS == [
        "authentication.md",
        "covariates.md",
        "data.md",
        "examples.md",
        "index.md",
        "projects.md",
        "reference/auth.md",
        "reference/batch.md",
        "reference/client.md",
        "reference/exceptions.md",
        "reference/geometry.md",
        "reference/index.md",
        "reference/models.md",
        "reference/pagination.md",
        "reference/resources.md",
        "zonal_stats.md",
    ]
    assert len(PYTHON_BLOCKS) > 30


def test_every_page_is_in_the_nav():
    """An orphaned page is unreachable, however well it reads."""

    nav = set(NAV_ENTRY.findall(MKDOCS_YML.read_text(encoding="utf-8")))
    assert nav == set(PAGE_IDS)


@pytest.mark.parametrize("block", PYTHON_BLOCKS, ids=str)
def test_python_blocks_parse(block):
    """A block that is not valid Python cannot be copied and pasted."""

    ast.parse(block.source)


@pytest.mark.parametrize("block", ALL_BLOCKS, ids=str)
def test_code_blocks_declare_a_language(block):
    """An untagged block loses its highlighting and its place in these checks."""

    assert block.language in {"python", "bash", "console", "text"}


# -- imports ---------------------------------------------------------------


def imported_names(source: str):
    """Every ``from datamermaid... import X`` name in one block."""

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("datamermaid"):
            for alias in node.names:
                yield node.module, alias.name


@pytest.mark.parametrize("block", PYTHON_BLOCKS, ids=str)
def test_documented_imports_exist(block):
    """Nothing is imported from the package that the package does not export."""

    import importlib

    for module_name, name in imported_names(block.source):
        module = importlib.import_module(module_name)
        assert hasattr(module, name), f"{module_name} has no {name!r}"
        if module_name == "datamermaid":
            assert name in datamermaid.__all__, f"{name!r} is missing from __all__"


# -- cross-references ------------------------------------------------------


def cross_reference_targets():
    for path in PAGES:
        for target in CROSS_REFERENCE.findall(path.read_text(encoding="utf-8")):
            yield str(path.relative_to(DOCS)), target


REFERENCES = sorted(set(cross_reference_targets()))


@pytest.mark.parametrize(("page_name", "target"), REFERENCES, ids=lambda value: str(value))
def test_cross_references_resolve(page_name, target):
    """``[`Site`][datamermaid.models.Site]`` names something that exists.

    ``mkdocs build --strict`` catches these too, but only when the docs
    dependency group is installed; this runs in the ordinary test job.
    """

    import importlib

    parts = target.split(".")
    module, index = None, 0
    for index in range(len(parts), 0, -1):
        try:
            module = importlib.import_module(".".join(parts[:index]))
        except ImportError:
            continue
        break
    assert module is not None, f"{page_name}: no module in {target!r}"

    value: Any = module
    for attribute in parts[index:]:
        assert hasattr(value, attribute), f"{page_name}: {target!r} has no {attribute!r}"
        value = getattr(value, attribute)


# -- the quickstart actually runs ------------------------------------------


def example(page_name, section, index=0):
    """Select an actual guide block; a missing or renamed section fails the test."""
    selected = [
        block for block in PYTHON_BLOCKS if block.page == page_name and block.section == section
    ]
    assert len(selected) > index, f"Missing example: {page_name}: {section} [{index}]"
    return selected[index]


def run_example(block, namespace=None):
    """Execute unchanged source, preserving Markdown line numbers in tracebacks."""
    namespace = {} if namespace is None else namespace
    source = "\n" * block.line + block.source
    exec(compile(source, str(DOCS / block.page), "exec"), namespace)
    return namespace


@respx.mock
def test_quickstart_runs(capsys):
    """Copy the home page's quickstart into a test, as the issue asked."""

    family = {
        "id": "0c6f2a1a-1d8a-4a4f-bb1a-1c1b1d1e1f01",
        "name": "Acanthuridae",
        "status": 10,
        "biomass_constant_a": 0.0186,
        "biomass_constant_b": 3.03,
        "biomass_constant_c": 1.0,
    }
    route = respx.get(f"{BASE_URL}fishfamilies/").mock(
        return_value=httpx.Response(200, json=page([family]))
    )

    run_example(example("index.md", "Quickstart"))

    assert route.called
    assert route.calls.last.request.url.params["search"] == "Acanthuridae"
    assert capsys.readouterr().out.strip() == f"{family['name']} {family['id']}"


def test_quickstart_is_the_public_one():
    """The executed block is the anonymous one, not a later key-bearing example."""

    source = example("index.md", "Quickstart").source
    assert "MERMAID_API_KEY" not in source
    assert "MermaidClient()" in source


def test_readme_and_pyproject_link_the_published_docs():
    """The published URL is discoverable from the two places people start."""

    url = "https://data-mermaid.github.io/py-datamermaid/"
    assert url in (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert url in (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_the_workflow_builds_strictly_and_deploys_from_main():
    """A pull request that breaks the docs has to fail before it can merge."""

    workflow = (REPO_ROOT / ".github/workflows/docs.yml").read_text(encoding="utf-8")
    assert "uv run --group docs mkdocs build --strict" in workflow
    assert "pull_request" in workflow
    assert "actions/deploy-pages" in workflow
    assert "refs/heads/main" in workflow


def test_docs_dependency_group_exists():
    """`uv run --group docs mkdocs build` is what CI runs; keep the group real."""

    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "mkdocs-material" in text
    assert "mkdocstrings[python]" in text


@pytest.mark.parametrize("index", [0, 1], ids=["environment-key", "explicit-key"])
@respx.mock
def test_api_key_examples_run(index, monkeypatch, capsys):
    monkeypatch.setenv("MERMAID_API_KEY", "mmd_environment.secret")
    route = respx.get(f"{BASE_URL}me/").respond(
        200, json={"full_name": "Reef Researcher", "email": "reef@example.test"}
    )
    run_example(example("authentication.md", "API keys", index))
    key = "mmd_environment.secret" if index == 0 else "mmd_<key_id>.<secret>"
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {key}"
    expected = "Reef Researcher" if index == 0 else "reef@example.test"
    assert capsys.readouterr().out.strip() == expected


@respx.mock
def test_lazy_loading_example_fetches_two_pages(capsys):
    url = f"{BASE_URL}fishspecies/"
    records = [{"id": str(i), "display_name": f"Fish {i}"} for i in range(200)]
    first = respx.get(url, params__eq={"limit": "100"}).respond(
        200, json=page(records[:100], next_url=f"{url}?limit=100&offset=100", count=250)
    )
    second = respx.get(url, params__eq={"limit": "100", "offset": "100"}).respond(
        200, json=page(records[100:], next_url=f"{url}?limit=100&offset=200", count=250)
    )
    run_example(example("data.md", "Lazy loading"))
    assert first.call_count == second.call_count == 1
    assert capsys.readouterr().out.splitlines() == [
        "<PaginatedList fetched=0 count=None lazy>",
        "250",
        "Fish 0",
        "Fish 150",
        "<PaginatedList fetched=200 count=250 lazy>",
    ]


@respx.mock
def test_project_handle_and_nested_survey_examples_run(client, capsys):
    project_id = "d5491b25-4a5f-401b-a50f-bb80fd1df78f"
    root = f"{BASE_URL}projects/{project_id}/"
    sites = respx.get(f"{root}sites/").respond(200, json=page([project_scoped_payload("sites")]))
    members = respx.get(f"{root}project_profiles/").respond(
        200, json=page([project_scoped_payload("project_profiles")])
    )
    namespace = run_example(example("projects.md", "The project handle"))
    assert sites.call_count == members.call_count == 1
    assert namespace["site"].name == project_scoped_payload("sites")["name"]
    # The next guide section assumes an open client and the project handle above.
    namespace["project"] = client.projects(project_id)
    survey = respx.get(
        f"{root}beltfishtransectmethods/ffffffff-0000-0000-0000-000000000001/"
    ).respond(200, json=project_scoped_payload("beltfish_methods"))
    run_example(example("projects.md", "Sample units and their observations"), namespace)
    assert survey.call_count == 1
    assert namespace["survey"].sample_event.sample_date is not None
    assert namespace["survey"].observations["obs_belt_fishes"]
    assert capsys.readouterr().out


@respx.mock
def test_project_dataframe_example_runs():
    pytest.importorskip("pandas")
    route = respx.get(
        f"{BASE_URL}projects/d5491b25-4a5f-401b-a50f-bb80fd1df78f/beltfishes/obstransectbeltfishes/"
    ).respond(
        200,
        json=page(
            [
                {
                    "site_name": "Reef A",
                    "sample_date": "2025-01-02",
                    "fish_taxon": "Acanthurus",
                    "biomass_kgha": 12.5,
                }
            ]
        ),
    )
    namespace = run_example(example("index.md", "From a project id to a DataFrame"))
    assert route.calls.last.request.url.params["sample_date_after"] == "2018-01-01"
    assert namespace["observations"]["biomass_kgha"].tolist() == [12.5]
    assert namespace["observations"]["site_name"].tolist() == ["Reef A"]


@respx.mock
def test_zonal_batch_and_exports_run(client):
    pytest.importorskip("pandas")
    points = [{"type": "Point", "coordinates": [178.0 + i, -18.1]} for i in range(2)]
    respx.get(f"{BASE_URL}projects/reef-project/sites/").respond(
        200,
        json=page(
            [
                {"id": "a", "location": points[0]},
                {"id": "missing-location"},
                {"id": "b", "location": points[1]},
            ]
        ),
    )
    route = respx.post(f"{ZONAL_STATS_URL}raster").respond(
        200, json=load_fixture("zonal_stats_responses")["raster"]
    )
    namespace = run_example(
        example("zonal_stats.md", "Many areas at once"),
        {"client": client, "project_id": "reef-project"},
    )
    assert route.call_count == 2
    for index in (0, 1):
        run_example(example("zonal_stats.md", "Wide rows and long rows", index), namespace)
    assert namespace["frame"]["label"].tolist() == ["a", "b"]
    assert namespace["frame"]["band_1_mean"].tolist() == [12.3, 12.3]
    assert set(namespace["long_frame"]["label"]) == {"a", "b"}
    assert route.call_count == 2  # Exports read completed results.
    for call in route.calls:
        body = json.loads(call.request.content)
        assert body["aoi"] in [{**point, "radius": 500} for point in points]
        assert body["stats"] == ["mean", "count"]
        assert body["url"] == "https://example.test/depth.tif"
        assert "Authorization" not in call.request.headers


@respx.mock
def test_zonal_partial_failure_example_runs(client, capsys):
    sites = [
        project_scoped_payload("sites"),
        {"id": "missing-location"},
    ]
    respx.get(f"{BASE_URL}projects/reef-project/sites/").respond(200, json=page(sites))
    route = respx.post(f"{ZONAL_STATS_URL}raster").respond(
        200, json=load_fixture("zonal_stats_responses")["raster"]
    )
    namespace = run_example(
        example("zonal_stats.md", "When one area fails"),
        {"client": client, "sites": client.projects("reef-project").sites.list()},
    )
    assert route.call_count == 1
    assert namespace["batch"][0].label == sites[0]["id"]
    assert isinstance(namespace["batch"][1].error, ValueError)
    assert "failed:" in capsys.readouterr().out
