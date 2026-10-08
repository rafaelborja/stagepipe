"""python bench/bench.py - the numbers quoted in the README."""
import sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from stagepipe import Stage, run

print(sys.version.split()[0])

# 1. cost of the machinery: no-op stages
N, f = 20000, (lambda v, i: v)
for stages in (1, 3):
    t = time.perf_counter()
    run(range(N), [Stage(f"s{k}", f, 2) for k in range(stages)], max_in_flight=64)
    dt = time.perf_counter() - t
    print(f"overhead, {stages} no-op stage(s), {N} items: {N / dt:,.0f} items/s = {dt / N * 1e6:.0f} us per item")

# 2. three stages of different speed
n = 40
A = lambda v, i: time.sleep(0.02) or v   # fast
B = lambda v, i: time.sleep(0.30) or v   # slow, waiting on the network
C = lambda v, i: time.sleep(0.05) or v   # medium, needs order

t0 = time.perf_counter(); first = []
for x in range(n):
    C(B(A(x, 0), 0), 0)
    first = first or [time.perf_counter() - t0]
seq = time.perf_counter() - t0

t0 = time.perf_counter()
with ThreadPoolExecutor(4) as p: r = list(p.map(lambda x: A(x, 0), range(n)))
with ThreadPoolExecutor(8) as p: r = list(p.map(lambda x: B(x, 0), r))
firstbar = None
for x in r:
    C(x, 0)
    firstbar = firstbar or time.perf_counter() - t0
bar = time.perf_counter() - t0

t0 = time.perf_counter(); firstpipe = []
run(range(n), [Stage("a", A, 4), Stage("b", B, 8), Stage("c", C, 1, ordered=True)], max_in_flight=16,
    on_done=lambda i, v: firstpipe.append(time.perf_counter() - t0) if not firstpipe else None)
pipe = time.perf_counter() - t0
print(f"{n} items, stages 0.02/0.30/0.05 s")
print(f"  one at a time          total {seq:5.1f}s  first result {first[0]:.2f}s")
print(f"  stage by stage (pools) total {bar:5.1f}s  first result {firstbar:.2f}s")
print(f"  stagepipe              total {pipe:5.1f}s  first result {firstpipe[0]:.2f}s")
print(f"  floor (last stage, 1 worker): {n * 0.05:.1f}s")
