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


def _describe(
    item: Any,
    label: Callable[[Any], Any] | None,
    error_context: Callable[[Any], dict[str, Any]] | None,
) -> dict[str, Any]:
    """The label and context fields of a failed input, or ``{}`` if a describer fails."""
    details: dict[str, Any] = {}
    try:
        if label is not None:
            details["label"] = label(item)
        if error_context is not None:
            details.update(error_context(item))
    except Exception:  # never hide the real error behind a broken describer
        return {}
    return details


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
    details = _describe(item, label, error_context)
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
                failure = BatchFailure(
                    outcome.item, outcome.error, _describe(outcome.item, label, error_context)
                )
                self._results.append(cast("T", failure))
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
        """Export completed results, with ``error`` and ``error_type`` columns for failed rows.

        Successful results use their ``to_dict()`` representation, and failed
        rows use [`BatchFailure.to_dict`][datamermaid.batch.BatchFailure.to_dict]:
        the input's label and context, the error message and the exception's
        class name.  Requires the optional pandas extra.
        """
        rows = [
            result.to_dict() if isinstance(result, BatchFailure) else result
            for result in self._results
        ]
        return to_dataframe(rows, **kwargs)


class BatchFailure(Exception, Generic[I]):
    """A batch failure retaining the input and original exception.

    ``details`` holds the input's label and context, the same fields a
    successful row carries for it (for a zonal stats task: ``label``,
    ``source`` and the ``stac_<field>`` columns).
    """

    def __init__(self, item: I, error: Exception, details: dict[str, Any] | None = None) -> None:
        super().__init__(str(error))
        self.item = item
        self.error = error
        self.details: dict[str, Any] = dict(details or {})

    def __reduce__(self) -> tuple[Any, ...]:
        # `args` holds only the message, so rebuild from the real arguments;
        # otherwise pickling and `copy.copy` fail.
        return (type(self), (self.item, self.error, self.details))

    def to_dict(self) -> dict[str, Any]:
        """One row: ``details``, then the ``error`` message and ``error_type``.

        The row has the same identifying columns as a successful result's
        ``to_dict()``, so success and failure rows written to one file or
        DataFrame share a layout.
        """
        return {**self.details, "error": str(self.error), "error_type": type(self.error).__name__}


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
                    details = _describe(outcome.item, label, error_context)
                    yield cast("T", BatchFailure(outcome.item, outcome.error, details))
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
