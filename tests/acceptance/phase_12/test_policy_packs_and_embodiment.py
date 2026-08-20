from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from openjarvis.cognition import ActionLedger, ActionState
from openjarvis.guardian.kernel import ActionRegistry, GuardianKernel
from openjarvis.policy_packs import (
    ARRIVAL_PUBLISHER,
    ARRIVAL_SIGNING_KEY,
    DIGITAL_PUBLISHER,
    DIGITAL_SIGNING_KEY,
    ArrivalGuardianPack,
    CommunicationState,
    CommunicationStatus,
    ControllerStatus,
    DependencySpec,
    DeterministicEmbodimentController,
    DigitalEmbodimentPack,
    EmbodimentIntent,
    EmbodimentSafetyError,
    FaultKind,
    PackInstallError,
    PackInstallState,
    PackManager,
    PerceptionSnapshot,
    Phase12Store,
    PolicyEvaluation,
    PolicyOutcome,
    PolicyPackManifest,
    PolicyPackRegistry,
    arrival_installation_grant,
    arrival_replay_fixtures,
    communication,
    digital_installation_grant,
    embodiment_intent,
    perception,
    utc_now,
)


def _manager(store: Phase12Store, *packs):
    registry = PolicyPackRegistry()
    for pack in packs:
        registry.register(pack)
    return PackManager(
        store,
        registry,
        trusted_publishers={
            ARRIVAL_PUBLISHER: ARRIVAL_SIGNING_KEY,
            DIGITAL_PUBLISHER: DIGITAL_SIGNING_KEY,
        },
    ), registry


def _embodiment(tmp_path: Path):
    db = tmp_path / "phase12.db"
    store = Phase12Store(db)
    pack = DigitalEmbodimentPack()
    manager, registry = _manager(store, pack)
    grant = digital_installation_grant("digital-install-1", grant_id="guardian-g1")
    manager.install(pack, grant)
    ledger = ActionLedger(db)
    guardian = GuardianKernel(ledger, ActionRegistry())
    guardian.grant(
        grant_id="guardian-g1",
        session_id="digital-install-1",
        capability="embodiment:simulate",
        scope={
            "action_type": "embodiment.simulated_actuator",
            "target": "virtual-actuator",
        },
    )
    controller = DeterministicEmbodimentController(store, manager, guardian)
    return db, store, manager, registry, ledger, guardian, controller


def test_manifest_signature_integrity_and_safe_roundtrip():
    pack = ArrivalGuardianPack()
    manifest = PolicyPackManifest.from_json(pack.manifest.to_json())
    assert manifest.to_dict() == pack.manifest.to_dict()
    assert manifest.verify({ARRIVAL_PUBLISHER: ARRIVAL_SIGNING_KEY}) == (
        True,
        "verified",
    )
    tampered = replace(manifest, name="Arrival Guardian with an untrusted change")
    assert tampered.verify({ARRIVAL_PUBLISHER: ARRIVAL_SIGNING_KEY})[0] is False
    with pytest.raises(ValueError, match="not allowed"):
        replace(manifest, ui_metadata={"token": "must-not-persist"})


def test_arrival_portability_replay_duplicate_and_common_timeline(tmp_path):
    store = Phase12Store(tmp_path / "packs.db")
    pack = ArrivalGuardianPack()
    manager, _registry = _manager(store, pack)
    installation = manager.install(
        pack, arrival_installation_grant("arrival-install-1")
    )
    assert installation.state is PackInstallState.INSTALLED
    evaluations = [
        manager.replay("arrival-guardian", event) for event in arrival_replay_fixtures()
    ]
    assert [item.outcome for item in evaluations] == [
        PolicyOutcome.PROPOSED,
        PolicyOutcome.PROPOSED,
        PolicyOutcome.PROPOSED,
        PolicyOutcome.BLOCKED,
        PolicyOutcome.BLOCKED,
    ]
    assert all(
        item.live_effects is False and item.simulation_only for item in evaluations
    )
    assert {item.stage for item in evaluations[0].timeline} == {
        "Plan",
        "Authorization",
        "ActionAttempt",
        "Verification",
    }
    duplicate = manager.replay("arrival-guardian", arrival_replay_fixtures()[0])
    assert duplicate.outcome is PolicyOutcome.REPLAYED
    assert len(store.replay_records("arrival-guardian")) == 5
    assert "false-presence-suppressed" in evaluations[3].reasons
    assert "service-outage-fail-closed" in evaluations[4].reasons
    store.close()


