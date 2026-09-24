from __future__ import annotations

import pytest

from datamermaid.pagination import PANDAS_INSTALL_HINT, Page, PaginatedList


def make_list(pages, calls=None):
    """Build a PaginatedList over `pages`, recording the urls requested."""

    calls = [] if calls is None else calls

    def fetch(next_url):
        calls.append(next_url)
        index = 0 if next_url is None else int(next_url)
        items, has_next = pages[index]
        return Page(
            items=list(items),
            next_url=str(index + 1) if has_next else None,
            count=sum(len(page_items) for page_items, _ in pages),
        )

    return PaginatedList(fetch), calls


def test_nothing_is_fetched_until_iteration():
    paginated, calls = make_list([(["a", "b"], True), (["c"], False)])
    assert calls == []
    assert paginated.fetched == []


def test_second_page_is_fetched_only_when_iteration_crosses_the_boundary():
    paginated, calls = make_list([(["a", "b"], True), (["c", "d"], False)])
    iterator = iter(paginated)

    assert next(iterator) == "a"
    assert len(calls) == 1
    assert next(iterator) == "b"
    assert len(calls) == 1, "page 2 must not be fetched while page 1 has items left"
    assert next(iterator) == "c"
    assert len(calls) == 2
    assert next(iterator) == "d"
    assert len(calls) == 2
    with pytest.raises(StopIteration):
        next(iterator)
    assert len(calls) == 2


def test_items_are_cached_across_iterations():
    paginated, calls = make_list([(["a"], True), (["b"], False)])
    assert list(paginated) == ["a", "b"]
    assert list(paginated) == ["a", "b"]
    assert calls == [None, "1"]


def test_len_uses_the_reported_count_without_fetching_every_page():
    paginated, calls = make_list([(["a"], True), (["b"], True), (["c"], False)])
    assert len(paginated) == 3
    assert calls == [None]


def test_len_falls_back_to_materialising_when_count_is_missing():
    def fetch(next_url):
        if next_url is None:
            return Page(items=["a"], next_url="1")
        return Page(items=["b"])

    assert len(PaginatedList(fetch)) == 2


def test_indexing_fetches_only_the_pages_needed():
    paginated, calls = make_list([(["a"], True), (["b"], True), (["c"], False)])
    assert paginated[1] == "b"
    assert calls == [None, "1"]


def test_negative_index_materialises_everything():
    paginated, calls = make_list([(["a"], True), (["b"], False)])
    assert paginated[-1] == "b"
    assert calls == [None, "1"]


def test_index_out_of_range():
    paginated, _ = make_list([(["a"], False)])
    with pytest.raises(IndexError):
        paginated[5]


def test_slicing_stops_at_the_requested_bound():
    paginated, calls = make_list([(["a"], True), (["b"], True), (["c"], False)])
    assert paginated[:2] == ["a", "b"]
    assert calls == [None, "1"]


def test_open_ended_slice_materialises_everything():
    paginated, calls = make_list([(["a"], True), (["b"], False)])
    assert paginated[1:] == ["b"]
    assert calls == [None, "1"]


def test_empty_pages_do_not_stall_iteration():
    paginated, calls = make_list([([], True), ([], True), (["a"], False)])
    assert list(paginated) == ["a"]
    assert calls == [None, "1", "2"]


def test_repr_reports_progress():
    paginated, _ = make_list([(["a"], False)])
    assert "lazy" in repr(paginated)
    list(paginated)
    assert "exhausted" in repr(paginated)


def test_to_df_returns_a_dataframe():
    pandas = pytest.importorskip("pandas")
    paginated, _ = make_list([([{"a": 1}], True), ([{"a": 2}], False)])
    frame = paginated.to_df()
    assert isinstance(frame, pandas.DataFrame)
    assert list(frame["a"]) == [1, 2]


def test_to_df_raises_an_informative_error_without_pandas(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pandas":
            raise ImportError("No module named 'pandas'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    paginated, calls = make_list([([{"a": 1}], True), ([{"a": 2}], False)])
    with pytest.raises(ImportError) as excinfo:
        paginated.to_df()
    assert calls == []
    assert PANDAS_INSTALL_HINT in str(excinfo.value)
    assert "datamermaid[pandas]" in str(excinfo.value)


def test_dataframe_preview_does_not_fetch_following_pages():
    pytest.importorskip("pandas")
    from datamermaid.pagination import to_dataframe

    paginated, calls = make_list([([{"a": 1}], True), ([{"a": 2}], False)])
    frame = to_dataframe(paginated[:1])
    assert frame["a"].tolist() == [1]
    assert calls == [None]
