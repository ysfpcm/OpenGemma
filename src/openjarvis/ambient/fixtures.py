"""Deterministic local Phase 11 tangible mission-control fixture."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import (
    AuthorityGrantScope,
    ChannelConsent,
    MissionUpdate,
    NotificationPolicy,
    PresenceState,
    UpdateKind,
)
from .service import AmbientMissionControl, LocalChannelAdapter
from .store import AmbientStore


class Phase11MissionFixture:
    """Exercise the leave, query, scoped steering, return, and review flow."""

    mission_id = "phase11-two-hour-codex-mission"
    thread_id = "thread-phase11-primary"
    turn_id = "turn-phase11-1"
    workspace = r"C:\fixture\ophanim\phase11"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "phase11.db"
        self.adapter = LocalChannelAdapter()

    def _consent(self) -> ChannelConsent:
        return ChannelConsent(
            id="consent:phase11:phone",
            consent_id="consent:phase11:phone",
            channel="phone",
            mission_id=self.mission_id,
            operations=["notify", "query", "steer"],
            provenance={"component": "marc-fixture-consent", "explicit": True},
            sensitivity_labels=["mission-control"],
        )

    def _scope(self) -> AuthorityGrantScope:
        return AuthorityGrantScope(
            id="grant:phase11:fork-stop",
            grant_id="grant:phase11:fork-stop",
            mission_id=self.mission_id,
            turn_id=self.turn_id,
            workspace=self.workspace,
            capability="codex:fork-stop",
            provenance={"component": "fixture", "created_by": "Marc"},
            sensitivity_labels=["authority"],
        )

    def run(self) -> dict[str, Any]:
        store = AmbientStore(self.db_path)
        control = AmbientMissionControl(store, channel_adapter=self.adapter)
        control.register_mission(
            mission_id=self.mission_id,
            thread_id=self.thread_id,
            turn_id=self.turn_id,
            workspace=self.workspace,
            goal=(
                "Complete the scoped repository change and report the decision "
                "without unattended expansion."
            ),
            budget={"steering_count": 1, "time_minutes": 120},
            authority_scope={"mode": "workspace-write", "network": False},
        )
        control.grant_channel_consent(self._consent())
        control.register_existing_authority_scope(self._scope())
        control.record_presence(self.mission_id, PresenceState.AT_DESK)

        start = control.record_update(
            MissionUpdate(
                id="update:phase11:1",
                update_id="update:phase11:1",
                mission_id=self.mission_id,
                turn_id=self.turn_id,
                sequence=1,
                kind=UpdateKind.MILESTONE,
                summary="Codex started the scoped two-hour mission.",
                materiality=0.75,
                changed_fields=["mission started"],
                evidence_ids=["codex:mission-start"],
                provenance={"component": "fixture-codex-observer"},
                sensitivity_labels=["workspace"],
            )
        )
        control.deliver_update(
            start.update_id,
            NotificationPolicy(
                id="policy:phase11:desktop",
                policy_id="policy:phase11:desktop",
                channel="local-desktop",
                minimum_materiality=0.5,
                provenance={"component": "fixture"},
            ),
        )
        control.record_presence(self.mission_id, PresenceState.AWAY_FROM_PC)

        unchanged = control.record_update(
            MissionUpdate(
                id="update:phase11:2",
                update_id="update:phase11:2",
                mission_id=self.mission_id,
                turn_id=self.turn_id,
                sequence=2,
                kind=UpdateKind.HEARTBEAT,
                summary="Codex started the scoped two-hour mission.",
                materiality=0.1,
                changed_fields=[],
                evidence_ids=["codex:heartbeat"],
                provenance={"component": "fixture-codex-observer"},
                sensitivity_labels=["workspace"],
            )
        )
        suppressed = control.deliver_update(
            unchanged.update_id,
            NotificationPolicy(
                id="policy:phase11:desktop:heartbeat",
                policy_id="policy:phase11:desktop:heartbeat",
                channel="local-desktop",
                minimum_materiality=0.5,
                provenance={"component": "fixture"},
            ),
        )

        # A process restart while Marc is away must preserve the ledger and
        # explicitly mark currentness rather than inventing a fresh fact.
        store.close()
        reopened = AmbientStore(self.db_path)
        recovered = reopened.recover_interrupted()
        control = AmbientMissionControl(reopened, channel_adapter=self.adapter)
        decision = control.record_update(
            MissionUpdate(
                id="update:phase11:3",
                update_id="update:phase11:3",
                mission_id=self.mission_id,
                turn_id=self.turn_id,
                sequence=3,
                kind=UpdateKind.DECISION,
                summary=(
                    "Codex reached a scoped decision; the safer approach remains "
                    "available, and the original tests still fail."
                ),
                materiality=0.95,
                state="blocked",
                changed_fields=["scoped decision reached", "safer approach available"],
                blockers=["original tests still fail"],
                evidence_ids=["codex:decision-3", "codex:test-result-3"],
                artifact_ids=["artifact:test-report", "artifact:decision-receipt"],
                uncertainty=[
                    "failure condition is fixture-observed, not live production success"
                ],
                provenance={"component": "fixture-codex-observer"},
                sensitivity_labels=["workspace"],
            )
        )
        control.deliver_update(
            decision.update_id,
            NotificationPolicy(
                id="policy:phase11:phone",
                policy_id="policy:phase11:phone",
                channel="phone",
                minimum_materiality=0.8,
                require_consent_id="consent:phase11:phone",
                provenance={"component": "fixture"},
            ),
            consent_id="consent:phase11:phone",
        )
        query = control.answer_remote_query(
            query_id="query:phase11:what-changed",
            mission_id=self.mission_id,
            channel="phone",
            question="What changed and what is blocked?",
            consent_id="consent:phase11:phone",
        )
        steering = control.request_steering(
            steering_id="steering:phase11:fork-stop",
            mission_id=self.mission_id,
            turn_id=self.turn_id,
            workspace=self.workspace,
            channel="phone",
            consent_id="consent:phase11:phone",
            authority_grant_id="grant:phase11:fork-stop",
        )
        executed = control.execute_steering(steering.steering_id, tests_failed=True)
        returned = control.record_presence(self.mission_id, PresenceState.RETURNED)
        final_summary = reopened.get_mission(self.mission_id)
        timeline = control.timeline(self.mission_id)
        reopened.close()
        return {
            "mission": final_summary,
            "recovered_records": recovered,
            "suppressed_delivery": suppressed,
            "remote_query": query,
            "steering_requested": steering,
            "steering_executed": executed,
            "returned_presence": returned,
            "timeline": timeline,
            "external_effects": self.adapter.external_effects,
            "local_deliveries": self.adapter.local_deliveries,
        }


__all__ = ["Phase11MissionFixture"]
