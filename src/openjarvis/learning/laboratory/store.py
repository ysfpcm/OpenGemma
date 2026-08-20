"""Durable, additive SQLite persistence for the Phase 10 laboratory."""

# ruff: noqa: E501

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .lifecycle import CandidateLifecycleError, validate_transition
from .models import (
    CandidateStatus,
    LaboratoryContract,
    LearningCandidate,
    ReplayRun,
    ReplayStatus,
    ShadowRun,
    ShadowStatus,
    contract_from_dict,
    utc_now,
)

MIGRATION_VERSION = 1


class LaboratoryStore:
    """A Phase-10-only ledger which does not touch unrelated application tables."""

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
                "SELECT COALESCE(MAX(version), 0) FROM phase10_schema_migrations"
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0]) if row else 0

    def migrate(self) -> None:
        migration = Path(__file__).with_name("migrations") / "0001_phase10.sql"
        self._conn.executescript(migration.read_text(encoding="utf-8"))
        self._conn.commit()

    def backup(self, destination: str | Path) -> Path:
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise ValueError("backup destination must differ from the live database")
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
        """Remove only Phase 10 tables, preserving all other application data."""

        with self._conn:
            migration = Path(__file__).with_name("migrations") / "0001_phase10.down.sql"
            self._conn.executescript(migration.read_text(encoding="utf-8"))

    def put(
        self,
        record: LaboratoryContract,
        *,
        _allow_candidate_transition: bool = False,
    ) -> LaboratoryContract:
        payload = record.to_json()
        status = getattr(record, "status", None)
        row = self._conn.execute(
            "SELECT kind, status, payload_json FROM phase10_records WHERE id = ?",
            (record.id,),
        ).fetchone()
        with self._conn:
            if row is None:
                self._conn.execute(
                    "INSERT INTO phase10_records "
                    "(id, kind, status, schema_version, payload_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.id,
                        record.contract_type,
                        status,
                        record.schema_version,
                        payload,
                        record.created_at,
                        utc_now(),
                    ),
                )
            elif row[0] != record.contract_type:
                raise ValueError(
                    f"record id {record.id!r} already belongs to {row[0]!r}"
                )
            elif (
                record.contract_type == "learning_candidate"
                and row[1] != status
                and not _allow_candidate_transition
            ):
                raise CandidateLifecycleError(
                    "candidate status changes must use transition_candidate"
                )
            elif row[2] != payload:
                self._conn.execute(
                    "UPDATE phase10_records SET status = ?, payload_json = ?, "
                    "updated_at = ? WHERE id = ?",
                    (status, payload, utc_now(), record.id),
                )
        return record

    def get(self, record_id: str) -> LaboratoryContract:
        row = self._conn.execute(
            "SELECT payload_json FROM phase10_records WHERE id = ?", (record_id,)
        ).fetchone()
        if row is None:
            raise KeyError(record_id)
        return contract_from_dict(json.loads(row[0]))

    def list_records(
        self, *, kind: str | None = None, status: str | None = None
    ) -> list[LaboratoryContract]:
        query = "SELECT payload_json FROM phase10_records"
        clauses: list[str] = []
        params: list[str] = []
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

    def transition_candidate(
        self,
        candidate_id: str,
        target: str | CandidateStatus,
        *,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> LearningCandidate:
        candidate = self.get(candidate_id)
        if not isinstance(candidate, LearningCandidate):
            raise TypeError(f"record {candidate_id} is not a learning candidate")
        target_value = target.value if isinstance(target, CandidateStatus) else target
        validate_transition(candidate.status, target_value)
        if candidate.status == target_value:
            return candidate
        candidate.status = target_value
        self.put(candidate, _allow_candidate_transition=True)
        self.audit(
            candidate_id,
            f"candidate:{candidate.status}",
            {"reason": reason, **(details or {})},
        )
        return candidate

    def audit(
        self,
        record_id: str | None,
        event: str,
        details: dict[str, Any] | None = None,
        *,
        event_id: str | None = None,
    ) -> bool:
        """Append one immutable event; repeated idempotency events are no-ops."""

        if event.startswith("idempotency:"):
            existing = self._conn.execute(
                "SELECT 1 FROM phase10_audit_events WHERE record_id = ? AND event = ?",
                (record_id, event),
            ).fetchone()
            if existing:
                return False
        event_id = event_id or f"phase10-{record_id or 'system'}-{utc_now()}"
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO phase10_audit_events "
                    "(event_id, record_id, event, details_json, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        event_id,
                        record_id,
                        event,
                        json.dumps(details or {}, sort_keys=True),
                        utc_now(),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def audit_events(self, record_id: str | None = None) -> list[dict[str, Any]]:
        if record_id is None:
            rows = self._conn.execute(
                "SELECT sequence, event_id, record_id, event, details_json, recorded_at "
                "FROM phase10_audit_events ORDER BY sequence"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT sequence, event_id, record_id, event, details_json, recorded_at "
                "FROM phase10_audit_events WHERE record_id = ? ORDER BY sequence",
                (record_id,),
            ).fetchall()
        return [
            {
                "sequence": row[0],
                "event_id": row[1],
                "record_id": row[2],
                "event": row[3],
                "details": json.loads(row[4]),
                "recorded_at": row[5],
            }
            for row in rows
        ]

    def recover_interrupted(self) -> int:
        recovered = 0
        for record in self.list_records(kind="replay_run"):
            if (
                isinstance(record, ReplayRun)
                and record.status == ReplayStatus.RUNNING.value
            ):
                record.status = ReplayStatus.FAILED.value
                record.passed = False
                record.issues.append("runtime restarted; replay outcome is unknown")
                self.put(record)
                self.audit(record.id, "replay:recovered_unknown", {"restart": True})
                recovered += 1
        for record in self.list_records(kind="shadow_run"):
            if (
                isinstance(record, ShadowRun)
                and record.status == ShadowStatus.RUNNING.value
            ):
                record.status = "failed"
                record.passed = False
                record.issues.append("runtime restarted; shadow outcome is unknown")
                self.put(record)
                self.audit(record.id, "shadow:recovered_unknown", {"restart": True})
                recovered += 1
        return recovered

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "LaboratoryStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = ["LaboratoryStore", "MIGRATION_VERSION"]
