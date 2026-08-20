# Phase 5 evidence — Living World Model and layered memory

Status: **accepted by Marc on 2026-08-16 (America/New_York)**.

The 2026-08-16 audit and acceptance are recorded in the [Phase 5 review addendum](review-2026-08-16.md).
The remaining live-validation gaps are deferred follow-up, not reopened
acceptance conditions for the deterministic slice.

Phase 2 remains accepted with non-blocking `P2-LIVE-01`. Phase 3 remains
accepted. Phase 4 remains implementation-complete with operational validation
pending. None of those statuses is reopened by Phase 5.

## Implementation scope

- `src/openjarvis/world_model/models.py` — typed entities, observations,
  beliefs, evidence, provenance, revisions, goals, commitments, predictions,
  memory items, and importer candidates;
- `src/openjarvis/world_model/store.py` — additive SQLite schema, deterministic
  materialization, contradiction preservation, decay, historical replay,
  source tombstoning, and restart durability;
- `src/openjarvis/world_model/world.py` — world-model facade, typed Phase 4 and
  ContextStore projections, seven layered-memory adapters, and non-destructive
  projections for existing facts, RAG, sessions, behavior, and traces;
- `src/openjarvis/world_model/importer.py` — deterministic local history
  inventory, exclusion, candidate extraction, review, correction, export, and
  deletion;
- `src/openjarvis/server/world_routes.py` — opt-in read-only inspection API
  plus typed observation ingestion;
- `tests/acceptance/phase_5/test_living_world_model.py` — tangible acceptance,
  contradiction/provenance, restart/replay, importer review/delete, sensitive
  exclusion, prompt-injection, layered memory, decay, and Phase 4 projection;
- `tests/server/test_world_routes.py` — API explanation and reconstruction
  contract.

## Exact deterministic verification

Focused Phase 5 acceptance and API checks:

```text
13 passed
```

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-review-phase-5-focused-final-3 `
  tests\acceptance\phase_5 `
  tests\server\test_world_routes.py
```

Touched Phase 5 Python checks:

```text
Ruff check: passed
Ruff format --check: passed
Python compileall: passed
```

The final Phase 5-only rerun passed 8 tests. The combined Phase 5 and route
contract lane passed 13 tests and reported two existing FastAPI `on_event`
deprecation warnings.

Complete acceptance regression:

```text
116 passed
```

Neighboring Phase 4/server regression:

```text
14 passed
```

The selected `tests\core\test_config_phase5.py` neighboring check still has
one pre-existing harness failure because its pytest `tmp_path` is inside the
source tree and the `OPENJARVIS_HOME` guard correctly rejects it.

Frontend:

```text
Vitest: 6 passed
Production build: passed
```

The Vite build retains the existing advisory that the main minified chunk is
above the 500 kB warning threshold. It is not a build failure.

The broader existing context/memory/RAG/session/trace lane was also sampled:

```text
197 passed, 40 failed, 29 skipped, 4 errors
```

Those frontend and broader-lane results are retained as historical
implementation evidence and were not rerun for this backend-only review.

## Acceptance result

The fixture demonstrates:

- current priority is `Ophanim Guardian Loop`;
- its supporting observation, provenance, and evidence ID are present;
- the older `Project A` observation is preserved as contradicting evidence
  until its source is deleted;
- `nodalUI` is a hypothesis and never becomes the current fact;
- source deletion clears stored conversation text and tombstones source-backed
  observations and semantic memory;
- current correction, goals, commitments, provenance, uncertainty, and
  hypotheses survive SQLite close/reopen;
- confidence decays deterministically for stale evidence;
- no Guardian table, authorization, action, or executor is created by memory;
- a tainted Phase 4 payload is projected only through a typed observation.

## Security and privacy

Sensitive categories and secret-like content are rejected before accepted
memory. Imported text is untrusted and cannot create authority. The protected
self-model rejects untrusted writes. The Phase 5 package has no Guardian,
executor, connector, Codex, computer-use, or notification dependency.

## Limitations and deferred validation

- The importer is a deterministic fixture grammar, not a general export
  parser or LLM extraction system.
- The API is intentionally read-only for inspection; a richer frontend is
  deferred until the model contract is accepted.
- Existing stores are projected through explicit methods, not migrated or
  deduplicated across databases yet.
- Confidence calibration and held-out personal retrieval measurement require
  real, consented data and remain outstanding.
- Live Phase 4 source operation, live Codex work, computer use, and Phase 6
  planning remain deferred.
- Marc explicitly accepted Phase 5 on 2026-08-16 (America/New_York). Deferred
  follow-up remains live personal-history/connectors, confidence calibration,
  held-out retrieval measurement, and real-world validation.

## Rollback

Disable `OPHANIM_PHASE_5_ENABLED`, restart, and preserve or remove only the
configured `OPHANIM_PHASE5_DB` after making a backup. Existing Phase 0–4 and
Guardian databases are independent and remain available.
