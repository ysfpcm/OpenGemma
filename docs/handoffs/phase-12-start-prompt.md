# Finalize Ophanim with Phase 12

Continue Ophanim work on Phase 12 in:

`C:\Users\Marc\Documents\Projects\Ophanim`

Phase 12 is now the active and final roadmap phase for this workstream:
**Policy packs and safe embodiment path**. The authoritative source is:

`docs/design/2026-08-15-ophanim-master-implementation-plan.md`

Phase 11 has an additive deterministic implementation and evidence pack under
`docs/evidence/phase-11/`. Do not infer or claim Marc acceptance for Phase 11
or any earlier phase unless repository evidence explicitly records it. Do not
start any post-Phase-12 roadmap work.

## Read before coding

Read these first:

- `docs/design/2026-08-15-ophanim-master-implementation-plan.md`
- the complete Phase 12 section in that master plan;
- `docs/evidence/phase-11/README.md`;
- `docs/evidence/phase-11/jarvis-experience.md`;
- `docs/design/2026-08-16-phase-11-ambient-personal-mission-control.md`;
- `docs/evidence/phase-10/README.md`;
- `docs/evidence/phase-10/jarvis-experience.md`;
- `docs/design/2026-08-16-phase-10-learning-laboratory.md`;
- `docs/design/2026-08-15-phase-9-imagination-engine-digital-twin.md`;
- `docs/evidence/phase-9/README.md`;
- existing Phase 0–11 designs, evidence, acceptance tests, and phase status
  records;
- current git status.

Inspect the existing implementation, especially:

- `src/openjarvis/cognition/` — plans, authorizations, attempts,
  verification, and truthful lifecycle;
- `src/openjarvis/guardian/` — registry, grants, revocation, emergency stop,
  preconditions, and independent verification;
- `src/openjarvis/planning/` — typed plans and contextual authority;
- `src/openjarvis/imagination/` — simulation and digital-twin boundaries;
- `src/openjarvis/learning/laboratory/` — candidate, replay, promotion, and
  rollback boundaries;
- `src/openjarvis/departure/`, `src/openjarvis/world_model/`, and
  `src/openjarvis/ambient/` — reusable policy/situation/mission contracts;
- `src/openjarvis/codex_observer/` — bounded Codex mission templates and
  workspace authority;
- `src/openjarvis/server/` and frontend surfaces only where they explain
  policy-pack installation or causal timelines;
- `tests/acceptance/`, `tests/server/`, `tests/codex_observer/`, and all
  existing phase regression fixtures.

## Repository safety rules

- Inspect `git status` before editing.
- Preserve all existing user changes, including uncommitted Phase 2–11 work.
- Do not run `git reset`, `git clean`, `git checkout`, or discard unrelated
  changes.
- Do not commit anything unless Marc explicitly asks.
- Do not rewrite the Guardian Kernel or verified executor with
  Arrival-specific or embodiment-specific branches.
- Before coding, freeze the Phase 12 acceptance scenarios, record the current
  baseline, identify schema/security/migration impact, and propose a concise
  implementation plan in the commentary channel.
- Keep Phase 12 bounded, additive, reviewable, and simulation-first.

## Phase 12 goal

Prove that Ophanim's existing causal contracts and safety boundaries generalize
to installable policy packs and a simulated embodiment without weakening core
authority, verification, privacy, uncertainty, or emergency-stop behavior.

The same causal language must continue to explain digital, home, and simulated
physical effects:

`Plan → Authorization → ActionAttempt → Verification`

## Required Phase 12 scope

### Guardian policy packs

Create an additive, installable policy-pack boundary. A pack must declare and
retain provenance for:

- pack ID, name, version, signature, publisher, and integrity digest;
- dependencies, compatibility, schema version, and migrations;
- triggers and required sources;
- situation detector and evidence requirements;
- supported typed actions and their risk/consequence classes;
- autonomy defaults and installation-scoped authority limits;
- UI/explanation metadata;
- deterministic replay fixtures, fault cases, and release gates;
- install, upgrade, downgrade, uninstall, and rollback behavior.

Build **Arrival Guardian** as the second first-party pack. Arrival Guardian
must be a pack, not a conditional branch in Guardian Kernel, the verified
executor, or the core action registry. Its authority cannot exceed explicit
installation grants, and it must fail closed when dependencies, signatures,
fresh evidence, migrations, or safety checks are invalid.

### Digital embodiment packs

Package mature Codex mission templates and bounded PC operations using the
existing plans, authorizations, attempts, and verification contracts. A pack
may describe capabilities and templates, but it cannot silently add authority,
network access, writable roots, or production activation.

### Simulation-first physical embodiment

Implement only a deterministic local simulator with:

- a restricted, low-risk simulated actuator vocabulary;
- VLA/world-model intent as a proposal only;
- a deterministic real-time controller that owns actuator commands;
- an independent physical-safety envelope and emergency stop;
- explicit stale-perception, duplicate-command, unsafe-motion, timeout, and
  communication-loss handling;
- no direct language-model or VLA motor commands;
- no real robot, motor, vehicle, door, garage, stove, weapon, security,
  wearable, or other physical device connection.

