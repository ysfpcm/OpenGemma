# Phase 7 — Departure Guardian end-to-end pilot

Status: **live standby wired; delayed execution disabled; review-ready; not
accepted by Marc**.

Phase 5 and Phase 6 remain pending Marc's review and explicit acceptance. Phase 7
does not claim acceptance for either phase and does not begin Phase 8.

## Boundary and architecture

Phase 7 is an additive coordinator around the existing Phase 6 and Guardian
Kernel boundaries:

```text
typed setup + observations
          |
          v
  DepartureGuardian ----> WhyNowBrief
          |
          +----> Phase 6 StructuredPlan / versions / tombstones
          |             |
          |             +---- exact contextual grant or Marc approval
          |
          +----> durable delayed schedule (local only)
                        |
                        +---- fresh observations + actual departure
                        +---- Guardian Kernel authorization
                        +---- registered live adapter
                        +---- independent verification
```

Planning remains separate from execution. A generated or edited plan never runs
by itself. Every proposed effect is an `ActionProposal`, every executor is
allow-listed in `ActionRegistry`, and the Guardian Kernel remains the only
component that can call an adapter.

Implementation locations:

- `src/openjarvis/departure/models.py` — typed setup, source, action,
  observation, brief, and recalculation contracts;
- `src/openjarvis/departure/store.py` — versioned setup state, scheduled
  effects, approvals, immutable causal events, and report storage;
- `src/openjarvis/departure/service.py` — recalculation, plan lifecycle,
  approval/grant routing, delayed execution gates, cancellation, and reports;
- `src/openjarvis/departure/adapters.py` — live Home Assistant REST and
  configured channel-bridge adapters, plus test-only deterministic fakes;
- `src/openjarvis/server/departure_routes.py` — live-standby inspection API;
- `tests/acceptance/phase_7/` and `tests/server/test_departure_routes.py` —
  deterministic replay and API coverage.

## Typed setup and persistence

`DepartureSetup` persists a versioned, inspectable record containing:

- calendar provider, calendar identity, timezone, and freshness;
- destination label/address, route identity, travel mode, and arrival grace;
- preparation buffer and source identity;
- traffic, weather, and presence source identities plus freshness limits;
- typed allow-listed actions and exact targets/parameters;
- local notification channel;
- autonomy level, actual-departure requirement, alarm policy, and execution
  feature state.

`DepartureStore` uses additive SQLite tables prefixed with `phase7_`. Setup
versions are append-only projections. Scheduled effects, approvals, and causal
events are durable and idempotent. Incomplete setup records fail construction;
missing, stale, offline, or contradictory required observations block a plan or
cancel a pending schedule.

## Recalculation and “why now?”

Recalculation fingerprints the setup version and all typed Departure
observations. A changed calendar event, travel estimate, weather, presence,
buffer, household context, or replay state supersedes the prior plan version and
cancels its scheduled effects. Missing or stale inputs invalidate the prior plan
and return a blocked result; authority is never widened to compensate.

`WhyNowBrief` includes the active reason, expected departure time, calendar
evidence, traffic/weather evidence, presence evidence, preparation assumptions,
proposed actions, expected effects, exact authority, expiration/cancellation
conditions, and uncertainty warnings. Imported descriptions and free-form text
remain evidence only. They are never parsed into grants or executors.

## Plan lifecycle

Phase 6's `StructuredPlan` is reused unchanged as the plan contract. Departure
plans contain typed `PlanStep` records, typed preconditions and cancellation
conditions, expected observations, resource cost, and explicit approval flags.
Marc edits call the Phase 6 edit path; removed steps become durable tombstones
and cannot reappear in later recalculations. Approval is stored against the
exact plan version and step. Contextual grants remain scoped to action, target,
plan version, situation, time, source freshness, presence, consequence class,
and edit state.

All new action policies begin at autonomy Level 0 (shadow). An explicit approval
temporarily uses the Phase 6 APPROVE path for that action. A contextual grant
uses the Phase 6 DELEGATED path. Alarm arming and every non-reversible action
still require separate Marc approval.

## Delayed execution gates

The local scheduler is deliberately inert unless both flags are enabled:

```text
OPHANIM_PHASE_7_ENABLED=1
OPHANIM_PHASE_7_DELAYED_EXECUTION=1
```

The setup's `execution_enabled` policy must also be true. Before each effect,
the coordinator checks:

1. the schedule is still pending and the current plan version is active;
2. the setup policy and feature flags permit the local pilot;
3. actual departure is detected;
4. cancellation, return-home, manual reversal, household conflict, stale
   source, and emergency-stop conditions are absent;
