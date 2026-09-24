"""The examples compile, declare what they need, and are all documented.

Running them needs credentials and the network, so this suite only checks the
things that rot silently: a syntax error after a refactor, an example added
without an entry in the index, a notebook that lost its inline metadata and so
would no longer run with a bare ``uv run``.
"""

from __future__ import annotations

import ast
import py_compile
import re
import sys

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 only
    import tomli as tomllib

from .conftest import REPO_ROOT

EXAMPLES = REPO_ROOT / "examples"
INDEX = EXAMPLES / "README.md"

#: Every example, as a path relative to the repository root.
SCRIPTS = sorted(path for path in EXAMPLES.glob("*.py"))
NOTEBOOKS = sorted(path for path in (EXAMPLES / "marimo").glob("*.py"))
ALL_EXAMPLES = SCRIPTS + NOTEBOOKS

IDS = [str(path.relative_to(EXAMPLES)) for path in ALL_EXAMPLES]
NOTEBOOK_IDS = [str(path.relative_to(EXAMPLES)) for path in NOTEBOOKS]

#: PEP 723 inline script metadata: ``# /// script`` ... ``# ///``.
SCRIPT_BLOCK = re.compile(
    r"(?m)^# /// script$\s(?P<body>(^#(| .*)$\s)+)^# ///$",
)


def script_metadata(source: str) -> dict:
    """Parse an example's PEP 723 block, as a runner would."""

    match = SCRIPT_BLOCK.search(source)
    assert match is not None, "no PEP 723 `# /// script` block"
    body = "".join(
        line[2:] if line.startswith("# ") else line[1:]
        for line in match.group("body").splitlines(keepends=True)
    )
    return tomllib.loads(body)


def test_examples_are_discovered():
    """A wrong directory here would make every other test vacuously pass."""

    assert len(SCRIPTS) == 6
    assert len(NOTEBOOKS) == 2


@pytest.mark.parametrize("path", ALL_EXAMPLES, ids=IDS)
def test_example_compiles(path, tmp_path):
    py_compile.compile(str(path), cfile=str(tmp_path / "out.pyc"), doraise=True)


@pytest.mark.parametrize("path", ALL_EXAMPLES, ids=IDS)
def test_example_declares_its_dependencies(path):
    """`uv run <example>` must work from a clean checkout, with no `uv sync`."""

    metadata = script_metadata(path.read_text(encoding="utf-8"))

    assert metadata["requires-python"] == ">=3.10"
    dependencies = metadata["dependencies"]
    assert any(name.startswith("datamermaid") for name in dependencies), dependencies

    # The SDK is not published yet, so the examples resolve it from the tree
    # they live in; the path is relative to the example, not to the caller.
    source = metadata["tool"]["uv"]["sources"]["datamermaid"]
    assert (path.parent / source["path"]).resolve() == REPO_ROOT


@pytest.mark.parametrize("path", NOTEBOOKS, ids=NOTEBOOK_IDS)
def test_notebook_is_a_marimo_app(path):
    source = path.read_text(encoding="utf-8")
    metadata = script_metadata(source)

    assert any(name.startswith("marimo") for name in metadata["dependencies"])
    assert "app = marimo.App(" in source
    assert "@app.cell" in source
    # Marimo's standard footer, so that running the file starts the app.
    assert source.rstrip().endswith("app.run()")


@pytest.mark.parametrize("path", NOTEBOOKS, ids=NOTEBOOK_IDS)
def test_notebook_cells_are_uniquely_named(path):
    """Two cells defining the same name is a marimo `MultipleDefinitionError`.

    Marimo records each cell's definitions in its ``return``; a name returned
    by two cells will not run, and the notebook would fail only when someone
    opens it.
    """

    module = ast.parse(path.read_text(encoding="utf-8"))
    seen: dict[str, str] = {}
    for node in module.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        returned = node.body[-1]
        if not isinstance(returned, ast.Return) or returned.value is None:
            continue
        names = (
            [element.id for element in returned.value.elts]
            if isinstance(returned.value, ast.Tuple)
            else [returned.value.id]
        )
        for name in names:
            assert name not in seen, f"{name} is defined by two cells"
            seen[name] = node.name


@pytest.mark.parametrize("path", ALL_EXAMPLES, ids=IDS)
def test_example_is_indexed(path):
    index = INDEX.read_text(encoding="utf-8")
    relative = path.relative_to(EXAMPLES).as_posix()

    assert relative in index, f"{relative} is missing from examples/README.md"
    assert f"uv run examples/{relative}" in index, f"{relative} has no run command"


def test_sst_authentication_failure_returns_nonzero(monkeypatch, capsys):
    import importlib.util
    from types import ModuleType

    from datamermaid import AuthenticationError

    # The failure happens before catalog access, so no pystac installation is needed.
    stac = ModuleType("pystac_client")
    stac.Client = object
    exceptions = ModuleType("pystac_client.exceptions")
    exceptions.APIError = type("APIError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "pystac_client", stac)
    monkeypatch.setitem(sys.modules, "pystac_client.exceptions", exceptions)
    spec = importlib.util.spec_from_file_location("sst_example", EXAMPLES / "zonal_stats_sst.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def rejected_client():
        raise AuthenticationError("invalid credentials", status_code=401)

    monkeypatch.setattr(module, "MermaidClient", rejected_client)
    monkeypatch.setattr(sys, "argv", ["zonal_stats_sst.py", "--project-id", "test"])
    assert module.main() == 1
    assert "Export MERMAID_API_KEY" in capsys.readouterr().err
