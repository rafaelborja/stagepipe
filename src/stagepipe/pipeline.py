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
* Declare what a stage does and the library knows what it may repeat. Transient("ocr", fn): leaves no
  trace, so it can be retried, redone and dropped at a stop. Permanent("upload", fn): creates a lasting
  change (writes to the destination, calls an API that modifies things, deletes, sends), so it is never
  repeated on its own: retries= needs idempotent=True (you promise a repeat is harmless) or recover=
  (your check, called before each retry: Skip(value), Rerun() or Fail(reason)). A plain Stage declares
  nothing and refuses retries=. Wrong declarations raise ValueError when the stage is created.
* retries=N, backoff=s, retry_on=(OSError,): a failing call is repeated up to N times, waiting
  backoff * 2**(n-1) seconds (cancel and Ctrl-C interrupt the wait). The error policy applies after the
  last attempt. A retry happens inside the call: the worker, the window slot and the ordered position stay.
* on_error on a Stage overrides the run's: "raise", "collect", "log" (collect and write one line to
  stderr) or a callable. limiter=Semaphore: acquired around each call, so one semaphore caps a
  resource across stages and across runs (a concurrency cap, not rate limiting).
* stop=Event, drain=None|True|False: a requested stop, not an error. The feeder stops pulling input;
  every call already running finishes; items that have not finished are marked UNFINISHED and run()
  returns normally with stats.stopped set. drain=False drops everything that has not started a call.
  drain=None (automatic) keeps an item going only if an effect already happened for it (it passed a
  Permanent stage) and only through Transient or plain stages, so no new effect starts. drain=True lets
  everything fed run to the end. Cancel and Ctrl-C still raise.
* abort_wait: after an abort (stage error, cancel, Ctrl-C) run() waits at most this many seconds
  (default 30.0; None waits as long as it takes) for stage calls that are already running, because
  a running call cannot be interrupted. Stats.stragglers tells how many workers were still running
  when run() returned; Stage(close=...) never runs while its worker is still inside a call.
