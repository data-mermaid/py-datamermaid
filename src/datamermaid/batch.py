"""Eager and streaming results over one bounded, ordered executor."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Generator, Iterable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
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


def _execute(
    inputs: Iterable[I], compute: Callable[[I], T], max_workers: int
) -> Generator[_Outcome[I, T], None, None]:
    """Yield task outcomes in input order, retaining at most max_workers futures.

    Error policy belongs to the consumer. Closing stops input consumption,
    cancels queued work, and waits for computations already in flight.
    """
    iterator = iter(inputs)
    pool = ThreadPoolExecutor(max_workers=max_workers)
    pending: deque[tuple[I, Future[T]]] = deque()
    try:
        for _ in range(max_workers):
            try:
                item = next(iterator)
            except StopIteration:
                break
            pending.append((item, pool.submit(compute, item)))
        while pending:
            item, future = pending.popleft()
            try:
                outcome = _Outcome(item, value=future.result())
            except Exception as exc:
                outcome = _Outcome(item, error=exc)
            yield outcome
            try:
                item = next(iterator)
            except StopIteration:
                continue
            pending.append((item, pool.submit(compute, item)))
    finally:
        for _, future in pending:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)


class Batch(Generic[I, T]):
    """Compute all inputs and store results in input order.

    Construction waits for every computation to finish. At most ``max_workers``
    computations are pending at once; inputs are consumed as capacity becomes
    available. ``compute`` must be thread-safe. Completed inputs and results are
    retained, and reading the completed batch never starts more work.

    With ``errors="raise"`` (the default), construction raises the first failed
    input's exception after all inputs finish. With ``errors="return"``, BatchFailure objects
    occupy their input positions. Only ordinary ``Exception`` subclasses are
    captured; interrupts propagate.

    Args:
        inputs: Inputs to consume and compute once each.
        compute: Function called for each input.
        max_workers: Maximum concurrent computations; defaults to eight.
        errors: Raise the first error in input order, or retain errors as results.
        label: Optional input label for failed rows in ``to_df()``.
        error_context: Optional additional fields for failed rows.
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

        with closing(_execute(inputs, compute, max_workers)) as outcomes:
            for outcome in outcomes:
                self._inputs.append(outcome.item)
                if outcome.error is not None:
                    self._results.append(cast("T", BatchFailure(outcome.item, outcome.error)))
                else:
                    self._results.append(cast("T", outcome.value))
        if errors == "raise":
            for result in self._results:
                if isinstance(result, BatchFailure):
                    raise result.error

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
    """Single-pass, input-ordered results with at most max_workers pending inputs.

    Work starts on iteration. Use as a context manager when stopping early;
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
    ) -> None: ...

    @overload
    def __init__(
        self: BatchStream[I, T | BatchFailure[I]],
        inputs: Iterable[I],
        compute: Callable[[I], T],
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["return"],
    ) -> None: ...

    @overload
    def __init__(
        self: BatchStream[I, T | BatchFailure[I]],
        inputs: Iterable[I],
        compute: Callable[[I], T],
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
    ) -> None: ...

    def __init__(
        self,
        inputs: Iterable[I],
        compute: Callable[[I], T],
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
    ) -> None:
        _validate_execution(max_workers, errors)
        self._iterator = self._run(inputs, compute, max_workers, errors)

    @staticmethod
    def _run(
        inputs: Iterable[I], compute: Callable[[I], T], max_workers: int, errors: str
    ) -> Generator[T, None, None]:
        with closing(_execute(inputs, compute, max_workers)) as outcomes:
            for outcome in outcomes:
                if outcome.error is not None:
                    if errors == "raise":
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
