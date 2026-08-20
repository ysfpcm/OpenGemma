"""Deterministic Phase 10 acceptance lane.

Every fixture in this file is local and side-effect free.  In particular, the
Home Assistant scenarios are state calculations, not connector calls, and the
Codex scenario writes only below its experiment root.
"""

# ruff: noqa: E501

from __future__ import annotations

import pytest

from openjarvis.learning.laboratory import (
    ApprovalRequiredError,
    ArtifactIntegrityError,
    ArtifactStage,
    AuthorityBoundaryError,
    CandidateLifecycleError,
    CandidateStatus,
    Consent,
    ConsentRequiredError,
    Correction,
    HomeAssistantLearningFixture,
    LaboratoryStore,
    LearningCandidate,
    LearningLaboratory,
    ReplayCase,
    ReplayStatus,
    ShadowStatus,
    digest_tree,
    historical_codex_mission_fixtures,
)


def _laboratory(tmp_path):
    store = LaboratoryStore(tmp_path / "ophanim.db")
    return store, LearningLaboratory(store, signing_key="test-signing-key")


def _sunset_correction() -> Correction:
    return Correction(
        id="correction-entry-light-1",
        statement=(
            "When I leave after sunset, keep the entry light on even if the "
            "general Away routine turns other lights off."
        ),
        context={"source_version": "home-fixture-v1", "routine": "away"},
        provenance={"source": "Marc", "episode_id": "episode-departure-1"},
        causal_parents=["episode-departure-1"],
        sensitivity_labels=["personal-home"],
        confidence=1.0,
    )


def _promoted_home_candidate(lab: LearningLaboratory):
    correction, evidence, adaptation = lab.record_correction(_sunset_correction())
    candidate = lab.generate_policy_candidate(correction.id)
    lab.sandbox_candidate(candidate.id)
    fixture = HomeAssistantLearningFixture()
    replay = lab.run_replay(
        candidate.id,
        fixture.cases(),
        suite=fixture.scenario_id,
    )
    shadow = lab.run_shadow(candidate.id, fixture.cases(), suite=fixture.scenario_id)
    decision = lab.approve(candidate.id, marc_approved=True)
    artifact = lab.stage(candidate.id)
    return (
        correction,
        evidence,
        adaptation,
        candidate,
        replay,
        shadow,
        decision,
        artifact,
    )


def test_tangible_correction_replay_shadow_activation_and_exact_rollback(tmp_path):
    store, lab = _laboratory(tmp_path)
    correction, evidence, adaptation, candidate, replay, shadow, decision, artifact = (
        _promoted_home_candidate(lab)
    )

    assert {item.evidence_kind for item in evidence} == {"relationship", "procedural"}
    assert adaptation.production_weights_changed is False
    assert adaptation.production_prompts_changed is False
    assert adaptation.production_policies_changed is False
    assert adaptation.current_plan_revision
    assert replay.status == ReplayStatus.PASSED.value
    assert replay.metrics["candidate_pass_rate"] == 1.0
    assert replay.metrics["baseline_pass_rate"] < 1.0
    assert shadow.status == ShadowStatus.PASSED.value
    assert shadow.real_effect is False
    assert shadow.authority_created is False
    assert shadow.external_effects == []
    assert decision.decision == "approved"
    assert decision.explicit_marc_approval is True
    assert decision.requirements["rollback_verified"] is True
    assert artifact.stage == ArtifactStage.STAGED.value

    with pytest.raises(ApprovalRequiredError):
        lab.activate(candidate.id, explicit_activation=False)
    lab.activate(candidate.id, explicit_activation=True)
    active = lab.store.get(candidate.id)
    assert isinstance(active, LearningCandidate)
    assert active.status == CandidateStatus.ACTIVE.value
    assert (
        lab.evaluate_policy({"phase": "sunset", "marc_leaves": True})["entry_light"]
        == "on"
    )
    assert (
        lab.evaluate_policy({"phase": "daytime", "marc_leaves": True})["entry_light"]
        == "off"
    )

    rollback = lab.rollback(candidate.id, reason="Phase 10 deterministic rollback test")
    assert rollback.exact_restore is True
    assert rollback.rollback_verified is True
    assert rollback.authority_scope_restored is True
    rolled = lab.store.get(candidate.id)
    assert isinstance(rolled, LearningCandidate)
    assert rolled.status == CandidateStatus.ROLLED_BACK.value
    assert (
        lab.evaluate_policy({"phase": "sunset", "marc_leaves": True})["entry_light"]
        == "off"
    )
    assert lab.store.get("phase10-runtime-state").active_artifact_id is None
    assert not lab.store.list_records(kind="promotion_decision", status="active")
    store.close()


