# Phase 1 bounded implementation spec — Codex read-only observer

## Frozen acceptance scenario v1

Objective:

> Inspect the Ophanim repository. Map how managed agents receive live context, identify the three highest-risk integration seams, and make no changes.

The observer must use Codex app-server protocol v2, start the thread and turn with a read-only sandbox, persist the Ophanim mission to Codex thread/turn mapping, normalize observable events, and show a traceable plain-language mission view.

The test injects an App Server exit during the mission, restarts the bridge, and either resumes observation or accurately classifies the interruption. Replayed unchanged notifications must not create duplicate milestones. Approval or mutation requests must be rejected without exposing any Phase 2 control surface.

## In scope

- Codex CLI detection and protocol compatibility manifest.
- JSON-RPC stdio lifecycle and initialize/initialized handshake.
- Read-only thread start, list, read, and resume observation.
- Durable mission, normalized-event, milestone, and restricted raw-event records.
- Redaction before persistence intended for display and before API/UI output.
- Read-only Operations mission list/detail experience.
- Restart recovery, deduplication, traceability, and retention cleanup.

## Out of scope

- Steering, interrupt controls, approvals, workspace-write mode, remote control, automatic stall steering, and autonomous mission starts.
- Claims about hidden reasoning. Only protocol events and inspectable artifacts may be summarized.

## Security invariants

- Mission cwd must resolve within an explicitly configured observer root.
- Thread and turn requests always specify read-only sandboxing.
- Incoming server requests for command, file, permission, tool, or user approval receive a denial/error response and are recorded as blockers.
- Raw payloads are short-retention and never returned by the public mission API.
- Tokens, credentials, authorization headers, cookies, and common secret formats are redacted recursively.