def test_arrival_replay_requires_complete_fresh_provenance_bearing_evidence(tmp_path):
    store = Phase12Store(tmp_path / "packs.db")
    pack = ArrivalGuardianPack()
    manager, _registry = _manager(store, pack)
    manager.install(pack, arrival_installation_grant("arrival-install-1"))
    event = arrival_replay_fixtures()[0]

    missing_provenance = replace(
        event,
        dedupe_key="phase12:missing-provenance",
        evidence=(replace(event.evidence[0], provenance={}), *event.evidence[1:]),
    )
    assert (
        manager.replay("arrival-guardian", missing_provenance).outcome
        is PolicyOutcome.BLOCKED
    )

    stale = replace(
        event,
        dedupe_key="phase12:stale",
        evidence=(
            replace(
                event.evidence[0],
                observed_at=(
                    datetime.now(timezone.utc) - timedelta(minutes=10)
                ).isoformat(),
            ),
            *event.evidence[1:],
        ),
    )
    assert manager.replay("arrival-guardian", stale).outcome is PolicyOutcome.BLOCKED

    future = replace(
        event,
        dedupe_key="phase12:future",
        evidence=(
            replace(
                event.evidence[0],
                observed_at=(
                    datetime.now(timezone.utc) + timedelta(minutes=10)
                ).isoformat(),
            ),
            *event.evidence[1:],
        ),
    )
    assert manager.replay("arrival-guardian", future).outcome is PolicyOutcome.BLOCKED

    missing_source = replace(
        event,
        dedupe_key="phase12:missing-source",
        sources=("presence",),
    )
    assert (
        manager.replay("arrival-guardian", missing_source).outcome
        is PolicyOutcome.BLOCKED
    )
    store.close()


def test_installation_requires_registered_code_and_replay_boundary(tmp_path):
    store = Phase12Store(tmp_path / "packs.db")
    arrival = ArrivalGuardianPack()
    manager, registry = _manager(store, arrival)

    class UnregisteredPack:
        manifest = arrival.manifest

        def evaluate(self, event, installation):
            return arrival.evaluate(event, installation)

    with pytest.raises(PackInstallError, match="registered verified implementation"):
        manager.install(UnregisteredPack(), arrival_installation_grant("unregistered"))

    class UntrustedEvaluationPack:
        manifest = replace(
            arrival.manifest,
            pack_id="untrusted-evaluation",
            name="Untrusted Evaluation Pack",
        ).signed(ARRIVAL_SIGNING_KEY)

        def evaluate(self, event, installation):
            return PolicyEvaluation(
                evaluation_id="forged-evaluation",
                event_id="forged-event",
                pack_id=self.manifest.pack_id,
                pack_version=self.manifest.version,
                outcome=PolicyOutcome.PROPOSED,
                explanation="forged",
                simulation_only=False,
                live_effects=True,
            )

    untrusted = UntrustedEvaluationPack()
    registry.register(untrusted)
    grant = replace(
        arrival_installation_grant("untrusted-install"),
        pack_id=untrusted.manifest.pack_id,
    )
    manager.install(untrusted, grant)
    evaluation = manager.replay(
        untrusted.manifest.pack_id, arrival_replay_fixtures()[0]
    )
    assert evaluation.outcome is PolicyOutcome.BLOCKED
    assert evaluation.live_effects is False
    assert "evaluation-event-mismatch" in evaluation.reasons
    store.close()


