"""python bench/report.py reports/*.json > summary.md

Merges the JSON lines written by `bench/footprint.py --json` (one file per CI job) into one
Markdown table, ordered by OS then Python version.
"""
import json
import sys
from pathlib import Path


def key(r):
    return (r["os"], tuple(int(x) for x in r["python"].split(".")[:3] if x.isdigit()))


def main(paths) -> None:
    rows = sorted((json.loads(Path(p).read_text(encoding="utf-8").strip().splitlines()[-1]) for p in paths), key=key)
    print("### stagepipe footprint by OS and Python\n")
    print("| OS | Python | import (KB) | import (ms) | run (KB) | us/item, 1 stage | us/item, 3 stages | mixed workload (s) | floor (s) |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        n = lambda v: "n/a" if v is None else v
        print(f"| {r['os']} | {r['python']} | {n(r['import_kb'])} | {r['import_ms']} | {n(r['run_kb'])} | "
              f"{r['us_1_stage']} | {r['us_3_stage']} | {r['mixed_s']} | {r['mixed_floor_s']} |")
    print("\nMemory is resident-set growth in a fresh process (psutil on Windows, `resource` peak elsewhere, "
          "so macOS and Linux figures are peaks and can read higher). Timings come from shared CI runners "
          "and are noisy; read them as orders of magnitude.")


if __name__ == "__main__":
    main(sys.argv[1:])
