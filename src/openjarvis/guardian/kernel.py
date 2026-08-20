"""Fail-closed Guardian Kernel for Phase 3 registered side effects.

No adapter is selected from model output.  A caller may propose an action, but
only a registry entry, a live scoped grant, fresh preconditions, and an
independent verifier can move it through the action ledger.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from openjarvis.cognition import (
    ActionError,
    ActionLedger,
    ActionProposal,
    ActionRuntime,
    ActionState,
    Authorization,
    ExecutionOutcome,
    Verification,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat()


@dataclass(frozen=True)
class PreconditionResult:
    """A named observation used immediately before a side effect."""

    name: str
    satisfied: bool
    observed_at: datetime
    details: dict[str, Any] = field(default_factory=dict)
    contradictory: bool = False


Executor = Callable[[ActionProposal], ExecutionOutcome]
Verifier = Callable[[ActionProposal], tuple[bool, Any]]
Precondition = Callable[[ActionProposal], list[PreconditionResult]]


@dataclass(frozen=True)
class ActionDefinition:
    action_type: str
    input_schema: dict[str, Any]
    capability: str
    risk_class: str
    consequence_class: str
    preconditions: Precondition
    executor: Executor
    verifier: Verifier
    freshness_seconds: int = 60
    idempotency_strategy: str = "caller-key"
    expected_effect: dict[str, Any] = field(default_factory=dict)
    compensation: Optional[Executor] = None
    redaction_policy: str = "metadata-only"
    timeout_classification: ActionError = ActionError.AMBIGUOUS_EFFECT
    retryable_errors: frozenset[ActionError] = frozenset({ActionError.TRANSIENT})


class ActionRegistry:
    """Allow-list of typed adapters.  Unknown action types do not execute."""

    def __init__(self) -> None:
        self._definitions: dict[str, ActionDefinition] = {}

    def register(self, definition: ActionDefinition) -> None:
        if not definition.action_type or definition.action_type in self._definitions:
            raise ValueError("action type must be non-empty and registered once")
        if definition.input_schema.get("type") != "object":
            raise ValueError("registered action input schema must be an object schema")
        if definition.freshness_seconds < 0:
            raise ValueError("freshness_seconds must not be negative")
        self._definitions[definition.action_type] = definition

    def get(self, action_type: str) -> ActionDefinition:
        try:
            return self._definitions[action_type]
        except KeyError as exc:
            raise KeyError(f"unknown action type: {action_type}") from exc

    def validate(self, proposal: ActionProposal) -> Optional[str]:
        try:
            definition = self.get(proposal.action_type)
        except KeyError as exc:
            return str(exc)
        schema = definition.input_schema
        parameters = proposal.parameters
        if not isinstance(parameters, dict):
            return "action parameters must be an object"
        required = schema.get("required", [])
        if any(key not in parameters for key in required):
            return "action parameters are missing required fields"
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and any(
            key not in properties for key in parameters
        ):
            return "action parameters contain an unregistered field"
        for key, spec in properties.items():
            if key not in parameters or "type" not in spec:
                continue
            expected = spec["type"]
            value = parameters[key]
            expected_type = {
                "string": str,
                "boolean": bool,
                "number": (int, float),
                "integer": int,
                "object": dict,
                "array": list,
            }.get(expected)
            if expected_type and (
                not isinstance(value, expected_type)
                or (expected in {"integer", "number"} and isinstance(value, bool))
            ):
                return f"action parameter {key!r} must be {expected}"
        return None


@dataclass(frozen=True)
class GuardianDecision:
    allowed: bool
    authorization: Authorization
    reason: str = ""


@dataclass(frozen=True)
class GuardianResult:
    outcome: ExecutionOutcome
    verification: Optional[Verification]
    state: ActionState
    exception_summary: Optional[str] = None


class GuardianKernel:
    """Persistent authority store and sole executor for registered actions."""

    def __init__(self, ledger: ActionLedger, registry: ActionRegistry) -> None:
        self.ledger = ledger
        self.registry = registry
        self._conn = sqlite3.connect(
            str(ledger.path), isolation_level=None, check_same_thread=False
        )
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            BEGIN;
            CREATE TABLE IF NOT EXISTS guardian_schema_migrations (
                version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS guardian_grants (
                grant_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                capability TEXT NOT NULL, scope_json TEXT NOT NULL,
                expires_at TEXT, revoked_at TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS guardian_denials (
                fingerprint TEXT PRIMARY KEY, reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS guardian_audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, action_id TEXT,
                event TEXT NOT NULL, details_json TEXT NOT NULL, event_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS guardian_emergency_stop (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                active INTEGER NOT NULL,
                reason TEXT, updated_at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS guardian_audit_immutable_update
            BEFORE UPDATE ON guardian_audit BEGIN
                SELECT RAISE(ABORT, 'guardian audit is immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS guardian_audit_immutable_delete
            BEFORE DELETE ON guardian_audit BEGIN
                SELECT RAISE(ABORT, 'guardian audit is immutable');
            END;
            INSERT OR IGNORE INTO guardian_schema_migrations
                VALUES (1, CURRENT_TIMESTAMP);
            COMMIT;
            """
        )

    def close(self) -> None:
        self._conn.close()

    def grant(
        self,
        *,
        grant_id: str,
        session_id: str,
        capability: str,
        scope: dict[str, Any],
        expires_in_seconds: Optional[int] = None,
    ) -> None:
        if not grant_id or not session_id or not capability:
            raise ValueError("grant_id, session_id, and capability are required")
        expires_at = (
            _timestamp(_now() + timedelta(seconds=expires_in_seconds))
            if expires_in_seconds is not None
            else None
        )
        self._conn.execute(
            "INSERT INTO guardian_grants VALUES (?, ?, ?, ?, ?, NULL, ?)",
            (
                grant_id,
                session_id,
                capability,
                json.dumps(scope, sort_keys=True),
                expires_at,
                _timestamp(_now()),
            ),
        )
        self._audit(
            None, "grant-created", {"grant_id": grant_id, "session_id": session_id}
        )

    def revoke(
        self, session_id: str, *, reason: str = "Marc revoked authority"
    ) -> None:
        self._conn.execute(
            "UPDATE guardian_grants SET revoked_at = ? "
            "WHERE session_id = ? AND revoked_at IS NULL",
            (_timestamp(_now()), session_id),
        )
        self._audit(
            None, "session-revoked", {"session_id": session_id, "reason": reason}
        )

    def emergency_stop(self, *, reason: str = "emergency stop") -> None:
        self._conn.execute(
            "INSERT INTO guardian_emergency_stop VALUES (1, 1, ?, ?) "
            "ON CONFLICT(singleton) DO UPDATE SET active=1, "
            "reason=excluded.reason, updated_at=excluded.updated_at",
            (reason, _timestamp(_now())),
        )
        self._audit(None, "emergency-stop", {"reason": reason})

    def clear_emergency_stop(self, *, authority: str) -> None:
        if authority != "Marc":
            raise PermissionError("only Marc may clear the emergency stop")
        self._conn.execute(
            "UPDATE guardian_emergency_stop SET active=0, updated_at=? "
            "WHERE singleton=1",
            (_timestamp(_now()),),
        )
        self._audit(None, "emergency-stop-cleared", {"authority": authority})

    def authorize(
        self, proposal: ActionProposal, *, session_id: str, authority: str
    ) -> GuardianDecision:
        action_id, created = self.ledger.propose(proposal)
        if action_id != proposal.id:
            return GuardianDecision(
                False,
                Authorization(
                    action_id=action_id,
                    decision="denied",
                    authority="Guardian",
                    scope={},
                ),
                "idempotency key already belongs to another action",
            )
        if not created:
            stored = self.ledger.proposal(action_id)
            if stored.to_json() != proposal.to_json():
                reason = "idempotency key conflicts with the stored proposal"
                self._audit(action_id, "idempotency-conflict", {"reason": reason})
                return GuardianDecision(
                    False,
                    Authorization(
                        action_id=action_id,
                        decision="denied",
                        authority="Guardian",
                        scope={},
                    ),
                    reason,
                )
            state = self.ledger.state(action_id)
            if state is ActionState.AUTHORIZED:
                existing = self._stored_authorization(action_id)
                if existing is not None:
                    return GuardianDecision(
                        True, existing, "action is already authorized"
                    )
            if state is not ActionState.PROPOSED:
                reason = f"idempotent action is already handled ({state.value})"
                return GuardianDecision(
                    False,
                    Authorization(
                        action_id=action_id,
                        decision="denied",
                        authority="Guardian",
                        scope={},
                    ),
                    reason,
                )
        error = self.registry.validate(proposal)
        if error:
            return self._deny(proposal, error, ActionError.INVALID)
        if self._stopped():
            return self._deny(
                proposal, "emergency stop is active", ActionError.CANCELED
            )
        fingerprint = self._fingerprint(proposal)
        if self._conn.execute(
            "SELECT 1 FROM guardian_denials WHERE fingerprint=?", (fingerprint,)
        ).fetchone():
            return self._deny(
                proposal, "an equivalent action was already denied", ActionError.DENIED
            )
        definition = self.registry.get(proposal.action_type)
        grant = self._active_grant(session_id, definition.capability, proposal)
        if grant is None:
            return self._deny(
                proposal, "no live grant covers this exact action", ActionError.DENIED
            )
        authorization = Authorization(
            action_id=proposal.id,
            authority=authority,
            scope={
                "grant_id": grant[0],
                "session_id": session_id,
                "capability": definition.capability,
                "action_type": proposal.action_type,
                "target": proposal.parameters.get("target"),
            },
            causal_parents=[proposal.id],
            provenance={"component": "guardian-kernel"},
        )
        self.ledger.authorize(authorization)
        self._audit(proposal.id, "authorization-granted", authorization.to_dict())
        return GuardianDecision(True, authorization)

    def execute(
        self,
        action_id: str,
        authorization: Authorization,
        *,
        verify: bool = True,
    ) -> GuardianResult:
        proposal = self.ledger.proposal(action_id)
        if self.ledger.state(action_id) is not ActionState.AUTHORIZED:
            return GuardianResult(
                ExecutionOutcome(
                    False, "action is not authorized", ActionError.INVALID
                ),
                None,
                self.ledger.state(action_id),
            )
        error = self.registry.validate(proposal)
        if (
            error
            or self._stopped()
            or not self._authorization_is_live(authorization, proposal)
        ):
            reason = (
                error or "emergency stop is active"
                if self._stopped()
                else error or "authorization was revoked or out of scope"
            )
            code = ActionError.INVALID if error else ActionError.CANCELED
            self.ledger.transition(
                action_id,
                ActionState.FAILED,
                "guardian-pre-execution-denial",
                error=code,
                details={"reason": reason},
            )
            self._audit(action_id, "execution-blocked", {"reason": reason})
            return GuardianResult(
                ExecutionOutcome(False, reason, code),
                None,
                self.ledger.state(action_id),
                reason,
            )
        definition = self.registry.get(proposal.action_type)
        blocked = self._check_preconditions(proposal, definition)
        if blocked:
            self.ledger.transition(
                action_id,
                ActionState.FAILED,
                "guardian-preconditions-failed",
                error=ActionError.STALE,
                details={"preconditions": blocked},
            )
            self._audit(action_id, "execution-blocked", {"preconditions": blocked})
            return GuardianResult(
                ExecutionOutcome(False, "preconditions failed", ActionError.STALE),
                None,
                self.ledger.state(action_id),
                "Action was not run: preconditions changed.",
            )
        runtime = ActionRuntime(self.ledger)
        outcome = runtime.execute(
            action_id,
            lambda: self._run_executor(definition, proposal),
            executor_name=proposal.action_type,
        )
        if not outcome.success:
            summary = (
                "Effect may have occurred; verification is required."
                if outcome.error is ActionError.AMBIGUOUS_EFFECT
                else "Action failed before verified effect."
            )
            self._audit(
                action_id,
                "execution-finished",
                {
                    "success": False,
                    "error": outcome.error.value if outcome.error else None,
                },
            )
            return GuardianResult(outcome, None, self.ledger.state(action_id), summary)
        if not verify:
            # A direct user command has a confirmed adapter dispatch, while
            # Home Assistant's event stream is the source of truth for the
            # eventual observed state. Leave the action effect-pending so the
            # context pipeline can settle it without delaying the chat reply.
            self._audit(
                action_id,
                "execution-dispatched",
                {"verification": "context_async"},
            )
            return GuardianResult(
                outcome,
                None,
                self.ledger.state(action_id),
                "Action dispatched; awaiting Home Assistant context observation.",
            )
        verification = runtime.verify(
            action_id,
            lambda: definition.verifier(proposal),
            verifier_name=f"{proposal.action_type}:independent-read",
        )
        summary = (
            None
            if verification.succeeded
            else "Action reported success, but independent verification failed."
        )
        self._audit(
            action_id, "verification-finished", {"succeeded": verification.succeeded}
        )
        return GuardianResult(
            outcome, verification, self.ledger.state(action_id), summary
        )

    def timeline(self, action_id: str) -> dict[str, Any]:
        rows = self._conn.execute(
            "SELECT event, details_json, event_at FROM guardian_audit "
            "WHERE action_id=? ORDER BY sequence",
            (action_id,),
        ).fetchall()
        return {
            "proposal": self.ledger.proposal(action_id).to_dict(),
            "state": self.ledger.state(action_id).value,
            "attempts": [item.to_dict() for item in self.ledger.attempts(action_id)],
            "verifications": [
                item.to_dict() for item in self.ledger.verifications(action_id)
            ],
            "action_audit": self.ledger.audit(action_id),
            "guardian_audit": [
                {"event": r[0], "details": json.loads(r[1]), "at": r[2]} for r in rows
            ],
        }

    def _deny(
        self, proposal: ActionProposal, reason: str, code: ActionError
    ) -> GuardianDecision:
        self._conn.execute(
            "INSERT OR IGNORE INTO guardian_denials VALUES (?, ?, ?)",
            (self._fingerprint(proposal), reason, _timestamp(_now())),
        )
        authorization = Authorization(
            action_id=proposal.id,
            decision="denied",
            authority="Guardian",
            scope={},
            causal_parents=[proposal.id],
            provenance={"component": "guardian-kernel"},
        )
        # Preserve the reason's error class in the shared action ledger.  A
        # stale/invalid/canceled denial must not be reported as a generic
        # policy denial when the causal record is inspected later.
        self.ledger.transition(
            proposal.id,
            ActionState.FAILED,
            "authorization-denied",
            error=code,
            details={"authorization": authorization.to_dict(), "reason": reason},
        )
        self._audit(proposal.id, "authorization-denied", {"reason": reason})
        return GuardianDecision(False, authorization, reason)

    def _active_grant(self, session_id: str, capability: str, proposal: ActionProposal):
        rows = self._conn.execute(
            "SELECT grant_id, scope_json, expires_at, revoked_at "
            "FROM guardian_grants WHERE session_id=? AND capability=?",
            (session_id, capability),
        ).fetchall()
        for row in rows:
            if row[3] or (row[2] and datetime.fromisoformat(row[2]) <= _now()):
                continue
            scope = json.loads(row[1])
            if scope.get("action_type") != proposal.action_type:
                continue
            if "target" in scope and scope["target"] != proposal.parameters.get(
                "target"
            ):
                continue
            parameter_hash = scope.get("parameters_hash")
            if parameter_hash and parameter_hash != self._parameters_hash(proposal):
                continue
            return row
        return None

    def _authorization_is_live(
        self, authorization: Authorization, proposal: ActionProposal
    ) -> bool:
        if (
            authorization.decision != "authorized"
            or authorization.action_id != proposal.id
        ):
            return False
        scope = authorization.scope
        if scope.get("action_type") != proposal.action_type or scope.get(
            "target"
        ) != proposal.parameters.get("target"):
            return False
        grant = self._active_grant(
            scope.get("session_id", ""), scope.get("capability", ""), proposal
        )
        return grant is not None and grant[0] == scope.get("grant_id")

    def _check_preconditions(
        self, proposal: ActionProposal, definition: ActionDefinition
    ) -> list[dict[str, Any]]:
        blocked = []
        try:
            results = definition.preconditions(proposal)
            iterator = iter(results)
        except Exception as exc:
            return [self._precondition_error(exc)]
        try:
            for result in iterator:
                try:
                    age = (_now() - result.observed_at).total_seconds()
                    details = (
                        result.details
                        if isinstance(result.details, dict)
                        else {"details_type": type(result.details).__name__}
                    )
                    blocked_result = (
                        not result.satisfied
                        or result.contradictory
                        or age < 0
                        or age > definition.freshness_seconds
                    )
                except Exception as exc:
                    blocked.append(self._precondition_error(exc))
                    continue
                if blocked_result:
                    blocked.append(
                        {
                            "name": result.name,
                            "satisfied": result.satisfied,
                            "contradictory": result.contradictory,
                            "age_seconds": age,
                            "details": details,
                        }
                    )
        except Exception as exc:
            blocked.append(self._precondition_error(exc))
        return blocked

    @staticmethod
    def _precondition_error(exc: Exception) -> dict[str, Any]:
        return {
            "name": "guardian-precondition-evaluation",
            "satisfied": False,
            "contradictory": True,
            "age_seconds": None,
            "details": {"error_type": type(exc).__name__},
        }

    @staticmethod
    def _run_executor(
        definition: ActionDefinition, proposal: ActionProposal
    ) -> ExecutionOutcome:
        try:
            return definition.executor(proposal)
        except TimeoutError as exc:
            return ExecutionOutcome(False, str(exc), definition.timeout_classification)

    def _stored_authorization(self, action_id: str) -> Optional[Authorization]:
        for event in reversed(self.ledger.audit(action_id)):
            if event["reason"] != "authorization-granted":
                continue
            raw = event["details"].get("authorization")
            if not isinstance(raw, dict):
                continue
            try:
                return Authorization.from_dict(raw)
            except (TypeError, ValueError):
                return None

    def _stopped(self) -> bool:
        row = self._conn.execute(
            "SELECT active FROM guardian_emergency_stop WHERE singleton=1"
        ).fetchone()
        return bool(row and row[0])

    @staticmethod
    def _fingerprint(proposal: ActionProposal) -> str:
        body = json.dumps(
            {"action_type": proposal.action_type, "parameters": proposal.parameters},
            sort_keys=True,
        )
        return hashlib.sha256(body.encode()).hexdigest()

    @staticmethod
    def _parameters_hash(proposal: ActionProposal) -> str:
        return hashlib.sha256(
            json.dumps(proposal.parameters, sort_keys=True).encode()
        ).hexdigest()

    def _audit(
        self, action_id: Optional[str], event: str, details: dict[str, Any]
    ) -> None:
        self._conn.execute(
            "INSERT INTO guardian_audit(action_id, event, details_json, event_at) "
            "VALUES (?, ?, ?, ?)",
            (action_id, event, json.dumps(details, sort_keys=True), _timestamp(_now())),
        )
