# Decision log

Newest topics last within each section. "Accepted" means decided by the maintainer; "Proposed"
means written down and waiting for a decision; "Open" means not decided and not yet analysed.
Issue numbers refer to https://github.com/rafaelborja/stagepipe/issues.

## Accepted

| Date | Decision | Where |
|---|---|---|
| 2026-10-08 | License Apache-2.0; public repo; the maintainer's name and GitHub profile appear in the metadata, never a personal email. | `pyproject.toml` |
| 2026-10-08 | Releases go through a gated workflow: the full CI matrix, then PyPI Trusted Publishing, then the footprint report is attached to the release. | `.github/workflows/` |
| 2026-10-08 | Python 3.15 joins CI and the release workflow only when GitHub runners offer it; until then it is checked locally. | roadmap |
| 2026-10-08 | Memory footprint is the point of the library: no heavy imports, lazy input, `keep_results=False`, bounded by `max_in_flight`. Shipped in 0.1.0. | [principles](principles.md) |
| 2026-10-08 | Never attack or "beat" other libraries (aiostream is more complete; different philosophy). No side-by-side comparison charts. | [principles](principles.md) |
| 2026-10-09 | Persistence layout is open (per-item files, per-stage files, small journal). Fixed rules: light, atomic, stdlib, no database, no index of all items in memory. | roadmap, #9 |
| 2026-10-09 | Restored items go through `on_done` and results in index order together with new items; `Stats` counts `restored`; each restored result is marked (flag or `on_restored`, to be chosen when built). | #9 |
| 2026-10-09 | Streaming results as an iterator (`for r in stream(...)`) moves to "Later", built only if users ask. `keep_results=False` plus `on_done` already gives flat memory. | roadmap |
| 2026-10-09 | Optional smaller thread stacks move to "Later". | roadmap |
| 2026-10-09 | One code path for all Python versions, feature detection instead of version checks; free-threaded, interpreter stages and `os.process_cpu_count()` are optional later items. | [principles](principles.md), [execution-models](execution-models.md) |
| 2026-10-09 | The `pypi` GitHub environment is the only one the PyPI Trusted Publisher accepts. | PyPI settings |
| 2026-10-09 | **Contract by boundary** (not "option 1 everywhere"): retry within a run needs no serialization; resume needs a stable key plus serializable stored data; process/interpreter stages need an importable function and picklable values. | [restartability](restartability.md) |
| 2026-10-09 | Stage functions must be able to reach their item's key and attempt number (mechanism undecided: `stagepipe.current()` or an opt-in argument). | [restartability](restartability.md) |

| 2026-10-09 | **Resume rule:** a restarted run continues each item from its last saved boundary; the failed or interrupted stage is re-run from its saved input. No promise to resume inside a running stage. | [stage-classes](stage-classes.md) |
| 2026-10-09 | **Custom recovery (R6):** a stage may supply recovery logic (a function, a lambda is enough) that decides on a restarted run whether the work is already done (Skip), must be redone (Rerun) or needs a human (Fail). It lifts the ban on retrying or re-running destructive stages: the library requires the logic instead of forbidding the stage, and checks that it is present when the stage is created. | [stage-classes](stage-classes.md) |
| 2026-10-09 | **Stage kinds are classes**: a base Stage (undeclared, today behaviour, no retries or checkpoint) with a subclass per kind and the behaviour (retry, resume, stop, validate) on the classes. The cost is a few extra public names, nothing per item. Names of the kinds still open. | [stage-classes](stage-classes.md) |
| 2026-10-09 | A destructive-kind stage may be re-run or retried **only if recovery logic is provided**; without it the stage is refused when it is created. | [stage-classes](stage-classes.md) |
| 2026-10-09 | **A graceful stop returns normally** with UNFINISHED for every dropped item (not an exception) and sets stats.stopped. Cancel and Ctrl-C still raise. | [stage-classes](stage-classes.md) |
| 2026-10-09 | **Drain control**: drain=None (automatic, default), True (finish everything in flight) or False (drop everything not running). | [stage-classes](stage-classes.md) |
| 2026-10-09 | **Cut from the roadmap**: adaptive concurrency, mixed sync and async stages, memory budget in bytes. | roadmap |
| 2026-10-09 | A validation tool (stagepipe.validate() and a command line check) is a later feature request: issue #13. | #13 |

## Shipped

- 0.0.1 to 0.0.3: library, README, CI matrix, footprint report, examples.
- 0.1.0 (2026-10-09): `on_error`, `Stage(init=)`, `Stats`, `partial=True`, `keep_results=False`, lazy input,
  Ctrl-C fix, light imports. Closed #1, #2, #4, #5, #7.