* current(): from inside a stage function (or its init/close), stagepipe.current() returns an object
  with .stage (name), .index (the item's position) and .attempt (0 for the first call), or None
  outside a stage thread. It saves threading the item index through your own code.
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


class _Dropped:
    __slots__ = ()


_DROPPED = _Dropped()                 # an item dropped at a stop: carried to the end, never processed
_local = None                                  # threading.local, created by the first run()


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


class Current:
    """What current() returns: the stage name, the item's index and the attempt of the call in progress."""
    __slots__ = ("stage", "index", "attempt")

    def __init__(self, stage: str) -> None:
        self.stage, self.index, self.attempt = stage, None, 0

    def __repr__(self) -> str:
        return f"Current(stage={self.stage!r}, index={self.index}, attempt={self.attempt})"


def current() -> Current | None:
    """The stage call in progress in this thread, or None when called from outside a stage thread."""
    return getattr(_local, "cur", None) if _local is not None else None


class Skip:
    """recover= result: the work was already done; pass `value` on to the next stage."""
    __slots__ = ("value",)

    def __init__(self, value: Any = None) -> None:
        self.value = value


class Rerun:
    """recover= result: run the stage again."""
    __slots__ = ()


class Fail:
    """recover= result: do not decide automatically; the item fails with RecoveryError(reason)."""
    __slots__ = ("reason",)

    def __init__(self, reason: str = "") -> None:
        self.reason = reason


class RecoveryError(Exception):
    """The error an item fails with when recover= answered Fail(reason)."""


class Ctx:
    """What recover(ctx) is told. key is the item's index until checkpoint keys exist; began is true because an
    earlier attempt started; finished and saved describe the saved boundary and are False and None without a checkpoint."""
    __slots__ = ("key", "index", "value", "began", "finished", "saved", "attempt")

    def __init__(self, index: int, value: Any, attempt: int) -> None:
        self.key = self.index = index
        self.value, self.attempt = value, attempt
        self.began, self.finished, self.saved = True, False, None


def _log_error(stage: str, index: int, exc: BaseException) -> None:
    import sys                                         # already loaded in every process; costs nothing
    sys.stderr.write(f"stagepipe: stage {stage!r} item {index}: {type(exc).__name__}: {exc}\n")


def _policy(h: Any) -> str | None:
    if h is None:
        return None
    if isinstance(h, str):
        if h in ("raise", "collect", "log"):
            return h
        raise ValueError('on_error must be None, "raise", "collect", "log" or a callable')
    if callable(h):
        return "call"
    raise ValueError('on_error must be None, "raise", "collect", "log" or a callable')


class Stage:
    """A step of the pipeline. A plain Stage declares nothing about its effects, so it works exactly as before
    but refuses retries=, recover= and idempotent=. Use Transient or Permanent to declare what it does."""
    __slots__ = ("name", "fn", "workers", "ordered", "init", "close", "retries", "backoff", "retry_on",
                 "recover", "idempotent", "on_error", "limiter")
    kind = ""

    def __init__(self, name: str, fn: Callable[..., Any], workers: int = 1, ordered: bool = False,
                 init: Callable[[], Any] | None = None, close: Callable[[Any], Any] | None = None,
                 *, retries: int = 0, backoff: float = 0.5, retry_on: Any = Exception,
                 recover: Callable[[Ctx], Any] | None = None, idempotent: bool = False,
                 on_error: Any = None, limiter: Any = None) -> None:
        self.name, self.fn, self.workers, self.ordered, self.init, self.close = name, fn, workers, ordered, init, close
        self.retries, self.backoff, self.retry_on = retries, backoff, retry_on
        self.recover, self.idempotent, self.on_error, self.limiter = recover, idempotent, on_error, limiter
        self.check()

    def check(self) -> None:
        """Raise ValueError for an invalid declaration, when the stage is created. Kinds extend this."""
        n = self.name
        if not isinstance(self.retries, int) or self.retries < 0:
            raise ValueError(f"stage {n!r}: retries must be an integer >= 0")
        if self.backoff < 0:
            raise ValueError(f"stage {n!r}: backoff must be >= 0")
        _policy(self.on_error)
        if self.limiter is not None and not (hasattr(self.limiter, "acquire") and hasattr(self.limiter, "release")):
            raise ValueError(f"stage {n!r}: limiter needs acquire(timeout=) and release(), like threading.Semaphore")
        if self.recover is not None and not callable(self.recover):
            raise ValueError(f"stage {n!r}: recover must be callable")
        if self.kind == "" and (self.retries > 0 or self.recover is not None or self.idempotent):
            raise ValueError(f"stage {n!r}: retries=, recover= and idempotent= need a declared kind; "
                             "use Transient (no trace, safe to repeat) or Permanent (a lasting change)")

    def may_retry(self, attempt: int, exc: BaseException) -> bool:
        """May this failure be retried? attempt counts retries from 1. Override in a subclass to change the rule."""
        return not isinstance(exc, Cancelled) and isinstance(exc, self.retry_on)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r}, workers={self.workers}, ordered={self.ordered})"


class Transient(Stage):
    """Leaves no trace: it can run any number of times and affects nothing outside the pipeline (read, parse,
    OCR, ask a model). The library may retry it, redo it and drop it at a stop."""
    __slots__ = ()
    kind = "transient"

    def check(self) -> None:
        super().check()
        if self.recover is not None or self.idempotent:
            raise ValueError(f"stage {self.name!r}: recover= and idempotent= belong on a Permanent stage; "
                             "a Transient stage can always be repeated")


class Permanent(Stage):
    """Once started, creates a lasting change (write to the destination, call an API that modifies things,
    delete, send, bill). It is never repeated on its own: retries= needs idempotent=True (you promise a repeat
    is harmless) or recover= (your check, called before each retry, returning Skip, Rerun or Fail)."""
    __slots__ = ()
    kind = "permanent"

    def check(self) -> None:
        super().check()
        if self.retries > 0 and not self.idempotent and self.recover is None:
            raise ValueError(f"stage {self.name!r}: retries={self.retries} but the stage is Permanent; "
                             "pass idempotent=True if a repeat is harmless, or recover= to check first")


class StageStats:
    """Totals for one stage. wait_s is time items spent queued with every worker busy."""
    __slots__ = ("name", "workers", "items", "failed", "run_s", "wait_s", "max_wait_s", "max_queue")

    def __init__(self, name: str, workers: int) -> None:
        self.name, self.workers = name, workers
        self.items = self.failed = self.max_queue = 0
        self.run_s = self.wait_s = self.max_wait_s = 0.0


class Stats:
    """Filled in by run(stats=...). Aggregates only: memory does not grow with the number of items."""
    __slots__ = ("wall_s", "stages", "stragglers", "stopped", "dropped")

    def __init__(self) -> None:
        self.wall_s = 0.0
        self.stragglers = 0                 # workers still running when run() returned after an abort
        self.stopped = False                # run(stop=...) cut the run short
        self.dropped = 0                    # items marked UNFINISHED by a stop
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
        if self.stopped:
            rows.append(f"stopped on request: {self.dropped} item(s) not finished (UNFINISHED)")
        rows.append(f"wall time {self.wall_s:.2f}s")
        return "\n".join(rows)


