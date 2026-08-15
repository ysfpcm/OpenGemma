# Ophanim Guardian Loop

> **Implementation source of truth:** [Ophanim Master Implementation Plan](2026-08-15-ophanim-master-implementation-plan.md). This document is retained as Guardian Loop design rationale.

**Status:** Proposed flagship initiative  
**Date:** 2026-08-15  
**Working name:** Guardian Loop  
**Product promise:** Ophanim notices meaningful changes, prepares a safe plan, acts with the right level of permission, verifies the result, and learns the user's preferences — locally by default.

## Executive decision

The next major step should not be another connector, agent template, dashboard card, or model backend. Ophanim already has enough primitives. The next step is to make them operate as one trustworthy autonomy system.

Build a durable, event-driven **Guardian Loop**:

> Observe → Understand → Propose → Authorize → Act → Verify → Learn

The first complete experience will be **Departure Guardian**. Ophanim will combine the calendar, traffic, weather, Home Assistant, device state, and learned preferences to help the user leave on time and leave the home in a safe state. It will start in shadow mode, graduate to suggestions, and only then earn narrowly scoped autonomy.

This creates a product people can describe in one sentence: **“Ophanim quietly runs the routines around my life, on my own hardware, without taking control away from me.”**

## Why this is the right bet

### What already exists

Ophanim already has most of the expensive ingredients:

- A live SQLite context model with current state, history, freshness, and Home Assistant ingestion.
- Calendar, traffic, messaging, email, tasks, and local knowledge connectors.
- Managed agents with schedules, budgets, checkpoints, retries, stall detection, and persistent context.
- A system-wide event bus with context, camera, channel, tool, agent, security, and scheduler events.
- A proactive agent with risk tiers, approval memory, notification delivery, and a persistent approval queue.
- Capability policies, taint tracking, security events, traces, and telemetry.
- A workflow engine and an Operations UI with live terminal, agent run status, Context Atlas, and approvals.
- Teachable device behavior and a Home Assistant action catalog.

### What is missing

Those pieces do not yet form a closed loop:

- Proactivity is cron-first and centered on one hard-coded morning inbox scan.
- EventBus delivery is synchronous and in-memory; it is observation plumbing, not a durable trigger fabric.
- There is no shared contract for a situation, proposed plan, preconditions, expected effects, verification, or compensation.
- Permission memory is keyed to action strings, but is not constrained by context, time, device, confidence, or consequence.
- An action can be marked `executed` even when its executor reports failure; success and completion are not modeled separately.
- The UI can show events and pending approvals, but cannot explain the causal chain from observation to decision to verified result.
- There is no replay harness that proves an autonomy policy would have behaved safely across recorded situations.

The opportunity is therefore integration with a strong product thesis, not surface-area expansion.

## Product principles

1. **Local observation, minimum disclosure.** Raw home and personal context stays local. Cloud escalation receives a redacted, minimal problem statement only when explicitly allowed.
2. **Earned autonomy.** Every routine begins in shadow mode. Autonomy expands only from demonstrated, measurable user trust.
3. **Plans before actions.** The model may propose intent; deterministic code validates capabilities, preconditions, budgets, and risk.
4. **Verification is part of execution.** “API call returned 200” is not the same as “the door locked.”
5. **Every action is explainable.** The user can answer: what changed, why Ophanim cared, what it considered, what it did, and whether it worked.
6. **Silence is a feature.** The system optimizes for useful interventions, not engagement or notification volume.
7. **Failure must be boring.** Duplicate events, restarts, stale context, unavailable models, and partial tool failures produce safe and predictable outcomes.

## Lighthouse experience: Departure Guardian

### User story

At 4:05 p.m., Ophanim notices a 5:00 p.m. appointment across town. Traffic has added 14 minutes, the user is still home, rain is likely, and a downstairs window is open.

Ophanim:

1. Updates the recommended departure time to 4:18 p.m.
2. Sends one concise brief: “Leave in 13 minutes. Rain likely. Downstairs window is open.”
3. Offers a single plan: close supported covers, set the thermostat to Away, turn off downstairs lights, and arm the alarm after departure.
4. Automatically performs only previously authorized, low-risk actions.
5. Asks once for unfamiliar or consequential actions.
6. Detects departure, executes the approved plan, then verifies each device state.
7. Reports only exceptions: “Away mode is set. Alarm armed. Kitchen window still reports open.”
8. Learns edits such as “never turn off the entry light when I leave after sunset.”

### Why this slice

Departure Guardian forces the platform to solve the hard, reusable problems:

