# Continue Ophanim work on Phase 11

Continue Ophanim work on Phase 11 in:

`C:\Users\Marc\Documents\Projects\Ophanim`

Phase 10 is implementation-complete and review-ready, but Marc has not yet
formally accepted it unless repository evidence explicitly says so. Do not
claim Phase 10 acceptance on Marc's behalf. Phase 11 is now the active phase.

## Read before coding

Read these first:

- `docs/design/2026-08-15-ophanim-master-implementation-plan.md`
- `docs/design/2026-08-16-phase-10-learning-laboratory.md`
- `docs/evidence/phase-10/README.md`
- `docs/evidence/phase-10/jarvis-experience.md`
- `docs/design/2026-08-15-phase-9-imagination-engine-digital-twin.md`
- `docs/evidence/phase-9/README.md`
- `docs/evidence/phase-9/jarvis-experience.md`
- existing Phase 0–10 evidence and acceptance tests
- current git status

Inspect the existing implementation, especially:

- `src/openjarvis/cognition/`
- `src/openjarvis/world_model/`
- `src/openjarvis/executive/`
- `src/openjarvis/guardian/`
- `src/openjarvis/imagination/`
- `src/openjarvis/learning/laboratory/`
- `src/openjarvis/codex_observer/`
- `src/openjarvis/server/`
- existing channels, desktop, voice, and notification boundaries
- `tests/acceptance/`
- `tests/server/`
- `tests/codex_observer/`

## Repository safety rules

- Inspect `git status` before editing.
- Preserve all existing user changes.
- Do not run `git reset`, `git clean`, `git checkout`, or discard unrelated
  work.
- Existing Phase 2–10 work may be uncommitted; do not assume uncommitted means
  disposable.
- Do not commit anything unless Marc explicitly asks.
- Do not claim acceptance for any phase on Marc's behalf.
- Do not start Phase 12.
- Before coding, inspect the existing contracts and propose a concise
  implementation plan in the commentary channel.

## Phase 11 goal — Ambient personal mission control

Make Ophanim useful across Marc's day, not only while the desktop interface is
open. Voice, desktop, phone, and remote channels must feel like one coherent,
interruptible Ophanim identity while preserving explicit human control.

## Required Phase 11 scope

### Interruptible presence

- low-latency voice input and text-to-speech with working-memory continuity;
- barge-in and cancel during speech and active missions;
- configured desktop, phone, and channel mission updates;
- optional consented screen or visual context with explicit active indicators;
- affect hypotheses may exist only as short-lived uncertain context;
- affect or inferred emotion must never grant authority or change plans alone.

### Personal mission portfolio

Join, with provenance and sensitivity preserved:

- calendar and commute;
- commitments and important messages;
- active projects and repository health;
- Codex mission status;
- Guardian situations and household exceptions;
- available time, cost, and attention.

Generate a ranked “what Ophanim can move forward for you” queue. Do not
automatically start consequential work because the model finds it interesting.
Every queued item must retain goal, source evidence, confidence, uncertainty,
authority scope, budget, and cancellation state.

### Flagship advanced experiences

Implement only bounded, reviewable foundations for:

- Ophanim building Ophanim under Guardian supervision;
- a personal R&D laboratory;
- digital chief of staff and morning readiness;
- home reliability engineering;
- Windows recovery command;
- autonomous quality science;
- security stewardship;
- ambient project continuity.

Do not broaden the scope into Phase 12 policy packs or physical embodiment.

## Tangible Phase 11 test

Create a deterministic local acceptance fixture for this sequence:

1. Marc starts a two-hour Codex mission through Ophanim.
2. Marc leaves the PC.
3. Ophanim sends only material milestone updates.
4. Codex reaches a scoped decision.
5. Marc asks by voice or phone: “What changed and what is blocked?”
6. Ophanim answers from the mission ledger.
7. Marc says: “Fork the safer approach and stop the original if its tests still
   fail.”
8. Ophanim executes only the authorized steering behavior.
9. Marc returns and reviews the complete causal timeline and artifacts.

The first implementation lane should be deterministic and local. Live phone,
voice, channel, calendar, notification, deployment, or Codex steering effects
must remain disabled unless a separate explicit approval boundary is designed,
tested, and requested.

Prove at minimum:

- mission state remains continuous across leaving and returning;
- summaries match the durable mission ledger;
- unchanged-state notification spam is suppressed;
- barge-in, cancel, and emergency stop outrank lower-priority work;
- remote answers redact sensitive commands, diffs, credentials, and paths;
- fork/stop steering is scoped to the exact mission, turn, workspace, and
  authority grant;
- a declined, stale, canceled, budget-exceeded, or revoked request cannot act;
- remote steering cannot create new Guardian authority;
- no live external effect occurs in the deterministic acceptance lane.

## Required deliverables

- additive Phase 11 design document;
- `docs/evidence/phase-11/README.md`;
- `docs/evidence/phase-11/jarvis-experience.md`;
- typed contracts for ambient presence, interruption, mission update,
  notification policy, mission portfolio item, remote query, remote steering,
  redaction, channel consent, and continuity/acknowledgment state;
- durable persistence for presence, mission summaries, update delivery,
  interruptions, remote queries, steering decisions, and redaction evidence;
- deterministic fixtures for desktop departure/return, remote query, milestone
  deduplication, barge-in, cancellation, and scoped fork/stop behavior;
- material-update ranking and notification suppression;
- explicit channel consent and privacy/redaction checks;
- exact mission/turn/workspace/authority scoping for steering;
- Guardian emergency-stop and revocation integration at the boundary;
- tests for restart, duplicate updates, stale mission state, disagreement,
  cancellation, budget, redaction, consent, authority, emergency stop, and
  remote channel failure;
- no hidden self-modification, authority growth, deployment, or external
  notification path.

## Non-negotiable safety requirements

- No hidden self-modification.
- No automatic production model, prompt, skill, or policy activation.
- No remote channel may silently create or expand Guardian authority.
- No affect inference grants authority or changes a plan by itself.
- No stale mission summary is presented as current fact.
- No simulation or replay success is real-world success.
- No duplicate milestone or steering action after restart.
- Emergency stop, cancellation, revocation, and barge-in must fail closed.
- Preserve explicit uncertainty, contradiction, provenance, sensitivity, consent,
  redaction, and authority state.
- Do not expose secrets, credentials, sensitive diffs, or hidden chain-of-
  thought claims through remote summaries.
- No live or consequential external effects in the initial acceptance lane.

## Completion handoff requirements

At the end, provide a complete handoff covering:

- implementation status and whether Phase 11 is review-ready or accepted;
- files changed and schema/migration changes;
- ambient presence and interruption lifecycle;
- mission portfolio ranking and material-update suppression;
- remote query, redaction, consent, and channel-failure behavior;
- fork/stop steering, Guardian scope, revocation, cancellation, and emergency
  stop behavior;
- restart and duplicate-delivery behavior;
- test commands and exact results;
- tangible mission-control fixture evidence;
- baseline versus new behavior;
- regressions and unrelated pre-existing failures;
- security and privacy impact;
- rollback procedure;
- manual review items and limitations;
- explicit confirmation that Phase 12 has not started;
- explicit confirmation that no live or consequential external effects occurred.
