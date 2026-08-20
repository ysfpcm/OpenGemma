# Phase 12 handoff — policy packs and safe embodiment path

## Status

Phase 12 is implemented and review-ready, not accepted by Marc. It is the
final active roadmap phase for this workstream. No post-Phase-12 roadmap work
started.

## Earlier phase status from repository evidence

These are repository-recorded statuses, not a new acceptance claim:

- Phase 0: technically verified, still `verifying` until Marc accepts it.
- Phase 1: verified; its evidence records Marc acceptance on 2026-08-15.
- Phase 2: accepted by Marc with deferred non-blocking follow-up `P2-LIVE-01`.
- Phase 3: accepted by Marc on 2026-08-15.
- Phase 4: technically complete deterministic slice; not accepted by Marc.
- Phase 5: deterministic implementation slice complete; not accepted by Marc.
- Phase 6: accepted by Marc on 2026-08-16; live source validation and real-world execution remain deferred to Phase 7.
- Phase 7: live standby wired/review-ready; not accepted by Marc.
- Phase 8: accepted by Marc on 2026-08-16; live Codex/production RAG
  validation remains deferred follow-up.
- Phase 9: accepted by Marc on 2026-08-16 for the deterministic local slice;
  live adapters, ledger views, held-out replay measurement, and future live
  policy review remain deferred.
- Phase 10: implementation-complete/review-ready; Marc acceptance pending.
- Phase 11: implementation-complete/review-ready; Marc acceptance pending.

## Files and schema

Added `src/openjarvis/policy_packs/` with typed contracts, durable store,
generic registry/manager, Arrival Guardian, digital embodiment pack, fixtures,
and deterministic simulator/controller. Added migration
`migrations/0001_phase12.sql` with a phase-only down migration. Added the
Phase 12 design, evidence README, JARVIS assessment, acceptance tests, and
this handoff.

The schema retains manifest/signature/digest, dependencies, versions,
migrations, grants, lifecycle events, replay keys, intents, controller
decisions, actuator commands/attempts, safety stops, communication state,
simulator state, and simulated verifications. Pack uninstall retains audit and
replay provenance. SQLite backup/restore and phase-only rollback are tested.

## Pack behavior

`PackManager` verifies trusted publisher signature and integrity digest,
compatibility, dependencies, required release gates, reversible migrations,
and authority bounds before install. Installation is persisted inactive for
production. Upgrade, downgrade, rollback, uninstall, reinstall, revocation,
and duplicate replay are durable and idempotent. A failed migration keeps the
previous version installed and records the failure.

Arrival Guardian is an ordinary pack with five replay fixtures. Arrival,
guest-present, and vacation produce bounded proposals; false-presence and
service-outage fail closed. No Arrival-specific Guardian or executor branch
exists.

The digital pack contains local Codex observe/navigate/fixture-write templates
and declares only the low-risk simulated actuator action. It adds no network,
writable-root, production, or Guardian authority implicitly.

## Embodiment lifecycle and safety

World-model/VLA input is a proposal-only `EmbodimentIntent`. A deterministic
controller compiles it into a restricted virtual-arm/virtual-gripper command,
then uses the existing Guardian ledger for:

`Plan → Authorization → ActionAttempt → Verification`

The independent `SafetyEnvelope` checks allow-lists, bounded motion, fresh and
non-contradictory perception, connected communication, and emergency-stop
state before authorization and again as Guardian preconditions. Timeout,
cancellation, stale perception, unsafe motion, contradiction, communication
loss, revocation, duplicate command, and emergency stop refuse or stop without
dispatch. Emergency stop halts the simulator and outranks model output.

Restart after authorization resumes exactly once; terminal replay/reinstall
does not add an attempt. Direct motor/torque commands are rejected. The
simulator is local and deterministic; all results retain
`simulation_only=true`/`live_effects=false`. No live physical or consequential
external effect occurred.

## Verification

```text
.venv\Scripts\ruff.exe check src/openjarvis/policy_packs tests/acceptance/phase_12
All checks passed.

.venv\Scripts\ruff.exe format --check src/openjarvis/policy_packs tests/acceptance/phase_12
All files formatted.

.venv\Scripts\python.exe -m compileall -q src/openjarvis/policy_packs tests/acceptance/phase_12
Compilation passed.

.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp-review-phase-12-after-format tests/acceptance/phase_12
17 passed.

.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp-review-phase-12-all tests/acceptance
139 passed, 3 failed; all three failures are unrelated Phase 10 Learning
Laboratory failures documented in the Phase 12 evidence README.
```

Shared Guardian/action regression checks were 11 and 82 passed. The prior
297-pass/9-failure server lane is historical evidence and was not rerun because
Phase 12 does not change server code.

## Security, privacy, and rollback

Pack metadata rejects secrets, credentials, raw diffs, and hidden-reasoning
claims. Evidence stores provenance, uncertainty, contradiction, sensitivity,
and safe summaries. No remote summary or pack metadata receives private
world-model contents. No unrestricted self-improvement or automatic production
activation exists.

For rollback, first create a SQLite backup, uninstall the pack to remove live
availability while retaining evidence, use `PackManager.rollback()` to restore
the prior verified version, or use `Phase12Store.rollback()`/backup restore for
the Phase 12 schema. The down migration does not remove unrelated tables.

## Manual review and limitations

- Review publisher-key lifecycle before distributing non-fixture packs.
- Decide whether a future integration should explicitly map installation grants
  to Guardian grants; Phase 12 intentionally does not create Guardian authority
  implicitly.
- Review UI treatment of causal timelines; the contract/evidence layer is
  complete, but no new production UI was added.
- The prior server lane recorded nine documented unrelated failures; it was not
  rerun because Phase 12 does not change server code.
- Physical hardware, live adapters, neuromorphic research, and brain-computer
  interfaces are deferred and not dependencies.
