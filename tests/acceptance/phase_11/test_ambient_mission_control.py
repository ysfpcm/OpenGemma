"""Deterministic local acceptance tests for Phase 11 ambient mission control."""

from __future__ import annotations

import sqlite3

import pytest

from openjarvis.ambient import (
    AmbientMissionControl,
    AmbientPresence,
    AmbientStore,
    AuthorityGrantScope,
    ChannelConsent,
    InterruptionKind,
    InterruptionStatus,
    MissionPortfolioItem,
    MissionUpdate,
    NotificationPolicy,
    Phase11MissionFixture,
    PresenceState,
    SteeringStatus,
    UpdateDeliveryStatus,
    UpdateKind,
)


def _control(tmp_path, *, budget: int = 1, failed_channels: set[str] | None = None):
    from openjarvis.ambient import LocalChannelAdapter

    store = AmbientStore(tmp_path / "ambient.db")
    adapter = LocalChannelAdapter(failed_channels=failed_channels)
    control = AmbientMissionControl(store, channel_adapter=adapter)
    control.register_mission(
        mission_id="mission-11",
        thread_id="thread-11",
        turn_id="turn-11",
        workspace=r"C:\Users\Marc\Projects\fixture",
        goal="finish the bounded fixture mission",
        budget={"steering_count": budget},
    )
    consent = ChannelConsent(
        id="consent-11",
        consent_id="consent-11",
        channel="phone",
        mission_id="mission-11",
        operations=["notify", "query", "steer"],
    )
    control.grant_channel_consent(consent)
    scope = AuthorityGrantScope(
        id="grant-11",
        grant_id="grant-11",
        mission_id="mission-11",
        turn_id="turn-11",
        workspace=r"C:\Users\Marc\Projects\fixture",
        capability="codex:fork-stop",
        provenance={"created_by": "Marc"},
    )
    control.register_existing_authority_scope(scope)
    return store, control, adapter


def _update(
    *,
    mission_id: str = "mission-11",
    turn_id: str = "turn-11",
    sequence: int = 1,
    summary: str = "A material milestone completed.",
    materiality: float = 0.9,
    kind: UpdateKind = UpdateKind.MILESTONE,
    changed: list[str] | None = None,
    blockers: list[str] | None = None,
    labels: list[str] | None = None,
) -> MissionUpdate:
    return MissionUpdate(
        id=f"update-{sequence}",
        update_id=f"update-{sequence}",
        mission_id=mission_id,
        turn_id=turn_id,
        sequence=sequence,
        kind=kind,
        summary=summary,
        materiality=materiality,
        changed_fields=list(changed or ["one material change"]),
        blockers=list(blockers or []),
        sensitivity_labels=list(labels or ["workspace"]),
    )


def test_tangible_two_hour_mission_continuity_query_and_scoped_steering(tmp_path):
    result = Phase11MissionFixture(tmp_path).run()

    assert result["recovered_records"] >= 1
    assert (
        result["suppressed_delivery"].status
        is UpdateDeliveryStatus.SUPPRESSED_UNCHANGED
    )
    assert result["remote_query"].status == "answered"
    assert "safer approach available" in result["remote_query"].answer
    assert "original tests still fail" in result["remote_query"].answer
    assert result["mission"].state == "forked_local_original_stopped"
    assert result["mission"].last_update_seq == 3
    assert result["steering_requested"].status is SteeringStatus.APPROVED
    assert result["steering_executed"].status is SteeringStatus.EXECUTED_LOCAL
    assert result["steering_executed"].original_stop_requested is True
    assert result["steering_executed"].live_effect is False
    assert result["returned_presence"].state is PresenceState.RETURNED
    assert result["external_effects"] == []
    assert len(result["local_deliveries"]) == 2
    assert any(
        item["event"] == "steering:executed_local"
        for item in result["timeline"]["audit"]
    )


def test_material_update_suppression_does_not_send_duplicate_after_restart(tmp_path):
    store, control, adapter = _control(tmp_path)
    update = control.record_update(_update())
    policy = NotificationPolicy(
        id="policy-11",
        policy_id="policy-11",
        channel="phone",
        require_consent_id="consent-11",
    )
    first = control.deliver_update(update.update_id, policy, consent_id="consent-11")
    assert first.status is UpdateDeliveryStatus.DELIVERED_LOCAL
    store.close()

    reopened = AmbientStore(tmp_path / "ambient.db")
    control = AmbientMissionControl(reopened, channel_adapter=adapter)
    duplicate = control.deliver_update(
        update.update_id, policy, consent_id="consent-11"
    )
    assert duplicate.status is UpdateDeliveryStatus.DELIVERED_LOCAL
    assert len(adapter.local_deliveries) == 1
    reopened.close()