- Time-based and event-based triggers.
- Multi-source context with freshness requirements.
- A plan containing read-only, reversible, and consequential actions.
- Delayed execution after a real-world condition such as presence changing.
- Verification through independent observations.
- Partial failure, cancellation, and user overrides.
- A measurable outcome: on-time departure with fewer forgotten home-state tasks.

Once this is solid, Arrival Guardian, Sleep Guardian, Weather Guardian, Inbox Guardian, and Caregiver Guardian become policy packs rather than new architectures.

## System model

### Core records

The Guardian Loop should persist seven first-class records:

| Record | Purpose |
|---|---|
| `Observation` | Normalized fact from a source, with provenance, timestamp, freshness, and sensitivity labels. |
| `Situation` | A deduplicated, meaningful condition derived from observations, such as “departure risk.” |
| `Plan` | Versioned set of proposed actions, dependencies, expected outcomes, expiration, and rationale. |
| `ActionProposal` | One typed action with inputs, capability, risk, confidence, preconditions, and idempotency key. |
| `Authorization` | User or policy decision, constrained by scope, context, time, and maximum consequence. |
| `ActionAttempt` | Immutable execution attempt with tool result, timing, and error classification. |
| `Verification` | Independent check of the expected outcome, including retry or compensation decision. |

These records create one causal lineage:

`Observation → Situation → Plan → Authorization → ActionAttempt → Verification → LearningSignal`

### Runtime components

#### 1. Durable trigger fabric

Add a persistent inbox between EventBus producers and Guardian policies.

Responsibilities:

- Persist subscribed events before policy evaluation.
- Normalize source-specific events into observations.
- Deduplicate with stable event and situation keys.
- Support debounce, cooldown, correlation windows, and freshness requirements.
- Resume after restart without replaying completed effects.
- Route events to policies by declarative trigger rules.
- Dead-letter poison events after bounded retries.

The existing EventBus remains the low-latency process bus. A new durable event journal becomes the autonomy boundary.

#### 2. Situation engine

The situation engine converts many low-level observations into a small number of meaningful conditions. It should use deterministic predicates first and an LLM only for ambiguity or summarization.

Example Departure Guardian predicates:

- Calendar event has a physical location.
- Travel duration plus preparation buffer intersects the current time.
- Presence indicates the user is still home.
- Required source observations are fresh enough.
- No equivalent departure situation is already active.

#### 3. Planner and action contracts

The planner emits structured plans, not direct tool calls. Each action type is registered with:

- JSON input schema.
- Required capability.
- Default risk class.
- Preconditions.
- Idempotency strategy.
- Expected effects.
- Verification adapter.
- Optional compensation action.
- Redaction policy.
- Maximum retry count and timeout.

LLM output never gets to invent executors. Unknown action types fail closed.

#### 4. Trust kernel

Replace a single global “always approve this permission key” concept with contextual grants.

A grant may be limited by:

- Action type and target entity.
- Location or household mode.
- Time window or maximum duration.
- Required confidence and source freshness.
- Maximum frequency and daily budget.
- Presence of another person.
- Reversibility and consequence class.
- Whether the action was edited by the user.

Suggested autonomy ladder:

| Level | Behavior |
|---|---|
| 0 — Shadow | Record what Ophanim would propose; never notify or act. |
| 1 — Suggest | Notify with a plan; user performs actions manually. |
| 2 — Approve | One-tap approval executes the plan. |
| 3 — Delegated | Auto-execute narrowly granted actions; report exceptions. |
| 4 — Routine | Run a mature routine silently unless confidence or verification drops. |

High-consequence actions never exceed Level 2 without an explicit product decision and dedicated safety review.

#### 5. Verified executor

Execution is a state machine, not a boolean:

`proposed → authorized → executing → effect_pending → verified | failed | compensating | compensated | needs_attention`

Rules:

- Validate the authorization immediately before execution.
- Re-check preconditions using fresh observations.
- Use idempotency keys for every side effect.
- Record success and failure accurately; never label a failed attempt executed.
- Verify through a read path independent of the write response when possible.
- Retry only classified transient failures.
- Run compensation only when it is known safe.
- Escalate partial plans as one concise exception report.

#### 6. Learning loop

Capture explicit and implicit feedback:

- Approve, deny, edit, cancel, undo.
- User manually reverses an action shortly afterward.
- User ignores a suggestion.
- Verification fails.
- The predicted situation does not occur.

Initially, learning updates deterministic policy parameters and contextual grants. Model fine-tuning comes later, after the trace schema and evaluation set are trustworthy.

#### 7. Causal Operations UI

