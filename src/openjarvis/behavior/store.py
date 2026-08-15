"""SQLite-backed memory for Ophanim's teachable behavior examples."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from openjarvis.behavior.models import CorrectionExample, SUPPORTED_ACTIONS
from openjarvis.behavior.normalization import normalize_text, text_similarity
from openjarvis.core.paths import get_data_dir


@dataclass(frozen=True, slots=True)
class CorrectionMatch:
    example: CorrectionExample
    similarity: float


def _json_text(value: Mapping[str, Any] | None) -> str:
    return json.dumps(dict(value or {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class BehaviorStore:
    """Persist corrections separately from long-form document memory."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        path = db_path if db_path is not None else get_data_dir() / "behavior.db"
        if path == ":memory:":
            self._path: Path | None = None
            connect_path = ":memory:"
        else:
            path_obj = Path(path).expanduser()
            path_obj.parent.mkdir(parents=True, exist_ok=True)
            self._path = path_obj
            connect_path = str(path_obj)
        self._conn = sqlite3.connect(connect_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self.initialize()

    @property
    def path(self) -> Path | None:
        return self._path

    def initialize(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS behavior_corrections (
                    id INTEGER PRIMARY KEY,
                    before_text TEXT NOT NULL,
                    normalized_text TEXT NOT NULL,
                    corrected_action TEXT NOT NULL,
                    corrected_entity_id TEXT,
                    corrected_entity_name TEXT,
                    recent_topic_entity_id TEXT,
                    predicted_action TEXT,
                    predicted_entity_id TEXT,
                    corrected_parameters_json TEXT NOT NULL DEFAULT '{}',
                    predicted_parameters_json TEXT NOT NULL DEFAULT '{}',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_behavior_corrections_normalized
                    ON behavior_corrections(normalized_text);
                """
            )
            columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(behavior_corrections)")
            }
            for column in ("corrected_parameters_json", "predicted_parameters_json"):
                if column not in columns:
                    self._conn.execute(
                        f"ALTER TABLE behavior_corrections ADD COLUMN {column} TEXT NOT NULL DEFAULT '{{}}'"
                    )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "BehaviorStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def record_correction(
        self,
        *,
        before_text: str,
        corrected_action: str,
        corrected_entity_id: str | None = None,
        corrected_entity_name: str | None = None,
        recent_topic_entity_id: str | None = None,
        predicted_action: str | None = None,
        predicted_entity_id: str | None = None,
        corrected_parameters: Mapping[str, Any] | None = None,
        predicted_parameters: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> CorrectionExample:
        action = str(corrected_action or "").strip().lower()
        if action not in SUPPORTED_ACTIONS:
            raise ValueError(f"corrected_action must be one of {sorted(SUPPORTED_ACTIONS)}")
        before = str(before_text or "").strip()
        if not before:
            raise ValueError("before_text cannot be empty")
        timestamp = (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO behavior_corrections (
                    before_text, normalized_text, corrected_action,
                    corrected_entity_id, corrected_entity_name,
                    recent_topic_entity_id, predicted_action, predicted_entity_id,
                    corrected_parameters_json, predicted_parameters_json,
                    context_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    before,
                    normalize_text(before),
                    action,
                    corrected_entity_id,
                    corrected_entity_name,
                    recent_topic_entity_id,
                    predicted_action,
                    predicted_entity_id,
                    _json_text(corrected_parameters),
                    _json_text(predicted_parameters),
                    _json_text(context),
                    timestamp,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM behavior_corrections WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        assert row is not None
        return self._row_to_example(row)

    def find_matches(
        self,
        text: str,
        *,
        recent_topic_entity_id: str | None = None,
        limit: int = 50,
    ) -> list[CorrectionMatch]:
        """Find exact and typo-near examples, newest examples first on ties."""
        normalized = normalize_text(text)
        if not normalized:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM behavior_corrections ORDER BY id DESC LIMIT ?",
                (max(limit, 1) * 20,),
            ).fetchall()
        matches = [
            CorrectionMatch(
                example=self._row_to_example(row),
                similarity=text_similarity(normalized, row["normalized_text"]),
            )
            for row in rows
        ]
        matches.sort(key=lambda item: (item.similarity, item.example.id), reverse=True)
        return matches[: max(limit, 0)]

    def list_corrections(self, *, limit: int = 100) -> list[CorrectionExample]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM behavior_corrections ORDER BY id DESC LIMIT ?",
                (max(limit, 0),),
            ).fetchall()
        return [self._row_to_example(row) for row in rows]

    def count(self) -> int:
        with self._lock:
            return int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM behavior_corrections"
                ).fetchone()[0]
            )

    @staticmethod
    def _row_to_example(row: sqlite3.Row) -> CorrectionExample:
        return CorrectionExample(
            id=int(row["id"]),
            before_text=str(row["before_text"]),
            normalized_text=str(row["normalized_text"]),
            corrected_action=str(row["corrected_action"]),
            corrected_entity_id=row["corrected_entity_id"],
            corrected_entity_name=row["corrected_entity_name"],
            recent_topic_entity_id=row["recent_topic_entity_id"],
            predicted_action=row["predicted_action"],
            predicted_entity_id=row["predicted_entity_id"],
            corrected_parameters=json.loads(row["corrected_parameters_json"] or "{}"),
            predicted_parameters=json.loads(row["predicted_parameters_json"] or "{}"),
            context=json.loads(row["context_json"] or "{}"),
            created_at=str(row["created_at"]),
        )


__all__ = ["BehaviorStore", "CorrectionMatch"]
