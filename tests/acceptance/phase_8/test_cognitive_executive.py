"""Phase 8 exit-gate tests for durable executive coordination."""

# ruff: noqa: E501

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjarvis.connectors import DurableRagIndex
from openjarvis.executive import (
    AssignmentStatus,
    CommitmentStatus,
    CouncilStatus,
    DeterministicRagBenchmark,
    ExecutiveGoal,
    ExecutiveService,
    ExecutiveStore,
    FixtureCodexBoundary,
    GlobalWorkspace,
    GoalStatus,
    MissionTemplateName,
    MissionTemplates,
    SingleAgentRagBaseline,
    SpecialistClaim,
    SpecialistRole,
    run_rag_phase8_scenario,
)


def _service(
    tmp_path: Path,
) -> tuple[ExecutiveStore, ExecutiveService, FixtureCodexBoundary]:
    codex = FixtureCodexBoundary()
    store = ExecutiveStore(tmp_path / "executive.db")
    return store, ExecutiveService(store, codex=codex), codex


def _goal(service: ExecutiveService) -> ExecutiveGoal:
    goal = service.create_goal(
        "Bounded Phase 8 test objective",
        ["one evidence-backed subgoal completes"],
    )
    service.create_workspace(goal.id, current_focus="test objective")
    return goal


class _FailingCodexBoundary:
    def start_mission(self, *args: object, **kwargs: object) -> dict[str, str]:
        raise TimeoutError("fixture Codex timeout")

    def resume_observation(self, mission_id: str) -> dict[str, str]:
        raise TimeoutError(f"fixture resume timeout: {mission_id}")


def test_phase8_contracts_round_trip_with_evidence() -> None:
    goal = ExecutiveGoal(
        objective="Persist a durable objective",
        success_conditions=["goal can be recovered"],
        priority=70,
    )
    workspace = GlobalWorkspace(goal_id=goal.id, current_focus="recover")
    claim = SpecialistClaim(
        goal_id=goal.id,
        specialist_role=SpecialistRole.EVIDENCE_CRITIC.value,
        statement="The state is durable.",
        confidence=0.91,
        evidence_ids=["event-1"],
        evidence=[{"kind": "sqlite", "event": "event-1"}],
    )

    assert ExecutiveGoal.from_dict(goal.to_dict()).objective == goal.objective
    assert GlobalWorkspace.from_dict(workspace.to_dict()).goal_id == goal.id
    restored = SpecialistClaim.from_dict(claim.to_dict())
    assert restored.confidence == 0.91
    assert restored.evidence_ids == ["event-1"]

    with pytest.raises(ValueError, match="evidence"):
        SpecialistClaim(
            goal_id=goal.id,
            specialist_role="evidence-critic",
            statement="unsupported claim",
            confidence=0.5,
        )


def test_restart_recovers_without_replaying_completed_subgoal(tmp_path: Path) -> None:
    store, service, codex = _service(tmp_path)
    goal = _goal(service)
    council = service.create_council(goal.id, [SpecialistRole.SOFTWARE])
    completed = service.delegate(
        council.id,
        SpecialistRole.SOFTWARE,
        "Complete the research subgoal",
        template=MissionTemplateName.REPOSITORY_INVESTIGATION,
        workspace=str(tmp_path),
    )
    claim = service.record_claim(
        SpecialistClaim(
            goal_id=goal.id,
            specialist_role=SpecialistRole.SOFTWARE.value,
            statement="Research completed before restart.",
            confidence=0.9,
            evidence_ids=[completed.mission_id or "mission"],
            evidence=[{"kind": "fixture-codex", "mission_id": completed.mission_id}],
        ),
        assignment_id=completed.id,
    )
    service.complete_assignment(completed.id, claim_ids=[claim.id])
    pending = service.delegate(
        council.id,
        SpecialistRole.SOFTWARE,
        "Resume the implementation subgoal",
        template=MissionTemplateName.FEATURE_IMPLEMENTATION,
        workspace=str(tmp_path),
    )
    service.pause_goal(goal.id)
    store.close()

    restarted_store = ExecutiveStore(tmp_path / "executive.db")
    restarted = ExecutiveService(restarted_store, codex=codex)
    recovery = restarted.resume_goal(goal.id)

    assert completed.id not in recovery.resumed_assignments
    assert pending.id in recovery.resumed_assignments
    assert codex.resumptions == [pending.mission_id]
    assert completed.subgoal_id in recovery.completed_subgoals
    assert (
        restarted_store.get_assignment(completed.id).status
        is AssignmentStatus.COMPLETED
    )
    restarted_store.close()