def test_replay_cases_cover_day_sunset_overnight_guest_and_contradiction(tmp_path):
    store, lab = _laboratory(tmp_path)
    correction, _, _ = lab.record_correction(_sunset_correction())
    candidate = lab.generate_policy_candidate(correction.id)
    lab.sandbox_candidate(candidate.id)
    fixture = HomeAssistantLearningFixture()
    run = lab.run_replay(candidate.id, fixture.cases(), suite=fixture.scenario_id)

    assert run.passed
    outputs = {item["case_id"]: item["candidate_output"] for item in run.case_results}
    assert outputs["phase10-case-daytime"]["entry_light"] == "off"
    assert outputs["phase10-case-sunset"]["entry_light"] == "on"
    assert outputs["phase10-case-overnight"]["entry_light"] == "on"
    assert outputs["phase10-case-guest-present"]["entry_light"] == "off"
    assert outputs["phase10-case-contradictory-command"]["entry_light"] == "off"
    assert run.adversarial_passed and run.counterfactual_passed
    assert run.unauthorized_actions == 0 and run.duplicate_actions == 0
    assert len(store.list_records(kind="regression_result")) == 1
    store.close()


def test_restart_duplicate_and_phase10_only_rollback(tmp_path):
    db = tmp_path / "restart.db"
    store = LaboratoryStore(db)
    store._conn.execute(
        "CREATE TABLE unrelated_user_data (id TEXT PRIMARY KEY, value TEXT)"
    )
    store._conn.execute("INSERT INTO unrelated_user_data VALUES ('keep', 'yes')")
    store._conn.commit()
    lab = LearningLaboratory(store, signing_key="test-signing-key")
    correction = _sunset_correction()
    first = lab.record_correction(correction)
    second = lab.record_correction(_sunset_correction())
    assert first[0].id == second[0].id
    assert len(store.list_records(kind="correction")) == 1
    store.close()

    restarted = LaboratoryStore(db)
    assert restarted.get(correction.id).statement == correction.statement
    assert restarted.migration_version == 1
    backup = tmp_path / "phase10-backup.db"
    assert restarted.backup(backup) == backup
    assert backup.exists()
    restarted.rollback()
    row = restarted._conn.execute(
        "SELECT value FROM unrelated_user_data WHERE id = 'keep'"
    ).fetchone()
    assert row == ("yes",)
    assert (
        restarted._conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'phase10_records'"
        ).fetchone()
        is None
    )
    restarted.migrate()
    assert restarted.migration_version == 1
    restarted.close()


def test_conflicting_correction_and_direct_candidate_transition_fail_closed(tmp_path):
    store, lab = _laboratory(tmp_path)
    correction = _sunset_correction()
    lab.record_correction(correction)
    conflicting = Correction(
        id=correction.id,
        statement="a contradictory correction with the same delivery ID",
        context=correction.context,
    )
    with pytest.raises(ValueError):
        lab.record_correction(conflicting)

    candidate = lab.generate_policy_candidate(correction.id)
    candidate.status = CandidateStatus.SANDBOXED.value
    with pytest.raises(CandidateLifecycleError):
        store.put(candidate)
    assert store.get(candidate.id).status == CandidateStatus.CANDIDATE.value
    store.close()