def test_typed_proposal_parameters_fail_closed(tmp_path):
    store = Phase12Store(tmp_path / "packs.db")
    pack = DigitalEmbodimentPack()
    manager, _registry = _manager(store, pack)
    manager.install(pack, digital_installation_grant("digital-install-1"))
    with pytest.raises(PackInstallError, match="parameters"):
        manager.build_proposal(
            "digital-embodiment",
            action_type="embodiment.simulated_actuator",
            parameters={"target": "virtual-actuator"},
            plan_id="plan-invalid",
            idempotency_key="invalid-parameters",
        )
    store.close()


def test_pack_installation_signature_dependency_authority_and_lifecycle(tmp_path):
    store = Phase12Store(tmp_path / "packs.db")
    arrival = ArrivalGuardianPack()
    manager, registry = _manager(store, arrival)
    bad = replace(arrival.manifest, signature="hmac-sha256:not-the-signature")

    class BadPack:
        manifest = bad

        def evaluate(self, event, installation):
            return arrival.evaluate(event, installation)

    with pytest.raises(PackInstallError, match="signature"):
        manager.install(BadPack(), arrival_installation_grant("bad-install"))
    with pytest.raises(PackInstallError, match="widens"):
        manager.install(
            arrival,
            replace(
                arrival_installation_grant("bad-authority"),
                allowed_action_types=("arrival.prepare_entry", "unregistered.action"),
            ),
        )
    dependency_manifest = replace(
        arrival.manifest,
        pack_id="arrival-guardian-with-missing-dependency",
        dependencies=(DependencySpec("missing-pack", ">=1.0.0"),),
    ).signed(ARRIVAL_SIGNING_KEY)

    class MissingDependencyPack:
        manifest = dependency_manifest

        def evaluate(self, event, installation):
            return arrival.evaluate(event, installation)

    registry.register(MissingDependencyPack())
    with pytest.raises(PackInstallError, match="missing dependency"):
        manager.install(
            MissingDependencyPack(),
            replace(
                arrival_installation_grant("missing-dependency-install"),
                pack_id="arrival-guardian-with-missing-dependency",
            ),
        )

    v11 = ArrivalGuardianPack(version="1.1.0")
    registry.register(v11)
    manager.install(arrival, arrival_installation_grant("arrival-install-1"))
    upgraded = manager.upgrade(v11)
    assert upgraded.manifest.version == "1.1.0"
    downgraded = manager.downgrade(arrival)
    assert downgraded.manifest.version == "1.0.0"
    rolled_back = manager.rollback("arrival-guardian")
    assert rolled_back.manifest.version == "1.1.0"
    uninstalled = manager.uninstall("arrival-guardian")
    assert uninstalled.state is PackInstallState.UNINSTALLED
    reinstalled = manager.install(v11, arrival_installation_grant("arrival-install-2"))
    assert reinstalled.state is PackInstallState.INSTALLED
    operations = [item["operation"] for item in store.pack_events("arrival-guardian")]
    assert operations == [
        "install",
        "upgrade",
        "downgrade",
        "rollback",
        "uninstall",
        "reinstall",
    ]
    store.close()


def test_migration_failure_preserves_previous_version_and_rollback_is_local(tmp_path):
    store = Phase12Store(tmp_path / "packs.db")
    arrival = ArrivalGuardianPack()
    v11 = ArrivalGuardianPack(version="1.1.0")
    manager, registry = _manager(store, arrival, v11)
    manager.install(arrival, arrival_installation_grant("arrival-install-1"))

    def fail(_old, _new):
        raise RuntimeError("seeded migration failure")

    manager.migration_runner = fail
    with pytest.raises(PackInstallError, match="previous version remains"):
        manager.upgrade(v11)
    assert store.get_installation("arrival-guardian").manifest.version == "1.0.0"
    assert store.pack_events("arrival-guardian")[-1]["to_state"] == "failed"

    store._conn.execute("CREATE TABLE unrelated_phase12_test(value TEXT)")
    store._conn.execute("INSERT INTO unrelated_phase12_test VALUES ('preserve')")
    backup = store.backup(tmp_path / "packs.backup.db")
    assert backup.exists()
    store.rollback()
    assert store.migration_version == 0
    assert store._conn.execute(
        "SELECT value FROM unrelated_phase12_test"
    ).fetchone() == ("preserve",)
    store.migrate()
    store.close()