def test_bounded_context_excludes_transcript_and_limits_claims(tmp_path: Path) -> None:
    store, service, _codex = _service(tmp_path)
    goal = _goal(service)
    council = service.create_council(goal.id, [SpecialistRole.EVIDENCE_CRITIC])
    assignment = service.delegate(
        council.id,
        SpecialistRole.EVIDENCE_CRITIC,
        "Inspect evidence",
    )
    context = service.package_context(assignment.id)

    assert context.transcript_included is False
    assert context.goal_id == goal.id
    assert "read-personal-context" not in context.allowed_capabilities
    assert len(context.relevant_observations) <= 8
    store.close()


def test_missing_information_is_escalated_and_cancellation_is_durable(
    tmp_path: Path,
) -> None:
    store, service, _codex = _service(tmp_path)
    goal = _goal(service)
    council = service.create_council(goal.id, [SpecialistRole.PLAN_CRITIC])
    assignment = service.delegate(
        council.id, SpecialistRole.PLAN_CRITIC, "Find failure modes"
    )
    claim = service.record_claim(
        SpecialistClaim(
            goal_id=goal.id,
            specialist_role=SpecialistRole.PLAN_CRITIC.value,
            statement="A live source freshness value is missing.",
            confidence=0.86,
            evidence_ids=["fixture-observation"],
            evidence=[{"kind": "fixture", "source": "freshness"}],
            missing_information=["current source freshness"],
        ),
        assignment_id=assignment.id,
    )
    assert claim.id in {item.id for item in store.list_claims(goal.id)}
    assert (
        "current source freshness" in store.get_workspace(goal.id).unresolved_questions
    )

    service.cancel_goal(goal.id, reason="Marc canceled the test")
    assert store.get_goal(goal.id).status is GoalStatus.CANCELED
    assert store.get_assignment(assignment.id).status is AssignmentStatus.CANCELED
    assert any(item["kind"] == "goal-canceled" for item in store.events(goal.id))
    store.close()


def test_authority_cannot_expand_from_specialist_consensus(tmp_path: Path) -> None:
    store, service, _codex = _service(tmp_path)
    goal = _goal(service)
    decision = service.request_authority_expansion(
        goal.id,
        proposed_capabilities=["deploy", "network-write"],
        agreeing_claim_ids=["claim-a", "claim-b"],
    )

    assert decision.allowed is False
    assert decision.guardian_owner == "Guardian"
    assert decision.effects_enabled is False
    assert any(
        item["kind"] == "guardian-authority-expansion-blocked"
        for item in store.events(goal.id)
    )
    assert store.get_workspace(goal.id).guardian_feedback[-1]["decision"] == "blocked"
    store.close()


def test_mission_templates_have_explicit_evidence_and_budgets() -> None:
    assert set(MissionTemplates.names()) == set(MissionTemplateName)
    for name in MissionTemplateName:
        template = MissionTemplates.get(name)
        assert template.success_evidence
        assert template.default_budget["turns"] > 0
        assert "deploy" in template.forbidden_effects
    with pytest.raises(ValueError, match="non-negative"):
        MissionTemplates.request(
            MissionTemplateName.REPOSITORY_INVESTIGATION,
            "invalid budget fixture",
            ".",
            budgets={"tokens": -1},
        )


def test_council_budget_blocks_extra_codex_mission(tmp_path: Path) -> None:
    store, service, _codex = _service(tmp_path)
    goal = _goal(service)
    council = service.create_council(
        goal.id,
        [SpecialistRole.SOFTWARE],
        budget={"missions": 1},
    )
    service.delegate(
        council.id,
        SpecialistRole.SOFTWARE,
        "Use the one allowed mission",
        template=MissionTemplateName.REPOSITORY_INVESTIGATION,
    )
    with pytest.raises(ValueError, match="budget exhausted"):
        service.delegate(
            council.id,
            SpecialistRole.SOFTWARE,
            "This mission exceeds the council budget",
            template=MissionTemplateName.REPOSITORY_INVESTIGATION,
        )
    store.close()