def test_duplicate_replay_delivery_returns_the_durable_run(tmp_path):
    store, lab = _laboratory(tmp_path)
    correction, _, _ = lab.record_correction(_sunset_correction())
    candidate = lab.generate_policy_candidate(correction.id)
    lab.sandbox_candidate(candidate.id)
    fixture = HomeAssistantLearningFixture()
    first = lab.run_replay(candidate.id, fixture.cases(), suite=fixture.scenario_id)
    second = lab.run_replay(candidate.id, fixture.cases(), suite=fixture.scenario_id)
    assert second.id == first.id
    assert len(store.list_records(kind="replay_run")) == 1
    assert len(store.list_records(kind="regression_result")) == 1
    store.close()


def test_lifecycle_requires_each_distinct_promotion_state_and_marc_approval(tmp_path):
    store, lab = _laboratory(tmp_path)
    correction, _, _ = lab.record_correction(_sunset_correction())
    candidate = lab.generate_policy_candidate(correction.id)
    with pytest.raises(CandidateLifecycleError):
        lab.run_replay(
            candidate.id, HomeAssistantLearningFixture().cases(), suite="bad"
        )
    lab.sandbox_candidate(candidate.id)
    replay = lab.run_replay(
        candidate.id,
        HomeAssistantLearningFixture().cases(),
        suite="phase10-entry-light-exception",
    )
    assert replay.passed
    lab.run_shadow(
        candidate.id,
        HomeAssistantLearningFixture().cases(),
        suite="phase10-entry-light-exception",
    )
    with pytest.raises(ApprovalRequiredError):
        lab.approve(candidate.id, marc_approved=False)
    assert lab.store.get(candidate.id).status == CandidateStatus.SHADOW.value
    decision = lab.approve(candidate.id, marc_approved=True)
    assert decision.decision == "approved"
    artifact = lab.stage(candidate.id)
    assert lab.store.get(candidate.id).status == CandidateStatus.STAGED.value
    with pytest.raises(CandidateLifecycleError):
        lab.stage(candidate.id)
    assert artifact.signature
    store.close()


def test_privacy_and_consent_gate_model_adaptation(tmp_path):
    store, lab = _laboratory(tmp_path)
    candidate = lab.generate_candidate(
        candidate_kind="model_adapter",
        named_outcome="offline trace capability score",
        proposal={"adapter": "isolated-v1", "offline_only": True, "isolated": True},
        baseline_behavior={"score": 0.5},
        expected_behavior={"score": 0.6},
        source_ids=["trace-sensitive-1"],
        source_version="trace-v1",
    )
    with pytest.raises(ConsentRequiredError):
        lab.sandbox_candidate(candidate.id)
    consent = Consent(
        id="consent-1",
        purpose="offline isolated model experiment",
        source_ids=["trace-sensitive-1"],
        allowed_uses=["offline_experiment"],
        sensitivity_ceiling="personal",
        explicit=True,
        privacy_reviewed=True,
        reviewer="Marc",
    )
    lab.save_consent(consent)
    candidate.consent_id = consent.id
    lab.store.put(candidate)
    lab.sandbox_candidate(candidate.id)
    assert lab.store.get(candidate.id).status == CandidateStatus.SANDBOXED.value

    out_of_scope = lab.generate_candidate(
        candidate_kind="model_adapter",
        named_outcome="out of scope trace test",
        proposal={"adapter": "isolated-v2", "offline_only": True, "isolated": True},
        baseline_behavior={},
        expected_behavior={},
        source_ids=["trace-sensitive-2"],
        consent_id=consent.id,
    )
    with pytest.raises((ConsentRequiredError, ValueError)):
        lab.sandbox_candidate(out_of_scope.id)
    store.close()


