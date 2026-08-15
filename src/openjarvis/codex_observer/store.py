"""Durable mission ledger for the read-only Codex observer."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
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
                    last_meaningful_at TEXT NOT NULL, interrupted_reason TEXT);
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
                INSERT OR IGNORE INTO codex_observer_migrations
                    VALUES (1, CURRENT_TIMESTAMP);
            """)

    def create_mission(self, objective: str, workspace: str) -> str:
        mission_id = f"mission_{uuid.uuid4().hex}"
        now = _now()
        with self._conn:
            self._conn.execute(
                "INSERT INTO codex_missions VALUES "
                "(?, ?, ?, NULL, NULL, 'starting', 'starting', ?, '[]', '[]', "
                "'[]', '[]', 'not_started', '[]', '{}', ?, ?, ?, NULL)",
                (
                    mission_id,
                    objective,
                    workspace,
                    "Preparing read-only Codex mission",
                    now,
                    now,
                    now,
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
        ):
            result[key.removesuffix("_json")] = json.loads(result.pop(key))
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
        return result

    def list(self) -> list[dict[str, Any]]:
        ids = self._conn.execute(
            "SELECT id FROM codex_missions ORDER BY updated_at DESC"
        ).fetchall()
        return [self.get(row[0], include_events=False) for row in ids]

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def rollback_database(path: str | Path) -> None:
        """Remove only Phase 1 observer tables, preserving unrelated data."""
        connection = sqlite3.connect(str(path))
        try:
            with connection:
                for table in (
                    "codex_raw_events",
                    "codex_milestones",
                    "codex_events",
                    "codex_missions",
                    "codex_observer_migrations",
                ):
                    connection.execute(f"DROP TABLE IF EXISTS {table}")
        finally:
            connection.close()
