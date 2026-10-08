# stagepipe

[![PyPI](https://img.shields.io/pypi/v/stagepipe)](https://pypi.org/project/stagepipe/)
[![CI](https://github.com/rafaelborja/stagepipe/actions/workflows/ci.yml/badge.svg)](https://github.com/rafaelborja/stagepipe/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20to%203.14-blue)](https://github.com/rafaelborja/stagepipe/actions/workflows/ci.yml)
[![License](https://img.shields.io/pypi/l/stagepipe)](https://github.com/rafaelborja/stagepipe/blob/main/LICENSE)
[![Downloads](https://img.shields.io/pypi/dm/stagepipe)](https://pypistats.org/packages/stagepipe)

**Run your slow, blocking steps in parallel, and let every item move on the moment the next step is free.**

A tiny pipeline for plain Python: no event loop, no server, no dependencies, about 130 lines of
standard library. You say how many workers each stage gets; stagepipe keeps them all busy.

```python
from stagepipe import Stage, run

pages = run(files, [
    Stage("render", render_page, workers=4),               # fast, CPU
    Stage("ocr",    read_text,   workers=3),               # medium, native code
    Stage("llm",    ask_model,   workers=8),               # slow, waiting on the network
    Stage("save",   write_out,   workers=1, ordered=True), # must see pages in order
], max_in_flight=12)
```

Page 1 can be saving while page 9 is still being rendered. Nobody waits for the slowest page,
and memory stays bounded.

---

## Why stagepipe

**Lightweight.** One file, standard library only (`threading` and `queue`). Nothing to run,
nothing to configure, nothing written to disk. It lives inside your process and disappears
when the call returns. It adds about **2 MB** to a bare Python process, and the machinery itself
costs roughly **10 microseconds per item per stage** (around 100,000 items per second through a
single no-op stage; the figure is noisy, between 7 and 50 us on a busy desktop).

**Fast, where it counts.** Real pipelines are limited by the slowest stage, not by the framework.
stagepipe makes sure the slow stage is never starved and the fast stages never sit idle waiting
for it. Same workload, three ways (40 items through stages of 0.02 s, 0.30 s and 0.05 s):

| Approach | Total time | First result after |
|---|---|---|
| One item at a time | 14.8 s | 0.4 s |
| Parallel, but stage by stage (wait for all items before the next stage) | 3.7 s | 1.7 s |
| **stagepipe** | **2.3 s** | **0.4 s** |

The 2.3 s is close to the floor for this workload: the last stage has one worker and 40 items at
0.05 s each, which alone takes 2.0 s. (Measured on Windows 11, CPython 3.12.10 and 3.15.0rc2;
the script is `bench/` in the repo.)

**Reliable by being small.** Few moving parts, no global state, every run is independent
(you can run several at once). Shutdown, errors and cancel paths are covered by a stress suite
that runs randomized pipelines (random sizes, worker counts and window sizes), failures in the
first and last stage, bad configuration, and checks that no threads are left behind. Known gaps
are listed honestly [below](#status-and-known-limits).

**Built for small memory.** The target is serverless functions, containers with a few hundred MB,
and small devices. That is why there is no broker, no database and no event loop: nothing to
start, nothing resident. `max_in_flight` caps how many items exist at once, so a fast first stage
cannot render 500 page images while a slow stage is still on page 3. Measured in a fresh process
(CPython 3.12, Windows), 2,000 items of 20 KB each through two stages:

| | Process memory after the run | Python heap peak |
|---|---|---|
| bare Python | 16.8 MB | |
| **stagepipe** | **19.5 MB** (+2.7) | **2.3 MB** |
| aiostream (`task_limit=2`) | 25.1 MB (+8.3) | 4.8 MB |

aiostream is excellent and well maintained, and most of that difference is simply `asyncio`
being loaded. If you already run an event loop it costs you nothing extra. If you do not, and
memory is tight, a thread-and-queue design is the lighter tool. Reproduce with `bench/memory.py`.

Continuous integration repeats the footprint measurement on Linux, Windows and macOS for every
Python from 3.10 to 3.14 (see `bench/footprint.py` and the run summaries). Across those 15
combinations, importing stagepipe cost between 1.6 and 2.6 MB of resident memory and the
machinery cost between 2 and 17 microseconds per item per stage.

**Seconds, not nanoseconds.** stagepipe is for pipelines whose steps take milliseconds to minutes
(an OCR page, an API call, a transcode), where running in parallel saves *seconds*. Ten
microseconds of bookkeeping per item disappears next to that. So whenever speed and footprint
pull in different directions, footprint wins: we are happy to spend a few milliseconds to save
kilobytes.

**Where today's ~2 MB goes.** Measured by importing each module alone in a fresh process
(the numbers overlap, because these modules share dependencies, so they do not add up):

| Import | Extra resident memory | Why it is here |
|---|---|---|
| `dataclasses` | about 1.7 MB | Pulls in `inspect`, `re` and more, just to generate `__init__` for the small `Stage` class. |
| `typing` | about 0.7 MB | Only for type hints, which are never evaluated at run time. |
| `queue` | about 0.4 MB | The thread-safe queue; the C implementation underneath is much smaller. |
| stagepipe's own code | close to 0 | |

Neither `dataclasses` nor `typing` is needed to run a pipeline. They are conveniences, and
removing them is the first item on the [roadmap](#roadmap). A prototype already brings the import
cost from about 2.4 MB to about 0.2 MB: `Stage` becomes a few lines of plain Python, the type
hints are only read by type checkers, and `threading` plus the C queue load on the first `run()`
instead of at import. The same prototype also feeds items lazily and can drop results as they
are consumed, so memory stays flat however many items go through.

---

## The idea in one picture

Most hand-rolled parallel code processes **stage by stage**: do all the rendering, then all the
OCR, then all the saving. The slowest stage sets the pace for everyone, and nothing finishes
until nearly the end.

![Timeline: stage by stage finishes at t=14 with the first result at t=9; stagepipe finishes at t=10 with the first result at t=5](https://raw.githubusercontent.com/rafaelborja/stagepipe/main/docs/pipeline.svg)

Same six items, same workers, same stage durations. The only difference is that stagepipe does
not hold an item back to wait for its siblings.

With stagepipe each stage has its own pool of workers and its own queue. A finished item goes
straight onto the next stage's queue.

---

## Quick start

```bash
pip install stagepipe
```

```python
import time
from stagepipe import Stage, run

def download(url, i):          # slow, waits on the network
    time.sleep(0.3)
    return f"<html for {url}>"

def parse(html, i):            # fast
    return html.upper()

def store(doc, i):             # needs the original order
    print("stored", i, doc)
    return doc

urls = [f"https://example.org/{n}" for n in range(10)]

docs = run(urls, [
    Stage("download", download, workers=5),
    Stage("parse",    parse,    workers=2),
    Stage("store",    store,    workers=1, ordered=True),
], max_in_flight=8)
```

- Each stage function is called as `fn(value, index)` and returns the value for the next stage.
- `run()` returns the final values **in input order**.
- Ten downloads that would take 3 s one by one take about 0.6 s here.

### The knobs

| Option | What it does |
|---|---|
| `Stage(name, fn, workers=N)` | N threads work on this stage in parallel. Give slow stages more. |
| `ordered=True` | The stage sees items in index order (needs `workers=1`). Use it when a stage carries state across items. Earlier stages still run out of order. |
| `max_in_flight=M` | At most M items are being processed at any moment. Bounds memory and keeps stages balanced. |
| `cancelled=callable` | Checked before every call; when it returns true the run stops and raises `Cancelled`. |
| `on_done=callable` | Called with `(index, value)` as each item leaves the last stage. Good for progress bars. **It runs in a worker thread**, and concurrently if the last stage has several workers, so it must be thread-safe (or make the last stage `ordered`, which has exactly one worker). |

If any stage raises, new work stops, workers drain, and the first exception is re-raised from `run()`.

---

## Examples

Two runnable scripts in [`examples/`](https://github.com/rafaelborja/stagepipe/tree/main/examples).
Both run straight from a clone, with no install and no extra packages.

**A pizza kitchen** (`examples/pizza_kitchen.py`), the playful one. Eight orders go through dough,
toppings, two ovens and a boxing station. It prints when each pizza leaves, then a timeline of
what every station was doing, and compares with a kitchen where each station finishes the whole
batch first:

```
  dough    222223333355555...........788888....................................
  toppings .....22222233333335555555......7888888..............................
  oven     ...........111111111111113333333333333555555555555588888888888888...
  box      .........................12...........34...........56...........788.

Kitchen 1 finished in 2.02s, kitchen 2 in 3.25s: 1.6x faster, and the first pizza left
after 0.78s instead of 3.25s.
```

**Compress a folder** (`examples/compress_folder.py`), the useful one. Read, gzip and write every
file in a folder as separate stages, with `max_in_flight=6` so only six files are ever in memory,
however big the folder is. On 24 files it ran about **4x faster than one file at a time**.
Point it at your own folders with `python examples/compress_folder.py SRC OUT`.

---

## Where it fits

stagepipe is for **blocking work that spends its time waiting or in native code**, which is
exactly where threads help:

- **Document and image pipelines**: render, OCR, vision-model call, save, per page.
- **Calling APIs at scale**: fetch, transform, upload, with different limits per stage.
- **ETL on files**: read, decode, enrich, write, without loading the dataset into memory.
- **ML inference around a model**: prepare on CPU, run on one GPU worker, post-process on CPU.
- **Media and archive jobs**: transcode, hash, compress, upload.
- **Scraping**: fetch pages, parse, store, with a polite worker count at the network stage.

Python threads run native code (NumPy, onnxruntime, torch, image libraries, HTTP, subprocesses)
in parallel because those release the GIL. Pure-Python number crunching will not speed up in a
thread. Use a process pool inside a stage function for that.

### Threads, shared resources, and cancelling: what to know

These come from running stagepipe on a real five-stage document pipeline.

- **Threads only help when the work releases the GIL** (I/O, onnxruntime, PyTorch). One trap that
  was measured: PyTorch's CPU thread count is global to the process, so several threads each
  running a CPU model share a single thread pool. For CPU-bound PyTorch, separate processes
  scaled better (1.7x in that test). For a GPU, use a stage with one worker.
- **A resource that is not thread-safe** (a PDF library, a database handle) goes behind a lock
  inside the stage function, or in a stage with `workers=1`.
- **One model or session per worker thread.** Until `Stage(init=...)` exists (see the roadmap),
  build it lazily in the stage function with `threading.local()`:

  ```python
  import threading
  _mine = threading.local()

  def ocr(page, i):
      if not hasattr(_mine, "session"):
          _mine.session = load_model()      # once per worker thread
      return _mine.session.read(page)
  ```
- **Cancel, then resume.** When a run is cancelled or fails, items that were in flight are
  dropped, not committed. So let the last stage save each item atomically (write to a temporary
  file, then rename), and on the next run skip the items already saved. That makes a cancelled
  run safe to restart.
- **`on_done` runs in a worker thread**, not in the thread that called `run()`. See the table above.

### What it is not

- Not a job queue or scheduler: no broker, no workers on other machines, no persistence (yet, see the roadmap).
- Not a workflow engine with branching graphs (yet): stages form a straight line.
- Not async: there is no event loop. If your code is already `async`, see the next section.

---

## Which one should I use?

**If your code is already async, use [aiostream](https://github.com/vxgmichel/aiostream).**
"Already async" means your steps are `async def` functions that you `await`: an async HTTP client,
an async database driver, `asyncio` tasks. Your program already runs an event loop, and
aiostream chains those steps on it directly. Pushing async code through threads would be a step
backwards, and stagepipe would add nothing.

**If your steps are ordinary blocking functions, use stagepipe.** OCR engines, onnxruntime,
PyTorch, `requests`, PDF and image libraries, subprocesses: plain `def` functions you can call
and test on their own. There is no event loop to start and nothing to wrap in `async`.

Two more reasons to pick stagepipe, both about running small:

- **Small memory footprint.** No event loop and no heavy imports. Loading `asyncio` alone costs
  about 6 MB of resident memory on CPython 3.12; stagepipe is about 2 MB today and the roadmap
  takes it to a few hundred KB. That matters in serverless functions and on small devices.
- **Surviving a crash without a database** (planned for 0.2, not in 0.0.x yet). The design is
  plain files: one small file per finished item and an append-only journal, with memory use that
  does not grow with the number of items. aiostream has no persistence of any kind, so with it
  you would build that yourself.

---

## Why not something else?

Checked on Python 3.12 in a clean virtual environment, with blocking functions.

| Option | Verdict |
|---|---|
| **concurrent.futures** by hand | The right foundation, and stagepipe is a thin layer over threads and queues. Wiring several stages yourself means re-deriving queues, shutdown, error propagation and the in-flight cap in every project. |
| **[aiostream](https://github.com/vxgmichel/aiostream)** | Maintained and good for async code. For blocking functions it rejects parallelism: `stream.map(sync_fn, task_limit=4)` raises `ValueError: The 'task_limit' argument can only be used when the provided function is asynchronous`. Wrapping every stage in `asyncio.to_thread` works (8 x 0.3 s in 0.61 s) but needs an event loop and an async wrapper per function, and loading `asyncio` alone costs about 6 MB more than stagepipe's whole footprint. |
| **[pypeln](https://github.com/cgarciae/pypeln)** | The closest idea (`pl.thread.map(f, workers=4)`), but unmaintained since January 2022. On a clean Python 3.12 environment `import pypeln` fails with `No module named 'pkg_resources'` until you install `setuptools<81`, and output order is not preserved by default. |
| **Dask, Ray, Celery, Prefect, Airflow** | Excellent, and a different class of tool: schedulers, clusters and brokers. Right when you need machines and dashboards, heavy when you need a function call. |

---

## Status and known limits

**Alpha, version 0.0.2.** It was extracted from a working document-processing pipeline where it
replaces a hand-rolled look-ahead loop, and it passes its stress suite in CI on CPython 3.10 to 3.14 on Linux, Windows and macOS (and locally on the 3.15 release candidate).
In its first real use, five stages with different worker counts and at most 8 items in flight
gave output identical to the sequential loop, and cancelling then resuming worked.

Things this first version does **not** do well yet. They are tracked as
[issues](https://github.com/rafaelborja/stagepipe/issues) and are the first things on the roadmap:

- **Ctrl-C does not cancel promptly.** If the calling thread is interrupted, queued work is still
  drained before `run()` returns (measured: a 5 s job returned after 5.0 s).
- **Memory is not bounded as designed.** `run()` keeps every input until it returns, an idle
  worker keeps a reference to the last item it handled, and all results collect in one list.
  Fine for hundreds of pages; not yet for streams of large objects.
- **No failure recovery.** The first exception stops the whole run; there are no retries, and a
  rerun starts from the beginning.

---

## Roadmap

**Design rule for everything below: memory grows with `max_in_flight`, never with the number of
items, and nothing needs a database or a server.** Features that would break that rule do not ship.
Persistence, for example, will be plain files on disk (one small file per item per stage plus an
append-only journal), read back as a stream; at most one bit per item is kept in memory to
remember what is done, which is 125 KB for a million items.

The aim: stay tiny and dependable, and become the best answer for *"I have blocking code in
stages and I want it parallel, safe and observable"*, the niche that async libraries
do not cover and that the older thread-based libraries have left behind.

### Next: make the basics trustworthy (0.1)
- [ ] Ctrl-C and cancel stop promptly and cleanly.
- [ ] **Smaller footprint**, because memory is the point of this library:
  - drop `dataclasses` and `typing` at run time, and replace the little we use of them with a few
    lines of our own (about 2.4 MB less);
  - import nothing until the first `run()`; on Python 3.15 this also plays well with the new
    lazy-import flag (`-X lazy_imports`) and `__lazy_modules__`;
  - use the C queue directly instead of the `queue` module;
  - pull input lazily instead of copying it into a list, so a generator is never materialised;
  - `keep_results=False` and streaming results (`for r in stream(...)`): nothing is kept, results
    are handed over as they finish;
  - workers let go of the last item they handled as soon as they are idle;
  - optional smaller thread stacks for very small containers.
- [ ] Failure policy per run: `collect` failed items and finish the rest, `skip`, or `retry(n, backoff)`.
- [ ] Per-worker setup: `Stage(init=...)`, run once in each worker thread, e.g. one model
      session per worker. *Asked for by the first real user, who had to build it by hand with
      `threading.local()`.*
- [ ] A bottleneck report after every run: per stage, busy time against waiting time, items
      done and queue depth, so the stage to scale is obvious. *Also asked for by the first real
      user, who timed inside their stage functions.*

### Then: make it safe to rerun and easy to watch (0.2)
- [ ] **Resume after a crash**, with the lightest possible persistence: one small file per item
      per stage, written atomically, plus an append-only journal. No database. A rerun skips
      work that is already done.
- [ ] **Events for dashboards**: a stable, versioned event stream (enter/exit per stage, worker,
      duration, queue depth) written as JSON lines or sent to a callback. A dashboard can be a
      separate project that just reads it.
- [ ] Shared limits across stages, e.g. a `gpu=1` slot that three stages compete for.
- [ ] A memory budget in bytes, not only an item count.

### Later: bigger shapes (0.3 and beyond)
- [ ] One-to-many stages (a page becomes several regions) and parallel branches that join again.
- [ ] Process-backed stages for CPU-bound Python, with workers recycled after N items to contain
      leaks. (Measured in a real pipeline: CPU-bound PyTorch ran 1.7x faster in separate
      processes than in threads.)
- [ ] Adaptive concurrency for rate-limited APIs (back off on errors, speed up when healthy).
- [ ] Mixed sync and async stages.
- [ ] Verified on free-threaded Python builds.

### Python versions
Tested in CI on CPython 3.10, 3.11, 3.12, 3.13 and 3.14, on Linux, Windows and macOS, and the
release workflow only publishes if the whole matrix passes. Python 3.15 works locally (release
candidate) and joins the CI matrix and the release workflow as soon as GitHub's runners offer it.
Python 3.15's lazy imports (PEP 810) are on the list: the optional features above will load only when used, so
`import stagepipe` stays near-instant.

Want something on this list sooner, or something that is missing? Open an
[issue](https://github.com/rafaelborja/stagepipe/issues) and describe your pipeline.

---

## Development

```bash
git clone https://github.com/rafaelborja/stagepipe
cd stagepipe
pip install -e .
python tests/stress.py        # plain script, no pytest needed
python bench/bench.py         # speed numbers above
python bench/memory.py        # memory numbers above (needs psutil; aiostream optional)
python bench/footprint.py     # memory and time on your Python and OS
python examples/pizza_kitchen.py
```

## Changelog

See [CHANGELOG.md](https://github.com/rafaelborja/stagepipe/blob/main/CHANGELOG.md).

## Author

Built and maintained by [Rafael Borja](https://github.com/rafaelborja).

## License

Apache License 2.0. See [LICENSE](https://github.com/rafaelborja/stagepipe/blob/main/LICENSE).
