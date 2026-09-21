"""The documentation site says things about the SDK that are actually true.

Prose rots quietly: an attribute is renamed, a method loses an argument, and the
example that used it keeps rendering perfectly.  So every ``python`` block in
``docs/`` is parsed, and every attribute chain rooted at a name we can identify
is resolved against the real classes.  The quickstart goes further and is
executed against a mocked API, which is the only way to be sure it still runs.

Nothing here touches the network.
"""

from __future__ import annotations

import ast
import dataclasses
import re
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import respx

import datamermaid
from datamermaid import (
    AggregatedRecord,
    APIKeyAuth,
    APIModel,
    FishFamily,
    FishGenus,
    FishSpecies,
    Me,
    MermaidClient,
    OAuth,
    PaginatedList,
    Project,
    ProjectContext,
    ProjectProfile,
    Site,
    SummarySampleEvent,
    TokenSet,
)
from datamermaid.models import BeltFishMethod
from datamermaid.resources.aggregated import AggregatedFamilyResource
from datamermaid.resources.base import Resource
from datamermaid.resources.projects import ProjectsResource

from .conftest import BASE_URL, PROJECT_ID, REPO_ROOT, page

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
        "data.md",
        "examples.md",
        "index.md",
        "projects.md",
        "reference/auth.md",
        "reference/batch.md",
        "reference/client.md",
        "reference/exceptions.md",
        "reference/index.md",
        "reference/models.md",
        "reference/pagination.md",
        "reference/resources.md",
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


# -- attribute chains ------------------------------------------------------


@dataclass(frozen=True)
class ListOf:
    """A :class:`PaginatedList` (or tuple) whose items are ``item``."""

    item: type


def _model_attribute(model: type[APIModel], name: str) -> Any:
    """What ``<a model>.<name>`` is, or ``AttributeError`` if it is nothing.

    Declared fields resolve to their annotated type, so a chain can keep going
    through a nested model; anything else that merely exists (a property, a
    method, a ``ClassVar``) resolves to ``None``, meaning "real, but stop here".
    """

    field = {each.name: each for each in dataclasses.fields(model)}.get(name)
    if field is None:
        if not hasattr(model, name):
            raise AttributeError(f"{model.__name__} has no attribute {name!r}")
        return None

    annotation = typing.get_type_hints(model)[name]
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Union or str(origin) == "<class 'types.UnionType'>":
        remaining = [arg for arg in args if arg is not type(None)]
        annotation = remaining[0] if len(remaining) == 1 else None
    elif origin is tuple and len(args) == 2 and args[1] is Ellipsis:
        annotation = ListOf(args[0]) if _is_model(args[0]) else None
    else:
        annotation = None

    if _is_model(annotation):
        return annotation()
    return annotation if isinstance(annotation, ListOf) else None


def _is_model(value: Any) -> bool:
    return isinstance(value, type) and issubclass(value, APIModel)


def _call_result(target: Any, name: str) -> Any:
    """What calling ``target.<name>()`` yields, or ``None`` when we cannot tell.

    Only the shapes the guides actually chain off are modelled; everything else
    ends the chain, which costs a check but never invents one.
    """

    if isinstance(target, AggregatedFamilyResource):
        return ListOf(AggregatedRecord)
    if isinstance(target, Resource):
        if name == "list":
            return ListOf(target.model)
        if name == "get":
            return target.model()
    if isinstance(target, MermaidClient) and name == "me":
        return Me()
    if isinstance(target, PaginatedList) and name == "to_df":
        return None
    return None