def test_simulated_embodiment_uses_shared_causal_lifecycle_and_is_idempotent(tmp_path):
    _db, store, _manager_instance, _registry, ledger, _guardian, controller = (
        _embodiment(tmp_path)
    )
    now = utc_now()
    intent = embodiment_intent("intent-success")
    result = controller.submit(
        intent,
        PerceptionSnapshot("perception-success", now, {"virtual_arm": 0.0}),
        CommunicationState("connection-success", CommunicationStatus.CONNECTED),
    )
    assert result.status is ControllerStatus.ACCEPTED
    assert result.verification and result.verification.succeeded
    assert result.live_effects is False
    assert ledger.state(result.action_id) is ActionState.VERIFIED
    assert [item.stage for item in result.timeline] == [
        "Plan",
        "Authorization",
        "ActionAttempt",
        "Verification",
    ]
    assert len(ledger.attempts(result.action_id)) == 1
    assert len(store.actuator_attempts(result.action_id)) == 1

    duplicate = controller.submit(
        intent,
        PerceptionSnapshot("perception-success", now, {"virtual_arm": 0.0}),
        CommunicationState("connection-success", CommunicationStatus.CONNECTED),
    )
    assert duplicate.status is ControllerStatus.DUPLICATE
    assert duplicate.state["sequence"] == 1
    assert len(ledger.attempts(result.action_id)) == 1
    with pytest.raises(EmbodimentSafetyError):
        controller.simulator.direct_motor_command("virtual_arm", 1.0)
    with pytest.raises(ValueError):
        EmbodimentIntent(
            "intent-direct",
            "digital-embodiment",
            "digital-install-1",
            "embodiment.simulated_actuator",
            "virtual-actuator",
            {"motor_torque": 1.0},
        )
    store.close()
    ledger.close()


@pytest.mark.parametrize(
    "name,fault,perception_value,communication_value,expected",
    [
        (
            "unsafe",
            FaultKind.UNSAFE_MOTION,
            perception("p-unsafe"),
            communication(),
            ControllerStatus.REFUSED,
        ),
        (
            "stale",
            FaultKind.STALE_PERCEPTION,
            perception("p-stale", age_seconds=10),
            communication(),
            ControllerStatus.REFUSED,
        ),
        (
            "contradictory",
            FaultKind.CONTRADICTORY_PERCEPTION,
            perception("p-contradictory", contradictory=True),
            communication(),
            ControllerStatus.REFUSED,
        ),
        (
            "communication",
            FaultKind.COMMUNICATION_LOSS,
            perception("p-communication"),
            communication(lost=True),
            ControllerStatus.REFUSED,
        ),
        (
            "timeout",
            FaultKind.CONTROLLER_TIMEOUT,
            perception("p-timeout"),
            communication(),
            ControllerStatus.TIMED_OUT,
        ),
        (
            "canceled",
            FaultKind.CANCELED,
            perception("p-canceled"),
            communication(),
            ControllerStatus.REFUSED,
        ),
    ],
)
def test_embodiment_faults_fail_closed(
    tmp_path, name, fault, perception_value, communication_value, expected
):
    _db, store, _manager_instance, _registry, ledger, _guardian, controller = (
        _embodiment(tmp_path / name)
    )
    intent = embodiment_intent(
        f"intent-{name}", value=0.9 if fault is FaultKind.UNSAFE_MOTION else 0.1
    )
    result = controller.submit(
        intent, perception_value, communication_value, fault=fault
    )
    assert result.status is expected
    assert result.action_id is None
    assert result.live_effects is False
    assert (
        ledger._conn.execute("SELECT COUNT(*) FROM action_records").fetchone()[0] == 0
    )
    assert result.state["sequence"] == 0
    store.close()
    ledger.close()


