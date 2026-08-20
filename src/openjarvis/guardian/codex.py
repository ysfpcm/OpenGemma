"""Guardian records for Codex native-protocol approval requests."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from openjarvis.cognition import ActionError, ActionProposal, ExecutionOutcome

from .kernel import (
    ActionDefinition,
    ActionRegistry,
    GuardianDecision,
    GuardianKernel,
    PreconditionResult,
)

_KINDS = {
    "command": "codex.command.approval",
    "file-change": "codex.file-change.approval",
    "permission": "codex.permission.approval",
    "network": "codex.network.approval",
    "user-input": "codex.user-input.approval",
    "unsupported": "codex.unsupported.approval",
}


def _pending_precondition(_: ActionProposal) -> list[PreconditionResult]:
    return [
        PreconditionResult(
            "native-protocol-response-pending", True, datetime.now(timezone.utc)
        )
    ]


def _not_an_executor(_: ActionProposal) -> ExecutionOutcome:
    return ExecutionOutcome(
        False, "Codex protocol approvals are answered natively", ActionError.INVALID
    )


def _native_reply_pending(_: ActionProposal) -> tuple[bool, dict[str, str]]:
    return False, {"native_protocol": "pending"}


def register_codex_approval_actions(registry: ActionRegistry) -> None:
    """Register protocol-approval records, never arbitrary Codex executors."""
    for kind, action_type in _KINDS.items():
        try:
            registry.get(action_type)
        except KeyError:
            registry.register(
                ActionDefinition(
                    action_type=action_type,
                    input_schema={
                        "type": "object",
                        "required": ["target", "request_id", "request_digest"],
                        "properties": {
                            "target": {"type": "string"},
                            "request_id": {"type": "string"},
                            "request_digest": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                    capability=f"codex.{kind}.approval",
                    risk_class="approval",
                    consequence_class="bounded",
                    preconditions=_pending_precondition,
                    executor=_not_an_executor,
                    verifier=_native_reply_pending,
                    expected_effect={"native_protocol_response": "allowed"},
                    redaction_policy="request-digest-only",
                )
            )


class CodexGuardianBridge:
    """Create exact Guardian authorization before a native Codex reply."""

    def __init__(self, kernel: GuardianKernel) -> None:
        self.kernel = kernel
        register_codex_approval_actions(kernel.registry)

    def authorize(
        self,
        *,
        mission_id: str,
        decision_id: str,
        request_id: int | str,
        kind: str,
        workspace: str,
        requested: dict[str, Any],
    ) -> GuardianDecision:
        proposal = self._proposal(
            mission_id, decision_id, request_id, kind, workspace, requested
        )
        definition = self.kernel.registry.get(proposal.action_type)
        self.kernel.grant(
            grant_id=f"codex-grant:{decision_id}",
            session_id=f"codex-decision:{decision_id}",
            capability=definition.capability,
            scope={
                "action_type": proposal.action_type,
                "target": workspace,
                "parameters_hash": self._parameter_hash(proposal.parameters),
            },
            expires_in_seconds=300,
        )
        return self.kernel.authorize(
            proposal,
            session_id=f"codex-decision:{decision_id}",
            authority="Marc",
        )

    def deny(
        self,
        *,
        mission_id: str,
        decision_id: str,
        request_id: int | str,
        kind: str,
        workspace: str,
        requested: dict[str, Any],
    ) -> GuardianDecision:
        proposal = self._proposal(
            mission_id, decision_id, request_id, kind, workspace, requested
        )
        # No grant is created: the kernel writes an immutable denial.
        return self.kernel.authorize(
            proposal,
            session_id=f"codex-decision:{decision_id}",
            authority="Guardian",
        )

    def _proposal(
        self,
        mission_id: str,
        decision_id: str,
        request_id: int | str,
        kind: str,
        workspace: str,
        requested: dict[str, Any],
    ) -> ActionProposal:
        safe_request = json.dumps(requested, sort_keys=True, separators=(",", ":"))
        return ActionProposal(
            action_type=_KINDS.get(kind, _KINDS["unsupported"]),
            description=f"Codex {kind} approval",
            parameters={
                "target": workspace,
                "request_id": str(request_id),
                "request_digest": hashlib.sha256(safe_request.encode()).hexdigest(),
            },
            idempotency_key=f"codex:{mission_id}:{decision_id}",
            expected_effect={"native_protocol_response": "pending"},
            sensitivity_labels=["codex-request-redacted"],
            provenance={"component": "codex-guardian-bridge"},
        )

    @staticmethod
    def _parameter_hash(parameters: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(parameters, sort_keys=True).encode()
        ).hexdigest()
