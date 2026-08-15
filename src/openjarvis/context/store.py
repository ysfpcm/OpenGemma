"""SQLite persistence for Ophanim's live context state.

The store is deliberately independent from the existing document-memory and
PostgreSQL extension tables. Connectors submit normalized events here; the
store owns idempotency, current-state projection, and state-change history.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from openjarvis.core.paths import get_data_dir

_DEFAULT_STALE_AFTER_SECONDS = 300
_VALID_SOURCE_STATUSES = {"unknown", "online", "degraded", "offline"}
_VALID_QUALITIES = {"good", "stale", "unknown", "error"}
_SAFE_SNAPSHOT_METADATA_KEYS = {
    "camera_event",
    "captured_at",
    "content_type",
    "error_code",
    "sha256",
    "snapshot_id",
    "status",
    "local_ref",
    "byte_size",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | str | None) -> str:
    """Normalize a datetime or ISO-8601 string to a UTC ``Z`` timestamp."""
    if value is None:
        value = _now()
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("timestamp cannot be empty")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    else:
        raise TypeError("timestamp must be a datetime, ISO-8601 string, or None")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _json_text(value: Any, *, field_name: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON serializable") from exc


def _json_value(value: str) -> Any:
    return json.loads(value)


def _validate_non_empty(value: str, field_name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field_name} cannot be empty")
    return result


@dataclass(frozen=True, slots=True)
class StateValue:
    """A current property value and its optional display metadata."""

    value: Any
    unit: str = ""
    quality: str = "good"

    def __post_init__(self) -> None:
        if self.quality not in _VALID_QUALITIES:
            raise ValueError(
                f"quality must be one of {sorted(_VALID_QUALITIES)}, got {self.quality!r}"
            )


@dataclass(frozen=True, slots=True)
class ContextEvent:
    """Normalized event submitted by a live-data connector."""

    source_key: str
    source_type: str = "unknown"
    source_display_name: str = ""
    source_status: str = "online"
    stale_after_seconds: int | None = None
    external_event_id: str | None = None
    external_entity_id: str | None = None
    entity_type: str | None = None
    entity_name: str | None = None
    area: str = ""
    entity_metadata: Mapping[str, Any] = field(default_factory=dict)
    event_type: str = "state_changed"
    occurred_at: datetime | str | None = None
    state: Mapping[str, StateValue | Any] = field(default_factory=dict)
    payload: Mapping[str, Any] = field(default_factory=dict)
    # Safe, local snapshot metadata. The source URL is deliberately never a
    # field here; connectors must download expiring URLs before applying this
    # event and provide only a local reference plus content metadata.
    snapshot_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """Outcome of applying one event."""

    event_id: int
    source_id: int
    entity_id: int | None
    inserted: bool
    duplicate: bool
    changed_state_keys: tuple[str, ...] = ()
    updated_state_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CurrentStateRecord:
    """Flat current-state row used by the context builder."""

    entity_id: int
    source_key: str
    source_display_name: str
    source_status: str
    stale_after_seconds: int
    source_last_seen_at: str | None
    external_entity_id: str
    entity_type: str
    entity_name: str
    area: str
    state_key: str
    value: Any
    unit: str
    quality: str
    observed_at: str


@dataclass(frozen=True, slots=True)
class RecentEventRecord:
    """Event row used in a context snapshot."""

    event_id: int
    source_key: str
    entity_name: str | None
    area: str | None
    event_type: str
    occurred_at: str
    payload: Any


@dataclass(frozen=True, slots=True)
class SourceHealth:
    """Source status used for freshness warnings."""

    source_key: str
    display_name: str
    status: str
    stale_after_seconds: int
    last_seen_at: str | None
    last_sync_at: str | None


class ContextStore:
    """Thread-safe SQLite repository for live context state."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            path: str | Path = get_data_dir() / "context.db"
        else:
            path = db_path

        if isinstance(path, Path) or path != ":memory:":
            path_obj = Path(path).expanduser()
            path_obj.parent.mkdir(parents=True, exist_ok=True)
            connect_path = str(path_obj)
            self._path: Path | None = path_obj
        else:
            connect_path = ":memory:"
            self._path = None

        self._conn = sqlite3.connect(
            connect_path,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._configure_connection()
        self.initialize()

    @property
    def path(self) -> Path | None:
        """Filesystem path, or ``None`` for an in-memory store."""
        return self._path

    def _configure_connection(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.execute("PRAGMA journal_mode = WAL")

    def initialize(self) -> None:
        """Create the context schema if it does not already exist."""
        schema_path = Path(__file__).resolve().parents[1] / "db" / "context_schema.sql"
        schema = schema_path.read_text(encoding="utf-8")
        with self._lock:
            self._conn.executescript(schema)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "ContextStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    @staticmethod
    def _normalize_state(
        state: Mapping[str, StateValue | Any],
    ) -> dict[str, StateValue]:
        normalized: dict[str, StateValue] = {}
        for raw_key, raw_value in state.items():
            key = _validate_non_empty(str(raw_key), "state key")
            normalized[key] = (
                raw_value if isinstance(raw_value, StateValue) else StateValue(raw_value)
            )
        return dict(sorted(normalized.items()))

    def _upsert_source(self, cursor: sqlite3.Cursor, event: ContextEvent, now: str) -> int:
        source_key = _validate_non_empty(event.source_key, "source_key")
        existing = cursor.execute(
            """
            SELECT source_type, display_name, stale_after_seconds
            FROM context_sources
            WHERE source_key = ?
            """,
            (source_key,),
        ).fetchone()
        source_type = _validate_non_empty(
            event.source_type
            if event.source_type != "unknown" or existing is None
            else existing["source_type"],
            "source_type",
        )
        display_name = (
            event.source_display_name
            or (existing["display_name"] if existing is not None else source_key)
        ).strip()
        status = event.source_status or "online"
        if status not in _VALID_SOURCE_STATUSES:
            raise ValueError(
                f"source_status must be one of {sorted(_VALID_SOURCE_STATUSES)}, got {status!r}"
            )
        stale_after = (
            int(existing["stale_after_seconds"])
            if event.stale_after_seconds is None and existing is not None
            else (
                _DEFAULT_STALE_AFTER_SECONDS
                if event.stale_after_seconds is None
                else int(event.stale_after_seconds)
            )
        )
        if stale_after <= 0:
            raise ValueError("stale_after_seconds must be greater than zero")
        metadata_json = _json_text({}, field_name="source metadata")

        cursor.execute(
            """
            INSERT INTO context_sources (
                source_key, source_type, display_name, status,
                stale_after_seconds, last_seen_at, last_sync_at,
                metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                source_type = excluded.source_type,
                display_name = excluded.display_name,
                status = excluded.status,
                stale_after_seconds = excluded.stale_after_seconds,
                last_seen_at = excluded.last_seen_at,
                last_sync_at = excluded.last_sync_at,
                updated_at = excluded.updated_at
            """,
            (
                source_key,
                source_type,
                display_name,
                status,
                stale_after,
                now,
                now,
                metadata_json,
                now,
                now,
            ),
        )
        row = cursor.execute(
            "SELECT id FROM context_sources WHERE source_key = ?",
            (source_key,),
        ).fetchone()
        assert row is not None
        return int(row["id"])

    def _upsert_entity(
        self,
        cursor: sqlite3.Cursor,
        event: ContextEvent,
        source_id: int,
        now: str,
    ) -> int | None:
        if not event.external_entity_id:
            if event.state:
                raise ValueError("state-bearing events require external_entity_id")
            return None

        external_id = _validate_non_empty(event.external_entity_id, "external_entity_id")
        existing = cursor.execute(
            """
            SELECT entity_type, display_name, area, metadata_json
            FROM context_entities
            WHERE source_id = ? AND external_id = ?
            """,
            (source_id, external_id),
        ).fetchone()
        entity_type = _validate_non_empty(
            event.entity_type
            or (existing["entity_type"] if existing is not None else "unknown"),
            "entity_type",
        )
        display_name = _validate_non_empty(
            event.entity_name
            or (existing["display_name"] if existing is not None else external_id),
            "entity_name",
        )
        area = (
            event.area
            if event.area
            else (existing["area"] if existing is not None else "")
        ).strip()
        metadata_json = (
            _json_text(event.entity_metadata, field_name="entity metadata")
            if event.entity_metadata
            else (existing["metadata_json"] if existing is not None else "{}")
        )

        cursor.execute(
            """
            INSERT INTO context_entities (
                source_id, external_id, entity_type, display_name, area,
                metadata_json, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, external_id) DO UPDATE SET
                entity_type = excluded.entity_type,
                display_name = excluded.display_name,
                area = excluded.area,
                metadata_json = excluded.metadata_json,
                last_seen_at = excluded.last_seen_at
            """,
            (
                source_id,
                external_id,
                entity_type,
                display_name,
                area,
                metadata_json,
                now,
                now,
            ),
        )
        row = cursor.execute(
            """
            SELECT id FROM context_entities
            WHERE source_id = ? AND external_id = ?
            """,
            (source_id, external_id),
        ).fetchone()
        assert row is not None
        return int(row["id"])

    @staticmethod
    def _duplicate_result(row: sqlite3.Row, source_id: int) -> ApplyResult:
        """Build a duplicate result without mutating current state or history."""
        return ApplyResult(
            event_id=int(row["id"]),
            source_id=source_id,
            entity_id=(
                int(row["entity_id"]) if row["entity_id"] is not None else None
            ),
            inserted=False,
            duplicate=True,
        )

    def apply_event(self, event: ContextEvent) -> ApplyResult:
        """Apply an event atomically and update the current-state projection."""
        occurred_at = _timestamp(event.occurred_at)
        received_at = _timestamp(None)
        state = self._normalize_state(event.state)
        payload: dict[str, Any] = dict(event.payload)
        if state and "state" not in payload:
            payload["state"] = {
                key: {"value": value.value, "unit": value.unit}
                for key, value in state.items()
            }
        payload_json = _json_text(payload, field_name="event payload")

        with self._lock:
            with self._conn:
                cursor = self._conn.cursor()
                # Do this before the source upsert. A replayed event must be a
                # no-op, including for source health fields such as status and
                # last_seen_at.
                source_key = _validate_non_empty(event.source_key, "source_key")
                if event.external_event_id:
                    source = cursor.execute(
                        "SELECT id FROM context_sources WHERE source_key = ?",
                        (source_key,),
                    ).fetchone()
                    if source is not None:
                        source_id = int(source["id"])
                        existing = cursor.execute(
                            """
                            SELECT id, entity_id FROM context_events
                            WHERE source_id = ? AND external_event_id = ?
                            """,
                            (source_id, event.external_event_id),
                        ).fetchone()
                        if existing is not None:
                            return self._duplicate_result(existing, source_id)

                source_id = self._upsert_source(cursor, event, received_at)

                if event.external_event_id:
                    existing = cursor.execute(
                        """
                        SELECT id, entity_id FROM context_events
                        WHERE source_id = ? AND external_event_id = ?
                        """,
                        (source_id, event.external_event_id),
                    ).fetchone()
                    if existing is not None:
                        return self._duplicate_result(existing, source_id)

                entity_id = self._upsert_entity(cursor, event, source_id, received_at)
                state_key = next(iter(state)) if len(state) == 1 else None
                try:
                    cursor.execute(
                        """
                        INSERT INTO context_events (
                            source_id, entity_id, event_type, state_key,
                            external_event_id, occurred_at, received_at, payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source_id,
                            entity_id,
                            _validate_non_empty(event.event_type, "event_type"),
                            state_key,
                            event.external_event_id,
                            occurred_at,
                            received_at,
                            payload_json,
                        ),
                    )
                except sqlite3.IntegrityError:
                    if not event.external_event_id:
                        raise
                    existing = cursor.execute(
                        """
                        SELECT id, entity_id FROM context_events
                        WHERE source_id = ? AND external_event_id = ?
                        """,
                        (source_id, event.external_event_id),
                    ).fetchone()
                    if existing is None:
                        raise
                    return self._duplicate_result(existing, source_id)

                event_id = int(cursor.lastrowid)
                changed: list[str] = []
                updated: list[str] = []

                if event.snapshot_metadata:
                    snapshot = dict(event.snapshot_metadata)
                    snapshot_id = _validate_non_empty(
                        str(snapshot.get("snapshot_id", "")),
                        "snapshot_metadata.snapshot_id",
                    )
                    local_ref = _validate_non_empty(
                        str(snapshot.get("local_ref", "")),
                        "snapshot_metadata.local_ref",
                    )
                    if not local_ref.startswith("context://"):
                        raise ValueError(
                            "snapshot_metadata.local_ref must use the context:// scheme"
                        )
                    status = str(snapshot.get("status", ""))
                    if status not in {"received", "failed"}:
                        raise ValueError(
                            "snapshot_metadata.status must be 'received' or 'failed'"
                        )
                    metadata_json = _json_text(
                        {
                            key: value
                            for key, value in snapshot.items()
                            if key in _SAFE_SNAPSHOT_METADATA_KEYS
                            and key
                            not in {
                                "snapshot_id",
                                "local_ref",
                                "status",
                                "content_type",
                                "byte_size",
                                "sha256",
                                "error_code",
                                "captured_at",
                            }
                        },
                        field_name="snapshot metadata",
                    )
                    cursor.execute(
                        """
                        INSERT INTO context_snapshots (
                            source_id, entity_id, event_id, snapshot_id, local_ref,
                            status, content_type, byte_size, sha256, error_code,
                            captured_at, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(snapshot_id) DO NOTHING
                        """,
                        (
                            source_id,
                            entity_id,
                            event_id,
                            snapshot_id,
                            local_ref,
                            status,
                            snapshot.get("content_type"),
                            snapshot.get("byte_size"),
                            snapshot.get("sha256"),
                            snapshot.get("error_code"),
                            _timestamp(snapshot.get("captured_at") or occurred_at),
                            metadata_json,
                        ),
                    )

                if entity_id is not None:
                    for key, state_value in state.items():
                        value_json = _json_text(state_value.value, field_name=f"state[{key}]")
                        current = cursor.execute(
                            """
                            SELECT value_json, unit, quality, observed_at
                            FROM context_current_state
                            WHERE entity_id = ? AND state_key = ?
                            """,
                            (entity_id, key),
                        ).fetchone()

                        if current is not None and occurred_at < current["observed_at"]:
                            continue

                        previous_json = current["value_json"] if current is not None else None
                        value_changed = current is None or (
                            current["value_json"] != value_json
                            or current["unit"] != state_value.unit
                        )
                        current_changed = value_changed or current is None or (
                            current["quality"] != state_value.quality
                            or current["observed_at"] != occurred_at
                        )

                        if value_changed:
                            cursor.execute(
                                """
                                INSERT INTO context_state_history (
                                    entity_id, state_key, previous_value_json,
                                    new_value_json, unit, event_id, occurred_at, recorded_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    entity_id,
                                    key,
                                    previous_json,
                                    value_json,
                                    state_value.unit,
                                    event_id,
                                    occurred_at,
                                    received_at,
                                ),
                            )
                            changed.append(key)

                        if current_changed:
                            cursor.execute(
                                """
                                INSERT INTO context_current_state (
                                    entity_id, state_key, value_json, unit,
                                    quality, observed_at, received_at, event_id
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(entity_id, state_key) DO UPDATE SET
                                    value_json = excluded.value_json,
                                    unit = excluded.unit,
                                    quality = excluded.quality,
                                    observed_at = excluded.observed_at,
                                    received_at = excluded.received_at,
                                    event_id = excluded.event_id
                                """,
                                (
                                    entity_id,
                                    key,
                                    value_json,
                                    state_value.unit,
                                    state_value.quality,
                                    occurred_at,
                                    received_at,
                                    event_id,
                                ),
                            )
                            updated.append(key)

                return ApplyResult(
                    event_id=event_id,
                    source_id=source_id,
                    entity_id=entity_id,
                    inserted=True,
                    duplicate=False,
                    changed_state_keys=tuple(changed),
                    updated_state_keys=tuple(updated),
                )

    @staticmethod
    def _selector_sql(
        *,
        source_keys: Sequence[str] = (),
        areas: Sequence[str] = (),
        entity_types: Sequence[str] = (),
        include_disabled: bool = False,
    ) -> tuple[str, list[str]]:
        clauses: list[str] = []
        params: list[str] = []
        if source_keys:
            placeholders = ", ".join("?" for _ in source_keys)
            clauses.append(f"s.source_key IN ({placeholders})")
            params.extend(str(value) for value in source_keys)
        if areas:
            placeholders = ", ".join("?" for _ in areas)
            clauses.append(f"e.area IN ({placeholders})")
            params.extend(str(value) for value in areas)
        if entity_types:
            placeholders = ", ".join("?" for _ in entity_types)
            clauses.append(f"e.entity_type IN ({placeholders})")
            params.extend(str(value) for value in entity_types)
        if not include_disabled:
            clauses.append("e.enabled = 1")
        return (" AND ".join(clauses) or "1 = 1", params)

    def get_current_state(
        self,
        *,
        source_keys: Sequence[str] = (),
        areas: Sequence[str] = (),
        entity_types: Sequence[str] = (),
        include_disabled: bool = False,
    ) -> list[CurrentStateRecord]:
        where, params = self._selector_sql(
            source_keys=source_keys,
            areas=areas,
            entity_types=entity_types,
            include_disabled=include_disabled,
        )
        query = f"""
            SELECT
                e.id AS entity_id,
                s.source_key,
                s.display_name AS source_display_name,
                s.status AS source_status,
                s.stale_after_seconds,
                s.last_seen_at AS source_last_seen_at,
                e.external_id AS external_entity_id,
                e.entity_type,
                e.display_name AS entity_name,
                e.area,
                c.state_key,
                c.value_json,
                c.unit,
                c.quality,
                c.observed_at
            FROM context_current_state c
            JOIN context_entities e ON e.id = c.entity_id
            JOIN context_sources s ON s.id = e.source_id
            WHERE {where}
            ORDER BY COALESCE(e.area, ''), e.display_name, c.state_key
        """
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            CurrentStateRecord(
                entity_id=int(row["entity_id"]),
                source_key=row["source_key"],
                source_display_name=row["source_display_name"],
                source_status=row["source_status"],
                stale_after_seconds=int(row["stale_after_seconds"]),
                source_last_seen_at=row["source_last_seen_at"],
                external_entity_id=row["external_entity_id"],
                entity_type=row["entity_type"],
                entity_name=row["entity_name"],
                area=row["area"],
                state_key=row["state_key"],
                value=_json_value(row["value_json"]),
                unit=row["unit"],
                quality=row["quality"],
                observed_at=row["observed_at"],
            )
            for row in rows
        ]

    def get_recent_events(
        self,
        *,
        limit: int = 20,
        source_keys: Sequence[str] = (),
        areas: Sequence[str] = (),
        entity_types: Sequence[str] = (),
        include_disabled: bool = False,
    ) -> list[RecentEventRecord]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if limit == 0:
            return []
        where, params = self._selector_sql(
            source_keys=source_keys,
            areas=areas,
            entity_types=entity_types,
            include_disabled=include_disabled,
        )
        query = f"""
            SELECT
                ev.id AS event_id,
                s.source_key,
                e.display_name AS entity_name,
                e.area,
                ev.event_type,
                ev.occurred_at,
                ev.payload_json
            FROM context_events ev
            JOIN context_sources s ON s.id = ev.source_id
            LEFT JOIN context_entities e ON e.id = ev.entity_id
            WHERE {where}
            ORDER BY ev.occurred_at DESC, ev.id DESC
            LIMIT ?
        """
        # Events without an entity are source-level events and should still be
        # visible when no entity filters are requested.
        if not (areas or entity_types) and not include_disabled:
            where = where.replace("e.enabled = 1", "(e.enabled = 1 OR e.id IS NULL)")
            query = f"""
                SELECT
                    ev.id AS event_id,
                    s.source_key,
                    e.display_name AS entity_name,
                    e.area,
                    ev.event_type,
                    ev.occurred_at,
                    ev.payload_json
                FROM context_events ev
                JOIN context_sources s ON s.id = ev.source_id
                LEFT JOIN context_entities e ON e.id = ev.entity_id
                WHERE {where}
                ORDER BY ev.occurred_at DESC, ev.id DESC
                LIMIT ?
            """
        with self._lock:
            rows = self._conn.execute(query, [*params, limit]).fetchall()
        return [
            RecentEventRecord(
                event_id=int(row["event_id"]),
                source_key=row["source_key"],
                entity_name=row["entity_name"],
                area=row["area"],
                event_type=row["event_type"],
                occurred_at=row["occurred_at"],
                payload=_json_value(row["payload_json"]),
            )
            for row in rows
        ]

    def get_source_health(
        self,
        *,
        source_keys: Sequence[str] = (),
    ) -> list[SourceHealth]:
        params: list[str] = []
        where = "1 = 1"
        if source_keys:
            placeholders = ", ".join("?" for _ in source_keys)
            where = f"source_key IN ({placeholders})"
            params.extend(str(value) for value in source_keys)
        query = f"""
            SELECT source_key, display_name, status, stale_after_seconds,
                   last_seen_at, last_sync_at
            FROM context_sources
            WHERE {where}
            ORDER BY source_key
        """
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            SourceHealth(
                source_key=row["source_key"],
                display_name=row["display_name"],
                status=row["status"],
                stale_after_seconds=int(row["stale_after_seconds"]),
                last_seen_at=row["last_seen_at"],
                last_sync_at=row["last_sync_at"],
            )
            for row in rows
        ]

    def get_history(
        self,
        entity_id: int,
        state_key: str | None = None,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        clauses = ["entity_id = ?"]
        params: list[Any] = [int(entity_id)]
        if state_key is not None:
            clauses.append("state_key = ?")
            params.append(state_key)
        query = f"""
            SELECT id, entity_id, state_key, previous_value_json,
                   new_value_json, unit, event_id, occurred_at, recorded_at
            FROM context_state_history
            WHERE {' AND '.join(clauses)}
            ORDER BY occurred_at DESC, id DESC
            LIMIT ?
        """
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            {
                "id": int(row["id"]),
                "entity_id": int(row["entity_id"]),
                "state_key": row["state_key"],
                "previous_value": (
                    _json_value(row["previous_value_json"])
                    if row["previous_value_json"] is not None
                    else None
                ),
                "new_value": _json_value(row["new_value_json"]),
                "unit": row["unit"],
                "event_id": row["event_id"],
                "occurred_at": row["occurred_at"],
                "recorded_at": row["recorded_at"],
            }
            for row in rows
        ]

    def get_snapshots(
        self,
        *,
        limit: int = 100,
        source_keys: Sequence[str] = (),
        entity_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return safe local snapshot metadata, never the source URL."""
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if limit == 0:
            return []
        clauses = ["1 = 1"]
        params: list[Any] = []
        if source_keys:
            placeholders = ", ".join("?" for _ in source_keys)
            clauses.append(f"s.source_key IN ({placeholders})")
            params.extend(str(value) for value in source_keys)
        if entity_id is not None:
            clauses.append("sn.entity_id = ?")
            params.append(int(entity_id))
        query = f"""
            SELECT sn.id, s.source_key, e.display_name AS entity_name,
                   sn.event_id, sn.snapshot_id, sn.local_ref, sn.status,
                   sn.content_type, sn.byte_size, sn.sha256, sn.error_code,
                   sn.captured_at, sn.created_at, sn.metadata_json
            FROM context_snapshots sn
            JOIN context_sources s ON s.id = sn.source_id
            LEFT JOIN context_entities e ON e.id = sn.entity_id
            WHERE {' AND '.join(clauses)}
            ORDER BY sn.captured_at DESC, sn.id DESC
            LIMIT ?
        """
        with self._lock:
            rows = self._conn.execute(query, [*params, limit]).fetchall()
        return [
            {
                "id": int(row["id"]),
                "source_key": row["source_key"],
                "entity_name": row["entity_name"],
                "event_id": row["event_id"],
                "snapshot_id": row["snapshot_id"],
                "local_ref": row["local_ref"],
                "status": row["status"],
                "content_type": row["content_type"],
                "byte_size": row["byte_size"],
                "sha256": row["sha256"],
                "error_code": row["error_code"],
                "captured_at": row["captured_at"],
                "created_at": row["created_at"],
                "metadata": _json_value(row["metadata_json"]),
            }
            for row in rows
        ]


__all__ = [
    "ApplyResult",
    "ContextEvent",
    "ContextStore",
    "CurrentStateRecord",
    "RecentEventRecord",
    "SourceHealth",
    "StateValue",
]
