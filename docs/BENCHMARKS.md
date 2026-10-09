# Benchmarks: memory and speed

Measured by the project's own CI on GitHub-hosted runners for **stagepipe 0.1.0**, on every Python
from 3.10 to 3.14 and on Linux, Windows and macOS (15 combinations). The raw run, with all
artifacts, is [CI run 37863058441](https://github.com/rafaelborja/stagepipe/actions/runs/37863058441).
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
| Darwin | 3.10.11 | 48 | 0.5 | 656 | 6.0 | 6.6 | 1.69 | 0.6 |
| Darwin | 3.11.9 | 32 | 0.5 | 784 | 3.3 | 6.6 | 1.53 | 0.6 |
| Darwin | 3.12.10 | 16 | 1.4 | 624 | 4.1 | 6.1 | 1.5 | 0.6 |
| Darwin | 3.13.15 | 32 | 2.1 | 448 | 2.6 | 3.5 | 1.93 | 0.6 |
| Darwin | 3.14.7 | 96 | 0.6 | 400 | 4.3 | 6.6 | 1.57 | 0.6 |
| Linux | 3.10.22 | 16 | 0.6 | 580 | 6.6 | 9.7 | 0.78 | 0.6 |
| Linux | 3.11.17 | 44 | 0.6 | 660 | 5.1 | 9.0 | 0.78 | 0.6 |
| Linux | 3.12.15 | 48 | 0.5 | 652 | 3.4 | 5.2 | 0.77 | 0.6 |
| Linux | 3.13.16 | 64 | 0.8 | 644 | 4.8 | 7.7 | 0.78 | 0.6 |
| Linux | 3.14.8 | 44 | 0.5 | 640 | 2.8 | 4.5 | 0.78 | 0.6 |
| Windows | 3.10.11 | 52 | 1.5 | 508 | 6.4 | 9.8 | 0.82 | 0.6 |
| Windows | 3.11.9 | 48 | 1.1 | 536 | 5.5 | 8.5 | 0.78 | 0.6 |
| Windows | 3.12.10 | 32 | 1.4 | 340 | 5.1 | 8.1 | 0.78 | 0.6 |
| Windows | 3.13.15 | 68 | 1.1 | 652 | 5.5 | 8.8 | 0.78 | 0.6 |
| Windows | 3.14.7 | 68 | 1.3 | 504 | 5.0 | 8.4 | 0.78 | 0.6 |

Memory is resident-set growth in a fresh process (psutil on Windows, `resource` peak elsewhere, so macOS and Linux figures are peaks and can read higher). Timings come from shared CI runners and are noisy; read them as orders of magnitude.

## How to read them

- **Memory:** 16 to 96 KB to import and 0.3 to 0.8 MB after a run. (Version 0.0.3 measured 1.6 to 2.6 MB
  to import and 1.9 to 3.0 MB after a run: 0.1.0 dropped `dataclasses`, `typing` and the `queue`
  module, and imports `threading` on the first run.)
- **Import time:** 0.5 to 2.1 ms (0.0.3: 8 to 21 ms).
- **Overhead:** 2.6 to 6.6 microseconds per item for a one-stage run and 3.5 to 9.8 for three stages
  (the figure is for the whole run, per item), irrelevant next to steps that take milliseconds to
  minutes. stagepipe is not built to move nanoseconds.
- **Mixed workload:** on Linux and Windows it finishes in 0.77 to 0.82 s against a floor of 0.6 s. The macOS
  runners (1.5 to 1.9 s) are slower and noisier at timed sleeps; read them as a rough bound.
- **Noise:** shared CI machines vary from run to run. These are orders of magnitude, not guarantees.

Whole-process memory on one machine (CPython 3.12, Windows, 2,000 items of 20 KB through two stages):
16.8 MB for Python alone and 17.0 MB with a stagepipe run. See `bench/memory.py` and the README for
the picture.

*Generated 2026-10-08 from the CI artifacts of the 0.1.0 branch.*