Turn the graph prototype into a useful “Why this happened” view rather than a database-schema visualization.

The graph should show:

- Observations as source nodes.
- The active situation and its confidence.
- Candidate and selected plans.
- Approval gates and policy decisions.
- Actions flowing through execution.
- Verification results and any failed edge.

The default UI remains a human-readable timeline. The graph is the drill-down for debugging and trust. Users can approve, deny, edit, pause, or revoke a grant from the same surface.

## Delivery plan

### Milestone 0 — Baseline and safety repairs

**Goal:** Establish reliable semantics before adding autonomy.

Deliverables:

- Correct action lifecycle so failed actions are not marked executed.
- Add immutable action-attempt records and event emission.
- Define action risk taxonomy and supported side-effect registry.
- Document current approval, capability, taint, audit, workflow, and agent-runtime boundaries.
- Add end-to-end tests for approval → execution → failure/success reporting.

Exit gate:

- Existing proactive flows preserve behavior.
- Every attempted side effect has an accurate terminal state and audit record.

### Milestone 1 — Durable events and shadow policies

**Goal:** Make context events safely actionable without taking actions.

Deliverables:

- SQLite event journal, consumer leases, retry count, and dead-letter state.
- Observation normalization and provenance.
- Declarative trigger schema with correlation windows, dedupe, debounce, cooldown, and freshness.
- Policy runtime that creates Situations in shadow mode.
- Departure Guardian situation detector using calendar, traffic, presence, and home state.
- Replay command for recorded event fixtures.

Exit gate:

- Restart and duplicate-event tests produce exactly one Situation.
- Seven days of shadow-mode use produce no duplicate or obviously stale departure situations.

### Milestone 2 — Structured planning and contextual approvals

**Goal:** Turn Situations into inspectable, safe plans.

Deliverables:

- Versioned Plan and ActionProposal schemas.
- Action registry with precondition and verification interfaces.
- Deterministic plan validator and risk classifier.
- Contextual grants and the autonomy ladder.
- Approval UI that shows rationale, evidence freshness, expected effects, and editable actions.
- Plan expiration and cancellation when the underlying situation changes.

Exit gate:

- Invalid, stale, unauthorized, and unknown actions fail closed.
- A user can edit a plan and see the exact authorization scope before approval.

### Milestone 3 — Verified execution

**Goal:** Close the loop from permission to independently verified outcome.

Deliverables:

- Durable execution state machine and idempotency keys.
- Verification adapters for the first Home Assistant action set.
- Bounded retries, timeout classification, and safe compensation hooks.
- Exception-only notifications.
- Full causal timeline in Operations.

Initial supported effects:

- Read current device state.
- Lights off/on.
- Thermostat preset or target temperature.
- Cover close/open where position feedback exists.
- Alarm arm only with explicit approval.
- Notification and reminder delivery.

Exit gate:

- Duplicate delivery and process restart cannot repeat a side effect.
- Every successful action is verified or explicitly labeled unverified.
- Partial failure produces one accurate exception summary.

### Milestone 4 — Departure Guardian pilot

**Goal:** Deliver the complete user-visible promise.

Deliverables:

- Setup wizard for calendars, destination assumptions, preparation buffer, traffic, presence, home actions, and notification channel.
- Shadow → Suggest → Approve → Delegated progression.
- “Why now?” brief and one-tap plan editing.
- Departure detection, cancellation, late-event changes, and return-home edge cases.
- A personal weekly trust report: interventions, approvals, denials, edits, verification, and avoided noise.

Exit gate:

- At least 20 real or replayed departure scenarios.
- Zero unauthorized side effects.
- At least 95% verified completion for supported device actions.
- Fewer than one unnecessary notification per ten valid situations.

### Milestone 5 — Policy-pack platform

**Goal:** Prove the architecture generalizes without another rewrite.

Deliverables:

- Package format for Guardian policies, triggers, required sources, actions, UI copy, and evaluation fixtures.
- Arrival Guardian as the second first-party policy.
- Policy simulator and safety scorecard.
- Export/import with secrets and personal observations excluded.

Exit gate:

- Arrival Guardian ships using the same runtime and adds no policy-specific branches to the core executor.

## Evaluation strategy

### Scenario replay

Build a local corpus of timestamped, anonymized event sequences. Each scenario declares:

- Relevant observations and their freshness.
- Expected situation or expected silence.
- Allowed and forbidden actions.
- Required approval level.
- Expected verification behavior.
- Changes that should cancel the plan.

Required scenario classes:

