"""Truthful, restart-safe action lifecycle and audit ledger."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .lifecycle import (
    LEGAL_TRANSITIONS,
    ActionState,
    IllegalActionTransition,
)
from .lifecycle import (
    ActionErrorCode as ActionError,
)
from .models import ActionAttempt, ActionProposal, Authorization, Verification, utc_now


@dataclass(frozen=True)
class ExecutionOutcome:
    success: bool
    response: Any
    error: Optional[ActionError] = None


class ActionLedger:
    """Persistent projection plus immutable attempts, verifications, and audit."""

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
            CREATE TABLE IF NOT EXISTS action_schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS action_records (
                action_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                action_type TEXT NOT NULL,
                proposal_json TEXT NOT NULL,
                state TEXT NOT NULL,
                error_kind TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS action_attempts (
                attempt_id TEXT PRIMARY KEY,
                action_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                body_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(action_id, attempt_number),
                FOREIGN KEY(action_id) REFERENCES action_records(action_id)
            );
            CREATE TABLE IF NOT EXISTS action_verifications (
                verification_id TEXT PRIMARY KEY,
                action_id TEXT NOT NULL,
                body_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(action_id) REFERENCES action_records(action_id)
            );
            CREATE TABLE IF NOT EXISTS action_audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                action_id TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT NOT NULL,
                reason TEXT NOT NULL,
                event_at TEXT NOT NULL,
                details_json TEXT NOT NULL,
                FOREIGN KEY(action_id) REFERENCES action_records(action_id)
            );
            CREATE TRIGGER IF NOT EXISTS action_attempts_immutable_update
            BEFORE UPDATE ON action_attempts BEGIN
                SELECT RAISE(ABORT, 'action attempts are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS action_attempts_immutable_delete
            BEFORE DELETE ON action_attempts BEGIN
                SELECT RAISE(ABORT, 'action attempts are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS action_verifications_immutable_update
            BEFORE UPDATE ON action_verifications BEGIN
                SELECT RAISE(ABORT, 'verifications are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS action_verifications_immutable_delete
            BEFORE DELETE ON action_verifications BEGIN
                SELECT RAISE(ABORT, 'verifications are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS action_audit_immutable_update
            BEFORE UPDATE ON action_audit BEGIN
                SELECT RAISE(ABORT, 'audit events are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS action_audit_immutable_delete
            BEFORE DELETE ON action_audit BEGIN
                SELECT RAISE(ABORT, 'audit events are immutable');
            END;
            INSERT OR IGNORE INTO action_schema_migrations(version, applied_at)
                VALUES (1, CURRENT_TIMESTAMP);
            COMMIT;
            """
        )

    def propose(self, proposal: ActionProposal) -> tuple[str, bool]:
        if not proposal.idempotency_key:
            raise ValueError("idempotency_key must not be empty")
        existing = self._conn.execute(
            "SELECT action_id FROM action_records WHERE idempotency_key = ?",
            (proposal.idempotency_key,),
        ).fetchone()
        if existing:
            return existing[0], False
        now = utc_now()
        with self._conn:
            self._conn.execute(
                "INSERT INTO action_records VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    proposal.id,
                    proposal.idempotency_key,
                    proposal.action_type,
                    proposal.to_json(),
                    ActionState.PROPOSED.value,
                    now,
                    now,
                ),
            )
            self._audit(
                proposal.id, None, ActionState.PROPOSED, "proposal-recorded", {}
            )
        return proposal.id, True

    def state(self, action_id: str) -> ActionState:
        row = self._conn.execute(
            "SELECT state FROM action_records WHERE action_id = ?", (action_id,)
        ).fetchone()
        if not row:
            raise KeyError(action_id)
        return ActionState(row[0])

    def transition(
        self,
        action_id: str,
        target: ActionState,
        reason: str,
        *,
        error: Optional[ActionError] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        current = self.state(action_id)
        if target not in LEGAL_TRANSITIONS[current]:
            raise IllegalActionTransition(
                f"illegal action transition: {current.value} -> {target.value}"
            )
        with self._conn:
            self._conn.execute(
                "UPDATE action_records SET state = ?, error_kind = ?, updated_at = ? "
                "WHERE action_id = ?",
                (target.value, error.value if error else None, utc_now(), action_id),
            )
            self._audit(action_id, current, target, reason, details or {})

    def authorize(self, authorization: Authorization) -> None:
        if authorization.decision != "authorized":
            self.transition(
                authorization.action_id,
                ActionState.FAILED,
                "authorization-denied",
                error=ActionError.DENIED,
                details={"authorization": authorization.to_dict()},
            )
            return
        self.transition(
            authorization.action_id,
            ActionState.AUTHORIZED,
            "authorization-granted",
            details={"authorization": authorization.to_dict()},
        )

    def add_attempt(self, attempt: ActionAttempt) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO action_attempts VALUES (?, ?, ?, ?, ?)",
                (
                    attempt.id,
                    attempt.action_id,
                    attempt.attempt_number,
                    attempt.to_json(),
                    attempt.created_at,
                ),
            )

    def add_verification(self, verification: Verification) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO action_verifications VALUES (?, ?, ?, ?)",
                (
                    verification.id,
                    verification.action_id,
                    verification.to_json(),
                    verification.created_at,
                ),
            )

    def attempt_count(self, action_id: str) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM action_attempts WHERE action_id = ?", (action_id,)
            ).fetchone()[0]
        )

    def recovery_queue(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT action_id FROM action_records WHERE state IN (?, ?) "
            "ORDER BY created_at",
            (ActionState.EXECUTING.value, ActionState.EFFECT_PENDING.value),
        ).fetchall()
        return [row[0] for row in rows]

    def recover_ambiguous_executions(self) -> int:
        rows = self._conn.execute(
            "SELECT action_id FROM action_records WHERE state = ?",
            (ActionState.EXECUTING.value,),
        ).fetchall()
        for (action_id,) in rows:
            self.transition(
                action_id,
                ActionState.NEEDS_ATTENTION,
                "restart-during-execution",
                error=ActionError.AMBIGUOUS_EFFECT,
            )
        return len(rows)

    def audit(self, action_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT sequence, from_state, to_state, reason, event_at, details_json "
            "FROM action_audit WHERE action_id = ? ORDER BY sequence",
            (action_id,),
        ).fetchall()
        return [
            {
                "sequence": row[0],
                "from_state": row[1],
                "to_state": row[2],
                "reason": row[3],
                "event_at": row[4],
                "details": json.loads(row[5]),
            }
            for row in rows
        ]

    def _audit(
        self,
        action_id: str,
        source: Optional[ActionState],
        target: ActionState,
        reason: str,
        details: dict[str, Any],
    ) -> None:
        self._conn.execute(
            "INSERT INTO action_audit(action_id, from_state, to_state, reason, "
            "event_at, details_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                action_id,
                source.value if source else None,
                target.value,
                reason,
                utc_now(),
                json.dumps(details, sort_keys=True),
            ),
        )

    def close(self) -> None:
        self._conn.close()