def test_revocation_emergency_stop_and_restart_recovery(tmp_path):
    _db, store, manager, _registry, ledger, guardian, controller = _embodiment(
        tmp_path / "revocation"
    )
    manager.revoke("digital-embodiment", reason="fixture revoke")
    revoked = controller.submit(
        embodiment_intent("intent-revoked"),
        perception("p-revoked"),
        communication(),
        fault=FaultKind.REVOKED_AUTHORITY,
    )
    assert revoked.status is ControllerStatus.REFUSED
    assert revoked.action_id is None
    store.close()
    ledger.close()

    _db, store, manager, registry, ledger, guardian, controller = _embodiment(
        tmp_path / "restart"
    )
    pending = controller.submit(
        embodiment_intent("intent-restart"),
        perception("p-restart"),
        communication(),
        crash_after_authorization=True,
    )
    assert pending.status is ControllerStatus.ACCEPTED
    assert ledger.state(pending.action_id) is ActionState.AUTHORIZED
    store.close()
    ledger.close()

    reopened = Phase12Store(tmp_path / "restart" / "phase12.db")
    reopened_ledger = ActionLedger(tmp_path / "restart" / "phase12.db")
    reopened_guardian = GuardianKernel(reopened_ledger, ActionRegistry())
    resumed_controller = DeterministicEmbodimentController(
        reopened,
        PackManager(
            reopened,
            registry,
            trusted_publishers={DIGITAL_PUBLISHER: DIGITAL_SIGNING_KEY},
        ),
        reopened_guardian,
        guardian_grant_id="guardian-g1",
    )
    resumed = resumed_controller.resume(
        pending.action_id, perception("p-restart-resumed"), communication()
    )
    assert resumed.status is ControllerStatus.ACCEPTED
    assert resumed.verification and resumed.verification.succeeded
    assert reopened_ledger.state(pending.action_id) is ActionState.VERIFIED
    assert len(reopened_ledger.attempts(pending.action_id)) == 1
    duplicate_after_restart = resumed_controller.submit(
        embodiment_intent("intent-restart"),
        perception("p-restart-resumed"),
        communication(),
    )
    assert duplicate_after_restart.status is ControllerStatus.DUPLICATE
    assert len(reopened_ledger.attempts(pending.action_id)) == 1
    reopened.close()
    reopened_ledger.close()

    _db, store, _manager_instance, _registry, ledger, _guardian, controller = (
        _embodiment(tmp_path / "stop")
    )
    stop = controller.emergency_stop(reason="fixture emergency")
    stopped = controller.submit(
        embodiment_intent("intent-stopped"),
        perception("p-stopped"),
        communication(),
        fault=FaultKind.EMERGENCY_STOP,
    )
    assert stop.emergency and stopped.status is ControllerStatus.STOPPED
    assert stopped.state["halted"] is True
    assert (
        ledger._conn.execute("SELECT COUNT(*) FROM action_records").fetchone()[0] == 0
    )
    store.close()
    ledger.close()


def test_pack_revocation_blocks_restart_resume_and_persists_communication(tmp_path):
    _db, store, manager, _registry, ledger, _guardian, controller = _embodiment(
        tmp_path / "revoked-after-authorization"
    )
    pending = controller.submit(
        embodiment_intent("intent-revoked-after-authorization"),
        perception("p-revoked-after-authorization"),
        communication(),
        crash_after_authorization=True,
    )
    assert store.communication("phase12-connection") is not None
    manager.revoke("digital-embodiment", reason="revoked before restart resume")

    resumed = controller.resume(
        pending.action_id,
        perception("p-revoked-after-authorization-resume"),
        communication(),
    )
    assert resumed.status is ControllerStatus.REFUSED
    assert ledger.state(pending.action_id) is ActionState.FAILED
    assert ledger.attempts(pending.action_id) == []
    store.close()
    ledger.close()


def test_core_guardian_and_executor_have_no_phase12_policy_branches():
    root = Path(__file__).parents[3]
    kernel = (
        (root / "src/openjarvis/guardian/kernel.py").read_text(encoding="utf-8").lower()
    )
    executor = (
        (root / "src/openjarvis/cognition/actions.py")
        .read_text(encoding="utf-8")
        .lower()
    )
    assert "arrival" not in kernel
    assert "virtual_arm" not in kernel
    assert "arrival" not in executor
    assert "virtual_arm" not in executor
