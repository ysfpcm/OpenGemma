"""Durable Phase 7 setup, schedule, approval, and causal evidence store."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .models import DepartureSetup


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class DepartureStore:
    """Append-oriented local state for the Phase 7 pilot."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
            CREATE TABLE IF NOT EXISTS phase7_schema_migrations (
                version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase7_setup_versions (
                setup_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                body_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(setup_id, version)
            );
            CREATE TABLE IF NOT EXISTS phase7_setup_state (
                setup_id TEXT PRIMARY KEY,
                latest_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase7_departure_state (
                departure_id TEXT PRIMARY KEY,
                setup_id TEXT NOT NULL,
                setup_version INTEGER NOT NULL,
                plan_id TEXT,
                plan_version INTEGER,
                context_fingerprint TEXT,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS phase7_schedule (
                effect_id TEXT PRIMARY KEY,
                departure_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                plan_version INTEGER NOT NULL,
                step_id TEXT NOT NULL,
                due_at TEXT NOT NULL,
                status TEXT NOT NULL,
                grant_id TEXT,
                approval_id TEXT,
                action_id TEXT,
                result_json TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                canceled_at TEXT,
                reason TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS phase7_approvals (
                approval_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                plan_version INTEGER NOT NULL,
                step_id TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                reason TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS phase7_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                event TEXT NOT NULL,
                departure_id TEXT,
                plan_id TEXT,
                details_json TEXT NOT NULL,
                event_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase7_reports (
                report_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                body_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS phase7_events_immutable_update
            BEFORE UPDATE ON phase7_events BEGIN
                SELECT RAISE(ABORT, 'phase7 events are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS phase7_events_immutable_delete
            BEFORE DELETE ON phase7_events BEGIN
                SELECT RAISE(ABORT, 'phase7 events are immutable');
            END;
            INSERT OR IGNORE INTO phase7_schema_migrations VALUES (1, CURRENT_TIMESTAMP);
            COMMIT;
            """
        )

    def close(self) -> None:
        self._conn.close()

    def save_setup(self, setup: DepartureSetup) -> bool:
        body = setup.to_dict()
        existing = self._conn.execute(
            "SELECT body_json FROM phase7_setup_versions WHERE setup_id=? AND version=?",
            (setup.setup_id, setup.version),
        ).fetchone()
        if existing:
            if json.loads(existing[0]) == body:
                return False
            raise ValueError("setup version already exists with different content")
        latest = self._conn.execute(
            "SELECT latest_version FROM phase7_setup_state WHERE setup_id=?",
            (setup.setup_id,),
        ).fetchone()
        if latest and setup.version <= int(latest[0]):
            raise ValueError(
                "new setup versions must be greater than the latest version"
            )
        with self._conn:
            self._conn.execute(
                "INSERT INTO phase7_setup_versions VALUES (?, ?, ?, ?, ?)",
                (
                    setup.setup_id,
                    setup.version,
                    _json(body),
                    setup.status,
                    setup.created_at,
                ),
            )
            if latest:
                self._conn.execute(
                    "UPDATE phase7_setup_versions SET status='superseded' WHERE setup_id=? AND version=?",
                    (setup.setup_id, int(latest[0])),
                )
                self._conn.execute(
                    "UPDATE phase7_setup_state SET latest_version=?, status=?, reason='', updated_at=? WHERE setup_id=?",
                    (setup.version, setup.status, _now(), setup.setup_id),
                )
            else:
                self._conn.execute(
                    "INSERT INTO phase7_setup_state VALUES (?, ?, ?, '', ?)",
                    (setup.setup_id, setup.version, setup.status, _now()),
                )
            self.record_event(
                "setup-saved",
                details={"setup_id": setup.setup_id, "version": setup.version},
            )
        return True

    def get_setup(self, setup_id: str, version: int | None = None) -> DepartureSetup:
        if version is None:
            row = self._conn.execute(
                "SELECT latest_version FROM phase7_setup_state WHERE setup_id=?",
                (setup_id,),
            ).fetchone()
            if row is None:
                raise KeyError(setup_id)
            version = int(row[0])
        row = self._conn.execute(
            "SELECT body_json, status FROM phase7_setup_versions WHERE setup_id=? AND version=?",
            (setup_id, version),
        ).fetchone()
        if row is None:
            raise KeyError(f"{setup_id}:v{version}")
        body = json.loads(row[0])
        body["status"] = row[1]
        return DepartureSetup.from_dict(body)

    def list_setup_versions(self, setup_id: str) -> list[DepartureSetup]:
        rows = self._conn.execute(
            "SELECT version FROM phase7_setup_versions WHERE setup_id=? ORDER BY version",
            (setup_id,),
        ).fetchall()
        return [self.get_setup(setup_id, int(row[0])) for row in rows]

    def set_setup_status(self, setup_id: str, status: str, reason: str) -> None:
        setup = self.get_setup(setup_id)
        with self._conn:
            self._conn.execute(
                "UPDATE phase7_setup_versions SET status=? WHERE setup_id=? AND version=?",
                (status, setup_id, setup.version),
            )
            self._conn.execute(
                "UPDATE phase7_setup_state SET status=?, reason=?, updated_at=? WHERE setup_id=?",
                (status, reason, _now(), setup_id),
            )
            self.record_event(
                "setup-status-changed",
                details={"setup_id": setup_id, "status": status, "reason": reason},
            )

    def save_departure_state(self, departure_id: str, **values: Any) -> None:
        current = self.get_departure_state(departure_id)
        body = {
            "departure_id": departure_id,
            "setup_id": values.get("setup_id", current.get("setup_id", "")),
            "setup_version": values.get(
                "setup_version", current.get("setup_version", 0)
            ),
            "plan_id": values.get("plan_id", current.get("plan_id")),
            "plan_version": values.get("plan_version", current.get("plan_version")),
            "context_fingerprint": values.get(
                "context_fingerprint", current.get("context_fingerprint")
            ),
            "status": values.get("status", current.get("status", "unknown")),
            "updated_at": values.get("updated_at", _now()),
            "reason": values.get("reason", current.get("reason", "")),
        }
        with self._conn:
            self._conn.execute(
                "INSERT INTO phase7_departure_state VALUES (:departure_id, :setup_id, :setup_version, :plan_id, :plan_version, :context_fingerprint, :status, :updated_at, :reason) "
                "ON CONFLICT(departure_id) DO UPDATE SET setup_id=excluded.setup_id, setup_version=excluded.setup_version, plan_id=excluded.plan_id, plan_version=excluded.plan_version, context_fingerprint=excluded.context_fingerprint, status=excluded.status, updated_at=excluded.updated_at, reason=excluded.reason",
                body,
            )

    def get_departure_state(self, departure_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT departure_id, setup_id, setup_version, plan_id, plan_version, context_fingerprint, status, updated_at, reason FROM phase7_departure_state WHERE departure_id=?",
            (departure_id,),
        ).fetchone()
        if row is None:
            return {}
        keys = (
            "departure_id",
            "setup_id",
            "setup_version",
            "plan_id",
            "plan_version",
            "context_fingerprint",
            "status",
            "updated_at",
            "reason",
        )
        return dict(zip(keys, row))

    def list_departure_states(
        self, *, setup_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if setup_id:
            clauses.append("setup_id=?")
            params.append(setup_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            "SELECT departure_id, setup_id, setup_version, plan_id, plan_version, "
            "context_fingerprint, status, updated_at, reason "
            "FROM phase7_departure_state" + where + " ORDER BY departure_id",
            params,
        ).fetchall()
        keys = (
            "departure_id",
            "setup_id",
            "setup_version",
            "plan_id",
            "plan_version",
            "context_fingerprint",
            "status",
            "updated_at",
            "reason",
        )
        return [dict(zip(keys, row)) for row in rows]

    def schedule_effect(
        self,
        *,
        effect_id: str,
        departure_id: str,
        plan_id: str,
        plan_version: int,
        step_id: str,
        due_at: str,
        grant_id: str | None = None,
        approval_id: str | None = None,
    ) -> bool:
        with self._conn:
            existing = self._conn.execute(
                "SELECT status, grant_id, approval_id FROM phase7_schedule "
                "WHERE effect_id=?",
                (effect_id,),
            ).fetchone()
            changed = False
            if existing is None:
                self._conn.execute(
                    "INSERT INTO phase7_schedule(effect_id, departure_id, plan_id, plan_version, step_id, due_at, status, grant_id, approval_id, created_at) VALUES (?, ?, ?, ?, ?, ?, 'scheduled', ?, ?, ?)",
                    (
                        effect_id,
                        departure_id,
                        plan_id,
                        plan_version,
                        step_id,
                        due_at,
                        grant_id,
                        approval_id,
                        _now(),
                    ),
                )
                changed = True
            elif (grant_id or approval_id) and existing[0] in {
                "scheduled",
                "pending",
                "skipped",
            }:
                next_grant = grant_id if grant_id is not None else existing[1]
                next_approval = approval_id if approval_id is not None else existing[2]
                changed = (
                    next_grant != existing[1]
                    or next_approval != existing[2]
                    or existing[0] == "skipped"
                )
                if changed:
                    self._conn.execute(
                        "UPDATE phase7_schedule SET status='scheduled', "
                        "grant_id=?, approval_id=?, reason='' WHERE effect_id=?",
                        (next_grant, next_approval, effect_id),
                    )
            if changed:
                self.record_event(
                    "effect-scheduled",
                    departure_id=departure_id,
                    plan_id=plan_id,
                    details={
                        "effect_id": effect_id,
                        "step_id": step_id,
                        "due_at": due_at,
                    },
                )
            return changed

    def list_schedules(
        self, departure_id: str | None = None, plan_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if departure_id:
            clauses.append("departure_id=?")
            params.append(departure_id)
        if plan_id:
            clauses.append("plan_id=?")
            params.append(plan_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            "SELECT effect_id, departure_id, plan_id, plan_version, step_id, due_at, status, grant_id, approval_id, action_id, result_json, created_at, started_at, completed_at, canceled_at, reason FROM phase7_schedule"
            + where
            + " ORDER BY due_at, effect_id",
            params,
        ).fetchall()
        keys = (
            "effect_id",
            "departure_id",
            "plan_id",
            "plan_version",
            "step_id",
            "due_at",
            "status",
            "grant_id",
            "approval_id",
            "action_id",
            "result_json",
            "created_at",
            "started_at",
            "completed_at",
            "canceled_at",
            "reason",
        )
        result = []
        for row in rows:
            item = dict(zip(keys, row))
            item["result"] = (
                json.loads(item.pop("result_json")) if item["result_json"] else None
            )
            result.append(item)
        return result

    def mark_schedule(
        self,
        effect_id: str,
        status: str,
        *,
        reason: str = "",
        action_id: str | None = None,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        valid_statuses = {
            "scheduled",
            "pending",
            "executing",
            "completed",
            "failed",
            "ambiguous",
            "canceled",
            "skipped",
        }
        if status not in valid_statuses:
            raise ValueError(f"unknown Phase 7 schedule status: {status}")
        now = _now()
        with self._conn:
            current = self._conn.execute(
                "SELECT status FROM phase7_schedule WHERE effect_id=?",
                (effect_id,),
            ).fetchone()
            if current is None or current[0] == status:
                return
            if current[0] in {"completed", "failed", "ambiguous", "canceled"}:
                return
            self._conn.execute(
                "UPDATE phase7_schedule SET status=?, reason=?, action_id=COALESCE(?, action_id), result_json=?, started_at=CASE WHEN ?='executing' AND started_at IS NULL THEN ? ELSE started_at END, completed_at=CASE WHEN ? IN ('completed','failed','ambiguous','canceled','skipped') THEN ? ELSE completed_at END, canceled_at=CASE WHEN ?='canceled' THEN ? ELSE canceled_at END WHERE effect_id=?",
                (
                    status,
                    reason,
                    action_id,
                    _json(result) if result is not None else None,
                    status,
                    now,
                    status,
                    now,
                    status,
                    now,
                    effect_id,
                ),
            )
            row = self._conn.execute(
                "SELECT departure_id, plan_id, step_id FROM phase7_schedule WHERE effect_id=?",
                (effect_id,),
            ).fetchone()
            if row:
                self.record_event(
                    f"effect-{status}",
                    departure_id=row[0],
                    plan_id=row[1],
                    details={
                        "effect_id": effect_id,
                        "step_id": row[2],
                        "reason": reason,
                        "action_id": action_id,
                    },
                )

    def cancel_schedules(
        self,
        *,
        departure_id: str | None = None,
        plan_id: str | None = None,
        reason: str,
    ) -> int:
        clauses = ["status IN ('scheduled','pending')"]
        params: list[Any] = []
        if departure_id:
            clauses.append("departure_id=?")
            params.append(departure_id)
        if plan_id:
            clauses.append("plan_id=?")
            params.append(plan_id)
        with self._conn:
            rows = self._conn.execute(
                "SELECT effect_id FROM phase7_schedule WHERE " + " AND ".join(clauses),
                params,
            ).fetchall()
        for (effect_id,) in rows:
            self.mark_schedule(effect_id, "canceled", reason=reason)
        return len(rows)

    def save_approval(
        self,
        approval_id: str,
        plan_id: str,
        plan_version: int,
        step_id: str,
        expires_at: str,
        reason: str = "Marc approved exact step",
    ) -> None:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO phase7_approvals VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    approval_id,
                    plan_id,
                    plan_version,
                    step_id,
                    _now(),
                    expires_at,
                    reason,
                ),
            )
            if cursor.rowcount:
                self.record_event(
                    "approval-created",
                    plan_id=plan_id,
                    details={
                        "approval_id": approval_id,
                        "plan_version": plan_version,
                        "step_id": step_id,
                    },
                )

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT approval_id, plan_id, plan_version, step_id, approved_at, expires_at, revoked_at, reason FROM phase7_approvals WHERE approval_id=?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise KeyError(approval_id)
        keys = (
            "approval_id",
            "plan_id",
            "plan_version",
            "step_id",
            "approved_at",
            "expires_at",
            "revoked_at",
            "reason",
        )
        return dict(zip(keys, row))

    def revoke_approval(self, approval_id: str, reason: str) -> None:
        self.get_approval(approval_id)
        with self._conn:
            self._conn.execute(
                "UPDATE phase7_approvals SET revoked_at=? WHERE approval_id=? AND revoked_at IS NULL",
                (_now(), approval_id),
            )
            self.record_event(
                "approval-revoked",
                details={"approval_id": approval_id, "reason": reason},
            )

    def record_event(
        self,
        event: str,
        *,
        details: Mapping[str, Any] | None = None,
        departure_id: str | None = None,
        plan_id: str | None = None,
        event_id: str | None = None,
    ) -> str:
        event_id = (
            event_id
            or f"phase7:{event}:{datetime.now(timezone.utc).timestamp()}:{self._conn.execute('SELECT COALESCE(MAX(sequence), 0) FROM phase7_events').fetchone()[0]}"
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO phase7_events(event_id, event, departure_id, plan_id, details_json, event_at) VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, event, departure_id, plan_id, _json(details or {}), _now()),
        )
        return event_id

    def events(
        self, *, departure_id: str | None = None, plan_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if departure_id:
            clauses.append("departure_id=?")
            params.append(departure_id)
        if plan_id:
            clauses.append("plan_id=?")
            params.append(plan_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            "SELECT sequence, event_id, event, departure_id, plan_id, details_json, event_at FROM phase7_events"
            + where
            + " ORDER BY sequence",
            params,
        ).fetchall()
        keys = (
            "sequence",
            "event_id",
            "event",
            "departure_id",
            "plan_id",
            "details_json",
            "event_at",
        )
        return [{**dict(zip(keys, row)), "details": json.loads(row[5])} for row in rows]

    def save_report(self, report_id: str, kind: str, body: Mapping[str, Any]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO phase7_reports VALUES (?, ?, ?, ?)",
            (report_id, kind, _json(body), _now()),
        )

    def weekly_trust_report(self, week: str) -> dict[str, Any]:
        rows = self._conn.execute(
            "SELECT event, details_json, event_at FROM phase7_events WHERE substr(event_at, 1, 10) >= ? ORDER BY sequence",
            (week,),
        ).fetchall()
        counts: dict[str, int] = {}
        for event, _details, _at in rows:
            counts[event] = counts.get(event, 0) + 1
        categories = {
            "situations_detected": ("recalculation", "recalculation-blocked"),
            "suggestions": ("brief-generated",),
            "approvals": ("approval-created",),
            "edits": ("plan-edited",),
            "denials": ("authorization-denied", "execution-blocked"),
            "revocations": ("approval-revoked", "grant-revoked"),
            "cancellations": ("plan-canceled", "effect-canceled"),
            "verified_actions": ("effect-completed",),
            "failed_or_ambiguous_actions": ("effect-failed", "effect-ambiguous"),
            "avoided_notifications": ("notification-avoided",),
            "stale_source_events": ("recalculation-blocked",),
            "emergency_stops": ("emergency-stop",),
            "duplicate_prevention_events": ("duplicate-prevented",),
        }
        return {
            "week_start": week,
            "counts": counts,
            "categories": {
                key: sum(counts.get(item, 0) for item in events)
                for key, events in categories.items()
            },
            "events": [
                dict(zip(("event", "details_json", "at"), row))
                | {"details": json.loads(row[1])}
                for row in rows
            ],
        }


__all__ = ["DepartureStore"]
