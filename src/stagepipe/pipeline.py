"""Staged pipeline for blocking code: every item moves to the next stage the moment that stage has
a free worker, with no barrier between stages. Standard library only, no server, nothing persisted.

    results = run(pages, [Stage("render", render, workers=4),
                          Stage("ocr", read_text, workers=3),
                          Stage("save", save, workers=1, ordered=True)],
                  max_in_flight=8)

Each stage function is fn(value, index) -> value; the value it returns goes to the next stage.
Slow stages get more workers, fast ones fewer; an item waits only when the next stage is full.

* ordered=True: the stage sees items in index order (use it for stages with cross-item state);
  it must have workers=1. Items finishing earlier wait in a small buffer.
* init=fn: called once in each worker thread; its return value (a model, a session, a client) is
  passed to the stage function as a third argument, fn(value, index, state).
* close=fn (needs init): close(state) is called once per worker, in the worker thread that created
  the state, after that worker's last stage call has returned: on a normal finish and also after a
  stage error, cancel or Ctrl-C. If it raises, other workers still close, the run's own error wins,
  and otherwise the first close error is raised from run().
* max_in_flight: at most this many items are between "fed" and "done" at once. It bounds memory
  (page images) and keeps a fast first stage from racing ahead of a slow one. Items are pulled
  from `items` lazily, so a generator is never materialised.
* keep_results=False: run() keeps nothing and returns None; use on_done to consume each result.
  Memory then stays flat however many items go through.
* on_error: what a failing stage function does. None or "raise" (default): the first exception
  stops feeding, drains the workers and is raised from run(). "collect": the item becomes a
  Failed(stage, index, exc) that skips the remaining stages and appears in the results (and in
  on_done), and the rest of the run carries on. A callable on_error(stage, index, value, exc)
  returns the value to carry on with (for example a marker), or raises to stop the run.
* cancelled(): checked before every stage call; when true the run stops and raises Cancelled.
  With partial=True, run() returns the results so far instead, with UNFINISHED for the rest.
* stats: pass a Stats() to get per-stage busy time, queue wait time and queue depth, and a
  report() that names the bottleneck. Interrupting the caller (Ctrl-C) stops the run promptly.
* nothing heavy is imported: no typing, dataclasses, inspect, re or asyncio. threading and the C
  queue load on the first run() call, not at `import stagepipe`.
"""
from __future__ import annotations

# Only what is used at call time is imported, and only when needed. `typing` (+0.7 MB resident) and
# `dataclasses` (+1.7 MB, it drags in inspect, re, ...) are deliberately not used: annotations are
# lazy (from __future__), and the small classes below use __slots__ instead of a dataclass.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Any, Callable, Iterable

_STOP = object()


def _never() -> bool:
    return False


def _zero() -> float:
    return 0.0


class Cancelled(Exception):
    pass


class Failed:
    """Stands in for an item whose stage raised, when run(on_error="collect")."""
    __slots__ = ("stage", "index", "exc")

    def __init__(self, stage: str, index: int, exc: BaseException) -> None:
        self.stage, self.index, self.exc = stage, index, exc

    def __repr__(self) -> str:
        return f"Failed(stage={self.stage!r}, index={self.index}, exc={self.exc!r})"


class _Unfinished:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNFINISHED"


UNFINISHED = _Unfinished()          # marks items that had not finished when a run was cancelled


class Stage:
    __slots__ = ("name", "fn", "workers", "ordered", "init", "close")

    def __init__(self, name: str, fn: Callable[..., Any], workers: int = 1, ordered: bool = False,
                 init: Callable[[], Any] | None = None, close: Callable[[Any], Any] | None = None) -> None:
        self.name, self.fn, self.workers, self.ordered, self.init, self.close = name, fn, workers, ordered, init, close

    def __repr__(self) -> str:
        return f"Stage({self.name!r}, workers={self.workers}, ordered={self.ordered})"


class StageStats:
    """Totals for one stage. wait_s is time items spent queued with every worker busy."""
    __slots__ = ("name", "workers", "items", "failed", "run_s", "wait_s", "max_wait_s", "max_queue")

    def __init__(self, name: str, workers: int) -> None:
        self.name, self.workers = name, workers
        self.items = self.failed = self.max_queue = 0
        self.run_s = self.wait_s = self.max_wait_s = 0.0


