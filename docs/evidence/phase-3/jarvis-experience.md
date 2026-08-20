# Phase 3 JARVIS experience assessment

Status: **accepted by Marc on 2026-08-16 (America/New_York)**; this assessment covers deterministic Guardian
fixtures, the registered reversible Home Assistant adapter, native Codex
approval handoff, and a Guardian database restart check. It does not claim a
live household action was performed. The current audit and acceptance are
recorded in [`review-2026-08-16.md`](review-2026-08-16.md). Live external
validation remains deferred.

- Presence: one Guardian timeline links the proposal, scoped authority,
  attempt, independent observation, and final state.
- Continuity: the same SQLite database was reopened after a verified effect;
  the effect timeline, grant/revocation state, emergency stop, and audit
  records remained reconstructable, while audit mutation was rejected.
- Initiative: deliberately unchanged. The kernel never originates a side
  effect; it only validates an already proposed registered action.
- Competence: approved fixture effects verify independently; disagreement,
  stale or contradictory inputs, and ambiguous timeouts receive truthful
  terminal or attention states.
- Trust: action types fail closed, authorization is rechecked immediately
  before effect, equivalent denied actions remain denied, and emergency stop
  outranks an otherwise valid grant. Denial error classes remain truthful in
  the shared action ledger.

Regression found: the original Phase 0 executor mapped an ambiguous timeout to
`failed`, which made a later caller more likely to treat it as safely
retryable. Phase 3 now records it as `needs_attention` with its immutable
attempt evidence. The Phase 3 recheck also found that Guardian denial codes
were being collapsed to generic `denied`; that was corrected and covered by
the focused acceptance run.
