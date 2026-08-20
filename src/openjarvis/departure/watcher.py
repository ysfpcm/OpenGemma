"""Durable, one-shot departure watchers for connected Home Assistant events.

This module is deliberately separate from the calendar-oriented Phase 7
Departure Guardian.  A watcher is an evidence pipeline, not an identity
detector: a front-door camera event can establish activity at the door, but it
cannot establish that Marc personally left the house.
"""

from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from openjarvis.cognition import ActionProposal
from openjarvis.context import ContextBuilder, ContextRequest, ContextStore
from openjarvis.core.events import Event, EventBus, EventType

_CAMERA_EVENT_TYPES = frozenset(
    {"camera_motion", "person_detected", "sound_detected", "doorbell_chime"}
)
_SUBSCRIBED_EVENT_TYPES = (
    EventType.CAMERA_MOTION,
    EventType.PERSON_DETECTED,
    EventType.SOUND_DETECTED,
    EventType.DOORBELL_CHIME,
)
_WATCHER_STATUSES = frozenset(
    {"ACTIVE", "TRIGGERED", "COMPLETED", "EXPIRED", "CANCELED", "NEEDS_ATTENTION"}
)
_TARGET_NAME = "Living Room Lamp"
_TARGET_ENTITY_ID = "light.living_room_lamp"
_DEFAULT_MAX_EVENT_AGE_SECONDS = 90
_DEFAULT_CONFLICT_WINDOW_SECONDS = 90
_FUTURE_TOLERANCE_SECONDS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp(value: datetime | str) -> str:
    return _parse(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _truthy_state(value: Any) -> bool | None:
    normalized = _text(value)
    if normalized in {"on", "home", "occupied", "present", "active", "detected", "true"}:
        return True
    if normalized in {
        "off",
        "away",
        "not home",
        "not present",
        "clear",
        "inactive",
        "false",
        "idle",
    }:
        return False
    if normalized in {"", "unknown", "unavailable", "none", "null"}:
        return None
    return None


def _json_safe(value: Any) -> Any:
    """Make a small, deterministic evidence payload without leaking secrets."""
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class DepartureWatcherService:
    """Persisted one-time watcher coordinator.

    The service keeps only subscriptions and a lock in memory.  Definitions,
    active state, decisions, evidence, authorizations, and device states are
    all read from and written to ``ContextStore``.  This makes restart reload
    deterministic and prevents a process-local watcher from silently being
    lost.
    """

    def __init__(
        self,
        store: ContextStore,
        *,
        guardian: Any | None = None,
        home_assistant_bridge: Any | None = None,
        state_reader: Any | None = None,
        bus: EventBus | None = None,
        clock: Callable[[], datetime] = _now,
        monitor_available: bool | None = None,
    ) -> None:
        self.store = store
        self.builder = ContextBuilder(store)
        self.guardian = guardian
        self.home_assistant_bridge = home_assistant_bridge
        self.state_reader = state_reader
        self.bus = bus
        self.clock = clock
        self._monitor_available_override = monitor_available
        self._lock = threading.RLock()
        self._subscribed = False
        self._status_subscribed = False
        self._active_ids: set[str] = set()

    @property
    def monitor_available(self) -> bool:
        if self._monitor_available_override is not None:
            return self._monitor_available_override
        bridge = self.home_assistant_bridge
        if bridge is None:
            return False
        configured = getattr(bridge, "is_configured", False)
        return bool(configured() if callable(configured) else configured)

    def start(self) -> None:
        """Reload durable active watchers and subscribe to new HA events."""
        with self._lock:
            self._expire_due_watchers()
            active = self.store.list_active_departure_watchers(now=self.clock())
            self._active_ids = {item["watcher_id"] for item in active}
            if not self.monitor_available:
                for item in active:
                    self._needs_attention(
                        item["watcher_id"],
                        "Home Assistant bridge is unavailable; watcher was not armed.",
                    )
                self._active_ids.clear()
            if self.bus is not None and not self._subscribed:
                for event_type in _SUBSCRIBED_EVENT_TYPES:
                    self.bus.subscribe(event_type, self._on_bus_event)
                self._subscribed = True
            if self.bus is not None and not self._status_subscribed:
                self.bus.subscribe(EventType.HOME_ASSISTANT_STATUS, self._on_status_event)
                self._status_subscribed = True

    def stop(self) -> None:
        with self._lock:
            if self.bus is not None and self._subscribed:
                for event_type in _SUBSCRIBED_EVENT_TYPES:
                    self.bus.unsubscribe(event_type, self._on_bus_event)
            if self.bus is not None and self._status_subscribed:
                self.bus.unsubscribe(EventType.HOME_ASSISTANT_STATUS, self._on_status_event)
            self._subscribed = False
            self._status_subscribed = False
            self._active_ids.clear()

    def create_watcher(
        self,
        *,
        conversation_id: str,
        duration_seconds: int = 15 * 60,
        mode: str = "simulation",
        watcher_id: str | None = None,
        now: datetime | str | None = None,
        live_approved: bool = False,
    ) -> dict[str, Any]:
        """Create a bounded watcher, defaulting to simulation mode."""
        if duration_seconds <= 0 or duration_seconds > 15 * 60:
            raise ValueError("one-time departure watchers may run for at most 15 minutes")
        if mode not in {"simulation", "live"}:
            raise ValueError("mode must be simulation or live")
        if mode == "live" and not live_approved:
            raise PermissionError("live mode requires an explicit Marc approval")
        armed = _parse(now or self.clock())
        expires = armed + timedelta(seconds=duration_seconds)
        watcher_id = watcher_id or f"departure-watcher-{uuid.uuid4().hex}"
        trigger = {
            "event_types": sorted(_CAMERA_EVENT_TYPES),
            "source_key": "home_assistant",
            "entity_terms": ["front door", "front_door", "doorbell"],
            "received_after_armed_at": True,
            "max_event_age_seconds": _DEFAULT_MAX_EVENT_AGE_SECONDS,
            "future_tolerance_seconds": _FUTURE_TOLERANCE_SECONDS,
            "conflict_window_seconds": _DEFAULT_CONFLICT_WINDOW_SECONDS,
        }
        action = {
            "action_type": "home_assistant.turn_off",
            "target_name": _TARGET_NAME,
            # This watcher is intentionally bound to the entity ID Marc
            # approved.  A friendly-name lookup is useful for ordinary chat,
            # but it must never broaden an unattended action.
            "target_entity_id": _TARGET_ENTITY_ID,
            "expected_state": "off",
            "allowed_entities": [_TARGET_ENTITY_ID],
            "scope": "only Living Room Lamp; no other device domains or entities",
        }
        permissions = {
            "context_database": "required",
            "home_assistant_read": "required for camera, occupancy, and lamp state",
            "home_assistant_bridge": "required for future event monitoring",
            "guardian_capability": "home.assistant.write",
            "guardian_action": "home_assistant.turn_off",
            "guardian_target": _TARGET_ENTITY_ID,
            "explicit_live_approval": "required for live execution; absent in simulation",
            "disallowed": [
                "locks",
                "garage",
                "alarms",
                "thermostat",
                "other lights",
                "security devices",
            ],
        }
        status = "ACTIVE" if self.monitor_available else "NEEDS_ATTENTION"
        reason = "" if status == "ACTIVE" else (
            "Home Assistant bridge is unavailable; watcher cannot monitor future events."
        )
        self.store.create_departure_watcher(
            watcher_id=watcher_id,
            conversation_id=conversation_id,
            created_at=armed,
            armed_at=armed,
            expires_at=expires,
            status=status,
            mode=mode,
            trigger=trigger,
            action=action,
            permissions=permissions,
            reason=reason,
        )
        if live_approved:
            self.store.record_departure_watcher_authorization(
                authorization_id=f"approval:{watcher_id}",
                watcher_id=watcher_id,
                action_id=None,
                decision="explicit_live_approval",
                authority="Marc",
                required={"live_approval": True},
                scope={"watcher_id": watcher_id},
                details={"mode": "live"},
            )
            if status == "ACTIVE" and not self._grant_live_authority(
                watcher_id, expires_at=expires
            ):
                self._needs_attention(
                    watcher_id,
                    "Guardian could not create the exact scoped live grant; no action was armed.",
                )
        if status == "ACTIVE":
            self._active_ids.add(watcher_id)
        return self.inspect(watcher_id)

    def approve_live(self, watcher_id: str, *, authority: str = "Marc") -> dict[str, Any]:
        """Explicitly switch one watcher from simulation to live mode."""
        if authority != "Marc":
            raise PermissionError("only Marc may approve a live watcher action")
        with self._lock:
            record = self.store.get_departure_watcher(watcher_id)
            if record["status"] != "ACTIVE":
                raise ValueError("only an active watcher can receive live approval")
            if not self.monitor_available:
                self._needs_attention(
                    watcher_id,
                    "Home Assistant bridge is unavailable; live approval was not armed.",
                )
                return self.inspect(watcher_id)
            self.store.set_departure_watcher_mode(watcher_id, "live")
            self.store.record_departure_watcher_authorization(
                authorization_id=f"approval:{watcher_id}",
                watcher_id=watcher_id,
                action_id=None,
                decision="explicit_live_approval",
                authority=authority,
                required={"live_approval": True},
                scope={
                    "action_type": "home_assistant.turn_off",
                    "target": _TARGET_NAME,
                },
                details={"mode": "live", "approved_at": _timestamp(self.clock())},
            )
            record = self.store.get_departure_watcher(watcher_id)
            if not self._grant_live_authority(
                watcher_id, expires_at=_parse(record["expires_at"])
            ):
                self._needs_attention(
                    watcher_id,
                    "Guardian could not create the exact scoped live grant; no action was armed.",
                )
            return self.inspect(watcher_id)

    def cancel(self, watcher_id: str, reason: str = "Marc canceled watcher") -> dict[str, Any]:
        with self._lock:
            self.store.update_departure_watcher(
                watcher_id,
                status="CANCELED",
                reason=reason,
                cancelled_at=self.clock(),
            )
            self._revoke_live_authority(watcher_id, reason=reason)
            self._active_ids.discard(watcher_id)
            return self.inspect(watcher_id)

    def _grant_live_authority(self, watcher_id: str, *, expires_at: datetime) -> bool:
        """Create the one exact Guardian grant required by a live watcher.

        A durable watcher approval is not itself Guardian authority.  Keeping
        this grant creation beside the approval boundary prevents a watcher
        from looking armed while failing later with "no live grant covers this
        exact action".
        """
        if self.guardian is None:
            return False
        seconds = max(1, int((expires_at - self.clock()).total_seconds()))
        grant_id = f"departure-watcher-grant:{watcher_id}"
        scope = {
            "action_type": "home_assistant.turn_off",
            "target": _TARGET_ENTITY_ID,
        }
        try:
            self.guardian.grant(
                grant_id=grant_id,
                session_id=watcher_id,
                capability="home.assistant.write",
                scope=scope,
                expires_in_seconds=seconds,
            )
        except Exception:
            return False
        self.store.record_departure_watcher_authorization(
            authorization_id=f"guardian-grant:{watcher_id}",
            watcher_id=watcher_id,
            action_id=None,
            decision="scoped_live_grant_created",
            authority="Guardian",
            required={"capability": "home.assistant.write"},
            scope={"grant_id": grant_id, **scope},
            details={"expires_at": _timestamp(expires_at)},
        )
        return True

    def _revoke_live_authority(self, watcher_id: str, *, reason: str) -> None:
        if self.guardian is None:
            return
        try:
            self.guardian.revoke(watcher_id, reason=reason)
        except Exception:
            # Revocation failure must not overwrite a more specific durable
            # watcher failure. Guardian will still enforce the grant expiry.
            pass

    def inspect(self, watcher_id: str) -> dict[str, Any]:
        record = self.store.get_departure_watcher(watcher_id)
        evidence = record["evidence"]
        action_records = record["authorizations"]
        device_states = record["device_states"]
        verification = next(
            (item for item in reversed(device_states) if item["phase"] == "verification"),
            None,
        )
        post_state = next(
            (item for item in reversed(device_states) if item["phase"] == "post_action"),
            None,
        )
        simulation = record["mode"] == "simulation"
        accepted = any(
            item["evidence_kind"] == "departure_candidate"
            and item["classification"] == "inferred"
            for item in evidence
        )
        if simulation and accepted:
            action_attempted: Any = {
                "mode": "simulation",
                "status": "prepared",
                "executed": False,
                "reason": "simulation mode never sends a Home Assistant write",
            }
        elif action_records:
            action_attempted = {
                "mode": record["mode"],
                "status": record["status"],
                "executed": bool(record["fire_count"] and not simulation),
                "authorization": action_records[-1],
            }
        else:
            action_attempted = {"mode": record["mode"], "status": "not_attempted"}
        if simulation and accepted:
            final_state: Any = {
                "status": "not_changed_by_simulation",
                "real_world_state": "not_claimed",
            }
        elif verification is not None:
            final_state = verification["state"]
        elif post_state is not None:
            final_state = post_state["state"]
        else:
            final_state = "unknown"
        rollback = record["rollbacks"][-1] if record["rollbacks"] else {
            "method": "turn_on Living Room Lamp only if a live action changed a previously-on lamp",
            "status": "available_after_verified_live_effect",
        }
        return {
            **record,
            "understood": (
                "Watch only for a new front-door or doorbell-camera event received "
                "after armed_at. Treat it as door activity, never as proof that Marc left."
            ),
            "trigger_condition": record["trigger"],
            "expiration_time": record["expires_at"],
            "permissions_required": record["permissions"],
            "current_status": record["status"],
            "observed_evidence": evidence,
            "departure_observed": False,
            "departure_inferred": accepted,
            "departure_label": (
                "inferred-consistent-only" if accepted else "not-observed"
            ),
            "action_attempted": action_attempted,
            "independent_verification": (
                verification
                if verification is not None
                else {
                    "status": "not_available_in_simulation"
                    if simulation
                    else "not_performed"
                }
            ),
            "final_state": final_state,
            "rollback_method": rollback,
        }

    def handle_context_event(
        self, context_event_id: int, *, home_assistant_event_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Evaluate one already-ingested HA event against active watchers."""
        with self._lock:
            self._expire_due_watchers()
            try:
                event = self.store.get_event(context_event_id)
            except Exception as exc:  # fail closed when evidence lookup fails
                self._mark_all_needs_attention(
                    f"durable context event {context_event_id} is unavailable: {type(exc).__name__}"
                )
                return []
            home_assistant_event_id = home_assistant_event_id or event.external_event_id
            results: list[dict[str, Any]] = []
            for watcher in self.store.list_active_departure_watchers(now=self.clock()):
                result = self._evaluate_one(
                    watcher,
                    event,
                    home_assistant_event_id=home_assistant_event_id,
                )
                results.append(result)
            return results

    def _on_bus_event(self, event: Event) -> None:
        context_event_id = event.data.get("context_event_id")
        try:
            event_id = int(context_event_id)
        except (TypeError, ValueError):
            self._mark_all_needs_attention(
                "Home Assistant event did not include a durable context event ID."
            )
            return
        self.handle_context_event(
            event_id,
            home_assistant_event_id=(
                str(event.data["source_event_id"])
                if event.data.get("source_event_id")
                else None
            ),
        )

    def _on_status_event(self, event: Event) -> None:
        status = str(event.data.get("status", "")).lower()
        if status in {"offline", "degraded"}:
            self._mark_all_needs_attention(
                f"Home Assistant bridge reported {status}; live watcher processing is blocked."
            )

    def _evaluate_one(
        self,
        watcher: Mapping[str, Any],
        event: Any,
        *,
        home_assistant_event_id: str | None,
    ) -> dict[str, Any]:
        watcher_id = str(watcher["watcher_id"])
        trigger = dict(watcher["trigger"])
        try:
            if event.source_key != trigger["source_key"]:
                return self._decision(watcher, event, "IGNORED", "event source is not Home Assistant")
            if event.event_type not in set(trigger["event_types"]):
                return self._decision(watcher, event, "IGNORED", "event is not a camera activity event")
            searchable = _text(
                f"{event.external_entity_id or ''} {event.entity_name or ''} {event.entity_type or ''}"
            )
            if not any(_text(term) in searchable for term in trigger["entity_terms"]):
                return self._decision(watcher, event, "IGNORED", "event is not from the front-door or doorbell camera")
            if event.received_at <= watcher["armed_at"]:
                return self._decision(watcher, event, "REJECTED", "event was received before watcher armed_at", classification="stale")

            now = self.clock()
            occurred = _parse(event.occurred_at)
            received = _parse(event.received_at)
            age = (now - occurred).total_seconds()
            if received > now + timedelta(seconds=_FUTURE_TOLERANCE_SECONDS):
                return self._decision(watcher, event, "REJECTED", "event received_at is in the future", classification="uncertain")
            if age < -_FUTURE_TOLERANCE_SECONDS:
                return self._decision(watcher, event, "REJECTED", "event occurred_at is in the future", classification="uncertain")
            if age > int(trigger["max_event_age_seconds"]):
                return self._decision(watcher, event, "REJECTED", "camera event is stale", classification="stale")
            payload = event.payload if isinstance(event.payload, Mapping) else {}
            if payload.get("active") is False:
                return self._decision(watcher, event, "REJECTED", "camera activity is inactive", classification="observed")

            snapshot = self.builder.build(
                ContextRequest(
                    source_keys=("home_assistant",),
                    recent_event_limit=30,
                    now=now,
                )
            )
            source_ok, source_reason = self._source_is_usable(snapshot, event)
            camera_ok, camera_facts = self._camera_status(snapshot, event)
            occupancy_ok, occupancy_facts = self._occupancy_context(snapshot, watcher)
            conflicts = self._conflicting_events(snapshot, event, watcher)
            target_id, target_facts = self._resolve_target(snapshot, watcher)
            facts = {
                "event": {
                    "classification": "observed",
                    "context_event_id": event.event_id,
                    "home_assistant_event_id": home_assistant_event_id,
                    "event_type": event.event_type,
                    "entity_id": event.external_entity_id,
                    "occurred_at": event.occurred_at,
                    "received_at": event.received_at,
                    "age_seconds": age,
                },
                "activity_at_door": {
                    "classification": "observed",
                    "value": True,
                    "meaning": "activity at the door; not personal identity proof",
                },
                "departure": {
                    "classification": "inferred",
                    "value": "not_observed",
                    "meaning": "evidence is only consistent with the user's departure intention",
                },
                "source": {
                    "classification": "observed" if source_ok else "uncertain",
                    "usable": source_ok,
                    "reason": source_reason,
                    "warnings": list(snapshot.warnings),
                },
                "camera_status": camera_facts,
                "occupancy": occupancy_facts,
                "recent_conflicts": conflicts,
                "target": target_facts,
                "context_snapshot": _json_safe(snapshot.to_dict()),
            }
            usable = source_ok and camera_ok and occupancy_ok and not conflicts and target_id is not None
            if not usable:
                classification = (
                    "contradictory" if conflicts or not camera_ok and camera_facts.get("reason") == "camera offline" else "uncertain"
                )
                reason = "; ".join(
                    item
                    for item in (
                        source_reason if not source_ok else "",
                        camera_facts.get("reason", "") if not camera_ok else "",
                        occupancy_facts.get("reason", "") if not occupancy_ok else "",
                        "recent conflicting activity" if conflicts else "",
                        target_facts.get("reason", "") if target_id is None else "",
                    )
                    if item
                ) or "evidence was not consistent"
                unavailable = (
                    not source_ok
                    or camera_facts.get("classification") in {"stale", "uncertain"}
                    or occupancy_facts.get("classification") in {"stale", "uncertain"}
                    or target_id is None
                )
                if unavailable:
                    self._needs_attention(watcher_id, reason)
                    decision = "NEEDS_ATTENTION"
                else:
                    decision = "REJECTED"
                return self._decision(
                    watcher,
                    event,
                    decision,
                    reason,
                    facts=facts,
                    classification=classification,
                )
            self.store.record_departure_watcher_evidence(
                evidence_id=f"{watcher_id}:departure:{event.event_id}",
                watcher_id=watcher_id,
                context_event_id=event.event_id,
                evidence_kind="departure_candidate",
                classification="inferred",
                source_scope="observed_context_with_inference",
                facts=facts,
            )
            self.store.update_departure_watcher(
                watcher_id,
                status="TRIGGERED",
                increment_fire=True,
                triggered_at=now,
            )
            return self._prepare_action(
                watcher,
                event,
                target_id=target_id,
                target_facts=target_facts,
                facts=facts,
                home_assistant_event_id=home_assistant_event_id,
            )
        except Exception as exc:  # durable fail-closed boundary
            self._needs_attention(watcher_id, f"watcher evaluation failed: {type(exc).__name__}")
            return self._decision(
                watcher,
                event,
                "NEEDS_ATTENTION",
                f"watcher evaluation failed: {type(exc).__name__}",
                classification="uncertain",
                best_effort=True,
            )

    def _prepare_action(
        self,
        watcher: Mapping[str, Any],
        event: Any,
        *,
        target_id: str,
        target_facts: Mapping[str, Any],
        facts: Mapping[str, Any],
        home_assistant_event_id: str | None,
    ) -> dict[str, Any]:
        watcher_id = str(watcher["watcher_id"])
        now = self.clock()
        pre_state, pre_classification = self._read_pre_state(target_id, target_facts)
        if pre_state is None:
            self._needs_attention(
                watcher_id,
                "Living Room Lamp pre-action state is unavailable; no action was performed.",
            )
            return self._decision(
                watcher,
                event,
                "NEEDS_ATTENTION",
                "pre-action Living Room Lamp state unavailable",
                facts=facts,
            )
        self.store.record_departure_watcher_device_state(
            watcher_id=watcher_id,
            phase="pre_action",
            entity_id=target_id,
            state=pre_state,
            classification=pre_classification,
            observed_at=now,
        )
        if watcher["mode"] == "simulation":
            self.store.record_departure_watcher_authorization(
                authorization_id=f"simulation:{watcher_id}:{event.event_id}",
                watcher_id=watcher_id,
                action_id=None,
                decision="simulation_only_no_live_authorization",
                authority="watcher-policy",
                required=watcher["permissions"],
                scope={"action_type": "home_assistant.turn_off", "target": target_id},
                details={"context_event_id": event.event_id, "live_effects": False},
            )
            self.store.record_departure_watcher_device_state(
                watcher_id=watcher_id,
                phase="verification",
                entity_id=target_id,
                state=None,
                classification="uncertain",
                observed_at=now,
            )
            self.store.record_departure_watcher_rollback(
                watcher_id=watcher_id,
                action_id=None,
                method="turn_on Living Room Lamp only if a live action changed a previously-on lamp",
                prior_state=pre_state,
                status="not_needed_simulation",
                details={"live_effects": False},
            )
            self.store.record_departure_watcher_event(
                watcher_id=watcher_id,
                context_event_id=event.event_id,
                home_assistant_event_id=home_assistant_event_id,
                decision="SIMULATION_PREPARED",
                reason="action prepared but not sent in simulation mode",
            )
            self.store.update_departure_watcher(
                watcher_id,
                status="COMPLETED",
                completed_at=now,
            )
            report = self.inspect(watcher_id)
            report["decision"] = "SIMULATION_PREPARED"
            report["decision_reason"] = "action prepared but not sent in simulation mode"
            return report

        if not self.monitor_available or self.guardian is None or self.state_reader is None:
            self._needs_attention(
                watcher_id,
                "Home Assistant bridge, Guardian, or state reader is unavailable; live action blocked.",
            )
            return self._decision(
                watcher,
                event,
                "NEEDS_ATTENTION",
                "live execution dependencies unavailable",
                facts=facts,
            )
        if not watcher.get("live_approved") and not any(
            item["decision"] == "explicit_live_approval"
            for item in self.store.list_departure_watcher_authorizations(watcher_id)
        ):
            self._needs_attention(watcher_id, "explicit Marc live approval is missing")
            return self._decision(
                watcher,
                event,
                "NEEDS_ATTENTION",
                "explicit Marc live approval is missing",
                facts=facts,
            )

        parameters = {"target": target_id}
        proposal = ActionProposal(
            id=f"departure-action:{watcher_id}",
            action_type="home_assistant.turn_off",
            description="Turn off only the Living Room Lamp after a validated door-activity trigger.",
            parameters=parameters,
            idempotency_key=f"departure-watcher-action:{watcher_id}",
            expected_effect={"state": "off", "target": target_id},
            provenance={
                "component": "departure-watcher",
                "watcher_id": watcher_id,
                "context_event_id": event.event_id,
                "simulation_only": False,
            },
        )
        try:
            decision = self.guardian.authorize(
                proposal,
                session_id=watcher_id,
                authority="Marc",
            )
        except Exception as exc:
            self._needs_attention(watcher_id, f"Guardian authorization unavailable: {type(exc).__name__}")
            return self._decision(watcher, event, "NEEDS_ATTENTION", "Guardian authorization unavailable", facts=facts)
        self.store.record_departure_watcher_authorization(
            authorization_id=f"guardian:{proposal.id}",
            watcher_id=watcher_id,
            action_id=proposal.id,
            decision="authorized" if decision.allowed else "denied",
            authority="Guardian",
            required=watcher["permissions"],
            scope=decision.authorization.scope,
            details={"reason": decision.reason, "context_event_id": event.event_id},
        )
        if not decision.allowed:
            self._needs_attention(watcher_id, f"Guardian denied exact action: {decision.reason}")
            return self._decision(watcher, event, "NEEDS_ATTENTION", decision.reason, facts=facts)
        try:
            result = self.guardian.execute(proposal.id, decision.authorization)
        except Exception as exc:
            self._needs_attention(watcher_id, f"Guardian execution failed: {type(exc).__name__}")
            return self._decision(watcher, event, "NEEDS_ATTENTION", "Guardian execution failed", facts=facts)
        action_state = {
            "guardian_state": result.state.value,
            "outcome_success": result.outcome.success,
            "outcome_error": result.outcome.error.value if result.outcome.error else None,
            "exception_summary": result.exception_summary,
        }
        if result.verification is not None:
            action_state["guardian_verification"] = result.verification.to_dict()
        try:
            independently_observed = self.state_reader.read_state(target_id)
            independently_ok = _text(independently_observed.get("state")) == "off"
        except Exception as exc:
            independently_observed = {"error": type(exc).__name__}
            independently_ok = False
        self.store.record_departure_watcher_device_state(
            watcher_id=watcher_id,
            phase="post_action",
            entity_id=target_id,
            state=independently_observed,
            classification="observed" if independently_ok else "uncertain",
            observed_at=self.clock(),
        )
        self.store.record_departure_watcher_device_state(
            watcher_id=watcher_id,
            phase="verification",
            entity_id=target_id,
            state=independently_observed,
            classification="observed" if independently_ok else "contradictory",
            observed_at=self.clock(),
        )
        prior_on = _text(pre_state.get("state")) == "on" if isinstance(pre_state, Mapping) else False
        self.store.record_departure_watcher_rollback(
            watcher_id=watcher_id,
            action_id=proposal.id,
            method="home_assistant.turn_on for the Living Room Lamp only",
            prior_state=pre_state,
            status="available" if prior_on else "no_op_prior_state_not_on",
            details={"target": target_id},
        )
        if independently_ok and result.state.value == "verified":
            self.store.record_departure_watcher_event(
                watcher_id=watcher_id,
                context_event_id=event.event_id,
                home_assistant_event_id=home_assistant_event_id,
                decision="EXECUTED_AND_VERIFIED",
                reason="Guardian authorization and independent lamp readback succeeded",
            )
            self.store.update_departure_watcher(
                watcher_id,
                status="COMPLETED",
                completed_at=self.clock(),
            )
        else:
            self._needs_attention(
                watcher_id,
                "independent Living Room Lamp verification did not confirm off",
            )
            self.store.record_departure_watcher_event(
                watcher_id=watcher_id,
                context_event_id=event.event_id,
                home_assistant_event_id=home_assistant_event_id,
                decision="NEEDS_ATTENTION",
                reason="action result was not independently verified",
            )
        report = self.inspect(watcher_id)
        report["decision"] = (
            "EXECUTED_AND_VERIFIED"
            if independently_ok and result.state.value == "verified"
            else "NEEDS_ATTENTION"
        )
        return report

    def _read_pre_state(
        self, target_id: str, target_facts: Mapping[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        if self.state_reader is not None:
            try:
                state = self.state_reader.read_state(target_id)
                if isinstance(state, Mapping) and _text(state.get("state")) not in {
                    "unknown",
                    "unavailable",
                }:
                    return dict(state), "observed"
            except Exception:
                pass
        context_state = target_facts.get("context_state")
        if (
            target_facts.get("stale") is not True
            and isinstance(context_state, Mapping)
            and _text(context_state.get("state")) not in {"unknown", "unavailable"}
        ):
            return dict(context_state), "observed"
        return None, "uncertain"

    def _source_is_usable(self, snapshot: Any, event: Any) -> tuple[bool, str]:
        if event.source_status != "online":
            return False, f"Home Assistant source status is {event.source_status}"
        # ContextBuilder warnings include every stale entity in the source.
        # A stale backup sensor or speaker state must not invalidate a fresh
        # camera event. Only source-health warnings belong in this gate.
        warning = next(
            (
                item
                for item in snapshot.warnings
                if item.lower().startswith("home assistant (home_assistant)")
                and any(
                    token in item.lower()
                    for token in ("offline", "degraded", "stale", "not reported")
                )
            ),
            None,
        )
        if warning:
            return False, warning
        return True, "Home Assistant context source is online and fresh"

    def _camera_status(self, snapshot: Any, event: Any) -> tuple[bool, dict[str, Any]]:
        event_id = event.external_entity_id or ""
        searchable_event = _text(f"{event_id} {event.entity_name or ''}")
        candidates = []
        for entity in snapshot.entities:
            searchable = _text(f"{entity.external_entity_id} {entity.entity_name}")
            if entity.entity_type not in {"camera", "event"}:
                continue
            if event_id == entity.external_entity_id or any(
                term in searchable for term in ("front door", "front door camera", "doorbell")
            ) or any(part and part in searchable for part in searchable_event.split() if len(part) > 4):
                candidates.append(entity)
        if not candidates:
            if event.source_status == "online":
                return True, {
                    "classification": "inferred",
                    "status": "event_source_active",
                    "reason": "fresh camera event received from an online Home Assistant source",
                }
            return False, {
                "classification": "uncertain",
                "status": "unknown",
                "reason": "camera status unavailable",
            }
        stale_candidates = []
        for entity in candidates:
            state_map = {item.state_key: item.value for item in entity.states}
            status = state_map.get("status", state_map.get("state"))
            if _text(status) in {"offline", "unavailable", "disconnected"} and not entity.stale:
                return False, {
                    "classification": "contradictory",
                    "status": status,
                    "reason": "camera offline",
                }
            if entity.stale:
                stale_candidates.append(entity.entity_name)
                continue
            if _text(status) in {"online", "idle", "on", "streaming", "active"} or status is not None:
                return True, {"classification": "observed", "status": status, "reason": "camera status usable"}
        if event.source_status == "online":
            return True, {
                "classification": "inferred",
                "status": "event_source_active",
                "reason": (
                    "fresh camera event received from an online Home Assistant source; "
                    "only the cached camera status is stale"
                ),
                "stale_cached_entities": stale_candidates,
            }
        return False, {
            "classification": "uncertain",
            "status": "unknown",
            "reason": "camera status unavailable",
        }

    def _occupancy_context(self, snapshot: Any, watcher: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
        candidates = []
        for entity in snapshot.entities:
            searchable = _text(f"{entity.external_entity_id} {entity.entity_name}")
            if entity.entity_type not in {"binary_sensor", "sensor", "person", "device_tracker"}:
                continue
            if any(term in searchable for term in ("occupancy", "presence", "person", "resident", "device tracker")):
                candidates.append(entity)
        if not candidates:
            return True, {
                "classification": "uncertain",
                "status": "unknown",
                "observations": [],
                "reason": "occupancy context unavailable; no fresh contradictory observation",
            }
        observations = []
        stale_entities = []
        uncertain_entities = []
        fresh_active = False
        for entity in candidates:
            states = {item.state_key: item.value for item in entity.states}
            value = states.get("occupancy", states.get("state"))
            active = _truthy_state(value)
            observations.append(
                {
                    "entity_id": entity.external_entity_id,
                    "state": value,
                    "active": active,
                    "classification": "stale" if entity.stale else ("uncertain" if active is None else "observed"),
                }
            )
            if entity.stale:
                stale_entities.append(entity.entity_name)
            elif active is True:
                fresh_active = True
            elif active is None:
                uncertain_entities.append(entity.entity_name)
        if fresh_active:
            return False, {
                "classification": "contradictory",
                "status": "occupied",
                "observations": observations,
                "reason": "fresh occupancy context indicates someone is present",
            }
        if stale_entities or uncertain_entities:
            return True, {
                "classification": "uncertain",
                "status": "unknown",
                "observations": observations,
                "stale_entities": stale_entities,
                "uncertain_entities": uncertain_entities,
                "reason": "occupancy is stale or uncertain; no fresh contradictory observation",
            }
        return True, {
            "classification": "observed",
            "status": "unoccupied",
            "observations": observations,
            "reason": "fresh occupancy context is not occupied",
        }

    def _conflicting_events(self, snapshot: Any, trigger_event: Any, watcher: Mapping[str, Any]) -> list[dict[str, Any]]:
        armed = _parse(watcher["armed_at"])
        now = self.clock()
        window = int(watcher["trigger"]["conflict_window_seconds"])
        trigger_occurred = _parse(trigger_event.occurred_at)
        conflicts: list[dict[str, Any]] = []
        for event in snapshot.recent_events:
            if event.event_id == trigger_event.event_id:
                continue
            occurred = _parse(event.occurred_at)
            if occurred < armed or (now - occurred).total_seconds() > window:
                continue
            # Motion or presence immediately before the camera event is normal
            # departure context. Only activity after the trigger can contradict
            # the requested one-shot departure action.
            if occurred <= trigger_occurred:
                continue
            searchable = _text(f"{event.entity_name or ''} {event.area or ''}")
            if event.event_type == "motion_heartbeat" and bool(event.payload.get("active")):
                conflicts.append({"event_id": event.event_id, "reason": "recent active motion", "classification": "contradictory"})
            elif event.event_type == "person_detected" and "front door" not in searchable and "doorbell" not in searchable:
                conflicts.append({"event_id": event.event_id, "reason": "person detected elsewhere", "classification": "contradictory"})
            elif event.event_type in {"door_opened", "door_open", "state_changed"}:
                payload = event.payload if isinstance(event.payload, Mapping) else {}
                new_state = payload.get("new_state")
                if isinstance(new_state, Mapping):
                    new_state = new_state.get("state")
                if _truthy_state(new_state) is True and any(term in searchable for term in ("occupancy", "presence", "person", "motion", "door")):
                    conflicts.append({"event_id": event.event_id, "reason": "recent conflicting state change", "classification": "contradictory"})
        return conflicts

    def _resolve_target(self, snapshot: Any, watcher: Mapping[str, Any]) -> tuple[str | None, dict[str, Any]]:
        action = watcher["action"]
        explicit = action.get("target_entity_id")
        if explicit:
            candidates = [entity for entity in snapshot.entities if entity.external_entity_id == explicit]
        else:
            target_name = _text(action.get("target_name", _TARGET_NAME))
            candidates = [
                entity
                for entity in snapshot.entities
                if entity.entity_type == "light" and _text(entity.entity_name) == target_name
            ]
        if len(candidates) != 1:
            return None, {
                "classification": "uncertain",
                "reason": "Living Room Lamp target is missing or ambiguous",
                "candidate_count": len(candidates),
            }
        entity = candidates[0]
        if entity.entity_type != "light" or entity.external_entity_id not in {
            _TARGET_ENTITY_ID,
            explicit,
        } and not (_text(entity.entity_name) == _text(_TARGET_NAME)):
            return None, {"classification": "contradictory", "reason": "resolved target is not only the Living Room Lamp"}
        states = {item.state_key: item.value for item in entity.states}
        return entity.external_entity_id, {
            "classification": "observed",
            "entity_id": entity.external_entity_id,
            "entity_name": entity.entity_name,
            "stale": bool(entity.stale),
            "context_state": {"state": states.get("state"), "attributes": {}},
        }

    def _decision(
        self,
        watcher: Mapping[str, Any],
        event: Any,
        decision: str,
        reason: str,
        *,
        facts: Mapping[str, Any] | None = None,
        classification: str = "observed",
        best_effort: bool = False,
    ) -> dict[str, Any]:
        watcher_id = str(watcher["watcher_id"])
        try:
            self.store.record_departure_watcher_event(
                watcher_id=watcher_id,
                context_event_id=event.event_id,
                home_assistant_event_id=event.external_event_id,
                decision=decision,
                reason=reason,
            )
            if facts is not None:
                self.store.record_departure_watcher_evidence(
                    evidence_id=f"{watcher_id}:decision:{event.event_id}",
                    watcher_id=watcher_id,
                    context_event_id=event.event_id,
                    evidence_kind="watcher_decision",
                    classification=classification,
                    source_scope="context_database",
                    facts=facts,
                )
        except Exception:
            if not best_effort:
                self._needs_attention(watcher_id, "watcher decision/evidence record unavailable")
        if decision == "NEEDS_ATTENTION":
            self._needs_attention(watcher_id, reason)
        return {"watcher_id": watcher_id, "context_event_id": event.event_id, "decision": decision, "reason": reason}

    def _needs_attention(self, watcher_id: str, reason: str) -> None:
        try:
            self.store.update_departure_watcher(
                watcher_id,
                status="NEEDS_ATTENTION",
                error=reason,
                reason=reason,
            )
        except Exception:
            # There is no safe action when the evidence DB itself cannot be
            # updated. The caller still fails closed and surfaces the reason.
            pass
        self._active_ids.discard(watcher_id)

    def _mark_all_needs_attention(self, reason: str) -> None:
        for watcher in self.store.list_active_departure_watchers(now=self.clock()):
            self._needs_attention(watcher["watcher_id"], reason)

    def _expire_due_watchers(self) -> None:
        now = self.clock()
        for watcher in self.store.list_departure_watchers(status="ACTIVE"):
            if _parse(watcher["expires_at"]) <= now:
                self.store.update_departure_watcher(
                    watcher["watcher_id"],
                    status="EXPIRED",
                    reason="watcher expiration time reached",
                )
                self._active_ids.discard(watcher["watcher_id"])


__all__ = ["DepartureWatcherService"]
