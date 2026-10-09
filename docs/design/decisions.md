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

## Shipped

- 0.0.1 to 0.0.3: library, README, CI matrix, footprint report, examples.
- 0.1.0 (2026-10-09): `on_error`, `Stage(init=)`, `Stats`, `partial=True`, `keep_results=False`, lazy input,
  Ctrl-C fix, light imports. Closed #1, #2, #4, #5, #7.
- 0.1.1 (merged, **not released**): `Stage(close=)` for `init` state (#10).

## Proposed (waiting for the maintainer)

- Contract by boundary, retry per stage, checkpoint final result with a required key, at-least-once
  semantics. Full text and the four decisions needed: [restartability.md](restartability.md).

## Open

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
