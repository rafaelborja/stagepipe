"""python tests/stages_test.py - 0.2: Transient / Permanent, retries, recover=, per-stage on_error, limiter, stop."""
import contextlib
import io
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from stagepipe import (UNFINISHED, Cancelled, Failed, Fail, Permanent, RecoveryError, Rerun, Skip, Stage, Stats,
                       Transient, current, run)


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc as e:
        return e
    raise AssertionError(f"{exc.__name__} not raised")


# ---------------------------------------------------------------- 1. declarations are checked when the stage is created
raises(ValueError, Stage, "x", len, retries=1)                       # a plain Stage declares nothing
raises(ValueError, Stage, "x", len, idempotent=True)
raises(ValueError, Transient, "x", len, recover=lambda c: Rerun())   # Transient can always be repeated
raises(ValueError, Transient, "x", len, idempotent=True)
raises(ValueError, Permanent, "x", len, retries=2)                   # a blind repeat of a permanent change
raises(ValueError, Stage, "x", len, retries=-1)
raises(ValueError, Stage, "x", len, on_error="nope")
raises(ValueError, Stage, "x", len, limiter=object())
Transient("x", len, retries=2)
Permanent("x", len, retries=2, idempotent=True)
Permanent("x", len, retries=2, recover=lambda c: Rerun())
Permanent("x", len)                                                  # never repeated: nothing to prove
Stage("x", len)                                                      # exactly as before
print("declarations: invalid combinations raise ValueError at creation, valid ones pass")

# ---------------------------------------------------------------- 2. retries
calls = {}


def flaky(v, i):
    n = calls[i] = calls.get(i, 0) + 1
    c = current()
    assert c.attempt == n - 1, (c, n)
    if n < 3 and i % 2 == 0:
        raise OSError("transient failure")
    return v * 2


out = run(range(10), [Transient("f", flaky, workers=3, retries=2, backoff=0.01)])
assert out == [v * 2 for v in range(10)], out
assert all(calls[i] == (3 if i % 2 == 0 else 1) for i in range(10))
print("retries: failed twice, succeeded on attempt 3, results in order, current().attempt correct")

calls.clear()
def always(v, i):
    raise OSError("always")


raises(OSError, run, range(4), [Transient("f", always, retries=2, backoff=0.0)])
st = Stats()
res = run(range(4), [Transient("f", always, workers=2, retries=2, backoff=0.0, on_error="collect")], stats=st)
assert all(isinstance(r, Failed) for r in res)
print("retries exhausted: the error policy applies (raise / collect)")

n_calls = []
raises(KeyError, run, [1], [Transient("k", lambda v, i: n_calls.append(1) or {}[1], retries=5, backoff=0.0,
                                      retry_on=(OSError,))])
assert len(n_calls) == 1
n_calls.clear()


def cancelled_inside(v, i):
    n_calls.append(1)
    raise Cancelled()


raises(Cancelled, run, [1], [Transient("c", cancelled_inside, retries=3, backoff=0.0)])
assert len(n_calls) == 1
print("retry_on honoured; Cancelled is never retried")

# a backoff wait ends promptly on cancel
flag = threading.Event()
threading.Timer(0.2, flag.set).start()
t0 = time.perf_counter()
raises(Cancelled, run, [1], [Transient("w", lambda v, i: 1 / 0, retries=3, backoff=30.0)], cancelled=flag.is_set)
assert time.perf_counter() - t0 < 3, time.perf_counter() - t0
print("cancel interrupts a 30 s backoff in %.2fs" % (time.perf_counter() - t0))

# ordered stage with retries still sees every index in order
seen, tries = [], {}


def ord_fn(v, i):
    seen.append(i)
    return v


def flaky2(v, i):
    tries[i] = tries.get(i, 0) + 1
    if i % 3 == 0 and tries[i] == 1:
        raise OSError("x")
    return v


run(range(30), [Transient("a", flaky2, workers=4, retries=1, backoff=0.0),
                Transient("b", ord_fn, workers=1, ordered=True)], max_in_flight=8)
assert seen == list(range(30)), seen
print("ordered stage after retries: every index, in order")

# ---------------------------------------------------------------- 3. Permanent: idempotent and recover=
done = []


def upload(v, i):
    done.append(i)
    if len(done) <= 2:
        raise OSError("lost the reply")
    return v


assert run([7], [Permanent("u", upload, retries=3, backoff=0.0, idempotent=True)]) == [7]
assert len(done) == 3
print("Permanent idempotent=True: retried like a Transient")


