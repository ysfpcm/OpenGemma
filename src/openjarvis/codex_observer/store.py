"""Durable mission ledger for the read-only Codex observer."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


class CodexMissionStore:
    def __init__(self, path: str | Path, *, raw_retention_hours: int = 24) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_retention_hours = raw_retention_hours
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()
        self.purge_expired_raw()
        self._restrict_database_files()

    def _restrict_database_files(self) -> None:
        """Best-effort owner-only permissions for raw diagnostic payloads."""
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.path}{suffix}")
            if candidate.exists():
                try:
                    os.chmod(candidate, stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    # Windows ACL inheritance remains the governing restriction.
                    pass

    def _migrate(self) -> None:
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS codex_observer_migrations (
                    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS codex_missions (
                    id TEXT PRIMARY KEY, objective TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    thread_id TEXT UNIQUE, active_turn_id TEXT, status TEXT NOT NULL,
                    phase TEXT NOT NULL, progress TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    commands_json TEXT NOT NULL, tools_json TEXT NOT NULL,
                    files_json TEXT NOT NULL, verification_state TEXT NOT NULL,
                    errors_json TEXT NOT NULL, usage_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    last_meaningful_at TEXT NOT NULL, interrupted_reason TEXT,
                    mode TEXT NOT NULL DEFAULT 'read-only',
                    budgets_json TEXT NOT NULL DEFAULT '{}',
                    budget_state_json TEXT NOT NULL DEFAULT '{}',
                    authority_json TEXT NOT NULL DEFAULT '{}',
                    effects_json TEXT NOT NULL DEFAULT '[]',
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    parent_mission_id TEXT,
                    selected_fork_id TEXT);
                CREATE TABLE IF NOT EXISTS codex_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, method TEXT NOT NULL,
                    contract_json TEXT NOT NULL, display_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL, UNIQUE(mission_id, fingerprint));
                CREATE TABLE IF NOT EXISTS codex_milestones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, summary TEXT NOT NULL,
                    event_fingerprint TEXT NOT NULL, recorded_at TEXT NOT NULL,
                    UNIQUE(mission_id, fingerprint));
                CREATE TABLE IF NOT EXISTS codex_raw_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL,
                    method TEXT NOT NULL, payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL, expires_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS codex_control_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL,
                    action TEXT NOT NULL, detail_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS codex_decisions (
                    id TEXT PRIMARY KEY, mission_id TEXT NOT NULL,
                    request_id TEXT NOT NULL, request_method TEXT NOT NULL,
                    kind TEXT NOT NULL, thread_id TEXT, turn_id TEXT, item_id TEXT,
                    requested_json TEXT NOT NULL, offered_json TEXT NOT NULL,
                    guardian_allowed INTEGER NOT NULL, guardian_reason TEXT NOT NULL,
                    status TEXT NOT NULL, marc_decision_json TEXT,
                    response_json TEXT, requested_at TEXT NOT NULL, resolved_at TEXT,
                    UNIQUE(mission_id, request_id));
                CREATE TABLE IF NOT EXISTS codex_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL, channel TEXT NOT NULL,
                    status TEXT NOT NULL, payload_json TEXT NOT NULL,
                    sent_at TEXT NOT NULL, UNIQUE(decision_id, channel));
                INSERT OR IGNORE INTO codex_observer_migrations
                    VALUES (1, CURRENT_TIMESTAMP);
                INSERT OR IGNORE INTO codex_observer_migrations
                    VALUES (2, CURRENT_TIMESTAMP);
            """)
            columns = {
                row[1]
                for row in self._conn.execute("PRAGMA table_info(codex_missions)")
            }
            additions = {
                "mode": "TEXT NOT NULL DEFAULT 'read-only'",
                "budgets_json": "TEXT NOT NULL DEFAULT '{}'",
                "budget_state_json": "TEXT NOT NULL DEFAULT '{}'",
                "authority_json": "TEXT NOT NULL DEFAULT '{}'",
                "effects_json": "TEXT NOT NULL DEFAULT '[]'",
                "checkpoint_json": "TEXT NOT NULL DEFAULT '{}'",
                "parent_mission_id": "TEXT",
                "selected_fork_id": "TEXT",
            }
            for name, definition in additions.items():
                if name not in columns:
                    self._conn.execute(
                        f"ALTER TABLE codex_missions ADD COLUMN {name} {definition}"
                    )
            self._conn.execute(
                "INSERT OR IGNORE INTO codex_observer_migrations "
                "VALUES (3, CURRENT_TIMESTAMP)"
            )

    def create_mission(
        self,
        objective: str,
        workspace: str,
        *,
        mode: str = "read-only",
        budgets: Optional[dict[str, Any]] = None,
        authority: Optional[dict[str, Any]] = None,
        parent_mission_id: Optional[str] = None,
    ) -> str:
        if mode not in {"read-only", "workspace-write"}:
            raise ValueError("unsupported mission mode")
        mission_id = f"mission_{uuid.uuid4().hex}"
        now = _now()
        budget_payload = budgets or {}
        authority_payload = authority or {}
        with self._conn:
            self._conn.execute(
                "INSERT INTO codex_missions "
                "(id, objective, workspace, thread_id, active_turn_id, status, phase, "
                "progress, plan_json, commands_json, tools_json, files_json, "
                "verification_state, errors_json, usage_json, created_at, updated_at, "
                "last_meaningful_at, interrupted_reason, mode, budgets_json, "
                "budget_state_json, authority_json, effects_json, checkpoint_json, "
                "parent_mission_id, selected_fork_id) VALUES "
                "(?, ?, ?, NULL, NULL, 'starting', 'starting', ?, '[]', '[]', '[]', "
                "'[]', 'not_started', '[]', '{}', ?, ?, ?, NULL, ?, ?, '{}', "
                "?, '[]', '{}', ?, NULL)",
                (
                    mission_id,
                    objective,
                    workspace,
                    "Preparing read-only Codex mission",
                    now,
                    now,
                    now,
                    mode,
                    json.dumps(budget_payload),
                    json.dumps(authority_payload),
                    parent_mission_id,
                ),
            )
        return mission_id

    def bind(
        self,
        mission_id: str,
        *,
        thread_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> None:
        fields, values = [], []
        if thread_id:
            fields.append("thread_id = ?")
            values.append(thread_id)
        if turn_id:
            fields.append("active_turn_id = ?")
            values.append(turn_id)
        if fields:
            values.extend([_now(), mission_id])
            with self._conn:
                self._conn.execute(
                    f"UPDATE codex_missions SET {', '.join(fields)}, "
                    "updated_at = ? WHERE id = ?",
                    values,
                )

    def mission_for_thread(self, thread_id: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT id FROM codex_missions WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return row[0] if row else None

    def update(self, mission_id: str, **changes: Any) -> None:
        allowed = {
            "status",
            "phase",
            "progress",
            "plan_json",
            "commands_json",
            "tools_json",
            "files_json",
            "verification_state",
            "errors_json",
            "usage_json",
            "interrupted_reason",
            "mode",
            "budgets_json",
            "budget_state_json",
            "authority_json",
            "effects_json",
            "checkpoint_json",
            "parent_mission_id",
            "selected_fork_id",
        }
        fields, values = [], []
        for key, value in changes.items():
            if key not in allowed:
                raise ValueError(f"unsupported mission field: {key}")
            fields.append(f"{key} = ?")
            values.append(json.dumps(value) if key.endswith("_json") else value)
        if not fields:
            return
        now = _now()
        values.extend([now, now, mission_id])
        with self._conn:
            self._conn.execute(
                f"UPDATE codex_missions SET {', '.join(fields)}, updated_at = ?, "
                "last_meaningful_at = ? WHERE id = ?",
                values,
            )

    def record_event(
        self,
        mission_id: str,
        method: str,
        payload: dict[str, Any],
        contract_json: str,
        display: dict[str, Any],
    ) -> tuple[str, bool]:
        self.purge_expired_raw()
        event_fp = fingerprint({"method": method, "payload": display})
        now = _now()
        expires = (
            datetime.now(timezone.utc) + timedelta(hours=self.raw_retention_hours)
        ).isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT INTO codex_raw_events(mission_id, method, payload_json, "
                "recorded_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (mission_id, method, json.dumps(payload, default=str), now, expires),
            )
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO codex_events(mission_id, fingerprint, method, "
                "contract_json, display_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (mission_id, event_fp, method, contract_json, json.dumps(display), now),
            )
        self._restrict_database_files()
        return event_fp, cur.rowcount == 1

    def milestone(self, mission_id: str, summary: str, event_fp: str) -> bool:
        milestone_fp = fingerprint({"summary": summary})
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO codex_milestones(mission_id, fingerprint, "
                "summary, "
                "event_fingerprint, recorded_at) VALUES (?, ?, ?, ?, ?)",
                (mission_id, milestone_fp, summary, event_fp, _now()),
            )
        return cur.rowcount == 1

    def record_control(
        self, mission_id: str, action: str, detail: dict[str, Any]
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO codex_control_actions(mission_id, action, detail_json, "
                "recorded_at) VALUES (?, ?, ?, ?)",
                (mission_id, action, json.dumps(detail), _now()),
            )

    def purge_expired_raw(self, now: Optional[str] = None) -> int:
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM codex_raw_events WHERE expires_at <= ?", (now or _now(),)
            )
        return cur.rowcount

    def get(self, mission_id: str, *, include_events: bool = True) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM codex_missions WHERE id = ?", (mission_id,)
        ).fetchone()
        if not row:
            raise KeyError(mission_id)
        names = [
            item[1] for item in self._conn.execute("PRAGMA table_info(codex_missions)")
        ]
        result = dict(zip(names, row))
        for key in (
            "plan_json",
            "commands_json",
            "tools_json",
            "files_json",
            "errors_json",
            "usage_json",
            "budgets_json",
            "budget_state_json",
            "authority_json",
            "effects_json",
            "checkpoint_json",
        ):
            result[key.removesuffix("_json")] = json.loads(result.pop(key) or "{}")
        if not isinstance(result["effects"], list):
            result["effects"] = []
        if include_events:
            result["events"] = [
                {
                    "id": r[0],
                    "fingerprint": r[1],
                    "method": r[2],
                    "contract": json.loads(r[3]),
                    "display": json.loads(r[4]),
                    "recorded_at": r[5],
                }
                for r in self._conn.execute(
                    "SELECT id, fingerprint, method, contract_json, display_json, "
                    "recorded_at "
                    "FROM codex_events WHERE mission_id = ? ORDER BY id",
                    (mission_id,),
                ).fetchall()
            ]
            result["milestones"] = [
                {"summary": r[0], "event_fingerprint": r[1], "recorded_at": r[2]}
                for r in self._conn.execute(
                    "SELECT summary, event_fingerprint, recorded_at "
                    "FROM codex_milestones "
                    "WHERE mission_id = ? ORDER BY id",
                    (mission_id,),
                ).fetchall()
            ]
            result["controls"] = [
                {"action": r[0], "detail": json.loads(r[1]), "recorded_at": r[2]}
                for r in self._conn.execute(
                    "SELECT action, detail_json, recorded_at "
                    "FROM codex_control_actions WHERE mission_id = ? ORDER BY id",
                    (mission_id,),
                ).fetchall()
            ]
            result["decisions"] = self.list_decisions(mission_id)
            result["notifications"] = self.list_notifications(mission_id)
        return result

    def record_decision(
        self,
        mission_id: str,
        *,
        request_id: int | str,
        request_method: str,
        kind: str,
        thread_id: Optional[str],
        turn_id: Optional[str],
        item_id: Optional[str],
        requested: dict[str, Any],
        offered: dict[str, Any],
        guardian_allowed: bool,
        guardian_reason: str,
    ) -> str:
        return self.record_decision_if_absent(
            mission_id,
            request_id=request_id,
            request_method=request_method,
            kind=kind,
            thread_id=thread_id,
            turn_id=turn_id,
            item_id=item_id,
            requested=requested,
            offered=offered,
            guardian_allowed=guardian_allowed,
            guardian_reason=guardian_reason,
        )[0]

    def record_decision_if_absent(
        self,
        mission_id: str,
        *,
        request_id: int | str,
        request_method: str,
        kind: str,
        thread_id: Optional[str],
        turn_id: Optional[str],
        item_id: Optional[str],
        requested: dict[str, Any],
        offered: dict[str, Any],
        guardian_allowed: bool,
        guardian_reason: str,
    ) -> tuple[str, bool]:
        """Insert one server request decision, preserving request idempotency."""
        decision_id = f"decision_{uuid.uuid4().hex}"
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM codex_decisions WHERE mission_id = ? "
                "AND request_id = ?",
                (mission_id, str(request_id)),
            ).fetchone()
            if existing:
                return str(existing[0]), False
            try:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO codex_decisions "
                        "(id, mission_id, request_id, request_method, kind, thread_id, "
                        "turn_id, "
                        "item_id, requested_json, offered_json, guardian_allowed, "
                        "guardian_reason, status, marc_decision_json, response_json, "
                        "requested_at, resolved_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, "
                        "NULL, ?, NULL)",
                        (
                            decision_id,
                            mission_id,
                            str(request_id),
                            request_method,
                            kind,
                            thread_id,
                            turn_id,
                            item_id,
                            json.dumps(requested),
                            json.dumps(offered),
                            int(guardian_allowed),
                            guardian_reason,
                            _now(),
                        ),
                    )
            except sqlite3.IntegrityError:
                existing = self._conn.execute(
                    "SELECT id FROM codex_decisions WHERE mission_id = ? "
                    "AND request_id = ?",
                    (mission_id, str(request_id)),
                ).fetchone()
                if existing:
                    return str(existing[0]), False
                raise
        return decision_id, True

    def get_decision_for_request(
        self,
        mission_id: str,
        request_id: int | str,
        *,
        include_response: bool = False,
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM codex_decisions WHERE mission_id = ? "
                "AND request_id = ?",
                (mission_id, str(request_id)),
            ).fetchone()
        if not row:
            return None
        return self.get_decision(str(row[0]), include_response=include_response)

    def get_decision(
        self, decision_id: str, *, include_response: bool = False
    ) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, mission_id, request_id, request_method, kind, thread_id, "
                "turn_id, item_id, requested_json, offered_json, guardian_allowed, "
                "guardian_reason, status, "
                "marc_decision_json, response_json, requested_at, resolved_at "
                "FROM codex_decisions WHERE id = ?",
                (decision_id,),
            ).fetchone()
        if not row:
            raise KeyError(decision_id)
        keys = (
            "id",
            "mission_id",
            "request_id",
            "request_method",
            "kind",
            "thread_id",
            "turn_id",
            "item_id",
            "requested",
            "offered",
            "guardian_allowed",
            "guardian_reason",
            "status",
            "marc_decision",
            "response",
            "requested_at",
            "resolved_at",
        )
        result = dict(zip(keys, row))
        for key in ("requested", "offered", "marc_decision", "response"):
            if result[key] is not None:
                result[key] = json.loads(result[key])
        if not include_response:
            result["response"] = None
        result["guardian_allowed"] = bool(result["guardian_allowed"])
        result["codex_requested"] = True
        result["guardian_allows"] = result["guardian_allowed"]
        result["marc_approved"] = result["status"] == "approved"
        return result

    def list_decisions(
        self, mission_id: str, *, pending_only: bool = False
    ) -> list[dict[str, Any]]:
        query = "SELECT id FROM codex_decisions WHERE mission_id = ?"
        values: list[Any] = [mission_id]
        if pending_only:
            query += " AND status = 'pending'"
        query += " ORDER BY requested_at"
        return [
            self.get_decision(row[0])
            for row in self._conn.execute(query, values).fetchall()
        ]

    def resolve_decision(
        self,
        decision_id: str,
        *,
        status: str,
        marc_decision: dict[str, Any],
        response: Optional[dict[str, Any]],
    ) -> None:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE codex_decisions SET status = ?, marc_decision_json = ?, "
                "response_json = ?, resolved_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (
                    status,
                    json.dumps(marc_decision),
                    json.dumps(response) if response is not None else None,
                    _now(),
                    decision_id,
                ),
            )
        if cur.rowcount != 1:
            raise ValueError("decision is no longer pending")

    def record_notification(
        self,
        mission_id: str,
        decision_id: str,
        channel: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO codex_notifications "
                "(mission_id, decision_id, channel, status, payload_json, sent_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (mission_id, decision_id, channel, status, json.dumps(payload), _now()),
            )

    def list_notifications(self, mission_id: str) -> list[dict[str, Any]]:
        return [
            {
                "decision_id": row[0],
                "channel": row[1],
                "status": row[2],
                "payload": json.loads(row[3]),
                "sent_at": row[4],
            }
            for row in self._conn.execute(
                "SELECT decision_id, channel, status, payload_json, sent_at "
                "FROM codex_notifications WHERE mission_id = ? ORDER BY id",
                (mission_id,),
            ).fetchall()
        ]

    def list(self) -> list[dict[str, Any]]:
        ids = self._conn.execute(
            "SELECT id FROM codex_missions ORDER BY updated_at DESC"
        ).fetchall()
        return [self.get(row[0], include_events=False) for row in ids]

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def rollback_database(path: str | Path) -> None:
        """Remove only Codex observer tables, preserving unrelated data."""
        connection = sqlite3.connect(str(path))
        try:
            with connection:
                for table in (
                    "codex_notifications",
                    "codex_decisions",
                    "codex_raw_events",
                    "codex_control_actions",
                    "codex_milestones",
                    "codex_events",
                    "codex_missions",
                    "codex_observer_migrations",
                ):
                    connection.execute(f"DROP TABLE IF EXISTS {table}")
        finally:
            connection.close()
