# Architecture proposal: stage boundaries, effects, resume, graceful stop

Status: **proposed**. Nothing here is built. The review guide with diagrams and the list of decisions is
[stage-classes.md](stage-classes.md); this note holds the full reasoning. It is the result of an independent read-only review
of the whole library and roadmap against requirements R1 to R5 in [decisions.md](decisions.md),
followed by the maintainer's decisions listed at the end. Memory stays independent of the number
of items throughout (R5).

## Verdict

R1 to R5 are mostly right, but R3 (stage kinds by nature) and R4 (effects) describe one idea twice,
and R3 puts the distinction on the wrong thing. "Memory-dependent versus recoverable" is a property
of **the value crossing a stage boundary**, not of the stage's code. A pipeline is a chain of
stages; each boundary is either **durable** (the value is saved to disk, or it is the item the caller
supplies) or **memory-only**. Every rule for resume, stop and retry follows from two facts per
stage: *is its output saved*, and *what effect does it have*.

Two clarifications of the requirements:

- **R1 means "resume from the last saved boundary".** The failed or interrupted stage is re-run from
  its saved input. Resuming in the middle of a running stage is impossible and is not promised.
- **R2 should not depend on "the nature of the next step"** but on (1) whether the item's current
  value is recoverable and (2) whether the next step has an effect. An effect stage is never started
  by a stop.

## 1. Taxonomy: one `Stage` class, two keywords

The reviewer recommends one class with two keywords instead of a class hierarchy (the maintainer
had suggested a class per kind; see "Decisions needed"). Only four combinations are legal:

```python
Stage("ocr", fn, workers=3)                              # memory, pure (today's behavior, the default)
Stage("ocr", fn, save=True)                              # durable, pure: output stored, a resume point
Stage("upload", fn, effect="idempotent")                 # durable, repeat-safe side effect (implies save)
Stage("charge", fn, effect="once", on_uncertain="fail")  # durable, not repeat-safe (implies save)
```

- **Memory stage:** output lives only in RAM. May be retried inside the run, or dropped and
  recomputed. Never an effect.
- **Durable pure stage:** output is serializable and stored; it is a resume point and is skipped on
  resume.
- **Idempotent effect:** repeating the call is harmless; the user supplies an idempotency key (item key
  plus attempt). Retried, and re-run on resume unless its done marker exists. A stop never starts it.
- **Once effect:** the library keeps a begin marker and a done marker. Never retried or re-run
  automatically.
- **Illegal:** an effect stage whose output cannot be stored when checkpointing is on (detected at the
  first write).
- **Boundary 0 (the source item) is always durable**, because the caller supplies it again on a
  restart. This assumes the input iterable is re-iterable and gives the same items; document it. A
  stage that "reads a file by path" therefore needs nothing stored.
- **Run-start validation:** an `effect="once"` stage must be the first stage or follow a `save` stage,
  because a destructive step makes its upstream stages unrepeatable (read the file, delete it, crash,
  rerun: the read now fails).

How this maps to the maintainer's effect classes (see R4): *pure or read-only* = memory or durable
pure; *writes to working files* = pure or idempotent (private, regenerable, safe to redo);
*writes to target outputs* = idempotent if atomic (temporary file plus rename, upsert), else needs a
done marker; *destructive or irreversible* = `once`, or an idempotent delete where "already gone"
counts as success.

**Not to be built:** a class per kind, a purity detector, a compensating-action (undo) framework,
exactly-once semantics, resume inside a stage, tying process stages to this taxonomy now.

## 2. Per-stage resume

- **Persisted:** the output of each `save` stage, as `<dir>/<shard>/<key>.s<k>`. Never the input (it is
  the previous output or the source) and never a bare marker, because a skipped stage must hand its
  successor a value. `once` effect stages also keep `.b<k>` (begin marker). Every file is written to a
  unique temporary name and renamed with `os.replace`, and stores the key and an optional
  `fingerprint(item)` (off by default).
