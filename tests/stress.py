"""python tests/stress.py - checks stagepipe: no stage barrier, ordered stage, errors."""
import sys, time, threading
from pathlib import Path
from stagepipe import Stage, run, Cancelled

# 1. no barrier: page 0 reaches 'finish' while later pages are still in the slow stage
t0, finish_at = time.time(), {}
def fast(v, i): time.sleep(0.05); return v
def slow(v, i): time.sleep(1.0 if i else 0.1); return v
def fin(v, i): finish_at[i] = time.time() - t0; return v * 10
out = run(range(6), [Stage("a", fast, 2), Stage("b", slow, 3), Stage("c", fin, 1, ordered=True)], max_in_flight=6)
assert out == [0, 10, 20, 30, 40, 50], out
assert finish_at[0] < 0.5 < finish_at[1], finish_at   # page 0 did not wait for the others
print("no-barrier ok", {k: round(v, 2) for k, v in finish_at.items()})

# 2. ordered stage sees 0..n-1 in order even when upstream finishes out of order
seen = []
def jitter(v, i): time.sleep((5 - i) * 0.02); return v
run(range(6), [Stage("j", jitter, 6), Stage("o", lambda v, i: seen.append(i) or v, 1, ordered=True)])
assert seen == list(range(6)), seen
print("ordered ok")

# 3. error propagates, run returns promptly
def boom(v, i):
    if i == 2: raise ValueError("x")
    time.sleep(0.05); return v
try: run(range(20), [Stage("b", boom, 3)]); raise SystemExit("no error raised")
except ValueError: print("error ok")

# 4. cancel
flag = threading.Event(); threading.Timer(0.1, flag.set).start()
try: run(range(50), [Stage("s", lambda v, i: time.sleep(0.05) or v, 2)], cancelled=flag.is_set); raise SystemExit("not cancelled")
except Cancelled: print("cancel ok")

# 5. max_in_flight bounds concurrency of items
live, peak, lock = 0, 0, threading.Lock()
def a(v, i):
    global live, peak
    with lock: live += 1; peak = max(peak, live)
    return v
def b(v, i):
    global live
    time.sleep(0.02)
    with lock: live -= 1
    return v
run(range(30), [Stage("a", a, 4), Stage("b", b, 1)], max_in_flight=3)
assert peak <= 3, peak
print("in-flight ok", peak)

# ---- stress: shutdown, last-stage failure, tiny queues, many items, no leaked threads ----
import random
base_threads = threading.active_count()

def rnd(v, i): time.sleep(random.random() * 0.003); return v
for trial in range(30):                                   # many items, tiny window, mixed workers
    n = random.randint(1, 120)
    out = run(range(n), [Stage("a", rnd, random.randint(1, 5)), Stage("b", rnd, random.randint(1, 5)),
                         Stage("c", rnd, 1, ordered=True)], max_in_flight=random.randint(1, 4))
    assert out == list(range(n)), (trial, n)
print("stress random ok")

def last_boom(v, i):
    if i == 7: raise KeyError("last")
    return v
for _ in range(20):                                       # failure in the LAST (ordered) stage
    t = time.time()
    try: run(range(60), [Stage("a", rnd, 4), Stage("z", last_boom, 1, ordered=True)], max_in_flight=3); raise SystemExit("no error")
    except KeyError: assert time.time() - t < 5
print("last-stage failure ok")

def first_boom(v, i):
    if i == 0: raise RuntimeError("first")
    time.sleep(0.01); return v
for _ in range(20):                                       # failure on item 0 while others are queued
    try: run(range(200), [Stage("a", first_boom, 4), Stage("b", rnd, 2)], max_in_flight=2); raise SystemExit("no error")
    except RuntimeError: pass
print("first-item failure ok")

try: run(range(5), [Stage("o", rnd, 2, ordered=True)]); raise SystemExit("accepted bad config")
except ValueError: pass
assert run([], [Stage("a", rnd)]) == []
assert run([7], [Stage("a", lambda v, i: v + 1)]) == [8]
seen = []                                                 # on_done sees every item once; its failure propagates
run(range(10), [Stage("a", rnd, 3)], on_done=lambda i, v: seen.append(i)); assert sorted(seen) == list(range(10))
try: run(range(10), [Stage("a", rnd, 3)], on_done=lambda i, v: 1 / 0); raise SystemExit("on_done error lost")
except ZeroDivisionError: pass
print("edge cases ok")

time.sleep(0.5)
assert threading.active_count() <= base_threads + 1, ("leaked threads", threading.active_count() - base_threads)
print("no leaked threads ok")

# ---- several runs at once do not interfere (no shared state between runs) ----
box = {}
def one(tag, n):
    box[tag] = run(range(n), [Stage("a", rnd, 3), Stage("b", rnd, 1, ordered=True)], max_in_flight=3)
ths = [threading.Thread(target=one, args=(k, 20 + k)) for k in range(8)]
[th.start() for th in ths]; [th.join() for th in ths]
assert all(box[k] == list(range(20 + k)) for k in range(8)), "concurrent runs mixed up"
print("concurrent runs ok")
