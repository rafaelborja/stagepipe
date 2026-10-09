# Principles

Status: **accepted**. A new feature that breaks one of these does not ship, or ships behind an opt-in
that costs nothing when it is off.

1. **Memory first.** The target is serverless functions, small containers and small devices.
   Memory grows with `max_in_flight`, never with the number of items. `import stagepipe` loads
   nothing heavy (no `typing`, `dataclasses`, `inspect`, `re`, `asyncio`); `threading` and the C
   queue load on the first `run()`. Measured: 16 to 96 KB at import (0.1.0).
2. **Seconds, not nanoseconds.** stagepipe is for steps that take milliseconds to minutes, where
   parallelism saves seconds. When speed and footprint pull apart, footprint wins: we will spend a
   few milliseconds to save kilobytes.
3. **No database, no server, standard library only.** Persistence, when it exists, is plain files
   written atomically, as light as possible. The layout is open (a file per item, per-stage
   checkpoints, a small journal); the fixed rules are: light, atomic, no database, and no index of
   all items held in memory.
4. **Default behavior never changes.** Every new capability is opt-in. Code written for an older
   version keeps working (0.1.0 was verified as a drop-in upgrade by two production users).
5. **One code path for every supported Python.** Use feature detection (`try: import ...`), not
   version checks. The CI matrix (3.10 to 3.14 on Linux, Windows, macOS) tests that path; 3.15 joins
   when GitHub runners offer it. Newer features (free-threaded builds, interpreter pools,
   `os.process_cpu_count()`) are optional and imported only when used.
6. **A narrow tool, honestly described.** stagepipe chains blocking functions through worker pools
   and bounded queues. It does not try to be a stream-operator library or a scheduler. Other
   libraries (for example aiostream for async code) are described neutrally: different
   philosophy, not a replacement. Never frame another library as something to beat.
7. **Measure before claiming.** Every number in the README comes from a script in the repo
   (`bench/`) or from CI. Verify examples and snippets by running them before publishing.
8. **Releases are deliberate.** A PyPI upload cannot be undone, so it needs the maintainer's
   go-ahead. Build from a clean clone (`core.autocrlf=false`), CI must pass on the whole matrix, and
   the publish workflow only runs through the `pypi` environment.
