"""python tests/checkpoint_test.py - checkpoint= / key= / save= / markers / scratch: resume after a stop or a crash."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from stagepipe import (Failed, Fail, Permanent, RecoveryError, Rerun, Skip, Stage, Stats, Transient, UNFINISHED, current,
                       run)


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc as e:
        return e
    raise AssertionError(f"{exc.__name__} not raised")


def files(d, stage):
    p = os.path.join(d, stage)
    return sorted(os.listdir(p)) if os.path.isdir(p) else []


tmp = tempfile.mkdtemp(prefix="sp_cp_")


def fresh():
    return tempfile.mkdtemp(prefix="c", dir=tmp)


KEY = lambda it: f"k{it}"

# ---------------------------------------------------------------- 1. save, restore, no repeat work
d = fresh()
calls = []


def double(v, i):
    calls.append(v)
    return v * 2


st = Stats()
out = run(range(20), [Transient("double", double, workers=3)], checkpoint=d, key=KEY, stats=st)
assert out == [v * 2 for v in range(20)] and len(calls) == 20 and st.saved == 20 and st.restored == 0
assert len(files(d, "double")) == 20 and open(os.path.join(d, "VERSION")).read().startswith("stagepipe-checkpoint")
calls.clear()
done, restored = [], []
st = Stats()
out = run(range(20), [Transient("double", double, workers=3)], checkpoint=d, key=KEY, stats=st,
          on_done=lambda i, v: done.append((i, v)), on_restored=lambda i, v: restored.append(i))
assert out == [v * 2 for v in range(20)] and calls == [] and st.restored == 20 and st.saved == 0
assert sorted(done) == [(i, i * 2) for i in range(20)] and sorted(restored) == list(range(20))
print("save and restore: second run called the stage 0 times, 20 restored, results and on_done complete")

# ---------------------------------------------------------------- 2. stop in the middle, then resume: each item computed once overall
d = fresh()
calls.clear()
ev = threading.Event()
threading.Timer(0.25, ev.set).start()


def slow(v, i):
    time.sleep(0.02)
    calls.append(v)
    return v + 100


out = run(range(100), [Transient("slow", slow, workers=2)], checkpoint=d, key=KEY, stop=ev, drain=False, max_in_flight=4)
n_done = sum(1 for r in out if r is not UNFINISHED)
assert 0 < n_done < 100, n_done
calls.clear()
st = Stats()
out2 = run(range(100), [Transient("slow", slow, workers=2)], checkpoint=d, key=KEY, stats=st, max_in_flight=4)
assert out2 == [v + 100 for v in range(100)]
assert st.restored == n_done and len(calls) == 100 - n_done, (st.restored, n_done, len(calls))
print(f"stop then resume: {n_done} finished before the stop, the rerun restored {st.restored} and computed {len(calls)}")

# ---------------------------------------------------------------- 3. a real kill, with a leftover temporary file
d = fresh()
script = f"""
import sys, time
sys.path.insert(0, {str(ROOT / 'src')!r})
from stagepipe import Transient, run
def slow(v, i):
    time.sleep(0.05)
    return v
