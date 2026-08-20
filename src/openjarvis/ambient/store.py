"""Durable, additive Phase 11 SQLite ledger."""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .contracts import (
    AmbientContract,
    ContinuityAcknowledgment,
    MissionSummary,
    RemoteSteering,
    SteeringStatus,
    contract_from_dict,
    utc_now,
)

MIGRATION_VERSION = 1


class AmbientStore:
    """A Phase-11-only ledger; unrelated application tables are untouched."""

    def __init__(self, db_path: str | Path, *, auto_migrate: bool = True) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        if auto_migrate:
            self.migrate()

    @property
    def migration_version(self) -> int:
        try:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM phase11_schema_migrations"
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0]) if row else 0

    def migrate(self) -> None:
        migration = Path(__file__).with_name("migrations") / "0001_phase11.sql"
        self._conn.executescript(migration.read_text(encoding="utf-8"))
        self._conn.commit()

    def backup(self, destination: str | Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(destination))
        try:
            self._conn.backup(target)
        finally:
            target.close()
        return destination

    @staticmethod
    def restore_backup(backup: str | Path, destination: str | Path) -> None:
        shutil.copy2(Path(backup), Path(destination))

    def rollback(self) -> None:
        """Remove only Phase 11 records and audit tables."""

        migration = Path(__file__).with_name("migrations") / "0001_phase11.down.sql"
        with self._conn:
            self._conn.executescript(migration.read_text(encoding="utf-8"))

    def put(self, record: AmbientContract) -> bool:
        encoded = record.to_json()
        status = getattr(record, "status", None)
        row = self._conn.execute(
            "SELECT payload_json FROM phase11_records WHERE id = ?", (record.id,)
        ).fetchone()
        with self._conn:
            if row is None:
                self._conn.execute(
                    "INSERT INTO phase11_records "
                    "(id, kind, status, payload_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        record.id,
                        record.contract_type,
                        str(status.value if hasattr(status, "value") else status)
                        if status is not None
                        else None,
                        encoded,
                        record.created_at,
                        utc_now(),
                    ),
                )
                changed = True
                event = "created"
            elif row[0] == encoded:
                changed = False
                event = "duplicate_noop"
            else:
                self._conn.execute(
                    "UPDATE phase11_records SET kind = ?, status = ?, "
                    "payload_json = ?, "
                    "updated_at = ? WHERE id = ?",
                    (
                        record.contract_type,
                        str(status.value if hasattr(status, "value") else status)
                        if status is not None
                        else None,
                        encoded,
                        utc_now(),
                        record.id,
                    ),
                )
                changed = True
                event = "updated"
            self.audit(record.id, event, {"kind": record.contract_type})
        return changed

    def get(self, record_id: str) -> AmbientContract:
        row = self._conn.execute(
            "SELECT payload_json FROM phase11_records WHERE id = ?", (record_id,)
        ).fetchone()
        if row is None:
            raise KeyError(record_id)
        return contract_from_dict(json.loads(row[0]))

    def list_records(
        self, *, kind: str | None = None, status: str | None = None
    ) -> list[AmbientContract]:
        query = "SELECT payload_json FROM phase11_records"
        params: list[str] = []
        clauses: list[str] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at, id"
        rows = self._conn.execute(query, params).fetchall()
        return [contract_from_dict(json.loads(row[0])) for row in rows]

    def get_mission(self, mission_id: str) -> MissionSummary:
        for record in self.list_records(kind=MissionSummary.contract_type):
            if isinstance(record, MissionSummary) and record.mission_id == mission_id:
                return record
        raise KeyError(mission_id)

    def get_steering(self, steering_id: str) -> RemoteSteering:
        record = self.get(steering_id)
        if not isinstance(record, RemoteSteering):
            raise KeyError(steering_id)
        return record

    def audit(
        self,
        object_id: str | None,
        event: str,
        details: dict[str, Any] | None = None,
        *,
        event_id: str | None = None,
    ) -> bool:
        event_id = event_id or f"phase11-{uuid.uuid4().hex}"
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO phase11_audit_events "
                    "(event_id, object_id, event, details_json, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        event_id,
                        object_id,
                        event,
                        json.dumps(details or {}, sort_keys=True),
                        utc_now(),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def audit_events(self, object_id: str | None = None) -> list[dict[str, Any]]:
        if object_id is None:
            rows = self._conn.execute(
                "SELECT sequence, event_id, object_id, event, details_json, "
                "recorded_at "
                "FROM phase11_audit_events ORDER BY sequence"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT sequence, event_id, object_id, event, details_json, "
                "recorded_at "
                "FROM phase11_audit_events WHERE object_id = ? ORDER BY sequence",
                (object_id,),
            ).fetchall()
        return [
            {
                "sequence": row[0],
                "event_id": row[1],
                "object_id": row[2],
                "event": row[3],
                "details": json.loads(row[4]),
                "recorded_at": row[5],
            }
            for row in rows
        ]

    def recover_interrupted(self) -> int:
        """Recover in-flight records without inventing an external outcome."""

        recovered = 0
        for record in self.list_records(kind=RemoteSteering.contract_type):
            if not isinstance(record, RemoteSteering):
                continue
            if record.status in {SteeringStatus.REQUESTED, SteeringStatus.APPROVED}:
                record.status = SteeringStatus.FAILED
                record.reason = "runtime restarted; steering outcome is unknown"
                record.live_effect = False
                self.put(record)
                self.audit(
                    record.id, "steering:recovered_unknown", {"live_effect": False}
                )
                recovered += 1
        for record in self.list_records(kind=ContinuityAcknowledgment.contract_type):
            if isinstance(
                record, ContinuityAcknowledgment
            ) and record.continuity_state in {"active", "continuous"}:
                record.continuity_state = "stale_after_restart"
                self.put(record)
                self.audit(record.id, "continuity:recovered_stale", {"current": False})
                recovered += 1
        for record in self.list_records(kind=MissionSummary.contract_type):
            if (
                isinstance(record, MissionSummary)
                and record.state in {"active", "running"}
                and not record.stale
            ):
                record.stale = True
                record.uncertainty = list(
                    dict.fromkeys(
                        [
                            *record.uncertainty,
                            "runtime restarted; current state requires refresh",
                        ]
                    )
                )
                self.put(record)
                self.audit(
                    record.id, "mission:marked_stale_after_restart", {"current": False}
                )
                recovered += 1
        return recovered

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AmbientStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = ["AmbientStore", "MIGRATION_VERSION"]
