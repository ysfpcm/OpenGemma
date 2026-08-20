"""Persistent Cognitive Executive orchestration and truthful synthesis."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

from .missions import CodexMissionBoundary, MissionTemplateName, MissionTemplates
from .models import (
    AssignmentStatus,
    ClaimStatus,
    CommitmentStatus,
    CouncilStatus,
    ExecutiveCommitment,
    ExecutiveDecisionReceipt,
    ExecutiveGoal,
    GlobalWorkspace,
    GoalStatus,
    SpecialistAssignment,
    SpecialistClaim,
    SpecialistCouncil,
    SpecialistRole,
    VerifiedArtifact,
)
from .routing import BoundedContext, SpecialistRouter
from .store import ExecutiveStore


@dataclass(frozen=True)
class AuthorityBoundaryDecision:
    allowed: bool
    reason: str
    guardian_owner: str = "Guardian"
    effects_enabled: bool = False


@dataclass(frozen=True)
class RecoveryResult:
    goal_id: str
    completed_subgoals: tuple[str, ...]
    resumed_assignments: tuple[str, ...]
    pending_assignments: tuple[str, ...]
    replayed_completed_subgoals: tuple[str, ...] = ()


class ExecutiveService:
    """Owns synthesis while delegating bounded evidence-producing work."""

    def __init__(
        self,
        store: ExecutiveStore,
        *,
        codex: Optional[CodexMissionBoundary] = None,
        router: Optional[SpecialistRouter] = None,
    ) -> None:
        self.store = store
        self.codex = codex
        self.router = router or SpecialistRouter()

    # -- durable objective lifecycle ----------------------------------

    def create_goal(
        self,
        objective: str,
        success_conditions: list[str],
        *,
        priority: int = 50,
        owner: str = "Ophanim",
        deadline: Optional[str] = None,
        dependencies: Optional[list[str]] = None,
        parent_goal_id: Optional[str] = None,
        kind: str = "goal",
    ) -> ExecutiveGoal:
        if parent_goal_id:
            parent = self.store.get_goal(parent_goal_id)
            if parent.status not in {GoalStatus.ACTIVE, GoalStatus.PAUSED}:
                raise ValueError("cannot create a child of a terminal goal")
        goal = ExecutiveGoal(
            objective=objective,
            success_conditions=list(success_conditions),
            priority=priority,
            owner=owner,
            deadline=deadline,
            dependencies=list(dependencies or []),
            parent_goal_id=parent_goal_id,
            kind=kind,
            provenance={"component": "phase8-executive"},
        )
        self.store.put_goal(goal)
        self.store.append_event(goal.id, "goal-created", entity_id=goal.id)
        if parent_goal_id:
            self.store.append_event(
                parent_goal_id,
                "subgoal-created",
                entity_id=goal.id,
                details={"objective": objective},
            )
        return goal

    def create_workspace(
        self,
        goal_id: str,
        *,
        current_focus: str,
        constraints: Optional[list[str]] = None,
        unresolved_questions: Optional[list[str]] = None,
    ) -> GlobalWorkspace:
        self.store.get_goal(goal_id)
        workspace = GlobalWorkspace(
            goal_id=goal_id,
            current_focus=current_focus,
            constraints=list(constraints or []),
            unresolved_questions=list(unresolved_questions or []),
            provenance={"component": "phase8-executive"},
        )
        self.store.put_workspace(workspace)
        self.store.append_event(goal_id, "workspace-created", entity_id=workspace.id)
        return workspace

    def update_workspace(
        self, workspace: GlobalWorkspace, **changes: Any
    ) -> GlobalWorkspace:
        allowed = {
            "current_focus",
            "relevant_observations",
            "beliefs",
            "constraints",
            "unresolved_questions",
            "candidate_plans",
            "specialist_claim_ids",
            "guardian_feedback",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported workspace fields: {sorted(unknown)}")
        next_workspace = replace(workspace, revision=workspace.revision + 1, **changes)
        self.store.put_workspace(next_workspace)
        self.store.append_event(
            workspace.goal_id,
            "workspace-updated",
            entity_id=next_workspace.id,
            details={"revision": next_workspace.revision},
        )
        return next_workspace

    def create_commitment(
        self,
        goal_id: str,
        promise: str,
        *,
        due_at: Optional[str] = None,
        success_conditions: Optional[list[str]] = None,
    ) -> ExecutiveCommitment:
        self.store.get_goal(goal_id)
        commitment = ExecutiveCommitment(
            goal_id=goal_id,
            promise=promise,
            due_at=due_at,
            success_conditions=list(success_conditions or []),
            provenance={"component": "phase8-executive", "made_by": "Ophanim"},
        )
        self.store.put_commitment(commitment)
        self.store.append_event(goal_id, "commitment-made", entity_id=commitment.id)
        return commitment

    def create_council(
        self,
        goal_id: str,
        roles: Optional[list[SpecialistRole | str]] = None,
        *,
        budget: Optional[dict[str, int]] = None,
    ) -> SpecialistCouncil:
        self.store.get_goal(goal_id)
        role_values = [
            SpecialistRole(role).value for role in (roles or list(SpecialistRole))
        ]
        for role in role_values:
            if role not in self.router.specs:
                raise ValueError(f"unknown specialist role: {role}")
        final_budget = dict(budget or {})
        if any(
            not isinstance(value, int) or value < 0 for value in final_budget.values()
        ):
            raise ValueError("council budgets must be non-negative integers")
        council = SpecialistCouncil(
            goal_id=goal_id,
            specialist_roles=role_values,
            budget=final_budget,
            status=CouncilStatus.ACTIVE,
            provenance={"component": "phase8-executive"},
        )
        self.store.put_council(council)
        self.store.append_event(
            goal_id,
            "council-formed",
            entity_id=council.id,
            details={"roles": role_values},
        )
        return council

    def delegate(
        self,
        council_id: str,
        role: SpecialistRole | str,
        objective: str,
        *,
        template: MissionTemplateName | str | None = None,
        workspace: str = ".",
        subgoal_id: Optional[str] = None,
        budgets: Optional[dict[str, int]] = None,
    ) -> SpecialistAssignment:
        council = self.store.get_council(council_id)
        if council.status is not CouncilStatus.ACTIVE:
            raise ValueError("cannot delegate from an inactive council")
        role_value = SpecialistRole(role).value
        if role_value not in council.specialist_roles:
            raise ValueError("role is not a member of this council")
        goal = self.store.get_goal(council.goal_id)
        if goal.status is not GoalStatus.ACTIVE:
            raise ValueError("cannot delegate from an inactive goal")
        selected_template = MissionTemplateName(template) if template else None
        spec = self.router.specs[role_value]
        budget = dict(budgets or spec.default_budget)
        if any(not isinstance(value, int) or value < 0 for value in budget.values()):
            raise ValueError("assignment budgets must be non-negative integers")
        if selected_template is not None:
            # Validate the template and its budget before creating a subgoal.
            MissionTemplates.request(
                selected_template,
                objective,
                workspace,
                budgets=budget,
            )
        mission_limit = council.budget.get("missions")
        if mission_limit is not None:
            current_missions = sum(
                1
                for item in self.store.list_assignments(goal.id)
                if item.council_id == council.id and item.mission_template
            )
            if current_missions >= mission_limit and selected_template is not None:
                raise ValueError("council mission budget exhausted")
        token_limit = council.budget.get("tokens")
        if token_limit is not None:
            used_tokens = sum(
                item.budget.get("tokens", 0)
                for item in self.store.list_assignments(goal.id)
                if item.council_id == council.id
            )
            if used_tokens + budget.get("tokens", 0) > token_limit:
                raise ValueError("council token budget exhausted")
        if subgoal_id is None:
            subgoal = self.create_goal(
                objective,
                ["specialist returned an evidence-backed claim"],
                parent_goal_id=goal.id,
                kind="subgoal",
            )
            subgoal_id = subgoal.id
        else:
            subgoal = self.store.get_goal(subgoal_id)
            if subgoal.parent_goal_id != goal.id:
                raise ValueError("subgoal does not belong to council goal")
        assignment = SpecialistAssignment(
            goal_id=goal.id,
            council_id=council.id,
            subgoal_id=subgoal_id,
            specialist_role=role_value,
            objective=objective,
            budget=budget,
            allowed_capabilities=list(spec.allowed_capabilities),
            mission_template=(selected_template.value if selected_template else None),
            status=AssignmentStatus.PENDING,
            provenance={
                "component": "phase8-executive",
                "synthesis_owner": "Executive",
            },
        )
        self.store.put_assignment(assignment)
        updated_council = replace(
            council, assignment_ids=[*council.assignment_ids, assignment.id]
        )
        self.store.put_council(updated_council)
        self.store.append_event(
            goal.id,
            "specialist-delegated",
            entity_id=assignment.id,
            details={
                "role": role_value,
                "mission_id": assignment.mission_id,
                "template": str(selected_template) if selected_template else None,
            },
        )
        if self.codex is not None and selected_template is not None:
            try:
                bounded_context = self.router.package(
                    goal,
                    self.store.get_workspace(goal.id),
                    assignment_id=assignment.id,
                    role=role_value,
                    objective=objective,
                    claims=self.store.list_claims(goal.id),
                    budget=budget,
                )
                request = MissionTemplates.request(
                    selected_template,
                    objective,
                    workspace,
                    budgets=budget,
                    context=bounded_context.to_dict(),
                )
                result = self.codex.start_mission(
                    request.prompt(),
                    request.workspace,
                    mode=MissionTemplates.get(selected_template).mode,
                    budgets=request.budgets,
                )
                mission_id = result.get("id") or result.get("mission_id")
                if not mission_id:
                    raise RuntimeError(
                        "Codex mission did not return a durable mission id"
                    )
                assignment = replace(
                    assignment,
                    mission_id=str(mission_id),
                    status=AssignmentStatus.RUNNING,
                    result_summary=str(result.get("progress", "Codex mission started")),
                )
                self.store.put_assignment(assignment)
            except Exception as exc:
                self._fail_assignment(assignment, f"Codex delegation failed: {exc}")
                raise
        return assignment

    def package_context(self, assignment_id: str) -> BoundedContext:
        assignment = self.store.get_assignment(assignment_id)
        goal = self.store.get_goal(assignment.goal_id)
        workspace = self.store.get_workspace(assignment.goal_id)
        return self.router.package(
            goal,
            workspace,
            assignment_id=assignment.id,
            role=assignment.specialist_role,
            objective=assignment.objective,
            claims=self.store.list_claims(assignment.goal_id),
            budget=assignment.budget,
        )

    # -- claims, artifacts, and reconciliation ------------------------

    def record_claim(
        self, claim: SpecialistClaim, *, assignment_id: Optional[str] = None
    ) -> SpecialistClaim:
        assignment = None
        if assignment_id:
            assignment = self.store.get_assignment(assignment_id)
            if assignment.goal_id != claim.goal_id:
                raise ValueError("claim and assignment belong to different goals")
            if assignment.specialist_role != claim.specialist_role:
                raise ValueError("claim role does not match assignment specialist")
        workspace = self.store.get_workspace(claim.goal_id)
        self.store.put_claim(claim)
        if claim.id not in workspace.specialist_claim_ids:
            workspace = self.update_workspace(
                workspace,
                specialist_claim_ids=[*workspace.specialist_claim_ids, claim.id],
            )
        if assignment is not None:
            if claim.id not in assignment.claim_ids:
                self.store.put_assignment(
                    replace(assignment, claim_ids=[*assignment.claim_ids, claim.id])
                )
        self.store.append_event(
            claim.goal_id,
            "specialist-claim-recorded",
            entity_id=claim.id,
            details={"role": claim.specialist_role, "confidence": claim.confidence},
        )
        if claim.missing_information:
            self.escalate_missing_information(
                claim.goal_id, claim.missing_information, claim_id=claim.id
            )
        return claim

    def record_artifact(
        self, artifact: VerifiedArtifact, *, assignment_id: Optional[str] = None
    ) -> VerifiedArtifact:
        assignment = None
        if assignment_id:
            assignment = self.store.get_assignment(assignment_id)
            if assignment.goal_id != artifact.goal_id:
                raise ValueError("artifact and assignment belong to different goals")
        if artifact.verified:
            path = Path(artifact.path)
            if not path.exists():
                raise FileNotFoundError(artifact.path)
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != artifact.checksum:
                raise ValueError("artifact checksum does not match bytes on disk")
        self.store.put_artifact(artifact)
        if assignment is not None:
            if artifact.id not in assignment.artifact_ids:
                self.store.put_assignment(
                    replace(
                        assignment, artifact_ids=[*assignment.artifact_ids, artifact.id]
                    )
                )
        self.store.append_event(
            artifact.goal_id,
            "artifact-recorded",
            entity_id=artifact.id,
            details={"verified": artifact.verified, "kind": artifact.artifact_kind},
        )
        return artifact

    def complete_assignment(
        self,
        assignment_id: str,
        *,
        claim_ids: Optional[list[str]] = None,
        artifact_ids: Optional[list[str]] = None,
        summary: str = "",
    ) -> SpecialistAssignment:
        assignment = self.store.get_assignment(assignment_id)
        if assignment.status in {
            AssignmentStatus.COMPLETED,
            AssignmentStatus.CANCELED,
            AssignmentStatus.FAILED,
            AssignmentStatus.BLOCKED,
        }:
            raise ValueError("assignment is already terminal")
        goal = self.store.get_goal(assignment.goal_id)
        if goal.status in {
            GoalStatus.CANCELED,
            GoalStatus.SUPERSEDED,
            GoalStatus.BLOCKED,
        }:
            raise ValueError("cannot complete work for an inactive goal")
        merged_claim_ids = list(
            dict.fromkeys([*assignment.claim_ids, *(claim_ids or [])])
        )
        merged_artifact_ids = list(
            dict.fromkeys([*assignment.artifact_ids, *(artifact_ids or [])])
        )
        if not merged_claim_ids and not merged_artifact_ids:
            raise ValueError("completed assignment requires persisted evidence")
        for claim_id in claim_ids or []:
            claim = self.store.get_claim(claim_id)
            if claim.goal_id != assignment.goal_id:
                raise ValueError("claim does not belong to assignment goal")
        for artifact_id in artifact_ids or []:
            artifact = self.store.get_artifact(artifact_id)
            if artifact.goal_id != assignment.goal_id:
                raise ValueError("artifact does not belong to assignment goal")
        completed = replace(
            assignment,
            status=AssignmentStatus.COMPLETED,
            claim_ids=merged_claim_ids,
            artifact_ids=merged_artifact_ids,
            result_summary=summary
            or assignment.result_summary
            or "Completed with persisted evidence",
        )
        self.store.put_assignment(completed)
        if assignment.subgoal_id:
            subgoal = self.store.get_goal(assignment.subgoal_id)
            self.store.put_goal(
                replace(
                    subgoal,
                    status=GoalStatus.COMPLETED,
                    completed_at=subgoal.completed_at or _now(),
                    status_reason="assignment completed",
                )
            )
        self.store.append_event(
            assignment.goal_id, "specialist-completed", entity_id=assignment.id
        )
        return completed

    def reconcile_claims(
        self,
        goal_id: str,
        claim_ids: list[str],
        *,
        preferred_claim_id: Optional[str] = None,
        reason: str = "Executive selected the claim with the strongest evidence and confidence.",
    ) -> dict[str, Any]:
        claims = [self.store.get_claim(item) for item in claim_ids]
        if not claims:
            raise ValueError("at least one claim is required")
        if any(claim.goal_id != goal_id for claim in claims):
            raise ValueError("claims must belong to goal")
        if preferred_claim_id:
            selected = next(
                (claim for claim in claims if claim.id == preferred_claim_id), None
            )
            if selected is None:
                raise ValueError("preferred claim is not in disagreement set")
        else:
            selected = max(
                claims,
                key=lambda claim: (
                    claim.confidence or 0.0,
                    len(claim.evidence_ids) + len(claim.evidence),
                    len(claim.artifact_ids),
                ),
            )
        disagreement_id = f"disagreement_{uuid.uuid4().hex}"
        for claim in claims:
            next_status = (
                ClaimStatus.ACCEPTED
                if claim.id == selected.id
                else ClaimStatus.REJECTED
            )
            self.store.put_claim(replace(claim, status=next_status))
        self.store.append_event(
            goal_id,
            "specialist-disagreement-reconciled",
            entity_id=disagreement_id,
            details={
                "claim_ids": claim_ids,
                "selected_claim_id": selected.id,
                "reason": reason,
            },
        )
        return {
            "disagreement_id": disagreement_id,
            "claim_ids": claim_ids,
            "selected_claim_id": selected.id,
            "reason": reason,
        }

    def escalate_missing_information(
        self,
        goal_id: str,
        questions: list[str],
        *,
        claim_id: Optional[str] = None,
    ) -> None:
        workspace = self.store.get_workspace(goal_id)
        merged = list(dict.fromkeys([*workspace.unresolved_questions, *questions]))
        self.update_workspace(workspace, unresolved_questions=merged)
        self.store.append_event(
            goal_id,
            "missing-information-escalated",
            entity_id=claim_id,
            details={"questions": questions},
        )

    # -- lifecycle and restart recovery -------------------------------

    def _fail_assignment(
        self, assignment: SpecialistAssignment, reason: str
    ) -> SpecialistAssignment:
        failed = replace(
            assignment,
            status=AssignmentStatus.FAILED,
            result_summary=reason,
        )
        self.store.put_assignment(failed)
        if assignment.subgoal_id:
            subgoal = self.store.get_goal(assignment.subgoal_id)
            if subgoal.status not in {
                GoalStatus.COMPLETED,
                GoalStatus.CANCELED,
                GoalStatus.SUPERSEDED,
            }:
                self.store.put_goal(
                    replace(
                        subgoal,
                        status=GoalStatus.BLOCKED,
                        status_reason=reason,
                    )
                )
        self.store.append_event(
            assignment.goal_id,
            "specialist-failed",
            entity_id=assignment.id,
            details={"reason": reason},
        )
        return failed

    def _check_intervention_budget(self, goal_id: str) -> None:
        intervention_count = sum(
            1
            for event in self.store.events(goal_id)
            if event["kind"] in {"goal-resumed", "executive-recovered"}
        )
        for council in self.store.list_councils(goal_id):
            limit = council.budget.get("interventions")
            if limit is not None and intervention_count >= limit:
                raise ValueError("council intervention budget exhausted")

    def pause_goal(self, goal_id: str, *, reason: str = "paused by Marc") -> None:
        goal = self.store.get_goal(goal_id)
        if goal.status is not GoalStatus.ACTIVE:
            raise ValueError("only an active goal can be paused")
        self.store.put_goal(
            replace(goal, status=GoalStatus.PAUSED, status_reason=reason)
        )
        for assignment in self.store.list_assignments(goal_id):
            if assignment.status in {
                AssignmentStatus.PENDING,
                AssignmentStatus.RUNNING,
            }:
                self.store.put_assignment(
                    replace(assignment, status=AssignmentStatus.PAUSED)
                )
        for subgoal in self.store.list_goals(parent_goal_id=goal_id):
            if subgoal.status is GoalStatus.ACTIVE:
                self.store.put_goal(
                    replace(subgoal, status=GoalStatus.PAUSED, status_reason=reason)
                )
        for commitment in self.store.list_commitments(goal_id):
            if commitment.status is CommitmentStatus.ACTIVE:
                self.store.put_commitment(
                    replace(commitment, status=CommitmentStatus.PAUSED)
                )
        for council in self.store.list_councils(goal_id):
            if council.status is CouncilStatus.ACTIVE:
                self.store.put_council(replace(council, status=CouncilStatus.PAUSED))
        self.store.append_event(goal_id, "goal-paused", details={"reason": reason})

    def resume_goal(self, goal_id: str) -> RecoveryResult:
        goal = self.store.get_goal(goal_id)
        if goal.status in {
            GoalStatus.CANCELED,
            GoalStatus.SUPERSEDED,
            GoalStatus.COMPLETED,
            GoalStatus.BLOCKED,
        }:
            raise ValueError("cannot resume a terminal goal")
        self._check_intervention_budget(goal_id)
        if goal.status is GoalStatus.PAUSED:
            self.store.put_goal(
                replace(goal, status=GoalStatus.ACTIVE, status_reason="resumed")
            )
            for subgoal in self.store.list_goals(parent_goal_id=goal_id):
                if subgoal.status is GoalStatus.PAUSED:
                    self.store.put_goal(
                        replace(
                            subgoal,
                            status=GoalStatus.ACTIVE,
                            status_reason="parent resumed",
                        )
                    )
            for commitment in self.store.list_commitments(goal_id):
                if commitment.status is CommitmentStatus.PAUSED:
                    self.store.put_commitment(
                        replace(commitment, status=CommitmentStatus.ACTIVE)
                    )
            for council in self.store.list_councils(goal_id):
                if council.status is CouncilStatus.PAUSED:
                    self.store.put_council(
                        replace(council, status=CouncilStatus.ACTIVE)
                    )
        recovery = self.store.recover(goal_id)
        resumed: list[str] = []
        for assignment in recovery["pending_assignments"]:
            if assignment.mission_id and self.codex is not None:
                try:
                    result = self.codex.resume_observation(assignment.mission_id)
                    self.store.put_assignment(
                        replace(
                            assignment,
                            status=AssignmentStatus.RUNNING,
                            result_summary=str(
                                result.get("progress", "Codex observation resumed")
                            ),
                        )
                    )
                    resumed.append(assignment.id)
                except Exception as exc:
                    self._fail_assignment(assignment, f"Codex resume failed: {exc}")
        self.store.append_event(
            goal_id, "goal-resumed", details={"resumed_assignments": resumed}
        )
        remaining = tuple(
            item.id
            for item in recovery["pending_assignments"]
            if self.store.get_assignment(item.id).status
            not in {
                AssignmentStatus.COMPLETED,
                AssignmentStatus.CANCELED,
                AssignmentStatus.FAILED,
                AssignmentStatus.BLOCKED,
            }
        )
        return RecoveryResult(
            goal_id=goal_id,
            completed_subgoals=tuple(recovery["completed_subgoals"]),
            resumed_assignments=tuple(resumed),
            pending_assignments=remaining,
        )

    def cancel_goal(self, goal_id: str, *, reason: str = "canceled by Marc") -> None:
        goal = self.store.get_goal(goal_id)
        if goal.status in {
            GoalStatus.COMPLETED,
            GoalStatus.CANCELED,
            GoalStatus.SUPERSEDED,
            GoalStatus.BLOCKED,
        }:
            return
        self.store.put_goal(
            replace(goal, status=GoalStatus.CANCELED, status_reason=reason)
        )
        for assignment in self.store.list_assignments(goal_id):
            if assignment.status not in {
                AssignmentStatus.COMPLETED,
                AssignmentStatus.CANCELED,
            }:
                self.store.put_assignment(
                    replace(assignment, status=AssignmentStatus.CANCELED)
                )
        for subgoal in self.store.list_goals(parent_goal_id=goal_id):
            if subgoal.status not in {GoalStatus.COMPLETED, GoalStatus.CANCELED}:
                self.store.put_goal(
                    replace(subgoal, status=GoalStatus.CANCELED, status_reason=reason)
                )
        for commitment in self.store.list_commitments(goal_id):
            if commitment.status not in {
                CommitmentStatus.COMPLETED,
                CommitmentStatus.CANCELED,
            }:
                self.store.put_commitment(
                    replace(commitment, status=CommitmentStatus.CANCELED)
                )
        for council in self.store.list_councils(goal_id):
            if council.status not in {
                CouncilStatus.COMPLETED,
                CouncilStatus.CANCELED,
            }:
                self.store.put_council(replace(council, status=CouncilStatus.CANCELED))
        self.store.append_event(goal_id, "goal-canceled", details={"reason": reason})

    def supersede_goal(self, goal_id: str, replacement_goal_id: str) -> None:
        if goal_id == replacement_goal_id:
            raise ValueError("replacement goal must be different from superseded goal")
        goal = self.store.get_goal(goal_id)
        replacement = self.store.get_goal(replacement_goal_id)
        if replacement.parent_goal_id:
            raise ValueError("replacement goal must be a root goal")
        if goal.status in {
            GoalStatus.COMPLETED,
            GoalStatus.CANCELED,
            GoalStatus.SUPERSEDED,
            GoalStatus.BLOCKED,
        }:
            raise ValueError("cannot supersede a terminal goal")
        self.store.put_goal(
            replace(
                goal, status=GoalStatus.SUPERSEDED, status_reason=replacement_goal_id
            )
        )
        for assignment in self.store.list_assignments(goal_id):
            if assignment.status not in {
                AssignmentStatus.COMPLETED,
                AssignmentStatus.CANCELED,
                AssignmentStatus.FAILED,
                AssignmentStatus.BLOCKED,
            }:
                self.store.put_assignment(
                    replace(assignment, status=AssignmentStatus.CANCELED)
                )
        for subgoal in self.store.list_goals(parent_goal_id=goal_id):
            if subgoal.status not in {
                GoalStatus.COMPLETED,
                GoalStatus.CANCELED,
                GoalStatus.SUPERSEDED,
            }:
                self.store.put_goal(
                    replace(
                        subgoal,
                        status=GoalStatus.SUPERSEDED,
                        status_reason=replacement_goal_id,
                    )
                )
        for commitment in self.store.list_commitments(goal_id):
            if commitment.status in {CommitmentStatus.ACTIVE, CommitmentStatus.PAUSED}:
                self.store.put_commitment(
                    replace(commitment, status=CommitmentStatus.SUPERSEDED)
                )
        for council in self.store.list_councils(goal_id):
            if council.status not in {
                CouncilStatus.COMPLETED,
                CouncilStatus.CANCELED,
            }:
                self.store.put_council(replace(council, status=CouncilStatus.CANCELED))
        self.store.append_event(
            goal_id,
            "goal-superseded",
            details={"replacement_goal_id": replacement_goal_id},
        )

    def complete_goal(
        self, goal_id: str, *, reason: str = "all success conditions verified"
    ) -> ExecutiveGoal:
        goal = self.store.get_goal(goal_id)
        if goal.status in {
            GoalStatus.COMPLETED,
            GoalStatus.CANCELED,
            GoalStatus.SUPERSEDED,
            GoalStatus.BLOCKED,
        }:
            raise ValueError("cannot complete a terminal goal")
        subgoals = self.store.list_goals(parent_goal_id=goal_id)
        if any(subgoal.status is not GoalStatus.COMPLETED for subgoal in subgoals):
            raise ValueError("cannot complete goal while a subgoal is incomplete")
        completed = replace(
            goal, status=GoalStatus.COMPLETED, completed_at=_now(), status_reason=reason
        )
        self.store.put_goal(completed)
        for commitment in self.store.list_commitments(goal_id):
            if commitment.status in {CommitmentStatus.ACTIVE, CommitmentStatus.PAUSED}:
                self.store.put_commitment(
                    replace(commitment, status=CommitmentStatus.COMPLETED)
                )
        for council in self.store.list_councils(goal_id):
            if council.status in {CouncilStatus.ACTIVE, CouncilStatus.PAUSED}:
                self.store.put_council(replace(council, status=CouncilStatus.COMPLETED))
        self.store.append_event(goal_id, "goal-completed", details={"reason": reason})
        return completed

    def recover_after_restart(self, goal_id: str) -> RecoveryResult:
        """Recover persisted state; completed assignments are never rerun."""
        goal = self.store.get_goal(goal_id)
        if goal.status in {
            GoalStatus.CANCELED,
            GoalStatus.SUPERSEDED,
            GoalStatus.COMPLETED,
            GoalStatus.BLOCKED,
        }:
            raise ValueError("cannot recover a terminal goal")
        self._check_intervention_budget(goal_id)
        recovery = self.store.recover(goal_id)
        resumed: list[str] = []
        for assignment in recovery["pending_assignments"]:
            if (
                assignment.mission_id
                and self.codex is not None
                and assignment.status is AssignmentStatus.RUNNING
            ):
                try:
                    result = self.codex.resume_observation(assignment.mission_id)
                    self.store.put_assignment(
                        replace(
                            assignment,
                            result_summary=str(
                                result.get("progress", "Codex observation recovered")
                            ),
                        )
                    )
                    resumed.append(assignment.id)
                except Exception as exc:
                    self._fail_assignment(assignment, f"Codex recovery failed: {exc}")
        self.store.append_event(
            goal_id, "executive-recovered", details={"resumed_assignments": resumed}
        )
        remaining = tuple(
            item.id
            for item in recovery["pending_assignments"]
            if self.store.get_assignment(item.id).status
            not in {
                AssignmentStatus.COMPLETED,
                AssignmentStatus.CANCELED,
                AssignmentStatus.FAILED,
                AssignmentStatus.BLOCKED,
            }
        )
        return RecoveryResult(
            goal_id=goal_id,
            completed_subgoals=tuple(recovery["completed_subgoals"]),
            resumed_assignments=tuple(resumed),
            pending_assignments=remaining,
        )

    # -- authority and synthesis --------------------------------------

    def request_authority_expansion(
        self,
        goal_id: str,
        *,
        proposed_capabilities: list[str],
        agreeing_claim_ids: list[str],
    ) -> AuthorityBoundaryDecision:
        """Refuse council consensus as an authority source.

        The real Guardian API remains the only route to a consequential
        proposal.  This method is useful in tests and in the executive audit
        because it makes the refusal explicit even when every specialist agrees.
        """
        if not proposed_capabilities:
            raise ValueError("proposed capabilities must not be empty")
        self.store.append_event(
            goal_id,
            "guardian-authority-expansion-blocked",
            details={
                "capabilities": proposed_capabilities,
                "agreeing_claim_ids": agreeing_claim_ids,
            },
        )
        workspace = self.store.get_workspace(goal_id)
        self.update_workspace(
            workspace,
            guardian_feedback=[
                *workspace.guardian_feedback,
                {
                    "decision": "blocked",
                    "reason": "Specialist consensus cannot expand Guardian authority.",
                    "capabilities": proposed_capabilities,
                },
            ],
        )
        return AuthorityBoundaryDecision(
            False,
            "Guardian remains the sole authority boundary; specialist consensus cannot widen permissions.",
        )

    def synthesize(
        self,
        goal_id: str,
        *,
        decision: str,
        rationale: str,
        claim_ids: list[str],
        disagreement_ids: Optional[list[str]] = None,
        verified_artifact_ids: Optional[list[str]] = None,
        baseline_comparison: Optional[dict[str, Any]] = None,
        outcome: str = "verified",
    ) -> ExecutiveDecisionReceipt:
        claims = [self.store.get_claim(item) for item in claim_ids]
        if any(claim.goal_id != goal_id for claim in claims):
            raise ValueError("claim does not belong to goal")
        artifacts = [
            self.store.get_artifact(item) for item in (verified_artifact_ids or [])
        ]
        if any(artifact.goal_id != goal_id for artifact in artifacts):
            raise ValueError("artifact does not belong to goal")
        evidence_ids = list(
            dict.fromkeys([item for claim in claims for item in claim.evidence_ids])
        )
        evidence_ids.extend(item.id for item in artifacts)
        if not evidence_ids:
            raise ValueError("synthesis requires evidence")
        verified = bool(artifacts) and all(item.verified for item in artifacts)
        narrative = (
            f"Ophanim advanced '{self.store.get_goal(goal_id).objective}'. "
            f"Decision: {decision}. {rationale} "
            f"Evidence is retained in {len(evidence_ids)} durable records; Guardian remains the authority boundary."
        )
        receipt = ExecutiveDecisionReceipt(
            goal_id=goal_id,
            decision=decision,
            rationale=rationale,
            evidence_ids=evidence_ids,
            claim_ids=claim_ids,
            disagreement_ids=list(disagreement_ids or []),
            verified_artifact_ids=list(verified_artifact_ids or []),
            baseline_comparison=dict(baseline_comparison or {}),
            verified=verified,
            outcome=outcome,
            narrative=narrative,
            provenance={
                "component": "phase8-executive",
                "synthesis_owner": "Executive",
            },
        )
        self.store.put_decision(receipt)
        self.store.append_event(
            goal_id, "executive-decision-receipted", entity_id=receipt.id
        )
        return receipt


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


__all__ = ["AuthorityBoundaryDecision", "ExecutiveService", "RecoveryResult"]
