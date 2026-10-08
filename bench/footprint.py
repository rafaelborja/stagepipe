"""python bench/footprint.py [--json]

Memory and time cost of stagepipe on THIS interpreter and OS, each measured in a fresh process:
  - import: extra resident memory and milliseconds to `import stagepipe`
  - run:    extra resident memory after pushing 2000 items of 20 KB through two stages
  - overhead: microseconds per item per stage, with no-op stages
  - mixed:  a 3-stage workload of different speeds against its theoretical floor

psutil is used for memory when installed (pip install psutil); otherwise the standard library's
`resource` module on Linux and macOS. CI runs this on every Python/OS and prints one table.
"""
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")

PROBE = r"""
import sys, time, json
sys.path.insert(0, %(src)r)
def rss_kb():
    try:
        import psutil
        return psutil.Process().memory_info().rss // 1024
    except ImportError:
        try:
            import resource
        except ImportError:
            return None                      # Windows without psutil: memory not measurable
        v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return v // 1024 if sys.platform == "darwin" else v
def grew(a, b):
    return None if a is None or b is None else b - a
base = rss_kb()
t = time.perf_counter()
import stagepipe
from stagepipe import Stage, run
import_ms = (time.perf_counter() - t) * 1000
after_import = rss_kb()
big = lambda v, i: bytearray(20_000)
run(range(2000), [Stage("a", big, 2), Stage("b", lambda v, i: len(v), 2)], max_in_flight=4)
after_run = rss_kb()
print(json.dumps({"import_ms": round(import_ms, 1), "import_kb": grew(base, after_import), "run_kb": grew(base, after_run)}))
"""

TIMING = r"""
import sys, time, json
sys.path.insert(0, %(src)r)
from stagepipe import Stage, run
N, f = 20000, (lambda v, i: v)
out = {}
for stages in (1, 3):
    t = time.perf_counter()
    run(range(N), [Stage("s%%d" %% k, f, 2) for k in range(stages)], max_in_flight=64)
    out["us_%%d_stage" %% stages] = round((time.perf_counter() - t) / N * 1e6, 1)
n = 20
A = lambda v, i: time.sleep(0.02) or v
B = lambda v, i: time.sleep(0.15) or v
C = lambda v, i: time.sleep(0.03) or v
t = time.perf_counter()
run(range(n), [Stage("a", A, 4), Stage("b", B, 8), Stage("c", C, 1, ordered=True)], max_in_flight=16)
took = time.perf_counter() - t
floor = n * 0.03
out["mixed_s"], out["mixed_floor_s"] = round(took, 2), round(floor, 2)
print(json.dumps(out))
"""


def probe(code: str) -> dict:
    r = subprocess.run([sys.executable, "-c", code % {"src": SRC}], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise SystemExit(r.stderr)
    return json.loads(r.stdout.strip().splitlines()[-1])


def main() -> None:
    runs = [probe(PROBE) for _ in range(3)]
    mem = min(runs, key=lambda d: d["import_kb"] if d["import_kb"] is not None else 0)   # least noisy of 3
    timing = probe(TIMING)
    result = {"python": platform.python_version(), "os": platform.system(), **mem, **timing}
    if "--json" in sys.argv:
        print(json.dumps(result))
        return
    print(f"stagepipe footprint on Python {result['python']} / {result['os']}")
    kb = lambda v: "n/a (pip install psutil)" if v is None else f"+{v} KB resident"
    print(f"  import:   {kb(result['import_kb'])}, {result['import_ms']} ms")
    print(f"  run:      {kb(result['run_kb'])} after 2000 x 20 KB items (window 4)")
    print(f"  overhead: {result['us_1_stage']} us/item with 1 no-op stage, {result['us_3_stage']} us/item with 3")
    print(f"  mixed:    {result['mixed_s']} s for a workload whose floor is {result['mixed_floor_s']} s")


if __name__ == "__main__":
    main()
