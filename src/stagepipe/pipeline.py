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
* checkpoint="dir", key=fn(item): a killed run can resume. Every stage must be declared (Transient or
  Permanent). The last stage's output is saved as dir/<stage>/<key> (a temporary file, then an atomic
  rename); Stage(save=True) saves an earlier stage too, and a rerun with the same directory restores each
  item from its latest saved output and runs only what is missing. Restored items appear in the results
  and in on_done in index order, counted in stats.restored (on_restored= is also called). A Permanent stage
  that is not saved writes .start and .done markers; if a rerun finds them it asks recover=, repeats it
  when idempotent=True, or fails the item with RecoveryError. Corrupt files are redone, Failed items and
  on_error substitutes are never saved, serializer= (default pickle) and fingerprint=fn(item) (a changed
  input is redone) are optional. Transient(scratch=True) gives current().scratch, a private path that is
  removed after each call. Restored items skip ordered stages; cross-item state is not rebuilt.
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


class _Skip:
    __slots__ = ()


_SKIP = _Skip()                       # a gap token: this index never visits an ordered stage (restored items)
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
    """What current() returns: the stage name, the item's index and key, the attempt of the call in progress and,
    for a Transient(scratch=True) stage, the path of the item's private scratch file or directory."""
    __slots__ = ("stage", "index", "attempt", "key", "scratch")

    def __init__(self, stage: str) -> None:
        self.stage, self.index, self.attempt, self.key, self.scratch = stage, None, 0, None, None

    def __repr__(self) -> str:
        return f"Current(stage={self.stage!r}, index={self.index}, key={self.key!r}, attempt={self.attempt})"


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
    """What recover(ctx) is told: the item's key (its index when there is no key=), index, the stage's input value,
    whether an earlier attempt began and finished (from the .start and .done markers on a resumed run), the
    attempt number, and the saved output (None: a saved output is restored before the stage is reached)."""
    __slots__ = ("key", "index", "value", "began", "finished", "saved", "attempt")

    def __init__(self, key: Any, index: int, value: Any, attempt: int, began: bool = True,
                 finished: bool = False, saved: Any = None) -> None:
        self.key, self.index, self.value, self.attempt = key, index, value, attempt
        self.began, self.finished, self.saved = began, finished, saved


def _decision(d: Any, stage: Any) -> Any:
    """Normalise what recover() returned: None or the class Rerun mean Rerun()."""
    if d is None:
        return Rerun()
    if isinstance(d, type):
        d = d()
    if not isinstance(d, (Skip, Rerun, Fail)):
        raise TypeError(f"recover= of stage {stage.name!r} returned {d!r}; expected Skip, Rerun or Fail")
    return d


_KEY_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


def _safe_key(k: Any) -> str:
    """A checkpoint key becomes a file name, so it must be short and made of letters, digits, '.', '_' and '-'."""
    if isinstance(k, int) and not isinstance(k, bool):
        k = str(k)
    if not isinstance(k, str) or not k or len(k) > 150 or k in (".", "..") or not _KEY_CHARS.issuperset(k):
        raise ValueError(f"checkpoint key {k!r} is not usable as a file name: use 1 to 150 letters, digits, '.', '_' or '-'")
    return k


def _write(path: str, data: bytes) -> None:
    """Write through a temporary file in the same directory, then rename: a reader never sees a partial file."""
    import os
    from _thread import get_ident
    tmp = f"{path}.tmp{os.getpid()}-{get_ident()}"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _unlink(path: str) -> None:
    import os
    try:
        os.remove(path)
    except OSError:
        pass


