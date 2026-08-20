# Phase 3 evidence — Guardian Kernel and verified action runtime

Status: **accepted by Marc** on 2026-08-16 (America/New_York), after the Phase 3
review and technical verification. The Phase 3 exit-gate scenarios pass with deterministic
Home Assistant-like and Codex workspace fixtures, a registered reversible Home
Assistant adapter, a Codex native-protocol Guardian handoff, and a database
restart/immutability check. See the [2026-08-16 review addendum](review-2026-08-16.md)
for the complete audit and deferred follow-up.

## Current source scope

- `src/openjarvis/guardian/`: typed registry, durable grants/revocations,
  emergency stop, precondition checks, registered execution, independent
  verification, Home Assistant adapter, Codex bridge, and Guardian audit
  timeline. Guardian denial error classes are preserved in the shared action
  ledger.
- `src/openjarvis/cognition/actions.py`: adds read-only causal-chain queries
  and records a timeout after possible effect as `needs_attention` rather than
  failed-and-retryable.
- `tests/acceptance/phase_3/`: deterministic fixture demonstration using
  Home Assistant-like and Codex-workspace-like adapters, plus the native
  Codex-supervisor bridge and registered Home Assistant adapter.

## Reproduction

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-phase3-tests `
  tests\acceptance\phase_3 `
  tests\server\test_guardian_routes.py `
  tests\tools\test_home_assistant.py `
  tests\cognition\test_actions.py `
  tests\acceptance\phase_0\test_truthful_lifecycle.py

.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-phase3-regression `
  tests\codex_observer\test_phase_1_observer.py `
  tests\codex_observer\test_phase_2_mission_control.py `
  tests\server\test_codex_routes.py
```

## Current results

- Phase 0–3 lifecycle, Guardian API, Home Assistant, and Codex acceptance
  suite: 81 passed.
- Phase 1/2 Codex observer and mission-control regression suite: 18 passed.
- Combined focused Phase 0–3 regression result: 99 passed.
- Complete `tests/acceptance` regression suite: 111 passed.
- Ruff for the Guardian, action runtime, Home Assistant boundary, API, and
  Phase 3 acceptance tests: passed.
- Frontend type-check, production build, and Vitest suite: passed (6 tests).
- Schema migration: additive `guardian_*` SQLite tables; no existing action,
  Codex, or connector table is modified.
- Prior evidence records a local API smoke at `http://127.0.0.1:8000` in which
  an unregistered action returned `allowed: false`, `state: failed`, and a
  timeline containing both the Guardian denial and underlying action audit;
  this review did not connect to a running external service.

## Known limitations and deliberate deferrals

Only reversible Home Assistant `turn_on` and `turn_off` are registered in
Phase 3. More device actions require their own registry entry, exact grant
shape, fresh preconditions, compensation decision, and independent verifier.
Codex native approvals are recorded before native reply; their later command
or file effects remain observable in the existing Codex mission ledger rather
than being claimed as verified by the approval record itself.

The broader existing server baseline still has an unrelated failure in
`tests/server/test_routes.py::TestMemoryServiceWiring::test_non_streaming_completion_feeds_memory`:
the injected memory-service spy receives no submission. It is outside the
Guardian, Home Assistant, Codex, and API paths changed for Phase 3; the
focused Phase 0–3 and Phase 1/2 regression suite above passes.

Phase 2 remains accepted by Marc. Its non-blocking `P2-LIVE-01` Codex App
Server acknowledgement delay is deliberately not reopened as a Phase 3
blocker.

The current review retained private Home Assistant write/verification helpers
whose external compatibility is uncertain; they are cleanup candidates rather
than Phase 3 behavioral blockers.

## Rollback

Stop Phase 3 callers and restore the pre-Phase-3 database backup if the
Guardian records must be removed. The migration is additive: removing
`guardian_*` tables leaves Phase 0 action records and Phase 1/2 Codex tables
unchanged. Do not widen a Codex sandbox or connector authority to roll back.
