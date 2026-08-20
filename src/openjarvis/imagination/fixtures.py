"""Deterministic Phase 9 fixtures and side-effect-free adapters."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import SimulationKind, SimulationRun, SimulationStatus, stable_id


@dataclass(frozen=True, slots=True)
class DepartureFixture:
    """A replay-only world snapshot; it never connects to live services."""

    scenario_id: str = "phase9-departure-seeded-faults"
    calendar_event_id: str = "calendar:phase9-remote-likely"
    calendar_title: str = "Client review"
    calendar_address: str = "1 Example Plaza"
    calendar_likely_remote: bool = True
    household_present: tuple[str, ...] = ("Marc", "Guest")
    cover_entity: str = "cover.entry"
    cover_reported_state: str = "closed"
    cover_observed_state: str = "open"
    traffic_available: bool = False
    traffic_minutes: int | None = None
    traffic_fallback_minutes: int = 20
    current_time: str = "2026-08-15T08:00:00+00:00"

    def fingerprint(self) -> str:
        raw = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "calendar_event_id": self.calendar_event_id,
            "calendar_title": self.calendar_title,
            "calendar_address": self.calendar_address,
            "calendar_likely_remote": self.calendar_likely_remote,
            "household_present": list(self.household_present),
            "cover_entity": self.cover_entity,
            "cover_reported_state": self.cover_reported_state,
            "cover_observed_state": self.cover_observed_state,
            "traffic_available": self.traffic_available,
            "traffic_minutes": self.traffic_minutes,
            "traffic_fallback_minutes": self.traffic_fallback_minutes,
            "current_time": self.current_time,
        }


@dataclass(slots=True)
class DigitalTwinHomeAssistant:
    """A state machine for HA simulation, with no network or live adapter."""

    initial_states: dict[str, dict[str, Any]]
    calls: list[dict[str, Any]] = field(default_factory=list)
    live_effects: int = 0
    states: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.states = {key: dict(value) for key, value in self.initial_states.items()}

    def read_state(self, target: str) -> dict[str, Any]:
        return dict(self.states.get(target, {"entity_id": target, "state": "unknown"}))

    def apply(
        self, action_type: str, target: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "action_type": action_type,
                "target": target,
                "parameters": dict(parameters),
            }
        )
        state = self.states.setdefault(
            target, {"entity_id": target, "state": "unknown"}
        )
        # The seeded cover is intentionally a feedback contradiction: the
        # command reports success but the simulated physical state does not move.
        if action_type == "home_assistant.cover.position" and state.get(
            "seeded_no_move"
        ):
            return {
                "reported": True,
                "state": dict(state),
                "effect_scope": "digital_twin",
            }
        if action_type in {"home_assistant.light.on", "home_assistant.turn_on"}:
            state["state"] = "on"
        elif action_type in {"home_assistant.light.off", "home_assistant.turn_off"}:
            state["state"] = "off"
        elif action_type == "home_assistant.cover.position":
            state["state"] = (
                "closed" if float(parameters.get("position", 100)) <= 5 else "open"
            )
            state["position"] = float(parameters.get("position", 100))
        return {"reported": True, "state": dict(state), "effect_scope": "digital_twin"}

    def verify(
        self, action_type: str, target: str, parameters: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        state = self.read_state(target)
        normalized = str(state.get("state", "")).lower()
        if action_type == "home_assistant.cover.position":
            expected = float(parameters.get("position", 100))
            observed = state.get("position")
            if observed is not None:
                return abs(float(observed) - expected) <= 5, state
            return (expected <= 5 and normalized == "closed") or (
                expected > 5 and normalized == "open"
            ), state
        if action_type in {"home_assistant.light.on", "home_assistant.turn_on"}:
            return normalized == "on", state
        if action_type in {"home_assistant.light.off", "home_assistant.turn_off"}:
            return normalized == "off", state
        return True, state


class BoundedCodexExperiment:
    """Copy a workspace into a bounded experiment root and run seeded evidence.

    This is intentionally a fixture boundary rather than a live Codex client.
    A production adapter can map the same contract to a Codex worktree, but it
    must preserve the root check and must never write the primary workspace.
    """

    def __init__(
        self, primary_workspace: str | Path, experiment_root: str | Path
    ) -> None:
        self.primary = Path(primary_workspace).resolve()
        self.root = Path(experiment_root).resolve()
        if self.primary == self.root or self.root.is_relative_to(self.primary):
            raise ValueError("experiment root must be outside the primary workspace")
        self.root.mkdir(parents=True, exist_ok=True)

    def run(self, *, candidate_id: str, seeded_failure: bool = True) -> SimulationRun:
        safe_candidate = (
            re.sub(r"[^A-Za-z0-9._-]+", "_", candidate_id).strip("._") or "candidate"
        )
        workspace = (self.root / f"phase9-{safe_candidate}").resolve()
        if not workspace.is_relative_to(self.root):
            raise ValueError("experiment workspace escaped its bounded root")
        if workspace.exists():
            raise FileExistsError(f"experiment workspace already exists: {workspace}")
        shutil.copytree(
            self.primary,
            workspace,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
        )
        marker = workspace / "phase9-seeded-candidate.txt"
        marker.write_text(
            "seeded test failure\n" if seeded_failure else "seeded test pass\n",
            encoding="utf-8",
        )
        return SimulationRun(
            simulation_id=stable_id(
                "sim", candidate_id, SimulationKind.CODEX_WORKSPACE.value
            ),
            candidate_id=candidate_id,
            kind=SimulationKind.CODEX_WORKSPACE,
            status=SimulationStatus.COMPLETED,
            simulation_outcome="failure" if seeded_failure else "success",
            evidence_scope="simulation",
            findings=(
                "seeded test failure was observed in the isolated experimental workspace",
                "primary workspace was not written",
            )
            if seeded_failure
            else ("isolated experimental test passed",),
            prediction_ids=(),
            failure_summary="seeded failure is experimental evidence only"
            if seeded_failure
            else None,
            workspace_path=str(workspace),
        )


def departure_twin(fixture: DepartureFixture) -> DigitalTwinHomeAssistant:
    return DigitalTwinHomeAssistant(
        {
            fixture.cover_entity: {
                "entity_id": fixture.cover_entity,
                "state": fixture.cover_observed_state,
                "position": 100 if fixture.cover_observed_state == "open" else 0,
                "seeded_no_move": fixture.cover_reported_state
                != fixture.cover_observed_state,
            },
            "light.entry": {"entity_id": "light.entry", "state": "on"},
        }
    )


__all__ = [
    "BoundedCodexExperiment",
    "DepartureFixture",
    "DigitalTwinHomeAssistant",
    "departure_twin",
]
