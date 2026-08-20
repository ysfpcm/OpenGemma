"""Strict, JSON-safe Phase 7 Departure Guardian data contracts.

The contracts in this module deliberately keep imported text in evidence
fields.  Action authority is represented only by typed ``DepartureAction``
records and the Phase 6 plan/grant contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from openjarvis.planning import ObservationSnapshot


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


def _now() -> str:
    return _timestamp(datetime.now(timezone.utc))


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be boolean")
    return value


def _optional_bool(value: Any, field: str) -> bool | None:
    if value is None:
        return None
    return _bool(value, field)


class SourceStatus(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"
    CONTRADICTORY = "contradictory"
    OFFLINE = "offline"


@dataclass(frozen=True, slots=True)
class CalendarSource:
    source_id: str
    provider: str
    calendar_id: str
    timezone_name: str = "UTC"
    freshness_seconds: int = 86_400

    def __post_init__(self) -> None:
        if not self.source_id or not self.provider or not self.calendar_id:
            raise ValueError("calendar source requires source, provider, and calendar")
        if self.freshness_seconds < 0:
            raise ValueError("calendar freshness must not be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "provider": self.provider,
            "calendar_id": self.calendar_id,
            "timezone_name": self.timezone_name,
            "freshness_seconds": self.freshness_seconds,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CalendarSource":
        return cls(
            source_id=str(value["source_id"]),
            provider=str(value["provider"]),
            calendar_id=str(value["calendar_id"]),
            timezone_name=str(value.get("timezone_name", "UTC")),
            freshness_seconds=int(value.get("freshness_seconds", 86_400)),
        )


@dataclass(frozen=True, slots=True)
class DestinationAssumptions:
    label: str
    address: str
    route_id: str
    travel_mode: str = "driving"
    arrival_grace_minutes: int = 0

    def __post_init__(self) -> None:
        if not self.label or not self.address or not self.route_id:
            raise ValueError(
                "destination assumptions require label, address, and route"
            )
        if self.arrival_grace_minutes < 0:
            raise ValueError("arrival grace must not be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "address": self.address,
            "route_id": self.route_id,
            "travel_mode": self.travel_mode,
            "arrival_grace_minutes": self.arrival_grace_minutes,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DestinationAssumptions":
        return cls(
            label=str(value["label"]),
            address=str(value["address"]),
            route_id=str(value["route_id"]),
            travel_mode=str(value.get("travel_mode", "driving")),
            arrival_grace_minutes=int(value.get("arrival_grace_minutes", 0)),
        )


@dataclass(frozen=True, slots=True)
class PreparationBuffer:
    minutes: int
    source_id: str = "departure-config"

    def __post_init__(self) -> None:
        if self.minutes < 0 or not self.source_id:
            raise ValueError("preparation buffer must be non-negative and sourced")

    def to_dict(self) -> dict[str, Any]:
        return {"minutes": self.minutes, "source_id": self.source_id}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PreparationBuffer":
        return cls(
            int(value["minutes"]), str(value.get("source_id", "departure-config"))
        )


@dataclass(frozen=True, slots=True)
class TrafficSource:
    source_id: str
    freshness_seconds: int = 900

    def __post_init__(self) -> None:
        if not self.source_id or self.freshness_seconds < 0:
            raise ValueError("traffic source is incomplete")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "freshness_seconds": self.freshness_seconds,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TrafficSource":
        return cls(str(value["source_id"]), int(value.get("freshness_seconds", 900)))


@dataclass(frozen=True, slots=True)
class WeatherSource:
    source_id: str
    freshness_seconds: int = 1_800

    def __post_init__(self) -> None:
        if not self.source_id or self.freshness_seconds < 0:
            raise ValueError("weather source is incomplete")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "freshness_seconds": self.freshness_seconds,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WeatherSource":
        return cls(str(value["source_id"]), int(value.get("freshness_seconds", 1_800)))


@dataclass(frozen=True, slots=True)
class PresenceSource:
    source_id: str
    person: str = "Marc"
    freshness_seconds: int = 300

    def __post_init__(self) -> None:
        if not self.source_id or not self.person or self.freshness_seconds < 0:
            raise ValueError("presence source is incomplete")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "person": self.person,
            "freshness_seconds": self.freshness_seconds,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PresenceSource":
        return cls(
            str(value["source_id"]),
            str(value.get("person", "Marc")),
            int(value.get("freshness_seconds", 300)),
        )


SUPPORTED_ACTION_TYPES = frozenset(
    {
        "home_assistant.read_state",
        "home_assistant.light.on",
        "home_assistant.light.off",
        "home_assistant.turn_on",
        "home_assistant.turn_off",
        "home_assistant.thermostat.preset",
        "home_assistant.thermostat.target",
        "home_assistant.cover.position",
        "home_assistant.alarm.arm",
        "notification.reminder",
    }
)


@dataclass(frozen=True, slots=True)
class DepartureAction:
    action_type: str
    target: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
    requires_explicit_approval: bool = False

    def __post_init__(self) -> None:
        if self.action_type not in SUPPORTED_ACTION_TYPES:
            raise ValueError(f"unsupported Departure action: {self.action_type}")
        if not self.target:
            raise ValueError("Departure action target is required")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("Departure action parameters must be an object")
        if "target" in self.parameters and self.parameters["target"] != self.target:
            raise ValueError("Departure action parameters cannot override target")
        _bool(self.enabled, "Departure action enabled")
        _bool(self.requires_explicit_approval, "Departure action approval flag")

    def proposal_parameters(self) -> dict[str, Any]:
        return {**dict(self.parameters), "target": self.target}

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "target": self.target,
            "parameters": dict(self.parameters),
            "enabled": self.enabled,
            "requires_explicit_approval": self.requires_explicit_approval,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DepartureAction":
        return cls(
            action_type=str(value["action_type"]),
            target=str(value["target"]),
            parameters=dict(value.get("parameters", {})),
            enabled=_bool(value.get("enabled", True), "Departure action enabled"),
            requires_explicit_approval=_bool(
                value.get("requires_explicit_approval", False),
                "Departure action approval flag",
            ),
        )


@dataclass(frozen=True, slots=True)
class NotificationChannel:
    channel_id: str
    channel_type: str = "channel_bridge"
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.channel_id or not self.channel_type:
            raise ValueError("notification channel is incomplete")
        _bool(self.enabled, "notification channel enabled")

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "channel_type": self.channel_type,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NotificationChannel":
        return cls(
            str(value["channel_id"]),
            str(value.get("channel_type", "channel_bridge")),
            _bool(value.get("enabled", True), "notification channel enabled"),
        )


@dataclass(frozen=True, slots=True)
class DeparturePolicy:
    autonomy_level: int = 0
    execution_enabled: bool = False
    require_actual_departure: bool = True
    reminder_minutes_before: int = 15
    allow_alarm_arming: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.autonomy_level <= 4:
            raise ValueError("autonomy level must be between 0 and 4")
        if self.reminder_minutes_before < 0:
            raise ValueError("reminder lead time must not be negative")
        if self.allow_alarm_arming and self.autonomy_level < 2:
            raise ValueError("alarm arming policy requires explicit-approval autonomy")
        _bool(self.execution_enabled, "execution_enabled")
        _bool(self.require_actual_departure, "require_actual_departure")
        _bool(self.allow_alarm_arming, "allow_alarm_arming")

    def to_dict(self) -> dict[str, Any]:
        return {
            "autonomy_level": self.autonomy_level,
            "execution_enabled": self.execution_enabled,
            "require_actual_departure": self.require_actual_departure,
            "reminder_minutes_before": self.reminder_minutes_before,
            "allow_alarm_arming": self.allow_alarm_arming,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DeparturePolicy":
        return cls(
            autonomy_level=int(value.get("autonomy_level", 0)),
            execution_enabled=_bool(
                value.get("execution_enabled", False), "execution_enabled"
            ),
            require_actual_departure=_bool(
                value.get("require_actual_departure", True),
                "require_actual_departure",
            ),
            reminder_minutes_before=int(value.get("reminder_minutes_before", 15)),
            allow_alarm_arming=_bool(
                value.get("allow_alarm_arming", False), "allow_alarm_arming"
            ),
        )


@dataclass(frozen=True, slots=True)
class DepartureSetup:
    setup_id: str
    version: int
    calendar: CalendarSource
    destination: DestinationAssumptions
    preparation_buffer: PreparationBuffer
    traffic: TrafficSource
    weather: WeatherSource
    presence: PresenceSource
    supported_actions: tuple[DepartureAction, ...]
    notification: NotificationChannel
    policy: DeparturePolicy = field(default_factory=DeparturePolicy)
    created_at: str = field(default_factory=_now)
    status: str = "active"

    def __post_init__(self) -> None:
        if not self.setup_id or self.version < 1:
            raise ValueError("Departure setup identity and version are required")
        _parse(self.created_at)
        if not self.supported_actions:
            raise ValueError("Departure setup must declare supported actions")
        enabled = [item for item in self.supported_actions if item.enabled]
        if not enabled:
            raise ValueError("Departure setup must enable at least one action")
        if any(
            item.action_type == "home_assistant.alarm.arm"
            and not self.policy.allow_alarm_arming
            for item in enabled
        ):
            raise ValueError("alarm arming is not enabled by the Departure policy")

    @property
    def source_ids(self) -> tuple[str, ...]:
        return (
            self.calendar.source_id,
            self.traffic.source_id,
            self.weather.source_id,
            self.presence.source_id,
            self.preparation_buffer.source_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_id": self.setup_id,
            "version": self.version,
            "calendar": self.calendar.to_dict(),
            "destination": self.destination.to_dict(),
            "preparation_buffer": self.preparation_buffer.to_dict(),
            "traffic": self.traffic.to_dict(),
            "weather": self.weather.to_dict(),
            "presence": self.presence.to_dict(),
            "supported_actions": [item.to_dict() for item in self.supported_actions],
            "notification": self.notification.to_dict(),
            "policy": self.policy.to_dict(),
            "created_at": self.created_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DepartureSetup":
        allowed = {
            "setup_id",
            "version",
            "calendar",
            "destination",
            "preparation_buffer",
            "traffic",
            "weather",
            "presence",
            "supported_actions",
            "notification",
            "policy",
            "created_at",
            "status",
        }
        extra = set(value) - allowed
        if extra:
            raise ValueError(f"unknown Departure setup fields: {sorted(extra)}")
        return cls(
            setup_id=str(value["setup_id"]),
            version=int(value["version"]),
            calendar=CalendarSource.from_dict(value["calendar"]),
            destination=DestinationAssumptions.from_dict(value["destination"]),
            preparation_buffer=PreparationBuffer.from_dict(value["preparation_buffer"]),
            traffic=TrafficSource.from_dict(value["traffic"]),
            weather=WeatherSource.from_dict(value["weather"]),
            presence=PresenceSource.from_dict(value["presence"]),
            supported_actions=tuple(
                DepartureAction.from_dict(item) for item in value["supported_actions"]
            ),
            notification=NotificationChannel.from_dict(value["notification"]),
            policy=DeparturePolicy.from_dict(value.get("policy", {})),
            created_at=str(value.get("created_at", _now())),
            status=str(value.get("status", "active")),
        )


@dataclass(frozen=True, slots=True)
class DepartureObservations:
    departure_id: str
    now: str
    calendar_event_id: str
    calendar_event_start: str
    calendar_title: str
    calendar_status: str
    destination: str
    traffic_minutes: int | None
    weather_summary: str | None
    weather_precipitation: bool | None
    marc_present: bool | None
    present_people: tuple[str, ...]
    household_mode: str = "home"
    workspace: str = "local"
    location: str = "home"
    actual_departure_detected: bool = False
    return_home: bool = False
    manual_reversal: bool = False
    conflicting_plan_id: str | None = None
    source_status: Mapping[str, SourceStatus | str] = field(default_factory=dict)
    source_observed_at: Mapping[str, str] = field(default_factory=dict)
    source_confidence: Mapping[str, float] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.departure_id
            or not self.calendar_event_id
            or not self.calendar_event_start
        ):
            raise ValueError(
                "Departure observations require an event identity and start"
            )
        _parse(self.now)
        _parse(self.calendar_event_start)
        if self.traffic_minutes is not None and self.traffic_minutes < 0:
            raise ValueError("traffic minutes must not be negative")
        _optional_bool(self.weather_precipitation, "weather_precipitation")
        _optional_bool(self.marc_present, "marc_present")
        _bool(self.actual_departure_detected, "actual_departure_detected")
        _bool(self.return_home, "return_home")
        _bool(self.manual_reversal, "manual_reversal")
        for value in self.source_observed_at.values():
            _parse(value)
        for value in self.source_confidence.values():
            if not 0 <= float(value) <= 1:
                raise ValueError("source confidence must be between 0 and 1")

    @property
    def now_datetime(self) -> datetime:
        return _parse(self.now)

    @property
    def expected_departure_at(self) -> str:
        # Preparation is added by DepartureGuardian because it is setup data.
        return _timestamp(_parse(self.calendar_event_start))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DepartureObservations":
        return cls(
            departure_id=str(value["departure_id"]),
            now=str(value["now"]),
            calendar_event_id=str(value["calendar_event_id"]),
            calendar_event_start=str(value["calendar_event_start"]),
            calendar_title=str(value.get("calendar_title", "")),
            calendar_status=str(value.get("calendar_status", "confirmed")),
            destination=str(value.get("destination", "")),
            traffic_minutes=(
                int(value["traffic_minutes"])
                if value.get("traffic_minutes") is not None
                else None
            ),
            weather_summary=value.get("weather_summary"),
            weather_precipitation=_optional_bool(
                value.get("weather_precipitation"), "weather_precipitation"
            ),
            marc_present=_optional_bool(value.get("marc_present"), "marc_present"),
            present_people=tuple(str(item) for item in value.get("present_people", [])),
            household_mode=str(value.get("household_mode", "home")),
            workspace=str(value.get("workspace", "local")),
            location=str(value.get("location", "home")),
            actual_departure_detected=_bool(
                value.get("actual_departure_detected", False),
                "actual_departure_detected",
            ),
            return_home=_bool(
                value.get("return_home", False),
                "return_home",
            ),
            manual_reversal=_bool(
                value.get("manual_reversal", False),
                "manual_reversal",
            ),
            conflicting_plan_id=value.get("conflicting_plan_id"),
            source_status=dict(value.get("source_status", {})),
            source_observed_at=dict(value.get("source_observed_at", {})),
            source_confidence=dict(value.get("source_confidence", {})),
            evidence_ids=tuple(str(item) for item in value.get("evidence_ids", [])),
        )

    def status_for(self, source_id: str) -> SourceStatus:
        raw = self.source_status.get(source_id, SourceStatus.MISSING)
        return raw if isinstance(raw, SourceStatus) else SourceStatus(str(raw))

    def age_seconds(self, source_id: str) -> float | None:
        observed = self.source_observed_at.get(source_id)
        return (
            None
            if observed is None
            else max(0.0, (self.now_datetime - _parse(observed)).total_seconds())
        )

    def to_planning_observations(
        self, setup: DepartureSetup
    ) -> dict[str, ObservationSnapshot]:
        values: dict[str, tuple[bool, str, float, dict[str, Any]]] = {}
        for key, source_id, good, details in (
            (
                "calendar_active",
                setup.calendar.source_id,
                self.calendar_status.lower() not in {"canceled", "cancelled"},
                {"status": self.calendar_status},
            ),
            (
                "traffic_fresh",
                setup.traffic.source_id,
                self.traffic_minutes is not None,
                {"minutes": self.traffic_minutes},
            ),
            (
                "weather_fresh",
                setup.weather.source_id,
                self.weather_summary is not None,
                {"summary": self.weather_summary},
            ),
            (
                "presence_fresh",
                setup.presence.source_id,
                self.marc_present is not None,
                {"present": self.marc_present},
            ),
            (
                "at_home",
                setup.presence.source_id,
                self.marc_present is True,
                {"present": self.marc_present},
            ),
            (
                "calendar_canceled",
                setup.calendar.source_id,
                self.calendar_status.lower() in {"canceled", "cancelled"},
                {},
            ),
            ("return_home", setup.presence.source_id, self.return_home, {}),
            ("manual_reversal", "departure-control", self.manual_reversal, {}),
            (
                "household_stable",
                setup.presence.source_id,
                not bool(self.conflicting_plan_id),
                {"conflicting_plan_id": self.conflicting_plan_id},
            ),
            (
                "actual_departure",
                setup.presence.source_id,
                self.actual_departure_detected,
                {},
            ),
        ):
            observed_at = self.source_observed_at.get(source_id, self.now)
            values[key] = (
                good,
                observed_at,
                float(self.source_confidence.get(source_id, 1.0)),
                {**details, "source_id": source_id},
            )
        for action_key in setup.supported_actions:
            if action_key.enabled:
                key = f"verified:{action_key.target}"
                values.setdefault(
                    key, (True, self.now, 1.0, {"target": action_key.target})
                )
        return {
            key: ObservationSnapshot(
                key=key,
                observed_at=observed_at,
                satisfied=satisfied,
                confidence=confidence,
                source_id=details.get("source_id", "departure-control"),
                details=details,
            )
            for key, (satisfied, observed_at, confidence, details) in values.items()
        }

    def source_warnings(self, setup: DepartureSetup) -> tuple[str, ...]:
        required = (
            (setup.calendar.source_id, setup.calendar.freshness_seconds),
            (setup.traffic.source_id, setup.traffic.freshness_seconds),
            (setup.weather.source_id, setup.weather.freshness_seconds),
            (setup.presence.source_id, setup.presence.freshness_seconds),
        )
        warnings: list[str] = []
        for source_id, freshness in required:
            status = self.status_for(source_id)
            age = self.age_seconds(source_id)
            if status is not SourceStatus.FRESH:
                warnings.append(f"{source_id}: {status.value}")
            elif age is None or age > freshness:
                warnings.append(f"{source_id}: stale")
        if self.traffic_minutes is None:
            warnings.append("traffic estimate unavailable")
        if self.weather_summary is None:
            warnings.append("weather condition unavailable")
        if self.marc_present is None:
            warnings.append("presence unavailable")
        return tuple(dict.fromkeys(warnings))

    def to_dict(self) -> dict[str, Any]:
        return {
            "departure_id": self.departure_id,
            "now": self.now,
            "calendar_event_id": self.calendar_event_id,
            "calendar_event_start": self.calendar_event_start,
            "calendar_title": self.calendar_title,
            "calendar_status": self.calendar_status,
            "destination": self.destination,
            "traffic_minutes": self.traffic_minutes,
            "weather_summary": self.weather_summary,
            "weather_precipitation": self.weather_precipitation,
            "marc_present": self.marc_present,
            "present_people": list(self.present_people),
            "household_mode": self.household_mode,
            "workspace": self.workspace,
            "location": self.location,
            "actual_departure_detected": self.actual_departure_detected,
            "return_home": self.return_home,
            "manual_reversal": self.manual_reversal,
            "conflicting_plan_id": self.conflicting_plan_id,
            "source_status": {
                key: str(value.value if isinstance(value, SourceStatus) else value)
                for key, value in self.source_status.items()
            },
            "source_observed_at": dict(self.source_observed_at),
            "source_confidence": dict(self.source_confidence),
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class WhyNowBrief:
    departure_id: str
    active: bool
    why_active: str
    expected_departure_at: str
    calendar_evidence: tuple[str, ...]
    traffic_weather_evidence: tuple[str, ...]
    presence_evidence: tuple[str, ...]
    preparation_assumptions: tuple[str, ...]
    proposed_actions: tuple[dict[str, Any], ...]
    expected_effects: tuple[str, ...]
    exact_authority_required: tuple[str, ...]
    expiration_conditions: tuple[str, ...]
    cancellation_conditions: tuple[str, ...]
    uncertainty_warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "departure_id": self.departure_id,
            "active": self.active,
            "why_active": self.why_active,
            "expected_departure_at": self.expected_departure_at,
            "calendar_evidence": list(self.calendar_evidence),
            "traffic_weather_evidence": list(self.traffic_weather_evidence),
            "presence_evidence": list(self.presence_evidence),
            "preparation_assumptions": list(self.preparation_assumptions),
            "proposed_actions": [dict(item) for item in self.proposed_actions],
            "expected_effects": list(self.expected_effects),
            "exact_authority_required": list(self.exact_authority_required),
            "expiration_conditions": list(self.expiration_conditions),
            "cancellation_conditions": list(self.cancellation_conditions),
            "uncertainty_warnings": list(self.uncertainty_warnings),
        }

    @property
    def text(self) -> str:
        status = "Departure is active" if self.active else "Departure is not ready"
        warnings = (
            f" Warnings: {', '.join(self.uncertainty_warnings)}."
            if self.uncertainty_warnings
            else ""
        )
        return f"{status}: {self.why_active}. Leave by {self.expected_departure_at}. Proposed: {', '.join(item.get('action_type', '') for item in self.proposed_actions)}.{warnings}"


@dataclass(frozen=True, slots=True)
class RecalculationResult:
    departure_id: str
    status: str
    plan_id: str | None
    plan_version: int | None
    reasons: tuple[str, ...]
    brief: WhyNowBrief
    validation: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "departure_id": self.departure_id,
            "status": self.status,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "reasons": list(self.reasons),
            "brief": self.brief.to_dict(),
            "validation": dict(self.validation) if self.validation else None,
        }


__all__ = [
    "CalendarSource",
    "DepartureAction",
    "DepartureObservations",
    "DeparturePolicy",
    "DepartureSetup",
    "DestinationAssumptions",
    "NotificationChannel",
    "PreparationBuffer",
    "PresenceSource",
    "RecalculationResult",
    "SourceStatus",
    "TrafficSource",
    "WeatherSource",
    "WhyNowBrief",
    "SUPPORTED_ACTION_TYPES",
]
