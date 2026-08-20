# Ophanim Phase 11 — Ambient personal mission control

**Status:** additive implementation, review-ready; not accepted by Marc
**Date:** 2026-08-16
**Scope:** deterministic local foundations for continuous mission presence,
interruptibility, material updates, remote queries, and exact-scope steering

## Purpose

Phase 11 extends Ophanim's existing Codex observer, Cognitive Executive,
Imagination Engine, Learning Laboratory, and Guardian boundaries so an active
mission remains understandable when Marc leaves the desktop. The first lane
is intentionally deterministic and local. It does not connect to live voice,
phone, calendar, notification, deployment, Home Assistant, or Codex steering
effects.

The invariant is:

> One Ophanim identity may summarize and prepare a mission across surfaces, but
> only an explicit human decision inside an exact existing authority scope may
> steer it.

## Additive architecture

```text
Codex / executive / fixture evidence
              |
              v
    AmbientMissionControl
      | presence + continuity
      | material update ranking
      | consent + redaction
      | interruption precedence
      | exact remote steering scope
              |
              v
       AmbientStore (SQLite)
       phase11_* tables only
              |
              +--> local channel sink (no external effect)
              +--> existing Guardian stop/revoke boundary, if supplied
```

`openjarvis.ambient` is a coordination and evidence layer, not a second
Guardian. A `RemoteSteering` record can reference a pre-existing
`AuthorityGrantScope`, but the remote channel cannot create, widen, or infer
that scope. Local fixture execution is marked `executed_local` and
`live_effect=false`; it is not Codex success or real-world success.

## Typed contracts

Schema version 1 provides JSON-round-trippable records for:

- `AmbientPresence` — desktop departure/return, source, uncertainty, and an
  active indicator. Affective context, if present in uncertainty, expires as
  context and cannot affect authority.
- `Interruption` — barge-in, cancel, emergency stop, revocation, budget, and
  channel failure with explicit priority and fail-closed status.
- `MissionSummary` and `MissionUpdate` — durable goal, thread/turn/workspace,
  state, blockers, changed fields, evidence IDs, artifacts, budget, and stale
  status.
- `NotificationPolicy` and `UpdateDelivery` — materiality thresholds,
  sensitivity policy, consent, redaction evidence, and duplicate suppression.
- `MissionPortfolioItem` — goal, provenance, confidence, uncertainty,
  authority scope, budget, cancellation state, available time, attention and
  cost constraints, and deterministic ranking score.
- `RemoteQuery` and `RemoteSteering` — channel, consent, exact mission/turn/
  workspace, authority grant reference, response currentness, and decision
  state.
- `RedactionEvidence` and `ChannelConsent` — safe digest, redaction category,
  no-secret persistence assertion, channel operation allow-list, active
  indicator requirement, expiry, and revocation.
- `ContinuityAcknowledgment` — last update sequence, presence state, speech
  state, channel, and whether continuity became stale after restart.

## Lifecycles and safety boundaries

### Presence and interruption

`record_presence()` clears the stale marker only with a new observed local
presence record. `start_speech()` records working-memory continuity without
opening a live microphone or TTS path. `interrupt_speech()` emits a
`barge_in` record and cancels speech while leaving the mission itself intact.

Priority is emergency stop, revocation, cancel, barge-in, budget, then remote
channel failure. A lower-priority request cannot supersede an applied higher-
priority interruption. Emergency stop calls the existing Guardian boundary
when provided. Revocation marks the exact referenced scope revoked and calls
Guardian revocation; neither path grants a remote channel new authority.

### Mission portfolio and update suppression

Portfolio ranking is deterministic and reviewable: urgency and value dominate,
then confidence, time availability, and attention cost. It is a queue of work
Ophanim can propose, never an automatic start signal. Items retain source
evidence, uncertainty, authority, budget, and cancellation state.

Updates are deduplicated by stable `update_id` and delivery identity. A
heartbeat or low-materiality unchanged update is persisted as
`suppressed_unchanged` without calling the local sink. The same delivery ID is
idempotent after restart. Material state changes, blockers, decisions, and
completion records can pass the policy threshold; every delivery remains
local in this lane.

### Remote query, consent, and redaction

Phone/voice/remote query and notification operations require a persisted
`ChannelConsent` matching the exact mission and channel. Consent is
operation-specific, expiry-aware, revocable, and requires an active indicator.
The local desktop surface is the explicit local indicator; no remote channel
gets that exemption.

Remote answers are composed only from the durable mission summary. They report
the last known update sequence and explicitly say `stale` after restart rather
than presenting it as current fact. Commands, diffs, credentials, secret-like
values, and workspace paths are removed before delivery. Sensitive text in
mission updates is redacted before ledger persistence; only a safe digest and
redaction categories are persisted as redaction evidence. Exact workspace
values remain durable where required for authority-scope matching.
Unavailable remote channels create a durable failed query and a channel-failure
interruption. No steering retry is attempted.

### Exact fork/stop steering

The fixture supports the bounded instruction “fork the safer approach and stop
the original if its tests still fail.” Before approval, the service checks:

1. active consent covers `steer` on the exact channel and mission;
2. a pre-existing `AuthorityGrantScope` covers the exact mission, turn,
   workspace, and `codex:fork-stop` capability;
3. the mission is current, not canceled, revoked, budget-exceeded, or under
   emergency stop; and
4. the requested condition is explicit.

At execution the same checks run again. A stale, declined, canceled,
budget-exceeded, revoked, or stopped request cannot act. A passing fixture
condition records a local fork ID and original-stop decision only in the
ledger; `live_effect=false` remains hard-coded. The remote request never
creates a Guardian grant.

## Durable schema, restart, and rollback

`AmbientStore` creates `phase11_schema_migrations`, `phase11_records`, and
immutable `phase11_audit_events` at migration 1. All required Phase 11 record
kinds are durable in the typed record ledger. Writes are idempotent by stable
contract ID; identical duplicate writes are no-ops, while contradictory update
IDs and sequence numbers are rejected. Calling `recover_interrupted()` after a
restart marks active steering as failed/unknown and active mission/continuity
records stale; an applied emergency stop remains fail-closed when a new
service instance loads the ledger. Recovery never invents an external outcome
or repeats a delivery.

Call `AmbientStore.backup()` before rollback. `rollback()` removes only
`phase11_*` tables and leaves unrelated application tables unchanged. Re-run
`migrate()` to restore the Phase 11 schema.

## Bounded flagship foundations

The same contracts can represent evidence for Ophanim building Ophanim, a
personal R&D laboratory, readiness, home reliability, Windows recovery,
quality science, security stewardship, and ambient project continuity. This
phase only supplies the mission-control ledger and safety boundary. It does
not activate self-modification, policy packs, physical embodiment, live
channels, or external effects.

## Review limitations

- Voice input and text-to-speech integration remain disabled; the local speech
  lifecycle is a continuity/barge-in foundation, not an audio adapter.
- The local channel sink is an acceptance double, not a phone, desktop push, or
  notification service.
- The authority-scope record represents a pre-existing grant reference; the
  implementation does not mint or inspect a live Guardian grant itself.
- Portfolio ranking is deterministic fixture policy, not a production
  calendar, commute, message, or cost connector.
- Redaction is a conservative summary boundary; sensitive source artifacts are
  never intended to be placed in remote summaries.

Phase 12 policy packs and embodiment work have not started.