class Stats:
    """Filled in by run(stats=...). Aggregates only: memory does not grow with the number of items."""
    __slots__ = ("wall_s", "stages")

    def __init__(self) -> None:
        self.wall_s = 0.0
        self.stages: list[StageStats] = []

    def busy(self, stage: StageStats) -> float:
        """Fraction of the run this stage's workers spent running the stage function."""
        return stage.run_s / (stage.workers * self.wall_s) if self.wall_s > 0 else 0.0

    def bottleneck(self) -> StageStats | None:
        return max(self.stages, key=self.busy, default=None)

    def report(self) -> str:
        rows = [f"{'stage':<12}{'workers':>8}{'items':>7}{'failed':>7}{'busy':>6}{'avg run':>10}{'avg wait':>10}{'max queue':>10}"]
        for s in self.stages:
            n = s.items or 1
            rows.append(f"{s.name:<12}{s.workers:>8}{s.items:>7}{s.failed:>7}{self.busy(s):>6.0%}"
                        f"{s.run_s / n:>9.3f}s{s.wait_s / n:>9.3f}s{s.max_queue:>10}")
        top = self.bottleneck()
        if top is not None and self.busy(top) >= 0.7:
            rows.append(f'"{top.name}" is the bottleneck ({self.busy(top):.0%} busy): more workers there will '
                        f"speed the run up; more workers on the other stages will not.")
        elif top is not None:
            rows.append("No stage is saturated: the input, max_in_flight or an external limit is setting the pace.")
        rows.append(f"wall time {self.wall_s:.2f}s")
        return "\n".join(rows)


