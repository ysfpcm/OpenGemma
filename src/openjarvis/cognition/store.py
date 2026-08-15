"""SQLite persistence, migrations, rollback, and durable action recovery."""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from .lifecycle import (
    ActionErrorCode,
    ActionState,
    IllegalActionTransition,
    validate_transition,
)
from .models import (
    ActionAttempt,
    ActionProposal,
    Authorization,
    CognitionContract,
    Verification,
    utc_now,
)

MIGRATION_VERSION = 1


@dataclass(frozen=True)
class ActionRecord:
    id: str
    idempotency_key: str
    state: ActionState
    proposal: ActionProposal
    authorization: Optional[Authorization]
    error_code: Optional[ActionErrorCode]
    error_message: Optional[str]


class CognitionStore:
    """Phase 0 durable ledger. All causal evidence is append-only."""

    def __init__(self, db_path: str | Path, *, auto_migrate: bool = True) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        self._conn.commit()
        if auto_migrate:
            self.migrate()

    @property
    def migration_version(self) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        return int(row[0])

    def backup(self, destination: str | Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(destination))
        try:
            self._conn.backup(target)
        finally:
            target.close()
        return destination

    def migrate(self) -> None:
        if self.migration_version >= MIGRATION_VERSION:
            return
        migration = Path(__file__).with_name("migrations") / "0001_phase0.sql"
        with self._conn:
            self._conn.executescript(migration.read_text(encoding="utf-8"))
            self._conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) "
                "VALUES (?, ?, ?)",
                (1, "phase0", utc_now()),
            )

    def rollback(self) -> None:
        if self.migration_version != MIGRATION_VERSION:
            return
        migration = Path(__file__).with_name("migrations") / "0001_phase0.down.sql"
        with self._conn:
            self._conn.executescript(migration.read_text(encoding="utf-8"))
            self._conn.execute("DELETE FROM schema_migrations WHERE version = 1")

    @staticmethod
    def restore_backup(backup: str | Path, destination: str | Path) -> None:
        shutil.copy2(Path(backup), Path(destination))

    def save_contract(self, contract: CognitionContract) -> None:
        self._conn.execute(
            "INSERT INTO cognition_records(id, kind, schema_version, "
            "payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                contract.id,
                contract.contract_type,
                contract.schema_version,
                contract.to_json(),
                contract.created_at,
            ),
        )
        self._conn.commit()

    def load_contract(self, contract_id: str) -> CognitionContract:
        row = self._conn.execute(
            "SELECT payload_json FROM cognition_records WHERE id = ?", (contract_id,)
        ).fetchone()
        if row is None:
            raise KeyError(contract_id)
        return CognitionContract.from_json(row[0])

    def propose(self, proposal: ActionProposal, idempotency_key: str) -> ActionRecord:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must not be empty")
        existing = self.get_action_by_key(idempotency_key)
        if existing:
            return existing
        now = utc_now()
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO actions(id, idempotency_key, proposal_json, state, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        proposal.id,
                        idempotency_key,
                        proposal.to_json(),
                        ActionState.PROPOSED.value,
                        now,
                        now,
                    ),
                )
                self._append_audit(
                    proposal.id, None, ActionState.PROPOSED, "proposal persisted"
                )
        except sqlite3.IntegrityError:
            existing = self.get_action_by_key(idempotency_key)
            if existing:
                return existing
            raise
        return self.get_action(proposal.id)

    def authorize(self, action_id: str, authorization: Authorization) -> ActionRecord:
        record = self.get_action(action_id)
        self._transition(action_id, record.state, ActionState.AUTHORIZED, "authorized")
        self._conn.execute(
            "UPDATE actions SET authorization_json = ?, updated_at = ? WHERE id = ?",
            (authorization.to_json(), utc_now(), action_id),
        )
        self._conn.commit()
        return self.get_action(action_id)

    def deny(
        self, action_id: str, message: str = "authorization denied"
    ) -> ActionRecord:
        current = self.get_action(action_id)
        self._transition(
            action_id,
            current.state,
            ActionState.FAILED,
            message,
            ActionErrorCode.DENIED,
        )
        return self.get_action(action_id)

    def execute(
        self,
        action_id: str,
        executor: Callable[[ActionProposal], Tuple[bool, Any]],
    ) -> ActionRecord:
        record = self.get_action(action_id)
        if record.state is not ActionState.AUTHORIZED:
            return record
        self._transition(
            action_id, record.state, ActionState.EXECUTING, "execution started"
        )
        attempt_id = uuid.uuid4().hex
        self._append_attempt(
            action_id, attempt_id, "started", {"proposal": record.proposal.to_dict()}
        )
        try:
            success, response = executor(record.proposal)
        except TimeoutError as exc:
            self._append_attempt(
                action_id,
                attempt_id,
                "tool_response",
                {"success": False, "error": str(exc)},
            )
            self._transition(
                action_id,
                ActionState.EXECUTING,
                ActionState.NEEDS_ATTENTION,
                "executor timed out; effect is ambiguous",
                ActionErrorCode.AMBIGUOUS_EFFECT,
            )
            return self.get_action(action_id)
        except Exception as exc:
            self._append_attempt(
                action_id,
                attempt_id,
                "tool_response",
                {"success": False, "error": str(exc)},
            )
            self._transition(
                action_id,
                ActionState.EXECUTING,
                ActionState.FAILED,
                "executor raised an error",
                ActionErrorCode.PERMANENT,
                {"message": str(exc)},
            )
            return self.get_action(action_id)

        self._append_attempt(
            action_id,
            attempt_id,
            "tool_response",
            {"success": bool(success), "response": response},
        )
        if success:
            self._transition(
                action_id,
                ActionState.EXECUTING,
                ActionState.EFFECT_PENDING,
                "tool reported success; independent verification pending",
            )
        else:
            self._transition(
                action_id,
                ActionState.EXECUTING,
                ActionState.FAILED,
                "tool reported failure",
                ActionErrorCode.PERMANENT,
                {"tool_response": response},
            )
        return self.get_action(action_id)

    def verify(
        self, action_id: str, verification: Verification, observed_effect: bool
    ) -> ActionRecord:
        record = self.get_action(action_id)
        if record.state not in {
            ActionState.EFFECT_PENDING,
            ActionState.NEEDS_ATTENTION,
        }:
            raise IllegalActionTransition(
                f"cannot verify action in state {record.state.value}"
            )
        self._conn.execute(
            "INSERT INTO verifications(id, action_id, payload_json, "
            "observed_effect, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                verification.id,
                action_id,
                verification.to_json(),
                int(observed_effect),
                utc_now(),
            ),
        )
        self._conn.commit()
        target = ActionState.VERIFIED if observed_effect else ActionState.FAILED
        error = None if observed_effect else ActionErrorCode.VERIFICATION_FAILED
        reason = (
            "expected effect independently observed"
            if observed_effect
            else "expected effect not observed"
        )
        self._transition(
            action_id,
            record.state,
            target,
            reason,
            error,
            {"verification_id": verification.id},
        )
        return self.get_action(action_id)

    def recover_interrupted(self) -> int:
        rows = self._conn.execute(
            "SELECT id FROM actions WHERE state = ?", (ActionState.EXECUTING.value,)
        ).fetchall()
        for (action_id,) in rows:
            self._transition(
                action_id,
                ActionState.EXECUTING,
                ActionState.NEEDS_ATTENTION,
                "runtime restarted during execution; effect is ambiguous",
                ActionErrorCode.AMBIGUOUS_EFFECT,
            )
        return len(rows)

    def get_action(self, action_id: str) -> ActionRecord:
        row = self._conn.execute(
            "SELECT id, idempotency_key, state, proposal_json, authorization_json, "
            "error_code, error_message FROM actions WHERE id = ?",
            (action_id,),
        ).fetchone()
        if row is None:
            raise KeyError(action_id)
        return self._row_to_action(row)

    def get_action_by_key(self, idempotency_key: str) -> Optional[ActionRecord]:
        row = self._conn.execute(
            "SELECT id, idempotency_key, state, proposal_json, authorization_json, "
            "error_code, error_message FROM actions WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return self._row_to_action(row) if row else None

    def audit_events(self, action_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT sequence, event_id, from_state, to_state, reason, error_code, "
            "evidence_json, recorded_at FROM action_audit_events "
            "WHERE action_id = ? ORDER BY sequence",
            (action_id,),
        ).fetchall()
        return [
            {
                "sequence": row[0],
                "event_id": row[1],
                "from_state": row[2],
                "to_state": row[3],
                "reason": row[4],
                "error_code": row[5],
                "evidence": json.loads(row[6]),
                "recorded_at": row[7],
            }
            for row in rows
        ]

    def attempt_records(self, action_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT record_id, attempt_id, phase, payload_json, recorded_at "
            "FROM action_attempt_records WHERE action_id = ? "
            "ORDER BY recorded_at, record_id",
            (action_id,),
        ).fetchall()
        return [
            {
                "record_id": r[0],
                "attempt_id": r[1],
                "phase": r[2],
                "payload": json.loads(r[3]),
                "recorded_at": r[4],
            }
            for r in rows
        ]

    def _row_to_action(self, row: tuple[Any, ...]) -> ActionRecord:
        return ActionRecord(
            id=row[0],
            idempotency_key=row[1],
            state=ActionState(row[2]),
            proposal=ActionProposal.from_json(row[3]),
            authorization=Authorization.from_json(row[4]) if row[4] else None,
            error_code=ActionErrorCode(row[5]) if row[5] else None,
            error_message=row[6],
        )

    def _transition(
        self,
        action_id: str,
        current: ActionState,
        target: ActionState,
        reason: str,
        error_code: Optional[ActionErrorCode] = None,
        evidence: Optional[dict[str, Any]] = None,
    ) -> None:
        validate_transition(current, target)
        now = utc_now()
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE actions SET state = ?, error_code = ?, error_message = ?, "
                "updated_at = ? WHERE id = ? AND state = ?",
                (
                    target.value,
                    error_code.value if error_code else None,
                    reason if error_code else None,
                    now,
                    action_id,
                    current.value,
                ),
            )
            if cursor.rowcount != 1:
                raise IllegalActionTransition("action state changed concurrently")
            self._append_audit(
                action_id, current, target, reason, error_code, evidence or {}
            )

    def _append_audit(
        self,
        action_id: str,
        current: Optional[ActionState],
        target: ActionState,
        reason: str,
        error_code: Optional[ActionErrorCode] = None,
        evidence: Optional[dict[str, Any]] = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO action_audit_events(event_id, action_id, from_state, "
            "to_state, reason, error_code, evidence_json, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                action_id,
                current.value if current else None,
                target.value,
                reason,
                error_code.value if error_code else None,
                json.dumps(evidence or {}, sort_keys=True),
                utc_now(),
            ),
        )

    def _append_attempt(
        self, action_id: str, attempt_id: str, phase: str, payload: dict[str, Any]
    ) -> None:
        contract = ActionAttempt(
            causal_parents=[action_id],
            attributes={"attempt_id": attempt_id, "phase": phase, **payload},
        )
        self._conn.execute(
            "INSERT INTO action_attempt_records(record_id, attempt_id, action_id, "
            "phase, "
            "payload_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (contract.id, attempt_id, action_id, phase, contract.to_json(), utc_now()),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CognitionStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
