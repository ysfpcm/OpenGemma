# Ophanim Phase 9 — Imagination Engine and digital twin

**Status:** accepted by Marc on 2026-08-16 (America/New_York) for the deterministic local slice
**Date:** 2026-08-15
**Scope:** deterministic candidate deliberation, prediction ledger, simulation boundaries, and pre-Guardian validation

**Acceptance record:** Marc explicitly accepted Phase 9 on 2026-08-16
(America/New_York). Deferred follow-up is live service and Codex adapters,
Phase 9 ledger views, held-out personal replay measurement, and review of
scoring weights, fallback policy, notification wording, and future live action
types. These follow-ups do not expand this phase's deterministic local
acceptance.

## Purpose

Phase 9 adds a counterfactual layer to Ophanim's existing planning, Guardian,
departure, world-model, executive, and Codex boundaries. It produces several
typed candidate plans, simulates their consequences, explains the evidence
that rejects or modifies them, and emits only valid candidates for a fresh
Guardian revalidation.

The Imagination Engine cannot authorize, execute, notify, deploy, call live
Home Assistant, or expand a grant. A simulation result is always evidence with
`evidence_scope=simulation`; it is never an action attempt, observed effect,
or verification.

## Additive architecture

```text
fixture / replay evidence
          |
          v
  candidate generator ------> diversity check
          |                         |
          v                         v
  typed actions + expected observations
          |
          +--> symbolic schedule/dependency/budget simulation
          +--> personal event replay
          +--> service/API dry run
          +--> Home Assistant digital twin
          +--> bounded Codex experimental workspace
          |
          v
  failure / feasibility / safety critics
          |
          v
  seven-dimension score + durable prediction ledger
          |
          v
  pre-Guardian rejection or modification
          |
          v
  GuardianBoundary.revalidate()  -- no grant, no execution
```

`openjarvis.imagination` is intentionally separate from the existing
`openjarvis.guardian` and `openjarvis.departure` packages. It compiles typed
actions to the existing `cognition.ActionProposal` shape only at the handoff
boundary. The handoff calls registry validation when a registry is provided;
it never calls `GuardianKernel.authorize()` or `GuardianKernel.execute()`.

## Contracts

Phase 9 schema version 1 defines JSON-round-trippable records for:

- `CandidatePlan` and `TypedAction` — strategy family, substantive diversity
  signature, typed action parameters, dependencies, reversibility, and
  expected-observation links;
- `ExpectedObservation` and `Prediction` — observable claim, confidence,
  provenance, assumptions, causal parents, and simulation/replay evidence;
- `SimulationRun` — simulation layer, status, simulation-only outcome,
  findings, failure summary, and hard-fail `real_effect=false` /
  `authority_created=false` fields;
- `FailureMode`, `CriticResult`, and `Score` — retrieved failure knowledge,
  critic evidence, and success/safety/privacy/reversibility/cost/latency/
  Marc-burden dimensions;
- `ObservationRecord` and `CalibrationRecord` — later replay comparison with
  explicit `verified`, `contradicted`, or `unknown` outcomes;
- `ImaginationDecision` — candidate set, rejected and valid subsets, selected
  candidate, explanation, and Guardian-eligible IDs. It rejects any attempt to
  persist authority or live effects.

Prediction status is not collapsed into a boolean. The supported states are
`simulated`, `proposed`, `attempted`, `observed`, `verified`, `contradicted`,
and `unknown`. An unknown, stale, missing, or contradictory source remains
explicit.

## Durable ledger and migration

`ImaginationStore` adds SQLite schema version 1 tables prefixed with `phase9_`:

- candidates, predictions, simulations, failure modes, critics, scores;
- decisions, observations, calibrations, and immutable audit events.

Writes are idempotent by stable record ID. Existing payloads can be updated
only through the same record identity, and duplicate identical writes are
no-ops. `backup()` uses SQLite backup; `rollback()` drops only `phase9_`
tables and leaves unrelated application tables untouched. `recover_interrupted`
marks an interrupted simulation as simulation `failed` with
`simulation_outcome=unknown`, never as a real-world failure.

## Deterministic simulation layers

`DepartureFixture` injects the tangible Phase 9 faults:

- Home Assistant cover feedback reports success while the state remains open;
- `Marc` and `Guest` are present;
- a calendar event has an address but is likely remote;
- traffic is unavailable and only a labeled fallback buffer is allowed.

`DepartureSimulationSuite` runs symbolic, personal-event, service dry-run,
and digital-twin layers. `DigitalTwinHomeAssistant` records simulated calls
but has no network client and no live-effect counter. `BoundedCodexExperiment`
copies a primary workspace under a separate experiment root, writes a seeded
failure marker there, and returns simulation-scoped evidence. Windows-invalid
candidate IDs are sanitized before the bounded directory is created.

## Candidate lifecycle

```text
proposed -> simulated -> valid | modified | rejected
valid / modified -> guardian_pending -> guardian_revalidated
any non-terminal candidate -> canceled
```

The seeded departure run produces three strategy families:

1. `full-away-automation`: alarm, cover, and light changes. Rejected for
   household presence, cover contradiction, weak calendar attendance evidence,
   and strict traffic dependency.
2. `presence-aware-preparation`: initially attempts device preparation. The
   cover command is modified into an observation-only read and the unsafe
   common-area effect is removed; the candidate becomes valid.
3. `degraded-service-consent`: reads the cover, records attendance as unknown,
   uses a labeled traffic fallback, and asks Marc before consequential change.

Only the final valid/modified set is handed to `GuardianBoundary`. A digital
twin's success or a specialist/candidate agreement cannot create a grant.

## Codex isolation

Phase 9 does not use the live Codex app-server. The fixture boundary models the
required production contract: an implementation candidate runs only in a
bounded experimental root, can fail deliberately, and cannot write the
primary workspace. A later adapter may use a real Codex worktree only if it
retains the same root check, seeded-failure semantics, approval broker, and
no-authority guarantee.

## Review and rollback

Review the evidence pack under `docs/evidence/phase-9/` and run the focused
acceptance test. To roll back a local Phase 9 database, call
`ImaginationStore.backup()` first, then `ImaginationStore.rollback()`; the
existing Phase 0–8 tables are not targeted. Source rollback is additive file
removal only after review—no reset, clean, checkout, or deployment is part of
this phase.

Phase 10 has not started. No live Home Assistant, channel, calendar, traffic,
weather, presence, deployment, or external notification effect occurred during
this implementation.
