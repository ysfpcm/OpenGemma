# Phase 4 — Durable events and shadow situations

Status: **accepted by Marc on 2026-08-16 for the deterministic fixture slice**

Date: 2026-08-15

Companion architecture: [Codex local-computer integration architecture](2026-08-15-codex-local-computer-architecture.md)

## Decision

Phase 4 adds a provider-neutral event journal and a deterministic-first
situation detector. It can persist and explain observations and produce typed
`Situation` records, but it cannot authorize, execute, notify, steer Codex, or
call a connector. The Guardian Kernel remains the already-accepted Phase 3
authority boundary.

The low-latency in-process `EventBus` remains available for telemetry. A
Phase 4 caller must first normalize and commit an event to the journal; policy
evaluation and replay operate only on committed records.

## Scope and contracts

### Durable event envelope

`openjarvis.situations.DurableEvent` is schema version 1 and contains:

- stable `event_id` and `identity_key`;
- event type, source, optional source event ID, observed time, and ingestion time;
- normalized JSON payload;
- source provenance, quality, sensitivity labels, and taint labels;
- optional raw payload held separately under an explicit retention window.

If a source event ID exists, identity is `source + source_event_id`. Otherwise
identity is a SHA-256 digest of the event type, source, observed time, and
normalized payload. A duplicate append returns the existing event and never
creates a second delivery or situation.

The normalized event row is append-only. Raw payloads are stored in a separate
retention table, are never retained for sensitive events, and are purged after
their configured expiry. Normalized sensitive payload keys such as tokens,
passwords, credentials, and auth values are redacted before persistence.

### Journal and delivery state

`DurableEventJournal` uses additive SQLite schema version 1 tables:

- `phase4_events` — immutable normalized events and their causal ordering;
- `phase4_raw_event_payloads` — optional expiring raw data;
- `phase4_deliveries` — per-consumer lease, owner, attempt, acknowledgement,
  retry, and dead-letter state;
- `phase4_situations` — idempotent typed situation projection with revision;
- `phase4_evaluations` — latest explainable detector result, including blocked
  reasons and cited evidence.

Consumers claim events with a bounded lease. Acknowledgement requires the
current lease owner. A crash leaves the lease to expire; a later worker can
reclaim it. `nack` returns a bounded retry to pending and transitions the
delivery to `dead` at the attempt limit. Dead letters can be inspected and
explicitly redriven.

### Shadow situations

The shared `Situation` contract now has explicit `status`, `uncertainty`, and
`source_provenance` fields in addition to its existing confidence and evidence
IDs. Phase 4 uses the statuses `active`, `uncertain`, and `closed`.

`ShadowSituationDetector` currently implements `departure-shadow-v1` for a
typed fixture vocabulary:

- `calendar.appointment` — appointment, start, route, preparation, status;
- `traffic.estimate` — route, travel minutes, and buffer;
- `presence.home` — person, home location, and presence state.

The detector replays events in observed-time, sequence, and event-ID order.
It requires a confirmed appointment, fresh traffic, fresh presence, no
contradiction, Marc still at home, and a reached departure threshold. A valid
result is one deterministic `Departure` record with one idempotency key per
appointment, confidence, uncertainty, evidence IDs, and source provenance.

Cancellation closes an existing record. Early departure remains silent when no
shadow situation was created. Stale, missing, contradictory, invalid, and
not-yet-due evidence is visible in the persisted evaluation report but does
not create a new situation. An already-active situation becomes `uncertain`
when later evidence removes confidence without providing a terminal
cancellation.

## Boundary to Guardian Kernel

The detector has no Guardian, action registry, executor, authorization, or
notification dependency. Its only writes are journal state, evaluation state,
and typed shadow situations. A situation is evidence for a future planning
phase; it is not an `ActionProposal`, `Authorization`, or execution request.

The optional `/v1/situations/events`, `/v1/situations/replay`, and
`/v1/situations` endpoints expose persistence and shadow results only. Phase 4
is disabled by default and can be enabled for a local fixture or controlled
shadow run with `OPHANIM_PHASE_4_ENABLED=1` and `OPHANIM_PHASE4_DB=...`.

Prompt injection or authority-shaped text inside an event remains untrusted
payload data. The detector reads only the typed fields needed by its fixture
contract and never treats text as policy, a grant, or a command.

## Acceptance oracle

The deterministic corpus in
`tests/acceptance/phase_4/test_durable_events_and_shadow_situations.py` replays
the normal sequence:

| Observed time | Event | Required meaning |
|---|---|---|
| 07:00 UTC | Calendar appointment at 08:45, 30-minute preparation | Appointment evidence |
| 07:40 UTC | 20-minute traffic estimate plus 10-minute buffer | Travel evidence |
| 07:46 UTC | Marc present at home | Fresh presence and threshold reached |

The threshold is 07:45 UTC. The oracle requires exactly one active `Departure`
situation, all three evidence IDs, confidence `0.95`, deterministic replay
after database restart, and zero action or Guardian authorization records.

The same test pack covers duplicates, late/out-of-order delivery, stale and
missing evidence, contradictory observations, cancellation, early departure,
consumer crash/restart, bounded retry, dead-letter redrive, raw retention,
database restart, detector failure injection, and prompt injection.

## Deliberate non-scope

- Live calendar, traffic, presence, camera, message, Home Assistant, Codex, or
  computer-use connectors;
- live Codex App Server work or desktop execution;
- LLM-generated situation rules;
- Guardian authorization or any action from a situation;
- seven-day shadow operations and production precision measurement;
- Phase 5 world-model consolidation and layered memory.

## Acceptance record

Marc accepted Phase 4 on 2026-08-16 for the deterministic fixture slice.
Deferred follow-up remains the seven-day live-shadow gate, real source adapters,
and live precision/recall validation; these are not enabled by this acceptance.