def run(items: Iterable[Any], stages: list[Stage], *, max_in_flight: int = 8,
        cancelled: Callable[[], bool] = _never,
        on_done: Callable[[int, Any], None] | None = None,
        keep_results: bool = True,
        on_error: Any = None,
        partial: bool = False,
        stats: Stats | None = None,
        abort_wait: float | None = 30.0,
        stop: Any = None,
        drain: bool | None = None) -> list[Any] | None:
    for s in stages:
        if s.ordered and s.workers != 1:
            raise ValueError(f"stage {s.name!r}: ordered needs workers=1")
        if s.close is not None and s.init is None:
            raise ValueError(f"stage {s.name!r}: close needs init")
        s.check()                                      # stages built as plain objects are checked again here
    _policy(on_error)
    if partial and not keep_results:
        raise ValueError("partial=True needs keep_results=True")
    if stop is not None and not hasattr(stop, "is_set"):
        raise ValueError("stop must be a threading.Event (anything with is_set())")
    if drain not in (None, True, False):
        raise ValueError("drain must be None (automatic), True or False")
    pol: list[str] = []                                # per stage: what a failing call does
    hnd: list[Any] = []
    for s in stages:
        h = s.on_error if s.on_error is not None else on_error
        pol.append(_policy(h) or "raise")
        hnd.append(h)
    import threading                                   # first use, not at `import stagepipe`
    global _local
    if _local is None:
        _local = threading.local()
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
    dropped: list[int] = []                            # one entry per item a stop marked UNFINISHED (bounded by the window)
    accs: list[list[list[Any]]] = [[] for _ in stages]  # per stage: one [items, failed, run, wait, maxwait, maxq] per worker

    def fail(exc: BaseException) -> None:
        errors.append(exc)
        abort.set()
        finished.release()

    def advance(k: int, i: int, value: Any, eff: bool) -> None:
        if k + 1 < len(stages):
            qs[k + 1].put((i, value, clock(), eff))
        else:
            if value is _DROPPED:
                dropped.append(i)
                if results is not None:
                    results[i] = UNFINISHED
                slots.release()
                finished.release()
                return
            if results is not None:
                results[i] = value
            slots.release()
            if on_done is not None:
                try:
                    on_done(i, value)
                except Exception as exc:          # noqa: BLE001 - a progress callback must not hang the run
                    return fail(exc)
            finished.release()

    def keeps(stage: Stage, eff: bool) -> bool:
        """At a stop, may this item start a call on `stage`? True: drain everything. None (automatic): only an item
        whose effect already happened and is not saved yet, and never into a Permanent stage (no new effect)."""
        return drain is True or (drain is None and eff and stage.kind != "permanent")

    def take(limiter: Any) -> bool:
        while not limiter.acquire(timeout=0.2):
            if abort.is_set():
                return False
        return True

    def pause(delay: float) -> bool:
        """Wait out a retry backoff; False when the run was aborted meanwhile."""
        end = perf_counter() + delay
        while True:
            rem = end - perf_counter()
            if rem <= 0:
                return not abort.is_set()
            if abort.wait(min(rem, 0.2)):
                return False
            if cancelled():
                return True                       # the caller raises Cancelled
            if stop is not None and stop.is_set() and drain is not True:
                return True                       # a stop ends the wait; the caller applies the stop rule

    def _call(stage: Stage, i: int, value: Any, t_enq: float, k: int, state: Any, loc: list[Any],
              cur: Current, eff: bool) -> bool:
        if cancelled():
            fail(Cancelled())
            return False
        if value is _DROPPED or isinstance(value, Failed):  # dropped or failed upstream: carried along, not processed
            advance(k, i, value, eff)
            return True
        if stop is not None and stop.is_set() and not keeps(stage, eff):
            advance(k, i, _DROPPED, eff)
            return True
        cur.index, cur.attempt = i, 0
        lim = stage.limiter
        attempt, ran, t0, out = 0, 0.0, 0.0, None
        pl, handler = pol[k], hnd[k]
        while True:
            if lim is not None and not take(lim):
                return False                      # aborted while waiting for the shared limit
            ta = clock()
            if attempt == 0:
                t0 = ta
            err: BaseException | None = None
            try:
                try:
                    out = stage.fn(value, i) if stage.init is None else stage.fn(value, i, state)
                finally:
                    if lim is not None:
                        lim.release()
            except Exception as exc:
                err = exc
            except BaseException as exc:          # noqa: BLE001 - re-raised from run()
                fail(exc)
                return False
            ran += clock() - ta
            if err is None:
                break
            if attempt < stage.retries and stage.may_retry(attempt + 1, err):
                attempt += 1
                cur.attempt = attempt
                if not pause(stage.backoff * (2 ** (attempt - 1))):
                    return False
                if cancelled():
                    fail(Cancelled())
                    return False
                if stop is not None and stop.is_set() and not keeps(stage, eff):
                    advance(k, i, _DROPPED, eff)
                    return True
                if stage.recover is not None:
                    try:
                        d = stage.recover(Ctx(i, value, attempt))
                        if d is None or isinstance(d, type):
                            d = d() if d is not None else Rerun()
                    except BaseException as exc2:  # noqa: BLE001 - the user's check failed: stop the run
                        fail(exc2)
                        return False
                    if isinstance(d, Skip):
                        err, out = None, d.value
                        break
                    if isinstance(d, Fail):
                        err = RecoveryError(d.reason)
                    elif not isinstance(d, Rerun):
                        fail(TypeError(f"recover= of stage {stage.name!r} returned {d!r}; expected Skip, Rerun or Fail"))
                        return False
                    else:
                        continue
                else:
                    continue
            # out of retries (or not retryable): the error policy decides
            if pl == "raise":
                fail(err)
                return False
            if stats is not None:
                loc[1] += 1
            if pl == "call":
                try:
                    out = handler(stage.name, i, value, err)
                except BaseException as exc2:     # noqa: BLE001 - the callback chose to stop the run
                    fail(exc2)
                    return False
            else:
                if pl == "log":
                    _log_error(stage.name, i, err)
                out = Failed(stage.name, i, err)
            break
        if stats is not None:
            wait = t0 - t_enq
            loc[0] += 1
            loc[2] += ran
            loc[3] += wait
            if wait > loc[4]:
                loc[4] = wait
        advance(k, i, out, eff or stage.kind == "permanent")
        return True

    def work(k: int) -> None:
        stage, q = stages[k], qs[k]
        buffer: dict[int, Any] = {}
        nxt = 0
        loc: list[Any] = [0, 0, 0.0, 0.0, 0.0, 0]
        accs[k].append(loc)
        state = None
        cur = _local.cur = Current(stage.name)
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
                i, value, t_enq, eff = got
                got = None
                if stats is not None:
                    depth = q.qsize() + 1
                    if depth > loc[5]:
                        loc[5] = depth
                if stage.ordered:
                    buffer[i] = (value, t_enq, eff)
                    value = None
                    while nxt in buffer and not abort.is_set():
                        j, (v, te, ef) = nxt, buffer.pop(nxt)
                        nxt += 1
                        ok = _call(stage, j, v, te, k, state, loc, cur, ef)
                        v = None
                        if not ok:
                            break
                else:
                    _call(stage, i, value, t_enq, k, state, loc, cur, eff)
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
    fed = queued = 0
    stopped_early = False
    try:
        it = iter(items)
        while True:
            if stop is not None and stop.is_set():
                stopped_early = True              # checked before pulling: the caller's iterator keeps the rest
                break
            try:
                value = next(it)                  # pulled lazily; blocks while max_in_flight are open
            except StopIteration:
                break
            got_slot = False
            while not got_slot:
                got_slot = slots.acquire(timeout=0.2)
                if not got_slot and (abort.is_set() or (stop is not None and stop.is_set())):
                    break
            if abort.is_set():
                break
            if not got_slot:                      # a stop arrived while waiting: this item was pulled but never started
                stopped_early = True
                dropped.append(fed)
                if results is not None:
                    results[fed] = UNFINISHED
                fed += 1
                break
            qs[0].put((fed, value, clock(), False))
            fed += 1
            queued += 1
            value = None
        for _ in range(queued):
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
        deadline = None if abort_wait is None else perf_counter() + abort_wait
        for t in threads:                         # a call already running cannot be interrupted; wait a bit
            t.join(timeout=None if not abort_was_set or deadline is None else max(0.0, deadline - perf_counter()))
        stragglers = sum(1 for t in threads if t.is_alive())
        if stats is not None:
            stats.wall_s = perf_counter() - t_start
            stats.stragglers = stragglers
            stats.stopped = stopped_early or bool(dropped)
            stats.dropped = len(dropped)
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