def make_recover(answer, log):
    def recover(ctx):
        log.append((ctx.index, ctx.value, ctx.attempt, ctx.began, ctx.finished, ctx.saved))
        return answer(ctx)
    return recover


ran, log = [], []


def charge(v, i):
    ran.append(v)
    raise OSError("timeout after the charge went through")


out = run([5], [Permanent("c", charge, retries=2, backoff=0.0, recover=make_recover(lambda c: Skip("invoice-5"), log))])
assert out == ["invoice-5"] and ran == [5] and log == [(0, 5, 1, True, False, None)], (out, ran, log)
print("recover=Skip(value): the earlier attempt counted as done; stage not called again")

ran.clear(); log.clear()
raises(OSError, run, [5], [Permanent("c", charge, retries=2, backoff=0.0,
                                     recover=make_recover(lambda c: Rerun(), log))])
assert len(ran) == 3 and [l[2] for l in log] == [1, 2]
print("recover=Rerun(): called before each retry; the stage ran again")

ran.clear()
e = raises(RecoveryError, run, [5], [Permanent("c", charge, retries=2, backoff=0.0,
                                               recover=lambda c: Fail("check the ledger"))])
assert "check the ledger" in str(e) and len(ran) == 1
res = run([5], [Permanent("c", charge, retries=2, backoff=0.0, recover=lambda c: Fail("look"), on_error="collect")])
assert isinstance(res[0], Failed) and isinstance(res[0].exc, RecoveryError)
print("recover=Fail(reason): the item fails with RecoveryError, through the stage's error policy")

raises(TypeError, run, [5], [Permanent("c", charge, retries=2, backoff=0.0, recover=lambda c: 42)])
print("recover= returning something else: TypeError")

# ---------------------------------------------------------------- 4. per-stage on_error and "log"
def bad_on_3(v, i):
    if i == 3:
        raise ValueError("bad 3")
    return v


res = run(range(6), [Stage("a", bad_on_3, on_error="collect"), Stage("b", lambda v, i: v + 1)])
assert isinstance(res[3], Failed) and res[3].stage == "a" and res[4] == 5
raises(ValueError, run, range(6), [Stage("a", bad_on_3), Stage("b", lambda v, i: v)], on_error="raise")
# the stage's own setting wins over the run's, both ways
res = run(range(6), [Stage("a", bad_on_3, on_error="collect")], on_error="raise")
assert isinstance(res[3], Failed)
raises(ValueError, run, range(6), [Stage("a", bad_on_3, on_error="raise")], on_error="collect")
res = run(range(6), [Stage("a", bad_on_3, on_error=lambda stage, i, v, exc: -1)])
assert res[3] == -1
buf = io.StringIO()
with contextlib.redirect_stderr(buf):
    res = run(range(6), [Stage("a", bad_on_3, on_error="log")])
assert isinstance(res[3], Failed) and "stage 'a' item 3: ValueError: bad 3" in buf.getvalue(), buf.getvalue()
buf = io.StringIO()
with contextlib.redirect_stderr(buf):
    res = run(range(6), [Stage("a", bad_on_3)], on_error="log")
assert isinstance(res[3], Failed) and buf.getvalue().count("\n") == 1
print("on_error per stage overrides the run's; 'log' writes one line and keeps the item as Failed")

# ---------------------------------------------------------------- 5. limiter shared across stages and runs
lim = threading.BoundedSemaphore(2)
lock = threading.Lock()
now = peak = 0


def limited(v, i):
    global now, peak
    with lock:
        now += 1
        peak = max(peak, now)
    time.sleep(0.01)
    with lock:
        now -= 1
    return v


def one_run():
    run(range(30), [Stage("a", limited, workers=4, limiter=lim)], max_in_flight=8)


ts = [threading.Thread(target=one_run) for _ in range(3)]
for t in ts:
    t.start()
for t in ts:
    t.join()
assert 1 <= peak <= 2, peak
# the limiter is released after an error too (a leaked permit would starve the next run)
lim2 = threading.BoundedSemaphore(1)
raises(ZeroDivisionError, run, range(3), [Stage("a", lambda v, i: 1 / 0, limiter=lim2)])
assert lim2.acquire(timeout=1)
lim2.release()
print("limiter: 3 runs x 4 workers never had more than %d calls in flight; released after an error" % peak)