def test_local_desktop_consent_exemption_does_not_accept_invalid_explicit_consent(
    tmp_path,
):
    store, control, adapter = _control(tmp_path)
    update = control.record_update(_update())
    delivery = control.deliver_update(
        update.update_id,
        NotificationPolicy(
            id="policy-local-explicit",
            policy_id="policy-local-explicit",
            channel="local-desktop",
            require_consent_id="missing-consent",
        ),
        consent_id="missing-consent",
    )
    assert delivery.status is UpdateDeliveryStatus.BLOCKED_CONSENT
    assert adapter.local_deliveries == []
    store.close()


def test_barge_in_cancel_and_emergency_stop_precedence(tmp_path):
    store, control, _adapter = _control(tmp_path)
    control.start_speech("mission-11")
    barge = control.interrupt_speech("mission-11")
    assert barge.status is InterruptionStatus.APPLIED
    lower = control.interrupt(
        "mission-11", InterruptionKind.BUDGET_EXCEEDED, reason="budget fixture"
    )
    assert lower.status is InterruptionStatus.DECLINED
    canceled = control.cancel_mission("mission-11")
    assert canceled.status is InterruptionStatus.APPLIED
    stopped = control.emergency_stop(reason="fixture emergency")
    assert stopped
    assert all(item.status is InterruptionStatus.APPLIED for item in stopped)
    store.close()


def test_remote_redaction_consent_stale_and_channel_failure(tmp_path):
    store, control, adapter = _control(tmp_path, failed_channels={"phone"})
    update = control.record_update(
        _update(
            summary=(
                "Tests changed. command: powershell -File C:\\secret\\run.ps1 "
                "token=super-secret diff --git a/private b/private"
            )
        )
    )
    delivery = control.deliver_update(
        update.update_id,
        NotificationPolicy(
            id="policy-remote",
            policy_id="policy-remote",
            channel="phone",
            require_consent_id="consent-11",
        ),
        consent_id="consent-11",
    )
    assert delivery.status is UpdateDeliveryStatus.FAILED_CHANNEL
    assert "super-secret" not in delivery.safe_summary
    assert "C:\\secret" not in delivery.safe_summary
    assert "powershell" not in delivery.safe_summary.lower()
    evidence = store.get(delivery.redaction_id)
    assert evidence.categories
    assert "command" in evidence.categories
    assert evidence.secret_values_persisted is False

    query = control.answer_remote_query(
        query_id="query-failed-channel",
        mission_id="mission-11",
        channel="phone",
        question="What changed? token=do-not-store",
        consent_id="consent-11",
        channel_available=False,
    )
    assert query.status == "failed_channel"
    assert query.answer == ""
    assert any(
        item.kind is InterruptionKind.REMOTE_CHANNEL_FAILURE
        for item in store.list_records(kind="interruption")
    )
    store.close()

    stale_store = AmbientStore(tmp_path / "stale.db")
    stale_control = AmbientMissionControl(stale_store)
    stale_control.register_mission(
        mission_id="mission-stale",
        thread_id="thread-stale",
        turn_id="turn-stale",
        workspace="C:\\stale",
        goal="stale mission fixture",
    )
    stale_control.grant_channel_consent(
        ChannelConsent(
            id="consent-stale",
            consent_id="consent-stale",
            channel="phone",
            mission_id="mission-stale",
            operations=["query"],
        )
    )
    stale_control.record_update(
        _update(sequence=1, mission_id="mission-stale", turn_id="turn-stale")
    )
    stale_store.close()
    stale_store = AmbientStore(tmp_path / "stale.db")
    assert stale_store.recover_interrupted() >= 1
    stale_control = AmbientMissionControl(stale_store)
    stale_query = stale_control.answer_remote_query(
        query_id="query-stale",
        mission_id="mission-stale",
        channel="phone",
        question="What changed?",
        consent_id="consent-stale",
    )
    assert stale_query.stale is True
    assert "Currentness: stale" in stale_query.answer
    stale_store.close()


