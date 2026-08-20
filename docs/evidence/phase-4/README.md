# Phase 4 evidence — durable events and shadow situations

Status: **accepted by Marc on 2026-08-16 for the deterministic fixture slice**.

Phase 2 remains accepted with deferred, non-blocking `P2-LIVE-01`. Phase 3
remains accepted. Neither status is reopened by this Phase 4 work.

## Reproducible source and configuration

- Repository: `C:\Users\Marc\Documents\Projects\Ophanim`
- Source snapshot: `e171045f550ebdf8aaed6fcf91f9c3231e428e69` plus the existing
  uncommitted working-tree changes across the project and this Phase 4 review
- Python environment: repository `.venv`
- Phase 4 database: isolated SQLite file created by each test
- Schema: additive `phase4_schema_migrations` version 1
- Production feature flag: `OPHANIM_PHASE_4_ENABLED=0` by default
- Live Codex App Server and computer-use execution: not used
- Provider: deterministic fixture events only

The Phase 4 implementation is in:

- `src/openjarvis/situations/models.py` — event identity, provenance,
  sensitivity, taint, and typed delivery records;
- `src/openjarvis/situations/journal.py` — durable event, lease, retry,
  acknowledgement, dead-letter, raw retention, situation, and evaluation state;
- `src/openjarvis/situations/detector.py` — deterministic Departure replay;
- `src/openjarvis/server/situation_routes.py` — opt-in read-only API;
- `tests/acceptance/phase_4/` and `tests/server/test_situation_routes.py` —
  acceptance, failure-injection, restart, and API evidence.

## Review update — 2026-08-16

The Phase 4 review found and fixed boundary defects that the original fixture
pack did not exercise:

- only confirmed appointments and an explicitly home, present Marc observation
  can satisfy the Departure detector;
- malformed calendar, traffic, preparation, and API input is blocked and
  explained without an exception-driven detector failure;
- active situations become `uncertain` when later evidence removes confidence;
- expired delivery leases cannot be acknowledged or nacked by stale workers;
- normalized event rows reject both updates and deletes;
- secret-like normalized and raw payload keys are redacted or not retained, and
  raw retention expiry is enforced on access as well as explicit purge;
- future untrusted message timestamps cannot advance the detector threshold.

Review cleanup removed one unused private `_blocked` parameter. The Phase 4
exports, server route, feature-flag branch, and shared `Situation` fields were
searched and remain used.

## Exact verification results

Final focused Phase 4 and API checks:

```text
14 passed in 0.98s
```

Complete acceptance regression:

```text
115 passed in 13.53s
```

Focused command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-review-phase-4-final-focused `
  tests\acceptance\phase_4 `
  tests\server\test_situation_routes.py
```

Complete acceptance command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-review-phase-4-final-all tests\acceptance
```

Neighboring regression command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-review-phase-4-final-neighbor `
  tests\acceptance\phase_3 `
  tests\acceptance\phase_5\test_living_world_model.py `
  tests\cognition\test_contracts.py `
  tests\server\test_situation_routes.py
