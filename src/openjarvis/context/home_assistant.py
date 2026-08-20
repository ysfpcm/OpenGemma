"""Home Assistant event and snapshot bridge for Ophanim.

Home Assistant is the only upstream used for Google/Nest camera events. This
module intentionally does not create a video stream or expose source URLs:
camera event metadata is normalized into the context store and the event bus,
while expiring snapshot URLs are downloaded into a local snapshot directory.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from openjarvis.context.store import ApplyResult, ContextEvent, ContextStore, StateValue
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.paths import get_data_dir

logger = logging.getLogger(__name__)

_SOURCE_KEY = "home_assistant"
_SOURCE_NAME = "Home Assistant"
_SOURCE_TYPE = "home_assistant"
_DEFAULT_URL = "http://127.0.0.1:8123"
_DEFAULT_STALE_AFTER_SECONDS = 300
_DEFAULT_MAX_SNAPSHOT_BYTES = 10 * 1024 * 1024
_DEFAULT_MOTION_HEARTBEAT_SECONDS = 120.0
_DEFAULT_MOTION_HOLD_SECONDS = 300.0


def _home_assistant_env(*names: str, default: str = "") -> str:
    """Read the first configured Home Assistant environment alias."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default

_CONTEXT_ATTRIBUTE_KEYS = {
    "battery_level",
    "brightness",
    "charging",
    "current_humidity",
    "current_temperature",
    "humidity",
    "is_volume_muted",
    "media_title",
    "motion",
    "occupancy",
    "percentage",
    "temperature",
    "volume_level",
}

_SNAPSHOT_URL_KEYS = {
    "event_snapshot",
    "image",
    "image_url",
    "snapshot",
    "snapshot_path",
    "snapshot_uri",
    "snapshot_url",
    "thumbnail_url",
}
_VIDEO_URL_KEYS = {
    "clip",
    "clip_url",
    "event_video",
    "video",
    "video_url",
}
_SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
}
_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+"),
    re.compile(r"(?i)([?&](?:access_token|api_key|token|sig|signature)=)[^&#\s]+"),
)


