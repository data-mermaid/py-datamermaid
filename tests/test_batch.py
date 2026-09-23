"""`LazyBatch` on its own: laziness, the bounded window, caching and error modes.

The compute functions here are fakes.  Concurrency is proved with a `Barrier`
that would time out if two computations never overlapped, and the window bound
with a counter under a lock; nothing sleeps for real.
"""

from __future__ import annotations

import threading
import time

import pytest

import datamermaid
from datamermaid import LazyBatch
from datamermaid.batch import DEFAULT_MAX_WORKERS

TIMEOUT = 5.0


class Spy:
    """A compute function that records what ran, when, and how many at once."""

    def __init__(self, fail_at=(), delay=0.0):
        self.calls = []
        self.in_flight = 0
        self.peak = 0
        self.fail_at = set(fail_at)
        self.delay = delay
        self.lock = threading.Lock()

    def __call__(self, value):
        with self.lock:
            self.calls.append(value)
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            if self.delay:
                time.sleep(self.delay)
            if value in self.fail_at:
                raise ValueError(f"boom {value}")
            return value * 10
        finally:
            with self.lock:
                self.in_flight -= 1


# -- construction -----------------------------------------------------------


def test_construction_does_no_work():
    spy = Spy()
    batch = LazyBatch(range(10), spy, max_workers=3)
    assert spy.calls == []
    assert len(batch) == 10
    assert batch.fetched == {}
    assert batch.inputs == list(range(10))
    assert batch.max_workers == 3
    assert batch.errors == "raise"
    assert spy.calls == []
    assert repr(batch) == "<LazyBatch computed=0/10 max_workers=3>"


def test_defaults():
    batch = LazyBatch([], lambda value: value)
    assert batch.max_workers == DEFAULT_MAX_WORKERS == 8
    assert len(batch) == 0
    assert list(batch) == []
    assert batch.results() == []


def test_inputs_are_consumed_once():
    batch = LazyBatch(iter([1, 2, 3]), lambda value: value)
    assert len(batch) == 3
    assert batch.results() == [1, 2, 3]
    assert batch.results() == [1, 2, 3]


@pytest.mark.parametrize("max_workers", [0, -1])
def test_max_workers_must_be_positive(max_workers):
    with pytest.raises(ValueError, match="max_workers"):
        LazyBatch([1], lambda value: value, max_workers=max_workers)


def test_unknown_error_mode_is_rejected():
    with pytest.raises(ValueError, match="errors"):
        LazyBatch([1], lambda value: value, errors="ignore")


def test_is_exported():
    assert datamermaid.LazyBatch is LazyBatch
    assert "LazyBatch" in datamermaid.__all__


# -- iteration --------------------------------------------------------------


def test_iteration_is_in_input_order_and_cached():
    spy = Spy()
    batch = LazyBatch(range(10), spy, max_workers=3)

    assert list(batch) == [value * 10 for value in range(10)]
    assert sorted(spy.calls) == list(range(10))
    assert len(spy.calls) == 10

    # A second pass comes from the cache.
    assert list(batch) == [value * 10 for value in range(10)]
    assert len(spy.calls) == 10
    assert repr(batch) == "<LazyBatch computed=10/10 max_workers=3>"


def test_never_more_than_max_workers_in_flight():
    spy = Spy(delay=0.005)
    batch = LazyBatch(range(10), spy, max_workers=3)

    assert list(batch) == [value * 10 for value in range(10)]
    assert spy.peak <= 3
    assert spy.peak >= 1


def test_at_least_two_run_concurrently():
    """A barrier of two: if the computations were serial this would time out."""

    barrier = threading.Barrier(2, timeout=TIMEOUT)

    def compute(value):
        barrier.wait()
        return value

    batch = LazyBatch(range(2), compute, max_workers=2)
    assert list(batch) == [0, 1]


def test_max_workers_one_is_serial():
    spy = Spy(delay=0.001)
    batch = LazyBatch(range(5), spy, max_workers=1)
    assert list(batch) == [0, 10, 20, 30, 40]
    assert spy.peak == 1
    assert spy.calls == [0, 1, 2, 3, 4]


def test_breaking_early_wastes_at_most_a_window():
    started = threading.Event()
    release = threading.Event()
    spy = Spy()
    lock = threading.Lock()
    calls = []

    def compute(value):
        with lock:
            calls.append(value)
        if value != 0:
            # Hold the prefetched items until the consumer has broken out, so
            # the count below is a hard bound and not a race.
            started.set()
            release.wait(TIMEOUT)
        return spy(value)

    batch = LazyBatch(range(10), compute, max_workers=3)
    for result in batch:
        assert result == 0
        break
    started.wait(TIMEOUT)
    release.set()

    # The consumer stopped, so nothing beyond the first window was ever started.
    deadline = time.monotonic() + TIMEOUT
    while len(batch.fetched) < len(calls) and time.monotonic() < deadline:
        time.sleep(0.001)
    assert len(calls) <= 3
    assert 0 in calls
    assert len(batch.fetched) <= 3


