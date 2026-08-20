# Phase 9 evidence — Imagination Engine and digital twin

**Status:** accepted by Marc on 2026-08-16 (America/New_York) for the deterministic local slice
**Acceptance lane:** deterministic local fixtures only
**Phase 10:** not started

## Configuration and snapshot

- Python project: `OpenJarvis` source tree under `C:\Users\Marc\Documents\Projects\Ophanim`
- Phase 9 schema: `phase9_schema_migrations` version 1
- Fixture: `DepartureFixture(scenario_id="phase9-departure-seeded-faults")`
- Runtime boundary: no live Home Assistant, channel, calendar, traffic,
  weather, presence, deployment, or Codex app-server connection
- Existing dirty worktree preserved; no reset, clean, checkout, or unrelated
  file overwrite performed

## Test commands

Focused acceptance command used on Windows:

```powershell
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .pytest-phase9-run2 `
  tests/acceptance/phase_9/test_imagination_engine.py
```

Original implementation run result: **7 passed**.

The system Python did not have pytest installed; the repository `.venv` was
used. The default pytest temporary directory was permission-blocked on this
machine, so the test used a repository-local basetemp. This is an environment
setup note, not a product failure.

Review rerun (2026-08-16):

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-review-phase-9-final-focused `
  tests\acceptance\phase_9
```

Result: **9 passed**. The review added cycle rejection, prediction-link,
replay-scope, duplicate-delivery, rollback-isolation, and bounded-workspace
checks.

Additional checks:

```powershell
.\.venv\Scripts\ruff.exe check src\openjarvis\imagination tests\acceptance\phase_9
.\.venv\Scripts\ruff.exe format --check src\openjarvis\imagination tests\acceptance\phase_9
.\.venv\Scripts\python.exe -m compileall -q src\openjarvis\imagination
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-review-phase-9-final-all `
  tests\acceptance
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-review-phase-9-final-guardian-regression `
  tests\acceptance\phase_3 tests\acceptance\phase_7
```

Results: ruff check passed, format check passed, compileall passed, complete
acceptance **134 passed**, and the targeted Phase 3/7 Guardian/departure
regression **49 passed**.

The repository already contained unrelated uncommitted changes and prior
temporary evidence trees; they were preserved.

## Review corrections

- Symbolic simulation now rejects dependency cycles rather than treating them
  as schedulable.
- Simulation runs retain actual deterministic prediction IDs, not expected
  observation IDs under the `prediction_ids` field.
- Replay recording requires a non-empty source and `replay` evidence scope;
  replay value comparison does not conflate booleans with integers.
- Duplicate replay delivery is a no-op by stable calibration identity.
- A repeated bounded Codex experiment refuses an existing workspace instead
  of deleting it.
- Candidate, decision, score, observation, and calibration contracts reject
  duplicate or structurally unsafe records. The unused private
  `SimulationFinding` helper was removed.

## Tangible departure result

| Candidate | Result | Evidence |
|---|---|---|
| Full away automation | rejected before Guardian | cover feedback contradiction; guest present; remote-likely calendar; strict traffic outage |
| Presence-aware preparation | modified, then valid | cover command replaced with read-only observation; common-area effect removed |
| Degraded-service consent | selected and Guardian-eligible | cover read only; physical attendance `unknown`; labeled fallback; consent request |

The diversity check reports three strategy families, three distinct diversity
signatures, and three distinct action shapes. This is substantive diversity,
not wording variation.

Every candidate compiles into `TypedAction` records linked to
`ExpectedObservation` records. Every prediction retains confidence,
provenance, assumptions, causal parents, and a later calibration path.

## Prediction and calibration evidence

The acceptance suite replays three outcomes:

- cover state `open` -> `verified` for the observation-only prediction;
- physical attendance `unknown` -> explicit `unknown`, not false;
- fallback buffer observed as `42` rather than the predicted `20` ->
  `contradicted` with calibration error `1.0`.

Simulation outcomes remain in the simulation ledger and never become real
action attempts or verified effects. Restart recovery converts a running
simulation to `failed` / `simulation_outcome=unknown` with an explicit
"real-world outcome unknown" explanation.

## Codex failure evidence

The isolated experiment copies a fixture primary workspace into a bounded
`experiments/phase9-*` directory, writes a seeded failure marker, and records:

- `simulation_outcome=failure`;
- `evidence_scope=simulation`;
- `real_effect=false`;
- `authority_created=false`;
- unchanged primary workspace digest;
- an experiment path contained under the experiment root.

No Guardian authorization is created by this path, and no deployment or
external action occurs.

## Schema, migration, and rollback

`ImaginationStore` creates the additive `phase9_*` tables at migration 1,
supports SQLite backup, idempotent record writes, immutable audit events, and
Phase-9-only rollback. The acceptance suite verifies backup, rollback, and
re-migration.

## Security and privacy impact

The new layer stores candidate parameters, expected observations, source IDs,
failure evidence, and calibration data in a dedicated local SQLite ledger.
Simulation records are explicitly tainted as simulation evidence. No new
network path is opened. The digital twin is not an adapter for the configured
live Home Assistant client. Candidate consensus and predicted success do not
change Guardian grants.

## Limitations and manual review

- The current Phase 9 adapter is deterministic and local; it does not connect
  to live services or the Codex app-server.
- The server/UI does not yet expose Phase 9 ledger views.
- Real traffic/calendar/presence adapters must be integrated only behind the
  same stale/unknown/contradiction semantics.
- Marc should review the candidate scoring weights, fallback buffer policy,
  notification wording, and whether any future live lane may use a given
  action type.
- Phase 7 and Phase 8 remain pending Marc's explicit acceptance; this evidence
  pack does not claim acceptance for them.

## No-effects confirmation

No live or consequential external effect occurred. No real Home Assistant or
channel call, external notification, deployment, calendar mutation, traffic
request, weather request, presence action, or live Codex worktree was used.

## Acceptance record

Marc explicitly accepted Phase 9 on 2026-08-16 (America/New_York) for the
deterministic local acceptance lane. Deferred follow-up remains live service
and Codex adapters, Phase 9 ledger views, held-out personal replay measurement,
and Marc's review of scoring weights, fallback policy, notification wording,
and future live action types.
