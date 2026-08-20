# Phase 3 — Guardian Kernel and verified action runtime

Status: **accepted by Marc** on 2026-08-16 (America/New_York), after the Phase 3
review and technical verification. This specification freezes and documents
the completed Phase 3 acceptance scenario.

The current audit and acceptance are documented in
[`docs/evidence/phase-3/review-2026-08-16.md`](../evidence/phase-3/review-2026-08-16.md).
Deferred follow-up remains live external Home Assistant/Codex validation and
broader private-helper cleanup.

## Scope

Phase 3 adds a fail-closed `openjarvis.guardian` control plane to the existing
Phase 0 action ledger. Each registered action has an object input schema,
capability, risk and consequence class, freshness requirement, idempotency
strategy, expected effect, independent verifier, compensation hook, redaction
policy, and timeout/retry classification. Model text cannot supply an adapter.

The kernel persists narrow session grants, revocations, equivalent-action
denials, emergency-stop state, and an immutable Guardian audit stream in the
same SQLite database as the action ledger. It validates a grant and fresh
preconditions immediately before calling a registered adapter, then verifies
through the separate registered read path.

## Frozen acceptance scenario

`tests/acceptance/phase_3/test_guardian_kernel.py` uses two fixture adapters:

1. a Home Assistant-like state change;
2. a Codex-workspace-like state change.

It proves one approved reversible effect is independently verified; a missing
grant is denied; a tool-reported success with a disagreeing verifier fails; a
timeout after dispatch becomes `needs_attention` without a retry; and a
session revoked after authorization blocks the next side effect. It also
proves unknown, stale, contradictory, equivalent-denied, and emergency-stop
cases remain fail-closed, while the timeline reconstructs the causal chain.
The acceptance suite additionally reopens the same database and proves that the
verified timeline, grant/revocation state, emergency stop, and immutable audit
records survive restart.

## Deliberate limits

Direct Home Assistant writes now fail closed. The supported reversible
`turn_on`/`turn_off` actions run only through registered Guardian adapters and
independent state reads. Codex command, file-change, permission, and network
approvals create redacted, exact-scope Guardian records before any native
Codex response is sent. User-input requests remain native non-effectful
conversation. The Codex protocol and its sandbox remain in control of the
downstream operation.

## Rollback

Disable Phase 3 callers, then retain the SQLite file as evidence or restore
the pre-Phase-3 backup. `guardian_*` tables are additive and do not alter
existing action, Codex, or connector tables. Removing the Guardian caller
returns the system to the existing Phase 0/2 behavior; it never grants a
broader connector or Codex sandbox permission.
