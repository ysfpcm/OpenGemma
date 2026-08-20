# Phase 12 evidence — policy portability and safe embodiment

**Status:** review-ready implementation evidence; Marc acceptance is pending.
**Phase:** 12, final active roadmap phase
**Acceptance version:** 1
**Source snapshot:** working tree after the additive Phase 12 review fixes; no commit was created
**Migration:** `phase12_schema_migrations` v1 from `src/openjarvis/policy_packs/migrations/0001_phase12.sql`
**Rollback:** SQLite backup and phase-only down migration verified by acceptance test
**Live effects:** none; no physical or consequential external effect occurred

## Implementation

- `src/openjarvis/policy_packs/contracts.py` — typed manifests, signatures,
  dependencies, migrations, grants, evidence, causal timelines, intent,
  actuator, safety, controller, communication, stop, and verification records.
- `src/openjarvis/policy_packs/store.py` — durable Phase 12 state, backup,
  restore, rollback, dedupe, and immutable evidence persistence.
- `src/openjarvis/policy_packs/manager.py` — generic registry and lifecycle
  manager for install, upgrade, downgrade, rollback, uninstall, reinstall,
  replay, authority bounds, and fail-closed validation.
- `src/openjarvis/policy_packs/arrival.py` — Arrival Guardian first-party pack.
- `src/openjarvis/policy_packs/digital.py` — bounded Codex/local mission
  template and simulated-actuator pack foundation.
- `src/openjarvis/policy_packs/embodiment.py` — deterministic controller,
  restricted local simulator, independent safety envelope integration, and
  emergency stop.
- `tests/acceptance/phase_12/test_policy_packs_and_embodiment.py` — frozen
  portability, lifecycle, fault, restart, duplicate, rollback, and no-branch
  acceptance fixtures.

## Exact verification commands

Run from `C:\Users\Marc\Documents\Projects\Ophanim`:

```text
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp-review-phase-12-after-format tests/acceptance/phase_12
```

Result: **17 passed**. The review additions cover provenance-bearing and
future/stale evidence, registered implementation identity, evaluation safety
labels, typed proposal inputs, communication persistence, and revocation during
restart recovery.

```text
.venv\Scripts\ruff.exe check src/openjarvis/policy_packs tests/acceptance/phase_12
.venv\Scripts\ruff.exe format --check src/openjarvis/policy_packs tests/acceptance/phase_12
.venv\Scripts\python.exe -m compileall -q src/openjarvis/policy_packs tests/acceptance/phase_12
```

Result: **All checks passed**.

The pre-change regression lane was:

```text
.venv\Scripts\python.exe -m pytest --basetemp .tmp-phase12-baseline tests/acceptance tests/codex_observer
```

Result before Phase 12 edits: **108 passed, 1 warning**. The warning was a
pytest cache permission warning. A broader 414-test attempt using the default
temp location produced 405 setup errors because this environment denies access
to `C:\Users\Marc\AppData\Local\Temp\pytest-of-Marc`; it did not reach test
assertions. The repository-local `--basetemp` lane is the meaningful baseline.

The complete acceptance regression lane in this review was:

```text
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp-review-phase-12-all tests/acceptance
```

Result: **139 passed, 3 failed**. All three failures are outside Phase 12 in
`tests/acceptance/phase_10/test_learning_laboratory.py`: artifact integrity
verification, the missing `_existing` replay path, and the model-adaptation
privacy fixture. No Phase 12 source is on those paths. The prior 297-pass/
9-failure server result remains historical evidence and was not rerun because
Phase 12 does not change server code.

The shared runtime regression lanes selected because the simulator uses the
Guardian and action lifecycle were:

```text
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp-review-phase-12-neighbor-guardian tests/acceptance/phase_3/test_guardian_kernel.py tests/acceptance/phase_3/test_phase_3_integrations.py
```

Result: **11 passed**.

```text
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp-review-phase-12-neighbor-cognition tests/cognition/test_actions.py tests/cognition/test_contracts.py
```

Result: **82 passed**.

## Portability results

The five deterministic Arrival fixtures produce:

| Fixture | Result | Evidence |
|---|---|---|
| arrival | proposed | fresh presence/calendar/connectivity evidence |
| guest-present | proposed | explanation-only bounded notification |
| vacation | proposed | reversible away-scene proposal |
| false-presence | blocked | contradictory/false-presence suppression |
| service-outage | blocked | explicit outage fail-closed behavior |

Duplicate delivery returns `replayed` and does not add a second replay record.
Install, upgrade, downgrade, rollback, uninstall, and reinstall lifecycle
events are retained. Invalid signature, authority widening, seeded migration
failure, and missing live installation all remain blocked.

## Embodiment results

An allowed `virtual_arm` nudge creates one typed proposal and one Guardian
authorization, one action attempt, and one independent simulator verification.
The persisted simulator state reports `live_effects=false`.

The fixture lane covers:

- unsafe motion and out-of-envelope values;
- stale and contradictory perception;
- communication loss;
- deterministic controller timeout;
- revoked installation authority;
- emergency stop and halted actuator state;
- duplicate intent delivery;
- restart after authorization and before execution;
- replay/reinstall-safe idempotency;
- direct motor-command rejection.

After restart, the authorized action resumes once, verifies once, and a
duplicate delivery produces no second attempt.

## Regression and review status

The focused Phase 12 lane and targeted quality checks are green. The complete
acceptance lane is 139 passed with the three documented Phase 10 failures above;
the failures are outside the Phase 12 implementation and frozen acceptance
scenario. This evidence pack does not claim Marc acceptance for Phase 12,
Phase 11, or any earlier phase.

## Rollback and uninstall

1. Stop any local Phase 12 process and call `Phase12Store.backup()` to create a
   SQLite backup.
2. Use `PackManager.uninstall(pack_id)` to remove live pack availability while
   retaining lifecycle and replay provenance.
3. Use `PackManager.rollback(pack_id)` after a verified prior version exists.
4. For a full Phase 12 schema rollback, call `Phase12Store.rollback()` or
   restore the backup. The down migration drops only Phase 12 tables and does
   not delete unrelated application tables.

## Deferred and manual review items

- Review publisher key management before any non-fixture distribution.
- Review whether installation grants should be linked to a separate Guardian
  grant record in a future integration; Phase 12 intentionally does not create
  Guardian authority implicitly.
- Review UI presentation of policy timelines; the contract and replay evidence
  exist, but no new production UI is required for this phase.
- No live adapter or hardware validation is appropriate for this phase.
- Neuromorphic and brain-computer-interface research remains deferred and is
  not a dependency.