# ---------------------------------------------------------------- 6. stop
# pipeline: read (Transient) -> upload (Permanent) -> save (Transient)
def slow_pipeline(counter, read_s=0.01, up_s=0.05, save_s=0.01):
    def read(v, i):
        time.sleep(read_s)
        return v

    def up(v, i):
        counter.append(("up", i))
        time.sleep(up_s)
        return v

    def save(v, i):
        time.sleep(save_s)
        counter.append(("save", i))
        return v
    return [Transient("read", read, workers=2), Permanent("upload", up, workers=2), Transient("save", save, workers=1)]


def fed_counter():
    pulled = []

    def gen():
        for n in range(1000):
            pulled.append(n)
            yield n
    return pulled, gen()


for drain in (False, None, True):
    ev = threading.Event()
    rec, st = [], Stats()
    pulled, it = fed_counter()
    threading.Timer(0.12, ev.set).start()
    out = run(it, slow_pipeline(rec), max_in_flight=6, stop=ev, drain=drain, stats=st)
    assert st.stopped and st.dropped >= 0
    unfinished = [i for i, r in enumerate(out) if r is UNFINISHED]
    done_ids = [i for i, r in enumerate(out) if r is not UNFINISHED]
    assert len(out) <= len(pulled) <= len(out) + 1, (len(out), len(pulled))   # nothing pulled and lost
    assert len(pulled) < 1000
    assert st.dropped == len(unfinished)
    uploaded = {i for k, i in rec if k == "up"}
    saved = {i for k, i in rec if k == "save"}
    assert saved == set(done_ids)
    if drain is None:
        # no new effect after the stop; an item that uploaded was carried to its save
        assert uploaded == saved, (sorted(uploaded - saved))
    if drain is False:
        assert uploaded >= saved
    if drain is True:
        assert uploaded == saved == set(range(len(out))), (len(out), len(saved))
    print(f"stop drain={drain!s:5}: {len(done_ids)} finished, {len(unfinished)} UNFINISHED, "
          f"{len(uploaded)} uploaded, {len(saved)} saved, stopped={st.stopped}")

# a stop with nothing running and the input exhausted is just a normal run
ev = threading.Event()
st = Stats()
out = run(range(5), [Transient("a", lambda v, i: v)], stop=ev, stats=st)
assert out == list(range(5)) and not st.stopped
ev.set()
st = Stats()
out = run(range(5), [Transient("a", lambda v, i: v)], stop=ev, stats=st)      # already set before the start
assert out == [] and st.stopped
print("stop: unset stop changes nothing; a stop set before the start pulls nothing from the input")

# ordered stage: a stop must not hang a run whose dropped item leaves a gap
ev = threading.Event()
got = []
threading.Timer(0.08, ev.set).start()
out = run(range(500), [Transient("a", lambda v, i: (time.sleep(0.005 * (i % 3)), v)[1], workers=4),
                       Transient("b", lambda v, i: got.append(i) or v, workers=1, ordered=True)],
          max_in_flight=8, stop=ev, drain=False)
assert got == sorted(got) and [i for i, r in enumerate(out) if r is not UNFINISHED] == sorted(got)
print("stop with an ordered stage: no hang, ordered stage saw increasing indices:", len(got), "items")

# keep_results=False: the call returns None and stats.stopped is the only signal
ev = threading.Event()
st = Stats()
threading.Timer(0.05, ev.set).start()
assert run(range(10 ** 6), [Transient("a", lambda v, i: time.sleep(0.001) or v)], keep_results=False,
           stop=ev, stats=st) is None and st.stopped
print("stop with keep_results=False: returns None, stats.stopped is set")

# on_done is not called for dropped items; cancel still raises
ev = threading.Event()
done_ids = []
threading.Timer(0.05, ev.set).start()
out = run(range(10 ** 6), [Transient("a", lambda v, i: time.sleep(0.001) or v)], stop=ev, drain=False,
          on_done=lambda i, v: done_ids.append(i))
assert sorted(done_ids) == [i for i, r in enumerate(out) if r is not UNFINISHED]
flag = threading.Event()
threading.Timer(0.05, flag.set).start()
raises(Cancelled, run, range(10 ** 6), [Transient("a", lambda v, i: time.sleep(0.001) or v)], cancelled=flag.is_set,
       stop=threading.Event())
raises(ValueError, run, [1], [Stage("a", len)], stop=object())
raises(ValueError, run, [1], [Stage("a", len)], stop=threading.Event(), drain="yes")
print("stop: on_done only for finished items; cancel still raises; bad arguments rejected")
print("OK")
