# Per-call deadline: `Stage(deadline_s=...)`

Status: **proposed, for the maintainer's review**. Nothing here is built. Origin: the audio-transcription
service, whose router call "joins a thread with a timeout" because `httpx`'s timeout bounds each read, not
the whole call, and calls hung for up to 715 s. Related: `abort_wait` and `Stats.stragglers` (shipped in
0.2.0), `close=` (a worker's state must not be closed while a call still uses it).

## The ask

Report a call as failed when its wall-clock deadline passes, free the worker slot so the pipeline moves on,
and count the call that is still running as a straggler. A number or `fn(item)` gives the deadline.

## The hard fact

A Python thread cannot be killed. A call that has passed its deadline keeps running, using whatever it
holds (a socket, a model, an `init` state). Anything we do is a choice about **what to do with that
still-running call**, and each choice has a cost. Three honest designs:

| | Design | What it gives | What it costs |
|---|---|---|---|
| A | **Tell the function the deadline** (`current().deadline`, `current().remaining()`), no abandonment | The function can pass a real total timeout to its client. Nothing leaks, nothing is shared unsafely. | The library never frees a slot by itself; a function that ignores the deadline still hangs. |
| B | **Abandon** the call: run each call in a helper thread, the worker waits `deadline` seconds, then records a timeout and moves on | The slot and the window recover; the run goes on. | One extra thread per call (milliseconds), a leaked running thread until it returns, and the questions below. |
| C | **Cooperative cancel token** passed to the function | Clean when the function checks it | Needs every function to be written for it; same hang when it does not |

Recommendation: ship **A** first (small, safe, and it gives one deadline per item across retries), then
**B** as an explicit opt-in (`abandon=True`) with the rules below. Field report from the requesting service:
**A alone does not fix their 715 s hangs**, because their client's timeout bounds each read, not the whole
call, so a trickling response never trips it. They fixed it by running the call in its own thread, joining it
with a timeout (60 s + 1.2 x the clip length) and abandoning it, which is design B and costs a leaked thread
per abandoned call (acceptable to them). So both are needed. A is cheap and comes first; B is what removes
their case.

## Rules if B is built

1. **A timed-out call is an error** (`TimeoutError`), so retries, `recover=` and `on_error` apply as for any
   failure. A `Transient` stage may retry it. A `Permanent` stage never retries it on its own: the effect may
   have happened, so it goes to `recover=` (which sees the attempt) or fails.
2. **The result of an abandoned call is discarded**, even if it returns later. Its exception is discarded too.
3. **The shared limiter stays held until the call really returns**, because the resource is still in use.
   Releasing it at the deadline would break the cap the limiter exists to enforce.
4. **`init` state.** After an abandonment the worker must not hand the same state to its next call while the
   straggler still uses it. Options: the worker re-runs `init` for a fresh state and the old state is closed
   when the straggler returns, or the stage declares its state thread-safe. Default: fresh state.
5. **Bounded leak:** `max_abandoned` (default small, e.g. 2 x workers). Beyond it the run fails with a clear
   error instead of growing threads without limit. Memory stays bounded by `max_in_flight` plus this cap.
6. **Stats:** `Stats.abandoned` (total) and `Stats.stragglers` (still running when `run()` returned), the
   latter already shipped.
7. **At the end of a run** `run()` waits for abandoned calls only up to `abort_wait`, like any straggler.
8. **Cost:** a helper thread per call is a few hundred microseconds and a few KB while it exists. That fits
   "seconds, not nanoseconds", and it is paid only by stages that set `abandon=True`.

## Open questions

- Settled by the field report: A is not enough on its own; B is needed (see above).
- For B, is a fresh `init` state per abandonment acceptable, or do stages need a thread-safe declaration?
- Per attempt or across all retries? The requesting service wants one deadline per item across retries
  (`remaining()` shrinks across attempts) with a per-attempt cap. Proposal: both, `deadline_s` per attempt and
  `total_deadline_s` per item.

## Tests to write if built

1. A function that sleeps past the deadline: the item fails with `TimeoutError`, the next item starts on time.
2. The straggler returns later: its result is discarded, the limiter is released then, `Stats.abandoned` counts it.
3. `Permanent` stage: no automatic retry after a timeout; `recover=` is consulted.
4. `max_abandoned` exceeded: the run fails with a clear error, no thread growth beyond the cap.
5. `init` state: the next call gets a fresh state; the old one is closed after the straggler returns.
6. `remaining()` shrinks across retries per attempt; `None` without a deadline.
