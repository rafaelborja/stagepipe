# Changelog

All notable changes to stagepipe are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/) (while the version is 0.x, minor releases may change the API).

## [Unreleased]

Planned work is described in the [roadmap](README.md#roadmap). Known defects in 0.0.1 are tracked
as [issues](https://github.com/rafaelborja/stagepipe/issues).

## [0.0.1] - 2026-10-08

First public release (alpha).

### Added
- `run(items, stages, max_in_flight=, cancelled=, on_done=)`: runs items through a straight line of
  stages; each item moves on as soon as the next stage has a free worker.
- `Stage(name, fn, workers=, ordered=)`: per-stage thread pool; `ordered=True` (one worker) sees
  items in index order through a reorder buffer.
- `max_in_flight` cap on items being processed at once.
- Cancellation through a `cancelled()` callback (raises `Cancelled`); the first stage exception
  stops new work and is re-raised from `run()`.
- `on_done(index, value)` progress callback.
- Standard library only; typed (`py.typed`); Python 3.10+. Tested on CPython 3.12 and 3.15.0rc2.
- Stress suite (`tests/stress.py`), speed and memory benchmarks (`bench/`).

### Known issues
- Ctrl-C on the calling thread does not cancel promptly: queued work is drained first.
- `run()` holds all inputs, idle workers keep a reference to their last item, and results collect
  in one list, so memory is not yet bounded by `max_in_flight` alone.
- No failure recovery: the first exception aborts the run.

[Unreleased]: https://github.com/rafaelborja/stagepipe/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/rafaelborja/stagepipe/releases/tag/v0.0.1
