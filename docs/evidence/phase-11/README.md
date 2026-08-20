# Ophanim Phase 11 evidence — Ambient personal mission control

**Status:** implementation-complete and review-ready; Marc acceptance pending
**Acceptance lane:** deterministic local fixtures only
**Phase 10:** not claimed accepted; repository evidence describes it as
implementation-complete/review-ready
**Phase 12:** not started

## Snapshot and boundary

- Python package: `src/openjarvis/ambient/`
- Schema: `phase11_schema_migrations` version 1
- Fixture: `Phase11MissionFixture` in
  `tests/acceptance/phase_11/test_ambient_mission_control.py`
- Runtime: local SQLite, in-memory local channel sink, no live phone, voice,
  calendar, notification, deployment, Home Assistant, or Codex app-server
  effect
- Existing dirty worktree was preserved; no reset, clean, checkout, or commit
  was used

## Test commands

Focused Phase 11 acceptance:

```powershell
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-review-phase-11-final `
  tests\acceptance\phase_11
```

Result: **11 passed in 0.53s**.

Compile check:

```powershell
.venv\Scripts\python.exe -m compileall -q src\openjarvis\ambient
```

Result: passed.

Targeted quality checks:

```powershell
.venv\Scripts\ruff.exe check src\openjarvis\ambient tests\acceptance\phase_11
.venv\Scripts\ruff.exe format --check src\openjarvis\ambient tests\acceptance\phase_11
.venv\Scripts\python.exe -m compileall -q src\openjarvis\ambient
```

Result: **all passed**.

Complete acceptance regression:

```powershell
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-review-phase-11-all `
  tests\acceptance
```

Result: **139 passed, 3 failed in 14.86s**. The three failures are in
`tests/acceptance/phase_10/test_learning_laboratory.py` and concern the
pre-existing Phase 10 working-tree implementation. No Phase 11 test failed,
and Phase 11 did not modify the Phase 10 package or tests.

## Tangible mission-control result

The fixture performs this local sequence:

1. Registers a two-hour Codex mission with a bounded no-network authority
   description.
2. Records Marc at the desktop, then away from the PC.
3. Delivers one material start milestone and suppresses an unchanged heartbeat.
4. Restarts Ophanim's local ledger while Marc is away; recovery marks the
   mission/currentness explicitly rather than inventing a result.
5. Records a scoped decision: the safer approach is available and the original
   tests still fail.
6. Answers “What changed and what is blocked?” from the durable mission
   summary, including the last update sequence and blocker.
7. Accepts the exact pre-existing `codex:fork-stop` scope and records a local
   fork/stop only because `tests_failed=true` in the fixture.
8. Records Marc's return and exposes the complete causal timeline and artifact
   IDs.

The result proves continuous mission identity, restart continuity, milestone
deduplication, query currentness, scoped steering, and no live effect. Local
delivery records are evidence, not external notifications.

## Safety evidence

- Emergency stop outranks barge-in, budget, and other lower-priority work.
- Barge-in cancels speech continuity but does not silently alter the mission.
- Cancel, stale, revoked, declined, budget-exceeded, and stopped steering
  requests do not execute.
- Remote answers and update deliveries require channel consent, redact
  command-like text, diffs, credentials, and paths, and persist only safe
  digests/categories.
- Remote channel failure is durable and does not trigger a steering retry.
- A remote channel cannot register an authority scope.
- Restart recovery preserves an applied emergency stop and marks continuous
  acknowledgments stale until refreshed.
- Steering rechecks consent, exact mission/turn/workspace scope, capability,
  and the bounded condition at execution time, not only at request time.
- Sensitive mission-update text is redacted before it is written to the Phase
  11 ledger; only non-sensitive redaction categories and digests are retained.
- Guardian emergency-stop and revocation callbacks are exercised at the
  boundary when a Guardian control is supplied.
- Portfolio ranking retains source evidence, confidence, uncertainty,
  authority scope, budget, and cancellation state.
- Store restart, duplicate delivery, and Phase-11-only backup/rollback are
  exercised.

## Schema and files

- `src/openjarvis/ambient/contracts.py` — typed Phase 11 contracts.
- `src/openjarvis/ambient/store.py` — SQLite persistence, recovery, audit,
  backup, and rollback.
- `src/openjarvis/ambient/service.py` — consent, redaction, ranking,
  interruptibility, continuity, and exact steering boundary.
- `src/openjarvis/ambient/fixtures.py` — deterministic two-hour mission lane.
- `src/openjarvis/ambient/migrations/0001_phase11.sql` and `.down.sql` —
  additive migration and Phase-11-only rollback.

## Security and privacy impact

Phase 11 adds local durable records for mission summaries, presence,
interruptions, consent, delivery status, remote queries, steering decisions,
portfolio items, and redaction evidence. Remote-facing text is redacted before
the local channel sink. Sensitive text from mission updates is also redacted
before Phase 11 ledger persistence; secret values, credentials, sensitive
diffs, hidden chain-of-thought claims, and workspace paths are not exposed
through the remote answer contract. Exact workspace values remain durable
where required for scope matching. No new network path is opened.

The local sink records payloads only in process memory and has an explicit
`external_effects=[]` assertion. The optional Guardian callback is a boundary
integration for emergency stop/revocation, not a remote authority creator.

## Baseline versus new behavior

Baseline Phase 10 had durable learning-laboratory evidence while ambient
presence, remote mission summaries, channel consent, interruption precedence,
and remote steering were deliberately unchanged. Phase 11 adds these local
contracts and ledgers without changing the Phase 10 learning boundary or the
existing Codex/Guardian routes.

## Rollback procedure

1. Stop the local Ophanim process using the ledger.
2. Call `AmbientStore.backup()` to a review path.
3. Call `AmbientStore.rollback()`; verify unrelated tables remain.
4. Re-run `AmbientStore.migrate()` only when Phase 11 is intentionally
   restored.

No source reset, deployment, external notification, live Codex steering, or
production policy change is part of rollback.

## Manual review items and limitations

- Marc should review materiality thresholds and which future channel consent
  operations may be permitted.
- Marc should review the portfolio scoring weights and whether any live
  connector may later populate each evidence class.
- Voice/TTS, phone delivery, and live Codex fork/stop adapters remain
  intentionally unimplemented behind this local boundary.
- A production authority-scope adapter must verify the referenced Guardian
  grant without allowing the remote channel to mint one.
- This evidence is deterministic simulation/fixture evidence, not real-world
  mission success.

## No-effects confirmation

No live or consequential external effect occurred. No phone call, voice
session, channel notification, calendar mutation, commute lookup, deployment,
Home Assistant call, live Codex steering, Guardian grant creation, or
production policy/model/skill activation occurred. Phase 12 has not started.

## Review cleanup audit

No code was removed in this review. `MIGRATION_VERSION` remains an exported
schema contract, `AmbientStore.restore_backup()` remains a recoverable public
rollback helper, and the local channel adapter remains the deterministic
no-effect acceptance sink. The Phase 11 package is not dynamically registered
or wired to live channels; adding that wiring is outside this phase. Temporary
review directories and unrelated dirty-worktree changes were preserved.
