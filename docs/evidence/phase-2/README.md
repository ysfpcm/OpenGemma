# Phase 2 evidence — human mission control

Phase 2 is **accepted by Marc on 2026-08-16**. This evidence pack records the
implementation and test results; the live operational follow-up remains deferred
as `P2-LIVE-01`.
The deterministic evidence below covers the bounded workspace-write supervisor,
approval broker, mission ledger, Operations controls, notifications, budgets,
isolated forks, and rollback behavior. The local API now has the Ophanim root
configured, and the Operations page can start a root-scoped Codex mission.

## Reproducible source and environment

- Repository: `C:\Users\Marc\Documents\Projects\Ophanim`
- Source snapshot: current uncommitted working tree; unrelated later-phase
  changes are present and were not treated as Phase 2 evidence.
- Codex: `codex-cli 0.147.0`
- Python environment: repository `.venv`
- Protocol: Codex App Server v2 over local stdio
- Observer schema: migration v3, including persisted decisions and
  notifications
- Acceptance fixture: created under pytest's isolated temporary directory;
  the fixture workspace was restored byte-for-byte before the test returned.

## Exact verification results

Focused Phase 2 fixture:

```text
5 passed in 2.85s
```

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp `
  ".tmp-review-phase-2-final-focused-2" `
  tests\codex_observer\test_phase_2_mission_control.py
```

Neighboring Codex/regression tests:

```text
21 passed in 3.74s
```

Complete acceptance suite:

```text
107 passed in 13.19s
```

Targeted Python compilation, Ruff, and format checks: **passed** (`All checks
passed!`). Frontend:

- Vitest: **6 passed**.
- TypeScript and Vite production build: **passed**.
- Existing advisory: the main generated JavaScript chunk is above Vite's
  500 kB warning threshold; this does not fail the build.

## Exit-gate evidence

- Active-turn steering: the supervisor sends the persisted `threadId`,
  `activeTurnId`, and Codex `expectedTurnId`; stale or non-active turns are
  refused.
- Safe interruption: interruption targets the persisted turn and remains
  honest (`interrupting` until Codex reports a terminal state).
- Persisted resume: resume calls `thread/resume`, starts a new turn on the
  same thread, and includes persisted completed effect IDs in the guard;
  effects are not replayed by Ophanim.
- Fork isolation: the fixture created a private fork workspace, marker,
  mission record, writable root, and authority record. Fork comparison is
  read-only and selection changes only the ledger.
- Checkpoints: the fixed observable checkpoint fields were requested through
  the supported Codex turn-steering protocol.
- Request routing: command/install, file-change, permission, network, MCP
  elicitation, and user-input requests were classified and routed through the
  broker. Network and dependency-install requests were denied before effect.
- Offered decisions: responses were restricted to the exact choices and
  question IDs supplied by Codex. Reusing a resolved request replays the
  persisted safe response without creating a second decision; reusing a
  request ID for a different request is rejected. Stale-turn and malformed
  requests are denied before approval.
- Notifications: waiting decisions produced desktop EventBus and injectable
  notification-sink records; payloads contain decision metadata only and no
  raw secret answer.
- Budgets: time, token, command, network, workspace-byte, and workspace-file
  counters are persisted. A boundary changes the mission to
  `budget_exceeded` and interrupts the active turn; resume cannot widen it.
- Authority labels: persisted decision records separately expose
  `codex_requested`, `guardian_allows`, and Marc's decision/approval.
- Public mission projections do not expose stored user-input answers; the
  protocol broker retains only the internal response needed for idempotent
  replay.
- Rollback: the Phase 2 fixture creates a persisted decision and notification,
  then `rollback_database` removes every `codex_*` table while preserving an
  unrelated user-owned table.
- Protection: read-only remains `approvalPolicy=never` and network-disabled;
  workspace-write is explicit, root-scoped, network-disabled, and
  `on-request` with Codex's user reviewer. No bypass control is exposed.

## Tangible fixture scenario

`tests/codex_observer/test_phase_2_mission_control.py` runs a real
workspace-write mission against an isolated repository and an App Server
protocol fixture. It adds a small feature and test, steers to the failing
test, requests a checkpoint, attempts an install/network operation and proves
it is denied before effect, forks an alternative, declines a scoped operation,
rejects a broader permission retry, interrupts, resumes, compares forks,
selects one, restores the original fixture bytes exactly, and exercises
duplicate delivery, stale-turn denial, malformed-request denial, and
budget-safe resume refusal.

The fixture is deterministic and protocol-shaped so that the exit gate does
not depend on a live model response, account state, or credit availability.

## Limitations

- **P2-LIVE-01 — deferred, non-blocking:** Codex CLI 0.147.0 can create the
  root-scoped live thread, but its App Server may delay the `turn/start`
  acknowledgement while desktop MCP integrations initialize. Ophanim marks that
  case as `TimeoutError` rather than claiming the turn is tracked. Complete a
  live start/steer/checkpoint/finish demonstration before relying on Mission
  Control for consequential work. This is now a later operational-readiness
  milestone, not a prerequisite for the Codex local-computer architecture or
  subsequent design work. The sandbox, network-disabled policy, configured-root
  restriction, budgets, and approval boundaries remain in force.

- Codex 0.147.0 has no dedicated checkpoint RPC; the supervisor requests the
  fixed checkpoint contract through `turn/steer` and does not treat arbitrary
  agent text as trusted structured data.
- Notification delivery currently covers the local desktop EventBus and an
  injectable sink. Remote channel adapters are later-phase work.
- Fork selection is intentionally ledger-only; automatic merging is a new
  separately authorized operation.
- A live workspace-write Codex demonstration was not used as the deterministic
  exit-gate oracle because it depends on the installed account/model and may
  consume credits. The installed CLI/schema compatibility was validated
  separately.

## Rollback

1. Stop new missions and unset `OPHANIM_CODEX_OBSERVER_ROOTS` before restarting
   Ophanim. This returns the observer to its fail-closed, no-launch state.
2. Back up the observer database if the evidence is needed, then call
   `CodexMissionStore.rollback_database(path)` from a maintenance script. It
   drops only `codex_*` tables, including Phase 2 decisions and notifications.
3. After confirming no fork is needed, remove only the private temporary fork
   directories created by the supervisor under the system temporary folder.
4. Revert the reviewed Phase 2 working-tree change set if the integration
   must be removed. Do not enable network access or alter approval policy to
   roll back; read-only is the safe operating mode.
