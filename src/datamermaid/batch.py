"""Eager and streaming results over one bounded, ordered executor."""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable, Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, cast, overload

from .pagination import to_dataframe

if TYPE_CHECKING:
    import pandas

__all__ = ["Batch", "BatchFailure", "BatchStream"]

I = TypeVar("I")  # noqa: E741
T = TypeVar("T", covariant=True)
DEFAULT_MAX_WORKERS = 8
#: How many inputs per worker a stream may start ahead of the next result it yields.
READAHEAD = 4


@dataclass(frozen=True)
class _Outcome(Generic[I, T]):
    item: I
    value: T | None = None
    error: Exception | None = None


def _validate_execution(max_workers: int, errors: str) -> None:
    if isinstance(max_workers, bool) or not isinstance(max_workers, int):
        raise TypeError("max_workers must be an integer")
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    if errors not in ("raise", "return"):
        raise ValueError(f"errors must be 'raise' or 'return', got {errors!r}")


def _note_failure(
    error: Exception,
    position: int,
    item: Any,
    label: Callable[[Any], Any] | None,
    error_context: Callable[[Any], dict[str, Any]] | None,
) -> None:
    """Add a note to ``error`` naming the input that raised it.

    The note shows in the traceback on Python 3.11 and later, and is in
    ``error.__notes__`` on every version.
    """
    details: dict[str, Any] = {}
    try:
        if label is not None:
            details["label"] = label(item)
        if error_context is not None:
            details.update(error_context(item))
    except Exception:  # never hide the real error behind a broken describer
        details = {}
    shown = ", ".join(f"{key}={value!r}" for key, value in details.items() if value is not None)
    note = f"batch input {position} failed" + (f": {shown}" if shown else "")
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(note)
    else:  # pragma: no cover - Python 3.10
        error.__notes__ = [*getattr(error, "__notes__", []), note]  # type: ignore[attr-defined]


def _execute(
    inputs: Iterable[I],
    compute: Callable[[I], T],
    max_workers: int,
    *,
    ordered: bool,
) -> Generator[tuple[int, _Outcome[I, T]], None, None]:
    """Yield ``(position, outcome)`` pairs, keeping ``max_workers`` computations running.

    A new input is started as soon as any computation finishes, so one slow
    input does not leave the other workers idle.  Unordered, outcomes come in
    completion order.  Ordered, they come in input order, and at most
    ``READAHEAD * max_workers`` inputs are started but not yet yielded; only a
    computation slower than that many others makes the workers wait for it.

    Error policy belongs to the consumer. Closing stops input consumption and
    waits for computations already in flight.
    """
    iterator = enumerate(inputs)
    capacity = max_workers * READAHEAD if ordered else max_workers
    pool = ThreadPoolExecutor(max_workers=max_workers)
    running: dict[Future[T], tuple[int, I]] = {}
    finished: dict[int, _Outcome[I, T]] = {}
    next_position = 0
    exhausted = False
    try:
        while True:
            while next_position in finished:
                yield next_position, finished.pop(next_position)
                next_position += 1
            while (
                not exhausted
                and len(running) < max_workers
                and len(running) + len(finished) < capacity
            ):
                try:
                    position, item = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                running[pool.submit(compute, item)] = (position, item)
            if not running:
                return
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                position, item = running.pop(future)
                try:
                    outcome = _Outcome(item, value=future.result())
                except Exception as exc:
                    outcome = _Outcome(item, error=exc)
                if ordered:
                    finished[position] = outcome
                else:
                    yield position, outcome
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


