"""Durable SQLite store for Phase 8 goals, council work, and receipts."""

# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Type, TypeVar

from .models import (
    AssignmentStatus,
    ExecutiveCommitment,
    ExecutiveContract,
    ExecutiveDecisionReceipt,
    ExecutiveGoal,
    GlobalWorkspace,
    GoalStatus,
    SpecialistAssignment,
    SpecialistClaim,
    SpecialistCouncil,
    VerifiedArtifact,
    contract_from_json,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_T = TypeVar("_T", bound=ExecutiveContract)


class ExecutiveStore:
    """Append-only evidence plus current projections for one executive.

    The store intentionally has its own ``phase8_`` namespace.  It can share
    the application's SQLite file with cognition, planning, Guardian, and
    Codex tables without owning or rewriting any of them.
    """

    MIGRATION_VERSION = 1

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            BEGIN;
            CREATE TABLE IF NOT EXISTS phase8_schema_migrations(
                version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase8_goals(
                id TEXT PRIMARY KEY, parent_goal_id TEXT, status TEXT NOT NULL,
                body_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase8_commitments(
                id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, status TEXT NOT NULL,
                body_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase8_workspaces(
                goal_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                body_json TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase8_claims(
                id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, specialist_role TEXT NOT NULL,
                status TEXT NOT NULL, confidence REAL NOT NULL, body_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase8_councils(
                id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, status TEXT NOT NULL,
                body_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase8_assignments(
                id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, council_id TEXT NOT NULL,
                specialist_role TEXT NOT NULL, status TEXT NOT NULL,
                mission_id TEXT, body_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase8_artifacts(
                id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, artifact_kind TEXT NOT NULL,
                verified INTEGER NOT NULL, body_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase8_decisions(
                id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, body_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase8_events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, goal_id TEXT NOT NULL,
                kind TEXT NOT NULL, entity_id TEXT, idempotency_key TEXT,
                body_json TEXT NOT NULL, event_at TEXT NOT NULL,
                UNIQUE(goal_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS phase8_goals_parent_idx
                ON phase8_goals(parent_goal_id, status);
            CREATE INDEX IF NOT EXISTS phase8_claims_goal_idx
                ON phase8_claims(goal_id, created_at);
            CREATE INDEX IF NOT EXISTS phase8_assignments_goal_idx
                ON phase8_assignments(goal_id, status);
            INSERT OR IGNORE INTO phase8_schema_migrations VALUES (1, CURRENT_TIMESTAMP);
            COMMIT;
            """
        )

    @property
    def migration_version(self) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM phase8_schema_migrations"
        ).fetchone()
        return int(row[0])

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _body(contract: ExecutiveContract) -> str:
        return contract.to_json()

    @staticmethod
    def _ensure_type(value: ExecutiveContract, expected: Type[_T]) -> _T:
        if not isinstance(value, expected):
            raise TypeError(f"expected {expected.__name__}, got {type(value).__name__}")
        return value

    def _put_versioned(
        self,
        table: str,
        contract: ExecutiveContract,
        *,
        extra: tuple[Any, ...] = (),
    ) -> None:
        body = self._body(contract)
        now = _now()
        if table == "phase8_goals":
            values = (
                contract.id,
                *extra,
                contract.status.value,
                body,
                contract.created_at,
                now,
            )
            insert = (
                "INSERT INTO phase8_goals(id,parent_goal_id,status,body_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)"
            )
            update = "parent_goal_id=excluded.parent_goal_id,body_json=excluded.body_json,status=excluded.status,updated_at=excluded.updated_at"
        elif table == "phase8_commitments":
            values = (
                contract.id,
                *extra,
                contract.status.value,
                body,
                contract.created_at,
                now,
            )
            insert = (
                "INSERT INTO phase8_commitments(id,goal_id,status,body_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)"
            )
            update = "goal_id=excluded.goal_id,body_json=excluded.body_json,status=excluded.status,updated_at=excluded.updated_at"
        elif table == "phase8_workspaces":
            values = (contract.goal_id, contract.revision, body, now)
            insert = (
                "INSERT INTO phase8_workspaces(goal_id,revision,body_json,updated_at) "
                "VALUES (?,?,?,?)"
            )
            update = "revision=excluded.revision,body_json=excluded.body_json,updated_at=excluded.updated_at"
        elif table == "phase8_councils":
            values = (
                contract.id,
                *extra,
                contract.status.value,
                body,
                contract.created_at,
                now,
            )
            insert = (
                "INSERT INTO phase8_councils(id,goal_id,status,body_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)"
            )
            update = "goal_id=excluded.goal_id,body_json=excluded.body_json,status=excluded.status,updated_at=excluded.updated_at"
        elif table == "phase8_assignments":
            values = (
                contract.id,
                contract.goal_id,
                contract.council_id,
                contract.specialist_role,
                contract.status.value,
                contract.mission_id,
                body,
                contract.created_at,
                now,
            )
            insert = (
                "INSERT INTO phase8_assignments("
                "id,goal_id,council_id,specialist_role,status,mission_id,body_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)"
            )
            update = (
                "goal_id=excluded.goal_id,council_id=excluded.council_id,"
                "specialist_role=excluded.specialist_role,status=excluded.status,"
                "mission_id=excluded.mission_id,"
                "body_json=excluded.body_json,updated_at=excluded.updated_at"
            )
        else:
            raise ValueError(f"unsupported versioned table: {table}")
        with self._conn:
            self._conn.execute(
                f"{insert} ON CONFLICT(id) DO UPDATE SET {update}"
                if table != "phase8_workspaces"
                else f"{insert} ON CONFLICT(goal_id) DO UPDATE SET {update}",
                values,
            )

    # -- goals and commitments -----------------------------------------

    def put_goal(self, goal: ExecutiveGoal) -> None:
        self._ensure_type(goal, ExecutiveGoal)
        self._put_versioned("phase8_goals", goal, extra=(goal.parent_goal_id,))

    def get_goal(self, goal_id: str) -> ExecutiveGoal:
        row = self._conn.execute(
            "SELECT body_json FROM phase8_goals WHERE id=?", (goal_id,)
        ).fetchone()
        if row is None:
            raise KeyError(goal_id)
        return self._ensure_type(contract_from_json(row[0]), ExecutiveGoal)

    def list_goals(
        self, *, parent_goal_id: Optional[str] = None
    ) -> list[ExecutiveGoal]:
        if parent_goal_id is None:
            rows = self._conn.execute(
                "SELECT body_json FROM phase8_goals WHERE parent_goal_id IS NULL ORDER BY created_at"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT body_json FROM phase8_goals WHERE parent_goal_id=? ORDER BY created_at",
                (parent_goal_id,),
            ).fetchall()
        return [
            self._ensure_type(contract_from_json(row[0]), ExecutiveGoal) for row in rows
        ]

    def put_commitment(self, commitment: ExecutiveCommitment) -> None:
        self._ensure_type(commitment, ExecutiveCommitment)
        self._put_versioned(
            "phase8_commitments", commitment, extra=(commitment.goal_id,)
        )

    def list_commitments(
        self, goal_id: Optional[str] = None
    ) -> list[ExecutiveCommitment]:
        query = "SELECT body_json FROM phase8_commitments"
        args: tuple[Any, ...] = ()
        if goal_id:
            query += " WHERE goal_id=?"
            args = (goal_id,)
        query += " ORDER BY created_at"
        return [
            self._ensure_type(contract_from_json(row[0]), ExecutiveCommitment)
            for row in self._conn.execute(query, args).fetchall()
        ]

    # -- workspace and council ----------------------------------------

    def put_workspace(self, workspace: GlobalWorkspace) -> None:
        self._ensure_type(workspace, GlobalWorkspace)
        self._put_versioned("phase8_workspaces", workspace)

    def get_workspace(self, goal_id: str) -> GlobalWorkspace:
        row = self._conn.execute(
            "SELECT body_json FROM phase8_workspaces WHERE goal_id=?", (goal_id,)
        ).fetchone()
        if row is None:
            raise KeyError(goal_id)
        return self._ensure_type(contract_from_json(row[0]), GlobalWorkspace)

    def put_council(self, council: SpecialistCouncil) -> None:
        self._ensure_type(council, SpecialistCouncil)
        self._put_versioned("phase8_councils", council, extra=(council.goal_id,))

    def get_council(self, council_id: str) -> SpecialistCouncil:
        row = self._conn.execute(
            "SELECT body_json FROM phase8_councils WHERE id=?", (council_id,)
        ).fetchone()
        if row is None:
            raise KeyError(council_id)
        return self._ensure_type(contract_from_json(row[0]), SpecialistCouncil)

    def list_councils(self, goal_id: Optional[str] = None) -> list[SpecialistCouncil]:
        query = "SELECT body_json FROM phase8_councils"
        args: tuple[Any, ...] = ()
        if goal_id:
            query += " WHERE goal_id=?"
            args = (goal_id,)
        query += " ORDER BY created_at"
        return [
            self._ensure_type(contract_from_json(row[0]), SpecialistCouncil)
            for row in self._conn.execute(query, args).fetchall()
        ]

    def put_assignment(self, assignment: SpecialistAssignment) -> None:
        self._ensure_type(assignment, SpecialistAssignment)
        self._put_versioned("phase8_assignments", assignment)

    def get_assignment(self, assignment_id: str) -> SpecialistAssignment:
        row = self._conn.execute(
            "SELECT body_json FROM phase8_assignments WHERE id=?", (assignment_id,)
        ).fetchone()
        if row is None:
            raise KeyError(assignment_id)
        return self._ensure_type(contract_from_json(row[0]), SpecialistAssignment)

    def list_assignments(
        self,
        goal_id: Optional[str] = None,
        *,
        statuses: Optional[Iterable[AssignmentStatus]] = None,
    ) -> list[SpecialistAssignment]:
        query = "SELECT body_json FROM phase8_assignments"
        clauses: list[str] = []
        args: list[Any] = []
        if goal_id:
            clauses.append("goal_id=?")
            args.append(goal_id)
        if statuses:
            values = [AssignmentStatus(item).value for item in statuses]
            clauses.append("status IN (" + ",".join("?" for _ in values) + ")")
            args.extend(values)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at"
        return [
            self._ensure_type(contract_from_json(row[0]), SpecialistAssignment)
            for row in self._conn.execute(query, args).fetchall()
        ]

    # -- claims and artifacts ------------------------------------------

    def put_claim(self, claim: SpecialistClaim) -> None:
        self._ensure_type(claim, SpecialistClaim)
        with self._conn:
            self._conn.execute(
                "INSERT INTO phase8_claims(id,goal_id,specialist_role,status,confidence,body_json,created_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET goal_id=excluded.goal_id,specialist_role=excluded.specialist_role,status=excluded.status,body_json=excluded.body_json,confidence=excluded.confidence",
                (
                    claim.id,
                    claim.goal_id,
                    claim.specialist_role,
                    claim.status.value,
                    claim.confidence,
                    claim.to_json(),
                    claim.created_at,
                ),
            )

    def get_claim(self, claim_id: str) -> SpecialistClaim:
        row = self._conn.execute(
            "SELECT body_json FROM phase8_claims WHERE id=?", (claim_id,)
        ).fetchone()
        if row is None:
            raise KeyError(claim_id)
        return self._ensure_type(contract_from_json(row[0]), SpecialistClaim)

    def list_claims(self, goal_id: Optional[str] = None) -> list[SpecialistClaim]:
        query = "SELECT body_json FROM phase8_claims"
        args: tuple[Any, ...] = ()
        if goal_id:
            query += " WHERE goal_id=?"
            args = (goal_id,)
        query += " ORDER BY created_at, id"
        return [
            self._ensure_type(contract_from_json(row[0]), SpecialistClaim)
            for row in self._conn.execute(query, args).fetchall()
        ]

    def put_artifact(self, artifact: VerifiedArtifact) -> None:
        self._ensure_type(artifact, VerifiedArtifact)
        with self._conn:
            self._conn.execute(
                "INSERT INTO phase8_artifacts(id,goal_id,artifact_kind,verified,body_json,created_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET goal_id=excluded.goal_id,artifact_kind=excluded.artifact_kind,verified=excluded.verified,body_json=excluded.body_json",
                (
                    artifact.id,
                    artifact.goal_id,
                    artifact.artifact_kind,
                    int(artifact.verified),
                    artifact.to_json(),
                    artifact.created_at,
                ),
            )

    def get_artifact(self, artifact_id: str) -> VerifiedArtifact:
        row = self._conn.execute(
            "SELECT body_json FROM phase8_artifacts WHERE id=?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return self._ensure_type(contract_from_json(row[0]), VerifiedArtifact)

    def list_artifacts(self, goal_id: Optional[str] = None) -> list[VerifiedArtifact]:
        query = "SELECT body_json FROM phase8_artifacts"
        args: tuple[Any, ...] = ()
        if goal_id:
            query += " WHERE goal_id=?"
            args = (goal_id,)
        query += " ORDER BY created_at"
        return [
            self._ensure_type(contract_from_json(row[0]), VerifiedArtifact)
            for row in self._conn.execute(query, args).fetchall()
        ]

    # -- decisions and evidence ---------------------------------------

    def put_decision(self, receipt: ExecutiveDecisionReceipt) -> None:
        self._ensure_type(receipt, ExecutiveDecisionReceipt)
        with self._conn:
            self._conn.execute(
                "INSERT INTO phase8_decisions(id,goal_id,body_json,created_at) VALUES (?,?,?,?)",
                (receipt.id, receipt.goal_id, receipt.to_json(), receipt.created_at),
            )

    def list_decisions(
        self, goal_id: Optional[str] = None
    ) -> list[ExecutiveDecisionReceipt]:
        query = "SELECT body_json FROM phase8_decisions"
        args: tuple[Any, ...] = ()
        if goal_id:
            query += " WHERE goal_id=?"
            args = (goal_id,)
        query += " ORDER BY created_at"
        return [
            self._ensure_type(contract_from_json(row[0]), ExecutiveDecisionReceipt)
            for row in self._conn.execute(query, args).fetchall()
        ]

    def append_event(
        self,
        goal_id: str,
        kind: str,
        *,
        entity_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> bool:
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO phase8_events(goal_id,kind,entity_id,idempotency_key,body_json,event_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        goal_id,
                        kind,
                        entity_id,
                        idempotency_key,
                        json.dumps(details or {}, sort_keys=True),
                        _now(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            if idempotency_key:
                return False
            raise

    def events(self, goal_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT sequence,kind,entity_id,body_json,event_at FROM phase8_events "
            "WHERE goal_id=? ORDER BY sequence",
            (goal_id,),
        ).fetchall()
        return [
            {
                "sequence": row[0],
                "kind": row[1],
                "entity_id": row[2],
                "details": json.loads(row[3]),
                "event_at": row[4],
            }
            for row in rows
        ]

    def recover(self, goal_id: str) -> dict[str, Any]:
        """Return durable state needed to resume without transcript replay."""
        goal = self.get_goal(goal_id)
        assignments = self.list_assignments(goal_id)
        return {
            "goal": goal,
            "workspace": self.get_workspace(goal_id),
            "completed_subgoals": [
                item.id
                for item in self.list_goals(parent_goal_id=goal_id)
                if item.status is GoalStatus.COMPLETED
            ],
            "pending_assignments": [
                item
                for item in assignments
                if item.status
                not in {
                    AssignmentStatus.COMPLETED,
                    AssignmentStatus.CANCELED,
                    AssignmentStatus.FAILED,
                    AssignmentStatus.BLOCKED,
                }
            ],
            "completed_assignments": [
                item.id
                for item in assignments
                if item.status is AssignmentStatus.COMPLETED
            ],
            "claims": self.list_claims(goal_id),
            "artifacts": self.list_artifacts(goal_id),
            "decisions": self.list_decisions(goal_id),
        }


__all__ = ["ExecutiveStore"]
