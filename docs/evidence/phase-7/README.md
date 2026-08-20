# Phase 7 evidence — Departure Guardian pilot

Status: **live standby wired; delayed execution disabled; review-ready; not
accepted by Marc**.

Phase 5 remains pending Marc's review and explicit acceptance. Phase 6 was
accepted by Marc on 2026-08-16; its live-source and real-world execution
follow-up remains a Phase 7 concern. Live integrations are active in standby;
live side effects and staged execution remain disabled.

## Evidence contents

- `docs/design/2026-08-15-phase-7-departure-guardian-pilot.md` — architecture,
  data model, gates, staged-live criteria, and limitations;
- `docs/evidence/phase-7/jarvis-experience.md` — presence, continuity,
  initiative, competence, and trust assessment;
- `src/openjarvis/departure/` — local contracts, persistence, orchestration,
  live adapters plus test-only deterministic fakes;
- `tests/acceptance/phase_7/test_departure_guardian_pilot.py` — 38 focused
  deterministic tests covering more than twenty recorded/synthetic Departure
  scenarios;
- `tests/server/test_departure_routes.py` — 4 disabled-mode, inspection, and
  execution-reporting API checks.

## Deterministic scenario coverage

The replay set covers normal departure; calendar cancellation; changed calendar
event; stale/missing/offline source; traffic increase; weather change; early
departure; late departure; return home; multiple people present; conflicting
household plan; approval expiry; grant scope; grant revocation path; Marc plan
editing; removed actions; partial failure; verification disagreement; ambiguous
effect; restart before execution; cancellation before execution; emergency
stop; cover feedback absence; untrusted prompt-shaped content; duplicate
prevention; high-consequence alarm approval; and exception-only reporting.

The replay asserts zero unauthorized effects, zero duplicate adapter calls, no
orphaned scheduled effects after cancellation/revocation, no authority growth
after restart, and separate approval for alarm arming.

## Current focused results

```text
Phase 7 acceptance + API: 42 passed
Complete tests/acceptance regression: 126 passed
Phase 3 Guardian + Phase 6 planning + related server neighbors: 18 passed
Phase 7 Ruff check: passed
Phase 7 Ruff format check: passed
Phase 7 Python compilation: passed
```

The focused command is:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-phase7-focused `
  tests/acceptance/phase_7 `
  tests/server/test_departure_routes.py
```

The full `tests/server` sweep was not rerun for this review. Its previous
documented baseline of 294 passed and 9 environment/baseline failures is not
treated as Phase 7 acceptance evidence.

## Review fixes and cleanup — 2026-08-16

The review preserved the Phase 7 architecture and made small safety-focused
fixes: approval records are checked against the exact plan version and step;
terminal effect history cannot be rewritten by later revocation; pending
schedules can be enriched with their newly approved exact authority; setup
version changes invalidate old departure schedules; malformed boolean fields
and action target overrides fail closed; Phase 3 light aliases are registered
at the Phase 7 boundary; and the execution endpoint reports when side effects
are enabled. The redundant compatibility export at `src/openjarvis/phase7/`
was retained because its public-import compatibility cannot be proven safe to
remove from repository search alone.

## Manual replay review

Use the deterministic acceptance lane, then inspect one successful fixture:

1. Confirm the typed setup is persisted at version 1 and all action policies
   start at Level 0.
2. Confirm recalculation creates a Departure plan and a concise brief with
   evidence, expected departure, authority, expiration, and warnings.
3. Approve one exact step and schedule it. Verify that no adapter call occurs
   before actual departure and while the delayed-execution flag is disabled.
4. Confirm GET /v1/departure/status reports live-standby and no side effects.
5. Replay again and confirm the schedule remains completed and the adapter call
   count is unchanged.
6. Change traffic, weather, calendar identity, or presence and confirm the old
   plan is superseded and old scheduled effects are canceled.
7. Edit the plan, inspect the new version, and confirm removed actions remain
   tombstoned.
8. Trigger a partial or ambiguous failure and inspect the exception-only
   report, verification result, skipped actions, and causal events.
9. Inspect the weekly trust report for approvals, denials, cancellations,
   verification, stale-source, emergency-stop, and duplicate-prevention counts.

## Implementation and review state

Implementation-complete items are the typed contracts, persistence,
recalculation, brief, Phase 6 plan lifecycle, exact approval/grant routing,
delayed-execution gates, live adapter wiring, cancellation/failure
handling, exception-only report, weekly trust report, API inspection, and the
deterministic replay suite.

Deferred live review before full activation:

- Confirm the running service remains live-standby with delayed execution off.
- Run real read-only Home Assistant state checks against the configured
  entities.
- Run real calendar, traffic, weather, and presence source tests with the
  configured accounts/endpoints and verify freshness/degraded behavior.
- Run one explicitly controlled notification delivery test through the
  configured channel bridge.
- Re-run the live departure scenarios with real observations, then review
  precision, verification, duplicate prevention, revocation, and partial
  failure evidence before enabling delayed execution.

Awaiting Marc's review are Phase 5 acceptance, Phase 7 review/acceptance, and
every staged-live go/no-go decision. Phase 6 acceptance is recorded in its
design and evidence documents; no other acceptance is claimed on Marc's
behalf.

The project is live on standby: the runtime uses the configured Home Assistant
and channel integrations, while delayed execution remains disabled and the
Guardian cannot create real-world effects from standby.