def test_authority_boundary_rejects_candidate_that_would_create_guardian_scope(
    tmp_path,
):
    store, lab = _laboratory(tmp_path)
    with pytest.raises(AuthorityBoundaryError):
        lab.generate_candidate(
            candidate_kind="policy_pack",
            named_outcome="authority widening",
            proposal={"creates_guardian_authority": True},
            baseline_behavior={},
            expected_behavior={},
        )
    store.close()


@pytest.mark.parametrize(
    "case_kwargs,expected_status,expected_issue",
    [
        (
            {"canceled": True},
            CandidateStatus.SANDBOXED.value,
            "canceled",
        ),
        (
            {"source_version": "old-fixture-v0"},
            CandidateStatus.REJECTED.value,
            "stale source",
        ),
        (
            {"disagreement": True},
            CandidateStatus.REJECTED.value,
            "disagreement",
        ),
    ],
)
def test_replay_stale_disagreement_and_cancellation_fail_closed(
    tmp_path, case_kwargs, expected_status, expected_issue
):
    store, lab = _laboratory(tmp_path)
    correction, _, _ = lab.record_correction(_sunset_correction())
    candidate = lab.generate_policy_candidate(correction.id)
    lab.sandbox_candidate(candidate.id)
    base = HomeAssistantLearningFixture().cases()[1]
    case = ReplayCase(
        id=f"case-{expected_issue.replace(' ', '-')}",
        suite="edge",
        name="edge case",
        context=base.context,
        expected=base.expected,
        tags=["adversarial", "counterfactual"],
        source_version=case_kwargs.pop("source_version", base.source_version),
        canceled=case_kwargs.pop("canceled", False),
        disagreement=case_kwargs.pop("disagreement", False),
    )
    run = lab.run_replay(candidate.id, [case], suite="edge", budget_limit=10)
    assert lab.store.get(candidate.id).status == expected_status
    assert any(expected_issue in issue for issue in run.issues)
    store.close()


def test_budget_exceeded_is_persisted_and_candidate_does_not_promote(tmp_path):
    store, lab = _laboratory(tmp_path)
    correction, _, _ = lab.record_correction(_sunset_correction())
    candidate = lab.generate_policy_candidate(correction.id)
    lab.sandbox_candidate(candidate.id)
    fixture = HomeAssistantLearningFixture()
    run = lab.run_replay(
        candidate.id, fixture.cases(), suite=fixture.scenario_id, budget_limit=1
    )
    assert run.budget_exceeded is True
    assert store.list_records(kind="regression_result")[0].passed is False
    assert lab.store.get(candidate.id).status == CandidateStatus.REJECTED.value
    store.close()


def test_restart_recovery_marks_running_replay_unknown_not_success(tmp_path):
    store, lab = _laboratory(tmp_path)
    # Use a real running record so recovery exercises the durable path.
    from openjarvis.learning.laboratory import ReplayRun

    run = ReplayRun(id="running-replay", candidate_id="missing", suite="restart")
    store.put(run)
    store.close()
    restarted = LaboratoryStore(tmp_path / "ophanim.db")
    assert restarted.recover_interrupted() == 1
    recovered = restarted.get("running-replay")
    assert recovered.status == ReplayStatus.FAILED.value
    assert recovered.passed is False
    assert "unknown" in recovered.issues[-1]
    restarted.close()


