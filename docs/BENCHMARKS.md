# Benchmarks: memory and speed

Measured by the project's own CI on GitHub-hosted runners for **stagepipe 0.2.0**, on every Python
from 3.10 to 3.14 and on Linux, Windows and macOS (15 combinations). The raw run, with all
artifacts, is [publish run 38621827331](https://github.com/rafaelborja/stagepipe/actions/runs/38621827331).
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
| Darwin | 3.10.11 | 96 | 0.6 | 608 | 6.0 | 8.9 | 1.52 | 0.6 |
| Darwin | 3.11.9 | 96 | 0.5 | 672 | 3.1 | 4.7 | 1.68 | 0.6 |
| Darwin | 3.12.10 | 64 | 1.8 | 512 | 5.7 | 9.2 | 1.8 | 0.6 |
| Darwin | 3.13.15 | 64 | 1.0 | 432 | 6.4 | 6.5 | 1.63 | 0.6 |
| Darwin | 3.14.7 | 160 | 0.8 | 544 | 3.7 | 4.9 | 1.82 | 0.6 |
| Linux | 3.10.22 | 40 | 0.6 | 688 | 5.4 | 8.3 | 0.77 | 0.6 |
| Linux | 3.11.17 | 124 | 0.9 | 724 | 5.5 | 8.3 | 0.78 | 0.6 |
| Linux | 3.12.15 | 72 | 0.9 | 644 | 4.8 | 7.7 | 0.78 | 0.6 |
| Linux | 3.13.16 | 116 | 1.2 | 688 | 5.9 | 8.9 | 0.78 | 0.6 |
| Linux | 3.14.8 | 92 | 0.9 | 692 | 4.7 | 7.9 | 0.78 | 0.6 |
| Windows | 3.10.11 | 72 | 0.9 | 348 | 3.4 | 5.6 | 0.83 | 0.6 |
| Windows | 3.11.9 | 84 | 1.5 | 568 | 5.9 | 9.6 | 0.78 | 0.6 |
| Windows | 3.12.10 | 80 | 0.8 | 584 | 2.7 | 4.4 | 0.78 | 0.6 |
| Windows | 3.13.15 | 56 | 1.3 | 628 | 5.7 | 9.4 | 0.78 | 0.6 |
| Windows | 3.14.8 | 128 | 1.0 | 664 | 4.0 | 6.6 | 0.78 | 0.6 |

Memory is resident-set growth in a fresh process (psutil on Windows, `resource` peak elsewhere, so macOS and Linux figures are peaks and can read higher). Timings come from shared CI runners and are noisy; read them as orders of magnitude.

## How to read them

- **Memory:** 40 to 160 KB to import and 0.35 to 0.72 MB after a run. (Version 0.1.0 measured 16 to 96 KB to
  import; 0.2.0 adds the stage kinds, retries and the stop logic. Version 0.0.3 measured 1.6 to 2.6 MB to
  import and 1.9 to 3.0 MB after a run: 0.1.0 dropped `dataclasses`, `typing` and the `queue` module, and
  `threading` is imported on the first run.)
- **Import time:** 0.5 to 1.8 ms (0.0.3: 8 to 21 ms).
- **Overhead:** 2.7 to 6.4 microseconds per item for a one-stage run and 4.4 to 9.6 for three stages
  (the figure is for the whole run, per item), irrelevant next to steps that take milliseconds to
  minutes. stagepipe is not built to move nanoseconds.
- **Mixed workload:** on Linux and Windows it finishes in 0.77 to 0.83 s against a floor of 0.6 s. The macOS
  runners (1.5 to 1.8 s) are slower and noisier at timed sleeps; read them as a rough bound.
- **Noise:** shared CI machines vary from run to run. These are orders of magnitude, not guarantees.

Whole-process memory on one machine (CPython 3.12, Windows, 2,000 items of 20 KB through two stages):
16.8 MB for Python alone and 17.0 MB with a stagepipe run. See `bench/memory.py` and the README for
the picture.

*Generated 2026-10-14 from the CI artifacts of the 0.2.0 release.*
