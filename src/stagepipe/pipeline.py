"""Tiny in-process pipeline: every item moves to the next stage the moment that stage has a free
worker, with no barrier between stages. Stdlib only (threads + queues), no server, nothing persisted.

    results = run(pages, [Stage("prep", prep, workers=4),
                          Stage("gemini", ask, workers=3),
                          Stage("finish", finish, workers=1, ordered=True)],
                  max_in_flight=8)

Each stage function is fn(value, index) -> value; the value it returns goes to the next stage.
Slow stages get more workers, fast ones fewer; a page waits only when the next stage is full.

* ordered=True: the stage sees items in index order (use it for stages with cross-page state,
  e.g. doc_pitch); it must have workers=1. Items finishing earlier wait in a small buffer.
* max_in_flight: at most this many items are between "fed" and "done" at once. It bounds memory
  (page images) and keeps a fast first stage from racing ahead of a slow one.
* the first exception (or cancelled() turning true) stops feeding, drains the workers and is
  raised from run(); results come back in input order.
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable

_STOP = object()


class Cancelled(Exception):
    pass


@dataclass
class Stage:
    name: str
    fn: Callable[[Any, int], Any]
    workers: int = 1
    ordered: bool = False


def run(items: Iterable[Any], stages: list[Stage], *, max_in_flight: int = 8,
        cancelled: Callable[[], bool] = lambda: False,
        on_done: Callable[[int, Any], None] | None = None) -> list[Any]:
    items = list(items)
    n = len(items)
    for s in stages:
        if s.ordered and s.workers != 1:
            raise ValueError(f"stage {s.name!r}: ordered needs workers=1")
    if n == 0:
        return []
    qs = [queue.Queue() for _ in stages]          # qs[k] feeds stages[k]
    results: list[Any] = [None] * n
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
        while True:
            got = q.get()
            if got is _STOP:
                return
            if abort.is_set():
                continue                          # drain: nothing new starts after a failure
            i, value = got
            if stage.ordered:
                buffer[i] = value
                while nxt in buffer and not abort.is_set():
                    j, v = nxt, buffer.pop(nxt)
                    nxt += 1
                    if not _call(stage, j, v, k):
                        break
            else:
                _call(stage, i, value, k)

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
    try:
        for i, value in enumerate(items):         # feeder: blocks while max_in_flight are open
            while not slots.acquire(timeout=0.2):
                if abort.is_set():
                    break
            if abort.is_set():
                break
            qs[0].put((i, value))
        for _ in range(n):
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
    return results
