"""Controlled learning, replay, shadow, promotion, and rollback service."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .fixtures import (
    CodexIsolationResult,
    copy_bounded_codex_workspace,
)
from .lifecycle import CandidateLifecycleError
from .models import (
    SAFE_ACTIVATION_SCOPE,
    ArtifactStage,
    ArtifactVersion,
    CandidateKind,
    CandidateStatus,
    Consent,
    Correction,
    ImmediateAdaptation,
    LearningCandidate,
    MemoryEvidence,
    Procedure,
    PromotionDecision,
    RegressionResult,
    ReplayCase,
    ReplayRun,
    ReplayStatus,
    RollbackRecord,
    RuntimeState,
    ShadowRun,
    ShadowStatus,
    stable_id,
)
from .store import LaboratoryStore

_REQUIRED_BASELINE_PHASES = {
    "daytime",
    "sunset",
    "overnight",
    "guest_present",
    "contradictory_command",
}
_SENSITIVITY_RANK = {
    "public": 0,
    "personal": 1,
    "personal-home": 2,
    "sensitive": 2,
    "restricted": 3,
    "highly-sensitive": 3,
}


class LearningLaboratoryError(ValueError):
    """Base error for a request that cannot cross the Phase 10 boundary."""


class ConsentRequiredError(LearningLaboratoryError):
    pass


class ApprovalRequiredError(LearningLaboratoryError):
    pass


class PrivacyViolationError(LearningLaboratoryError):
    pass


class AuthorityBoundaryError(LearningLaboratoryError):
    pass


class ArtifactIntegrityError(LearningLaboratoryError):
    pass


class LearningLaboratory:
    """The only Phase 10 promotion boundary.

    This service stores evidence and local artifact pointers.  It deliberately
    does not accept a Guardian instance, connector, notification client,
    deployment client, or production model handle.
    """

    def __init__(
        self,
        store: LaboratoryStore,
        *,
        signing_key: str = "phase10-local-review-key",
        signer: str = "phase10-review-fixture",
    ) -> None:
        self.store = store
        self.signing_key = signing_key.encode("utf-8")
        self.signer = signer
        if not self.store.list_records(kind="runtime_state"):
            self.store.put(RuntimeState(id="phase10-runtime-state"))

    # ------------------------------------------------------------------
    # Immediate memory adaptation
    # ------------------------------------------------------------------

    def record_correction(
        self, correction: Correction
    ) -> tuple[Correction, tuple[MemoryEvidence, ...], ImmediateAdaptation]:
        """Persist Marc's correction immediately without production mutation."""

        try:
            existing = self.store.get(correction.id)
        except KeyError:
            existing = None
        if existing is not None:
            existing_payload = existing.to_dict()
            incoming_payload = correction.to_dict()
            for payload in (existing_payload, incoming_payload):
                payload.pop("created_at", None)
                payload.pop("valid_from", None)
            if (
                not isinstance(existing, Correction)
                or existing_payload != incoming_payload
            ):
                raise ValueError(
                    f"correction id {correction.id!r} already contains different evidence"
                )
            correction = existing
        else:
            self.store.put(correction)
        evidence: list[MemoryEvidence] = []
        for kind in correction.evidence_kinds:
            evidence_id = stable_id("phase10-memory", correction.id, kind)
            existing_evidence = self._existing(evidence_id, MemoryEvidence)
            item = existing_evidence or MemoryEvidence(
                id=evidence_id,
                correction_id=correction.id,
                evidence_kind=kind,
                statement=correction.statement,
                context=dict(correction.context),
                provenance={
                    "component": "phase10-immediate-memory",
                    "source": correction.source,
                    "correction_id": correction.id,
                },
                causal_parents=[correction.id],
                sensitivity_labels=[correction.sensitivity],
                confidence=correction.confidence,
            )
            if existing_evidence is None:
                self.store.put(item)
            evidence.append(item)
        relationship = next(
            (item for item in evidence if item.evidence_kind == "relationship"),
            evidence[0],
        )
        procedural = next(
            (item for item in evidence if item.evidence_kind == "procedural"),
            evidence[-1],
        )
        adaptation_id = stable_id("phase10-adaptation", correction.id)
        adaptation = self._existing(adaptation_id, ImmediateAdaptation)
        if adaptation is None:
            adaptation = ImmediateAdaptation(
                id=adaptation_id,
                correction_id=correction.id,
                relationship_evidence_id=relationship.id,
                procedural_evidence_id=procedural.id,
                belief_revision=f"Belief revised from Marc's correction: {correction.statement}",
                current_plan_revision=(
                    "Current plans must apply this correction only within "
                    f"the stated context: {json.dumps(correction.context, sort_keys=True)}"
                ),
                provenance={
                    "component": "phase10-immediate-memory",
                    "no_production_mutation": True,
                },
                causal_parents=[correction.id, relationship.id, procedural.id],
                sensitivity_labels=[correction.sensitivity],
                confidence=correction.confidence,
            )
            self.store.put(adaptation)
        self.store.audit(
            correction.id,
            "idempotency:correction-recorded",
            {
                "relationship_evidence_id": relationship.id,
                "procedural_evidence_id": procedural.id,
                "production_weights_changed": False,
                "production_prompts_changed": False,
                "production_policies_changed": False,
                "production_skills_changed": False,
            },
        )
        return correction, tuple(evidence), adaptation

    def procedure_from_correction(self, correction_id: str) -> Procedure:
        correction = self._get(correction_id, Correction)
        existing = [
            item
            for item in self.store.list_records(kind="procedure")
            if isinstance(item, Procedure)
            and correction_id in item.source_correction_ids
        ]
        if existing:
            return existing[0]
        procedure = Procedure(
            id=stable_id("phase10-procedure", correction_id),
            name="sunset-entry-light-exception",
            trigger={
                "event": "marc_leaves",
                "after_sunset": True,
                "routine": "away",
            },
            steps=[
                {"action": "away.general", "entry_light": "off"},
                {"action": "preserve", "target": "light.entry", "state": "on"},
            ],
            exceptions=[
                {"when": "guest_present", "behavior": "do_not_apply"},
                {"when": "contradictory_command", "behavior": "explicit_command_wins"},
            ],
            scope={
                "subject": "Marc",
                "target": "light.entry",
                "routine": "away",
                "time": "after_sunset",
            },
            source_correction_ids=[correction_id],
            provenance={
                "component": "phase10-procedure-extractor",
                "source_correction_id": correction_id,
            },
            causal_parents=[correction_id],
            sensitivity_labels=list(correction.sensitivity_labels)
            or [correction.sensitivity],
            confidence=correction.confidence,
        )
        self.store.put(procedure)
        return procedure

    # ------------------------------------------------------------------
    # Candidate generation and consent
    # ------------------------------------------------------------------

    def generate_policy_candidate(self, correction_id: str) -> LearningCandidate:
        correction = self._get(correction_id, Correction)
        procedure = self.procedure_from_correction(correction_id)
        existing = [
            item
            for item in self.store.list_records(kind="learning_candidate")
            if isinstance(item, LearningCandidate)
            and correction_id in item.source_correction_ids
        ]
        if existing:
            return existing[0]
        candidate = LearningCandidate(
            id=stable_id("phase10-candidate", correction_id, "policy"),
            candidate_kind=CandidateKind.POLICY_PACK.value,
            procedure_id=procedure.id,
            source_correction_ids=[correction_id],
            source_version=str(
                correction.context.get("source_version", "home-fixture-v1")
            ),
            named_outcome="sunset departure entry-light exception accuracy",
            proposal={
                "policy_type": "departure_exception",
                "target": "light.entry",
                "trigger": dict(procedure.trigger),
                "steps": list(procedure.steps),
                "exceptions": list(procedure.exceptions),
                "production_activation": "explicit-only",
            },
            baseline_behavior={
                "daytime": "off",
                "sunset": "off",
                "overnight": "off",
                "guest_present": "off",
                "contradictory_command": "off",
            },
            expected_behavior={
                "daytime": "off",
                "sunset": "on",
                "overnight": "on",
                "guest_present": "off",
                "contradictory_command": "off",
            },
            required_marc_approval=True,
            provenance={
                "component": "phase10-candidate-generator",
                "source_correction_id": correction_id,
                "source_procedure_id": procedure.id,
                "cause": correction.statement,
                "production_mutation": False,
            },
            causal_parents=[correction_id, procedure.id],
            sensitivity_labels=list(correction.sensitivity_labels)
            or [correction.sensitivity],
            confidence=correction.confidence,
        )
        self.store.put(candidate)
        self.store.audit(
            candidate.id,
            "candidate:generated",
            {"source_correction_id": correction_id, "authority_created": False},
        )
        return candidate

    def generate_candidate(
        self,
        *,
        candidate_kind: str,
        named_outcome: str,
        proposal: dict[str, Any],
        baseline_behavior: dict[str, Any],
        expected_behavior: dict[str, Any],
        source_ids: Iterable[str] = (),
        source_version: str = "v1",
        consent_id: str | None = None,
        required_marc_approval: bool = True,
    ) -> LearningCandidate:
        source_id_list = list(source_ids)
        if proposal.get("authority_scope") or proposal.get(
            "creates_guardian_authority"
        ):
            raise AuthorityBoundaryError(
                "candidate proposals cannot create or expand Guardian authority"
            )
        candidate = LearningCandidate(
            id=stable_id(
                "phase10-generic-candidate", candidate_kind, named_outcome, proposal
            ),
            candidate_kind=candidate_kind,
            source_trace_ids=source_id_list,
            source_version=source_version,
            named_outcome=named_outcome,
            proposal=dict(proposal),
            baseline_behavior=dict(baseline_behavior),
            expected_behavior=dict(expected_behavior),
            required_marc_approval=required_marc_approval,
            consent_id=consent_id,
            provenance={
                "component": "phase10-candidate-generator",
                "source_ids": source_id_list,
                "production_mutation": False,
            },
            sensitivity_labels=["personal"],
        )
        self.store.put(candidate)
        return candidate

    def save_consent(self, consent: Consent) -> Consent:
        self.store.put(consent)
        self.store.audit(
            consent.id,
            "consent:recorded",
            {
                "explicit": consent.explicit,
                "privacy_reviewed": consent.privacy_reviewed,
            },
        )
        return consent

    def _check_privacy_and_consent(self, candidate: LearningCandidate) -> None:
        if candidate.authority_scope:
            raise AuthorityBoundaryError("candidate authority scope is not allowed")
        if candidate.proposal.get("writes_primary_workspace"):
            raise PrivacyViolationError(
                "primary workspace writes are forbidden in experiments"
            )
        if candidate.candidate_kind != CandidateKind.MODEL_ADAPTER.value:
            return
        if candidate.proposal.get("offline_only") is not True:
            raise PrivacyViolationError(
                "model experiments must declare offline_only=true"
            )
        if candidate.proposal.get("isolated") is not True:
            raise PrivacyViolationError("model experiments must declare isolated=true")
        if not candidate.consent_id:
            raise ConsentRequiredError("model adaptation requires explicit consent")
        consent = self._get(candidate.consent_id, Consent)
        if (
            consent.subject != "Marc"
            or not consent.explicit
            or consent.revoked
            or not consent.privacy_reviewed
        ):
            raise ConsentRequiredError(
                "consent must be Marc's, explicit, current, and privacy-reviewed"
            )
        if "offline_experiment" not in consent.allowed_uses:
            raise ConsentRequiredError(
                "consent does not allow offline model experiments"
            )
        if not candidate.source_trace_ids:
            raise PrivacyViolationError(
                "model experiments must name consented source traces"
            )
        if not consent.source_ids:
            raise ConsentRequiredError("consent must name source trace IDs")
        if consent.expires_at:
            try:
                expiry = datetime.fromisoformat(consent.expires_at)
                if expiry.tzinfo is None:
                    raise ValueError("consent expiry must include a timezone")
                expired = expiry <= datetime.now(timezone.utc)
            except (TypeError, ValueError) as exc:
                raise ConsentRequiredError(
                    "consent expiry is not valid ISO-8601"
                ) from exc
            if expired:
                raise ConsentRequiredError("consent has expired")
        if not set(candidate.source_trace_ids).issubset(set(consent.source_ids)):
            raise PrivacyViolationError("candidate source traces exceed consent scope")
        candidate_rank = _SENSITIVITY_RANK.get(candidate.privacy_class, 99)
        ceiling_rank = _SENSITIVITY_RANK.get(consent.sensitivity_ceiling, -1)
        if candidate_rank > ceiling_rank:
            raise PrivacyViolationError(
                "candidate privacy class exceeds the consent sensitivity ceiling"
            )

    def _check_case_privacy(
        self, candidate: LearningCandidate, case: ReplayCase
    ) -> None:
        if candidate.candidate_kind != CandidateKind.MODEL_ADAPTER.value:
            return
        try:
            consent = self._get(candidate.consent_id or "", Consent)
        except KeyError as exc:
            raise ConsentRequiredError("model adaptation consent is missing") from exc
        if not set(case.sensitive_source_ids).issubset(set(consent.source_ids)):
            raise PrivacyViolationError(
                f"replay case {case.id} contains out-of-scope sensitive traces"
            )

    def sandbox_candidate(self, candidate_id: str) -> LearningCandidate:
        candidate = self._get(candidate_id, LearningCandidate)
        self._check_privacy_and_consent(candidate)
        if candidate.status != CandidateStatus.CANDIDATE.value:
            return candidate
        return self.store.transition_candidate(
            candidate_id,
            CandidateStatus.SANDBOXED,
            reason="privacy and authority boundary passed",
            details={"consent_id": candidate.consent_id, "external_effects": False},
        )

    # ------------------------------------------------------------------
    # Deterministic replay and shadow mode
    # ------------------------------------------------------------------

    def run_replay(
        self,
        candidate_id: str,
        cases: Iterable[ReplayCase],
        *,
        suite: str,
        mode: str = "deterministic",
        budget_limit: int | None = None,
    ) -> ReplayRun:
        candidate = self._get(candidate_id, LearningCandidate)
        case_list = list(cases)
        source_digest = hashlib.sha256(
            json.dumps(
                [self._case_digest_payload(case) for case in case_list],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        replay_id = stable_id("phase10-replay", candidate_id, suite, source_digest)
        if candidate.status != CandidateStatus.SANDBOXED.value:
            existing = self._existing(replay_id, ReplayRun)
            if existing is not None:
                return existing
            raise CandidateLifecycleError(
                f"replay requires sandboxed candidate, got {candidate.status}"
            )
        self._check_privacy_and_consent(candidate)
        run = ReplayRun(
            id=replay_id,
            candidate_id=candidate_id,
            suite=suite,
            mode=mode,
            provenance={
                "component": "phase10-deterministic-replay",
                "candidate_id": candidate_id,
                "simulation_only": True,
            },
            causal_parents=[candidate_id, *[case.id for case in case_list]],
            source_digest=source_digest,
        )
        self.store.put(run)
        total_cost = 0
        baseline_passed = 0
        candidate_passed = 0
        protected: list[str] = []
        forgetting: list[str] = []
        behavioral_regressions: list[str] = []
        unauthorized = 0
        duplicates = 0
        issues: list[str] = []
        adversarial_seen = False
        counterfactual_seen = False
        adversarial_passed = True
        counterfactual_passed = True
        disagreement = False
        stale = False
        budget_exceeded = False
        canceled = False
        privacy_compliant = True
        consent_compliant = True

        for case in case_list:
            total_cost += case.budget_cost
            if budget_limit is not None and total_cost > budget_limit:
                budget_exceeded = True
                issues.append(f"budget exceeded before case {case.name}")
                break
            if case.canceled:
                canceled = True
                issues.append(f"replay canceled at case {case.name}")
                break
            try:
                self._check_case_privacy(candidate, case)
            except ConsentRequiredError as exc:
                consent_compliant = False
                issues.append(str(exc))
                break
            except PrivacyViolationError as exc:
                privacy_compliant = False
                issues.append(str(exc))
                break
            if (
                case.source_version != candidate.source_version
                and candidate.source_version != "any"
            ):
                stale = True
                issues.append(f"stale source for case {case.name}")
            if case.disagreement:
                disagreement = True
                issues.append(f"source disagreement for case {case.name}")
            try:
                result = self._evaluate_case(candidate, case)
            except (KeyError, TypeError, ValueError) as exc:
                issues.append(f"invalid replay case {case.name}: {exc}")
                break
            result["case_id"] = case.id
            result["name"] = case.name
            result["tags"] = list(case.tags)
            result["source_version"] = case.source_version
            result["passed"] = result.get("candidate_matches", False)
            run.case_results.append(result)
            baseline_passed += int(result.get("baseline_matches", False))
            candidate_passed += int(result.get("candidate_matches", False))
            unauthorized += int(result.get("unauthorized_actions", 0))
            duplicates += int(result.get("duplicate_actions", 0))
            if result.get("protected_regression"):
                protected.append(case.id)
            if result.get("forgetting_regression"):
                forgetting.append(case.id)
            if not result.get("candidate_matches", False):
                behavioral_regressions.append(case.id)
            if "adversarial" in case.tags:
                adversarial_seen = True
                adversarial_passed = adversarial_passed and bool(result.get("passed"))
            if "counterfactual" in case.tags:
                counterfactual_seen = True
                counterfactual_passed = counterfactual_passed and bool(
                    result.get("passed")
                )

        if not adversarial_seen:
            adversarial_passed = False
            issues.append("required adversarial replay cases were not supplied")
        if not counterfactual_seen:
            counterfactual_passed = False
            issues.append("required counterfactual replay cases were not supplied")
        if unauthorized:
            issues.append("candidate produced unauthorized actions")
        if duplicates:
            issues.append("candidate produced duplicate actions")
        if protected:
            issues.append("protected-capability regression detected")
        if forgetting:
            issues.append("forgetting/catastrophic regression detected")
        if behavioral_regressions:
            issues.append("candidate behavior did not meet the named outcome")
        if stale:
            issues.append("source snapshot is stale")
        if disagreement:
            issues.append("source disagreement remains unresolved")

        passed = (
            bool(case_list)
            and not any(
                (
                    issues,
                    protected,
                    forgetting,
                    unauthorized,
                    duplicates,
                    stale,
                    disagreement,
                    budget_exceeded,
                    canceled,
                    not privacy_compliant,
                    not consent_compliant,
                )
            )
            and adversarial_passed
            and counterfactual_passed
        )
        run.status = (
            ReplayStatus.CANCELED.value
            if canceled
            else ReplayStatus.PASSED.value
            if passed
            else ReplayStatus.FAILED.value
        )
        run.passed = passed
        run.adversarial_passed = adversarial_passed
        run.counterfactual_passed = counterfactual_passed
        run.protected_capability_regressions = protected
        run.forgetting_regressions = forgetting
        run.unauthorized_actions = unauthorized
        run.duplicate_actions = duplicates
        run.stale_source = stale
        run.disagreement = disagreement
        run.budget_exceeded = budget_exceeded
        run.canceled = canceled
        run.privacy_compliant = privacy_compliant
        run.consent_compliant = consent_compliant
        run.issues = list(dict.fromkeys(issues))
        run.metrics = {
            "baseline_pass_rate": round(baseline_passed / len(case_list), 4)
            if case_list
            else 0.0,
            "candidate_pass_rate": round(candidate_passed / len(case_list), 4)
            if case_list
            else 0.0,
            "improvement": round(
                (candidate_passed - baseline_passed) / len(case_list), 4
            )
            if case_list
            else 0.0,
            "case_count": float(len(case_list)),
        }
        self.store.put(run)
        regression = RegressionResult(
            id=stable_id("phase10-regression", run.id),
            candidate_id=candidate_id,
            suite=suite,
            protected_capabilities=protected,
            forgetting_cases=forgetting,
            behavioral_regressions=behavioral_regressions,
            unauthorized_actions=unauthorized,
            duplicate_actions=duplicates,
            passed=passed,
            provenance={
                "component": "phase10-regression-gate",
                "replay_run_id": run.id,
            },
            causal_parents=[run.id, candidate_id],
        )
        self.store.put(regression)
        candidate.replay_run_ids = [*candidate.replay_run_ids, run.id]
        candidate.replay_run_ids = list(dict.fromkeys(candidate.replay_run_ids))
        self.store.put(candidate)
        if passed:
            self.store.transition_candidate(
                candidate_id,
                CandidateStatus.REPLAY_PASSED,
                reason="deterministic replay passed",
                details={"replay_run_id": run.id, "metrics": run.metrics},
            )
        elif not canceled:
            candidate.rejection_reasons.extend(run.issues)
            self.store.put(candidate)
            self.store.transition_candidate(
                candidate_id,
                CandidateStatus.REJECTED,
                reason="deterministic replay failed",
                details={"replay_run_id": run.id, "issues": run.issues},
            )
        else:
            self.store.audit(
                candidate_id,
                "replay:canceled",
                {"replay_run_id": run.id, "candidate_remains": candidate.status},
            )
        return run

    def run_shadow(
        self,
        candidate_id: str,
        cases: Iterable[ReplayCase],
        *,
        suite: str,
    ) -> ShadowRun:
        candidate = self._get(candidate_id, LearningCandidate)
        case_list = list(cases)
        shadow_id = stable_id("phase10-shadow", candidate_id, suite)
        if candidate.status != CandidateStatus.REPLAY_PASSED.value:
            existing = self._existing(shadow_id, ShadowRun)
            if existing is not None:
                return existing
            raise CandidateLifecycleError(
                f"shadow mode requires replay_passed candidate, got {candidate.status}"
            )
        self._check_privacy_and_consent(candidate)
        run = ShadowRun(
            id=shadow_id,
            candidate_id=candidate_id,
            suite=suite,
            provenance={
                "component": "phase10-shadow-mode",
                "simulation_only": True,
                "no_live_connectors": True,
            },
            causal_parents=[candidate_id, *[case.id for case in case_list]],
            baseline_digest=self._behavior_digest(candidate, active=False),
            candidate_digest=self._behavior_digest(candidate, active=True),
        )
        for case in case_list:
            try:
                self._check_case_privacy(candidate, case)
                result = self._evaluate_case(candidate, case)
            except (
                ConsentRequiredError,
                PrivacyViolationError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                run.issues.append(f"shadow case {case.name} rejected: {exc}")
                continue
            result["case_id"] = case.id
            result["shadow"] = True
            result["real_effect"] = False
            result["authority_created"] = False
            run.case_results.append(result)
        run.passed = bool(case_list) and all(
            result.get("candidate_matches", False)
            and not result.get("protected_regression", False)
            and not result.get("forgetting_regression", False)
            and result.get("unauthorized_actions", 0) == 0
            and result.get("duplicate_actions", 0) == 0
            for result in run.case_results
        )
        run.status = (
            ShadowStatus.PASSED.value if run.passed else ShadowStatus.FAILED.value
        )
        run.metrics = {
            "candidate_pass_rate": round(
                sum(bool(item.get("candidate_matches")) for item in run.case_results)
                / len(run.case_results),
                4,
            )
            if run.case_results
            else 0.0
        }
        if len(run.case_results) != len(case_list):
            run.passed = False
        if any(
            case.source_version != candidate.source_version
            and candidate.source_version != "any"
            or case.canceled
            or case.disagreement
            for case in case_list
        ):
            run.issues.append("shadow input was stale, canceled, or contradictory")
            run.passed = False
        run.status = (
            ShadowStatus.PASSED.value if run.passed else ShadowStatus.FAILED.value
        )
        self.store.put(run)
        candidate.shadow_run_ids = [*candidate.shadow_run_ids, run.id]
        candidate.shadow_run_ids = list(dict.fromkeys(candidate.shadow_run_ids))
        self.store.put(candidate)
        if run.passed:
            self.store.transition_candidate(
                candidate_id,
                CandidateStatus.SHADOW,
                reason="shadow run passed with no effects",
                details={
                    "shadow_run_id": run.id,
                    "real_effect": False,
                    "authority_created": False,
                },
            )
        else:
            run.issues.append("shadow behavior did not match replay expectations")
            self.store.put(run)
            self.store.transition_candidate(
                candidate_id,
                CandidateStatus.REJECTED,
                reason="shadow run failed",
                details={"shadow_run_id": run.id},
            )
        return run

    # ------------------------------------------------------------------
    # Explicit promotion and artifact boundary
    # ------------------------------------------------------------------

    def approve(
        self,
        candidate_id: str,
        *,
        marc_approved: bool,
        reviewer: str = "Marc",
    ) -> PromotionDecision:
        candidate = self._get(candidate_id, LearningCandidate)
        if candidate.status != CandidateStatus.SHADOW.value:
            raise CandidateLifecycleError(
                f"approval requires shadow candidate, got {candidate.status}"
            )
        if candidate.required_marc_approval and not marc_approved:
            raise ApprovalRequiredError("Marc approval is required before promotion")
        self._check_privacy_and_consent(candidate)
        replay = self._latest_replay(candidate_id)
        shadow = self._latest_shadow(candidate_id)
        improvement = float(replay.metrics.get("improvement", 0.0)) if replay else 0.0
        requirements = {
            "named_outcome_improved": improvement > 0,
            "replay_passed": bool(replay and replay.passed),
            "protected_capabilities_preserved": bool(
                replay and not replay.protected_capability_regressions
            ),
            "forgetting_checks_passed": bool(
                replay and not replay.forgetting_regressions
            ),
            "replay_sources_current": bool(replay and not replay.stale_source),
            "replay_sources_agree": bool(replay and not replay.disagreement),
            "replay_within_budget": bool(replay and not replay.budget_exceeded),
            "replay_not_canceled": bool(replay and not replay.canceled),
            "adversarial_replay_passed": bool(replay and replay.adversarial_passed),
            "counterfactual_replay_passed": bool(
                replay and replay.counterfactual_passed
            ),
            "privacy_compliant": bool(replay and replay.privacy_compliant),
            "consent_compliant": bool(replay and replay.consent_compliant),
            "no_unauthorized_actions": bool(
                replay and replay.unauthorized_actions == 0
            ),
            "no_duplicate_actions": bool(replay and replay.duplicate_actions == 0),
            "shadow_effect_free": bool(
                shadow
                and shadow.passed
                and not shadow.real_effect
                and not shadow.authority_created
                and not shadow.external_effects
            ),
            "provenance_complete": bool(
                candidate.provenance and candidate.causal_parents
            ),
            "reversible": candidate.reversible,
            "authority_not_created": not candidate.authority_scope,
        }
        rollback_verified = self._verify_rollback(candidate)
        requirements["rollback_verified"] = rollback_verified
        reasons = [key for key, value in requirements.items() if not value]
        decision = PromotionDecision(
            id=stable_id(
                "phase10-promotion",
                candidate_id,
                "approved" if not reasons else "rejected",
            ),
            candidate_id=candidate_id,
            decision="approved" if not reasons else "rejected",
            reviewer=reviewer,
            explicit_marc_approval=marc_approved,
            named_outcome=candidate.named_outcome,
            improvement=improvement,
            requirements=requirements,
            reasons=reasons,
            rollback_verified=rollback_verified,
            authority_created=False,
            provenance={
                "component": "phase10-promotion-gate",
                "candidate_id": candidate_id,
                "reviewer": reviewer,
            },
            causal_parents=[
                candidate_id,
                replay.id if replay else "",
                shadow.id if shadow else "",
            ],
        )
        self.store.put(decision)
        if reasons:
            candidate.rejection_reasons.extend(reasons)
            self.store.put(candidate)
            self.store.transition_candidate(
                candidate_id,
                CandidateStatus.REJECTED,
                reason="promotion requirements failed",
                details={"promotion_decision_id": decision.id, "reasons": reasons},
            )
        else:
            self.store.transition_candidate(
                candidate_id,
                CandidateStatus.APPROVED,
                reason="Marc approval and promotion requirements passed",
                details={"promotion_decision_id": decision.id},
            )
        return decision

    def stage(self, candidate_id: str) -> ArtifactVersion:
        candidate = self._get(candidate_id, LearningCandidate)
        if candidate.status != CandidateStatus.APPROVED.value:
            raise CandidateLifecycleError(
                f"staging requires approved candidate, got {candidate.status}"
            )
        previous = self._active_artifact()
        payload = {
            "candidate_id": candidate.id,
            "candidate_kind": candidate.candidate_kind,
            "proposal": candidate.proposal,
            "baseline_behavior": candidate.baseline_behavior,
            "expected_behavior": candidate.expected_behavior,
            "authority_scope": candidate.authority_scope,
            "reversible": candidate.reversible,
            "privacy_class": candidate.privacy_class,
            "named_outcome": candidate.named_outcome,
            "activation_scope": dict(SAFE_ACTIVATION_SCOPE),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        signature = hmac.new(
            self.signing_key, canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        artifact = ArtifactVersion(
            id=stable_id("phase10-artifact", candidate.id, digest),
            candidate_id=candidate.id,
            artifact_kind=candidate.candidate_kind,
            artifact_name=f"{candidate.candidate_kind}:{candidate.id}",
            version=f"phase10.{candidate.id[:8]}",
            digest=digest,
            signature=signature,
            signed_by=self.signer,
            stage=ArtifactStage.STAGED.value,
            previous_artifact_id=previous.id if previous else None,
            activation_scope=dict(SAFE_ACTIVATION_SCOPE),
            provenance={
                "component": "phase10-artifact-stager",
                "candidate_id": candidate.id,
                "signing": "hmac-sha256-local-review-key",
            },
            causal_parents=[candidate.id],
        )
        self.store.put(artifact)
        candidate.artifact_id = artifact.id
        self.store.put(candidate)
        self.store.transition_candidate(
            candidate_id,
            CandidateStatus.STAGED,
            reason="signed artifact staged; activation remains explicit",
            details={
                "artifact_id": artifact.id,
                "previous_artifact_id": artifact.previous_artifact_id,
            },
        )
        return artifact

    def activate(
        self, candidate_id: str, *, explicit_activation: bool
    ) -> ArtifactVersion:
        if not explicit_activation:
            raise ApprovalRequiredError("activation requires an explicit confirmation")
        candidate = self._get(candidate_id, LearningCandidate)
        if (
            candidate.status != CandidateStatus.STAGED.value
            or not candidate.artifact_id
        ):
            raise CandidateLifecycleError(
                "activation requires a staged candidate artifact"
            )
        try:
            artifact = self._get(candidate.artifact_id, ArtifactVersion)
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactIntegrityError("staged artifact could not be loaded") from exc
        if artifact.stage != ArtifactStage.STAGED.value:
            raise CandidateLifecycleError("activation requires a staged artifact")
        self._verify_artifact(artifact)
        previous = self._active_artifact()
        if previous and previous.id != artifact.id:
            previous.stage = ArtifactStage.ROLLED_BACK.value
            self.store.put(previous)
        artifact.stage = ArtifactStage.ACTIVE.value
        self.store.put(artifact)
        digest = self._behavior_digest(candidate, active=True)
        baseline_candidate = (
            self._get(previous.candidate_id, LearningCandidate)
            if previous
            else candidate
        )
        self.store.put(
            RuntimeState(
                id="phase10-runtime-state",
                active_artifact_id=artifact.id,
                active_candidate_id=candidate.id,
                active_behavior_digest=digest,
                baseline_behavior=dict(baseline_candidate.baseline_behavior),
                provenance={"component": "phase10-explicit-activation"},
                causal_parents=[artifact.id, candidate.id],
            )
        )
        self.store.transition_candidate(
            candidate_id,
            CandidateStatus.ACTIVE,
            reason="explicit artifact activation in deterministic fixture",
            details={"artifact_id": artifact.id, "live_external_effects": False},
        )
        return artifact

    def rollback(self, candidate_id: str, *, reason: str) -> RollbackRecord:
        candidate = self._get(candidate_id, LearningCandidate)
        if (
            candidate.status != CandidateStatus.ACTIVE.value
            or not candidate.artifact_id
        ):
            raise CandidateLifecycleError("rollback requires an active candidate")
        artifact = self._get(candidate.artifact_id, ArtifactVersion)
        self._verify_artifact(artifact)
        artifact.stage = ArtifactStage.ROLLED_BACK.value
        self.store.put(artifact)
        previous = (
            self._get(artifact.previous_artifact_id, ArtifactVersion)
            if artifact.previous_artifact_id
            else None
        )
        if previous:
            self._verify_artifact(previous)
            previous.stage = ArtifactStage.ACTIVE.value
            self.store.put(previous)
        restored_candidate = (
            self._get(previous.candidate_id, LearningCandidate)
            if previous
            else candidate
        )
        restored_digest = self._behavior_digest(
            restored_candidate, active=previous is not None
        )
        self.store.put(
            RuntimeState(
                id="phase10-runtime-state",
                active_artifact_id=previous.id if previous else None,
                active_candidate_id=previous.candidate_id if previous else None,
                active_behavior_digest=restored_digest,
                baseline_behavior=dict(restored_candidate.baseline_behavior),
                provenance={"component": "phase10-rollback"},
                causal_parents=[artifact.id],
            )
        )
        restored_state = self._get("phase10-runtime-state", RuntimeState)
        exact_restore = restored_state.active_behavior_digest == restored_digest
        authority_scope_restored = not restored_candidate.authority_scope
        record = RollbackRecord(
            id=stable_id("phase10-rollback", candidate_id, artifact.id, reason),
            candidate_id=candidate_id,
            artifact_id=artifact.id,
            restored_artifact_id=previous.id if previous else None,
            reason=reason,
            exact_restore=exact_restore,
            rollback_verified=exact_restore,
            authority_scope_restored=authority_scope_restored,
            restored_behavior_digest=restored_digest,
            provenance={"component": "phase10-verified-rollback"},
            causal_parents=[candidate_id, artifact.id],
        )
        self.store.put(record)
        if exact_restore and authority_scope_restored:
            self.store.transition_candidate(
                candidate_id,
                CandidateStatus.ROLLED_BACK,
                reason="verified rollback restored the prior fixture behavior",
                details={"rollback_id": record.id, "exact_restore": True},
            )
        return record

    def evaluate_policy(self, context: dict[str, Any]) -> dict[str, Any]:
        """Evaluate the currently active fixture policy, or the baseline.

        This is intentionally a pure deterministic function.  It does not
        call Home Assistant, create an action proposal, or consult Guardian.
        """

        if not isinstance(context, dict):
            raise ValueError("fixture policy context must be an object")
        state = self.store.get("phase10-runtime-state")
        active_id = (
            state.active_candidate_id if isinstance(state, RuntimeState) else None
        )
        active_candidate: LearningCandidate | None = None
        invalid_active_pointer = False
        if active_id:
            try:
                active_candidate = self._get(active_id, LearningCandidate)
                artifact = self._get(state.active_artifact_id or "", ArtifactVersion)
                if (
                    artifact.stage != ArtifactStage.ACTIVE.value
                    or artifact.candidate_id != active_candidate.id
                    or active_candidate.status != CandidateStatus.ACTIVE.value
                ):
                    raise ArtifactIntegrityError(
                        "runtime activation pointer is invalid"
                    )
                self._verify_artifact(artifact)
                if state.active_behavior_digest != self._behavior_digest(
                    active_candidate, active=True
                ):
                    raise ArtifactIntegrityError(
                        "runtime behavior digest does not match active candidate"
                    )
                return self._home_policy_output(active_candidate, context)
            except (ArtifactIntegrityError, KeyError, TypeError, ValueError):
                # A corrupted or stale pointer fails closed to the durable
                # baseline; it must never silently enable candidate behavior.
                invalid_active_pointer = True
        candidates = [
            item
            for item in self.store.list_records(kind="learning_candidate")
            if isinstance(item, LearningCandidate)
        ]
        if invalid_active_pointer:
            baseline = {"entry_light": "off"}
        else:
            baseline = (
                state.baseline_behavior
                if isinstance(state, RuntimeState) and state.baseline_behavior
                else active_candidate.baseline_behavior
                if active_candidate is not None
                else candidates[-1].baseline_behavior
                if candidates
                else {}
            )
        return {
            "entry_light": baseline.get(context.get("phase"), "off"),
            "general_away": "off",
        }

    # ------------------------------------------------------------------
    # Codex isolation fixture
    # ------------------------------------------------------------------

    def run_codex_template_experiment(
        self,
        *,
        primary_workspace: str | Path,
        experiment_root: str | Path,
        cases: Iterable[ReplayCase],
    ) -> tuple[LearningCandidate, ReplayRun, CodexIsolationResult]:
        case_list = list(cases)
        candidate = self.generate_candidate(
            candidate_kind=CandidateKind.CODEX_MISSION_TEMPLATE.value,
            named_outcome="Codex mission-template held-out approval-boundary accuracy",
            proposal={
                "template": "codex-mission-v2",
                "workspace_mode": "bounded-experiment-only",
                "writes_primary_workspace": False,
                "creates_guardian_authority": False,
            },
            baseline_behavior={"approval_boundary": "preserved"},
            expected_behavior={"approval_boundary": "preserved"},
            source_ids=[case.id for case in case_list],
            source_version="codex-history-v1",
            required_marc_approval=True,
        )
        isolation = copy_bounded_codex_workspace(
            primary_workspace, experiment_root, candidate_id=candidate.id
        )
        self.sandbox_candidate(candidate.id)
        run = self.run_replay(
            candidate.id,
            case_list,
            suite="phase10-codex-historical-missions",
            mode="bounded-codex-experiment",
        )
        self.store.audit(
            candidate.id,
            "codex:isolated-experiment",
            {
                "primary_workspace": str(primary_workspace),
                "experiment_workspace": isolation.experiment_workspace,
                "primary_unchanged": isolation.primary_unchanged,
                "authority_created": False,
                "deployment": False,
            },
        )
        return self._get(candidate.id, LearningCandidate), run, isolation

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get(self, record_id: str, expected: type[Any]) -> Any:
        record = self.store.get(record_id)
        if not isinstance(record, expected):
            raise TypeError(f"record {record_id} is not {expected.__name__}")
        return record

    def _existing(self, record_id: str, expected: type[Any]) -> Any | None:
        try:
            record = self.store.get(record_id)
        except KeyError:
            return None
        if not isinstance(record, expected):
            raise TypeError(f"record {record_id} is not {expected.__name__}")
        return record

    @staticmethod
    def _case_digest_payload(case: ReplayCase) -> dict[str, Any]:
        payload = case.to_dict()
        # Creation timestamps describe delivery, not the source fixture.  They
        # must not make an otherwise duplicate replay look like new evidence.
        payload.pop("created_at", None)
        payload.pop("valid_from", None)
        return payload

    def _latest_replay(self, candidate_id: str) -> ReplayRun | None:
        items = [
            item
            for item in self.store.list_records(kind="replay_run")
            if isinstance(item, ReplayRun) and item.candidate_id == candidate_id
        ]
        return items[-1] if items else None

    def _latest_shadow(self, candidate_id: str) -> ShadowRun | None:
        items = [
            item
            for item in self.store.list_records(kind="shadow_run")
            if isinstance(item, ShadowRun) and item.candidate_id == candidate_id
        ]
        return items[-1] if items else None

    def _active_artifact(self) -> ArtifactVersion | None:
        items = [
            item
            for item in self.store.list_records(kind="artifact_version")
            if isinstance(item, ArtifactVersion)
            and item.stage == ArtifactStage.ACTIVE.value
        ]
        if not items:
            return None
        artifact = items[-1]
        candidate = self._get(artifact.candidate_id, LearningCandidate)
        if candidate.status != CandidateStatus.ACTIVE.value:
            raise ArtifactIntegrityError(
                "an active artifact must reference an active candidate"
            )
        self._verify_artifact(artifact)
        return artifact

    def _evaluate_case(
        self, candidate: LearningCandidate, case: ReplayCase
    ) -> dict[str, Any]:
        expected = case.expected
        if candidate.candidate_kind in {
            CandidateKind.POLICY_PACK.value,
            CandidateKind.PROCEDURE.value,
        }:
            candidate_output = self._home_policy_output(candidate, case.context)
            baseline_output = {
                "entry_light": candidate.baseline_behavior.get(
                    case.context.get("phase"), "off"
                ),
                "general_away": "off",
            }
        elif candidate.candidate_kind == CandidateKind.CODEX_MISSION_TEMPLATE.value:
            baseline_output = dict(expected.get("baseline_output", {}))
            candidate_output = dict(expected.get("candidate_output", {}))
        else:
            baseline_output = dict(
                expected.get("baseline_output", candidate.baseline_behavior)
            )
            candidate_output = dict(
                expected.get("candidate_output", candidate.expected_behavior)
            )
        expected_output = dict(expected.get("expected_output", expected))
        candidate_matches = all(
            candidate_output.get(key) == value for key, value in expected_output.items()
        )
        baseline_matches = all(
            baseline_output.get(key) == value for key, value in expected_output.items()
        )
        protected_key = expected.get("protected_capability")
        protected_regression = bool(
            protected_key
            and candidate_output.get(protected_key)
            != expected_output.get(protected_key)
        )
        protected_regression = protected_regression or bool(
            "protected" in case.tags and not candidate_matches
        )
        forgetting = bool(
            "forgetting" in case.tags
            and candidate_output.get("approval_boundary") != "preserved"
        )
        return {
            "baseline_output": baseline_output,
            "candidate_output": candidate_output,
            "expected_output": expected_output,
            "baseline_matches": baseline_matches,
            "candidate_matches": candidate_matches,
            "protected_regression": protected_regression,
            "forgetting_regression": forgetting,
            "unauthorized_actions": int(expected.get("unauthorized_actions", 0)),
            "duplicate_actions": int(expected.get("duplicate_actions", 0)),
        }

    @staticmethod
    def _home_policy_output(
        candidate: LearningCandidate, context: dict[str, Any]
    ) -> dict[str, Any]:
        phase = context.get("phase")
        if context.get("contradictory_command"):
            entry = context["contradictory_command"]
        elif context.get("guest_present"):
            entry = "off"
        elif context.get("marc_leaves") and phase in {"sunset", "overnight"}:
            entry = "on"
        else:
            entry = "off"
        return {"entry_light": entry, "general_away": "off"}

    def _behavior_digest(self, candidate: LearningCandidate, *, active: bool) -> str:
        values = self._behavior_vector(candidate, active=active)
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def _behavior_vector(
        self, candidate: LearningCandidate, *, active: bool
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for phase in (
            "daytime",
            "sunset",
            "overnight",
            "guest_present",
            "contradictory_command",
        ):
            context = {
                "phase": phase,
                "marc_leaves": True,
                "guest_present": phase == "guest_present",
            }
            if phase == "contradictory_command":
                context["phase"] = "sunset"
                context["contradictory_command"] = "off"
            values[phase] = (
                self._home_policy_output(candidate, context)["entry_light"]
                if active
                else candidate.baseline_behavior.get(phase, "off")
            )
        return values

    def _verify_artifact(self, artifact: ArtifactVersion) -> None:
        candidate = self._get(artifact.candidate_id, LearningCandidate)
        payload = {
            "candidate_id": candidate.id,
            "candidate_kind": candidate.candidate_kind,
            "proposal": candidate.proposal,
            "baseline_behavior": candidate.baseline_behavior,
            "expected_behavior": candidate.expected_behavior,
            "authority_scope": candidate.authority_scope,
            "reversible": candidate.reversible,
            "privacy_class": candidate.privacy_class,
            "named_outcome": candidate.named_outcome,
            "activation_scope": dict(artifact.activation_scope),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        signature = hmac.new(
            self.signing_key, canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if (
            artifact.digest != digest
            or not hmac.compare_digest(artifact.signature, signature)
            or artifact.activation_scope != SAFE_ACTIVATION_SCOPE
        ):
            raise ArtifactIntegrityError(
                "artifact signature or digest verification failed"
            )

    def _verify_rollback(self, candidate: LearningCandidate) -> bool:
        baseline = self._behavior_vector(candidate, active=False)
        baseline_complete = candidate.candidate_kind not in {
            CandidateKind.POLICY_PACK.value,
            CandidateKind.PROCEDURE.value,
        } or _REQUIRED_BASELINE_PHASES.issubset(candidate.baseline_behavior)
        return bool(
            candidate.reversible
            and not candidate.authority_scope
            and candidate.baseline_behavior
            and baseline_complete
            and all(value is not None for value in baseline.values())
        )


__all__ = [
    "ApprovalRequiredError",
    "ArtifactIntegrityError",
    "AuthorityBoundaryError",
    "ConsentRequiredError",
    "LearningLaboratory",
    "LearningLaboratoryError",
    "PrivacyViolationError",
]
