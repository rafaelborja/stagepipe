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
* max_in_flight: at most this many items are between "fed" and "done" at once. It bounds memory
  (page images) and keeps a fast first stage from racing ahead of a slow one. Items are pulled
  from `items` lazily, so a generator is never materialised.
* keep_results=False: run() keeps nothing and returns None; use on_done to consume each result.
  Memory then stays flat however many items go through.
* the first exception (or cancelled() turning true) stops feeding, drains the workers and is
  raised from run(); results come back in input order.
* nothing heavy is imported: no typing, dataclasses, inspect, re or asyncio. threading and the C
  queue load on the first run() call, not at `import stagepipe`.
"""
from __future__ import annotations

# Only what is used at call time is imported, and only when needed. `typing` (+0.7 MB resident) and
# `dataclasses` (+1.7 MB, it drags in inspect, re, ...) are deliberately not used: annotations are
# lazy (from __future__), and Stage is a few lines of __slots__ instead of a dataclass.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Any, Callable, Iterable

_STOP = object()


def _never() -> bool:
    return False


class Cancelled(Exception):
    pass


class Stage:
    __slots__ = ("name", "fn", "workers", "ordered")

    def __init__(self, name: str, fn: Callable[[Any, int], Any], workers: int = 1, ordered: bool = False) -> None:
        self.name, self.fn, self.workers, self.ordered = name, fn, workers, ordered

    def __repr__(self) -> str:
        return f"Stage({self.name!r}, workers={self.workers}, ordered={self.ordered})"


def run(items: Iterable[Any], stages: list[Stage], *, max_in_flight: int = 8,
        cancelled: Callable[[], bool] = _never,
        on_done: Callable[[int, Any], None] | None = None,
        keep_results: bool = True) -> list[Any] | None:
    for s in stages:
        if s.ordered and s.workers != 1:
            raise ValueError(f"stage {s.name!r}: ordered needs workers=1")
    import threading                                   # first use, not at `import stagepipe`
    try:
        from _queue import SimpleQueue                 # the C queue, without the queue module around it
    except ImportError:                                # other interpreters
        from queue import SimpleQueue
    qs = [SimpleQueue() for _ in stages]               # qs[k] feeds stages[k]
    results: dict[int, Any] | None = {} if keep_results else None
    slots = threading.BoundedSemaphore(max(1, max_in_flight))
    abort = threading.Event()
    finished = threading.Semaphore(0)
    errors: list[BaseException] = []

    def fail(exc: BaseException) -> None:
        errors.append(exc)
        abort.set()
        finished.release()

    def advance(k: int, i: int, value: Any) -> None:
        if k + 1 < len(stages):
            qs[k + 1].put((i, value))
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

    def work(k: int) -> None:
        stage, q = stages[k], qs[k]
        buffer: dict[int, Any] = {}
        nxt = 0
        got = value = v = None                    # reset every turn: an idle worker must not keep
        while True:                               # the last item alive
            got = q.get()
            if got is _STOP:
                return
            if abort.is_set():
                got = None
                continue                          # drain: nothing new starts after a failure
            i, value = got
            got = None
            if stage.ordered:
                buffer[i] = value
                value = None
                while nxt in buffer and not abort.is_set():
                    j, v = nxt, buffer.pop(nxt)
                    nxt += 1
                    ok = _call(stage, j, v, k)
                    v = None
                    if not ok:
                        break
            else:
                _call(stage, i, value, k)
                value = None

    def _call(stage: Stage, i: int, value: Any, k: int) -> bool:
        if cancelled():
            fail(Cancelled())
            return False
        try:
            out = stage.fn(value, i)
        except BaseException as exc:              # noqa: BLE001 - re-raised from run()
            fail(exc)
            return False
        advance(k, i, out)
        return True

    threads = [threading.Thread(target=work, args=(k,), daemon=True, name=f"pipe-{s.name}-{w}")
               for k, s in enumerate(stages) for w in range(s.workers)]
    for t in threads:
        t.start()
    fed = 0
    try:
        for value in items:                       # pulled lazily; blocks while max_in_flight are open
            while not slots.acquire(timeout=0.2):
                if abort.is_set():
                    break
            if abort.is_set():
                break
            qs[0].put((fed, value))
            fed += 1
            value = None
        for _ in range(fed):
            if abort.is_set():
                break
            while not finished.acquire(timeout=0.2):
                if abort.is_set():
                    break
    finally:
        abort_was_set = abort.is_set()
        for k, s in enumerate(stages):
            for _ in range(s.workers):
                qs[k].put(_STOP)
        for t in threads:
            t.join(timeout=None if not abort_was_set else 30)
    if errors:
        raise errors[0]
    if results is None:
        return None
    return [results.pop(i) for i in range(fed)]
