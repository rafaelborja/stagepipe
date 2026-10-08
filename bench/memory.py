"""python bench/memory.py - memory cost of importing and of running a pipeline (fresh process each).
Needs psutil (measurement only; stagepipe itself has no dependencies). Optional: aiostream."""
import subprocess, sys, json
from pathlib import Path
SRC = str(Path(__file__).resolve().parent.parent / "src")

CASES = {
 "python alone":      "pass",
 "stagepipe import":  "import stagepipe",
 "asyncio import":    "import asyncio",
 "aiostream import":  "import aiostream.stream",
 "stagepipe run":     """import stagepipe, time
from stagepipe import Stage, run
big = lambda v, i: bytearray(20_000)           # each item is a 20 KB buffer
run(range(2000), [Stage("a", big, 2), Stage("b", lambda v, i: len(v), 2)], max_in_flight=4)""",
 "aiostream run":     """import asyncio
from aiostream import stream, pipe
async def a(x): return bytearray(20_000)
async def b(x): return len(x)
async def main():
    await stream.list(stream.range(2000) | pipe.map(a, task_limit=2) | pipe.map(b, task_limit=2))
asyncio.run(main())""",
}
TEMPLATE = """
import sys; sys.path.insert(0, {src!r})
import psutil, os, json
p = psutil.Process(os.getpid()); base = p.memory_info().rss
{code}
m = p.memory_info()
print(json.dumps({{"rss_mb": round(m.rss/2**20, 1), "peak_mb": round(getattr(m, "peak_wset", m.rss)/2**20, 1)}}))
"""
print(sys.version.split()[0], "- resident / peak working set in MB, fresh process each")
for name, code in CASES.items():
    r = subprocess.run([sys.executable, "-c", TEMPLATE.format(src=SRC, code=code)], capture_output=True, text=True)
    out = r.stdout.strip().splitlines()
    print(f"  {name:18s}", out[-1] if r.returncode == 0 and out else "unavailable: " + (r.stderr.strip().splitlines() or ["?"])[-1])
