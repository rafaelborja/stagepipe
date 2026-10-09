# Design notes

Working notes on how stagepipe is designed and what is still being decided. They exist so that
nothing we have considered is lost between conversations and releases. They are not user
documentation: the [README](../../README.md) is. Each note says what is **accepted**, what is
**proposed** (waiting for the maintainer's decision) and what is **open**.

| Note | What it covers |
|---|---|
| [principles.md](principles.md) | The rules every feature must respect: light memory first, no database, one code path. |
| [execution-models.md](execution-models.md) | Threads, worker processes and sub-interpreters: who runs the work, what it costs (measured), when each fits. |
| [restartability.md](restartability.md) | Retry and resume: what must be storable, the proposed contract, what to persist, failure modes and tests. |
| [architecture.md](architecture.md) | **Proposed** whole-system design from an independent review: stage boundaries (durable or memory-only), effect classes, per-stage resume, graceful stop, phased plan, risks. Read this after restartability. |
| [decisions.md](decisions.md) | A dated log of decisions, proposals and open questions, with the issue numbers. |

Last updated: 2026-10-09 (library version 0.1.0 released, 0.1.1 merged and not yet released; the
architecture proposal is waiting for the maintainer's decisions).
