"""Eager batches: ordering, bounded concurrency, failures and export."""

from __future__ import annotations

import threading
import time

import pytest

import datamermaid
from datamermaid import Batch
from datamermaid.batch import DEFAULT_MAX_WORKERS, READAHEAD

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


def test_defaults():
    batch = Batch([], lambda value: value)
    assert batch.max_workers == DEFAULT_MAX_WORKERS == 8
    assert len(batch) == 0
    assert list(batch) == []
    assert batch.results() == []


def test_inputs_are_consumed_once():
    batch = Batch(iter([1, 2, 3]), lambda value: value)
    assert len(batch) == 3
    assert batch.results() == [1, 2, 3]
    assert batch.results() == [1, 2, 3]


@pytest.mark.parametrize("max_workers", [0, -1])
def test_max_workers_must_be_positive(max_workers):
    with pytest.raises(ValueError, match="max_workers"):
        Batch([1], lambda value: value, max_workers=max_workers)


def test_unknown_error_mode_is_rejected():
    with pytest.raises(ValueError, match="errors"):
        Batch([1], lambda value: value, errors="ignore")


def test_is_exported():
    assert datamermaid.Batch is Batch
    assert "Batch" in datamermaid.__all__


def test_never_more_than_max_workers_in_flight():
    spy = Spy(delay=0.005)
    batch = Batch(range(10), spy, max_workers=3)

    assert list(batch) == [value * 10 for value in range(10)]
    assert spy.peak <= 3
    assert spy.peak >= 1


def test_at_least_two_run_concurrently():
    """A barrier of two: if the computations were serial this would time out."""

    barrier = threading.Barrier(2, timeout=TIMEOUT)

    def compute(value):
        barrier.wait()
        return value

    batch = Batch(range(2), compute, max_workers=2)
    assert list(batch) == [0, 1]


def test_max_workers_one_is_serial():
    spy = Spy(delay=0.001)
    batch = Batch(range(5), spy, max_workers=1)
    assert list(batch) == [0, 10, 20, 30, 40]
    assert spy.peak == 1
    assert spy.calls == [0, 1, 2, 3, 4]


def test_results_with_max_workers_larger_than_the_batch():
    spy = Spy()
    batch = Batch(range(2), spy, max_workers=50)
    assert batch.results() == [0, 10]


def test_return_mode_yields_the_exception_in_place():
    spy = Spy(fail_at={2})
    batch = Batch(range(5), spy, max_workers=2, errors="return")

    results = list(batch)

    assert results[:2] == [0, 10]
    assert isinstance(results[2].error, ValueError)
    assert str(results[2]) == "boom 2"
    assert results[3:] == [30, 40]


def test_getitem_follows_the_error_mode():
    with pytest.raises(ValueError, match="boom 0"):
        Batch([0], Spy(fail_at={0}))[0]
    returned = Batch([0], Spy(fail_at={0}), errors="return")[0]
    assert isinstance(returned.error, ValueError)
    assert isinstance(Batch([0, 1], Spy(fail_at={0}), errors="return")[0:2][0].error, ValueError)


def test_base_exceptions_are_not_swallowed():
    def compute(value):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        Batch([1], compute, errors="return")[0]
    with pytest.raises(KeyboardInterrupt):
        list(Batch([1, 2], compute, errors="return"))


class Row:
    def __init__(self, value):
        self.value = value

    def to_dict(self):
        return {"label": self.value, "score": self.value * 10}


def test_to_df_follows_the_error_mode():
    pandas = pytest.importorskip("pandas")

    batch = Batch(range(3), Row, max_workers=2)
    frame = batch.to_df()
    assert isinstance(frame, pandas.DataFrame)
    assert list(frame.columns) == ["label", "score"]
    assert frame["score"].tolist() == [0, 10, 20]

    def failing(value):
        if value == 1:
            raise ValueError("boom")
        return Row(value)

    with pytest.raises(ValueError, match="boom"):
        Batch(range(3), failing).to_df()

    frame = Batch(range(3), failing, errors="return").to_df()
    assert len(frame) == 3
    assert list(frame.columns) == ["label", "score", "error"]
    assert frame["score"].tolist()[0] == 0
    assert frame["score"].tolist()[2] == 20
    assert pandas.isna(frame.loc[1, "score"])
    assert isinstance(frame.loc[1, "error"].error, ValueError)
    assert frame["error"].isna().tolist() == [True, False, True]


def test_to_df_labels_failed_rows_when_given_a_label_function():
    pytest.importorskip("pandas")

    def failing(value):
        if value == 1:
            raise ValueError("boom")
        return Row(value)

    frame = Batch(range(3), failing, errors="return", label=lambda value: value).to_df()
    assert frame["label"].tolist() == [0, 1, 2]
    assert isinstance(frame.loc[1, "error"].error, ValueError)


def test_to_df_reports_pandas_missing():
    try:
        import pandas  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("pandas is installed")
    with pytest.raises(ImportError, match="pandas is required"):
        Batch(range(3), Row).to_df()


