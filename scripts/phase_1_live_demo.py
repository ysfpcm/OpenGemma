"""Run the reproducible Phase 1 read-only interruption/recovery scenario."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from openjarvis.codex_observer import CodexMissionStore, CodexObserverSupervisor

OBJECTIVE = (
    "Inspect the Ophanim repository. Map how managed agents receive live context, "
    "identify the three highest-risk integration seams, and make no changes."
)


def git_state(workspace: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--observe-seconds", type=float, default=5.0)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    before = git_state(workspace)
    store = CodexMissionStore(args.database)
    first = CodexObserverSupervisor(store, roots=[str(workspace)])
    mission = first.start_mission(OBJECTIVE, str(workspace))

    deadline = time.monotonic() + args.observe_seconds
    while time.monotonic() < deadline:
        if len(store.get(mission["id"])["events"]) >= 2:
            break
        time.sleep(0.25)

    if not first.client.running:
        raise RuntimeError("App Server stopped before interruption injection")
    first.client.terminate_unexpectedly()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if store.get(mission["id"])["status"] == "interrupted":
            break
        time.sleep(0.1)
    interrupted = store.get(mission["id"])

    replacement = CodexObserverSupervisor(store, roots=[str(workspace)])
    replacement.resume_observation(mission["id"])
    recovered = store.get(mission["id"])
    replacement.stop()

    event_fingerprints = {event["fingerprint"] for event in recovered["events"]}
    traceable = all(
        milestone["event_fingerprint"] in event_fingerprints
        for milestone in recovered["milestones"]
    )
    after = git_state(workspace)
    report = {
        "objective": OBJECTIVE,
        "codex": first.capabilities,
        "mission_id": mission["id"],
        "thread_id": mission["thread_id"],
        "turn_id": mission["active_turn_id"],
        "interrupted_status": interrupted["status"],
        "interrupted_reason": interrupted["interrupted_reason"],
        "recovered_status": recovered["status"],
        "recovered_phase": recovered["phase"],
        "event_methods": [event["method"] for event in recovered["events"]],
        "event_count": len(recovered["events"]),
        "milestone_count": len(recovered["milestones"]),
        "milestones_traceable": traceable,
        "workspace_unchanged": before == after,
    }
    print(json.dumps(report, indent=2))
    store.close()
    return (
        0
        if interrupted["status"] == "interrupted" and traceable and before == after
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
