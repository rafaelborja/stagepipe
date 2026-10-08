# Benchmarks: memory and speed

Measured by the project's own CI on GitHub-hosted runners for **stagepipe 0.0.3**, on every Python
from 3.10 to 3.14 and on Linux, Windows and macOS (15 combinations). The raw run, with all
artifacts, is [publish run 37846641031](https://github.com/rafaelborja/stagepipe/actions/runs/37846641031).
Reproduce it on your own machine with `python bench/footprint.py` (add `pip install psutil` for
memory on Windows).

## What is measured

| Column | Meaning |
|---|---|
| import (KB) | Extra resident memory after `import stagepipe`, in a fresh process. |
| import (ms) | Wall time of `import stagepipe`. |
| run (KB) | Extra resident memory after pushing 2,000 items of 20 KB through two stages with `max_in_flight=4`. |
| us/item, 1 / 3 stages | Cost of the machinery alone: microseconds per item per run, with no-op stages. |
| mixed workload (s) | 20 items through three stages of 0.02 s, 0.15 s and 0.03 s with 4, 8 and 1 workers. |
| floor (s) | The theoretical minimum for that workload: the last stage has one worker and 20 items at 0.03 s. |

## Results

| OS | Python | import (KB) | import (ms) | run (KB) | us/item, 1 stage | us/item, 3 stages | mixed workload (s) | floor (s) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Darwin | 3.10.11 | 1952 | 12.0 | 2592 | 8.2 | 14.2 | 1.47 | 0.6 |
| Darwin | 3.11.9 | 2208 | 16.3 | 2704 | 4.9 | 9.0 | 2.72 | 0.6 |
| Darwin | 3.12.10 | 2480 | 17.1 | 3040 | 5.1 | 11.8 | 1.82 | 0.6 |
| Darwin | 3.13.15 | 2528 | 16.6 | 2832 | 3.8 | 6.6 | 1.83 | 0.6 |
| Darwin | 3.14.7 | 2576 | 11.0 | 2864 | 3.2 | 7.1 | 2.77 | 0.6 |
| Linux | 3.10.22 | 1668 | 9.5 | 2240 | 7.9 | 14.9 | 0.78 | 0.6 |
| Linux | 3.11.17 | 1908 | 11.9 | 2440 | 6.1 | 11.9 | 0.78 | 0.6 |
| Linux | 3.12.15 | 1916 | 8.9 | 2460 | 3.3 | 6.4 | 0.77 | 0.6 |
| Linux | 3.13.16 | 2452 | 9.0 | 2984 | 3.9 | 8.1 | 0.78 | 0.6 |
| Linux | 3.14.8 | 2484 | 8.1 | 3020 | 2.5 | 5.1 | 0.77 | 0.6 |
| Windows | 3.10.11 | 1616 | 17.6 | 1940 | 8.8 | 17.0 | 0.82 | 0.6 |
| Windows | 3.11.9 | 2092 | 17.8 | 2420 | 6.8 | 18.2 | 0.78 | 0.6 |
| Windows | 3.12.10 | 2068 | 14.5 | 2408 | 5.5 | 10.7 | 0.78 | 0.6 |
| Windows | 3.13.15 | 2288 | 16.6 | 2616 | 5.7 | 11.3 | 0.78 | 0.6 |
| Windows | 3.14.7 | 2468 | 18.0 | 2704 | 6.0 | 12.2 | 0.78 | 0.6 |

Memory is resident-set growth in a fresh process (psutil on Windows, `resource` peak elsewhere, so macOS and Linux figures are peaks and can read higher). Timings come from shared CI runners and are noisy; read them as orders of magnitude.

## How to read them

- **Memory:** 1.6 to 2.6 MB to import and 1.9 to 3.0 MB after a run. Most of the import cost is
  `dataclasses` and `typing`, which the roadmap removes (a prototype already measures about 0.2 MB).
- **Overhead:** 2.5 to 9 microseconds per item for a one-stage run and 5 to 18 for three stages (the
  figure is for the whole run, per item), irrelevant next to steps that take milliseconds to minutes. stagepipe is not built to move nanoseconds.
- **Mixed workload:** on Linux and Windows it finishes in 0.77 to 0.82 s against a floor of 0.6 s. The macOS
  runners (1.5 to 2.8 s) are slower and noisier at timed sleeps; read them as a rough bound.
- **Noise:** shared CI machines vary from run to run. These are orders of magnitude, not guarantees.

Comparison with aiostream on one machine (CPython 3.12, Windows, 2,000 items of 20 KB, two stages):
process memory after the run 19.5 MB for stagepipe against 25.1 MB for aiostream, on a bare Python
of 16.8 MB. See `bench/memory.py` and the README.

*Generated 2026-10-08 from the CI artifacts of the 0.0.3 release.*