def redact_secrets(value: Any, *, key: str | None = None) -> Any:
    """Return a safe copy suitable for logs and operator-facing payloads."""
    key_lower = key.lower() if key else ""
    if key_lower in {"sdp", "stream_url"}:
        return "[REDACTED]"
    if key_lower and "url" in key_lower and isinstance(value, str) and value.startswith("/"):
        return "[URL_REDACTED]"
    if key_lower in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_secrets(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        if value.startswith(("http://", "https://", "rtsp://", "ws://", "wss://")):
            return "[URL_REDACTED]"
        redacted = value
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        return redacted
    return value


@dataclass(frozen=True, slots=True)
class NormalizedCameraEvent:
    """Safe semantic representation of a Home Assistant camera event."""

    event_type: str
    entity_id: str
    entity_name: str
    entity_domain: str
    occurred_at: Any = None
    active: bool | None = None
    status: str | None = None
    source_event_id: str | None = None
    # Kept private to the bridge; it is never serialized into context/bus data.
    snapshot_url: str | None = field(default=None, repr=False, compare=False)
    video_url: str | None = field(default=None, repr=False, compare=False)


@dataclass(slots=True)
class _MotionActivity:
    """In-memory activity window for a Home Assistant motion-like sensor."""

    entity_id: str
    entity_name: str
    entity_domain: str
    device_class: str
    raw_active: bool
    last_detected_monotonic: float | None = None
    last_detected_at: Any = None
    recent_activity_active: bool = False


SnapshotFetcher = Callable[
    [str, Mapping[str, str]], Awaitable[tuple[bytes, str | None] | bytes]
]


def _timestamp_value(value: Any) -> Any:
    return value if isinstance(value, (str, datetime)) else None


def _state_is_active(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"on", "true", "active", "detected", "pressed", "ringing", "open"}:
        return True
    if normalized in {"off", "false", "inactive", "clear", "closed", "idle"}:
        return False
    return None


def _looks_like_snapshot_url(value: str) -> bool:
    if not value.startswith(("http://", "https://", "/")):
        return False
    lowered = value.lower()
    return not lowered.startswith(("rtsp://", "ws://", "wss://")) and ".m3u8" not in lowered


def _extract_media_url(
    value: Any,
    *,
    keys: set[str],
    allow_generic_url: bool = False,
) -> str | None:
    if isinstance(value, Mapping):
        for raw_key, raw_value in value.items():
            key = str(raw_key).lower()
            if key in keys or (allow_generic_url and key == "url"):
                if isinstance(raw_value, str) and _looks_like_snapshot_url(raw_value):
                    return raw_value
            nested = _extract_media_url(
                raw_value,
                keys=keys,
                allow_generic_url=allow_generic_url,
            )
            if nested:
                return nested
    elif isinstance(value, (list, tuple)):
        for item in value:
            nested = _extract_media_url(
                item,
                keys=keys,
                allow_generic_url=allow_generic_url,
            )
            if nested:
                return nested
    return None


def _extract_snapshot_url(value: Any, *, allow_generic_url: bool = False) -> str | None:
    """Extract the first image/snapshot attachment from an event."""
    return _extract_media_url(
        value,
        keys=_SNAPSHOT_URL_KEYS,
        allow_generic_url=allow_generic_url,
    )


def _extract_video_url(value: Any, *, allow_generic_url: bool = False) -> str | None:
    """Extract the first video/clip attachment from an event."""
    return _extract_media_url(
        value,
        keys=_VIDEO_URL_KEYS,
        allow_generic_url=allow_generic_url,
    )


def _media_subdirectory(content_type: str) -> str:
    if content_type == "image/gif":
        return "gifs"
    if content_type in {"image/jpeg", "image/jpg"}:
        return "jpgs"
    if content_type.startswith("video/"):
        return "videos"
    return "images"


def _camera_entity_for_event(entity_id: str) -> str | None:
    """Map a Home Assistant camera event entity back to its camera entity."""
    domain, separator, object_id = entity_id.partition(".")
    if not separator:
        return None
    if domain == "camera":
        return entity_id
    if domain != "event":
        return None
    for suffix in ("_motion", "_chime", "_person", "_sound"):
        if object_id.endswith(suffix):
            return f"camera.{object_id[:-len(suffix)]}"
    return None


class HomeAssistantContextSource:
    """Ingest Home Assistant state/event messages into Ophanim."""

    def __init__(
        self,
        store: ContextStore,
        *,
        url: str | None = None,
        token: str | None = None,
        bus: EventBus | None = None,
        stale_after_seconds: int = _DEFAULT_STALE_AFTER_SECONDS,
        snapshot_dir: str | Path | None = None,
        snapshot_fetcher: SnapshotFetcher | None = None,
        max_snapshot_bytes: int = _DEFAULT_MAX_SNAPSHOT_BYTES,
        motion_heartbeat_seconds: float | None = None,
        motion_hold_seconds: float | None = None,
        motion_entity_ids: Sequence[str] | None = None,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be greater than zero")
        if max_snapshot_bytes <= 0:
            raise ValueError("max_snapshot_bytes must be greater than zero")
        configured_heartbeat = motion_heartbeat_seconds
        if configured_heartbeat is None:
            configured_heartbeat = self._seconds_from_env(
                "HA_MOTION_HEARTBEAT_SECONDS",
                "HOME_ASSISTANT_MOTION_HEARTBEAT_SECONDS",
                default=_DEFAULT_MOTION_HEARTBEAT_SECONDS,
            )
        configured_hold = motion_hold_seconds
        if configured_hold is None:
            configured_hold = self._seconds_from_env(
                "HA_MOTION_HOLD_SECONDS",
                "HOME_ASSISTANT_MOTION_HOLD_SECONDS",
                default=_DEFAULT_MOTION_HOLD_SECONDS,
            )
        if configured_heartbeat <= 0:
            raise ValueError("motion_heartbeat_seconds must be greater than zero")
        if configured_hold <= 0:
            raise ValueError("motion_hold_seconds must be greater than zero")
        self._store = store
        self._url = (
            url
            if url is not None
            else _home_assistant_env(
                "HA_URL", "HOME_ASSISTANT_URL", default=_DEFAULT_URL
            )
        ).strip()
        self._token = (
            token
            if token is not None
            else _home_assistant_env("HA_TOKEN", "HOME_ASSISTANT_TOKEN")
        ).strip()
        self._bus = bus
        self._stale_after_seconds = stale_after_seconds
        self._snapshot_dir = Path(
            snapshot_dir
            if snapshot_dir is not None
            else os.environ.get("HA_SNAPSHOT_DIR", str(get_data_dir() / "context_snapshots"))
        ).expanduser()
        self._snapshot_fetcher = snapshot_fetcher
        self._max_snapshot_bytes = max_snapshot_bytes
        self._motion_heartbeat_seconds = configured_heartbeat
        self._motion_hold_seconds = configured_hold
        if motion_entity_ids is None:
            configured_entities = _home_assistant_env(
                "HA_MOTION_ENTITY_IDS",
                "HOME_ASSISTANT_MOTION_ENTITY_IDS",
            )
            motion_entity_ids = configured_entities.split(",") if configured_entities else None
        self._motion_entity_ids = {
            entity_id.strip()
            for entity_id in motion_entity_ids or ()
            if entity_id.strip()
        } or None
        self._motion_activity: dict[str, _MotionActivity] = {}
        self._motion_clear_sequence = 0
        self._connection_sequence = 0

    @staticmethod
    def _seconds_from_env(*names: str, default: float) -> float:
        raw_value = _home_assistant_env(*names)
        if not raw_value:
            return default
        try:
            return float(raw_value)
        except ValueError as exc:
            raise ValueError(f"invalid motion timing value: {raw_value!r}") from exc

    @property
    def is_configured(self) -> bool:
        return bool(self._url and self._token)

    @property
    def websocket_url(self) -> str:
        """Return Home Assistant's WebSocket URL without query credentials."""
        if not self._url:
            raise RuntimeError("Home Assistant URL is not configured (set HA_URL)")
        parsed = urlsplit(self._url)
        if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
            raise ValueError("Home Assistant URL must be an http(s) or ws(s) URL with a host")
        scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
        path = parsed.path.rstrip("/")
        if path.endswith("/api/websocket"):
            websocket_path = path
        elif path.endswith("/api"):
            websocket_path = f"{path}/websocket"
        else:
            websocket_path = f"{path}/api/websocket" if path else "/api/websocket"
        return urlunsplit((scheme, parsed.netloc, websocket_path, "", ""))

    def _is_motion_entity(
        self,
        entity_id: str,
        entity_name: str,
        domain: str,
        attributes: Mapping[str, Any],
    ) -> bool:
        if self._motion_entity_ids is not None:
            return entity_id in self._motion_entity_ids
        if domain != "binary_sensor":
            return False
        device_class = str(attributes.get("device_class") or "").strip().lower()
        if device_class in {"motion", "occupancy", "presence"}:
            return True
        searchable = f"{entity_id} {entity_name}".lower()
        return any(token in searchable for token in ("motion", "occupancy", "presence"))

    def _track_motion_state(
        self,
        state: Mapping[str, Any],
        *,
        attributes: Mapping[str, Any],
        occurred_at: Any,
    ) -> None:
        entity_id = state.get("entity_id")
        if not isinstance(entity_id, str) or "." not in entity_id:
            return
        domain = entity_id.split(".", 1)[0]
        entity_name = attributes.get("friendly_name")
        if not isinstance(entity_name, str) or not entity_name.strip():
            entity_name = entity_id
        if not self._is_motion_entity(entity_id, entity_name, domain, attributes):
            return

        raw_state = str(state.get("state") or "").strip().lower()
        active = _state_is_active(raw_state)
        if active is None:
            if raw_state not in {"unknown", "unavailable"}:
                return
            active = False
        activity = self._motion_activity.get(entity_id)
        if activity is None:
            activity = _MotionActivity(
                entity_id=entity_id,
                entity_name=entity_name.strip(),
                entity_domain=domain,
                device_class=str(attributes.get("device_class") or ""),
                raw_active=active,
                recent_activity_active=active,
            )
            self._motion_activity[entity_id] = activity
        else:
            activity.entity_name = entity_name.strip()
            activity.entity_domain = domain
            activity.device_class = str(attributes.get("device_class") or "")
            activity.raw_active = active
            if active:
                activity.recent_activity_active = True

        if active:
            activity.last_detected_monotonic = time.monotonic()
            activity.last_detected_at = _timestamp_value(occurred_at) or datetime.now(timezone.utc)

    def emit_motion_heartbeats(
        self,
        *,
        now_monotonic: float | None = None,
        occurred_at: Any | None = None,
    ) -> list[ApplyResult]:
        """Persist recent-activity heartbeats for motion-like sensors."""
        tick_now = time.monotonic() if now_monotonic is None else now_monotonic
        observed_at = occurred_at or datetime.now(timezone.utc)
        results: list[ApplyResult] = []
        heartbeat_bucket = int(tick_now // self._motion_heartbeat_seconds)

        for activity in list(self._motion_activity.values()):
            if activity.raw_active:
                activity.recent_activity_active = True
            elif not activity.recent_activity_active:
                continue
            elif (
                activity.last_detected_monotonic is None
                or tick_now - activity.last_detected_monotonic > self._motion_hold_seconds
            ):
                activity.recent_activity_active = False

            if activity.recent_activity_active:
                results.append(
                    self._apply_motion_activity(
                        activity,
                        active=True,
                        occurred_at=observed_at,
                        external_event_id=(
                            f"motion:heartbeat:{activity.entity_id}:{heartbeat_bucket}"
                        ),
                    )
                )
            else:
                self._motion_clear_sequence += 1
                results.append(
                    self._apply_motion_activity(
                        activity,
                        active=False,
                        occurred_at=observed_at,
                        external_event_id=(
                            f"motion:cleared:{activity.entity_id}:{self._motion_clear_sequence}"
                        ),
                    )
                )
        return results

    def _apply_motion_activity(
        self,
        activity: _MotionActivity,
        *,
        active: bool,
        occurred_at: Any,
        external_event_id: str,
    ) -> ApplyResult:
        event_type = "motion_heartbeat" if active else "motion_cleared"
        result = self._store.apply_event(
            ContextEvent(
                source_key=_SOURCE_KEY,
                source_type=_SOURCE_TYPE,
                source_display_name=_SOURCE_NAME,
                stale_after_seconds=self._stale_after_seconds,
                external_event_id=external_event_id,
                external_entity_id=activity.entity_id,
                entity_type=activity.entity_domain,
                entity_name=activity.entity_name,
                entity_metadata={
                    "domain": activity.entity_domain,
                    "device_class": activity.device_class,
                    "activity_semantics": "recent_activity",
                },
                event_type=event_type,
                occurred_at=occurred_at,
                state={"recent_activity": StateValue(active)},
                payload={
                    "active": active,
                    "motion_event": event_type,
                    "raw_motion_state": "on" if activity.raw_active else "off",
                    "last_motion_observed_at": activity.last_detected_at,
                    "snapshot_available": False,
                },
            )
        )
        if result.inserted and self._bus is not None:
            bus_event_type = EventType.MOTION_HEARTBEAT if active else EventType.MOTION_CLEARED
            context_data = {
                "source": _SOURCE_KEY,
                "context_event_id": result.event_id,
                "event_type": event_type,
                "entity_name": activity.entity_name,
                "entity_type": activity.entity_domain,
                "changed_state_keys": list(result.changed_state_keys),
                "updated_state_keys": list(result.updated_state_keys),
                "changed_state_count": len(result.changed_state_keys),
                "updated_state_count": len(result.updated_state_keys),
            }
            self._bus.publish(EventType.CONTEXT_UPDATED, context_data)
            self._bus.publish(
                bus_event_type,
                {
                    "source": _SOURCE_KEY,
                    "entity_id": activity.entity_id,
                    "entity_name": activity.entity_name,
                    "entity_type": activity.entity_domain,
                    "active": active,
                    "motion_event": event_type,
                    "recent_activity": active,
                    "raw_motion_state": "on" if activity.raw_active else "off",
                    "snapshot_available": False,
                },
            )
        return result

    async def _motion_heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._motion_heartbeat_seconds)
                self.emit_motion_heartbeats()
        except asyncio.CancelledError:
            raise

    def ingest_websocket_message(self, message: Mapping[str, Any]) -> ApplyResult | None:
        """Synchronously ingest the legacy state_changed path."""
        if message.get("type") != "event":
            return None
        event = message.get("event")
        if not isinstance(event, Mapping) or event.get("event_type") != "state_changed":
            return None
        return self.ingest_state_changed(event)

    async def process_websocket_message(self, message: Mapping[str, Any]) -> list[ApplyResult]:
        """Normalize one WebSocket event and download its snapshot promptly."""
        if message.get("type") != "event":
            return []
        event = message.get("event")
        if not isinstance(event, Mapping):
            return []
        results: list[ApplyResult] = []
        if event.get("event_type") == "state_changed":
            state_result = self.ingest_state_changed(event)
            if state_result is not None:
                results.append(state_result)
        normalized = self.normalize_event(event)
        if normalized is None:
            return results
        semantic_result, semantic_event_id = self._apply_normalized_event(normalized)
        results.append(semantic_result)
        if semantic_result.inserted:
            media_urls = tuple(
                dict.fromkeys(
                    url
                    for url in (normalized.snapshot_url, normalized.video_url)
                    if url
                )
            )
            for media_url in media_urls:
                results.append(
                    await self._store_snapshot_event(
                        normalized,
                        media_url=media_url,
                        semantic_event_id=semantic_event_id,
                    )
                )
        return results

    def ingest_state_changed(self, event: Mapping[str, Any]) -> ApplyResult | None:
        """Normalize and persist one Home Assistant ``state_changed`` event."""
        data = event.get("data")
        if not isinstance(data, Mapping):
            return None
        new_state = data.get("new_state")
        old_state = data.get("old_state")
        state = new_state if isinstance(new_state, Mapping) else old_state
        if not isinstance(state, Mapping):
            return None
        return self._ingest_state(
            state,
            occurred_at=event.get("time_fired"),
            event_type="state_changed",
            old_state=old_state if isinstance(old_state, Mapping) else None,
            new_state=new_state if isinstance(new_state, Mapping) else None,
        )

    def ingest_state_snapshot(self, state: Mapping[str, Any]) -> ApplyResult | None:
        """Normalize one state from Home Assistant's ``get_states`` response."""
        return self._ingest_state(
            state,
            occurred_at=state.get("last_updated") or state.get("last_changed"),
            event_type="state_snapshot",
            old_state=None,
            new_state=state,
        )

    def normalize_event(self, event: Mapping[str, Any]) -> NormalizedCameraEvent | None:
        """Map Home Assistant's varied camera event shapes to one taxonomy."""
        outer_type = str(event.get("event_type") or "").lower()
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        state = data.get("new_state") if isinstance(data.get("new_state"), Mapping) else {}
        if not state and isinstance(data.get("old_state"), Mapping):
            state = data["old_state"]
        attributes = state.get("attributes") if isinstance(state.get("attributes"), Mapping) else {}
        if not attributes and isinstance(data.get("attributes"), Mapping):
            attributes = data["attributes"]

        entity_id = (
            data.get("entity_id")
            or data.get("camera_entity_id")
            or state.get("entity_id")
            or event.get("entity_id")
        )
        if not isinstance(entity_id, str):
            device_id = data.get("camera_id") or data.get("device_id")
            if isinstance(device_id, str) and device_id.strip():
                entity_id = f"camera.{device_id.strip()}"
        if not isinstance(entity_id, str) or "." not in entity_id:
            return None
        domain = entity_id.split(".", 1)[0]
        entity_name = attributes.get("friendly_name")
        if not isinstance(entity_name, str) or not entity_name.strip():
            entity_name = entity_id
        explicit_event_name = " ".join(
            str(item)
            for item in (
                outer_type,
                data.get("event_type"),
                data.get("type"),
                data.get("event"),
                data.get("event_name"),
                data.get("capability"),
                attributes.get("event_type"),
                attributes.get("event_name"),
            )
            if item
        ).lower()
        entity_event_name = " ".join(
            str(item)
            for item in (entity_id, entity_name, attributes.get("device_class"))
            if item
        ).lower()
        event_name = f"{explicit_event_name} {entity_event_name}".strip()
        snapshot_url = _extract_snapshot_url(
            event,
            allow_generic_url=("snapshot" in event_name or "camera_event" in event_name),
        )
        video_url = _extract_video_url(
            event,
            allow_generic_url=("video" in event_name or "camera_event" in event_name),
        )
        camera_hint = (
            domain == "camera"
            or snapshot_url is not None
            or video_url is not None
            or any(
                token in event_name
                for token in ("camera", "nest", "doorbell", "motion", "person", "sound", "chime")
            )
        )
        if not camera_hint:
            return None

        state_value = state.get("state") if isinstance(state, Mapping) else data.get("state")
        active = _state_is_active(state_value)
        explicit_camera_event = (
            outer_type not in {"", "state_changed"}
            or any(
                token in explicit_event_name
                for token in (
                    "camera_event",
                    "camera_motion",
                    "person_detected",
                    "sound_detected",
                    "doorbell_chime",
                    "ring",
                    "motion",
                    "person",
                    "sound",
                )
            )
        )
        normalized_type: str | None = None
        status: str | None = None
        # A camera's friendly name/attributes often contain "doorbell" even
        # when it only reports a normal streaming state. Do not infer a
        # semantic camera event from those fields unless Home Assistant sent
        # an explicit event signal. For real event entities, the entity id
        # still provides a useful fallback (with motion/person/sound taking
        # precedence over a device name containing "doorbell").
        if domain != "camera" or explicit_camera_event:
            if "motion" in event_name:
                normalized_type = "camera_motion"
            elif "person" in event_name or "occupancy" in event_name:
                normalized_type = "person_detected"
            elif "sound" in event_name or "audio" in event_name:
                normalized_type = "sound_detected"
            elif (
                "chime" in event_name
                or "ring" in event_name
                or str(attributes.get("device_class") or "").lower() == "doorbell"
                or (domain == "event" and "doorbell" in event_name)
            ):
                normalized_type = "doorbell_chime"
                active = True if active is None else active
        if normalized_type is None and (domain == "camera" or any(
            token in event_name for token in ("online", "offline", "unavailable", "connected", "disconnected")
        )):
            normalized_type = "camera_status"
            status = (
                "offline"
                if str(state_value).lower()
                in {"unavailable", "unknown", "offline", "disconnected", "off"}
                else "online"
            )

        if normalized_type is None:
            return None
        if normalized_type in {
            "camera_motion",
            "person_detected",
            "sound_detected",
            "doorbell_chime",
        }:
            # Event entities use a timestamp-like state; unknown/unavailable
            # is only an initial or disconnected state, not a detection.
            if domain == "event" and str(state_value).strip().lower() in {
                "",
                "none",
                "unknown",
                "unavailable",
            }:
                return None
            old_state = data.get("old_state") if isinstance(data.get("old_state"), Mapping) else {}
            old_value = old_state.get("state")
            if active is True and _state_is_active(old_value) is True:
                return None
        if active is None and normalized_type in {
            "camera_motion",
            "person_detected",
            "sound_detected",
            "doorbell_chime",
        }:
            active = True
        # Home Assistant's Nest event entities can report motion immediately
        # without a media attachment. Capture the current camera-proxy frame
        # for those state changes so motion still produces a reviewable JPEG.
        if (
            normalized_type in {
                "camera_motion",
                "person_detected",
                "sound_detected",
                "doorbell_chime",
            }
            and snapshot_url is None
            and video_url is None
            and outer_type == "state_changed"
            and domain == "event"
        ):
            camera_entity_id = _camera_entity_for_event(entity_id)
            if camera_entity_id is not None:
                snapshot_url = f"/api/camera_proxy/{camera_entity_id}"
        source_event_id = data.get("id") or event.get("id")
        if not isinstance(source_event_id, str):
            context = data.get("context")
            source_event_id = context.get("id") if isinstance(context, Mapping) else None
        return NormalizedCameraEvent(
            event_type=normalized_type,
            entity_id=entity_id,
            entity_name=entity_name.strip(),
            entity_domain=domain,
            occurred_at=_timestamp_value(
                event.get("time_fired")
                or data.get("time_fired")
                or data.get("timestamp")
                or state.get("last_changed")
            ),
            active=active,
            status=status,
            source_event_id=source_event_id if isinstance(source_event_id, str) else None,
            snapshot_url=snapshot_url,
            video_url=video_url,
        )

    def record_connection_status(self, status: str, *, reason_code: str | None = None) -> ApplyResult:
        """Persist a safe source-level connection status event."""
        if status not in {"online", "degraded", "offline"}:
            raise ValueError("status must be online, degraded, or offline")
        self._connection_sequence += 1
        event_id = f"connection:{status}:{time.time_ns()}:{self._connection_sequence}"
        result = self._store.apply_event(
            ContextEvent(
                source_key=_SOURCE_KEY,
                source_type=_SOURCE_TYPE,
                source_display_name=_SOURCE_NAME,
                source_status=status,
                stale_after_seconds=self._stale_after_seconds,
                external_event_id=event_id,
                event_type="source_status",
                payload={"status": status, "reason_code": reason_code or ""},
            )
        )
        if result.inserted and self._bus is not None:
            self._bus.publish(
                EventType.HOME_ASSISTANT_STATUS,
                {"source": _SOURCE_KEY, "status": status, "reason_code": reason_code or ""},
            )
        return result

    def _ingest_state(
        self,
        state: Mapping[str, Any],
        *,
        occurred_at: Any,
        event_type: str,
        old_state: Mapping[str, Any] | None,
        new_state: Mapping[str, Any] | None,
    ) -> ApplyResult | None:
        entity_id = state.get("entity_id")
        if not isinstance(entity_id, str) or "." not in entity_id:
            return None
        attributes = state.get("attributes")
        attributes = attributes if isinstance(attributes, Mapping) else {}
        domain = entity_id.split(".", 1)[0]
        display_name = attributes.get("friendly_name")
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = entity_id
        observed_at = _timestamp_value(occurred_at)
        self._track_motion_state(
            state,
            attributes=attributes,
            occurred_at=observed_at,
        )
        context_state = self._context_state_values(state, attributes)
        fingerprint = {
            "entity_id": entity_id,
            "event_type": event_type,
            "occurred_at": observed_at,
            "state": state.get("state"),
            "attributes": attributes,
        }
        external_event_id = hashlib.sha256(
            json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        payload = {
            "old_state": old_state.get("state") if old_state is not None else None,
            "new_state": new_state.get("state") if new_state is not None else state.get("state"),
        }
        result = self._store.apply_event(
            ContextEvent(
                source_key=_SOURCE_KEY,
                source_type=_SOURCE_TYPE,
                source_display_name=_SOURCE_NAME,
                stale_after_seconds=self._stale_after_seconds,
                external_event_id=external_event_id,
                external_entity_id=entity_id,
                entity_type=domain,
                entity_name=display_name,
                entity_metadata={
                    "domain": domain,
                    "device_class": attributes.get("device_class"),
                    "unit_of_measurement": attributes.get("unit_of_measurement"),
                },
                event_type=event_type,
                occurred_at=observed_at,
                state=context_state,
                payload=redact_secrets(payload),
            )
        )
        # State changes are useful outside the context database too. Publish a
        # compact, secret-free notification so Ophanim's live event surfaces
        # can react immediately instead of waiting for their next poll.
        if event_type != "state_snapshot" and result.inserted and self._bus is not None:
            self._bus.publish(
                EventType.CONTEXT_UPDATED,
                {
                    "source": _SOURCE_KEY,
                    "context_event_id": result.event_id,
                    "event_type": event_type,
                    "entity_name": str(display_name),
                    "entity_type": domain,
                    "changed_state_keys": list(result.changed_state_keys),
                    "updated_state_keys": list(result.updated_state_keys),
                    "changed_state_count": len(result.changed_state_keys),
                    "updated_state_count": len(result.updated_state_keys),
                },
            )
        return result

    @staticmethod
    def _context_state_values(state: Mapping[str, Any], attributes: Mapping[str, Any]) -> dict[str, StateValue]:
        values: dict[str, StateValue] = {
            "state": StateValue(state.get("state"), str(attributes.get("unit_of_measurement") or ""))
        }
        for key in sorted(_CONTEXT_ATTRIBUTE_KEYS):
            value = attributes.get(key)
            if isinstance(value, (str, int, float, bool)) or (value is None and key in attributes):
                unit = ""
                if key in {"temperature", "current_temperature"}:
                    unit = str(attributes.get("temperature_unit") or attributes.get("unit_of_measurement") or "")
                elif key in {"humidity", "current_humidity"}:
                    unit = "%"
                values[key] = StateValue(value, unit)
        return values

    def _apply_normalized_event(self, normalized: NormalizedCameraEvent) -> tuple[ApplyResult, str]:
        identity = normalized.source_event_id or hashlib.sha256(
            json.dumps(
                {
                    "event_type": normalized.event_type,
                    "entity_id": normalized.entity_id,
                    "occurred_at": normalized.occurred_at,
                    "active": normalized.active,
                    "status": normalized.status,
                },
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        external_event_id = f"camera:{normalized.event_type}:{identity}"
        state: dict[str, StateValue] = {}
        if normalized.event_type == "camera_status":
            state["status"] = StateValue(normalized.status or "unknown")
        else:
            state_key = {
                "camera_motion": "motion",
                "person_detected": "person",
                "sound_detected": "sound",
                "doorbell_chime": "doorbell",
            }[normalized.event_type]
            state[state_key] = StateValue(normalized.active)
        payload = {
            "camera_event": normalized.event_type,
            "active": normalized.active,
            "status": normalized.status,
            "snapshot_available": bool(normalized.snapshot_url or normalized.video_url),
        }
        result = self._store.apply_event(
            ContextEvent(
                source_key=_SOURCE_KEY,
                source_type=_SOURCE_TYPE,
                source_display_name=_SOURCE_NAME,
                stale_after_seconds=self._stale_after_seconds,
                external_event_id=external_event_id,
                external_entity_id=normalized.entity_id,
                entity_type="camera" if normalized.entity_domain == "camera" else normalized.entity_domain,
                entity_name=normalized.entity_name,
                entity_metadata={"domain": normalized.entity_domain, "camera_event": normalized.event_type},
                event_type=normalized.event_type,
                occurred_at=normalized.occurred_at,
                state=state,
                payload=payload,
            )
        )
        if result.inserted and self._bus is not None:
            event_type = {
                "camera_motion": EventType.CAMERA_MOTION,
                "person_detected": EventType.PERSON_DETECTED,
                "sound_detected": EventType.SOUND_DETECTED,
                "doorbell_chime": EventType.DOORBELL_CHIME,
                "camera_status": EventType.CAMERA_STATUS,
            }[normalized.event_type]
            bus_data = {
                "source": _SOURCE_KEY,
                "context_event_id": result.event_id,
                "source_event_id": normalized.source_event_id,
                "entity_id": normalized.entity_id,
                "entity_name": normalized.entity_name,
                "entity_type": "camera" if normalized.entity_domain == "camera" else normalized.entity_domain,
                "camera_event": normalized.event_type,
                "active": normalized.active,
                "status": normalized.status,
                "snapshot_available": bool(normalized.snapshot_url or normalized.video_url),
            }
            self._bus.publish(event_type, {key: value for key, value in bus_data.items() if value is not None})
        return result, external_event_id

    async def _store_snapshot_event(
        self,
        normalized: NormalizedCameraEvent,
        *,
        media_url: str,
        semantic_event_id: str,
    ) -> ApplyResult:
        try:
            metadata = await self._download_snapshot(media_url)
            event_type = "snapshot_received"
            event_bus_type = EventType.SNAPSHOT_RECEIVED
        except Exception as exc:  # noqa: BLE001 - errors are intentionally redacted
            failure_id = hashlib.sha256(media_url.encode("utf-8")).hexdigest()[:24]
            metadata = {
                "snapshot_id": f"failed-{failure_id}",
                "local_ref": f"context://snapshots/unavailable/{failure_id}",
                "status": "failed",
                "error_code": _snapshot_error_code(exc),
                "captured_at": normalized.occurred_at,
            }
            event_type = "snapshot_failed"
            event_bus_type = EventType.SNAPSHOT_FAILED

        event = ContextEvent(
            source_key=_SOURCE_KEY,
            source_type=_SOURCE_TYPE,
            source_display_name=_SOURCE_NAME,
            stale_after_seconds=self._stale_after_seconds,
            external_event_id=(
                f"{semantic_event_id}:media:{hashlib.sha256(media_url.encode('utf-8')).hexdigest()[:24]}"
                f":{metadata['status']}"
            ),
            external_entity_id=normalized.entity_id,
            entity_type="camera" if normalized.entity_domain == "camera" else normalized.entity_domain,
            entity_name=normalized.entity_name,
            entity_metadata={"domain": normalized.entity_domain, "camera_event": event_type},
            event_type=event_type,
            occurred_at=normalized.occurred_at,
            payload={"snapshot": redact_secrets(metadata)},
            snapshot_metadata=metadata,
        )
        result = self._store.apply_event(event)
        if result.inserted and self._bus is not None:
            self._bus.publish(
                event_bus_type,
                {
                    "source": _SOURCE_KEY,
                    "entity_name": normalized.entity_name,
                    "snapshot_id": metadata.get("snapshot_id"),
                    "snapshot_ref": metadata.get("local_ref"),
                    "snapshot_status": metadata.get("status"),
                    "content_type": metadata.get("content_type"),
                    "size_bytes": metadata.get("byte_size"),
                    "error_code": metadata.get("error_code"),
                },
            )
        return result

    async def _download_snapshot(self, url: str) -> dict[str, Any]:
        # Home Assistant's Nest integration emits media attachments as paths
        # such as ``/api/nest/event_media/.../thumbnail``. Resolve those
        # against the configured HA origin before handing the URL to HTTPX.
        request_url = urljoin(f"{self._url.rstrip('/')}/", url)
        headers: dict[str, str] = {}
        if self._is_local_home_assistant_url(request_url):
            headers["Authorization"] = f"Bearer {self._token}"
        if self._snapshot_fetcher is not None:
            fetched = await self._snapshot_fetcher(request_url, headers)
            if isinstance(fetched, tuple):
                content, content_type = fetched
            else:
                content, content_type = fetched, None
        else:
            try:
                import httpx
            except ImportError as exc:  # pragma: no cover - project dependency
                raise RuntimeError("httpx is required for Home Assistant snapshots") from exc
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get(request_url, headers=headers)
                response.raise_for_status()
                content = response.content
                content_type = response.headers.get("content-type")
        if not isinstance(content, bytes):
            raise ValueError("invalid_content")
        if len(content) > self._max_snapshot_bytes:
            raise ValueError("snapshot_too_large")
        normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
        if not (
            normalized_content_type.startswith("image/")
            or normalized_content_type.startswith("video/")
        ):
            guessed_content_type = mimetypes.guess_type(urlsplit(request_url).path)[0] or ""
            if guessed_content_type.startswith(("image/", "video/")):
                normalized_content_type = guessed_content_type
            elif not normalized_content_type:
                normalized_content_type = "image/jpeg"
            else:
                raise ValueError("invalid_media_type")
        snapshot_hash = hashlib.sha256(content).hexdigest()
        snapshot_id = snapshot_hash[:24]
        extension = mimetypes.guess_extension(normalized_content_type) or ".jpg"
        filename = f"{snapshot_id}{extension}"
        media_subdirectory = _media_subdirectory(normalized_content_type)
        media_dir = self._snapshot_dir / media_subdirectory
        media_dir.mkdir(parents=True, exist_ok=True)
        path = media_dir / filename
        if not path.exists():
            path.write_bytes(content)
        return {
            "snapshot_id": snapshot_id,
            "local_ref": f"context://snapshots/{media_subdirectory}/{filename}",
            "status": "received",
            "content_type": normalized_content_type,
            "byte_size": len(content),
            "sha256": snapshot_hash,
            "captured_at": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        }

    def _is_local_home_assistant_url(self, url: str) -> bool:
        configured = urlsplit(self._url)
        candidate = urlsplit(url)
        return bool(configured.netloc and candidate.netloc == configured.netloc)

    async def listen(self) -> None:
        """Fetch initial state, subscribe to all HA events, and process until close."""
        if not self.is_configured:
            raise RuntimeError("Home Assistant requires both HA_URL and HA_TOKEN")
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover - dependency is project-wide
            raise RuntimeError("Home Assistant listening requires the websockets package") from exc

        connected = False
        heartbeat_task: asyncio.Task[Any] | None = None
        try:
            async with websockets.connect(self.websocket_url, ping_interval=20, ping_timeout=20) as socket:
                await self._authenticate(socket)
                snapshot = await self._request(socket, 1, "get_states")
                if not snapshot.get("success"):
                    raise RuntimeError("get_states_failed")
                states = snapshot.get("result")
                if not isinstance(states, list):
                    raise RuntimeError("get_states_invalid")
                for state in states:
                    if isinstance(state, Mapping):
                        self.ingest_state_snapshot(state)
                        normalized = self.normalize_event(
                            {
                                "event_type": "state_changed",
                                "time_fired": state.get("last_updated") or state.get("last_changed"),
                                "data": {"entity_id": state.get("entity_id"), "new_state": state},
                            }
                        )
                        if normalized is not None and normalized.event_type == "camera_status":
                            self._apply_normalized_event(normalized)
                subscription = await self._request(socket, 2, "subscribe_events")
                if not subscription.get("success"):
                    raise RuntimeError("subscribe_events_failed")
                heartbeat_task = asyncio.create_task(self._motion_heartbeat_loop())
                connected = True
                self.record_connection_status("online")
                async for raw_message in socket:
                    message = self._decode_message(raw_message)
                    if message is not None:
                        await self.process_websocket_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - caller owns reconnect policy
            self.record_connection_status("offline", reason_code=_snapshot_error_code(exc))
            raise
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            if connected:
                self.record_connection_status("offline", reason_code="connection_closed")

    async def _authenticate(self, socket: Any) -> None:
        message = self._decode_message(await socket.recv())
        if message is None or message.get("type") != "auth_required":
            raise RuntimeError("auth_required_missing")
        await socket.send(json.dumps({"type": "auth", "access_token": self._token}))
        response = self._decode_message(await socket.recv())
        if response is None or response.get("type") != "auth_ok":
            raise RuntimeError("authentication_failed")

    async def _request(self, socket: Any, request_id: int, message_type: str, **extra: Any) -> Mapping[str, Any]:
        await socket.send(json.dumps({"id": request_id, "type": message_type, **extra}))
        while True:
            message = self._decode_message(await socket.recv())
            if message is None:
                continue
            if message.get("type") == "event":
                await self.process_websocket_message(message)
                continue
            if message.get("id") == request_id:
                return message

    @staticmethod
    def _decode_message(raw_message: str | bytes) -> Mapping[str, Any] | None:
        try:
            decoded = json.loads(raw_message)
        except (TypeError, json.JSONDecodeError):
            return None
        return decoded if isinstance(decoded, Mapping) else None


def _snapshot_error_code(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc) if str(exc) in {"invalid_content", "snapshot_too_large"} else "invalid_snapshot"
    name = type(exc).__name__.lower()
    if "http" in name or "request" in name:
        return "download_failed"
    return "snapshot_unavailable"


class HomeAssistantBridge:
    """Supervise one Home Assistant source with bounded reconnect backoff."""

    def __init__(
        self,
        store: ContextStore,
        *,
        url: str | None = None,
        token: str | None = None,
        bus: EventBus | None = None,
        snapshot_dir: str | Path | None = None,
        source_factory: Callable[[], HomeAssistantContextSource] | None = None,
        reconnect_initial_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
        sleeper: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        if reconnect_initial_seconds <= 0 or reconnect_max_seconds < reconnect_initial_seconds:
            raise ValueError("invalid reconnect backoff")
        self._store = store
        self._url = url
        self._token = token.strip() if token is not None else None
        self._bus = bus
        self._snapshot_dir = snapshot_dir
        self._source_factory = source_factory or self._build_source
        self._initial_delay = reconnect_initial_seconds
        self._max_delay = reconnect_max_seconds
        self._sleeper = sleeper
        self._stop = asyncio.Event()
        self._task: asyncio.Task[Any] | None = None

    @property
    def is_configured(self) -> bool:
        url = (self._url or _home_assistant_env(
            "HA_URL", "HOME_ASSISTANT_URL", default=_DEFAULT_URL
        )).strip()
        token = self._token or _home_assistant_env(
            "HA_TOKEN", "HOME_ASSISTANT_TOKEN"
        )
        return bool(url and token)

    def start(self) -> None:
        if self._task is None and self.is_configured:
            self._stop.clear()
            self._task = asyncio.create_task(self.run_forever())

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def run_forever(self) -> None:
        delay = self._initial_delay
        while not self._stop.is_set():
            self._publish_status("connecting")
            source = self._source_factory()
            try:
                await source.listen()
                reason_code = "connection_closed"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - only the exception type is safe to report
                reason_code = _snapshot_error_code(exc)
            else:
                delay = self._initial_delay
            self._publish_status("offline", reason_code=reason_code)
            if self._stop.is_set():
                break
            await self._sleeper(delay)
            delay = min(self._max_delay, delay * 2)

    def _build_source(self) -> HomeAssistantContextSource:
        return HomeAssistantContextSource(
            self._store,
            url=self._url,
            token=self._token,
            bus=self._bus,
            snapshot_dir=self._snapshot_dir,
        )

    def _publish_status(self, status: str, *, reason_code: str = "") -> None:
        if self._bus is not None:
            self._bus.publish(
                EventType.HOME_ASSISTANT_STATUS,
                {"source": _SOURCE_KEY, "status": status, "reason_code": reason_code},
            )


__all__ = [
    "HomeAssistantBridge",
    "HomeAssistantContextSource",
    "NormalizedCameraEvent",
    "redact_secrets",
]