- Duplicate and out-of-order events.
- Stale presence or device state.
- Calendar event moved or canceled after planning.
- User leaves early, late, or not at all.
- Multiple household members present.
- Tool timeout after the side effect actually occurred.
- Service returns success but device state does not change.
- Restart during every execution state.
- User denies, edits, revokes, or manually reverses an action.

### North-star metrics

| Metric | Initial target |
|---|---|
| Unauthorized side effects | 0 |
| Duplicate side effects | 0 |
| Verified completion of supported actions | ≥ 95% |
| Situation precision in pilot | ≥ 90% |
| Approval acceptance after edits | ≥ 70% |
| Unnecessary intervention rate | < 10% |
| Median explanation latency | < 2 seconds local |
| Recovery from restart | No lost authorization or repeated effect |

Model accuracy alone is not a release gate. The system is judged on outcomes, silence, reversibility, and user trust.

## Suggested work breakdown

Use four parallel workstreams once the schemas are agreed:

1. **Runtime:** event journal, situations, policy runner, execution state machine.
2. **Trust:** action registry, contextual grants, validation, verification, audit.
3. **Experience:** setup, approvals, timeline, causal graph, weekly trust report.
4. **Evaluation:** replay fixtures, fault injection, metrics, pilot scorecard.

Schema and state-machine work comes first. UI and the lighthouse policy can then advance against stable contracts.

## Scope boundaries

### Build now

- One autonomy runtime.
- One excellent lighthouse experience.
- A narrow, verified Home Assistant action set.
- Local replay and shadow mode.
- Human-readable explanations and contextual grants.

### Explicitly defer

- A marketplace of hundreds of policies.
- Model fine-tuning from raw household traces.
- Financial transactions or purchases.
- Door unlock, garage open, stove/oven control, or other high-consequence physical actions.
- General computer-use autonomy.
- Multi-user identity inference from cameras.
- Cloud-hosted household context.

### Stop spending cycles on for this initiative

- Additional chat skins.
- More agent personas without new runtime semantics.
- New connectors that Departure Guardian does not need.
- More model/provider adapters.
- Generic dashboards without a decision or trust workflow.
- Broad refactors unrelated to the closed loop.

## Major risks and mitigations

| Risk | Mitigation |
|---|---|
| Notification fatigue | Shadow mode, cooldowns, situation dedupe, and an intervention-rate release gate. |
| Unsafe model-generated actions | Typed action registry, deterministic validation, contextual grants, and fail-closed unknowns. |
| Stale or contradictory context | Freshness requirements, provenance, confidence penalties, and precondition re-checks. |
| Side effect happens but response times out | Idempotency keys and independent verification before retry. |
| Permission becomes too broad over time | Narrow grants, expiration, visible scope, revocation, and no silent scope expansion. |
| Home Assistant device variance | Start with state-feedback-capable entities and explicit compatibility reporting. |
| Architecture grows into another framework | Hold the lighthouse experience and measurable pilot gates as the definition of done. |

## First ten implementation issues

1. Define action lifecycle, error classes, and database migration; fix failed-action status semantics.
2. Add `ActionAttempt` and `Verification` persistence with immutable audit events.
3. Implement the SQLite durable event journal with leases, idempotent acknowledgement, and dead letters.
4. Define `Observation`, `Situation`, `Plan`, `ActionProposal`, and `Authorization` schemas.
5. Implement trigger rules with dedupe, debounce, cooldown, correlation windows, and freshness constraints.
6. Build the shadow policy runner and event replay command.
7. Implement the Departure Guardian detector with fixture-based scenarios.
8. Build the typed Home Assistant action registry with precondition and verification adapters.
9. Add contextual grants and the four-stage user-facing autonomy progression.
10. Ship the Operations causal timeline; add the graph drill-down after the timeline is complete.

## Go / no-go checkpoint

After Milestone 2, pause and evaluate shadow data before enabling side effects.

Proceed only if:

- Departure situations are precise enough to be useful.
- Evidence and rationale are understandable without reading logs.
- Dedupe and restart behavior are deterministic.
- Users can predict exactly what an approval permits.
- The action registry covers the lighthouse plan without escape hatches.

If those conditions fail, improve situation quality and permission clarity. Do not compensate by giving the model broader tool access.

## Definition of “big”

This initiative is big because it changes the product category. Ophanim stops being a collection of local AI features and becomes a trusted local operating layer for everyday routines. The durable trigger fabric, trust kernel, verified executor, replay harness, and causal UI are reusable infrastructure — but the project remains anchored to one emotionally legible outcome: **help me move through my day without forgetting what matters or surrendering control.**
