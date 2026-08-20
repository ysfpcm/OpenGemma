"""Phase 7 adapter boundaries.

The production path uses the project's configured Home Assistant and channel
services.  The in-memory adapters remain available only for deterministic
replay tests; the server never selects them.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from openjarvis.cognition import ActionError, ActionProposal, ExecutionOutcome
from openjarvis.guardian import ActionDefinition, ActionRegistry, PreconditionResult


class HomeAssistantAdapter(Protocol):
    """Minimal typed surface required by the Guardian action registry."""

    def read_state(self, target: str) -> dict[str, Any]: ...

    def apply(
        self, action_type: str, target: str, parameters: dict[str, Any]
    ) -> ExecutionOutcome: ...

    def verify(
        self, action_type: str, target: str, parameters: dict[str, Any]
    ) -> tuple[bool, Any]: ...

    def compensate(
        self, action_type: str, target: str, parameters: dict[str, Any]
    ) -> ExecutionOutcome: ...


class NotificationAdapter(Protocol):
    """Minimal typed surface for a configured outbound channel."""

    def available(self, target: str) -> bool: ...

    def deliver(self, target: str, parameters: dict[str, Any]) -> ExecutionOutcome: ...


class LiveHomeAssistantAdapter:
    """Real Home Assistant REST adapter used by the live standby server.

    It performs no calls until Guardian authorizes an action.  Read and verify
    calls use the same HA URL/token aliases as the existing HomeAssistantTool.
    """

    def __init__(
        self,
        *,
        url: str | None = None,
        token: str | None = None,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.url = (
            url
            or os.environ.get("HA_URL", "").strip()
            or os.environ.get("HOME_ASSISTANT_URL", "").strip()
            or "http://127.0.0.1:8123"
        ).rstrip("/")
        self.token = (
            token
            or os.environ.get("HA_TOKEN", "").strip()
            or os.environ.get("HOME_ASSISTANT_TOKEN", "").strip()
        ).strip()
        self.timeout_seconds = float(timeout_seconds)
        if not self.url or not self.token:
            raise RuntimeError(
                "Home Assistant is required for live Departure Guardian "
                "(set HA_URL and HOME_ASSISTANT_TOKEN)"
            )

    def read_state(self, target: str) -> dict[str, Any]:
        state = self._request("GET", f"/api/states/{target}")
        if not isinstance(state, dict) or state.get("entity_id") != target:
            raise RuntimeError("Home Assistant returned an invalid entity state")
        attributes = state.get("attributes")
        if isinstance(attributes, dict):
            # Keep the raw HA payload while exposing the fields used by the
            # typed Guardian verifier in a stable, adapter-owned shape.
            result = dict(state)
            result["attributes"] = dict(attributes)
            if "current_position" in attributes:
                result["position"] = attributes["current_position"]
            if "preset_mode" in attributes:
                result["preset_mode"] = attributes["preset_mode"]
            if "temperature" in attributes:
                result["target_temperature"] = attributes["temperature"]
            return result
        return dict(state)

    def apply(
        self, action_type: str, target: str, parameters: dict[str, Any]
    ) -> ExecutionOutcome:
        try:
            domain, service, body = self._service_call(action_type, target, parameters)
            response = self._request("POST", f"/api/services/{domain}/{service}", body)
            return ExecutionOutcome(True, response)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            error = (
                ActionError.INVALID
                if 400 <= status < 500
                else ActionError.AMBIGUOUS_EFFECT
            )
            return ExecutionOutcome(False, f"Home Assistant HTTP {status}", error)
        except (httpx.HTTPError, TimeoutError, OSError):
            return ExecutionOutcome(
                False, "Home Assistant request failed", ActionError.AMBIGUOUS_EFFECT
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            return ExecutionOutcome(False, str(exc), ActionError.INVALID)

    def verify(
        self, action_type: str, target: str, parameters: dict[str, Any]
    ) -> tuple[bool, Any]:
        state = self.read_state(target)
        normalized = str(state.get("state", "")).lower()
        if action_type in {"home_assistant.light.on", "home_assistant.turn_on"}:
            return normalized == "on", state
        if action_type in {"home_assistant.light.off", "home_assistant.turn_off"}:
            return normalized == "off", state
        if action_type == "home_assistant.thermostat.preset":
            return str(state.get("preset_mode", normalized)) == str(
                parameters["preset"]
            ), state
        if action_type == "home_assistant.thermostat.target":
            return _numeric_close(
                state.get("target_temperature"), float(parameters["temperature"])
            ), state
        if action_type == "home_assistant.cover.position":
            return _numeric_close(
                state.get("position"), float(parameters["position"]), tolerance=5
            ), state
        if action_type == "home_assistant.alarm.arm":
            return normalized in {"armed_away", "armed_home", "armed_night"}, state
        if action_type == "home_assistant.read_state":
            return True, state
        return False, state

    def compensate(
        self, action_type: str, target: str, parameters: dict[str, Any]
    ) -> ExecutionOutcome:
        # Compensation is intentionally explicit.  The live adapter only
        # supports safe inverse operations when the caller supplied the prior
        # state in the persisted action parameters.
        prior = parameters.get("prior_state")
        if not isinstance(prior, dict):
            return ExecutionOutcome(
                False,
                "Live compensation requires the persisted prior state",
                ActionError.INVALID,
            )
        state = str(prior.get("state", "")).lower()
        if action_type in {"home_assistant.light.on", "home_assistant.light.off"}:
            inverse = (
                "home_assistant.light.on"
                if state == "on"
                else "home_assistant.light.off"
            )
            return self.apply(inverse, target, {"target": target})
        return ExecutionOutcome(
            False,
            "Live compensation is not configured for this action",
            ActionError.INVALID,
        )

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        response = httpx.request(
            method,
            f"{self.url}{path}",
            headers={"Authorization": f"Bearer {self.token}"},
            json=body,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _service_call(
        action_type: str, target: str, parameters: dict[str, Any]
    ) -> tuple[str, str, dict[str, Any]]:
        if action_type in {"home_assistant.light.on", "home_assistant.turn_on"}:
            return "light", "turn_on", {"entity_id": target}
        if action_type in {"home_assistant.light.off", "home_assistant.turn_off"}:
            return "light", "turn_off", {"entity_id": target}
        if action_type == "home_assistant.thermostat.preset":
            return (
                "climate",
                "set_preset_mode",
                {
                    "entity_id": target,
                    "preset_mode": str(parameters["preset"]),
                },
            )
        if action_type == "home_assistant.thermostat.target":
            return (
                "climate",
                "set_temperature",
                {
                    "entity_id": target,
                    "temperature": float(parameters["temperature"]),
                },
            )
        if action_type == "home_assistant.cover.position":
            return (
                "cover",
                "set_cover_position",
                {
                    "entity_id": target,
                    "position": int(parameters["position"]),
                },
            )
        if action_type == "home_assistant.alarm.arm":
            return "alarm_control_panel", "alarm_arm_away", {"entity_id": target}
        if action_type == "home_assistant.read_state":
            raise ValueError("read_state is not an executable service")
        raise ValueError(f"unsupported Home Assistant action: {action_type}")


class LiveNotificationAdapter:
    """Use the configured ChannelBridge for real outbound reminders."""

    def __init__(self, bridge: Any | None) -> None:
        self.bridge = bridge
        self._acknowledged: set[tuple[str, str]] = set()

    def available(self, target: str) -> bool:
        if self.bridge is None or not str(target).strip():
            return False
        try:
            target = str(target)
            if target in self.bridge.list_channels():
                return True
            channels = getattr(self.bridge, "_channels", {})
            return bool(
                re.fullmatch(r"[+]?[0-9]{7,15}", target)
                and isinstance(channels, dict)
                and "sendblue" in channels
            )
        except Exception:
            return False

    def deliver(self, target: str, parameters: dict[str, Any]) -> ExecutionOutcome:
        if not self.available(target):
            return ExecutionOutcome(
                False,
                "configured notification channel is unavailable",
                ActionError.TRANSIENT,
            )
        try:
            sent = self.bridge.send(str(target), str(parameters["message"]))
        except Exception:
            return ExecutionOutcome(
                False, "notification delivery failed", ActionError.TRANSIENT
            )
        if sent:
            self._acknowledged.add((str(target), str(parameters["message"])))
        return ExecutionOutcome(
            bool(sent),
            {"target": target, "delivered": bool(sent)},
            None if sent else ActionError.TRANSIENT,
        )

    def verify(self, target: str, parameters: dict[str, Any]) -> tuple[bool, Any]:
        # ChannelBridge.send is the transport's delivery acknowledgement. A
        # second send would duplicate the user's reminder, so verification is
        # limited to the recorded acknowledgement.
        key = (str(target), str(parameters.get("message", "")))
        return key in self._acknowledged, {
            "target": target,
            "verified": key in self._acknowledged,
            "reason": "transport acknowledgement",
        }


class FakeHomeAssistantAdapter:
    """Small in-memory Home Assistant twin with observable call history."""

    def __init__(self, states: dict[str, dict[str, Any]] | None = None) -> None:
        self.states = {key: dict(value) for key, value in (states or {}).items()}
        self.calls: list[dict[str, Any]] = []
        self.fail_actions: set[str] = set()
        self.ambiguous_actions: set[str] = set()
        self.disagree_on: set[str] = set()
        self.offline = False

    def read_state(self, target: str) -> dict[str, Any]:
        if self.offline:
            raise RuntimeError("fake Home Assistant is offline")
        state = self.states.get(target)
        if state is None:
            raise KeyError(target)
        return dict(state)

    def apply(
        self, action_type: str, target: str, parameters: dict[str, Any]
    ) -> ExecutionOutcome:
        self.calls.append(
            {
                "action_type": action_type,
                "target": target,
                "parameters": dict(parameters),
            }
        )
        if self.offline:
            return ExecutionOutcome(
                False, "fake Home Assistant is offline", ActionError.AMBIGUOUS_EFFECT
            )
        if action_type in self.fail_actions:
            return ExecutionOutcome(
                False, "seeded adapter failure", ActionError.PERMANENT
            )
        if action_type in self.ambiguous_actions:
            return ExecutionOutcome(
                False, "seeded timeout after dispatch", ActionError.AMBIGUOUS_EFFECT
            )
        state = self.states.setdefault(
            target, {"entity_id": target, "state": "unknown"}
        )
        if action_type in {"home_assistant.light.on", "home_assistant.turn_on"}:
            state["state"] = "on"
        elif action_type in {"home_assistant.light.off", "home_assistant.turn_off"}:
            state["state"] = "off"
        elif action_type == "home_assistant.thermostat.preset":
            state["state"] = str(parameters["preset"])
            state["preset_mode"] = str(parameters["preset"])
        elif action_type == "home_assistant.thermostat.target":
            state["target_temperature"] = float(parameters["temperature"])
            state["state"] = "heat"
        elif action_type == "home_assistant.cover.position":
            state["position"] = int(parameters["position"])
            state["state"] = "open" if state["position"] > 0 else "closed"
        elif action_type == "home_assistant.alarm.arm":
            state["state"] = "armed"
        elif action_type == "home_assistant.read_state":
            pass
        else:
            return ExecutionOutcome(
                False, "unsupported fake Home Assistant action", ActionError.INVALID
            )
        return ExecutionOutcome(True, dict(state))

    def verify(
        self, action_type: str, target: str, parameters: dict[str, Any]
    ) -> tuple[bool, Any]:
        state = self.read_state(target)
        if action_type in self.disagree_on:
            return False, {**state, "verification_disagreement": True}
        if action_type in {"home_assistant.light.on", "home_assistant.turn_on"}:
            return str(state.get("state", "")).lower() == "on", state
        if action_type in {"home_assistant.light.off", "home_assistant.turn_off"}:
            return str(state.get("state", "")).lower() == "off", state
        if action_type == "home_assistant.thermostat.preset":
            return str(state.get("preset_mode", state.get("state", ""))) == str(
                parameters["preset"]
            ), state
        if action_type == "home_assistant.thermostat.target":
            return float(state.get("target_temperature")) == float(
                parameters["temperature"]
            ), state
        if action_type == "home_assistant.cover.position":
            return "position" in state and int(state["position"]) == int(
                parameters["position"]
            ), state
        if action_type == "home_assistant.alarm.arm":
            return str(state.get("state", "")).lower() == "armed", state
        if action_type == "home_assistant.read_state":
            return True, state
        return False, state

    def compensate(
        self, action_type: str, target: str, parameters: dict[str, Any]
    ) -> ExecutionOutcome:
        return ExecutionOutcome(
            True, {"compensated": False, "reason": "deterministic replay adapter"}
        )


class FakeNotificationAdapter:
    """Local notification sink used by replay; it never sends externally."""

    def __init__(self) -> None:
        self.deliveries: list[dict[str, Any]] = []
        self.offline = False

    def deliver(self, target: str, parameters: dict[str, Any]) -> ExecutionOutcome:
        if self.offline:
            return ExecutionOutcome(
                False, "fake notification channel is offline", ActionError.TRANSIENT
            )
        delivery = {"target": target, **dict(parameters)}
        self.deliveries.append(delivery)
        return ExecutionOutcome(True, delivery)

    def available(self, target: str) -> bool:
        return not self.offline and bool(target)

    def verify(self, target: str, parameters: dict[str, Any]) -> tuple[bool, Any]:
        return any(item.get("target") == target for item in self.deliveries), {
            "target": target,
            "delivery_count": len(self.deliveries),
        }


def register_departure_adapters(
    registry: ActionRegistry,
    home: HomeAssistantAdapter,
    notifications: NotificationAdapter | None = None,
) -> None:
    """Register the narrow Phase 7 adapter vocabulary.

    The adapter instance is supplied by the runtime. This keeps the Guardian
    registry typed and allow-listed without selecting an integration from
    model output.
    """

    notifications = notifications or FakeNotificationAdapter()
    definitions = {
        "home_assistant.read_state": _home_definition(
            "home_assistant.read_state", home, consequence="low", reversible=False
        ),
        "home_assistant.light.on": _home_definition(
            "home_assistant.light.on",
            home,
            consequence="low",
            reversible=True,
            parameters={"target": {"type": "string"}},
            expected={"state": "on"},
        ),
        "home_assistant.light.off": _home_definition(
            "home_assistant.light.off",
            home,
            consequence="low",
            reversible=True,
            parameters={"target": {"type": "string"}},
            expected={"state": "off"},
        ),
        # Keep the Phase 3 Home Assistant aliases valid at the Phase 7
        # boundary.  They are the same narrow light operations, not a wider
        # executor vocabulary.
        "home_assistant.turn_on": _home_definition(
            "home_assistant.turn_on",
            home,
            consequence="low",
            reversible=True,
            parameters={"target": {"type": "string"}},
            expected={"state": "on"},
        ),
        "home_assistant.turn_off": _home_definition(
            "home_assistant.turn_off",
            home,
            consequence="low",
            reversible=True,
            parameters={"target": {"type": "string"}},
            expected={"state": "off"},
        ),
        "home_assistant.thermostat.preset": _home_definition(
            "home_assistant.thermostat.preset",
            home,
            consequence="medium",
            reversible=True,
            parameters={"target": {"type": "string"}, "preset": {"type": "string"}},
        ),
        "home_assistant.thermostat.target": _home_definition(
            "home_assistant.thermostat.target",
            home,
            consequence="medium",
            reversible=True,
            parameters={
                "target": {"type": "string"},
                "temperature": {"type": "number"},
            },
        ),
        "home_assistant.cover.position": _home_definition(
            "home_assistant.cover.position",
            home,
            consequence="medium",
            reversible=True,
            parameters={"target": {"type": "string"}, "position": {"type": "integer"}},
            requires_feedback=True,
        ),
        "home_assistant.alarm.arm": _home_definition(
            "home_assistant.alarm.arm",
            home,
            consequence="high",
            reversible=False,
            parameters={"target": {"type": "string"}},
            expected={"state": "armed"},
        ),
        "notification.reminder": _notification_definition(notifications),
    }
    for action_type, definition in definitions.items():
        try:
            registry.get(action_type)
        except KeyError:
            registry.register(definition)


def _schema(parameters: dict[str, dict[str, str]]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["target"],
        "properties": parameters,
        "additionalProperties": False,
    }


def _home_definition(
    action_type: str,
    home: HomeAssistantAdapter,
    *,
    consequence: str,
    reversible: bool,
    parameters: dict[str, dict[str, str]] | None = None,
    expected: dict[str, Any] | None = None,
    requires_feedback: bool = False,
) -> ActionDefinition:
    parameters = parameters or {"target": {"type": "string"}}

    def preconditions(proposal: ActionProposal) -> list[PreconditionResult]:
        now = datetime.now(timezone.utc)
        try:
            state = home.read_state(str(proposal.parameters["target"]))
        except Exception as exc:
            return [PreconditionResult("home-state", False, now, {"error": str(exc)})]
        usable = state.get("entity_id") == proposal.parameters["target"] and str(
            state.get("state", "")
        ).lower() not in {"unknown", "unavailable"}
        if requires_feedback and "position" not in state:
            usable = False
        return [PreconditionResult("home-state", usable, now, {"state": state})]

    def execute(proposal: ActionProposal) -> ExecutionOutcome:
        return home.apply(
            action_type, str(proposal.parameters["target"]), proposal.parameters
        )

    def verify(proposal: ActionProposal) -> tuple[bool, Any]:
        return home.verify(
            action_type, str(proposal.parameters["target"]), proposal.parameters
        )

    def compensate(proposal: ActionProposal) -> ExecutionOutcome:
        return home.compensate(
            action_type,
            str(proposal.parameters["target"]),
            proposal.parameters,
        )

    return ActionDefinition(
        action_type=action_type,
        input_schema=_schema(parameters),
        capability="home.assistant.read"
        if action_type.endswith("read_state")
        else "home.assistant.write",
        risk_class="read-only"
        if action_type.endswith("read_state")
        else "reversible"
        if reversible
        else "high-consequence",
        consequence_class=consequence,
        preconditions=preconditions,
        executor=execute,
        verifier=verify,
        freshness_seconds=15,
        expected_effect=expected or {},
        compensation=compensate if reversible else None,
        redaction_policy="entity-id-only",
    )


def _notification_definition(notification: NotificationAdapter) -> ActionDefinition:
    def preconditions(proposal: ActionProposal) -> list[PreconditionResult]:
        return [
            PreconditionResult(
                "notification-channel",
                notification.available(str(proposal.parameters.get("target", ""))),
                datetime.now(timezone.utc),
                {"target": proposal.parameters.get("target")},
            )
        ]

    def execute(proposal: ActionProposal) -> ExecutionOutcome:
        return notification.deliver(
            str(proposal.parameters["target"]), proposal.parameters
        )

    def verify(proposal: ActionProposal) -> tuple[bool, Any]:
        verify = getattr(notification, "verify", None)
        if verify is None:
            return False, {"verified": False}
        return verify(str(proposal.parameters.get("target", "")), proposal.parameters)

    return ActionDefinition(
        action_type="notification.reminder",
        input_schema={
            "type": "object",
            "required": ["target", "message"],
            "properties": {"target": {"type": "string"}, "message": {"type": "string"}},
            "additionalProperties": False,
        },
        capability="notification.reminder",
        risk_class="communication",
        consequence_class="medium",
        preconditions=preconditions,
        executor=execute,
        verifier=verify,
        freshness_seconds=30,
        compensation=None,
        redaction_policy="message-metadata-only",
    )


def _numeric_close(value: Any, target: float, *, tolerance: float = 0.5) -> bool:
    try:
        return abs(float(value) - target) <= tolerance
    except (TypeError, ValueError):
        return False


__all__ = [
    "HomeAssistantAdapter",
    "LiveHomeAssistantAdapter",
    "LiveNotificationAdapter",
    "NotificationAdapter",
    "FakeHomeAssistantAdapter",
    "FakeNotificationAdapter",
    "register_departure_adapters",
]