def test_construction_completes_work_and_reads_do_not_repeat_it():
    spy = Spy()
    batch = Batch(range(4), spy, max_workers=2)
    assert sorted(spy.calls) == [0, 1, 2, 3]
    assert batch.inputs == [0, 1, 2, 3]
    assert list(batch) == [0, 10, 20, 30]
    assert batch[-1] == 30
    assert batch[1:3] == [10, 20]
    copy = batch.results()
    copy.clear()
    assert batch.results() == [0, 10, 20, 30]
    assert len(spy.calls) == 4
    assert repr(batch) == "<Batch results=4 max_workers=2>"


def test_raise_mode_finishes_all_inputs_then_raises_first_error_in_input_order():
    second_finished = threading.Event()
    calls = []

    def compute(value):
        calls.append(value)
        if value == 0:
            assert second_finished.wait(TIMEOUT)
            raise ValueError("first")
        if value == 1:
            second_finished.set()
            raise ValueError("second")
        return value

    with pytest.raises(ValueError, match="first"):
        Batch(range(3), compute, max_workers=2)
    assert sorted(calls) == [0, 1, 2]


@pytest.mark.parametrize("stream", [False, True])
def test_consumes_inputs_only_as_execution_capacity_becomes_available(stream):
    """A large iterable must not be exhausted before the first work completes."""
    completed = [threading.Event() for _ in range(20)]

    def inputs():
        for index in range(len(completed)):
            done = sum(event.is_set() for event in completed)
            assert index - done < 3, "inputs consumed ahead of worker capacity"
            yield index

    def compute(index):
        completed[index].set()
        return index

    factory = datamermaid.BatchStream if stream else Batch
    assert list(factory(inputs(), compute, max_workers=3)) == list(range(20))


@pytest.mark.parametrize("stream", [False, True])
def test_a_slow_input_does_not_leave_the_other_workers_idle(stream):
    """The first input finishes only after later ones have run beside it."""
    later_done = threading.Event()

    def compute(index):
        if index == 0:
            assert later_done.wait(TIMEOUT), "workers waited on the slow input"
        elif index == 5:
            later_done.set()
        return index

    factory = datamermaid.BatchStream if stream else Batch
    assert list(factory(range(8), compute, max_workers=2)) == list(range(8))


def test_stream_bounds_how_far_it_reads_ahead_of_a_slow_input():
    release = threading.Event()
    consumed = []

    def inputs():
        for index in range(100):
            consumed.append(index)
            yield index

    def compute(index):
        if index == 0:
            assert release.wait(TIMEOUT)
        return index

    with datamermaid.BatchStream(inputs(), compute, max_workers=2) as results:
        first = threading.Thread(target=lambda: next(results))
        first.start()
        deadline = time.monotonic() + TIMEOUT
        while len(consumed) < 2 * READAHEAD and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        assert len(consumed) == 2 * READAHEAD
        release.set()
        first.join(TIMEOUT)


@pytest.mark.parametrize("stream", [False, True])
def test_successful_none_is_not_confused_with_failure(stream):
    factory = datamermaid.BatchStream if stream else Batch
    assert list(factory([1, 2], lambda _: None)) == [None, None]


def test_stream_raise_stops_consuming_more_inputs():
    consumed = []
    original = ValueError("stop")

    def inputs():
        for index in range(100):
            consumed.append(index)
            yield index

    def compute(index):
        raise original

    with datamermaid.BatchStream(inputs(), compute, max_workers=1) as results:
        with pytest.raises(ValueError) as raised:
            next(results)
        assert raised.value is original
    assert consumed == [0]


def test_stream_close_waits_for_running_work_without_scheduling_more():
    started = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    calls = []

    def compute(index):
        calls.append(index)
        if index == 0:
            assert started.wait(TIMEOUT)
        else:
            started.set()
            assert release.wait(TIMEOUT)
        return index

    results = datamermaid.BatchStream(range(100), compute, max_workers=2)
    assert next(results) == 0

    def close():
        results.close()
        closed.set()

    closer = threading.Thread(target=close)
    closer.start()
    try:
        assert not closed.wait(0.05)
    finally:
        release.set()
        closer.join(TIMEOUT)
    assert closed.is_set()
    assert sorted(calls) == [0, 1]
    assert list(results) == []


@pytest.mark.parametrize("stream", [False, True])
def test_failing_input_iterator_drains_running_work(stream):
    finished = threading.Event()
    original = ValueError("source failed")

    def inputs():
        yield 1
        raise original

    def compute(index):
        finished.set()
        return index

    factory = datamermaid.BatchStream if stream else Batch
    with pytest.raises(ValueError) as raised:
        list(factory(inputs(), compute, max_workers=2, errors="return"))
    assert raised.value is original
    assert finished.is_set()


def test_stream_propagates_interrupts_and_closes():
    def compute(index):
        raise KeyboardInterrupt

    with datamermaid.BatchStream(range(10), compute, max_workers=1) as results:
        with pytest.raises(KeyboardInterrupt):
            next(results)
        assert list(results) == []


@pytest.mark.parametrize("executor", [Batch, datamermaid.BatchStream])
@pytest.mark.parametrize("workers", [True, False, 1.5, "2", None])
def test_worker_type_is_rejected_without_consuming_input(executor, workers):
    def inputs():
        pytest.fail("invalid configuration must not consume input")
        yield 1

    with pytest.raises(TypeError, match="max_workers"):
        executor(inputs(), lambda value: value, max_workers=workers)