def test_deterministic_rag_benchmark_survives_restart_and_is_idempotent(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "rag-benchmark.json"
    result = DeterministicRagBenchmark(tmp_path / "knowledge.db").run(
        output_path=artifact_path
    )

    assert result.hit_rate == 1.0
    assert result.mean_reciprocal_rank == 1.0
    assert result.restart_consistent is True
    assert result.model_independent is True
    assert result.duplicate_ingest_free is True
    assert (
        json.loads(artifact_path.read_text(encoding="utf-8"))["benchmark_id"]
        == "phase8-rag-v1"
    )


def test_durable_rag_index_has_stable_retry_and_model_independent_boundary(
    tmp_path: Path,
) -> None:
    database = tmp_path / "durable-rag.db"
    with DurableRagIndex(database) as index:
        first_id = index.ingest(
            "A durable retrieval fixture survives restart.",
            source="phase8-test",
            source_id="stable-doc",
        )
        second_id = index.ingest(
            "A durable retrieval fixture survives restart.",
            source="phase8-test",
            source_id="stable-doc",
        )
        assert first_id == second_id
        assert index.model_independent is True
        assert index.chunk_count() == 1

    with DurableRagIndex(database) as reopened:
        assert (
            reopened.retrieve("durable retrieval", top_k=1)[0].metadata["source_id"]
            == "stable-doc"
        )


def test_phase8_tangible_rag_objective_and_baseline(tmp_path: Path) -> None:
    result = run_rag_phase8_scenario(
        tmp_path / "executive.db",
        tmp_path / "artifacts" / "rag-benchmark.json",
        workspace=str(tmp_path),
    )
    store = ExecutiveStore(tmp_path / "executive.db")

    assert result.benchmark.restart_consistent is True
    assert result.benchmark.model_independent is True
    assert result.comparison.beats_baseline is True
    assert result.comparison.cost_ratio <= 2.0
    assert result.authority_blocked is True
    assert result.codex_mission_count == 4
    assert store.get_goal(result.goal_id).status is GoalStatus.COMPLETED
    receipt = store.list_decisions(result.goal_id)[0]
    artifact = store.get_artifact(result.artifact_id)
    assert receipt.verified is True
    assert receipt.verified_artifact_ids == [result.artifact_id]
    assert artifact.verified is True
    assert artifact.checksum
    assert "Guardian" in receipt.narrative
    assert any(
        event["kind"] == "specialist-disagreement-reconciled"
        for event in store.events(result.goal_id)
    )
    store.close()


def test_terminal_lifecycle_and_cross_entity_validation_are_fail_closed(
    tmp_path: Path,
) -> None:
    store, service, _codex = _service(tmp_path)
    goal = _goal(service)
    council = service.create_council(goal.id, [SpecialistRole.SOFTWARE])
    assignment = service.delegate(
        council.id,
        SpecialistRole.SOFTWARE,
        "Bounded mission",
        template=MissionTemplateName.REPOSITORY_INVESTIGATION,
    )

    service.cancel_goal(goal.id)
    assert store.get_assignment(assignment.id).status is AssignmentStatus.CANCELED
    with pytest.raises(ValueError, match="terminal"):
        service.complete_assignment(assignment.id)
    with pytest.raises(ValueError, match="terminal"):
        service.resume_goal(goal.id)

    foreign = ExecutiveGoal(objective="foreign", success_conditions=["done"])
    claim = SpecialistClaim(
        goal_id=foreign.id,
        specialist_role=SpecialistRole.SOFTWARE.value,
        statement="cross-goal claim",
        evidence_ids=["fixture"],
    )
    with pytest.raises(ValueError, match="different goals"):
        service.record_claim(claim, assignment_id=assignment.id)
    assert store.list_claims() == []
    store.close()


def test_codex_receives_bounded_context_and_council_token_budget_is_enforced(
    tmp_path: Path,
) -> None:
    store, service, codex = _service(tmp_path)
    goal = _goal(service)
    workspace = service.update_workspace(
        store.get_workspace(goal.id), constraints=["no deployment"]
    )
    council = service.create_council(
        goal.id,
        [SpecialistRole.SOFTWARE],
        budget={"missions": 2, "tokens": 9000, "interventions": 1},
    )
    assignment = service.delegate(
        council.id,
        SpecialistRole.SOFTWARE,
        "Inspect the bounded workspace",
        template=MissionTemplateName.REPOSITORY_INVESTIGATION,
        budgets={"tokens": 9000},
    )

    prompt = codex.missions[0]["objective"]
    assert assignment.mission_template == MissionTemplateName.REPOSITORY_INVESTIGATION
    assert '"goal_id"' in prompt
    assert '"transcript_included":false' in prompt
    assert "no deployment" in prompt
    assert workspace.revision == 1

    with pytest.raises(ValueError, match="token budget exhausted"):
        service.delegate(council.id, SpecialistRole.SOFTWARE, "Over budget")
    service.pause_goal(goal.id)
    service.resume_goal(goal.id)
    service.pause_goal(goal.id)
    with pytest.raises(ValueError, match="intervention budget exhausted"):
        service.resume_goal(goal.id)
    store.close()


def test_failed_codex_delegation_is_durable_and_blocks_its_subgoal(
    tmp_path: Path,
) -> None:
    store = ExecutiveStore(tmp_path / "executive.db")
    service = ExecutiveService(store, codex=_FailingCodexBoundary())
    goal = _goal(service)
    council = service.create_council(goal.id, [SpecialistRole.SOFTWARE])

    with pytest.raises(TimeoutError, match="fixture Codex timeout"):
        service.delegate(
            council.id,
            SpecialistRole.SOFTWARE,
            "A mission that times out",
            template=MissionTemplateName.REPOSITORY_INVESTIGATION,
        )

    assignment = store.list_assignments(goal.id)[0]
    assert assignment.status is AssignmentStatus.FAILED
    assert "Codex delegation failed" in assignment.result_summary
    assert store.get_goal(assignment.subgoal_id or "").status is GoalStatus.BLOCKED
    service.pause_goal(goal.id)
    recovery = service.resume_goal(goal.id)
    assert recovery.pending_assignments == ()
    assert any(item["kind"] == "specialist-failed" for item in store.events(goal.id))
    store.close()


def test_durable_rag_rejects_conflicting_retry_and_invalid_limits(
    tmp_path: Path,
) -> None:
    with DurableRagIndex(tmp_path / "rag.db") as index:
        index.ingest("original content", source="fixture", source_id="doc")
        with pytest.raises(ValueError, match="conflicting content"):
            index.ingest("changed content", source="fixture", source_id="doc")
        with pytest.raises(ValueError, match="content"):
            index.ingest("", source="fixture", source_id="empty")
        assert index.retrieve("original", top_k=0) == []
        with pytest.raises(ValueError, match="top_k"):
            index.retrieve("original", top_k=-1)


def test_single_agent_baseline_is_not_claimed_as_restart_durable(
    tmp_path: Path,
) -> None:
    result = SingleAgentRagBaseline().run(tmp_path / "baseline.db")
    assert result.hit_rate == 1.0
    assert result.mean_reciprocal_rank == 1.0
    assert result.restart_consistent is False
    assert result.evidence_coverage == 0.25


def test_superseding_a_goal_stops_its_durable_work(tmp_path: Path) -> None:
    store, service, _codex = _service(tmp_path)
    goal = _goal(service)
    service.create_commitment(goal.id, "finish the original objective")
    council = service.create_council(goal.id, [SpecialistRole.SOFTWARE])
    assignment = service.delegate(
        council.id,
        SpecialistRole.SOFTWARE,
        "Original work",
        template=MissionTemplateName.REPOSITORY_INVESTIGATION,
    )
    replacement = service.create_goal("Replacement objective", ["replacement done"])

    service.supersede_goal(goal.id, replacement.id)

    assert store.get_goal(goal.id).status is GoalStatus.SUPERSEDED
    assert store.get_goal(assignment.subgoal_id or "").status is GoalStatus.SUPERSEDED
    assert store.get_assignment(assignment.id).status is AssignmentStatus.CANCELED
    assert store.list_commitments(goal.id)[0].status is CommitmentStatus.SUPERSEDED
    assert store.get_council(council.id).status is CouncilStatus.CANCELED
    with pytest.raises(ValueError, match="terminal"):
        service.resume_goal(goal.id)
    store.close()