class ActionRuntime:
    def __init__(self, ledger: ActionLedger) -> None:
        self.ledger = ledger

    def execute(
        self,
        action_id: str,
        executor: Callable[[], ExecutionOutcome],
        *,
        executor_name: str,
    ) -> ExecutionOutcome:
        current = self.ledger.state(action_id)
        if current is not ActionState.AUTHORIZED:
            return ExecutionOutcome(
                False,
                f"action already handled or not authorized ({current.value})",
                ActionError.INVALID,
            )
        self.ledger.transition(action_id, ActionState.EXECUTING, "execution-started")
        try:
            outcome = executor()
        except Exception as exc:
            outcome = ExecutionOutcome(False, str(exc), ActionError.TRANSIENT)
        attempt = ActionAttempt(
            action_id=action_id,
            attempt_number=self.ledger.attempt_count(action_id) + 1,
            executor=executor_name,
            tool_succeeded=outcome.success,
            tool_response=outcome.response,
            error_kind=outcome.error.value if outcome.error else None,
            causal_parents=[action_id],
            provenance={"component": "verified-action-runtime"},
        )
        self.ledger.add_attempt(attempt)
        if outcome.success:
            self.ledger.transition(
                action_id,
                ActionState.EFFECT_PENDING,
                "tool-reported-success-awaiting-independent-verification",
                details={"attempt_id": attempt.id},
            )
        else:
            self.ledger.transition(
                action_id,
                ActionState.FAILED,
                "tool-reported-failure",
                error=outcome.error or ActionError.PERMANENT,
                details={"attempt_id": attempt.id},
            )
        return outcome

    def verify(
        self,
        action_id: str,
        observer: Callable[[], tuple[bool, Any]],
        *,
        verifier_name: str,
    ) -> Verification:
        if self.ledger.state(action_id) is not ActionState.EFFECT_PENDING:
            raise IllegalActionTransition("only effect_pending actions can be verified")
        try:
            succeeded, observed = observer()
            error = None if succeeded else ActionError.VERIFICATION_FAILED
        except Exception as exc:
            succeeded, observed, error = False, str(exc), ActionError.AMBIGUOUS_EFFECT
        verification = Verification(
            action_id=action_id,
            verifier=verifier_name,
            observed_effect=observed,
            succeeded=succeeded,
            error_kind=error.value if error else None,
            causal_parents=[action_id],
            provenance={"component": "independent-verifier"},
        )
        self.ledger.add_verification(verification)
        if succeeded:
            self.ledger.transition(
                action_id,
                ActionState.VERIFIED,
                "independent-effect-observed",
                details={"verification_id": verification.id},
            )
        elif error is ActionError.AMBIGUOUS_EFFECT:
            self.ledger.transition(
                action_id,
                ActionState.NEEDS_ATTENTION,
                "verification-ambiguous",
                error=error,
                details={"verification_id": verification.id},
            )
        else:
            self.ledger.transition(
                action_id,
                ActionState.FAILED,
                "independent-verification-failed",
                error=error,
                details={"verification_id": verification.id},
            )
        return verification