def test_iteration_resumes_from_the_cache():
    spy = Spy()
    batch = LazyBatch(range(6), spy, max_workers=2)
    assert batch[4] == 40
    assert list(batch) == [0, 10, 20, 30, 40, 50]
    assert len(spy.calls) == 6
    assert spy.calls.count(4) == 1


# -- indexing ---------------------------------------------------------------


def test_indexing_computes_exactly_one_item():
    spy = Spy()
    batch = LazyBatch(range(10), spy, max_workers=3)

    assert batch[7] == 70
    assert spy.calls == [7]
    assert batch.fetched == {7: 70}
    assert repr(batch) == "<LazyBatch computed=1/10 max_workers=3>"

    assert batch[7] == 70
    assert spy.calls == [7]


def test_negative_index_uses_the_known_length():
    spy = Spy()
    batch = LazyBatch(range(10), spy)
    assert batch[-1] == 90
    assert spy.calls == [9]


@pytest.mark.parametrize("index", [10, -11])
def test_index_out_of_range(index):
    spy = Spy()
    batch = LazyBatch(range(10), spy)
    with pytest.raises(IndexError):
        batch[index]
    assert spy.calls == []


def test_slice_computes_only_its_items_in_parallel():
    barrier = threading.Barrier(2, timeout=TIMEOUT)
    spy = Spy()

    def compute(value):
        barrier.wait()  # would deadlock if the slice ran serially
        return spy(value)

    batch = LazyBatch(range(10), compute, max_workers=2)
    assert batch[2:4] == [20, 30]
    assert sorted(spy.calls) == [2, 3]


def test_slice_of_three_computes_exactly_three():
    spy = Spy()
    batch = LazyBatch(range(10), spy, max_workers=3)
    assert batch[2:5] == [20, 30, 40]
    assert sorted(spy.calls) == [2, 3, 4]
    assert batch.fetched == {2: 20, 3: 30, 4: 40}


def test_slice_with_step_and_negative_bounds():
    spy = Spy()
    batch = LazyBatch(range(10), spy)
    assert batch[-3::2] == [70, 90]
    assert sorted(spy.calls) == [7, 9]
    assert batch[::-4] == [90, 50, 10]
    assert sorted(spy.calls) == [1, 5, 7, 9]


def test_empty_slice_computes_nothing():
    spy = Spy()
    batch = LazyBatch(range(10), spy)
    assert batch[5:2] == []
    assert spy.calls == []


# -- results ----------------------------------------------------------------


def test_results_computes_everything_in_parallel():
    barrier = threading.Barrier(3, timeout=TIMEOUT)
    spy = Spy()

    def compute(value):
        barrier.wait()
        return spy(value)

    batch = LazyBatch(range(3), compute, max_workers=3)
    # The barrier only opens once all three are inside `compute` at the same time.
    assert batch.results() == [0, 10, 20]
    assert sorted(spy.calls) == [0, 1, 2]


def test_results_reuses_cached_items():
    spy = Spy()
    batch = LazyBatch(range(5), spy)
    batch[1]
    batch[3]
    assert batch.results() == [0, 10, 20, 30, 40]
    assert sorted(spy.calls) == [0, 1, 2, 3, 4]


def test_results_with_max_workers_larger_than_the_batch():
    spy = Spy()
    batch = LazyBatch(range(2), spy, max_workers=50)
    assert batch.results() == [0, 10]


def test_concurrent_reads_of_one_item_compute_it_once():
    started = threading.Event()
    release = threading.Event()
    spy = Spy()

    def compute(value):
        started.set()
        assert release.wait(TIMEOUT)
        return spy(value)

    batch = LazyBatch(range(3), compute)
    seen = []
    readers = [threading.Thread(target=lambda: seen.append(batch[0])) for _ in range(4)]
    readers[0].start()
    assert started.wait(TIMEOUT)
    for reader in readers[1:]:
        reader.start()
    # The later readers are now waiting on the first one's computation.
    release.set()
    for reader in readers:
        reader.join(TIMEOUT)

    assert seen == [0, 0, 0, 0]
    assert spy.calls == [0]


def test_reading_an_item_a_closed_iterator_left_running_waits_for_it():
    started = threading.Event()
    release = threading.Event()
    spy = Spy()

    def compute(value):
        if value == 1:
            started.set()
            assert release.wait(TIMEOUT)
        return spy(value)

    batch = LazyBatch(range(3), compute, max_workers=2)
    iterator = iter(batch)
    assert next(iterator) == 0
    assert started.wait(TIMEOUT)
    iterator.close()  # item 1 is still running on the abandoned pool

    threading.Timer(0.05, release.set).start()
    assert batch[1] == 10
    assert spy.calls.count(1) == 1


