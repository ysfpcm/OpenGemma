# Phase 2 — human mission control

Status: **accepted by Marc on 2026-08-16**. Phase 2 adds bounded workspace-write missions and direct
human control while preserving the Phase 1 read-only default and fail-closed
authority boundary.

## Implemented contract

- Read-only missions remain `sandbox=read-only`, `sandboxPolicy.type=readOnly`,
  `networkAccess=false`, and `approvalPolicy=never`.
- Workspace-write missions must opt in explicitly. They use
  `sandbox=workspace-write`, `sandboxPolicy.type=workspaceWrite`, a writable
  root equal to the mission workspace, `networkAccess=false`, and
  `approvalPolicy=on-request` with the Codex reviewer set to `user`.
- Steering targets the persisted `thread_id` and `active_turn_id`; Codex also
  receives `expectedTurnId`, so a stale control cannot steer a replacement turn.
- Interrupts target that exact turn and remain in `interrupting` until Codex
  reports a terminal status.
- Resume first calls `thread/resume`, starts a new turn on the same persisted
  thread, and records completed effect IDs in both the ledger and the guarded
  resume prompt. Completed effects are not replayed by Ophanim.
- Forks are copied into a private temporary workspace with a durable marker,
  separate mission record, separate writable root, and copied budgets. Compare
  is hash-based and read-only; select changes the ledger only and never merges
  or copies files into the primary workspace.
- Checkpoints are requested through the existing `turn/steer` protocol with a
  fixed observable field contract: `summary`, `changed_files`,
  `completed_effects`, `remaining_work`, `blockers`, `verification`, and
  `budget_state`. Private reasoning is explicitly excluded.

## Approval broker

The installed Codex v2 protocol requests are routed by their exact method:

| Codex request | Guardian classification | Marc response surface |
|---|---|---|
| `item/commandExecution/requestApproval` | `command` or `network` | Only decisions offered by the request; network/install commands are denied by Guardian. |
| `item/fileChange/requestApproval` | `file-change` | Accept, decline, or cancel when the requested root is within the mission workspace. |
| `item/permissions/requestApproval` | `permission` | Turn-scoped filesystem permissions only; network permissions are never granted. |
| `item/tool/requestUserInput` | `user-input` | Answers must match the exact question IDs Codex supplied. |
| `mcpServer/elicitation/request` | `user-input` | Accept, decline, or cancel as offered by Codex. |

Every decision records three separate facts: `codex_requested`,
`guardian_allows`, and `marc_decision`/`marc_approved`. A decline is terminal
for that request ID. A later attempt to reuse it, or to resolve a Guardian-
blocked request as an approval, is rejected. Unknown server request methods
are denied with a JSON-RPC error.

The protocol client itself remains fail-closed when no broker is installed.
No UI or API exposes danger-full-access, network enablement, approval-policy
bypass, arbitrary writable roots, session-wide grant, or exec-policy controls.

## Budgets

Budgets are persisted per mission and checked on observable events and before
human steering. The implementation tracks time, reported tokens, completed
commands, external/network tools, workspace bytes, and workspace file count.
Workspace budgets are measured as deltas from the mission's baseline, not the
size of the existing repository. At a boundary the mission becomes
`budget_exceeded`, an interrupt is issued, and the persisted state explains
which category stopped it. Resume cannot silently increase an exhausted
budget; a new explicitly configured mission is required.

Default limits are 1,800 seconds, 100,000 tokens, 200 commands, zero network
operations, 50 MB of workspace delta, and 2,000 new workspace files. Mission
start accepts lower limits for a stricter run.

## Verification

The acceptance test is
`tests/codex_observer/test_phase_2_mission_control.py`. It uses an isolated
fixture repository and a protocol-shaped App Server fixture. It runs an actual
workspace-write mission through the supervisor, creates a feature and test,
proves the test fails before the fix and passes after it, and restores the
fixture byte-for-byte.

The scenario covers: steering to the failing test, a structured checkpoint,
network/install denial before effect, scoped file-change denial, broader
permission denial, accepted alternative file changes, interruption, persisted
resume with skipped completed effect IDs, isolated fork creation, fork
comparison, fork selection, notification redaction, and exact rollback.

## Limitations and deliberate deferrals

- The Phase 2 fixture is deterministic and protocol-shaped; a real live Codex
  write mission remains a separate operator demo because it depends on the
  installed account/model and can consume credits.
- Checkpoints are requested through `turn/steer` because Codex v2.0.147 has no
  dedicated checkpoint RPC. Ophanim records the requested schema but does not
  parse arbitrary agent text into a trusted checkpoint until a typed Codex
  response is available.
- The current notification delivery is the local desktop EventBus plus an
  injectable notification sink. Remote channel adapters remain later-phase
  work; all notification payloads are metadata-only and redacted.
- Fork selection records the selected alternative; it intentionally does not
  merge files automatically. Any merge remains a new, separately authorized
  operation.

## Rollback

1. Stop starting new missions and unset `OPHANIM_CODEX_OBSERVER_ROOTS` before
   restarting Ophanim. This leaves Codex routes unconfigured and launches no
   App Server.
2. Preserve the database if the evidence is needed, then run
   `CodexMissionStore.rollback_database(path)` from a maintenance script. The
   rollback drops only `codex_*` tables, including Phase 2 decisions and
   notifications, and preserves unrelated SQLite tables.
3. Remove any private temporary fork directories under the system temporary
   directory after confirming no fork is needed. They contain only copied
   fixture/workspace data.
4. Revert the Phase 2 source changes as one reviewed change set if the API/UI
   integration must be removed. Do not manually edit an approval policy to
   regain access; the safe default is the read-only mission mode.
