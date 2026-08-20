# Phase 4 JARVIS experience assessment

Status: **accepted by Marc on 2026-08-16 for the deterministic shadow slice**.

- Presence: Ophanim presents one typed `Departure` situation with a stable ID,
  cited evidence IDs, source provenance, confidence, uncertainty, and shadow
  status. It is a situation record, not a disconnected alert.
- Continuity: the event journal, delivery state, situation revision, and
  evaluation explanation survive SQLite close/reopen. Replay reconstructs the
  same normal situation without duplication.
- Initiative: meaningful change is detected after durable ingestion, but
  initiative is deliberately bounded to observation. No notification,
  approval, action, Guardian call, Codex control, or computer-use effect is
  produced.
- Competence: duplicate, late, out-of-order, stale, missing, contradictory,
  canceled, early-departure, crash/restart, retry, dead-letter, retention, and
  prompt-injection fixtures receive the expected deterministic result. The
  normal personal-event replay produces exactly one correct Departure.
- Trust: persistence precedes detector evaluation; event identity is
  idempotent; leases require the current worker; retries are bounded; raw
  sensitive data is dropped; evidence and uncertainty remain visible; and
  untrusted event text cannot create authority.

Regression/limitations: the Phase 4 work intentionally does not add a live
source adapter, live Codex work, or computer-use execution. Operations now has
a read-only shadow-situation panel, while richer drill-down remains future
work. The seven-day shadow-use gate and live precision target remain future
operational evidence. Phase 2 `P2-LIVE-01` remains accepted as a
non-blocking operational-readiness follow-up, and the accepted Phase 3
Guardian Kernel was not reopened or modified.