def _rm_scratch(path: str) -> None:
    import os
    if os.path.isdir(path):
        import shutil                                  # only when a stage made a directory
        shutil.rmtree(path, ignore_errors=True)
    else:
        _unlink(path)


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
                 "recover", "idempotent", "on_error", "limiter", "save", "scratch")
    kind = ""

    def __init__(self, name: str, fn: Callable[..., Any], workers: int = 1, ordered: bool = False,
                 init: Callable[[], Any] | None = None, close: Callable[[Any], Any] | None = None,
                 *, retries: int = 0, backoff: float = 0.5, retry_on: Any = Exception,
                 recover: Callable[[Ctx], Any] | None = None, idempotent: bool = False,
                 on_error: Any = None, limiter: Any = None, save: bool | None = None, scratch: bool = False) -> None:
        self.name, self.fn, self.workers, self.ordered, self.init, self.close = name, fn, workers, ordered, init, close
        self.retries, self.backoff, self.retry_on = retries, backoff, retry_on
        self.recover, self.idempotent, self.on_error, self.limiter = recover, idempotent, on_error, limiter
        self.save, self.scratch = save, scratch
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
        if self.kind == "" and (self.retries > 0 or self.recover is not None or self.idempotent or self.save
                                or self.scratch):
            raise ValueError(f"stage {n!r}: retries=, recover=, idempotent=, save= and scratch= need a declared kind; "
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
        if self.scratch:
            raise ValueError(f"stage {self.name!r}: scratch=True belongs on a Transient stage")
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
    __slots__ = ("wall_s", "stages", "stragglers", "stopped", "dropped", "restored", "saved")

    def __init__(self) -> None:
        self.wall_s = 0.0
        self.stragglers = 0                 # workers still running when run() returned after an abort
        self.stopped = False                # run(stop=...) cut the run short
        self.dropped = 0                    # items marked UNFINISHED by a stop
        self.restored = 0                   # items whose final result came from the checkpoint
        self.saved = 0                      # stage outputs written to the checkpoint
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
        if self.restored or self.saved:
            rows.append(f"checkpoint: {self.restored} item(s) restored, {self.saved} stage output(s) saved")
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
        drain: bool | None = None,
        checkpoint: str | None = None,
        key: Callable[[Any], Any] | None = None,
        serializer: Any = None,
        fingerprint: Callable[[Any], Any] | None = None,
        on_restored: Callable[[int, Any], None] | None = None,
        scratch_dir: str | None = None) -> list[Any] | None:
    import os
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
    cp = checkpoint is not None
    if cp:
        if not callable(key):
            raise ValueError("checkpoint= needs key=fn(item) returning a stable, file-name-safe key for each item")
        names: set[str] = set()
        for s in stages:
            if s.kind == "":
                raise ValueError(f"stage {s.name!r}: checkpoint= needs every stage declared; use Transient or Permanent")
            if not s.name or s.name[0] in "._-" or s.name == "VERSION" or not _KEY_CHARS.issuperset(s.name) or s.name in names:
                raise ValueError(f"stage name {s.name!r}: with checkpoint= stage names become folder names, so they must "
                                 "be unique and use letters, digits, '.', '_' or '-', starting with a letter or digit")
            names.add(s.name)
    elif serializer is not None or fingerprint is not None or on_restored is not None:
        raise ValueError("serializer=, fingerprint= and on_restored= need checkpoint=")
    last = len(stages) - 1
    saves = [bool(s.save) if s.save is not None else (cp and k == last) for k, s in enumerate(stages)]
    if not cp and any(saves):
        raise ValueError("save=True needs checkpoint=")
    if cp and stages and not saves[last]:
        raise ValueError("with checkpoint= the last stage must be saved (leave save= unset or True on it)")
    sroot = scratch_dir if scratch_dir is not None else (os.path.join(checkpoint, "_scratch") if cp else None)
    if sroot is None and any(s.scratch for s in stages):
        raise ValueError("scratch=True needs checkpoint= or scratch_dir=")
    pol: list[str] = []                                # per stage: what a failing call does
    hnd: list[Any] = []
    for s in stages:
        h = s.on_error if s.on_error is not None else on_error
        pol.append(_policy(h) or "raise")
        hnd.append(h)
    ser = serializer
    if cp:
        if ser is None:
            import pickle                              # only for a checkpoint run
            ser = pickle
        os.makedirs(checkpoint, exist_ok=True)
        vpath = os.path.join(checkpoint, "VERSION")
        want = "stagepipe-checkpoint 1\n"
        have = _read(vpath)
        if have is None:
            _write(vpath, want.encode())
        elif have.decode("utf-8", "replace") != want:
            raise ValueError(f"{checkpoint!r} was written by a different checkpoint format ({have[:40]!r})")
    sdir = [os.path.join(checkpoint, s.name) if cp else "" for s in stages]
    perm = [s.kind == "permanent" for s in stages]
    if cp:
        for k, s in enumerate(stages):
            if saves[k] or perm[k]:
                os.makedirs(sdir[k], exist_ok=True)
    if sroot is not None:
        for s in stages:
            if s.scratch:
                os.makedirs(os.path.join(sroot, s.name), exist_ok=True)
    # what to delete for a key once stage k's output is safely saved: earlier outputs and markers, and k's own markers
    junk: list[list[tuple[int, str]]] = []
    for k in range(len(stages)):
        j_: list[tuple[int, str]] = []
        for j in range(k + 1):
            if saves[j] and j < k:
                j_.append((j, ""))
            if perm[j]:
                j_ += [(j, ".start"), (j, ".done")]
        junk.append(j_)
    ordered_ix = [k for k, s in enumerate(stages) if s.ordered]
    perm_ix = [k for k in range(len(stages)) if perm[k]]
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
    inflight: set[str] = set()                         # keys between "fed" and "done": bounded by the window
    accs: list[list[list[Any]]] = [[] for _ in stages]  # per stage: one [items, failed, run, wait, maxwait, maxq, saved] per worker

    def fail(exc: BaseException) -> None:
        errors.append(exc)
        abort.set()
        finished.release()

    def advance(k: int, i: int, value: Any, eff: bool, kf: Any, res: bool) -> None:
        if k + 1 < len(stages):
            qs[k + 1].put((i, value, clock(), eff, kf, res))
        else:
            if kf is not None:
                inflight.discard(kf[0])
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

    def save_output(k: int, kf: Any, out: Any) -> bool:
        """Write stage k's output, then remove what it makes redundant. False when the run was failed."""
        stage = stages[k]
        try:
            data = ser.dumps((kf[0], kf[1], out))
        except Exception as exc:                  # noqa: BLE001
            fail(ValueError(f"checkpoint: output of stage {stage.name!r} for key {kf[0]!r} ({type(out).__name__}) cannot be "
                            f"serialized by {type(ser).__name__ if not hasattr(ser, '__name__') else ser.__name__}: {exc}. "
                            "Return plain data from this stage or pass serializer=."))
            return False
        if isinstance(data, str):
            data = data.encode("utf-8")               # json.dumps and friends return text
        try:
            _write(os.path.join(sdir[k], kf[0]), data)
            for j, suffix in junk[k]:
                _unlink(os.path.join(sdir[j], kf[0] + suffix))
        except Exception as exc:                  # noqa: BLE001 - OSError, or a serializer that returned the wrong type
            fail(exc)
            return False
        return True

    def _call(stage: Stage, i: int, value: Any, t_enq: float, k: int, state: Any, loc: list[Any],
              cur: Current, eff: bool, kf: Any, res: bool) -> bool:
        if cancelled():
            fail(Cancelled())
            return False
        if value is _DROPPED or isinstance(value, Failed):  # dropped or failed upstream: carried along, not processed
            advance(k, i, value, eff, kf, res)
            return True
        if stop is not None and stop.is_set() and not keeps(stage, eff):
            advance(k, i, _DROPPED, eff, kf, res)
            return True
        kstr = kf[0] if kf is not None else None
        cur.index, cur.attempt, cur.key = i, 0, kstr
        lim = stage.limiter
        attempt, ran, t0, out = 0, 0.0, 0.0, None
        pl, handler = pol[k], hnd[k]
        spath = None
        if stage.scratch:
            spath = cur.scratch = os.path.join(sroot, stage.name, kstr if kstr is not None else str(i))
        skipped, pending, noretry = False, None, False
        if cp and perm[k]:
            sp, dp = os.path.join(sdir[k], kstr + ".start"), os.path.join(sdir[k], kstr + ".done")
            if res and (os.path.exists(sp) or os.path.exists(dp)):
                # an earlier run began (and maybe finished) this permanent stage and its output was not saved
                fin = os.path.exists(dp)
                try:
                    if stage.recover is not None:
                        d = _decision(stage.recover(Ctx(kstr, i, value, 0, True, fin, None)), stage)
                    elif stage.idempotent:
                        d = Rerun()
                    else:
                        d = Fail(f"stage {stage.name!r} {'finished' if fin else 'started'} for key {kstr!r} in an earlier run "
                                 "but its output was not saved, and a repeat could do it twice: save=True on the stage, "
                                 "idempotent=True or recover= makes it resumable")
                except BaseException as exc2:     # noqa: BLE001 - the user's check failed: stop the run
                    fail(exc2)
                    return False
                if isinstance(d, Skip):
                    out, skipped = d.value, True
                elif isinstance(d, Fail):
                    pending, noretry = RecoveryError(d.reason), True
            if not skipped and pending is None:
                try:
                    _write(sp, b"")
                except OSError as exc:
                    fail(exc)
                    return False
        while True:
            if skipped:
                err = None
                break
            err: BaseException | None = None
            if pending is not None:
                err, pending = pending, None
            else:
                if lim is not None and not take(lim):
                    return False                  # aborted while waiting for the shared limit
                ta = clock()
                if attempt == 0:
                    t0 = ta
                try:
                    try:
                        out = stage.fn(value, i) if stage.init is None else stage.fn(value, i, state)
                    finally:
                        if lim is not None:
                            lim.release()
                        if spath is not None:
                            _rm_scratch(spath)
                except Exception as exc:
                    err = exc
                except BaseException as exc:      # noqa: BLE001 - re-raised from run()
                    fail(exc)
                    return False
                ran += clock() - ta
            if err is None:
                break
            if not noretry and attempt < stage.retries and stage.may_retry(attempt + 1, err):
                attempt += 1
                cur.attempt = attempt
                if not pause(stage.backoff * (2 ** (attempt - 1))):
                    return False
                if cancelled():
                    fail(Cancelled())
                    return False
                if stop is not None and stop.is_set() and not keeps(stage, eff):
                    advance(k, i, _DROPPED, eff, kf, res)
                    return True
                if stage.recover is not None:
                    try:
                        d = _decision(stage.recover(Ctx(kstr if kstr is not None else i, i, value, attempt)), stage)
                    except BaseException as exc2:  # noqa: BLE001 - the user's check failed: stop the run
                        fail(exc2)
                        return False
                    if isinstance(d, Skip):
                        err, out = None, d.value
                        break
                    if isinstance(d, Fail):
                        err, noretry = RecoveryError(d.reason), True
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
        eff2 = eff or perm[k]
        if err is None and cp:                    # a real result (not a Failed or an on_error substitute)
            if saves[k]:
                if not save_output(k, kf, out):
                    return False
                loc[6] += 1
                eff2 = False                      # durable now: a stop need not hold the item
            elif perm[k] and not skipped:
                try:
                    _write(os.path.join(sdir[k], kstr + ".done"), b"")
                except OSError as exc:
                    fail(exc)
                    return False
        advance(k, i, out, eff2, kf, res)
        return True

    def work(k: int) -> None:
        stage, q = stages[k], qs[k]
        buffer: dict[int, Any] = {}
        nxt = 0
        loc: list[Any] = [0, 0, 0.0, 0.0, 0.0, 0, 0]
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
                i, value, t_enq, eff, kf, res = got
                got = None
                if stats is not None:
                    depth = q.qsize() + 1
                    if depth > loc[5]:
                        loc[5] = depth
                if stage.ordered:
                    buffer[i] = (value, t_enq, eff, kf, res)
                    value = None
                    while nxt in buffer and not abort.is_set():
                        j, (v, te, ef, kj, rj) = nxt, buffer.pop(nxt)
                        nxt += 1
                        if v is _SKIP:                # a restored item that never visits this stage: keep the count
                            v = None
                            continue
                        try:
                            ok = _call(stage, j, v, te, k, state, loc, cur, ef, kj, rj)
                        except BaseException as exc:  # noqa: BLE001 - a bug here must not hang the run
                            fail(exc)
                            ok = False
                        v = None
                        if not ok:
                            break
                else:
                    try:
                        _call(stage, i, value, t_enq, k, state, loc, cur, eff, kf, res)
                    except BaseException as exc:  # noqa: BLE001 - a bug here must not hang the run
                        fail(exc)
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
    fed = queued = restored = 0
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
            kf, start, res = None, 0, False
            if cp:
                kstr = _safe_key(key(value))
                if kstr in inflight:
                    raise ValueError(f"checkpoint key {kstr!r} is already in flight: keys must be unique per item")
                kf = (kstr, fingerprint(value) if fingerprint is not None else None)
                hit_k = -1                        # the last saved stage whose output is on disk and valid
                for kk in range(last, -1, -1):
                    if not saves[kk]:
                        continue
                    data = _read(os.path.join(sdir[kk], kstr))
                    if data is None:
                        continue
                    try:
                        kg, fg, val = ser.loads(data)
                    except Exception:             # noqa: BLE001 - a corrupt file is a miss: redo
                        continue
                    if kg == kstr and fg == kf[1]:
                        hit_k, value = kk, val
                        break
                if hit_k == last:                 # finished in an earlier run
                    i = fed
                    fed += 1
                    restored += 1
                    for ko in ordered_ix:
                        qs[ko].put((i, _SKIP, 0.0, False, None, False))
                    if results is not None:
                        results[i] = value
                    try:
                        if on_restored is not None:
                            on_restored(i, value)
                        if on_done is not None:
                            on_done(i, value)
                    except Exception as exc:      # noqa: BLE001
                        fail(exc)
                        break
                    value = None
                    continue
                start = hit_k + 1
                for kp in perm_ix:                # a marker from an earlier run, at or after the entry stage
                    if kp >= start and (os.path.exists(os.path.join(sdir[kp], kstr + ".start"))
                                        or os.path.exists(os.path.join(sdir[kp], kstr + ".done"))):
                        res = True
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
            if kf is not None:
                inflight.add(kf[0])
            for ko in ordered_ix:
                if ko < start:                    # restored past this ordered stage: tell it to move on
                    qs[ko].put((fed, _SKIP, 0.0, False, None, False))
            qs[start].put((fed, value, clock(), False, kf, res))
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
            stats.restored = restored
            stats.saved = 0
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
                    stats.saved += loc[6]
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
