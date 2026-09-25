"""Without the optional extras, the core works and the extra features say what to install.

CI runs the whole suite once with no extras installed; there, these tests run
and the tests that need an extra skip.
"""

from __future__ import annotations

import importlib.util

import pytest

from datamermaid.pagination import Page, PaginatedList

HAS_PANDAS = importlib.util.find_spec("pandas") is not None
HAS_PYSTAC_CLIENT = importlib.util.find_spec("pystac_client") is not None


@pytest.mark.skipif(HAS_PYSTAC_CLIENT, reason="pystac-client is installed")
def test_covariates_say_to_install_the_extra(client):
    with pytest.raises(ImportError, match=r"pip install 'datamermaid\[covariates\]'"):
        client.covariates.collections()


@pytest.mark.skipif(HAS_PANDAS, reason="pandas is installed")
def test_to_df_says_to_install_pandas():
    rows = PaginatedList(lambda url: Page(items=[{"id": 1}]))
    with pytest.raises(ImportError, match="pandas"):
        rows.to_df()


def test_the_core_imports_without_extras():
    import datamermaid.resources.covariates
    import datamermaid.resources.zonal_stats  # noqa: F401
