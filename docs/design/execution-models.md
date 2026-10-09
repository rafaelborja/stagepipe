# Execution models: who runs the work

Status: threads are **shipped**. Process-backed and interpreter-backed stages are **planned**
(roadmap, "Later"); this note records what we know so far.

## The three models

| Model | Where the work runs | Who manages it |
|---|---|---|
| **Threads** (today) | The stage function runs in the library's threads inside one process. If it calls an external program (for example tesseract through `subprocess`), that program is a **separate OS process started by the stage function**. If it uses a native library (onnxruntime and similar), the work is native code inside the process with the GIL released. | The library manages only the threads. A subprocess started by the stage function belongs to the function. |
| **Process-backed stages** (planned) | Worker processes started by the library run the stage function. | The library owns them: it can kill a stuck worker and recycle a leaky one. |
| **Interpreter-backed stages** (planned, Python 3.14+) | Isolated Python interpreters inside one process, each with its own GIL (`concurrent.futures.InterpreterPoolExecutor`). | The library owns them, but they share the process: a native crash takes everything down and nothing can be killed. |

Why it matters:

- **Copies.** Process and interpreter stages pass values by pickling: one copy per item in flight.
- **Models.** Each worker loads its own models, so a model of a few hundred MB multiplies by the
  worker count. Threads can share one model (or use one per thread through `Stage(init=...)`).
- **Crashes and hangs.** Only processes give isolation and the ability to kill a worker.
- **C extensions.** Some libraries still do not load inside sub-interpreters. Not yet checked here
  for numpy, onnxruntime or PIL.
- **Function and values.** For process and interpreter stages the function must be importable by
  name and values must pickle (see [restartability.md](restartability.md)).

For OCR and similar work, **threads stay the right default**: the work is native code or an
external process, so the GIL is not the bottleneck. Process and interpreter stages are for CPU-bound
**pure-Python** stages. The queue, ordering, backpressure and error logic stay the same for any
kind; a stage would only declare how it runs.

## Measurements

One Windows machine, Python 3.15.0rc2, private memory (USS) of the whole process tree, median of 3
runs. Treat as orders of magnitude; Linux may differ (fork can share memory between processes).

| Pool | Workers that do nothing | Workers that import stdlib modules\* |
|---|---|---|
| Threads | about 0.1 to 0.2 MB each | about 6.5 MB once, shared by all |
| Interpreters | about 7 MB each | 12.5 to 13.7 MB each |
| Processes | 10 to 11 MB each | 15 to 16 MB each |

\*ssl, decimal, sqlite3, xml, email, http, unittest.

- Interpreters cost about 30 to 40% less than processes per worker, and about 50 times more than an
  extra thread.
- **Importing the executor classes is cheap**: about 3.7 MB for `concurrent.futures` plus the
  interpreter executor (same as the thread executor), 5.1 MB for the process executor.
  `import concurrent.futures` alone is about 3.5 MB and 40 ms; with `-X lazy_imports=all`
  (Python 3.15, PEP 810) it is about 8 KB until a class is touched.
- **Lazy imports help inside workers**: four interpreters whose code imports 7 modules but uses one
  took 57.6 MB, and 41.3 MB with `-X lazy_imports=all` (about 4 MB saved per interpreter).
  Processes benefit the same way. Worth documenting for users of these stage kinds.

## Lazy imports and the "light device" question

stagepipe is already ready: `import stagepipe` imports nothing heavy. Any executor module would be
imported inside the branch of the code that needs it, so a small device that only uses threads pays
nothing for the other kinds. No separate light build or flag is needed.

## Free-threaded Python (3.13t, 3.14t)

Threads run Python code in parallel there, which removes the "threads only help when the GIL is
released" caveat and may make interpreter stages unnecessary for many users. To do: a non-blocking CI
job on `3.14t`, then documentation. Shared state in `run()` is per-worker or per-index, so no
GIL-dependent assumption is expected, but it is unverified.

## Open

- Whether to offer interpreter stages at all before free-threaded builds mature (low priority).
- C-extension compatibility inside sub-interpreters (needs testing with the real libraries).
- Re-measure on Linux and in CI before putting numbers in the README main text.
