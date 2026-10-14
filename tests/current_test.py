"""python tests/current_test.py - stagepipe.current() and run(abort_wait=...)."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import stagepipe
from stagepipe import Stage, Stats, current, run

# 1. outside a stage thread there is nothing to report (also before the first run())
assert current() is None

# 2. inside a stage function: stage name and index match the arguments, in every stage and worker
bad = []


def make(name):
    def fn(v, i):
        c = current()
        if c is None or c.stage != name or c.index != i or c.attempt != 0:
            bad.append((name, i, c))
        return v
    return fn


run(range(60), [Stage("a", make("a"), workers=3), Stage("b", make("b"), workers=2),
                Stage("c", make("c"), workers=1, ordered=True)], max_in_flight=8)
assert not bad, bad[:3]
assert current() is None                     # the caller's thread is untouched
print("current(): stage and index correct inside every stage call, None in the caller's thread")

# 3. init and close run in the worker thread: current() names the stage there too
seen = []
run(range(4), [Stage("m", lambda v, i, s: v, workers=2, init=lambda: seen.append(current().stage) or 1,
                     close=lambda s: seen.append(current().stage))])
assert seen.count("m") == 4, seen            # 2 workers x (init + close)
print("current(): available in init and close")

# 4. abort_wait: a call that outlasts the wait is reported as a straggler, and run() returns on time
release = threading.Event()
stats = Stats()


def stuck(v, i):
    if i == 0:
        release.wait(10)
    return v


t0 = time.perf_counter()
try:
    run(range(5), [Stage("s", stuck, workers=2, ordered=False), Stage("t", lambda v, i: 1 / 0, workers=1)],
        max_in_flight=5, stats=stats, abort_wait=0.3)
    raise SystemExit("error lost")
except ZeroDivisionError:
    pass
took = time.perf_counter() - t0
release.set()
assert took < 2.0, took
assert stats.stragglers == 1, stats.stragglers
print(f"abort_wait=0.3: run() returned after {took:.2f}s with stragglers={stats.stragglers}")

# 5. abort_wait=None waits for the running call, so there are no stragglers
release.clear()
stats = Stats()
threading.Timer(0.5, release.set).start()
t0 = time.perf_counter()
try:
    run(range(5), [Stage("s", stuck, workers=2), Stage("t", lambda v, i: 1 / 0, workers=1)],
        max_in_flight=5, stats=stats, abort_wait=None)
    raise SystemExit("error lost")
except ZeroDivisionError:
    pass
assert stats.stragglers == 0 and time.perf_counter() - t0 >= 0.45
print("abort_wait=None: waited for the running call, stragglers=0")

# 6. a normal finish never has stragglers
stats = Stats()
run(range(20), [Stage("a", lambda v, i: v, workers=3)], stats=stats)
assert stats.stragglers == 0
assert stagepipe.current is current
print("normal finish: stragglers=0")
print("OK")