```

Result:

```text
38 passed in 1.84s
```

Frontend:

```text
Vitest: 6 passed
Production build: passed
```

Commands:

```powershell
cd frontend
npm test -- --run
npm run build
```

The Vite build retains the existing advisory that the main minified chunk is
above the 500 kB warning threshold. It is not a build failure.

Touched-file quality checks:

```text
Ruff check: passed
Ruff format --check: passed
Python compileall: passed
```

Ruff emitted an existing cache-write permission warning but returned success.

## Tangible acceptance result

The normal replay produced exactly one situation:

```text
type: Departure
status: active
confidence: 0.95
evidence: calendar appointment, traffic estimate, Marc-at-home presence
mode: shadow
side effects: 0
Guardian authorizations: 0
```

Duplicate append returned `inserted = false`. Ingestion remained durable before
detector evaluation. Replaying the database after close/reopen returned the
same situation ID, evidence IDs, and source provenance. Cancellation changed
the same idempotent situation to `closed` instead of creating a second record.

## Scenario coverage

| Scenario | Result |
|---|---|
| Normal departure | One active `Departure` |
| Duplicate event | Deduplicated; no second delivery or situation |
| Late/out-of-order event | Observed-time replay produced the same result |
| Stale evidence | No new situation; reason persisted |
| Contradictory presence | No new situation; contradiction persisted |
| Missing evidence | No situation; missing evidence reason persisted |
| Unconfirmed appointment | No situation; confirmation reason persisted |
| Wrong presence location | No situation; not-at-home reason persisted |
| Malformed typed evidence | No situation; invalid-input reason persisted |
| Cancellation | Existing situation closed |
| Marc leaves early | Silent when no situation existed |
| Marc does not leave | Shadow situation remains; no action |
| Consumer crash/restart | Lease reclaimed after expiry |
| Retry/dead-letter | Bounded attempts, dead state, explicit redrive |
| Detector crash injection | Failure surfaced and delivery dead-lettered |
| Database restart/replay | Deterministic one-record result |
| Raw retention/sensitivity | Expiry purged; sensitive raw dropped |
| Prompt injection | Untrusted payload; no authority or action |
| Expired worker lease | Stale acknowledgement/nack rejected |
| Event mutation attempt | Update/delete rejected by append-only triggers |

## Metrics

- Correct Departure situations in the acceptance oracle: `1`
- Duplicate Departure situations: `0`
- Unauthorized actions or Guardian authorizations: `0`
- Event ingestion/detection side effects: `0`
- Required evidence IDs on the normal situation: `100%`
- Replay result stability after restart: `100%` for the fixture
- Phase 4/API focused tests: `14 passed`
- Complete acceptance regression: `115 passed`
- Neighboring regression: `38 passed`
- Frontend suite: `6 passed`
- Production build: passed, with the existing chunk-size warning

These are deterministic fixture metrics, not a seven-day live precision or
recall estimate.

## Security and privacy impact

Untrusted payload text is tainted data and cannot create authority. The Phase 4
package has no path to the Guardian action registry, executor, notification
sink, Codex control surface, or computer-use adapter. Sensitive raw payloads
are not retained; non-sensitive raw payload retention is explicit and
time-bounded. Normalized secret-like keys are redacted before persistence.

The opt-in API is still subject to the server's existing API authentication
middleware when a server API key is configured. No live external connector was
enabled for this phase.

## Limitations and next work

- Only the deterministic Departure vocabulary is implemented; real source
  adapters and broader situation types are Phase 5+ work.
- The detector is intentionally deterministic and fixture-shaped; no LLM or
  learned rule generation is allowed.
- The frontend Operations page now has a read-only shadow-situation panel; a
  richer live situation dashboard and source drill-down are later work. The
  backend API and persisted evaluation records remain the authoritative review
  surface for this slice.
- Seven-day shadow operation, production threshold tuning, and precision/recall
  measurement remain outstanding before any live suggestion policy.
- The installed Codex App Server readiness follow-up remains operational work,
  not a Phase 4 prerequisite.

The seven-day live-shadow gate and real source adapters remain deferred; they
are not represented as deterministic fixture evidence.

## Rollback

1. Stop Phase 4 event producers and set `OPHANIM_PHASE_4_ENABLED=0` before
   restarting the server.
2. Preserve the Phase 4 SQLite file as evidence if needed; it is separate from
   the Guardian database and does not contain Guardian authority state.
3. To remove Phase 4 state, stop all callers, make a backup, and remove only
   the explicitly configured `OPHANIM_PHASE4_DB` file. Restore the backup to
   re-enable replay; do not delete a workspace or broad configuration
   directory.
4. The existing Phase 0–3 action, Guardian, Codex, and connector ledgers are
   unchanged and continue to define authority and execution behavior.

## Acceptance record

Marc accepted Phase 4 on 2026-08-16 in the Codex task. The acceptance covers
the deterministic fixture slice verified above. Deferred follow-up remains the
seven-day live-shadow gate, real source adapters, and live precision/recall
validation; no live side effects are enabled by this acceptance.