class Batch(Generic[I, T]):
    """Compute all inputs and store results in input order.

    Construction waits for every computation to finish. ``max_workers``
    computations run at once, and a new input is consumed as soon as any of
    them finishes, whatever its position. ``compute`` must be thread-safe.
    Completed inputs and results are retained, and reading the completed batch
    never starts more work.

    With ``errors="raise"`` (the default), construction raises the first error
    as soon as it happens: no more inputs start, the computations already
    running finish, and the original exception is raised with a note naming the
    failed input's position, label and context. With ``errors="return"``,
    BatchFailure objects occupy their input positions and every input runs.
    Only ordinary ``Exception`` subclasses are captured; interrupts propagate.

    Args:
        inputs: Inputs to consume and compute once each.
        compute: Function called for each input.
        max_workers: Maximum concurrent computations; defaults to eight.
        errors: Raise the first error to happen, or retain errors as results.
        label: Optional input label for failed rows in ``to_df()`` and for
            the note on a raised error.
        error_context: Optional additional fields for failed rows and for the
            note on a raised error.
    """

    @overload
    def __init__(
        self,
        inputs: Iterable[I],
        compute: Callable[[I], T],
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise"] = "raise",
        label: Callable[[I], Any] | None = None,
        error_context: Callable[[I], dict[str, Any]] | None = None,
    ) -> None: ...

    @overload
    def __init__(
        self: Batch[I, T | BatchFailure[I]],
        inputs: Iterable[I],
        compute: Callable[[I], T],
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["return"],
        label: Callable[[I], Any] | None = None,
        error_context: Callable[[I], dict[str, Any]] | None = None,
    ) -> None: ...

    @overload
    def __init__(
        self: Batch[I, T | BatchFailure[I]],
        inputs: Iterable[I],
        compute: Callable[[I], T],
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
        label: Callable[[I], Any] | None = None,
        error_context: Callable[[I], dict[str, Any]] | None = None,
    ) -> None: ...

    def __init__(
        self,
        inputs: Iterable[I],
        compute: Callable[[I], T],
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
        label: Callable[[I], Any] | None = None,
        error_context: Callable[[I], dict[str, Any]] | None = None,
    ) -> None:
        _validate_execution(max_workers, errors)
        self._inputs: list[I] = []
        self._results: list[T] = []
        self.max_workers = max_workers
        self.errors = errors
        self._label = label
        self._error_context = error_context

        completed: list[tuple[int, _Outcome[I, T]]] = []
        with closing(_execute(inputs, compute, max_workers, ordered=False)) as outcomes:
            for position, outcome in outcomes:
                if outcome.error is not None and errors == "raise":
                    _note_failure(outcome.error, position, outcome.item, label, error_context)
                    raise outcome.error
                completed.append((position, outcome))
        completed.sort(key=lambda pair: pair[0])
        for _, outcome in completed:
            self._inputs.append(outcome.item)
            if outcome.error is not None:
                self._results.append(cast("T", BatchFailure(outcome.item, outcome.error)))
            else:
                self._results.append(cast("T", outcome.value))

    @property
    def inputs(self) -> Sequence[I]:
        """The inputs in result order, as a copy."""
        return list(self._inputs)

    def __len__(self) -> int:
        return len(self._results)

    def __repr__(self) -> str:
        return f"<Batch results={len(self)} max_workers={self.max_workers}>"

    def __iter__(self) -> Iterator[T]:
        return iter(self._results)

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> list[T]: ...

    def __getitem__(self, index: int | slice) -> T | list[T]:
        return self._results[index]

    def results(self) -> list[T]:
        """Return a copy of the completed results in input order."""
        return list(self._results)

    def to_df(self, **kwargs: Any) -> pandas.DataFrame:
        """Export completed results, with an ``error`` column for failed rows.

        Successful results use their ``to_dict()`` representation. Failed rows
        retain their input label if supplied. Requires the optional pandas extra.
        """
        rows: list[Any] = []
        for item, result in zip(self._inputs, self._results, strict=True):
            if isinstance(result, BatchFailure):
                row: dict[str, Any] = {"error": result}
                if self._label is not None:
                    row["label"] = self._label(item)
                if self._error_context is not None:
                    row.update(self._error_context(item))
                rows.append(row)
            else:
                rows.append(result)
        return to_dataframe(rows, **kwargs)


class BatchFailure(Exception, Generic[I]):
    """A batch failure retaining the input and original exception."""

    def __init__(self, item: I, error: Exception) -> None:
        super().__init__(str(error))
        self.item = item
        self.error = error


class BatchStream(Generic[I, T]):
    """Single-pass, input-ordered results with bounded memory.

    Work starts on iteration. ``max_workers`` computations run at once, and a
    new input starts whenever one finishes. Results that finish ahead of an
    earlier input wait for it, up to ``READAHEAD * max_workers`` inputs in
    total; past that, the stream waits for the earlier input before it starts
    more. Use as a context manager when stopping early;
    closing cancels queued work and waits for requests already in flight.
    Results are not retained. Keep the owning client open while consuming.
    """

    @overload
    def __init__(
        self,
        inputs: Iterable[I],
        compute: Callable[[I], T],
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise"] = "raise",
        label: Callable[[I], Any] | None = None,
        error_context: Callable[[I], dict[str, Any]] | None = None,
    ) -> None: ...

    @overload
    def __init__(
        self: BatchStream[I, T | BatchFailure[I]],
        inputs: Iterable[I],
        compute: Callable[[I], T],
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["return"],
        label: Callable[[I], Any] | None = None,
        error_context: Callable[[I], dict[str, Any]] | None = None,
    ) -> None: ...

    @overload
    def __init__(
        self: BatchStream[I, T | BatchFailure[I]],
        inputs: Iterable[I],
        compute: Callable[[I], T],
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
        label: Callable[[I], Any] | None = None,
        error_context: Callable[[I], dict[str, Any]] | None = None,
    ) -> None: ...

    def __init__(
        self,
        inputs: Iterable[I],
        compute: Callable[[I], T],
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
        label: Callable[[I], Any] | None = None,
        error_context: Callable[[I], dict[str, Any]] | None = None,
    ) -> None:
        _validate_execution(max_workers, errors)
        self._iterator = self._run(inputs, compute, max_workers, errors, label, error_context)

    @staticmethod
    def _run(
        inputs: Iterable[I],
        compute: Callable[[I], T],
        max_workers: int,
        errors: str,
        label: Callable[[I], Any] | None,
        error_context: Callable[[I], dict[str, Any]] | None,
    ) -> Generator[T, None, None]:
        with closing(_execute(inputs, compute, max_workers, ordered=True)) as outcomes:
            for position, outcome in outcomes:
                if outcome.error is not None:
                    if errors == "raise":
                        _note_failure(outcome.error, position, outcome.item, label, error_context)
                        raise outcome.error
                    yield cast("T", BatchFailure(outcome.item, outcome.error))
                else:
                    yield cast("T", outcome.value)

    def __iter__(self) -> BatchStream[I, T]:
        return self

    def __next__(self) -> T:
        return next(self._iterator)

    def close(self) -> None:
        """Stop consuming inputs and finish any in-flight requests."""
        self._iterator.close()

    def __enter__(self) -> BatchStream[I, T]:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
