"""SQLite-backed durable events, consumer leases, and replay state."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    ConsumerDelivery,
    DurableEvent,
    JournalAppendResult,
    canonical_json,
    utc_iso,
)


@dataclass(frozen=True)
class _DeliveryRow:
    consumer: str
    event_id: str
    status: str
    attempts: int
    worker_id: str | None
    lease_until: float | None
    last_error: str | None


class DurableEventJournal:
    """The Phase 4 autonomy boundary.

    ``append`` commits the normalized event before any detector can inspect it.
    The journal contains no executor or Guardian callback by design.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        raw_payload_retention_seconds: int = 0,
    ) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_payload_retention_seconds = max(0, raw_payload_retention_seconds)
        self._conn = sqlite3.connect(
            str(self.path), isolation_level=None, check_same_thread=False
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            BEGIN;
            CREATE TABLE IF NOT EXISTS phase4_schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase4_events (
                event_id TEXT NOT NULL UNIQUE,
                identity_key TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                observed_at TEXT NOT NULL,
                ingested_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                sensitivity_json TEXT NOT NULL,
                taint_json TEXT NOT NULL,
                quality TEXT NOT NULL,
                sequence INTEGER PRIMARY KEY AUTOINCREMENT
            );
            CREATE INDEX IF NOT EXISTS phase4_events_observed_idx
                ON phase4_events(observed_at, sequence, event_id);
            CREATE TABLE IF NOT EXISTS phase4_deliveries (
                consumer TEXT NOT NULL,
                event_id TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                worker_id TEXT,
                lease_until REAL,
                last_error TEXT,
                acked_at REAL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(consumer, event_id),
                FOREIGN KEY(event_id) REFERENCES phase4_events(event_id)
            );
            CREATE TABLE IF NOT EXISTS phase4_raw_event_payloads (
                event_id TEXT PRIMARY KEY,
                raw_payload_json TEXT NOT NULL,
                raw_expires_at TEXT NOT NULL,
                FOREIGN KEY(event_id) REFERENCES phase4_events(event_id)
            );
            CREATE TABLE IF NOT EXISTS phase4_situations (
                situation_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL,
                body_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase4_evaluations (
                evaluation_key TEXT PRIMARY KEY,
                body_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS phase4_events_immutable_delete
            BEFORE DELETE ON phase4_events BEGIN
                SELECT RAISE(ABORT, 'phase4 events are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS phase4_events_immutable_update
            BEFORE UPDATE ON phase4_events BEGIN
                SELECT RAISE(ABORT, 'phase4 events are immutable');
            END;
            INSERT OR IGNORE INTO phase4_schema_migrations(version, applied_at)
                VALUES (1, CURRENT_TIMESTAMP);
            COMMIT;
            """
        )

    def append(
        self,
        event: DurableEvent,
        *,
        retained_at: float | None = None,
    ) -> JournalAppendResult:
        """Durably append an event and return whether this identity was new."""

        now = time.time() if retained_at is None else retained_at
        raw_json: str | None = None
        raw_expires_at: str | None = None
        if (
            event.raw_payload is not None
            and not event.sensitive
            and not event.has_sensitive_keys
            and not event.raw_payload_has_sensitive_keys
        ):
            if self.raw_payload_retention_seconds > 0:
                raw_json = canonical_json(event.raw_payload)
                raw_expires_at = utc_iso(
                    datetime.fromtimestamp(
                        now + self.raw_payload_retention_seconds, timezone.utc
                    )
                )
        values = (
            event.event_id,
            event.identity_key,
            event.event_type,
            event.source,
            event.source_event_id,
            event.schema_version,
            event.observed_at,
            event.ingested_at,
            canonical_json(event.normalized_payload()),
            canonical_json(event.provenance),
            canonical_json(list(event.sensitivity_labels)),
            canonical_json(list(event.taint_labels)),
            event.quality.value,
        )
        with self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO phase4_events("
                "event_id, identity_key, event_type, source, source_event_id, "
                "schema_version, observed_at, ingested_at, payload_json, "
                "provenance_json, sensitivity_json, taint_json, quality) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            if (
                cursor.rowcount == 1
                and raw_json is not None
                and raw_expires_at is not None
            ):
                self._conn.execute(
                    "INSERT INTO phase4_raw_event_payloads("
                    "event_id, raw_payload_json, raw_expires_at) "
                    "VALUES (?, ?, ?)",
                    (event.event_id, raw_json, raw_expires_at),
                )
            row = self._conn.execute(
                "SELECT event_id FROM phase4_events WHERE identity_key = ?",
                (event.identity_key,),
            ).fetchone()
        if row is None:
            raise RuntimeError("event append did not produce a durable row")
        stored = self.get_event(row[0])
        return JournalAppendResult(stored, cursor.rowcount == 1)

    def get_event(self, event_id: str) -> DurableEvent:
        row = self._conn.execute(
            "SELECT event_id, identity_key, event_type, source, source_event_id, "
            "schema_version, observed_at, ingested_at, payload_json, provenance_json, "
            "sensitivity_json, taint_json, quality "
            "FROM phase4_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            raise KeyError(event_id)
        return DurableEvent(
            event_id=row[0],
            identity_key=row[1],
            event_type=row[2],
            source=row[3],
            source_event_id=row[4],
            schema_version=row[5],
            observed_at=row[6],
            ingested_at=row[7],
            payload=json.loads(row[8]),
            provenance=json.loads(row[9]),
            sensitivity_labels=tuple(json.loads(row[10])),
            taint_labels=tuple(json.loads(row[11])),
            quality=row[12],
        )

    def events(self) -> list[DurableEvent]:
        rows = self._conn.execute(
            "SELECT event_id FROM phase4_events "
            "ORDER BY observed_at, sequence, event_id"
        ).fetchall()
        return [self.get_event(row[0]) for row in rows]

    def raw_payload(self, event_id: str, *, now: float | None = None) -> Any:
        row = self._conn.execute(
            "SELECT raw_payload_json, raw_expires_at "
            "FROM phase4_raw_event_payloads WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            if (
                self._conn.execute(
                    "SELECT 1 FROM phase4_events WHERE event_id = ?", (event_id,)
                ).fetchone()
                is None
            ):
                raise KeyError(event_id)
            return None
        if row[0] is None:
            return None
        current_iso = utc_iso(
            datetime.fromtimestamp(
                time.time() if now is None else now,
                timezone.utc,
            )
        )
        if row[1] <= current_iso:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM phase4_raw_event_payloads WHERE event_id = ?",
                    (event_id,),
                )
            return None
        return json.loads(row[0])

    def purge_expired_raw(self, *, now: float | None = None) -> int:
        now_iso = utc_iso(
            datetime.fromtimestamp(
                now if now is not None else time.time(), timezone.utc
            )
        )
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM phase4_raw_event_payloads WHERE raw_expires_at <= ?",
                (now_iso,),
            )
        return cursor.rowcount

    def claim(
        self,
        consumer: str,
        worker_id: str,
        *,
        limit: int = 10,
        now: float | None = None,
        lease_seconds: float = 30.0,
        max_attempts: int = 3,
    ) -> list[ConsumerDelivery]:
        if not consumer or not worker_id:
            raise ValueError("consumer and worker_id are required")
        if limit < 1 or lease_seconds <= 0 or max_attempts < 1:
            raise ValueError("invalid lease or retry bounds")
        current = time.time() if now is None else now
        claimed: list[ConsumerDelivery] = []
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            rows = self._conn.execute(
                "SELECT e.event_id, d.status, COALESCE(d.attempts, 0), d.worker_id, "
                "d.lease_until, d.last_error FROM phase4_events e "
                "LEFT JOIN phase4_deliveries d ON d.event_id = e.event_id "
                "AND d.consumer = ? "
                "WHERE d.status IS NULL OR d.status = 'pending' "
                "OR (d.status = 'leased' AND d.lease_until <= ?) "
                "ORDER BY e.observed_at, e.sequence, e.event_id LIMIT ?",
                (consumer, current, limit),
            ).fetchall()
            for row in rows:
                event_id, status, attempts, _, _, _ = row
                if attempts >= max_attempts:
                    self._set_delivery(
                        consumer,
                        event_id,
                        status="dead",
                        attempts=attempts,
                        worker_id=None,
                        lease_until=None,
                        last_error="max-attempts-exceeded",
                        now=current,
                    )
                    continue
                attempts += 1
                lease_until = current + lease_seconds
                self._set_delivery(
                    consumer,
                    event_id,
                    status="leased",
                    attempts=attempts,
                    worker_id=worker_id,
                    lease_until=lease_until,
                    last_error=None,
                    now=current,
                )
                claimed.append(
                    ConsumerDelivery(
                        consumer=consumer,
                        worker_id=worker_id,
                        event=self.get_event(event_id),
                        attempts=attempts,
                        lease_until=lease_until,
                    )
                )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return claimed

    def _set_delivery(
        self,
        consumer: str,
        event_id: str,
        *,
        status: str,
        attempts: int,
        worker_id: str | None,
        lease_until: float | None,
        last_error: str | None,
        now: float,
    ) -> None:
        self._conn.execute(
            "INSERT INTO phase4_deliveries("
            "consumer, event_id, status, attempts, worker_id, "
            "lease_until, last_error, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(consumer, event_id) DO UPDATE SET status=excluded.status, "
            "attempts=excluded.attempts, worker_id=excluded.worker_id, "
            "lease_until=excluded.lease_until, last_error=excluded.last_error, "
            "updated_at=excluded.updated_at",
            (
                consumer,
                event_id,
                status,
                attempts,
                worker_id,
                lease_until,
                last_error,
                now,
            ),
        )

    def ack(
        self, consumer: str, event_id: str, worker_id: str, *, now: float | None = None
    ) -> None:
        current = time.time() if now is None else now
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE phase4_deliveries SET status='acked', worker_id=NULL, "
                "lease_until=NULL, acked_at=?, updated_at=? "
                "WHERE consumer=? AND event_id=? "
                "AND status='leased' AND worker_id=? AND lease_until > ?",
                (current, current, consumer, event_id, worker_id, current),
            )
        if cursor.rowcount != 1:
            raise ValueError("delivery is not leased by this worker")

    def nack(
        self,
        consumer: str,
        event_id: str,
        worker_id: str,
        error: str,
        *,
        now: float | None = None,
        max_attempts: int = 3,
    ) -> str:
        current = time.time() if now is None else now
        row = self._delivery_row(consumer, event_id)
        if (
            row is None
            or row.status != "leased"
            or row.worker_id != worker_id
            or row.lease_until is None
            or row.lease_until <= current
        ):
            raise ValueError("delivery is not leased by this worker")
        status = "dead" if row.attempts >= max_attempts else "pending"
        with self._conn:
            self._set_delivery(
                consumer,
                event_id,
                status=status,
                attempts=row.attempts,
                worker_id=None,
                lease_until=None,
                last_error=error,
                now=current,
            )
        return status

    def delivery_state(self, consumer: str, event_id: str) -> dict[str, Any] | None:
        row = self._delivery_row(consumer, event_id)
        if row is None:
            return None
        return {
            "consumer": row.consumer,
            "event_id": row.event_id,
            "status": row.status,
            "attempts": row.attempts,
            "worker_id": row.worker_id,
            "lease_until": row.lease_until,
            "last_error": row.last_error,
        }

    def _delivery_row(self, consumer: str, event_id: str) -> _DeliveryRow | None:
        row = self._conn.execute(
            "SELECT consumer, event_id, status, attempts, worker_id, "
            "lease_until, last_error "
            "FROM phase4_deliveries WHERE consumer=? AND event_id=?",
            (consumer, event_id),
        ).fetchone()
        return _DeliveryRow(*row) if row else None

    def dead_letters(self, consumer: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT event_id, attempts, last_error, updated_at FROM phase4_deliveries "
            "WHERE consumer=? AND status='dead' ORDER BY updated_at, event_id",
            (consumer,),
        ).fetchall()
        return [
            {
                "event_id": row[0],
                "attempts": row[1],
                "last_error": row[2],
                "updated_at": row[3],
            }
            for row in rows
        ]

    def redrive(self, consumer: str, event_id: str) -> None:
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE phase4_deliveries SET status='pending', attempts=0, "
                "worker_id=NULL, lease_until=NULL, last_error=NULL, updated_at=? "
                "WHERE consumer=? AND event_id=? "
                "AND status='dead'",
                (time.time(), consumer, event_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("only dead-lettered deliveries can be redriven")

    def save_situation(self, situation: Any, *, idempotency_key: str) -> bool:
        body = situation.to_dict()
        status = getattr(situation.status, "value", situation.status)
        now = utc_iso()
        existing = self._conn.execute(
            "SELECT body_json, revision FROM phase4_situations WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if existing and json.loads(existing[0]) == body:
            return False
        revision = int(existing[1]) + 1 if existing else 1
        with self._conn:
            self._conn.execute(
                "INSERT INTO phase4_situations("
                "situation_id, idempotency_key, status, revision, "
                "body_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(idempotency_key) DO UPDATE SET "
                "status=excluded.status, revision=excluded.revision, "
                "body_json=excluded.body_json, updated_at=excluded.updated_at",
                (
                    situation.id,
                    idempotency_key,
                    status,
                    revision,
                    canonical_json(body),
                    now,
                ),
            )
        return True

    def situations(self, *, status: str | None = None) -> list[Any]:
        from openjarvis.cognition import CognitionContract

        query = "SELECT body_json FROM phase4_situations"
        args: tuple[Any, ...] = ()
        if status:
            query += " WHERE status=?"
            args = (status,)
        query += " ORDER BY updated_at, situation_id"
        rows = self._conn.execute(query, args).fetchall()
        return [CognitionContract.from_json(row[0]) for row in rows]

    def save_evaluation(self, evaluation_key: str, body: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO phase4_evaluations("
                "evaluation_key, body_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(evaluation_key) DO UPDATE SET "
                "body_json=excluded.body_json, updated_at=excluded.updated_at",
                (evaluation_key, canonical_json(body), utc_iso()),
            )

    def evaluations(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT evaluation_key, body_json, updated_at "
            "FROM phase4_evaluations ORDER BY evaluation_key"
        ).fetchall()
        return [
            {"evaluation_key": row[0], **json.loads(row[1]), "updated_at": row[2]}
            for row in rows
        ]

    def close(self) -> None:
        self._conn.close()


__all__ = ["ConsumerDelivery", "DurableEventJournal", "JournalAppendResult"]