- 0.1.1 (merged, **not released**): `Stage(close=)` for `init` state (#10).

## Requirements stated 2026-10-09 (under architecture review)

The maintainer added these on top of the restartability proposal. They change the shape of the
design, so a whole-architecture review was requested; its result will be recorded here.

- **R1. Resume per stage, not only at the end of the pipeline.** A stage that failed or was
  interrupted must be resumable on its own; checkpointing only the last stage's result is not
  enough. (This overrides the earlier "final result only for 0.2, per-stage later" proposal.)
- **R2. Graceful shutdown.** A way to stop so that no new step or task is started and Python can
  exit cleanly. The right behaviour depends on the nature of the next step: if the next step depends
  on data held in memory, shutdown must let that next step finish too (a draining shutdown);
  otherwise it can stop at the stage boundary without waiting (a non-draining shutdown). Both
  variants are wanted.
- **R3. Different stage kinds by nature.** Memory-dependent stages (their input or state lives only
  in memory) should be a different class or method from stages whose data is on disk or
  re-creatable. Each kind defines how it is retried, resumed and shut down.
- **R4. Distinguish operations by effect.** The effect of a stage decides what the library may do
  automatically on retry, resume and suspension. At least these classes (the maintainer stressed that
  writing a *working* file is not the same as writing a *target* file or deleting a file):
  (a) pure or read-only (reads a file and passes the result on);
  (b) writes to **working/scratch files** the pipeline makes for itself: private, regenerable, safe to
  overwrite, redo or discard;
  (c) writes to **target outputs** (externally visible): safe to repeat only if atomic and idempotent
  (temporary file then rename, an upsert), otherwise needs a done marker or an idempotency key;
  (d) **destructive or irreversible** operations on sources or the outside world (delete, move,
  overwrite, send, bill): must not be retried or re-run blindly; resume checks a precondition or a
  done marker first, and "already deleted" counts as success for an idempotent delete.
  A stage needs a cheap way to declare its class, with a safe default when it does not.
- **R6. Custom resume and recovery logic.** The user can supply it (a lambda is enough); it can bypass the restriction on destructive stages by requiring that logic instead of forbidding the stage, and it is verified when the stage is created. A validation operation or command line check is a feature request.
- **R7. An optional error handler for each stage** (a default one that only logs was suggested). Design in the guide: on_error= per stage, run-wide default stays raise, plus a ready-made log handler; whether stages without a handler should log and carry on by default is decision 10.
- **R5. Always light on memory.** Every one of the above must keep memory independent of the number
  of items.

## Proposed (waiting for the maintainer)

- **The architecture proposal** ([architecture.md](architecture.md)): one `Stage` class with `save=` and
  `effect=` keywords (not a class per kind), durable versus memory-only boundaries, resume from the last
  saved boundary, `stop=Event` with `grace=` and draining semantics, effect classes
  (pure / idempotent / once), and phases 0.1.2 (stop and grace), 0.2 (retry, save, checkpoint), 0.3
  (`once` effects). Five decisions needed, listed at the end of that note.
- Retry as a per-stage setting (`Stage(retries=, backoff=, retry_on=)`), in place, no serialization,
  interruptible wait; at-least-once semantics for checkpoints. Details:
  [restartability.md](restartability.md).

## Open

- **Decisions waiting on the review guide** ([stage-classes.md](stage-classes.md), section 5): names of the kinds (decision 9: Repeatable / Scratch / Effect / Irreversible recommended), per-stage error handler default (10), the remaining details of custom recovery (2), undeclared stages (5), release order (6).

- **Reconcile the two architecture reports** ([architecture.md](architecture.md), addendum): effect vocabulary (pure / scratch / target / destructive), when an undeclared stage is refused, whether a stop raises Stopped or returns normally, drain= control, and whether persistence is 0.2 or 0.3.

- How a restored item is marked: a flag on the result, or an `on_restored` callback.
- Key and attempt access for stage functions: `stagepipe.current()` or an opt-in argument.
- `fingerprint(item)` to detect a stale checkpoint after the input changed.
- Replaying restored items through an `ordered` stage (`replay=True`) versus documenting the limit.
- Whether `Failed` and `on_error` substitutes should ever count as "done".
- Make the 30 s wait after an abort configurable, and report stragglers (#11).
- Shared limiter across runs, `Stage(limiter=...)` (#6), and named shared limits (for example a GPU slot).
- Events for dashboards: shape and versioning of the event stream.
- Process-backed and interpreter-backed stages: whether and when, C-extension compatibility, Linux
  measurements ([execution-models](execution-models.md)).
- Free-threaded CI job (`3.14t`, non-blocking).
- Stack Overflow: held answers (queue maxsize, generator pipeline, large results) can now use 0.1.0;
  drafts live outside the repository.