def run(items: Iterable[Any], stages: list[Stage], *, max_in_flight: int = 8,
        cancelled: Callable[[], bool] = _never,
        on_done: Callable[[int, Any], None] | None = None,
        keep_results: bool = True,
        on_error: Any = None,
        partial: bool = False,
        stats: Stats | None = None) -> list[Any] | None:
    for s in stages:
        if s.ordered and s.workers != 1:
            raise ValueError(f"stage {s.name!r}: ordered needs workers=1")
        if s.close is not None and s.init is None:
            raise ValueError(f"stage {s.name!r}: close needs init")
    if on_error is not None and on_error not in ("raise", "collect") and not callable(on_error):
        raise ValueError('on_error must be None, "raise", "collect" or a callable')
    if partial and not keep_results:
        raise ValueError("partial=True needs keep_results=True")
    policy = "raise" if on_error in (None, "raise") else ("collect" if on_error == "collect" else "call")
    import threading                                   # first use, not at `import stagepipe`
    from time import perf_counter
    try:
        from _queue import SimpleQueue                 # the C queue, without the queue module around it
    except ImportError:                                # other interpreters
        from queue import SimpleQueue
    clock = perf_counter if stats is not None else _zero
    qs = [SimpleQueue() for _ in stages]               # qs[k] feeds stages[k]
    results: dict[int, Any] | None = {} if keep_results else None
    slots = threading.BoundedSemaphore(max(1, max_in_flight))
    abort = threading.Event()
    finished = threading.Semaphore(0)
    errors: list[BaseException] = []
    close_errors: list[BaseException] = []
    accs: list[list[list[Any]]] = [[] for _ in stages]  # per stage: one [items, failed, run, wait, maxwait, maxq] per worker

    def fail(exc: BaseException) -> None:
        errors.append(exc)
        abort.set()
        finished.release()

    def advance(k: int, i: int, value: Any) -> None:
        if k + 1 < len(stages):
            qs[k + 1].put((i, value, clock()))
        else:
            if results is not None:
                results[i] = value
            slots.release()
            if on_done is not None:
                try:
                    on_done(i, value)
                except Exception as exc:          # noqa: BLE001 - a progress callback must not hang the run
                    return fail(exc)
            finished.release()

    def _call(stage: Stage, i: int, value: Any, t_enq: float, k: int, state: Any, loc: list[Any]) -> bool:
        if cancelled():
            fail(Cancelled())
            return False
        if isinstance(value, Failed):             # failed upstream: carried along, not processed
            advance(k, i, value)
            return True
        t0 = clock()
        try:
            out = stage.fn(value, i) if stage.init is None else stage.fn(value, i, state)
        except Exception as exc:
            if policy == "raise":
                fail(exc)
                return False
            if stats is not None:
                loc[1] += 1
            if policy == "collect":
                out = Failed(stage.name, i, exc)
            else:
                try:
                    out = on_error(stage.name, i, value, exc)
                except BaseException as exc2:     # noqa: BLE001 - the callback chose to stop the run
                    fail(exc2)
                    return False
        except BaseException as exc:              # noqa: BLE001 - re-raised from run()
            fail(exc)
            return False
        if stats is not None:
            t1 = clock()
            wait = t0 - t_enq
            loc[0] += 1
            loc[2] += t1 - t0
            loc[3] += wait
            if wait > loc[4]:
                loc[4] = wait
        advance(k, i, out)
        return True

    def work(k: int) -> None:
        stage, q = stages[k], qs[k]
        buffer: dict[int, Any] = {}
        nxt = 0
        loc: list[Any] = [0, 0, 0.0, 0.0, 0.0, 0]
        accs[k].append(loc)
        state = None
        ready = stage.init is None                # False only when init failed: then there is nothing to close
        if stage.init is not None:
            try:
                state = stage.init()              # once per worker thread
                ready = True
            except BaseException as exc:          # noqa: BLE001
                fail(exc)
        try:
            got = value = v = None                    # reset every turn: an idle worker must not keep
            while True:                               # the last item alive
                got = q.get()
                if got is _STOP:
                    return
                if abort.is_set():
                    got = None
                    continue                          # drain: nothing new starts after a failure
                i, value, t_enq = got
                got = None
                if stats is not None:
                    depth = q.qsize() + 1
                    if depth > loc[5]:
                        loc[5] = depth
                if stage.ordered:
                    buffer[i] = (value, t_enq)
                    value = None
                    while nxt in buffer and not abort.is_set():
                        j, (v, te) = nxt, buffer.pop(nxt)
                        nxt += 1
                        ok = _call(stage, j, v, te, k, state, loc)
                        v = None
                        if not ok:
                            break
                else:
                    _call(stage, i, value, t_enq, k, state, loc)
                    value = None
        finally:
            if ready and stage.close is not None:
                try:
                    stage.close(state)            # in this thread, after its last call has returned
                except BaseException as exc:      # noqa: BLE001 - other workers must still close
                    close_errors.append(exc)

    threads = [threading.Thread(target=work, args=(k,), daemon=True, name=f"pipe-{s.name}-{w}")
               for k, s in enumerate(stages) for w in range(s.workers)]
    for t in threads:
        t.start()
    t_start = perf_counter()
    fed = 0
    try:
        for value in items:                       # pulled lazily; blocks while max_in_flight are open
            while not slots.acquire(timeout=0.2):
                if abort.is_set():
                    break
            if abort.is_set():
                break
            qs[0].put((fed, value, clock()))
            fed += 1
            value = None
        for _ in range(fed):
            if abort.is_set():
                break
            while not finished.acquire(timeout=0.2):
                if abort.is_set():
                    break
    except BaseException:                         # Ctrl-C or any error in the caller: stop everything
        abort.set()
        raise
    finally:
        abort_was_set = abort.is_set()
        for k, s in enumerate(stages):
            for _ in range(s.workers):
                qs[k].put(_STOP)
        deadline = perf_counter() + 30
        for t in threads:                         # a call already running cannot be interrupted; wait a bit
            t.join(timeout=None if not abort_was_set else max(0.0, deadline - perf_counter()))
        if stats is not None:
            stats.wall_s = perf_counter() - t_start
            stats.stages = []
            for k, s in enumerate(stages):
                st = StageStats(s.name, s.workers)
                for loc in accs[k]:
                    st.items += loc[0]
                    st.failed += loc[1]
                    st.run_s += loc[2]
                    st.wait_s += loc[3]
                    st.max_wait_s = max(st.max_wait_s, loc[4])
                    st.max_queue = max(st.max_queue, loc[5])
                stats.stages.append(st)
    if errors:
        if partial and isinstance(errors[0], Cancelled) and results is not None:
            return [results.get(i, UNFINISHED) for i in range(fed)]
        raise errors[0]
    if close_errors:
        raise close_errors[0]
    if results is None:
        return None
    return [results.pop(i) for i in range(fed)]