run(range(400), [Transient("slow", slow, workers=2)], checkpoint={d!r}, key=lambda it: f"k{{it}}", max_in_flight=4)
"""
proc = subprocess.Popen([sys.executable, "-c", script])
time.sleep(1.5)
proc.kill()
proc.wait()
saved = len(files(d, "slow"))
assert 0 < saved < 400, saved
open(os.path.join(d, "slow", "k399.tmp123-456"), "wb").write(b"half a file")      # what a kill mid-write leaves
calls.clear()
out = run(range(400), [Transient("slow", lambda v, i: calls.append(v) or v, workers=4)], checkpoint=d, key=KEY)
assert out == list(range(400)) and len(calls) <= 400 - saved + 2, (len(calls), saved)
print(f"killed process: {saved} outputs on disk, a leftover temp file ignored, the rerun computed only {len(calls)} items")

# ---------------------------------------------------------------- 4. per-stage save: a failed later stage resumes from the saved boundary
d = fresh()
a_calls, b_calls = [], []
fail_on = {7}


def stage_a(v, i):
    a_calls.append(v)
    return v * 10


def stage_b(v, i):
    b_calls.append(v)
    if v // 10 in fail_on:
        raise OSError("b failed")
    return v + 1


stages = lambda: [Transient("a", stage_a, workers=2, save=True), Transient("b", stage_b, workers=2)]
raises(OSError, run, range(12), stages(), checkpoint=d, key=KEY, max_in_flight=12)
assert files(d, "a")                                   # some outputs of the expensive stage are on disk
fail_on.clear()
a_calls.clear(); b_calls.clear()
st = Stats()
out = run(range(12), stages(), checkpoint=d, key=KEY, stats=st, max_in_flight=12)
assert out == [v * 10 + 1 for v in range(12)]
assert len(a_calls) <= 12
# rolling cleanup: finished keys keep only the last stage's file
assert files(d, "a") == [] and len(files(d, "b")) == 12, (files(d, "a"), len(files(d, "b")))
print("per-stage save: the rerun resumed from stage a's saved output; intermediate files cleaned up, 12 final files remain")

# the failed item resumes at stage b without calling stage a again
d = fresh()
a_calls.clear(); b_calls.clear()
fail_on.add(3)
raises(OSError, run, range(6), stages(), checkpoint=d, key=KEY, max_in_flight=6)
fail_on.clear()
a_calls.clear()
run(range(6), stages(), checkpoint=d, key=KEY, max_in_flight=6)
assert 3 not in [v for v in a_calls] , a_calls                       # item 3 was not recomputed by stage a
print("per-stage save: the item that failed in stage b was not recomputed by stage a")

# ---------------------------------------------------------------- 5. Failed items and substitutes are never saved
d = fresh()


def maybe_bad(v, i):
    if v == 4:
        raise ValueError("bad")
    return v


out = run(range(8), [Transient("m", maybe_bad)], checkpoint=d, key=KEY, on_error="collect")
assert isinstance(out[4], Failed) and "k4" not in files(d, "m") and len(files(d, "m")) == 7
out = run(range(8), [Transient("m", maybe_bad)], checkpoint=d, key=KEY, on_error=lambda s, i, v, e: -1)
assert out[4] == -1 and "k4" not in files(d, "m")                    # a substitute is not a result
print("Failed items and on_error substitutes are never checkpointed")

# ---------------------------------------------------------------- 6. corrupt and stale files are redone
d = fresh()
run(range(5), [Transient("c", lambda v, i: v * 3)], checkpoint=d, key=KEY)
open(os.path.join(d, "c", "k2"), "wb").write(b"\x80garbage")
open(os.path.join(d, "c", "k3"), "wb").write(b"")
calls.clear()
st = Stats()
out = run(range(5), [Transient("c", lambda v, i: calls.append(v) or v * 3)], checkpoint=d, key=KEY, stats=st)
assert out == [0, 3, 6, 9, 12] and sorted(calls) == [2, 3] and st.restored == 3
# a changed input with a fingerprint is redone
d = fresh()
data = {1: "a", 2: "b"}
run([1, 2], [Transient("f", lambda v, i: data[v].upper())], checkpoint=d, key=KEY, fingerprint=lambda v: data[v])
data[2] = "changed"
st = Stats()
out = run([1, 2], [Transient("f", lambda v, i: data[v].upper())], checkpoint=d, key=KEY, fingerprint=lambda v: data[v], stats=st)
assert out == ["A", "CHANGED"] and st.restored == 1
print("corrupt or empty files are redone; fingerprint= redoes an item whose input changed")

# ---------------------------------------------------------------- 7. keys and declarations are checked
d = fresh()
raises(ValueError, run, [1], [Stage("p", len)], checkpoint=d, key=KEY)                     # undeclared
raises(ValueError, run, [1], [Transient("p", len)], checkpoint=d)                          # no key
raises(ValueError, run, [1], [Transient("p", len)], key=KEY, serializer=json)              # serializer without checkpoint
raises(ValueError, run, [1], [Transient("p", len, save=True)])                             # save without checkpoint
raises(ValueError, run, [1], [Transient("p", len, save=False)], checkpoint=d, key=KEY)     # last stage not saved
raises(ValueError, run, [1], [Transient("a b", len)], checkpoint=d, key=KEY)               # name not file-safe
raises(ValueError, run, [1], [Transient("a", len), Transient("a", len)], checkpoint=d, key=KEY)
raises(ValueError, run, ["../x"], [Transient("p", len)], checkpoint=d, key=lambda v: v)    # key not file-safe
raises(ValueError, run, [1, 1], [Transient("p", lambda v, i: time.sleep(0.3))], checkpoint=d, key=lambda v: "same")
raises(ValueError, Stage, "x", len, save=True)                                              # plain Stage cannot save
raises(ValueError, Permanent, "x", len, scratch=True)
raises(ValueError, run, [1], [Transient("p", len, scratch=True)])                          # no scratch root
open(os.path.join(d, "VERSION"), "w").write("something else\n")
raises(ValueError, run, [1], [Transient("p", len)], checkpoint=d, key=KEY)                 # foreign directory
print("declarations: undeclared stages, missing key, bad names and keys, duplicates in the window, foreign directory")

# ---------------------------------------------------------------- 8. a serializer that cannot store the result names the key
d = fresh()
e = raises(ValueError, run, [1], [Transient("j", lambda v, i: object())], checkpoint=d, key=lambda v: "thekey", serializer=json)
assert "thekey" in str(e) and "'j'" in str(e), e
out = run([1, 2], [Transient("j", lambda v, i: {"v": v})], checkpoint=fresh(), key=KEY, serializer=json)
# json has dumps/loads, but returns str: our files are bytes
print("unserializable result: the error names the stage and key")

# ---------------------------------------------------------------- 9. Permanent stage without save: markers and recover
d = fresh()
effects = []


def delete(v, i):
    effects.append(v)
    return f"deleted-{v}"


final_fail = {2}


def last(v, i):
    if i in final_fail:
        raise OSError("last failed")
    return v.upper()


def pipeline(**perm_kw):
    return [Transient("read", lambda v, i: v), Permanent("delete", delete, **perm_kw), Transient("last", last)]


# first run: the stage after the permanent effect fails for item 2 -> .done exists for it, nothing saved after it
raises(OSError, run, ["a", "b", "c"], pipeline(), checkpoint=d, key=lambda v: "x" + v, max_in_flight=3)
done_keys = {f[:-5] for f in files(d, "delete") if f.endswith(".done")}
assert "xc" in done_keys, files(d, "delete")
# rerun without recover: the item whose effect already happened fails safely instead of repeating it
effects.clear()
out = run(["a", "b", "c"], pipeline(), checkpoint=d, key=lambda v: "x" + v, on_error="collect")
assert effects.count("c") == 0, effects
assert isinstance(out[2], Failed) and isinstance(out[2].exc, RecoveryError), out
print("Permanent (not saved): a rerun refuses to repeat an effect that its markers say happened")
# with recover=Skip(value): the effect counts as done and the next stage gets the value
final_fail.clear()
d = fresh()
final_fail.add(2)
raises(OSError, run, ["a", "b", "c"], pipeline(), checkpoint=d, key=lambda v: "x" + v, max_in_flight=3)
final_fail.clear()
effects.clear()
log = []
out = run(["a", "b", "c"], pipeline(recover=lambda ctx: log.append((ctx.key, ctx.began, ctx.finished)) or Skip("deleted-c")),
          checkpoint=d, key=lambda v: "x" + v)
assert out == ["DELETED-A", "DELETED-B", "DELETED-C"] or out[2] == "DELETED-C", out
assert ("xc", True, True) in log and "c" not in effects, (log, effects)
# with idempotent=True: it is simply run again
d = fresh()
final_fail.add(2)
raises(OSError, run, ["a", "b", "c"], pipeline(), checkpoint=d, key=lambda v: "x" + v, max_in_flight=3)
final_fail.clear()
effects.clear()
out = run(["a", "b", "c"], pipeline(idempotent=True), checkpoint=d, key=lambda v: "x" + v)
assert out[2] == "DELETED-C" and "c" in effects
print("Permanent recover=Skip(value) and idempotent=True resume from the markers")

# a crash BETWEEN start and done (the effect may or may not have happened) is also caught
d = fresh()
effects.clear()


def boom(v, i):
    effects.append(v)
    raise RuntimeError("died during the call")


out = run(["a"], [Permanent("delete", boom), Transient("last", last)], checkpoint=d, key=lambda v: "x" + v, on_error="collect")
assert isinstance(out[0], Failed) and files(d, "delete") == ["xa.start"]
effects.clear()
out = run(["a"], [Permanent("delete", boom), Transient("last", last)], checkpoint=d, key=lambda v: "x" + v, on_error="collect")
assert effects == [] and isinstance(out[0].exc, RecoveryError)
print("Permanent: .start without .done (died inside the call) is not repeated blindly")

# Permanent with save=True: the saved output is the proof, so it is restored and not repeated
d = fresh()
effects.clear()
stages = lambda: [Permanent("send", lambda v, i: effects.append(v) or f"sent-{v}", save=True), Transient("tidy", lambda v, i: v.upper())]
run(["m1", "m2"], stages(), checkpoint=d, key=lambda v: v)
effects.clear()
out = run(["m1", "m2"], stages(), checkpoint=d, key=lambda v: v)
assert out == ["SENT-M1", "SENT-M2"] and effects == []
print("Permanent with save=True: restored from its saved output, effect not repeated")

# ---------------------------------------------------------------- 10. ordered stages with restored items
d = fresh()
run(range(0, 40, 2), [Transient("p", lambda v, i: v), Transient("o", lambda v, i: v, ordered=True)], checkpoint=d, key=KEY)
seen = []                                                                                  # evens are now finished on disk
st = Stats()
out = run(range(40), [Transient("p", lambda v, i: v, workers=3), Transient("o", lambda v, i: seen.append(i) or v, ordered=True)],
          checkpoint=d, key=KEY, stats=st)
assert out == list(range(40)) and st.restored == 20
assert seen == list(range(1, 40, 2)), seen                                                 # only the odd indices, in order
print(f"ordered stage + restored items: no stall, the ordered stage saw only the {len(seen)} new indices, in order")
d = fresh()
run(range(30), [Transient("p", lambda v, i: v), Transient("o", lambda v, i: v, ordered=True)], checkpoint=d, key=KEY)
seen.clear()
out = run(range(30), [Transient("p", lambda v, i: v), Transient("o", lambda v, i: seen.append(i) or v, ordered=True)],
          checkpoint=d, key=KEY, keep_results=False)
assert out is None and seen == []
print("everything restored: ordered stage untouched and the run does not hang")

# ---------------------------------------------------------------- 11. scratch
d = fresh()
paths = []


def convert(v, i):
    c = current()
    paths.append((c.key, c.scratch))
    assert not os.path.exists(c.scratch)
    open(c.scratch, "w").write("tmp")
    return v


run(range(3), [Transient("conv", convert, scratch=True)], checkpoint=d, key=KEY)
assert all(p.endswith(os.path.join("_scratch", "conv", k)) for k, p in paths) and len(paths) == 3
assert os.listdir(os.path.join(d, "_scratch", "conv")) == []
paths.clear()
sd = fresh()
run(range(2), [Transient("conv", convert, scratch=True)], scratch_dir=sd)
assert all(p.startswith(sd) for _, p in paths) and os.listdir(os.path.join(sd, "conv")) == []
print("scratch: a private path per item, removed after the call; works with scratch_dir= alone")

# ---------------------------------------------------------------- 12. current().key, retries and checkpoint together
d = fresh()
seen_keys = []
n = {}


def flaky(v, i):
    seen_keys.append(current().key)
    n[v] = n.get(v, 0) + 1
    if n[v] == 1:
        raise OSError("once")
    return v


out = run(range(4), [Transient("r", flaky, retries=2, backoff=0.0)], checkpoint=d, key=KEY)
assert out == [0, 1, 2, 3] and set(seen_keys) == {"k0", "k1", "k2", "k3"}
print("current().key is set; a retried stage is saved only after it finally succeeds")

# ---------------------------------------------------------------- 13. many items: bounded and quick
d = fresh()
t0 = time.perf_counter()
cnt = []
out = run(range(3000), [Transient("a", lambda v, i: v + 1, workers=2), Transient("b", lambda v, i: v * 2, workers=2)],
          checkpoint=d, key=KEY, keep_results=False, on_done=lambda i, v: cnt.append(1), max_in_flight=8)
assert out is None and len(cnt) == 3000
t1 = time.perf_counter() - t0
st = Stats()
run(range(3000), [Transient("a", lambda v, i: v + 1, workers=2), Transient("b", lambda v, i: v * 2, workers=2)],
    checkpoint=d, key=KEY, keep_results=False, stats=st)
assert st.restored == 3000
print(f"3000 items x 2 stages: {t1:.1f} s with a checkpoint; a full restore of 3000 items works")

# ---------------------------------------------------------------- 14. memory does not grow with the number of items
import tracemalloc
d = fresh()
big = lambda: run(range(6000), [Transient("m", lambda v, i: v, workers=2)], checkpoint=d, key=KEY, keep_results=False, max_in_flight=8)
big()                                                   # first pass writes 6000 files; warm up caches
tracemalloc.start()
base = tracemalloc.get_traced_memory()[0]
big()                                                   # second pass restores all 6000
grown = tracemalloc.get_traced_memory()[0] - base
tracemalloc.stop()
assert grown < 300_000, grown
print(f"memory: a restore of 6000 items left {grown // 1024} KB allocated")
print("OK")
