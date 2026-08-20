# Phase 6 JARVIS experience assessment

Status: **accepted by Marc on 2026-08-16 (America/New_York)** for the local
deterministic slice.

- Presence: Ophanim can show the situation, goal, evidence IDs, expected
  observations, exact target, consequence class, reversibility, time window,
  authority, and rationale before an authorization decision.
- Continuity: plan edits create durable versions. The removed light remains in
  historical version 1 but cannot silently return in version 2 or later.
- Initiative: planning is inspectable but bounded. A plan never executes, calls
  a connector, sends a notification, or creates a real-world effect.
- Competence: the Departure fixture covers valid/invalid structure, registry
  allow-listing, freshness, changed calendar/situation, exact grant scope,
  autonomy levels, high-consequence approval, frequency/resource budgets,
  cancellation, replay, restart, and API inspection.
- Trust: contextual grants are exact records rather than string approvals;
  stale evidence, wrong target/time/situation/plan, revoked/expired grants,
  changed presence, and changed consequence class fail closed. Prompt-shaped
  untrusted content stays metadata and cannot create authority.

## Manual review: Departure plan

Run the focused command from the Phase 6 README. In the test or a local Python
fixture, inspect the returned `StructuredPlan` and then:

1. confirm version 1 contains reminder, light, thermostat Away, and alarm arm;
2. call the removal edit for `light` and confirm version 2 has the tombstone;
3. inspect `GET /v1/planning/plans/departure-plan-1` and compare `versions`,
   `edits`, `validation`, `grants`, `scheduled_effects`, and `audit`;
4. create the thermostat grant and compare every field to version 2 and the
   current context;
5. authorize thermostat with the exact context and confirm the decision
   authority is `Phase6.ContextualGrant`;
6. retry with a different target, time window, situation, calendar event,
   presence/freshness, or plan version and confirm denial;
7. authorize alarm without `marc_approved=true` and confirm separate approval
   is required;
8. schedule a local bookkeeping effect, cancel the plan, and confirm every
   scheduled/pending row is `canceled`.

The API reports `side_effects: false`. There is no Phase 7 departure pilot in
this implementation.

Marc accepted Phase 6 on 2026-08-16 (America/New_York). Phase 5 remains
pending Marc's review and explicit acceptance. Live source validation and
real-world execution remain deferred to Phase 7.