def test_steering_fails_closed_for_scope_revocation_budget_cancel_and_remote_authority(
    tmp_path,
):
    store, control, _adapter = _control(tmp_path, budget=1)
    with pytest.raises(PermissionError, match="cannot create"):
        control.register_existing_authority_scope(
            AuthorityGrantScope(
                id="grant-remote",
                grant_id="grant-remote",
                mission_id="mission-11",
                turn_id="turn-11",
                workspace=r"C:\Users\Marc\Projects\fixture",
                capability="codex:fork-stop",
                provenance={"created_by": "remote-channel"},
            )
        )
    mismatch = control.request_steering(
        steering_id="steering-mismatch",
        mission_id="mission-11",
        turn_id="turn-other",
        workspace=r"C:\Users\Marc\Projects\fixture",
        channel="phone",
        consent_id="consent-11",
        authority_grant_id="grant-11",
    )
    assert mismatch.status is SteeringStatus.STALE

    approved = control.request_steering(
        steering_id="steering-approved",
        mission_id="mission-11",
        turn_id="turn-11",
        workspace=r"C:\Users\Marc\Projects\fixture",
        channel="phone",
        consent_id="consent-11",
        authority_grant_id="grant-11",
    )
    assert approved.status is SteeringStatus.APPROVED
    assert (
        control.execute_steering(approved.steering_id, tests_failed=True).status
        is SteeringStatus.EXECUTED_LOCAL
    )
    budgeted = control.request_steering(
        steering_id="steering-budgeted",
        mission_id="mission-11",
        turn_id="turn-11",
        workspace=r"C:\Users\Marc\Projects\fixture",
        channel="phone",
        consent_id="consent-11",
        authority_grant_id="grant-11",
    )
    assert budgeted.status is SteeringStatus.BUDGET_EXCEEDED

    canceled_store, canceled, _ = _control(tmp_path / "canceled")
    canceled.cancel_mission("mission-11")
    canceled_request = canceled.request_steering(
        steering_id="steering-canceled",
        mission_id="mission-11",
        turn_id="turn-11",
        workspace=r"C:\Users\Marc\Projects\fixture",
        channel="phone",
        consent_id="consent-11",
        authority_grant_id="grant-11",
    )
    assert canceled_request.status is SteeringStatus.CANCELED
    canceled_store.close()

    revoked_store, revoked, _ = _control(tmp_path / "revoked")
    revoked.revoke_authority("grant-11")
    revoked_request = revoked.request_steering(
        steering_id="steering-revoked",
        mission_id="mission-11",
        turn_id="turn-11",
        workspace=r"C:\Users\Marc\Projects\fixture",
        channel="phone",
        consent_id="consent-11",
        authority_grant_id="grant-11",
    )
    assert revoked_request.status is SteeringStatus.REVOKED
    revoked_store.close()
    store.close()


def test_portfolio_ranking_contract_roundtrip_and_phase_only_rollback(tmp_path):
    store, control, _adapter = _control(tmp_path)
    first = control.add_portfolio_item(
        MissionPortfolioItem(
            id="portfolio-urgent",
            item_id="portfolio-urgent",
            goal="repair failing project tests",
            source_evidence=["codex:tests"],
            confidence=0.9,
            available_time_minutes=60,
            urgency=0.9,
            value=0.8,
            attention_cost=0.2,
        )
    )
    control.add_portfolio_item(
        MissionPortfolioItem(
            id="portfolio-low",
            item_id="portfolio-low",
            goal="read optional research",
            source_evidence=["calendar:free-time"],
            confidence=0.5,
            available_time_minutes=30,
            urgency=0.1,
            value=0.2,
            attention_cost=0.8,
        )
    )
    ranked = control.rank_portfolio()
    assert ranked[0].item_id == first.item_id
    restored = MissionPortfolioItem.from_json(first.to_json())
    assert restored.to_dict() == first.to_dict()
    assert (
        AmbientPresence.from_json(
            AmbientPresence(
                id="presence-contract",
                presence_id="presence-contract",
                mission_id="mission-11",
                state=PresenceState.AWAY_FROM_PC,
            ).to_json()
        ).state
        is PresenceState.AWAY_FROM_PC
    )

    unrelated = sqlite3.connect(str(tmp_path / "ambient.db"))
    unrelated.execute("CREATE TABLE unrelated_data (value TEXT)")
    unrelated.execute("INSERT INTO unrelated_data VALUES ('preserve me')")
    unrelated.commit()
    unrelated.close()
    backup = store.backup(tmp_path / "ambient-backup.db")
    assert backup.exists()
    store.rollback()
    assert store.migration_version == 0
    row = store._conn.execute("SELECT value FROM unrelated_data").fetchone()
    assert row == ("preserve me",)
    store.migrate()
    assert store.migration_version == 1
    store.close()