- **Lookup without an index:** when the feeder pulls an item and computes its key, it probes `.s<k>` from
  the last save stage down to the first and takes the first hit: one `stat` per save stage per fed item,
  no directory listing. The payload is read into the window slot and enters the queue of stage `k+1`
  with its index. A hit on the last stage is a restored result.
- **Cleanup bounds the disk:** after writing `.s<k>`, delete `.s<j>` for `j<k`. Intermediate files are
  then bounded by the number of items in flight. A crash between the write and the delete leaves two
  files; the lookup takes the highest. Final `.s<last>` files stay (they are the finished work) unless
  `checkpoint_keep="none"` is chosen for a clean run.
- **Never saved:** `Failed` items, `on_error` substitutes, attempts that have not finally succeeded.
- **Duplicate keys:** rejected inside the window only; a collision across the input is detected through
  the key stored in the file.
- **Ordered stages:** a restored item that bypasses an ordered stage must send a gap token `(i, SKIP)` to
  it, or its counter stalls forever. Cross-item state is not rebuilt (document it; `replay=True` later
  or never).
- **Corrupt or truncated file** is a miss: redo.
- **When per-stage is worth it:** a stage that costs real time or money and whose output is smaller than
  or comparable to the cost of redoing it (a billed call, minutes of OCR). Wasteful for cheap stages or
  page-image-sized outputs. It is opt-in per stage, so the default has zero disk cost. "Final result
  only" is the special case `save=True` on the last stage: one mechanism covers both.

## 3. Graceful stop

```python
stop = threading.Event()                         # the caller sets it, for example from a SIGTERM handler
run(items, stages, stop=stop, grace=30.0)        # grace replaces the hard-coded 30 s (issue #11)
```

The library does not touch signals. A hard stop stays `cancelled()` or Ctrl-C. Rules, with
checkpointing on:

1. The feeder stops pulling immediately; the count of fed items is final.
2. An item waiting in a queue or buffer whose value is **recoverable** (the source item, or the output
   of a saved stage) is retired without running. Retiring releases both the `finished` and the `slots`
   accounting exactly once.
3. An item whose value is **memory-only** continues through pure stages, and only as far as the next
   durable boundary (a saved output or the end). That is the **draining** case. It is bounded by
   `grace`; afterwards the item is retired and will be recomputed later.
4. **No effect stage is started after a stop.** If the next stage is an effect and the value is
   memory-only, the item is retired. An effect call already running completes, and its marker and
   output are written.
5. With checkpointing off nothing is durable, so every in-flight item runs to the end: the classic
   "finish what is in flight, start nothing new".
6. An ordered stage processes its buffer only in sequence and retires everything behind a gap, which
   prevents a deadlock.

**Result:** like `partial=True`: a list of length `fed` with `UNFINISHED` for retired items (or `None`
with `keep_results=False`), and **no exception**: a stop is a requested outcome, not an error. New
fields: `stats.stopped`, `stats.fed`, `stats.stragglers`. Close hooks run the same way for stop,
cancel and Ctrl-C. Saves in progress finish because they are atomic. After `grace` the run falls back
to abort semantics and reports stragglers. A blocking input generator cannot be interrupted by a stop
(document it).

## 4. Effects: what the library may do automatically

| Class | Auto retry in run | Re-run on resume | Skip on resume | Started during a stop | User must provide |
|---|---|---|---|---|---|
| memory / pure | yes | yes (recompute) | no | yes, only to reach a durable boundary | nothing |
| durable pure | yes | only if no `.s<k>` | yes, if `.s<k>` exists | same as pure | serializable output |
| idempotent | yes | yes, if no done file | yes, if done | never | idempotency key from key plus attempt |
| once | no (opt-in `retry_on` for failures before the send) | never automatically | yes, if done | never | verify hook, or an `on_uncertain` choice |

For `once`: write the begin marker, make the call, write the output and the done marker. Begin
present without done means the outcome is unknown; the default `on_uncertain="fail"` yields
`Failed(stage, index, Uncertain)`, the alternatives are `"rerun"` and `"skip"`, plus an optional
`verify(key) -> output or None`. A **delete** is classified `idempotent` (treat `FileNotFoundError` as
success), or `once` only if it is irreversible and unverifiable. In both cases its predecessor must
be a `save` stage or the first stage.

