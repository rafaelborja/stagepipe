"""python tests/memory_test.py - memory behaviour: lazy input, nothing retained, light import."""
import gc
import os
import subprocess
import sys
import time
import weakref
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
sys.path.insert(0, SRC)
from stagepipe import Stage, run


class Big:
    def __init__(self):
        self.d = bytearray(10_000)


# 1. input is pulled lazily: with a window of 4, only a handful of the 60 inputs are alive at the end
refs = []


def make(n):
    for _ in range(n):
        b = Big()
        refs.append(weakref.ref(b))
        yield b


alive = []


def probe(v, i):
    if i == 59:
        gc.collect()
        alive.append(sum(r() is not None for r in refs))


run(make(60), [Stage("a", lambda v, i: time.sleep(0.005) or v, 2), Stage("z", probe, 1, ordered=True)],
    max_in_flight=4, keep_results=False)
assert alive[0] <= 4 + 2 + 3, f"inputs alive: {alive[0]}"        # window + one per worker + the one probed
print(f"lazy input: {alive[0]} of 60 inputs alive at the end (window 4)")

# 2. keep_results=False retains no output, and on_done still sees every item
outs, seen = [], []


def make_out(v, i):
    o = Big()
    outs.append(weakref.ref(o))
    return o


assert run(range(40), [Stage("a", make_out, 2)], max_in_flight=4, keep_results=False,
           on_done=lambda i, v: seen.append(i)) is None
gc.collect()
assert sorted(seen) == list(range(40)), seen
assert sum(o() is not None for o in outs) == 0, "keep_results=False kept outputs"
print("keep_results=False: nothing retained, on_done saw every item")

# 3. a long stream through a small window finishes and returns ordered results when asked
assert run(iter(range(5000)), [Stage("a", lambda v, i: v + 1, 3)], max_in_flight=8) == list(range(1, 5001))
print("5000 items from an iterator, window 8: ok")

# 4. importing the package loads none of the heavy modules (checked in a clean interpreter)
code = ("import sys; sys.path.insert(0, %r); import stagepipe; "
        "print(sorted(m for m in ('typing','dataclasses','inspect','re','asyncio','threading','queue','_queue') "
        "if m in sys.modules))" % SRC)
loaded = subprocess.run([sys.executable, "-I", "-S", "-c", code], capture_output=True, text=True).stdout.strip()
assert loaded == "[]", loaded
print("import stagepipe loads none of typing, dataclasses, inspect, re, asyncio, threading, queue")
