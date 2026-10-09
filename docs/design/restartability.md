# Restartability: retry and resume

Status: **proposed**. Nothing here is built. It waits for the maintainer's decision on the points in
"Decisions needed". Related issues: #3 (retry), #9 (checkpoint), #11 (30 s abort wait), #6
(limiter). Source: the maintainer's concern, reviewed independently by a design agent.

## The concern

Any retry or resume means doing a stage's work again, possibly after the process was killed. So the
work must be describable in a way that survives. Two kinds of tasks exist:

1. **Work fully described by parameters** (plain data that can be stored in a file and reloaded).
2. **Work that refers to live Python objects** (open handles, loaded models, closures, generators,
   big in-memory objects). These cannot be resumed after a restart.

Tentative idea: if recovery or retry is on, force option 1. Constraint on every solution: very light
on memory (no growth with the number of items), standard library only, no database.

## Review verdict

The concern is right, but the contract should depend on **what has to cross a boundary**, not on
"parameters versus live objects" everywhere.

| Case | What must be storable | Live objects OK? |
|---|---|---|
| Retry within a run (threads) | Nothing: the input value is still in memory. The stage function must tolerate being called twice with the same input. | Yes |
| Resume after a restart (#9) | A stable `key` per item and the **last stage's result**. The caller supplies the inputs again, the stage code is already in the restarted program, `init` is rebuilt in each worker. | Yes inside the stage; the stored result must be serializable |
| Process or interpreter stages (later) | The stage function (importable by name) and every value in and out, by pickling. | No: option 1 appears here, naturally |

So option 1 as a general rule is too strict (for retry) and misses the real requirement (for
resume: a stable key). It is the right shape only for process and interpreter stages.

## Proposed contract (three independent opt-ins, all off by default)

```python
Stage("ocr", fn, retries=2, backoff=0.5, retry_on=(OSError,))    # per stage, default retries=0
run(items, stages, checkpoint="dir/", key=lambda it: it.id,
    serializer=pickle)                                            # object with dumps/loads
Stage("ocr", "pkg.mod:fn", kind="process")                        # later; separate issue
```

**Retry**
- No serialization requirement. `retry_on` defaults to `Exception`; never retry `Cancelled` or
  `BaseException`.
- The wait is `abort.wait(delay)` so cancel and Ctrl-C interrupt it (a plain `time.sleep` would
  extend the 30 s wait of issue #11).
- Retried in place inside the call: the worker slot, the `max_in_flight` slot, the `init` state and
  the ordered stage's bookkeeping stay as they are. The attempt counter is local to the call: no
  per-item memory.
- When retries are exhausted the normal `on_error` path applies.

**Checkpoint**
- `key` is required and must be filename-safe (validate, for example `[A-Za-z0-9._-]`).
- The final result must be serializable by the serializer. Detect a violation at the **first
  write**, not by trial-pickling every item up front. The error names the key and serializer:
  `checkpoint: result for key 'x' (type Foo) cannot be pickled: ... Return plain data from the
  last stage, or pass serializer=...`.
- A write error fails the run: a silent loss of resumability is worse than a stop.
- A duplicate key cannot be detected across the whole input without an index of all keys, which
  breaks the memory rule. The second item would silently restore the first item's result.
  Mitigations: store the key (and optionally a `fingerprint(item)`) inside each file and compare on
  restore; reject duplicates inside the in-flight window.
- Write via a temporary file in the same directory, then `os.replace`; ignore `*.tmp` on restore.
  A truncated or corrupt `.done` means "redo", never a crash. Do not rely on fsync for correctness.
- The write belongs in `advance()` (where completion happens), before `on_done` and before the
  `max_in_flight` slot is released: a slow disk then throttles the items in flight, which is correct
  backpressure.

**Process or interpreter stages (later)**
- The function must be importable by dotted name or a picklable top-level function; check once at
  the start with a cheap `pickle.dumps(stage.fn)`. Values are checked on first send. `init` still
  runs in the worker.

## What to persist

| Option | Memory | Disk | A restart gets |
|---|---|---|---|
| A. Final result only, `<key>.done` (issue #9) | 0 per item (one existence check per fed item) | a file per finished item | skips finished items; redoes anything in flight |
| B. Per-stage outputs `<key>.<stage>` | 0 | N files per item, plus cleanup | resumes mid-pipeline; costs the most disk |
| C. Input parameters per item | 0 | a file per item | nothing extra: the caller re-supplies the inputs (only useful to detect a changed input via a fingerprint) |

Recommendation: A for 0.2. B later, opt-in per slow stage (`Stage(checkpoint=True)`). C is not
needed. The layout itself is open; shard folders by key hash only if a flat folder becomes slow.

## Problems this exposed in the current design

- **Ordered stages are skipped on resume.** A restored item never runs any stage, including an
  `ordered` one with cross-item state or side effects. Document it and say so in the error text
  when a checkpoint is combined with an ordered stage. Restored items must still occupy their index,
  or the ordered stage's counter stalls. (Options for later: `Stage(ordered=True, replay=True)`.)
- **`index` is not stable** if the input changes between runs. Only a caller-supplied `key` is.
- **"Done" versus "placeholder".** A value returned by an `on_error` callable is a substitute, not a
  finished result: do not checkpoint it. `Failed` items are never checkpointed (retried next run).
- **Side effects repeat.** Checkpoint gives at-least-once, not exactly-once: a kill between an
  upload and the checkpoint write repeats the upload. Document it; advise idempotency keys derived
  from the item key. Option to expose key, index and attempt to the stage function without changing
  its signature: a small `stagepipe.current()` (thread-local), or an opt-in flag on the stage.
- **Stragglers after an abort (#11).** A call still running when `run()` returns can later write a
  checkpoint or call `on_done`. A late write is atomic and complete, so harmless, but document it.
- **Restored items and `max_in_flight`.** Restored items release their slot immediately, and the
  restore check runs in the feeder thread.

## Failure modes and tests to write

1. Kill mid-run (subprocess kill), rerun: only the missing keys run.
2. A leftover `.tmp` is ignored; a truncated or corrupt `.done` triggers a redo, not a crash.
3. Duplicate keys inside the window raise; a collision across the input is documented and detectable
   through the key stored in the file.
4. An unpicklable last result gives a clear error naming the key.
5. Retry: succeeds on attempt 3; exhaustion produces `Failed` or raises per the policy; `Cancelled`
   during a backoff returns promptly; non-matching exceptions are not retried.
6. Retry with an ordered stage: every index is still seen, in order.
7. Retry with `init`: the same state object is reused.
8. Checkpoint with an ordered stage: the limitation is documented or enforced.
9. Restored items appear in index order in results and `on_done`, mixed with new ones; `Stats`
   counts `restored`.
10. Memory: a flat profile over 100,000 items with the checkpoint on (extend `tests/memory_test.py`).
11. A straggler writing after the abort (#11).
12. Process kind: a lambda is rejected at the start with a clear message.
13. A changed input between runs with the same key: a stale result; optional `fingerprint`.
14. Disk full or read-only directory: the run stops with the OS error.

## Decisions needed from the maintainer

1. Adopt **contract by boundary** (nothing for retry, key plus serializable last result for resume,
   picklable function and values for process/interpreter kinds) instead of "option 1 everywhere"?
2. Retry as a **per-stage setting** (`Stage(retries=, backoff=, retry_on=)`, in place, no
   serialization) instead of an `on_error` policy?
3. Checkpoint final result only, `key` required, at-least-once, substitutes not checkpointed,
   ordered-stage limitation documented; per-stage checkpoints and process/interpreter kinds as
   separate later issues?
4. Key and attempt access for stage functions: `stagepipe.current()` or an opt-in argument?

Already agreed elsewhere (issue #9): restored items go through `on_done` and the results in index
order together with the new ones; `Stats` counts `restored`; layout is open.
