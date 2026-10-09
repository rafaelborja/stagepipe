"""python tests/features_test.py - error policy, per-worker init, stats, partial results, Ctrl-C."""
import _thread
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from stagepipe import UNFINISHED, Cancelled, Failed, Stage, Stats, run


def boom_on(*bad):
    def fn(v, i):
        if i in bad:
            raise ValueError(f"bad {i}")
        return v
    return fn


# ---- error policy ---------------------------------------------------------------------------
try:                                                                  # default: first error aborts
    run(range(10), [Stage("a", boom_on(3), 2)]); raise SystemExit("default policy did not raise")
except ValueError:
    pass
try:
    run(range(10), [Stage("a", boom_on(3), 2)], on_error="raise"); raise SystemExit("raise policy did not raise")
except ValueError:
    pass
print("on_error default and 'raise' abort the run")

seen = []
out = run(range(10), [Stage("a", boom_on(2, 7), 3), Stage("b", lambda v, i: v * 10, 2), Stage("c", lambda v, i: v + 1, 1, ordered=True)],
          on_error="collect", on_done=lambda i, v: seen.append(i))
assert [type(x) is Failed for x in out].count(True) == 2, out
assert out[2].stage == "a" and out[2].index == 2 and isinstance(out[2].exc, ValueError)
assert out[7].stage == "a"
assert [x for i, x in enumerate(out) if i not in (2, 7)] == [i * 10 + 1 for i in range(10) if i not in (2, 7)]
assert sorted(seen) == list(range(10))                                # on_done sees failed items too
print("on_error='collect': failed items carried as Failed, the rest finish, ordered stage does not stall")

called = []
def handler(stage, i, value, exc):
    called.append((stage, i, value, type(exc).__name__)); return "marker"
out = run(range(6), [Stage("a", boom_on(4), 2), Stage("b", lambda v, i: v, 1, ordered=True)], on_error=handler)
assert out[4] == "marker" and called == [("a", 4, 4, "ValueError")], (out, called)
print("on_error=callable: its return value replaces the item")

def stopper(stage, i, value, exc):
    raise RuntimeError("stop the run")
try:
    run(range(6), [Stage("a", boom_on(1), 2)], on_error=stopper); raise SystemExit("callable did not stop the run")
except RuntimeError as e:
    assert str(e) == "stop the run"
print("on_error callable that raises stops the run")

for bad in ("skip", 3):
    try:
        run([1], [Stage("a", lambda v, i: v)], on_error=bad); raise SystemExit("bad on_error accepted")
    except ValueError:
        pass
try:
    run([1], [Stage("a", lambda v, i: v)], partial=True, keep_results=False); raise SystemExit("partial+keep_results=False accepted")
except ValueError:
    pass

# ---- per-worker init ------------------------------------------------------------------------
inits, seen_states = [], []
def make_state():
    s = object(); inits.append(threading.current_thread().name); return s
def use_state(v, i, state):
    seen_states.append((threading.current_thread().name, state)); time.sleep(0.01); return v
run(range(40), [Stage("a", use_state, workers=3, init=make_state)], max_in_flight=6)
assert len(inits) == 3 and len(set(inits)) == 3, inits               # once per worker thread
by_thread = {}
for name, st in seen_states:
    by_thread.setdefault(name, set()).add(id(st))
assert all(len(v) == 1 for v in by_thread.values()), "a worker saw more than one state"
print("Stage(init=): called once in each worker thread, same state on every call")

try:
    run(range(5), [Stage("a", lambda v, i, s: v, workers=2, init=lambda: 1 / 0)]); raise SystemExit("init error lost")
except ZeroDivisionError:
    pass
print("an error in init stops the run")

# ---- stats ----------------------------------------------------------------------------------
st = Stats()
run(range(24), [Stage("fast", lambda v, i: time.sleep(0.005) or v, 2), Stage("slow", lambda v, i: time.sleep(0.05) or v, 2)],
    max_in_flight=12, stats=st)
names = [s.name for s in st.stages]
assert names == ["fast", "slow"] and all(s.items == 24 for s in st.stages)
assert st.bottleneck().name == "slow" and st.busy(st.stages[1]) > 0.7, st.report()
assert st.stages[1].wait_s > st.stages[0].wait_s * 3, "queue wait should pile up in front of the slow stage"
assert st.stages[1].max_queue >= 2
rep = st.report()
assert '"slow" is the bottleneck' in rep and "wall time" in rep, rep
print(rep)
print("stats: bottleneck named, queue wait recorded in front of the slow stage")

# ---- partial results on cancel --------------------------------------------------------------
flag = threading.Event(); threading.Timer(0.25, flag.set).start()
out = run(range(200), [Stage("a", lambda v, i: time.sleep(0.02) or v * 2, 2)], max_in_flight=6, cancelled=flag.is_set, partial=True)
done = [x for x in out if x is not UNFINISHED]
assert 0 < len(done) < 200 and len(out) < 200 + 1, (len(done), len(out))
assert all(out[i] == i * 2 for i, x in enumerate(out) if x is not UNFINISHED)
assert any(x is UNFINISHED for x in out) or len(out) == len(done)
print(f"partial results on cancel: {len(done)} finished, {sum(x is UNFINISHED for x in out)} marked UNFINISHED")
flag2 = threading.Event(); threading.Timer(0.1, flag2.set).start()
try:
    run(range(200), [Stage("a", lambda v, i: time.sleep(0.02) or v, 2)], cancelled=flag2.is_set); raise SystemExit("not cancelled")
except Cancelled:
    pass
print("without partial=True, cancel still raises Cancelled")

# ---- Ctrl-C ---------------------------------------------------------------------------------
threading.Timer(0.3, _thread.interrupt_main).start()
t = time.time()
try:
    run(range(100), [Stage("w", lambda v, i: time.sleep(0.1) or v, 2)], max_in_flight=100); raise SystemExit("not interrupted")
except KeyboardInterrupt:
    took = time.time() - t
assert took < 2.0, f"Ctrl-C took {took:.1f}s (was 5.0s before the fix)"
print(f"Ctrl-C: run() returned after {took:.2f}s (the job would have taken 5 s)")
time.sleep(0.5)

# ---- on_done raising is still an abort ------------------------------------------------------
try:
    run(range(10), [Stage("a", lambda v, i: v, 3)], on_done=lambda i, v: 1 / 0); raise SystemExit("on_done error lost")
except ZeroDivisionError:
    pass
print("a raising on_done aborts the run")