def test_guardian_emergency_stop_and_revocation_are_boundary_callbacks(tmp_path):
    class GuardianSpy:
        def __init__(self):
            self.stops = []
            self.revocations = []

        def emergency_stop(self, *, reason="emergency stop"):
            self.stops.append(reason)

        def revoke(self, session_id, *, reason="authority revoked"):
            self.revocations.append((session_id, reason))

    guardian = GuardianSpy()
    store = AmbientStore(tmp_path / "guardian.db")
    control = AmbientMissionControl(store, guardian=guardian)
    control.register_mission(
        mission_id="mission-guardian",
        thread_id="thread-guardian",
        turn_id="turn-guardian",
        workspace=r"C:\guardian",
        goal="guardian boundary fixture",
    )
    scope = AuthorityGrantScope(
        id="grant-guardian",
        grant_id="grant-guardian",
        mission_id="mission-guardian",
        turn_id="turn-guardian",
        workspace=r"C:\guardian",
        capability="codex:fork-stop",
        provenance={"created_by": "Marc"},
    )
    control.register_existing_authority_scope(scope)
    control.record_presence(
        "mission-guardian",
        PresenceState.AWAY_FROM_PC,
        uncertainty=["affect hypothesis is short-lived and non-authoritative"],
    )
    control.emergency_stop(reason="guardian fixture stop")
    assert guardian.stops == ["guardian fixture stop"]
    control.revoke_authority("grant-guardian", reason="guardian fixture revoke")
    assert guardian.revocations == [("mission-guardian", "guardian fixture revoke")]
    assert store.get("grant-guardian").revoked is True
    store.close()


def test_restart_recovery_marks_continuity_stale_and_preserves_emergency_stop(
    tmp_path,
):
    store, control, adapter = _control(tmp_path)
    control.start_speech("mission-11", channel="voice")
    control.emergency_stop(reason="persisted emergency fixture")
    store.close()

    reopened = AmbientStore(tmp_path / "ambient.db")
    assert reopened.recover_interrupted() >= 1
    assert reopened.recover_interrupted() == 0
    assert all(
        item.continuity_state == "stale_after_restart"
        for item in reopened.list_records(kind="continuity_acknowledgment")
    )
    recovered_control = AmbientMissionControl(reopened, channel_adapter=adapter)
    request = recovered_control.request_steering(
        steering_id="steering-after-emergency-restart",
        mission_id="mission-11",
        turn_id="turn-11",
        workspace=r"C:\Users\Marc\Projects\fixture",
        channel="phone",
        consent_id="consent-11",
        authority_grant_id="grant-11",
    )
    assert request.status is SteeringStatus.EMERGENCY_STOPPED
    reopened.close()


def test_steering_execution_rechecks_consent_and_exact_authority_scope(tmp_path):
    store, control, _adapter = _control(tmp_path)
    approved = control.request_steering(
        steering_id="steering-consent-recheck",
        mission_id="mission-11",
        turn_id="turn-11",
        workspace=r"C:\Users\Marc\Projects\fixture",
        channel="phone",
        consent_id="consent-11",
        authority_grant_id="grant-11",
    )
    control.revoke_channel_consent("consent-11", reason="fixture recheck")
    assert (
        control.execute_steering(approved.steering_id, tests_failed=True).status
        is SteeringStatus.DECLINED
    )
    store.close()

    scope_store, scope_control, _ = _control(tmp_path / "scope")
    approved = scope_control.request_steering(
        steering_id="steering-scope-recheck",
        mission_id="mission-11",
        turn_id="turn-11",
        workspace=r"C:\Users\Marc\Projects\fixture",
        channel="phone",
        consent_id="consent-11",
        authority_grant_id="grant-11",
    )
    # Simulate contradictory persisted evidence without using the public
    # registration boundary to replace an already-referenced scope.
    scope_store.put(
        AuthorityGrantScope(
            id="grant-11",
            grant_id="grant-11",
            mission_id="mission-11",
            turn_id="turn-11",
            workspace=r"C:\Users\Marc\Projects\other",
            capability="codex:fork-stop",
        )
    )
    assert (
        scope_control.execute_steering(approved.steering_id, tests_failed=True).status
        is SteeringStatus.REVOKED
    )
    scope_store.close()


def test_update_persistence_redacts_sensitive_text_and_rejects_conflicts(tmp_path):
    store, control, _adapter = _control(tmp_path)
    update = control.record_update(
        _update(
            summary=(
                "command: powershell -File C:\\secret\\run.ps1 token=super-secret"
            ),
            changed=["token=another-secret"],
        )
    )
    payloads = [record.to_json() for record in store.list_records()]
    assert all("super-secret" not in payload for payload in payloads)
    assert all("another-secret" not in payload for payload in payloads)
    assert "[REDACTED_COMMAND]" in update.summary
    assert control.record_update(update).to_json() == update.to_json()
    with pytest.raises(ValueError, match="conflicting duplicate"):
        control.record_update(
            _update(
                sequence=1,
                summary="a different update with the same identity",
            )
        )
    store.close()
