"""Registered, reversible Home Assistant actions for the Guardian Kernel."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from openjarvis.behavior.catalog import get_intent_spec
from openjarvis.cognition import ActionError, ActionProposal, ExecutionOutcome
from openjarvis.tools.home_assistant import HomeAssistantTool

from .kernel import ActionDefinition, ActionRegistry, PreconditionResult

_REVERSIBLE_POWER_ACTIONS = {"turn_on", "turn_off"}


def register_home_assistant_actions(
    registry: ActionRegistry, tool: HomeAssistantTool
) -> None:
    """Register the small reversible Phase 3 Home Assistant action set."""
    for action in _REVERSIBLE_POWER_ACTIONS:
        action_type = f"home_assistant.{action}"
        try:
            registry.get(action_type)
        except KeyError:
            registry.register(_definition(action, tool))
    try:
        registry.get("home_assistant.alexa_text_command")
    except KeyError:
        registry.register(_alexa_text_command_definition(tool))


def _alexa_text_command_definition(tool: HomeAssistantTool) -> ActionDefinition:
    """Dispatch a typed Alexa command; HA's event stream observes the effect."""

    def execute(proposal: ActionProposal) -> ExecutionOutcome:
        try:
            tool._request(
                "POST",
                "/api/services/alexa_devices/send_text_command",
                {
                    "device_id": proposal.parameters["target"],
                    "text_command": proposal.parameters["text_command"],
                },
            )
        except (httpx.TimeoutException, TimeoutError) as exc:
            return ExecutionOutcome(False, str(exc), ActionError.AMBIGUOUS_EFFECT)
        except Exception as exc:
            return ExecutionOutcome(False, str(exc), ActionError.TRANSIENT)
        return ExecutionOutcome(
            True,
            {"device_id": proposal.parameters["target"], "service": "alexa_devices.send_text_command"},
        )

    return ActionDefinition(
        action_type="home_assistant.alexa_text_command",
        input_schema={
            "type": "object",
            "required": ["target", "text_command"],
            "properties": {
                "target": {"type": "string"},
                "text_command": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        },
        capability="home.assistant.write",
        risk_class="reversible",
        consequence_class="low",
        preconditions=lambda _proposal: [],
        executor=execute,
        # No synchronous verification: Alexa commands are observed later by
        # the Home Assistant context pipeline, not inferred from a media state.
        verifier=lambda _proposal: (True, {"verification": "context_async"}),
        freshness_seconds=15,
        expected_effect={"service": "alexa_devices.send_text_command"},
        compensation=None,
        redaction_policy="entity-id-only",
    )


def _definition(action: str, tool: HomeAssistantTool) -> ActionDefinition:
    opposite = "turn_off" if action == "turn_on" else "turn_on"

    def preconditions(proposal: ActionProposal) -> list[PreconditionResult]:
        target = str(proposal.parameters["target"])
        try:
            state = tool._request("GET", f"/api/states/{target}")
        except Exception as exc:
            return [
                PreconditionResult(
                    "home-assistant-state",
                    False,
                    datetime.now(timezone.utc),
                    {"error": str(exc)},
                )
            ]
        spec = get_intent_spec(action)
        domain = target.split(".", 1)[0]
        valid = (
            isinstance(state, dict)
            and state.get("entity_id") == target
            and spec is not None
            and domain in spec.domains
            and state.get("state") not in {"unavailable", "unknown"}
        )
        return [
            PreconditionResult(
                "home-assistant-state",
                valid,
                datetime.now(timezone.utc),
                {
                    "entity_id": target,
                    "observed_state": state.get("state")
                    if isinstance(state, dict)
                    else None,
                },
            )
        ]

    def execute(proposal: ActionProposal) -> ExecutionOutcome:
        target = str(proposal.parameters["target"])
        spec = get_intent_spec(action)
        if spec is None or not spec.service:
            return ExecutionOutcome(
                False, "unsupported registered action", ActionError.INVALID
            )
        try:
            tool._call_service(target.split(".", 1)[0], spec.service, target)
        except (httpx.TimeoutException, TimeoutError) as exc:
            return ExecutionOutcome(False, str(exc), ActionError.AMBIGUOUS_EFFECT)
        except Exception as exc:
            return ExecutionOutcome(False, str(exc), ActionError.TRANSIENT)
        return ExecutionOutcome(True, {"entity_id": target, "service": spec.service})

    def verify(proposal: ActionProposal) -> tuple[bool, Any]:
        target = str(proposal.parameters["target"])
        expected = "on" if action == "turn_on" else "off"
        try:
            state = tool._request("GET", f"/api/states/{target}")
        except Exception as exc:
            raise RuntimeError("independent Home Assistant read failed") from exc
        return (
            isinstance(state, dict) and str(state.get("state")).lower() == expected,
            state,
        )

    def compensate(proposal: ActionProposal) -> ExecutionOutcome:
        target = str(proposal.parameters["target"])
        spec = get_intent_spec(opposite)
        if spec is None or not spec.service:
            return ExecutionOutcome(
                False, "unsupported compensation", ActionError.INVALID
            )
        try:
            tool._call_service(target.split(".", 1)[0], spec.service, target)
        except (httpx.TimeoutException, TimeoutError) as exc:
            return ExecutionOutcome(False, str(exc), ActionError.AMBIGUOUS_EFFECT)
        except Exception as exc:
            return ExecutionOutcome(False, str(exc), ActionError.TRANSIENT)
        return ExecutionOutcome(True, {"entity_id": target, "service": spec.service})

    return ActionDefinition(
        action_type=f"home_assistant.{action}",
        input_schema={
            "type": "object",
            "required": ["target"],
            "properties": {"target": {"type": "string"}},
            "additionalProperties": False,
        },
        capability="home.assistant.write",
        risk_class="reversible",
        consequence_class="low",
        preconditions=preconditions,
        executor=execute,
        verifier=verify,
        freshness_seconds=15,
        expected_effect={"state": "on" if action == "turn_on" else "off"},
        compensation=compensate,
        redaction_policy="entity-id-only",
    )
