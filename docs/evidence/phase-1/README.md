# Phase 1 evidence — Codex read-only observer

Phase 1 is **verifying**. The implementation and exit-gate evidence are complete; Marc's acceptance remains pending. Phase 2 has not started.

## Reproducible source and environment

- Source snapshot: `ea629ab625ea1d7cac0b1d2d96f23d921da271dc`
- Date: 2026-08-15, America/New_York
- Codex: `codex-cli 0.147.0`
- Protocol: App Server v2 over local stdio
- Generated schema SHA-256: `3ecdb0cb862982d9b0bfa075edfb46a9f3844ecfac8f40aada14013ddd502015`
- Required methods validated: `initialize`, `thread/start`, `thread/list`, `thread/read`, `thread/resume`, and `turn/start`
- Observer roots: fail closed unless `OPHANIM_CODEX_OBSERVER_ROOTS` is explicitly configured
- Database: `OPHANIM_CODEX_OBSERVER_DB`, or the Ophanim config directory when enabled
- Raw diagnostic retention: 24 hours by default, purged at startup and event ingestion

The frozen acceptance objective and security boundary are in `docs/design/2026-08-15-phase-1-codex-observer-spec.md`.

## What was built

- A supervised Codex App Server client with initialize/initialized handshake, thread start/list/read/resume, exact mission-to-thread/turn mappings, and process-tree failure detection.
- A read-only mission boundary: `sandbox=read-only`, `sandboxPolicy.type=readOnly`, network disabled, approval policy `never`, configured-root enforcement, and automatic denial of every server request.
- A SQLite v1 observer ledger for missions, normalized Phase 0 observations, event fingerprints, traceable milestones, short-lived raw diagnostics, plans, commands, tools, aggregated diffs/files, usage, errors, completion, and interruption state.
- Recursive display redaction and suppression of reasoning contents from normalized/public records.
- Deduplicated milestone publication through Ophanim's event stream and WebSocket bridge.
- Read-only APIs for capabilities, missions, thread history, and recovery.
- A Codex Missions panel in Operations showing objective, phase, elapsed time, progress, plan, commands/tools/files, verification, blockers, usage, and last meaningful activity. It contains no steering, approval, or execution controls.

## Verification results

### Deterministic tests

Command scope: Phase 1 observer/API tests plus Phase 0 contract, action, proactive, event-bus, WebSocket, and truthful-lifecycle regressions.

- Result: **117 passed**
- Phase 1 behaviors covered: forced read-only parameters, no network, approval denial, workspace scope refusal, recursive redaction, reasoning suppression, contract normalization, command/plan/diff/tool/usage/completion tracking, exact deduplication, restart recovery, traceability, raw retention, API exposure, and schema rollback.
- Lint: all changed Python files passed Ruff.
- Pytest emitted one non-product warning because the existing repository `.pytest_cache` directory is not writable.

### Frontend

- Vitest: **6 passed**
- TypeScript and production build: **passed**
- Existing informational build warning: the main generated bundle is over Vite's 500 kB advisory threshold.

### Installed-Codex compatibility

- Real initialize/initialized handshake: passed.
- Real `thread/list`: passed and returned the expected paginated v2 shape.
- Installed-schema generation and required-method validation: passed.

### Tangible interruption/recovery test

The exact frozen objective was launched through `CodexObserverSupervisor` in a real read-only Codex turn. The bridge observed the mission, the spawned App Server process tree was terminated, and a fresh bridge recovered the same persisted thread and classified the prior turn.

- Mission: `mission_fb70a666ee1f416e96152e3ad29760f1`
- Codex thread: `01a00736-68a8-7d71-a8b0-404a16fefcdd`
- Codex turn: `01a00736-6968-7d91-b782-ca37b3ecaac5`
- Normalized unique events: **8**
- Deduplicated milestones: **1**
- Status after injected exit: `interrupted` (`app-server exit -1`)
- Status after fresh bridge inspection: `interrupted`
- Milestones linked to real event fingerprints: **100%**
- Workspace before/after Git state: **unchanged**
- Approval/steer/write operations issued by Ophanim: **0**

Reproduce with:

```powershell
$demoDb = Join-Path $env:TEMP ("ophanim-phase1-live-" + [guid]::NewGuid().ToString('N') + ".db")
.\.venv\Scripts\python.exe scripts\phase_1_live_demo.py --workspace . --database $demoDb --observe-seconds 8
```

## Exit-gate assessment

- Thread, turn, command, tool, plan, aggregated diff/file, usage, error, and completion state: verified in deterministic protocol replay; real thread and turn identifiers verified live.
- No hidden chain-of-thought claims: verified; normalized reasoning payload content is removed and progress is derived only from observable lifecycle events.
- Duplicate unchanged updates: zero in replay.
- Secrets in normalized/display records: zero seeded leaks across nested-key and embedded-string cases.
- Restart corruption: zero; mappings and event links remained intact.
- Steering, approval, write, network, and workspace widening: denied or structurally unavailable.
- Final summaries/milestones traceable to underlying events: 100% in replay and live interruption test.

## Broader baseline

The selected server route baseline remains **12 passed, 2 failed**, unchanged from the pre-implementation baseline. Both failures are existing test-expectation mismatches: the unavailable optional native Rust memory backend truthfully returns HTTP 503, while two older tests allow only 200/500. Phase 1 does not alter memory endpoints.

## Migration and rollback

Migration v1 creates only `codex_*` observer tables. The rollback test removes those tables while proving an unrelated table and its data remain intact.

Operational rollback:

1. Unset `OPHANIM_CODEX_OBSERVER_ROOTS` and restart Ophanim; the routes remain fail-closed and no App Server is launched.
2. Back up the observer database if diagnostic history is needed.
3. Call `CodexMissionStore.rollback_database(path)` to remove only Phase 1 tables.
4. Revert source snapshot `76dd8c6682649e65ac7590ea06d9f687dc47d6cd` if the UI/API integration must also be removed.

## Known limitations and deferred work

- Phase 1 observes only explicitly started missions and configured roots; it does not auto-start from personal context.
- An App Server exit can leave Codex reporting that a writer remains active. Ophanim reads and labels that state without taking control; it does not steer or force ownership.
- Raw diagnostic payloads are local and owner-restricted on supported filesystems; Windows also relies on the containing directory's inherited ACL.
- No approve, steer, interrupt, fork, remote-control, or automatic stall-recovery UI is present. Those remain Phase 2 work.