def test_an_interrupted_computation_is_retried_by_the_next_reader():
    attempts = []

    def compute(value):
        attempts.append(value)
        if len(attempts) == 1:
            raise KeyboardInterrupt
        return value * 10

    batch = LazyBatch(range(1), compute)
    with pytest.raises(KeyboardInterrupt):
        batch[0]
    assert batch.fetched == {}
    assert batch[0] == 0
    assert attempts == [0, 0]


# -- errors -----------------------------------------------------------------


def test_raise_mode_raises_when_the_failed_item_is_reached():
    spy = Spy(fail_at={2})
    batch = LazyBatch(range(5), spy, max_workers=2)
    seen = []
    with pytest.raises(ValueError, match="boom 2"):
        for result in batch:
            seen.append(result)
    assert seen == [0, 10]

    # The items after the failure are not lost: they are cached or computable.
    assert batch[3] == 30
    assert batch[4] == 40
    assert isinstance(batch.fetched[2], ValueError)
    # The failure itself is cached; it is not recomputed.
    with pytest.raises(ValueError, match="boom 2"):
        batch[2]
    assert spy.calls.count(2) == 1


def test_return_mode_yields_the_exception_in_place():
    spy = Spy(fail_at={2})
    batch = LazyBatch(range(5), spy, max_workers=2, errors="return")

    results = list(batch)

    assert results[:2] == [0, 10]
    assert isinstance(results[2], ValueError)
    assert str(results[2]) == "boom 2"
    assert results[3:] == [30, 40]


def test_results_follow_the_error_mode():
    spy = Spy(fail_at={1, 3})
    raising = LazyBatch(range(5), spy, errors="raise")
    with pytest.raises(ValueError, match="boom 1"):
        raising.results()
    # Everything ran despite the failures.
    assert sorted(spy.calls) == [0, 1, 2, 3, 4]
    assert len(raising.fetched) == 5

    returning = LazyBatch(range(5), Spy(fail_at={1, 3}), errors="return")
    results = returning.results()
    assert results[0] == 0
    assert isinstance(results[1], ValueError)
    assert results[2] == 20
    assert isinstance(results[3], ValueError)
    assert results[4] == 40


def test_getitem_follows_the_error_mode():
    with pytest.raises(ValueError, match="boom 0"):
        LazyBatch([0], Spy(fail_at={0}))[0]
    returned = LazyBatch([0], Spy(fail_at={0}), errors="return")[0]
    assert isinstance(returned, ValueError)
    assert isinstance(LazyBatch([0, 1], Spy(fail_at={0}), errors="return")[0:2][0], ValueError)


def test_base_exceptions_are_not_swallowed():
    def compute(value):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        LazyBatch([1], compute, errors="return")[0]
    with pytest.raises(KeyboardInterrupt):
        list(LazyBatch([1, 2], compute, errors="return"))


# -- export -----------------------------------------------------------------


class Row:
    def __init__(self, value):
        self.value = value

    def to_dict(self):
        return {"label": self.value, "score": self.value * 10}


def test_to_df_follows_the_error_mode():
    pandas = pytest.importorskip("pandas")

    batch = LazyBatch(range(3), Row, max_workers=2)
    frame = batch.to_df()
    assert isinstance(frame, pandas.DataFrame)
    assert list(frame.columns) == ["label", "score"]
    assert frame["score"].tolist() == [0, 10, 20]

    def failing(value):
        if value == 1:
            raise ValueError("boom")
        return Row(value)

    with pytest.raises(ValueError, match="boom"):
        LazyBatch(range(3), failing).to_df()

    frame = LazyBatch(range(3), failing, errors="return").to_df()
    assert len(frame) == 3
    assert list(frame.columns) == ["label", "score", "error"]
    assert frame["score"].tolist()[0] == 0
    assert frame["score"].tolist()[2] == 20
    assert pandas.isna(frame.loc[1, "score"])
    assert isinstance(frame.loc[1, "error"], ValueError)
    assert frame["error"].isna().tolist() == [True, False, True]


def test_to_df_labels_failed_rows_when_given_a_label_function():
    pytest.importorskip("pandas")

    def failing(value):
        if value == 1:
            raise ValueError("boom")
        return Row(value)

    frame = LazyBatch(range(3), failing, errors="return", label=lambda value: value).to_df()
    assert frame["label"].tolist() == [0, 1, 2]
    assert isinstance(frame.loc[1, "error"], ValueError)


def test_to_df_reports_pandas_missing():
    try:
        import pandas  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("pandas is installed")
    with pytest.raises(ImportError, match="pandas is required"):
        LazyBatch(range(3), Row).to_df()
