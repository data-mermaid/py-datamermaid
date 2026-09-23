"""Lazy, parallel, caching collections over one computation per input.

A [`LazyBatch`][datamermaid.batch.LazyBatch] is to a list of independent requests
what [`PaginatedList`][datamermaid.pagination.PaginatedList] is to a paginated
endpoint: nothing runs until it is iterated, indexed or materialised, results are
cached by position, and ``to_df()`` flattens them into a DataFrame.  The
computations run on a bounded thread pool, so a batch of a hundred areas of
interest becomes a hundred HTTP requests with at most ``max_workers`` of them in
flight at once.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, cast, overload

from .pagination import to_dataframe

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas

__all__ = ["LazyBatch"]

#: The type of one input.
I = TypeVar("I")  # noqa: E741 - `I` for input, next to `T` for its result.
#: The type ``compute`` returns for one input.
T = TypeVar("T")

DEFAULT_MAX_WORKERS = 8


class LazyBatch(Generic[I, T]):
    """Results of ``compute(input)`` for every input, computed lazily and in parallel.

    Nothing runs at construction, and ``len(batch)`` is just the number of
    inputs.  Iterating yields results in input order while keeping at most
    ``max_workers`` computations in flight ahead of the consumer, so a loop
    that stops early wastes at most one window of work.  Indexing computes just
    the item asked for; a slice computes only the items it covers, in parallel;
    [`results`][.results] computes everything.  Every result is cached by
    position, so nothing is ever computed twice.

    In the SDK each computation is one HTTP request, which makes ``max_workers``
    the knob for how hard the service is pushed.  The client's throttle gate
    (see [`MermaidClient`][datamermaid.client.MermaidClient]) is shared by the
    workers: one ``429`` pauses all of them, not just the thread that saw it.

    A computation that raises does not spoil the rest.  Its exception is cached
    like any result and, with ``errors="raise"``, re-raised when that position is
    reached, after everything before it was yielded.  With ``errors="return"``
    the exception instance is yielded in place of the result and iteration
    carries on, so a partly failing batch can still be inspected.  Only
    ``Exception`` subclasses are captured; ``KeyboardInterrupt`` and friends
    propagate.

    Args:
        inputs: The inputs, one per result.  Consumed into a list up front.
        compute: Called once per input, possibly from a worker thread, so it
            must be thread-safe.
        max_workers: Most computations in flight at once, and the size of the
            prefetch window when iterating.
        errors: ``"raise"`` re-raises a failed item's exception at its position;
            ``"return"`` yields the exception object instead.
        label: Called with an input to get the ``label`` of its row when its
            computation failed, so ``to_df()`` can tell failed rows apart.
            Omitted, a failed row has only its ``error``.

    Raises:
        ValueError: If ``max_workers`` is below ``1`` or ``errors`` is unknown.

    Example:
        ```python
        batch = client.zonal_stats.raster.batch(sites, url=cog, stats=["mean"])
        len(batch)          # no requests yet
        batch[0]            # one request
        for result in batch:  # the rest, up to 8 at a time
            print(result.label, result["band_1"]["mean"])
        frame = batch.to_df()  # one wide row per site
        ```
    """

    def __init__(
        self,
        inputs: Iterable[I],
        compute: Callable[[I], T],
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        errors: Literal["raise", "return"] = "raise",
        label: Callable[[I], Any] | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if errors not in ("raise", "return"):
            raise ValueError(f"errors must be 'raise' or 'return', got {errors!r}")
        self._inputs: list[I] = list(inputs)
        self._compute = compute
        self._max_workers = max_workers
        self._errors = errors
        self._label = label
        self._lock = threading.Lock()
        # Index -> result, or the Exception the computation raised.
        self._results: dict[int, Any] = {}
        # Index -> an event set when the computation running for it finishes,
        # so a second reader waits for that one instead of starting another.
        self._inflight: dict[int, threading.Event] = {}

    # -- computing ----------------------------------------------------------

    def _run(self, index: int) -> None:
        """Compute item ``index`` and cache it, unless it is cached or already running.

        A caller that finds the item running waits for that computation, so an
        item is computed once however many threads ask for it at the same time.
        """

        while True:
            with self._lock:
                if index in self._results:
                    return
                running = self._inflight.get(index)
                if running is None:
                    done = self._inflight[index] = threading.Event()
                    break
            running.wait()
            # Loop: the item is cached now, unless the computation was cut
            # short by a BaseException, in which case this caller takes over.

        try:
            try:
                value: Any = self._compute(self._inputs[index])
            except Exception as exc:  # stored, and re-raised at its position
                value = exc
            with self._lock:
                self._results[index] = value
        finally:
            with self._lock:
                del self._inflight[index]
            done.set()

    def _resolve(self, index: int) -> T:
        """The cached result at ``index``, honouring the error mode."""

        with self._lock:
            value = self._results[index]
        if isinstance(value, Exception) and self._errors == "raise":
            raise value
        return cast("T", value)

    def _uncached(self, indexes: Iterable[int]) -> list[int]:
        with self._lock:
            return [index for index in indexes if index not in self._results]

    def _compute_many(self, indexes: Sequence[int]) -> list[T]:
        """Compute the missing items among ``indexes`` in parallel, then resolve them all."""

        missing = self._uncached(indexes)
        if len(missing) == 1:
            self._run(missing[0])
        elif missing:
            with ThreadPoolExecutor(max_workers=min(self._max_workers, len(missing))) as pool:
                # `list` waits for every worker, and surfaces a BaseException.
                list(pool.map(self._run, missing))
        return [self._resolve(index) for index in indexes]

    # -- introspection ------------------------------------------------------

    @property
    def inputs(self) -> Sequence[I]:
        """The inputs, in order."""

        return list(self._inputs)

    @property
    def max_workers(self) -> int:
        """Most computations in flight at once."""

        return self._max_workers

    @property
    def errors(self) -> Literal["raise", "return"]:
        """What happens at a failed item: ``"raise"`` or ``"return"``."""

        return self._errors

    @property
    def fetched(self) -> dict[int, T | Exception]:
        """The results computed so far, keyed by position, without computing any.

        A failed item appears as its exception whatever the error mode, so the
        mapping tells the whole story of what has run.
        """

        with self._lock:
            return dict(sorted(self._results.items()))

    def __len__(self) -> int:
        return len(self._inputs)

    def __repr__(self) -> str:
        with self._lock:
            computed = len(self._results)
        return (
            f"<LazyBatch computed={computed}/{len(self._inputs)} max_workers={self._max_workers}>"
        )

    # -- access -------------------------------------------------------------

    def __iter__(self) -> Iterator[T]:
        """Yield every result in order, prefetching up to ``max_workers`` ahead.

        A pool is created for the pass and shut down when the loop ends or the
        consumer breaks out, cancelling whatever has not started yet.
        """

        pending = iter(self._uncached(range(len(self._inputs))))
        futures: dict[int, Future[None]] = {}
        pool = ThreadPoolExecutor(max_workers=self._max_workers)

        def prefetch() -> None:
            index = next(pending, None)
            if index is not None:
                futures[index] = pool.submit(self._run, index)

        try:
            for _ in range(self._max_workers):
                prefetch()
            for index in range(len(self._inputs)):
                future = futures.pop(index, None)
                if future is not None:
                    future.result()
                yield self._resolve(index)
                # Refill after the hand-off, so a consumer that stops here has
                # caused at most `max_workers` computations.
                prefetch()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> list[T]: ...

    def __getitem__(self, index: int | slice) -> T | list[T]:
        positions = range(len(self._inputs))
        if isinstance(index, slice):
            return self._compute_many(list(positions[index]))
        try:
            position = positions[index]
        except IndexError:
            raise IndexError("LazyBatch index out of range") from None
        return self._compute_many([position])[0]

    def results(self) -> list[T]:
        """Compute every item, in parallel, and return them in input order."""

        return self._compute_many(list(range(len(self._inputs))))

    # -- export -------------------------------------------------------------

    def to_df(self, **kwargs: Any) -> pandas.DataFrame:
        """Compute every item and return the results as a DataFrame.

        Each result's ``to_dict()`` becomes a row (for a
        [`ZonalStatsResult`][datamermaid.models.ZonalStatsResult], one wide row
        keyed by its ``label``).  Rows are in input order, so row ``i`` belongs
        to ``inputs[i]``.  With ``errors="return"`` a failed item is a row whose
        ``error`` column holds the exception and whose other columns are empty,
        except ``label`` when the batch was given a ``label`` function.
        Requires the optional ``pandas`` extra.
        """

        rows = [
            self._error_row(index, item) if isinstance(item, Exception) else item
            for index, item in enumerate(self.results())
        ]
        return to_dataframe(rows, **kwargs)

    def _error_row(self, index: int, error: Exception) -> dict[str, Any]:
        if self._label is None:
            return {"error": error}
        return {"label": self._label(self._inputs[index]), "error": error}
