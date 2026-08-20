# Ophanim Phase 10 — Learning Laboratory

**Status:** additive implementation-complete and review-ready; not accepted by Marc
**Date:** 2026-08-16
**Scope:** immediate memory adaptation, deterministic procedural learning, controlled model-experiment boundaries, promotion, staging, activation, and rollback

## Purpose

Phase 10 lets Ophanim learn from experience while keeping production behavior
bounded and reviewable. A correction is durable evidence immediately, but it is
not a silent prompt, policy, skill, model-weight, or Guardian-authority change.

The implementation is isolated in `openjarvis.learning.laboratory`. It does not
depend on a Guardian instance, a live connector, a notification service, a
deployment client, or a production model handle.

## Learning speeds

### Immediate memory adaptation

`Correction` records Marc's statement, context, source, sensitivity, confidence,
provenance, and causal parents. `MemoryEvidence` records both relationship and
procedural evidence. `ImmediateAdaptation` records the belief and current-plan
revision. All four records are durable immediately. The adaptation contract
explicitly records that production weights, prompts, policies, and skills were
not changed.

### Periodic procedural learning

`Procedure` is extracted from correction evidence. `LearningCandidate` carries
the candidate kind, named outcome, baseline and expected behavior, source
version, provenance, causal parents, sensitivity, reversibility, and an empty
authority scope. Candidate proposals may describe retrieval, routing, prompt,
threshold, policy, procedure, or Codex-template changes, but they cannot create
or expand Guardian authority.

### Experimental model adaptation

Model-adapter candidates cannot enter the sandbox without a `Consent` record
that is explicit, current, privacy-reviewed, purpose-bound, and scoped to the
source trace IDs. The allowed use must include `offline_experiment`. Revoked,
expired, unreviewed, or out-of-scope trace data fails closed.

## Promotion lifecycle

```text
candidate
  -> sandboxed
  -> replay_passed
  -> shadow
  -> approved
  -> staged
  -> active | rejected | rolled_back
```

The SQLite store validates every transition. Replay failure moves a candidate to
`rejected`; cancellation leaves it `sandboxed` so a later bounded retry can be
examined. Approval cannot skip shadow mode. Staging creates a signed artifact
but does not activate it. Activation requires a separate explicit confirmation.

## Replay and shadow gates

`ReplayCase` is a deterministic fixture with a source version, expected result,
tags, sensitivity references, budget cost, cancellation state, and disagreement
state. `ReplayRun` persists each result and the aggregate gate fields:

- named-outcome baseline and candidate pass rates and improvement;
- adversarial and counterfactual replay status;
- privacy and consent status;
- protected-capability and forgetting regressions;
- unauthorized and duplicate action counts;
- stale source, disagreement, budget, and cancellation flags.

`RegressionResult` is persisted for every replay. A candidate cannot pass when a
candidate output misses the expected behavior, a protected capability changes,
a historical behavior is forgotten, a source is stale or contradictory, a
budget is exceeded, or an unauthorized/duplicate action is reported.

`ShadowRun` repeats candidate evaluation without calling a live adapter. The
contract hard-fails construction if `real_effect`, `authority_created`, or
`external_effects` is present. Shadow success is simulation evidence only.

## Approval, signing, staging, and rollback

`PromotionDecision` persists the named outcome, measured improvement, reviewer,
explicit Marc approval, every gate result, provenance, and a verified rollback
check. Approval requires a positive improvement and all safety, privacy,
authority, replay, shadow, provenance, reversibility, and rollback requirements.

`ArtifactVersion` is content-addressed and HMAC-signed by the configured local
review signer. Its activation scope is explicitly
`environment=deterministic-fixture`, `live_external_effects=false`, and
`guardian_authority=false`. The artifact is only `staged` until an explicit
activation call verifies its digest and signature.

Activation persists `RuntimeState`. `evaluate_policy()` reads that durable
pointer and uses the candidate only while its artifact is active. Rollback marks
the artifact `rolled_back`, clears or restores the prior pointer, persists a
`RollbackRecord`, verifies the baseline behavior digest, and transitions the
candidate to `rolled_back`. No Guardian grant is created by any transition.

## Durable schema

Migration `phase10-learning-laboratory` version 1 adds only:

- `phase10_schema_migrations`;
- `phase10_records`, an idempotent typed JSON ledger indexed by kind and status;
- immutable `phase10_audit_events` with idempotency-event protection.

The migration is in
`src/openjarvis/learning/laboratory/migrations/0001_phase10.sql` and its
down-migration. `LaboratoryStore.backup()` uses SQLite backup. Rollback drops
only the `phase10_*` tables and leaves unrelated application tables intact.
Restart recovery marks running replay and shadow records failed with explicit
unknown-outcome evidence rather than inventing success.

## Deterministic tangible test

The fixture teaches:

> “When I leave after sunset, keep the entry light on even if the general Away routine turns other lights off.”

The replay covers daytime, sunset, overnight, guest-present, and contradictory
command cases. The baseline turns the entry light off in every case. The
candidate turns it on only for Marc's sunset/overnight departure, preserves the
general Away off behavior, leaves the guest-present case unchanged, and lets an
explicit contradictory command win. The candidate passes replay and shadow,
requires explicit Marc approval, stages and activates only after a second
explicit confirmation, then rolls back to the exact baseline behavior.

## Codex mission-template boundary

Historical deterministic mission fixtures include a deliberate candidate
forgetting case: the candidate executes an ambiguous command where the baseline
asks and preserves the approval boundary. The experiment copies the primary
workspace under a separate bounded experiment root, writes a marker only there,
and compares the primary workspace digest before and after. The protected
approval-boundary regression rejects the candidate before shadow, approval,
staging, or activation. The path creates no Guardian authorization and no
deployment.

## Explicit non-goals

- no automatic production model, prompt, skill, or policy activation;
- no live Home Assistant, calendar, channel, notification, deployment, or Codex
  app-server effect;
- no primary-workspace write from the Codex experiment;
- no authority growth from a candidate, replay, shadow result, or model output;
- no Phase 11 work as part of this phase; Phase 11 remains a separate review
  target.