class ChainResolver:
    """Resolves an attribute chain against the objects a root name may hold."""

    def __init__(self, roots: dict[str, tuple[Any, ...]]) -> None:
        self.roots = roots

    def check(self, tree: ast.AST, where: str) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and self._root_name(node) in self.roots:
                self.resolve(node, where)

    def _root_name(self, node: ast.AST) -> str | None:
        while isinstance(node, (ast.Attribute, ast.Call, ast.Subscript)):
            node = node.func if isinstance(node, ast.Call) else node.value
        return node.id if isinstance(node, ast.Name) else None

    def resolve(self, node: ast.AST, where: str) -> tuple[Any, ...]:
        """Every value ``node`` may evaluate to; ``()`` means "unknown, stop"."""

        if isinstance(node, ast.Name):
            return self.roots.get(node.id, ())
        if isinstance(node, ast.Attribute):
            return self._attribute(node, where)
        if isinstance(node, ast.Call):
            return self._call(node, where)
        if isinstance(node, ast.Subscript):
            return self._subscript(node, where)
        return ()

    def _attribute(self, node: ast.Attribute, where: str) -> tuple[Any, ...]:
        targets = self.resolve(node.value, where)
        if not targets:
            return ()
        resolved, failures = [], []
        for target in targets:
            try:
                resolved.append(self._one_attribute(target, node.attr))
            except AttributeError as exc:
                failures.append(str(exc))
        assert resolved, f"{where}: {ast.unparse(node)} -> {'; '.join(failures)}"
        return tuple(value for value in resolved if value is not None)

    def _one_attribute(self, target: Any, name: str) -> Any:
        if isinstance(target, ListOf):
            target = PaginatedList
        if isinstance(target, APIModel):
            return _model_attribute(type(target), name)
        if not hasattr(target, name):
            owner = target if isinstance(target, type) else type(target)
            raise AttributeError(f"{owner.__name__} has no attribute {name!r}")
        return getattr(target, name)

    def _call(self, node: ast.Call, where: str) -> tuple[Any, ...]:
        if isinstance(node.func, ast.Attribute):
            targets = self.resolve(node.func.value, where)
            self.resolve(node.func, where)  # the attribute itself must exist
            return tuple(
                result
                for target in targets
                if (result := _call_result(target, node.func.attr)) is not None
            )
        # `client.projects(project_id)` opens a project handle.
        if any(isinstance(target, ProjectsResource) for target in self.resolve(node.func, where)):
            return tuple(
                root for root in self.roots.get("project", ()) if isinstance(root, ProjectContext)
            )
        return ()

    def _subscript(self, node: ast.Subscript, where: str) -> tuple[Any, ...]:
        index = node.slice
        positional = isinstance(index, ast.Slice) or (
            isinstance(index, ast.Constant) and isinstance(index.value, int)
        )
        if not positional:
            # A string or list key means a mapping or a DataFrame, not our list.
            return ()
        items = []
        for target in self.resolve(node.value, where):
            if isinstance(target, ListOf):
                items.append(target.item() if _is_model(target.item) else target.item)
        return tuple(items)


@pytest.fixture(scope="module")
def roots():
    """Variable names the guides bind, and every kind of value each one may hold.

    A chain passes when it resolves against at least one of them, which is what
    lets ``species`` be both a record and the lazy list it came out of.

    Built in a fixture rather than at import time so nothing here is constructed
    before the suite's isolation fixtures; ``cache=False`` and ``env=False`` keep
    the OAuth objects off the developer's real token cache either way, since
    ``oauth.tokens`` is one of the documented attributes this resolves.
    """

    with MermaidClient(api_key="mmd_key.secret", base_url=BASE_URL) as client:
        oauth = OAuth(interactive=False, cache=False, env=False)
        yield {
            "datamermaid": (datamermaid,),
            "client": (client,),
            "project": (ProjectContext(client, PROJECT_ID), Project()),
            "acanthuridae": (FishFamily(),),
            "auth": (APIKeyAuth("mmd_key.secret"), oauth),
            "first_page": (ListOf(FishSpecies),),
            "genus": (FishGenus(),),
            "me": (Me(),),
            "member": (ProjectProfile(),),
            "oauth": (oauth,),
            "row": (AggregatedRecord(),),
            "site": (Site(),),
            "species": (FishSpecies(), ListOf(FishSpecies)),
            "summary": (SummarySampleEvent(),),
            "surgeonfish": (FishFamily(),),
            "survey": (BeltFishMethod(),),
            "tokens": (TokenSet("header.body.signature"),),
        }


@pytest.mark.parametrize("block", PYTHON_BLOCKS, ids=str)
def test_attribute_chains_resolve(block, roots):
    """Every documented ``client.x.y`` really is a ``client.x.y``."""

    ChainResolver(roots).check(ast.parse(block.source), str(block))


def test_the_resolver_catches_a_typo(roots):
    """Otherwise the test above would pass no matter what the guides claimed."""

    with pytest.raises(AssertionError, match="no attribute 'fish_speciez'"):
        ChainResolver(roots).check(ast.parse("client.fish_speciez.list()"), "made up")

    with pytest.raises(AssertionError, match="no attribute 'beltfishez'"):
        ChainResolver(roots).check(ast.parse("project.beltfishez.observations()"), "made up")

    with pytest.raises(AssertionError, match="no attribute 'displayname'"):
        ChainResolver(roots).check(
            ast.parse("client.fish_species.list()[0].displayname"), "made up"
        )


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


def quickstart_source() -> str:
    """The first ``python`` block under the Quickstart heading of the home page."""

    for block in blocks(DOCS / "index.md"):
        if block.language == "python" and block.section == "Quickstart":
            return block.source
    raise AssertionError("docs/index.md has no Quickstart python block")


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

    exec(compile(quickstart_source(), "docs/index.md", "exec"), {})

    assert route.called
    assert route.calls.last.request.url.params["search"] == "Acanthuridae"
    assert capsys.readouterr().out.strip() == f"{family['name']} {family['id']}"


def test_quickstart_is_the_public_one():
    """The executed block is the anonymous one, not a later key-bearing example."""

    source = quickstart_source()
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


def test_mapping_fields_end_a_chain():
    """`_model_attribute` stops at a mapping rather than guessing its values."""

    assert _model_attribute(SummarySampleEvent, "protocols") is None
    assert isinstance(_model_attribute(AggregatedRecord, "extra"), (Mapping, type(None)))
