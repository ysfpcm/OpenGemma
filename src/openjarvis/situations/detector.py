"""Deterministic-first Departure detection with no authority or executor path."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from openjarvis.cognition import Situation

from .journal import DurableEventJournal
from .models import DurableEvent, SituationStatus, utc_iso

CALENDAR_EVENT = "calendar.appointment"
TRAFFIC_EVENT = "traffic.estimate"
PRESENCE_EVENT = "presence.home"
SYSTEM_CLOCK_EVENT = "system.clock"
_EVALUATION_EVENT_TYPES = {
    CALENDAR_EVENT,
    TRAFFIC_EVENT,
    PRESENCE_EVENT,
    SYSTEM_CLOCK_EVENT,
}


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("event times must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class DepartureDetectorConfig:
    presence_freshness_seconds: int = 300
    traffic_freshness_seconds: int = 900
    calendar_freshness_seconds: int = 86_400
    default_buffer_minutes: int = 10
    person: str = "Marc"
    home: str = "home"


@dataclass(frozen=True)
class DetectionReport:
    detector: str
    evaluation_key: str
    situation: Situation | None
    reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    evaluated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "evaluation_key": self.evaluation_key,
            "situation": self.situation.to_dict() if self.situation else None,
            "reasons": list(self.reasons),
            "evidence_ids": list(self.evidence_ids),
            "evaluated_at": self.evaluated_at,
        }


class ShadowSituationDetector:
    """Replayable detector. It writes evidence and situations only."""

    name = "departure-shadow-v1"

    def __init__(
        self,
        journal: DurableEventJournal,
        *,
        config: DepartureDetectorConfig | None = None,
    ) -> None:
        self.journal = journal
        self.config = config or DepartureDetectorConfig()

    def process_available(
        self,
        *,
        worker_id: str = "shadow-detector",
        consumer: str = "shadow-situations",
        now: float | None = None,
        lease_seconds: float = 30.0,
        max_attempts: int = 3,
    ) -> list[DetectionReport]:
        deliveries = self.journal.claim(
            consumer,
            worker_id,
            now=now,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
            limit=1000,
        )
        reports: list[DetectionReport] = []
        for delivery in deliveries:
            try:
                report = self.replay()
            except Exception as exc:
                self.journal.nack(
                    consumer,
                    delivery.event.event_id,
                    worker_id,
                    f"{type(exc).__name__}: {exc}",
                    now=now,
                    max_attempts=max_attempts,
                )
                raise
            self.journal.ack(consumer, delivery.event.event_id, worker_id, now=now)
            reports.append(report)
        return reports

    def replay(self) -> DetectionReport:
        events = self.journal.events()
        if not events:
            report = DetectionReport(
                detector=self.name,
                evaluation_key=f"{self.name}:empty",
                situation=None,
                reasons=("no-events",),
                evidence_ids=(),
                evaluated_at=utc_iso(),
            )
            self.journal.save_evaluation(report.evaluation_key, report.to_dict())
            return report
        evaluation_events = [
            event for event in events if event.event_type in _EVALUATION_EVENT_TYPES
        ]
        evaluation_time = max(
            (_parse(event.observed_at) for event in evaluation_events or events),
        )
        reports: list[DetectionReport] = []
        appointments: dict[str, list[DurableEvent]] = {}
        for event in events:
            if event.event_type != CALENDAR_EVENT:
                continue
            appointment_id = str(event.payload.get("appointment_id", "")).strip()
            if appointment_id:
                appointments.setdefault(appointment_id, []).append(event)
        for appointment_id in sorted(appointments):
            reports.append(
                self._evaluate_appointment(
                    appointment_id,
                    appointments[appointment_id],
                    events,
                    evaluation_time,
                )
            )
        if not reports:
            report = DetectionReport(
                detector=self.name,
                evaluation_key=f"{self.name}:no-appointments",
                situation=None,
                reasons=("missing-calendar-evidence",),
                evidence_ids=(),
                evaluated_at=utc_iso(evaluation_time),
            )
            self.journal.save_evaluation(report.evaluation_key, report.to_dict())
            return report
        # The Phase 4 fixture contains one appointment. A deterministic first
        # report is useful to callers while all appointment reports are still
        # persisted as separate evaluations.
        return reports[0]

    def _evaluate_appointment(
        self,
        appointment_id: str,
        calendar_events: list[DurableEvent],
        events: list[DurableEvent],
        evaluation_time: datetime,
    ) -> DetectionReport:
        ordered = sorted(calendar_events, key=lambda event: event.event_id)
        latest_time = max(_parse(event.observed_at) for event in ordered)
        latest_events = [
            event for event in ordered if _parse(event.observed_at) == latest_time
        ]
        latest = latest_events[-1]
        payload = latest.payload
        key = f"{self.name}:{appointment_id}"
        reasons: list[str] = []
        evidence: list[str] = [event.event_id for event in latest_events]
        starts_at = payload.get("starts_at")
        status = str(latest.payload.get("status", "")).strip().lower()
        if status == "canceled":
            return self._closed(
                appointment_id,
                key,
                latest,
                evaluation_time,
                evidence,
                ("appointment-canceled",),
            )
        if status != "confirmed":
            return self._record_blocked(
                appointment_id,
                key,
                evaluation_time,
                evidence,
                ["calendar-not-confirmed"],
            )
        try:
            appointment_start = _parse(str(starts_at))
        except (TypeError, ValueError):
            return self._record_blocked(
                appointment_id,
                key,
                evaluation_time,
                evidence,
                ["invalid-calendar-time"],
            )

        route_id = str(payload.get("route_id", "")).strip()
        if not route_id:
            reasons.append("invalid-calendar-route")
        traffic = self._latest_traffic(events, route_id)
        presence = self._latest_presence(events)
        if traffic is None:
            reasons.append("missing-traffic-evidence")
        if presence is None:
            reasons.append("missing-presence-evidence")
        if traffic is not None:
            evidence.append(traffic.event_id)
        if presence is not None:
            evidence.extend(item.event_id for item in presence[1])
        if (
            presence is not None
            and len(
                {
                    (
                        bool(item.payload.get("present")),
                        str(item.payload.get("location", "")).strip().lower(),
                    )
                    for item in presence[1]
                }
            )
            > 1
        ):
            reasons.append("contradictory-presence")
        if presence is not None and (
            not bool(presence[0].payload.get("present"))
            or str(presence[0].payload.get("location", "")).strip().lower()
            != self.config.home.lower()
        ):
            reasons.append("marc-not-at-home")
        if (
            _age_seconds(evaluation_time, latest_time)
            > self.config.calendar_freshness_seconds
        ):
            reasons.append("stale-calendar-evidence")
        if (
            traffic is not None
            and _age_seconds(evaluation_time, _parse(traffic.observed_at))
            > self.config.traffic_freshness_seconds
        ):
            reasons.append("stale-traffic-evidence")
        if (
            presence is not None
            and _age_seconds(evaluation_time, _parse(presence[0].observed_at))
            > self.config.presence_freshness_seconds
        ):
            reasons.append("stale-presence-evidence")

        travel_minutes = (
            _non_negative_minutes(traffic.payload.get("travel_minutes"))
            if traffic is not None
            else None
        )
        buffer_minutes = (
            _non_negative_minutes(
                traffic.payload.get(
                    "buffer_minutes", self.config.default_buffer_minutes
                )
            )
            if traffic is not None
            else None
        )
        if traffic is not None and travel_minutes is None:
            reasons.append("invalid-traffic-travel-time")
        if traffic is not None and buffer_minutes is None:
            reasons.append("invalid-traffic-buffer")
        prep_minutes = _non_negative_minutes(payload.get("prep_minutes", 0))
        if prep_minutes is None:
            reasons.append("invalid-calendar-preparation")
        if traffic is None or presence is None or reasons:
            return self._record_blocked(
                appointment_id, key, evaluation_time, evidence, reasons
            )

        assert travel_minutes is not None
        assert buffer_minutes is not None
        assert prep_minutes is not None
        departure_at = appointment_start - timedelta(
            minutes=prep_minutes + travel_minutes + buffer_minutes
        )
        if evaluation_time < departure_at:
            reasons.append("departure-threshold-not-reached")
            report = self._blocked(key, evaluation_time, evidence, reasons)
            self.journal.save_evaluation(key, report.to_dict())
            return report
        situation = self._make_situation(
            appointment_id,
            status=SituationStatus.ACTIVE.value,
            confidence=0.95,
            uncertainty=(),
            evidence_ids=sorted(set(evidence)),
            evaluation_time=evaluation_time,
            appointment_start=appointment_start,
            events=[latest, traffic, presence[0]],
            valid_from=departure_at,
        )
        self.journal.save_situation(situation, idempotency_key=key)
        report = DetectionReport(
            self.name,
            key,
            situation,
            (),
            tuple(sorted(set(evidence))),
            utc_iso(evaluation_time),
        )
        self.journal.save_evaluation(key, report.to_dict())
        return report

    def _latest_traffic(
        self, events: list[DurableEvent], route_id: Any
    ) -> DurableEvent | None:
        if not isinstance(route_id, str) or not route_id.strip():
            return None
        candidates = [
            event
            for event in events
            if event.event_type == TRAFFIC_EVENT
            and event.payload.get("route_id") == route_id
        ]
        return max(
            candidates,
            key=lambda event: (_parse(event.observed_at), event.event_id),
            default=None,
        )

    def _latest_presence(
        self, events: list[DurableEvent]
    ) -> tuple[DurableEvent, list[DurableEvent]] | None:
        candidates = [
            event
            for event in events
            if event.event_type == PRESENCE_EVENT
            and str(event.payload.get("person", self.config.person)).lower()
            == self.config.person.lower()
            and isinstance(event.payload.get("present"), bool)
        ]
        if not candidates:
            return None
        latest_time = max(_parse(event.observed_at) for event in candidates)
        latest = sorted(
            [event for event in candidates if _parse(event.observed_at) == latest_time],
            key=lambda event: event.event_id,
        )
        return latest[-1], latest

    def _existing(self, appointment_id: str) -> Situation | None:
        key = f"{self.name}:{appointment_id}"
        matches = [
            item
            for item in self.journal.situations()
            if item.provenance.get("idempotency_key") == key
        ]
        return matches[0] if matches else None

    def _blocked(
        self,
        key: str,
        evaluation_time: datetime,
        evidence: list[str],
        reasons: list[str],
    ) -> DetectionReport:
        return DetectionReport(
            self.name,
            key,
            None,
            tuple(sorted(set(reasons or ["required-evidence-unavailable"]))),
            tuple(sorted(set(evidence))),
            utc_iso(evaluation_time),
        )

    def _record_blocked(
        self,
        appointment_id: str,
        key: str,
        evaluation_time: datetime,
        evidence: list[str],
        reasons: list[str],
    ) -> DetectionReport:
        unique_reasons = sorted(set(reasons or ["required-evidence-unavailable"]))
        existing = self._existing(appointment_id)
        situation: Situation | None = None
        if existing is not None and existing.status == SituationStatus.ACTIVE.value:
            body = existing.to_dict()
            body.update(
                {
                    "status": SituationStatus.UNCERTAIN.value,
                    "confidence": 0.45,
                    "uncertainty": sorted(
                        set(existing.uncertainty).union(unique_reasons)
                    ),
                    "evidence_ids": sorted(set(existing.evidence_ids).union(evidence)),
                }
            )
            situation = Situation.from_dict(body)
            self.journal.save_situation(situation, idempotency_key=key)
        report = DetectionReport(
            self.name,
            key,
            situation,
            tuple(unique_reasons),
            tuple(sorted(set(evidence))),
            utc_iso(evaluation_time),
        )
        self.journal.save_evaluation(key, report.to_dict())
        return report

    def _closed(
        self,
        appointment_id: str,
        key: str,
        event: DurableEvent,
        evaluation_time: datetime,
        evidence: list[str],
        reasons: tuple[str, ...],
    ) -> DetectionReport:
        existing = self._existing(appointment_id)
        if existing is None:
            report = self._blocked(key, evaluation_time, evidence, list(reasons))
            self.journal.save_evaluation(key, report.to_dict())
            return report
        body = existing.to_dict()
        body.update(
            {
                "status": SituationStatus.CLOSED.value,
                "valid_until": utc_iso(evaluation_time),
                "evidence_ids": sorted(set(existing.evidence_ids + [event.event_id])),
                "uncertainty": list(reasons),
            }
        )
        # Constructing through the shared contract parser keeps the envelope
        # behavior identical to a persisted/replayed record.
        situation = Situation.from_dict(body)
        self.journal.save_situation(situation, idempotency_key=key)
        report = DetectionReport(
            self.name,
            key,
            situation,
            reasons,
            tuple(situation.evidence_ids),
            utc_iso(evaluation_time),
        )
        self.journal.save_evaluation(key, report.to_dict())
        return report

    def _make_situation(
        self,
        appointment_id: str,
        *,
        status: str,
        confidence: float,
        uncertainty: tuple[str, ...] | list[str],
        evidence_ids: list[str],
        evaluation_time: datetime,
        appointment_start: datetime,
        events: list[DurableEvent | None],
        valid_from: datetime | None = None,
    ) -> Situation:
        relevant = [event for event in events if event is not None]
        created_at = min(
            (_parse(event.observed_at) for event in relevant), default=evaluation_time
        )
        sources = [
            {
                "source": event.source,
                "event_id": event.event_id,
                "observed_at": event.observed_at,
            }
            for event in sorted(relevant, key=lambda item: item.event_id)
        ]
        stable_id = hashlib.sha256(appointment_id.encode("utf-8")).hexdigest()[:24]
        key = f"{self.name}:{appointment_id}"
        return Situation(
            id=f"sit_departure_{stable_id}",
            situation_type="Departure",
            status=status,
            evidence_ids=sorted(set(evidence_ids)),
            confidence=confidence,
            uncertainty=list(uncertainty),
            created_at=utc_iso(created_at),
            valid_from=utc_iso(valid_from or appointment_start),
            provenance={
                "component": "phase4-shadow-detector",
                "mode": "shadow",
                "idempotency_key": key,
            },
            source_provenance={"sources": sources},
            sensitivity_labels=sorted(
                {label for event in relevant for label in event.sensitivity_labels}
            ),
            taint_labels=sorted(
                {label for event in relevant for label in event.taint_labels}
            ),
        )


def _age_seconds(now: datetime, observed: datetime) -> float:
    return max(0.0, (now - observed).total_seconds())


def _non_negative_minutes(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)) or value < 0:
        return None
    return float(value)


__all__ = ["DetectionReport", "DepartureDetectorConfig", "ShadowSituationDetector"]