5. the exact approval or contextual grant is current;
6. Phase 6 revalidates the complete context;
7. the Guardian Kernel authorizes the exact `ActionProposal`;
8. the action's typed preconditions pass immediately before execution;
9. the idempotency key has not already produced an effect;
10. independent verification settles the result.

Completed, failed, and ambiguous effects are not retried. Restart/replay sees
the durable schedule and action ledger and records duplicate-prevention evidence.

## Adapter and verification boundary

The live server registers only the configured Home Assistant and notification
adapters for:

- read state;
- lights on/off;
- thermostat preset/target;
- cover position, only when position feedback exists;
- alarm arm, separately approved;
- configured channel-bridge notification/reminder delivery.

Each definition declares an input schema, capability, consequence class,
precondition reader, executor, independent verifier, and compensation metadata.
Unavailable or contradictory state fails closed. An executor failure is not
reported as success; a timeout after possible dispatch remains ambiguous and is
not automatically retried.

The server does not fall back to a fake integration. Home Assistant uses the
existing HA_URL/HOME_ASSISTANT_URL and HA_TOKEN/HOME_ASSISTANT_TOKEN
configuration. Notifications use the existing ChannelBridge; unavailable
channels fail closed. Calendar, traffic, weather, and presence observations
remain typed inputs supplied by the project's live context/connector layer,
and stale or missing observations block planning.

## Cancellation, failure, and reporting

Marc cancellation, approval/grant revocation, emergency stop, calendar
cancellation, return-home, household conflict, source degradation, plan edits,
and stale observations cancel or invalidate scheduled effects. There is no
orphan scheduler queue: pending effects become durable `canceled` or `skipped`
records with a causal reason.

Completion reporting is exception-only. A fully verified routine returns no
routine success notification. If an exception exists, the report contains the
partial-failure summary, verification results, skipped actions and reasons,
stale evidence, and the persistent causal timeline.

The weekly trust report aggregates detected situations, suggestions, approvals,
edits, denials, revocations, cancellations, verified/failed/ambiguous actions,
avoided notifications, stale-source events, emergency stops, and duplicate
prevention.

## Live standby and staged execution

The project launcher now activates live standby with:

    OPHANIM_PHASE_6_ENABLED=1
    OPHANIM_PHASE_7_ENABLED=1
    OPHANIM_PHASE_7_DELAYED_EXECUTION=0

In this state the service is live and uses real integrations, but it does not
send reminders or execute Home Assistant actions. The later execution rollout
remains gated:

1. seven days of Shadow mode;
2. Suggest mode with no automated side effects;
3. Approve mode with notification and one reversible action;
4. Thermostat Away only after the previous stage passes;
5. alarm arming remains separately approved.

Go/no-go criteria before any live activation are: zero unauthorized effects,
zero duplicates, at least 90% situation precision on the named replay set, at
least 95% verified supported actions, fewer than one unnecessary notification
per ten valid situations, one accurate exception summary for partial failure,
immediate Marc revocation, and understandable safe degraded/offline behavior.
The live standby wiring does not claim the live-pilot metrics; it provides the
real integration boundary and keeps the execution gate closed until the
staged evidence is reviewed.

Before full live activation, Marc's later review must run the real
Home Assistant, calendar, traffic, weather, presence, and notification tests,
then review precision, verification, duplicate prevention, revocation,
degraded-source behavior, and partial-failure evidence.

## Review and verification commands

Focused Phase 7 lane:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-phase7-focused `
  tests/acceptance/phase_7 `
  tests/server/test_departure_routes.py
```

Quality lane:

```powershell
.\.venv\Scripts\ruff.exe check `
  src/openjarvis/departure `
  src/openjarvis/phase7 `
  src/openjarvis/server/departure_routes.py `
  tests/acceptance/phase_7 `
  tests/server/test_departure_routes.py
.\.venv\Scripts\ruff.exe format --check `
  src/openjarvis/departure `
  src/openjarvis/phase7 `
  src/openjarvis/server/departure_routes.py `
  tests/acceptance/phase_7 `
  tests/server/test_departure_routes.py
.\.venv\Scripts\python.exe -m compileall -q `
  src/openjarvis/departure `
  src/openjarvis/phase7 `
  src/openjarvis/server/departure_routes.py
```

Manual review starts with `GET /v1/departure/{departure_id}` after a local
replay. Compare the setup versions, plan version, removed-step tombstones,
brief, schedules, approvals, action timeline, and Phase 7 causal events. The
API returns `side_effects: false` for inspection and setup/replay bookkeeping.

Known limitations: the scheduler is invoked by the inspection/replay boundary
rather than a live background service, actual departure is supplied by a typed
observation, source collection remains owned by the existing context/connector
layer, and no frontend surface was changed in this phase.