Neuromorphic hardware and brain-computer interfaces remain optional research
branches and are not Phase 12 dependencies.

## Frozen tangible acceptance scenarios

### Policy portability scenario

1. Install Arrival Guardian through the pack boundary.
2. Verify manifest, signature, version, dependency, migration, and bounded
   installation authority.
3. Replay arrival, guest-present, vacation, false-presence, and service-outage
   scenarios.
4. Verify supported actions and explanations use the common causal timeline.
5. Upgrade, downgrade, uninstall, and reinstall the pack.
6. Verify no Arrival-specific branch was added to Guardian Kernel or the
   verified executor.
7. Seed invalid signature, missing dependency, stale evidence, contradictory
   presence, migration failure, duplicate event, and revoked installation
   cases; every case must fail closed and remain reversible.

### Embodiment contract scenario

1. Submit a low-risk virtual actuator intent through a simulated embodiment
   pack.
2. Have the deterministic controller compile it into the existing
   `Plan → Authorization → ActionAttempt → Verification` records.
3. Verify an allowed command succeeds and independently verifies in the
   simulator.
4. Inject unsafe motion, stale perception, lost communication, duplicate
   command, controller timeout, revoked authority, and emergency stop.
5. Verify the independent safety controller refuses or stops the actuator
   regardless of model output.
6. Restart at each durable boundary and prove no duplicate command, lost
   authorization, false verification, or unsafe resumed motion.

The initial acceptance lane must remain deterministic and local. Simulation
success is not real-world success. No live physical or consequential external
effect is authorized by this prompt.

## Non-negotiable safety requirements

- No unrestricted recursive self-improvement.
- No automatic production model, prompt, skill, policy, or pack activation.
- No policy pack may modify or bypass Guardian Kernel safety branches.
- No pack may create or widen Guardian authority outside its installation
  grant.
- No direct LLM/VLA-to-motor control.
- Independent safety envelope and emergency stop outrank model output,
  pack logic, and resumed work.
- Communication loss, stale perception, timeout, contradiction, revocation,
  cancellation, and budget exhaustion fail closed.
- No physical device, robot, vehicle, home-security, appliance, wearable, or
  other live actuator connection in the acceptance lane.
- Preserve provenance, uncertainty, contradiction, sensitivity, consent,
  authority scope, migration version, signature evidence, and rollback state.
- No simulation or replay result may be reported as real-world success.
- No duplicate action after restart, retry, reinstall, or replay.
- No secrets, credentials, hidden chain-of-thought claims, sensitive diffs, or
  private world-model contents may escape through pack metadata or remote
  summaries.
- Existing Phase 0–11 acceptance behavior remains a regression requirement.

## Required deliverables

- additive Phase 12 design document;
- `docs/evidence/phase-12/README.md`;
- `docs/evidence/phase-12/jarvis-experience.md`;
- typed pack manifest, signature/integrity, dependency, migration, install
  state, autonomy/defaults, and release-gate contracts;
- typed embodiment intent, simulated actuator command, safety-envelope,
  controller decision, communication state, and simulated verification
  contracts;
- durable persistence for installed pack state, versions, signatures,
  dependencies, migrations, authority scope, controller decisions, actuator
  attempts, safety stops, and causal evidence;
- additive pack manager/registry with clean install, upgrade, downgrade,
  uninstall, reinstall, and rollback;
- Arrival Guardian policy pack with replay fixtures;
- bounded digital embodiment/Codex mission-template pack foundation;
- deterministic simulated actuator and independent safety controller;
- replay/fault-injection fixtures for all scenarios above;
- tests for invalid signatures, dependency failure, migration rollback,
  authority bounds, stale perception, contradiction, duplicate commands,
  communication loss, timeout, cancellation, revocation, emergency stop,
  restart, and no-live-effect behavior;
- proof that core Guardian/verified-executor code has no policy-specific or
  embodiment-specific execution branches.

## Completion handoff requirements

At the end, provide a complete handoff covering:

- Phase 12 implementation status and whether it is review-ready or accepted;
- explicit status of Phase 11 and earlier phases without claiming Marc
  acceptance unless repository evidence says so;
- files changed, pack/schema/migration changes, and rollback scope;
- pack manifest, signature, dependency, installation, upgrade, downgrade,
  uninstall, and authority-boundary behavior;
- Arrival Guardian portability evidence and replay results;
- digital embodiment and simulated actuator lifecycle;
- independent safety-envelope, emergency-stop, stale-perception,
  communication-loss, timeout, and duplicate-command behavior;
- restart, replay, reinstall, and duplicate-delivery/action behavior;
- test commands and exact results;
- tangible policy portability and embodiment fixture evidence;
- baseline versus new behavior;
- regressions and unrelated pre-existing failures;
- security and privacy impact;
- backup, rollback, and uninstall procedure;
- manual review items, known limitations, and deferred research branches;
- explicit confirmation that no post-Phase-12 roadmap work started;
- explicit confirmation that no live physical or consequential external effect
  occurred.

Do not claim Phase 12 acceptance on Marc's behalf. Do not connect a live
embodiment or production policy pack while completing this handoff.
