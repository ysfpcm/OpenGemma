# Phase 12 — Policy packs and safe embodiment path

**Status:** implementing / verifying; not accepted by Marc
**Scope:** additive, deterministic, local-only policy portability and simulated embodiment
**Authoritative roadmap:** `docs/design/2026-08-15-ophanim-master-implementation-plan.md`

## Goal and frozen acceptance lanes

Phase 12 proves that the existing causal and safety contracts generalize to
installable policy packs and a simulated embodiment without adding a policy
branch to Guardian Kernel or the verified executor.

The frozen lanes are:

1. Install Arrival Guardian, verify its signed manifest and bounded authority,
   replay arrival, guest-present, vacation, false-presence, and service-outage
   fixtures, then exercise upgrade, downgrade, rollback, uninstall, and
   reinstall. Invalid signatures, dependencies, migrations, stale or
   contradictory evidence, duplicate events, and revoked installations fail
   closed.
2. Submit a low-risk virtual actuator intent through the digital embodiment
   pack. The deterministic controller compiles it into the existing
   `Plan → Authorization → ActionAttempt → Verification` lifecycle, verifies
   the virtual state independently, and refuses or stops every injected fault.
   Restart, replay, reinstall, and duplicate delivery never issue a second
   command.

The acceptance lane has no network, credentials, production activation, or
physical-device adapter. A simulation result is always labelled
`simulation_only=true` and `live_effects=false`.

## Boundary

`openjarvis.policy_packs` is a new additive boundary:

```text
PackManifest + signature + digest
              |
       PackRegistry / PackManager
              |
     installed pack + installation grant
        /                         \
 Arrival policy replay       Digital mission templates
                                      |
                         proposal-only embodiment intent
                                      |
                    deterministic controller + safety envelope
                                      |
               existing Guardian ActionRegistry / ActionLedger
                                      |
                 local simulator + independent verification
```

The registry only maps a verified `(pack_id, version)` to pack code. The pack
does not create Guardian grants. `PackManager` validates an installation grant
against the manifest, and the caller must still provide the corresponding
Guardian capability grant before a consequential typed action can be
authorized.

## Typed pack contract

`PolicyPackManifest` retains:

- pack identity, publisher, semantic version, signature, and SHA-256 integrity
  digest;
- compatibility range, schema version, dependencies, and reversible migration
  descriptors;
- triggers, required sources, detector identity, evidence requirements, typed
  actions, risk and consequence classes;
- autonomy defaults and installation authority limits;
- safe UI/explanation metadata;
- replay fixture references, fault cases, release gates, and lifecycle behavior.

The local fixture signer uses HMAC-SHA256 and a trusted publisher map. The
signature is not a remote trust service; it is a deterministic acceptance
boundary that rejects missing, tampered, unknown-publisher, or mismatched
signatures before installation.

`InstallationGrant` is narrower than the manifest. It can restrict actions,
capabilities, risk/consequence class, targets, expiry, and revocation. A pack
cannot use an action that is absent from its manifest or outside its grant.
Installation is persisted as inactive (`production_active=false`); Phase 12
does not auto-activate a production policy.

## Arrival Guardian

Arrival Guardian is implemented in `arrival.py` as an ordinary pack. It is not
named in Guardian Kernel, `ActionRuntime`, or the core action registry.

The pack declares three low-risk, low-consequence proposal types:

- `arrival.prepare_entry`
- `arrival.notify_household`
- `arrival.set_away_scene`

Its detector accepts only fresh, non-contradictory, provenance-bearing evidence
with confidence at least 0.7. False presence and service outage are explicit
fail-closed cases. Replay produces proposals and a causal explanation, but
authorization, attempts, and verification are explicitly recorded as not
requested/not attempted/not applicable because replay has no live effect.

## Digital embodiment pack

The digital pack packages bounded local Codex templates for observe, navigate,
and isolated fixture-write missions. Templates declare writable roots and
network policy; all are local, non-production, and non-networked. They cannot
silently activate a model, prompt, skill, policy, or production mission.

The pack also declares `embodiment.simulated_actuator`, but that declaration is
only an allow-listed typed proposal. It does not itself execute an actuator.

## Simulation-first embodiment

`EmbodimentIntent` is proposal-only and rejects direct motor/torque fields. The
controller deterministically compiles it to the restricted command vocabulary:

- actuators: `virtual_arm`, `virtual_gripper`;
- operations: `nudge`, `open`, `close`, `hold`;
- bounded command value and stable idempotency key;
- local virtual state only.

`SafetyEnvelope` independently checks actuator and operation allow-lists,
bounded step, fresh perception, contradiction, connected communication, and
emergency-stop state. The same checks are repeated as Guardian preconditions
immediately before execution. The simulator has no socket, device, motor,
vehicle, appliance, home-security, wearable, or external connector.

The real-time controller owns command compilation. There is no direct
LLM/VLA-to-motor function; direct motor command calls raise
`EmbodimentSafetyError`.

## Durable schema and rollback

`Phase12Store` applies `0001_phase12.sql` and records migration version 1 in
`phase12_schema_migrations`. It persists:

- installed pack state, retained versions, signatures/digests, grants,
  lifecycle events, migration state, and rollback state;
- replay dedupe keys and safe causal evaluations;
- intents, actuator commands, controller decisions, communication state,
  safety stops, simulator state, and simulated verifications.

Lifecycle evidence and actuator attempts are immutable by SQLite triggers.
`backup()` uses SQLite backup, and `rollback()` removes only Phase 12 tables;
unrelated application tables are retained. Pack upgrade/downgrade/migration
failure retains the last installed version and writes a failed lifecycle event.

## Security, privacy, and uncertainty

- Pack metadata and event metadata reject credential/token/password fields,
  hidden-reasoning claims, and raw diff fields.
- Evidence retains source, observed time, confidence, contradiction,
  sensitivity, provenance, and a safe summary rather than private world-model
  contents.
- Replays and simulations cannot be reported as real-world success.
- Revocation, cancellation, stale or contradictory evidence, communication
  loss, controller timeout, budget-like refusal, and emergency stop fail
  closed.
- Emergency stop is owned by the independent controller and Guardian; it
  halts the virtual actuator and outranks pack logic and model proposals.
- Duplicate policy events and embodiment idempotency keys are durably
  suppressed before a second proposal or command can be issued.

## Deliberate non-goals

No live home or physical connection, real robot, motor, vehicle, door, garage,
stove, weapon, security system, wearable, neuromorphic hardware, brain-computer
interface, production policy activation, unrestricted self-improvement, or
post-Phase-12 roadmap work is included.
