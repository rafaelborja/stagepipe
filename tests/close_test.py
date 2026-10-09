"""python tests/close_test.py - Stage(init=..., close=...): teardown of per-worker state."""
import _thread
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from stagepipe import Cancelled, Stage, run


class Pool:
    """Records every borrow and give-back, and in which thread."""
    def __init__(self):
        self.lock = threading.Lock()
        self.borrowed, self.closed = [], []
        self.in_call = set()                       # states whose worker is inside a stage call right now
        self.closed_while_in_call = []

    def init(self):
        s = object()
        with self.lock:
            self.borrowed.append((s, threading.current_thread().name))
        return s

    def close(self, s):
        with self.lock:
            self.closed.append((s, threading.current_thread().name))
            if s in self.in_call:
                self.closed_while_in_call.append(s)

    def work(self, sleep=0.01, boom_on=None):
        def fn(v, i, s):
            with self.lock:
                self.in_call.add(s)
            try:
                time.sleep(sleep)
                if boom_on is not None and i == boom_on:
                    raise ValueError("boom")
                return v
            finally:
                with self.lock:
                    self.in_call.discard(s)
        return fn

    def check_all_closed(self, label):
        assert len(self.closed) == len(self.borrowed), (label, len(self.closed), len(self.borrowed))
        assert {s for s, _ in self.closed} == {s for s, _ in self.borrowed}, label
        born = dict(self.borrowed)
        assert all(born[s] == t for s, t in self.closed), f"{label}: close ran in another thread than init"
        assert not self.closed_while_in_call, f"{label}: close ran while a stage call was running"


# 1. normal finish: close once per worker, in the creating thread, before run() returns
p = Pool()
run(range(30), [Stage("a", p.work(), workers=3, init=p.init, close=p.close)], max_in_flight=6)
assert len(p.borrowed) == 3
p.check_all_closed("normal")
print("normal finish: close ran once per worker, in the worker's own thread, before run() returned")

# 2. stage error: the error is raised, every worker still closes
p = Pool()
try:
    run(range(40), [Stage("a", p.work(0.02, boom_on=5), workers=3, init=p.init, close=p.close)], max_in_flight=6)
    raise SystemExit("error lost")
except ValueError:
    pass
p.check_all_closed("error")
print("stage error: original error raised, every worker closed, none while a call was running")

# 3. cancel (raise and partial)
for partial in (False, True):
    p = Pool()
    flag = threading.Event(); threading.Timer(0.15, flag.set).start()
    try:
        run(range(500), [Stage("a", p.work(0.02), workers=3, init=p.init, close=p.close)], max_in_flight=6,
            cancelled=flag.is_set, partial=partial)
        assert partial
    except Cancelled:
        assert not partial
    p.check_all_closed(f"cancel partial={partial}")
print("cancel (raising and partial): every worker closed")

# 4. Ctrl-C
p = Pool()
threading.Timer(0.3, _thread.interrupt_main).start()
try:
    run(range(200), [Stage("a", p.work(0.05), workers=3, init=p.init, close=p.close)], max_in_flight=10)
    raise SystemExit("not interrupted")
except KeyboardInterrupt:
    pass
p.check_all_closed("ctrl-c")
print("Ctrl-C: every worker closed before run() re-raised, none while a call was running")
time.sleep(0.3)

# 5. a failing close does not stop the other workers, and its error surfaces if the run itself succeeded
closed = []
def bad_close(s):
    closed.append(s)
    raise RuntimeError("close failed")
try:
    run(range(20), [Stage("a", lambda v, i, s: v, workers=3, init=object, close=bad_close)])
    raise SystemExit("close error lost")
except RuntimeError as e:
    assert str(e) == "close failed"
assert len(closed) == 3, "a failing close stopped other workers from closing"
print("failing close: all workers still closed; the error is raised because the run itself succeeded")

# 6. the run's own error wins over a close error
try:
    run(range(20), [Stage("a", lambda v, i, s: 1 / 0 if i == 3 else v, workers=2, init=object, close=bad_close)])
    raise SystemExit("error lost")
except ZeroDivisionError:
    pass
print("failing close never hides the run's own error")

# 7. configuration errors
try:
    run([1], [Stage("a", lambda v, i: v, close=lambda s: None)]); raise SystemExit("close without init accepted")
except ValueError as e:
    assert "close needs init" in str(e)

# 8. a worker whose init failed has nothing to close
made, shut = [], []
lock = threading.Lock()
def flaky_init():
    with lock:
        n = len(made); made.append(n)
    if n == 1:
        raise OSError("no model")
    return n
try:
    run(range(10), [Stage("a", lambda v, i, s: v, workers=3, init=flaky_init, close=shut.append)])
except OSError:
    pass
assert sorted(shut) == [n for n in made if n != 1], (made, shut)
print("init failure: only the states that were created are closed")
