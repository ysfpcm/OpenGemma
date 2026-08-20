# Phase 6 evidence — Structured planning and contextual authorization

Status: **accepted by Marc on 2026-08-16 (America/New_York)** for the
deterministic local slice.

Phase 5 remains **pending Marc's review and explicit acceptance**. Phase 6 did
not mark Phase 5 accepted and did not enable Phase 7.

## Implementation scope

- `src/openjarvis/planning/models.py` — typed context, observations, plan
  steps, versioned plans, contextual grants, autonomy levels, validation, and
  authorization decision contracts;
- `src/openjarvis/planning/planner.py` — deterministic schema, registry,
  executor, capability, dependency, freshness, cancellation, consequence, and
  budget validation;
- `src/openjarvis/planning/store.py` — additive SQLite plan versions, edit
  history, tombstones, grants, grant budgets, decisions, policies, scheduled
  effects, status, and audit state;
- `src/openjarvis/planning/authorization.py` — exact contextual matching and
  the final Guardian Kernel authorization boundary with no execution method;
- `src/openjarvis/server/planning_routes.py` — opt-in plan/grant/decision
  inspection and review API;
- `tests/acceptance/phase_6/test_structured_planning_contextual_authorization.py`
  — deterministic Departure scenario, scope denial, cancellation, restart,
  replay, malformed/unregistered plans, and untrusted content;
- `tests/server/test_planning_routes.py` — API inspection and disabled-mode
  checks.

## Departure-plan oracle

The acceptance fixture creates four registered steps: reminder, reversible
light action, thermostat Away, and high-consequence alarm arm. It then:

1. removes the light and persists version 2;
2. proves the removed step remains only in version 1 and cannot reappear;
3. grants only thermostat Away for the exact edited plan, calendar event,
   situation, target, home/household context, presence, time window, and fresh
   observations;
4. denies a different target, time, situation, calendar event, stale
   presence, and wrong plan scope;
5. denies alarm arming without separate Marc approval;
6. cancels scheduled bookkeeping and leaves no scheduled effect;
7. closes/reopens the stores and verifies versions and grants remain narrow;
8. verifies malformed and prompt-shaped untrusted content cannot create
   authority.

## Verification commands

Focused Phase 6 tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-phase6-focused `
  tests/acceptance/phase_6 `
  tests/server/test_planning_routes.py
```

Review result on 2026-08-16:

```text
10 passed
```

Touched-file quality checks:

```powershell
.\.venv\Scripts\ruff.exe check `
  src/openjarvis/planning `
  src/openjarvis/server/planning_routes.py `
  tests/acceptance/phase_6 `
  tests/server/test_planning_routes.py
.\.venv\Scripts\ruff.exe format --check `
  src/openjarvis/planning `
  src/openjarvis/server/planning_routes.py `
  tests/acceptance/phase_6 `
  tests/server/test_planning_routes.py
.\.venv\Scripts\python.exe -m compileall -q `
  src/openjarvis/planning `
  src/openjarvis/server/planning_routes.py
```

The following results were rerun during the Phase 6 review, before the
separate Phase 7 review:

```text
Phase 6/API focus: 10 passed
Touched-file Ruff check: passed
Touched-file Ruff format --check: passed
Python compileall: passed
Historical neighboring Phase 7/departure route tests: 15 passed, 18 failed
Historical complete tests/acceptance: 102 passed, 24 failed
```

The historical neighboring and complete-suite failures were later Phase 7
failures, dominated by the then-existing `NameError: name 'cursor' is not
defined` at `src/openjarvis/departure/store.py:358`, plus a Phase 7 fixture
unpacking mismatch. Phase 6 tests did not reach that code. The separate Phase 7
review subsequently fixed those defects and reran the current lanes:

```text
Current Phase 7/API focus: 42 passed
Current complete tests/acceptance: 126 passed
```

These follow-up results do not change the Phase 6 acceptance decision. The
older handoff's `tests/server` and frontend results remain historical evidence.

## Review audit and cleanup

The review added deterministic checks for exact grant construction, wildcard
and malformed boolean rejection, edit-idempotency scope, and release of a
grant budget reservation after a Guardian denial. The implementation fixes
bind edit IDs to their plan and original payload, require grants to match the
persisted plan context and expiration, serialize grant-budget reservations,
and release reservations when no authorization is created. An unused private
timestamp helper was removed from the Phase 6 model module.

No Phase 7 or other later-phase implementation was added during the Phase 6
review. The Phase 7 defect was handled later in its own review; no ambiguous
outside-phase cleanup was removed.

## Safety result

No live calendar, Home Assistant, notification, messaging, or other external
service was connected. No plan executes an action. The only authority state
created by a successful Phase 6 authorization is an exact Guardian
authorization record for the already registered proposal.

## Acceptance record

Marc explicitly accepted Phase 6 in the Codex task on 2026-08-16
(America/New_York). Deferred follow-up is live calendar/presence/device
validation and real-world execution; those remain Phase 7 concerns. Phase 5
remains pending Marc's review and acceptance.

## Limitations and review state

The implementation is deterministic and local-fixture only. Phase 7 is
intentionally not enabled because the seven-day shadow/pilot and real-world
effect gates are outside this phase.