Everything is opt-in: `retries` defaults to 0, only `save=True` stages are checkpointed, and a stage
that declares nothing behaves exactly as today. Retrying a stage that does something destructive is
the user's explicit choice, so document it loudly.

## 5. Roadmap critique and phases

Problems with the current roadmap:

- `retry(n, backoff)` as an `on_error` policy is superseded by `Stage(retries=...)` plus `current()`;
  merge it with key and attempt access. Prefer a thread-local `current()` over a fourth positional
  argument (the third is already taken by `init`).
- "Resume" is one 0.2 item; split it into mechanism (save and lookup), effects, and ordering.
- Graceful stop (R2) and issue #11 are missing from the roadmap.
- The limiter (#6) is independent and tiny: it can ship any time and should not block 0.2.
- Dashboard events should be one optional callback shared with `Stats`, not a separate JSON-lines
  subsystem.
- A memory budget in bytes needs a size callback and sizing is unreliable: demote or cut it.
- Fan-out breaks index and key assumptions, so it waits until keys are settled.
- Process stages wait for the picklability contract defined here.
- The free-threaded CI job (`3.14t`) is cheap and should go first.

Proposed phases:

- **0.1.2:** release #10 (`close`); add `stop=`, `grace` and `stats.stragglers` (#11). No disk and no
  taxonomy, only the retire accounting and a tri-state flag. It tests the hardest concurrency change
  (retiring items from queues) without persistence involved.
- **0.2:** `Stage(retries, backoff, retry_on)`, `current()`, `save=True`, `checkpoint=` / `key=`, the
  restored count in `Stats`, ordered gap tokens. Effect classes `pure` and `idempotent` only.
- **0.3:** `effect="once"` with begin/done markers and `on_uncertain`, last because misuse costs real
  money and by then the persistence layer is proven in the field.
- **Any time:** `limiter` (#6), the `3.14t` CI job.
- **Later:** the events callback, process stages, fan-out.

## 6. Risks and tests

- **Retire accounting:** an item released twice raises `ValueError` from `BoundedSemaphore`; one never
  released hangs. Randomized stop times in `tests/stress.py`, asserting that `finished` and `slots`
  balance.
- **Ordered-stage deadlock** when an index is dropped: stop while an ordered buffer holds a gap.
- **Double effect:** a kill between the effect and its marker. Subprocess kill with a fault-injection
  hook before and after the marker; the `once` stage must yield `Uncertain`, not a rerun.
- **Partial writes:** ignore stale temporary files, treat a truncated payload as a miss, unique
  temporary names per thread.
- **Stragglers:** a call finishing after `run()` returns still writes atomically (test with `grace=0.1`).
- **Memory growth:** `tracemalloc` over 100,000 items with checkpointing on; no per-item sets or maps,
  only a key set inside the window; a flat directory with a million files needs shard folders.
- **Stale resume after the input changes:** a test with `fingerprint`.
- **Resume correctness:** Failed items and substitutes are not saved, the highest stage wins when two
  files exist, restored items appear in index order and are counted.

## 7. What the current `pipeline.py` shows to be wrong or missing

- One `abort` event conflates error, cancel, Ctrl-C and stop; it needs a state (run, stop, abort).
- The accounting assumes every fed item passes through `advance`. After an abort, `work` silently drops
  items and only the main loop's early break saves it. Restored and retired items never reach a worker,
  so the feeder or the retire path must release `finished` and `slots` itself.
- Completion is recorded in `advance` after `slots.release()`. A save must happen before the slot is
  released (backpressure) and before `on_done`. A substitute from an `on_error` callable cannot be told
  from a real result, so `_call` must flag it.
- The queue tuple `(i, value, t)` carries no key and no attempt; add the key, keep the attempt local.
- Workers are daemon threads with a 30 s join. That conflicts with "Python exits cleanly": a daemon
  straggler can die in the middle of an effect or a write at interpreter exit. After a stop, effect and
  save calls must be allowed to finish, with `grace` as the bound.
- `for value in items` blocks inside the input generator, so neither stop nor cancel can interrupt it.
- `partial` handles only `Cancelled`; a stop should reuse the same return path.

## Addendum: second report of the same reviewer (after the working-file refinement)

The reviewer was given the maintainer's extra point (writing a *working* file differs from writing a
*target* file and from deleting one) and produced a revised report. It keeps the core idea (one
`Stage` class, boundaries durable or memory-only, resume from the last saved boundary) and **changes
the details below**. Where the two reports differ, this addendum is the more recent.

### Effect vocabulary now matches the maintainer's four classes

```python
Stage(name, fn, workers=1, ordered=False, init=None, close=None,
      effect=None,            # "pure" | "scratch" | "target" | "destructive"
      save=False,             # persist this stage's output (needs checkpoint=)
      retries=0, backoff=0.5, retry_on=(Exception,),
      idempotent=False,       # target only: the user asserts the write is safe to repeat
      done=None)              # destructive/target: done(value, ctx) -> bool precondition check
```

| Class | Retry | Re-run on resume | Skip on resume | Drop at stop | Library owns |
|---|---|---|---|---|---|
| pure | yes | yes | if output saved | yes | nothing |
| scratch (working files) | yes (overwrite) | yes | if output saved | yes | a per-item scratch path and its cleanup |
| target, `idempotent=True` | yes | yes | if output saved | yes | nothing |
| target, otherwise | no | only after a done-marker check | if a done marker exists | no, finish it | a `.done` marker written after the call returns |
| destructive | never | never blindly | if a done marker exists or `done()` says so | no, finish it | a `.start` marker before and a `.done` marker after |

For destructive stages a `.start` without a `.done` means the outcome is unknown: the library calls
`done(value, ctx)` if given (true = skip as success, false = run); with no `done` the item becomes
`Failed` (indeterminate) and is not re-run. "Already deleted counts as success" is written by the user
in `done` or inside the function; the library does not swallow `FileNotFoundError`. There is always a
window between the effect and the marker, so the guarantee is at-least-once unless `done` is supplied.
No class per kind, no decorator form, no compensation framework.

### Declaration and default: refuse and ask

- Declare with the `effect=` argument only (a constant per stage, zero cost per item).
- If `retries`, `checkpoint=` or `stop=` is on and a stage is undeclared, `run()` raises `ValueError`
  at start naming the stage ("declare effect="). Treating undeclared as pure could double-send
  messages; treating it as destructive would make every checkpoint run unusable.
- Validate at start: `retries>0` with `effect="destructive"` is an error; `retries>0` with
  `effect="target"` and `idempotent=False` is an error.
- *Note from the reconciliation:* requiring a declaration for `stop=` alone is probably too strict.
  Without a checkpoint, a stop is just "finish what is in flight, start nothing new" and cannot cause a
  double effect. Proposal: require the declaration only when `retries` or `checkpoint=` is on.

### Per-stage persistence layout

`dir/<stage>/<key>` (temporary file then `os.replace`), plus `dir/VERSION` and a lock file so two runs
cannot share a directory. The last stage is saved by default when `checkpoint=` is set. Lookup probes
saved stages from last to first (at most S stat calls). A rolling cleanup deletes the previous saved
output for a key when a newer one is written. Markers only for target and destructive stages that do
not save. Failed and `on_error` substitutes are never saved. Ordered stages get a tombstone `(i, SKIP)`
for every index an item bypasses.

### Graceful stop, revised

`run(..., stop=event, drain=None, abort_wait=30.0)`:

- `stop` is anything with `is_set()`; the library never installs signal handlers.
- `drain` is `None` (automatic), `True` (let everything in flight finish) or `False` (drop everything
  not currently running). This gives both variants the maintainer asked for.
- Automatic rule, computed once: `hold[k]` is true if some stage `j<=k` is target or destructive and no
  saved stage lies after it. In words: if a side effect happened and its result exists only in memory,
  the item keeps going until its data is durable or it leaves the last stage. Everything else is dropped
  at the next boundary. A call already running always finishes.
- A worker that dequeues from queue `k` drops the item unless `hold[k-1]` or `drain=True`. Dropped
  items are not errors and write nothing, but they **must be counted as settled** (release both the
  `finished` and `slots` accounting).
- An ordered stage processes only the contiguous prefix it already has and discards its buffer.
- The result **raises `Stopped`**, or with `partial=True` returns the results with `UNFINISHED`, exactly
  like cancel. (The first report returned normally; this is the difference to settle, see below.)
- Cancel, Ctrl-C and errors stay "abort": discard everything now, bounded join.

### Phases, revised (persistence moves later)

- **0.1.2:** release #10 (`close`), `abort_wait` and the straggler report (#11), `stagepipe.current()`
  (key, index, attempt). Small, low risk.
- **0.2.0:** `effect=` / `idempotent=`, in-place retries, `stop=` / `drain`, plus `limiter=` (#6).
  **No disk.** Stop is the riskiest concurrency change and the stress suite can attack it; shipping it
  before persistence also fixes the vocabulary first.
- **0.3.0:** checkpoint with the per-stage layout, markers, scratch directory, tombstones. The on-disk
  format lasts, so it should come after the declarations are settled. This means persistence moves from
  0.2 (as in the README roadmap) to 0.3. If 0.2 is insisted on, ship `save` on the last stage only, in
  the per-stage layout, and refuse destructive stages with a checkpoint until markers exist.
- **Later:** events (folded into `Stats`), process and interpreter stages, replay for ordered stages,
  streaming iterator (a `break` equals a stop).
- **Cut from the roadmap:** adaptive concurrency, mixed sync and async stages; the in-bytes memory
  budget (sizing is unreliable).

### Missing pieces it lists, in addition to the first report

Closing the input iterator on abort (generator resources leak today); a dequeue-time check for stop
(`cancelled()` is checked only before a stage call); a state machine (running, stopping, aborted)
instead of one abort event; key and attempt access; the directory lock and version stamp; a cleanup
option (`keep=`); and a per-item scratch path for `scratch` stages.

### Where the two reports disagree, and the reconciliation

| Topic | First report | Second report | Leaning |
|---|---|---|---|
| Effect vocabulary | pure / durable pure / idempotent / once | pure / scratch / target / destructive plus `idempotent=`, `done=` | Second: it matches the maintainer's own classes |
| Undeclared stage | behaves as today | refuse and ask when retries, checkpoint or stop is on | Refuse and ask, but only for retries or checkpoint |
| Stop result | returns normally with `UNFINISHED` | raises `Stopped`, or returns partial with `partial=True` | Second: consistent with cancel |
| Drain control | automatic only | automatic plus `drain=True/False` | Second: the maintainer wants both variants |
| Persistence release | 0.2 | 0.3 (vocabulary and stop first) | Second, unless the maintainer needs persistence sooner |
| Directory safety | not mentioned | VERSION stamp and lock file | Second |

## Decisions needed from the maintainer

1. **One `Stage` class with `save=` and `effect=` keywords** (both reports recommend it), versus a
   separate class per kind as first suggested. A class per kind multiplies the combinations of retry,
   resume and stop; helper constructors (for example `Stage.once(...)`) can be added later as sugar.
2. Resume means "re-run the failed stage from its last saved boundary", not "resume inside a stage". **Agreed**, with custom recovery logic added (see stage-classes.md).
3. The stop semantics (rules 1 to 6) and the API `stop=Event`, `grace=`.
4. The phases: 0.1.2 `close` + stop/grace, 0.2 retry + save + checkpoint, 0.3 `once` effects; limiter
   and the `3.14t` job any time.
5. Cut the in-bytes memory budget; make dashboard events a callback shared with `Stats`; fan-out and
   process stages later.
