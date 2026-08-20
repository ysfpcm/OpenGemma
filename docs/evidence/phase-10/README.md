# Phase 10 evidence — Learning Laboratory

**Status:** implementation-complete and review-ready; Marc acceptance pending
**Acceptance lane:** deterministic local fixtures only
**Phase 11:** separate work exists in the repository; it is outside this review
and Marc acceptance remains pending

## Configuration and source scope

- Project: `C:\Users\Marc\Documents\Projects\Ophanim`
- Python: repository `.venv` on Windows
- Phase 10 package: `src/openjarvis/learning/laboratory/`
- Schema migration: `phase10-learning-laboratory` version 1
- Artifact signer: test/local HMAC key in the acceptance fixture
- External connectors: none
- Existing dirty worktree: preserved; no reset, clean, checkout, commit, or
  unrelated-file discard was performed

## Test commands and results

Focused Phase 10 acceptance command:

```powershell
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-review-phase-10-final-focused-4 `
  tests\acceptance\phase_10
```

Result: **17 passed**.

Static and import checks:

```powershell
.venv\Scripts\ruff.exe check `
  src\openjarvis\learning\laboratory `
  tests\acceptance\phase_10
.venv\Scripts\ruff.exe format --check `
  src\openjarvis\learning\laboratory `
  tests\acceptance\phase_10
.venv\Scripts\python.exe -m compileall -q `
  src\openjarvis\learning\laboratory `
  tests\acceptance\phase_10
.venv\Scripts\python.exe -c `
  "from openjarvis.learning.laboratory import *; print('import-ok')"
```

Results: **Ruff passed; format check passed; compileall passed; import passed**.

Complete acceptance regression command:

```powershell
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-review-phase-10-all-final-2 `
  tests\acceptance
```

Result: **146 passed**.

Neighboring learning regression command:

```powershell
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-review-phase-10-learning-regression `
  tests\learning
```

Result: **705 passed, 15 failed, 5 skipped**. The failures are pre-existing
checkpoint/source-tree path-guard expectations, torch availability, and a
Windows path-separator expectation; they do not import or exercise
`openjarvis.learning.laboratory` and are not attributed to Phase 10.

## Tangible correction evidence

The acceptance test `test_tangible_correction_replay_shadow_activation_and_exact_rollback`
proves:

1. Marc's correction is immediately durable as relationship and procedural
   `MemoryEvidence`, with an `ImmediateAdaptation` for belief and current-plan
   revision.
2. A `Procedure` and `LearningCandidate` are generated from the correction.
3. Daytime, sunset, overnight, guest-present, and contradictory-command cases
   replay deterministically.
4. The candidate reaches `replay_passed` and `shadow` with
   `real_effect=false`, `authority_created=false`, and no external effects.
5. Approval without Marc is blocked; explicit Marc approval is persisted.
6. A signed artifact is staged, but activation requires a separate explicit
   confirmation.
7. Activation makes the entry light `on` only in the intended sunset/overnight
   context. Daytime, guest-present, and contradictory-command behavior remain
   `off`.
8. Verified rollback transitions the candidate to `rolled_back`, clears the
   active artifact pointer, and restores sunset behavior to the prior `off`
   baseline exactly.

## Codex isolation and seeded regression evidence

`test_codex_candidate_isolated_and_seeded_forgetting_is_rejected` runs the
candidate mission-template across four historical deterministic fixtures. The
fourth deliberately changes an ambiguous-command decision from `ask` to
`execute` and widens the approval boundary. The result is:

- `ReplayRun.status=failed`;
- protected regression: `codex-history-004`;
- forgetting regression: `codex-history-004`;
- candidate status: `rejected`;
- primary workspace digest unchanged;
- candidate marker written only below `experiments\phase10-*`;
- no artifact, promotion decision, Guardian authorization, or deployment.

## Protected-capability and forgetting evidence

Home Assistant replay protects the guest-present and contradictory-command
behaviors. Codex replay protects the approval boundary and explicitly seeds a
forgetting case. Any candidate mismatch, protected regression, forgetting
regression, unauthorized action, duplicate action, stale source, disagreement,
or budget overrun blocks promotion.

## Persistence, restart, duplicate, cancellation, and rollback evidence

The acceptance suite covers:

- SQLite restart and preservation of correction state;
- idempotent duplicate correction persistence;
- duplicate replay delivery returning the original durable run;
- restart recovery of a running replay to failed/unknown;
- canceled replay remaining sandboxed rather than promoting;
- stale-source and disagreement rejection;
- budget-exceeded rejection;
- migration backup/rollback that preserves an unrelated application table;
- artifact tamper detection;
- artifact activation-scope and runtime-pointer tamper rejection;
- direct candidate status-bypass rejection at the store boundary;
- exact behavior rollback, including restoration of a previously active
  artifact.

## Review hardening and cleanup

The review fixed only Phase 10 laboratory behavior: timestamp-independent
duplicate identity for corrections and replay fixtures; immutable conflicting
correction rejection; store-level candidate lifecycle enforcement; stricter
model-experiment isolation and consent checks; failed replay regression records;
signed fixture-only artifact scope; runtime-pointer fail-closed evaluation; and
rollback verification against the restored durable pointer. The unused
`TypeVar` import/declaration in the Phase 10 contract module was removed as safe
dead cleanup.

No unrelated project cleanup was removed. Public-looking backup/version helpers,
shared provenance fields, and older learning code were retained because they
remain part of the current API or their dynamic/compatibility use was not
ambiguous.

## Security and privacy impact

The new durable records add local SQLite evidence containing correction text,
source IDs, context, candidate parameters, replay outcomes, and artifact
provenance. Sensitivity labels and causal parents remain attached. Model
experiments require explicit, privacy-reviewed consent scoped to trace IDs and
offline use. Codex experiments are copied into an isolated root and cannot
write the primary workspace. No new network or external-data path is opened.

The laboratory cannot construct a Guardian grant and rejects candidate payloads
that request authority or primary-workspace writes. Simulation and shadow
success are not action attempts or real-world verification.

## Rollback procedure

For a local Phase 10 database, first call `LaboratoryStore.backup()` to a review
path, then call `LaboratoryStore.rollback()`. This removes only `phase10_*`
tables. For a promoted deterministic artifact, call
`LearningLaboratory.rollback(candidate_id, reason=...)`; verify the persisted
`RollbackRecord` has `exact_restore=true`, `rollback_verified=true`, and
`authority_scope_restored=true`. No source rollback or deployment is part of
this phase.

## Manual review items and limitations

- Marc should review the candidate scoring threshold and whether a given future
  policy-pack kind may ever enter a live lane.
- The HMAC signer is a local test boundary, not a production key-management
  solution.
- The current replay evaluator is deterministic fixture code, not a live
  Home Assistant or Codex adapter.
- The current store uses a typed JSON ledger rather than domain-specific SQL
  tables; contract validation remains at the Python boundary.
- The current `evaluate_policy()` demonstration is fixture-only and cannot
  authorize or execute an external action.
- Production model training, artifact registry integration, and an operational
  approval UI remain future work behind this boundary.

## No-effects confirmation

No live or consequential external effect occurred. No Home Assistant call,
notification, calendar mutation, channel message, deployment, external model
training run, live Codex app-server session, Guardian authorization, or primary
workspace write occurred. No Phase 11 file was changed in this review; Phase 11
remains outside the active acceptance target and Marc acceptance remains
pending.