def test_codex_candidate_isolated_and_seeded_forgetting_is_rejected(tmp_path):
    store, lab = _laboratory(tmp_path)
    primary = tmp_path / "primary"
    primary.mkdir()
    (primary / "mission.md").write_text(
        "primary workspace remains unchanged\n", encoding="utf-8"
    )
    before = digest_tree(primary)
    cases = [item.as_case() for item in historical_codex_mission_fixtures()]
    candidate, run, isolation = lab.run_codex_template_experiment(
        primary_workspace=primary,
        experiment_root=tmp_path / "experiments",
        cases=cases,
    )
    assert run.status == ReplayStatus.FAILED.value
    assert run.protected_capability_regressions == ["codex-history-004"]
    assert run.forgetting_regressions == ["codex-history-004"]
    assert lab.store.get(candidate.id).status == CandidateStatus.REJECTED.value
    assert isolation.primary_unchanged
    assert isolation.workspace_is_bounded
    assert digest_tree(primary) == before
    assert (
        tmp_path
        / "experiments"
        / f"phase10-{candidate.id}"
        / "phase10-candidate-marker.txt"
    ).exists()
    assert not lab.store.list_records(kind="artifact_version")
    assert not lab.store.list_records(kind="promotion_decision")
    assert all(
        event["details"].get("authority_created") is not True
        for event in lab.store.audit_events(candidate.id)
    )
    store.close()


def test_artifact_signature_tamper_is_rejected(tmp_path):
    store, lab = _laboratory(tmp_path)
    _, _, _, candidate, _, _, _, artifact = _promoted_home_candidate(lab)
    artifact.signature = "tampered"
    store.put(artifact)
    with pytest.raises(ArtifactIntegrityError):
        lab.activate(candidate.id, explicit_activation=True)
    store.close()


def test_artifact_scope_and_runtime_pointer_tampering_fail_closed(tmp_path):
    store, lab = _laboratory(tmp_path)
    _, _, _, candidate, _, _, _, artifact = _promoted_home_candidate(lab)
    artifact.activation_scope["live_external_effects"] = True
    store.put(artifact)
    with pytest.raises(ArtifactIntegrityError):
        lab.activate(candidate.id, explicit_activation=True)

    artifact.activation_scope["live_external_effects"] = False
    lab.store.put(artifact)
    lab.activate(candidate.id, explicit_activation=True)
    runtime = lab.store.get("phase10-runtime-state")
    runtime.active_artifact_id = None
    runtime.baseline_behavior["sunset"] = "on"
    lab.store.put(runtime)
    assert (
        lab.evaluate_policy({"phase": "sunset", "marc_leaves": True})["entry_light"]
        == "off"
    )
    store.close()


def test_rollback_restores_a_previously_active_artifact(tmp_path):
    store, lab = _laboratory(tmp_path)
    _, _, _, first_candidate, _, _, _, first_artifact = _promoted_home_candidate(lab)
    lab.activate(first_candidate.id, explicit_activation=True)

    second_correction = Correction(
        id="correction-entry-light-2",
        statement=_sunset_correction().statement,
        context={"source_version": "home-fixture-v1", "routine": "away"},
    )
    correction, _, _ = lab.record_correction(second_correction)
    second_candidate = lab.generate_policy_candidate(correction.id)
    lab.sandbox_candidate(second_candidate.id)
    fixture = HomeAssistantLearningFixture()
    lab.run_replay(second_candidate.id, fixture.cases(), suite=fixture.scenario_id)
    lab.run_shadow(second_candidate.id, fixture.cases(), suite=fixture.scenario_id)
    lab.approve(second_candidate.id, marc_approved=True)
    second_artifact = lab.stage(second_candidate.id)
    lab.activate(second_candidate.id, explicit_activation=True)

    rollback = lab.rollback(second_candidate.id, reason="restore prior active artifact")
    assert rollback.restored_artifact_id == first_artifact.id
    assert rollback.exact_restore is True
    assert (
        lab.store.get("phase10-runtime-state").active_candidate_id == first_candidate.id
    )
    assert lab.store.get(first_candidate.id).status == CandidateStatus.ACTIVE.value
    assert (
        lab.store.get(second_candidate.id).status == CandidateStatus.ROLLED_BACK.value
    )
    assert second_artifact.stage == ArtifactStage.STAGED.value
    assert (
        lab.evaluate_policy({"phase": "sunset", "marc_leaves": True})["entry_light"]
        == "on"
    )
    store.close()
