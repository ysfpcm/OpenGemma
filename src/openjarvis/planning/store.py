"""Durable Phase 6 plan, grant, decision, and cancellation state."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from openjarvis.cognition import Authorization

from .models import (
    AuthorizationDecision,
    AutonomyLevel,
    ContextualGrant,
    PlanStatus,
    StructuredPlan,
    _timestamp,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class PlanningStore:
    """Append-oriented SQLite store with idempotent Phase 6 projections."""

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
            CREATE TABLE IF NOT EXISTS phase6_schema_migrations (
                version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase6_plan_versions (
                plan_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                body_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(plan_id, version)
            );
            CREATE TABLE IF NOT EXISTS phase6_plan_state (
                plan_id TEXT PRIMARY KEY,
                latest_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase6_plan_edits (
                edit_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                from_version INTEGER NOT NULL,
                to_version INTEGER NOT NULL,
                body_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase6_plan_tombstones (
                plan_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                removed_in_version INTEGER NOT NULL,
                PRIMARY KEY(plan_id, step_id)
            );
            CREATE TABLE IF NOT EXISTS phase6_scheduled_effects (
                effect_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                plan_version INTEGER NOT NULL,
                step_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                canceled_at TEXT
            );
            CREATE TABLE IF NOT EXISTS phase6_grants (
                grant_id TEXT PRIMARY KEY,
                body_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS phase6_grant_usage (
                grant_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                used_at TEXT NOT NULL,
                resource_cost REAL NOT NULL,
                PRIMARY KEY(grant_id, action_id),
                FOREIGN KEY(grant_id) REFERENCES phase6_grants(grant_id)
            );
            CREATE TABLE IF NOT EXISTS phase6_authorizations (
                plan_id TEXT NOT NULL,
                plan_version INTEGER NOT NULL,
                step_id TEXT NOT NULL,
                body_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(plan_id, plan_version, step_id)
            );
            CREATE TABLE IF NOT EXISTS phase6_policies (
                action_type TEXT PRIMARY KEY,
                level INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase6_audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                plan_id TEXT,
                details_json TEXT NOT NULL,
                event_at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS phase6_plan_edits_immutable_update
            BEFORE UPDATE ON phase6_plan_edits BEGIN
                SELECT RAISE(ABORT, 'phase6 plan edits are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS phase6_plan_edits_immutable_delete
            BEFORE DELETE ON phase6_plan_edits BEGIN
                SELECT RAISE(ABORT, 'phase6 plan edits are immutable');
            END;
            INSERT OR IGNORE INTO phase6_schema_migrations(version, applied_at)
                VALUES (1, CURRENT_TIMESTAMP);
            COMMIT;
            """
        )

    def close(self) -> None:
        self._conn.close()

    def _audit(self, event: str, plan_id: str | None, details: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO phase6_audit(event, plan_id, details_json, event_at) "
            "VALUES (?, ?, ?, ?)",
            (event, plan_id, _json(details), _now()),
        )

    def create_plan(self, plan: StructuredPlan) -> bool:
        body = plan.to_dict()
        existing = self._conn.execute(
            "SELECT body_json FROM phase6_plan_versions WHERE plan_id=? AND version=?",
            (plan.plan_id, plan.version),
        ).fetchone()
        if existing:
            if json.loads(existing[0]) == body:
                return False
            raise ValueError("plan version already exists with different content")
        if plan.version > 1:
            prior = self.get_plan(plan.plan_id, plan.version - 1)
            prior_ids = {step.step_id for step in prior.steps}
            current_ids = {step.step_id for step in plan.steps}
            tombstones = self.tombstoned_steps(plan.plan_id)
            if tombstones & current_ids:
                raise ValueError(
                    "removed action cannot reappear in a later plan version"
                )
            if not current_ids <= prior_ids | {
                step.step_id for step in plan.steps if step.step_id not in prior_ids
            }:
                raise ValueError("plan version contains an invalid step set")
        with self._conn:
            self._conn.execute(
                "INSERT INTO phase6_plan_versions("
                "plan_id, version, body_json, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    plan.plan_id,
                    plan.version,
                    _json(body),
                    plan.status.value,
                    plan.created_at,
                ),
            )
            latest = self._conn.execute(
                "SELECT latest_version FROM phase6_plan_state WHERE plan_id=?",
                (plan.plan_id,),
            ).fetchone()
            if latest and plan.version > latest[0]:
                self._conn.execute(
                    "UPDATE phase6_plan_versions SET status=? "
                    "WHERE plan_id=? AND version=?",
                    (PlanStatus.SUPERSEDED.value, plan.plan_id, latest[0]),
                )
                self._conn.execute(
                    "UPDATE phase6_plan_state SET latest_version=?, status=?, "
                    "reason='', updated_at=? "
                    "WHERE plan_id=?",
                    (plan.version, PlanStatus.ACTIVE.value, _now(), plan.plan_id),
                )
            elif not latest:
                self._conn.execute(
                    "INSERT INTO phase6_plan_state VALUES (?, ?, ?, '', ?)",
                    (plan.plan_id, plan.version, plan.status.value, _now()),
                )
            self._audit("plan-created", plan.plan_id, {"version": plan.version})
        return True

    def get_plan(self, plan_id: str, version: int | None = None) -> StructuredPlan:
        if version is None:
            row = self._conn.execute(
                "SELECT latest_version FROM phase6_plan_state WHERE plan_id=?",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise KeyError(plan_id)
            version = int(row[0])
        row = self._conn.execute(
            "SELECT body_json, status FROM phase6_plan_versions "
            "WHERE plan_id=? AND version=?",
            (plan_id, version),
        ).fetchone()
        if row is None:
            raise KeyError(f"{plan_id}:v{version}")
        body = json.loads(row[0])
        body["status"] = row[1]
        return StructuredPlan.from_dict(body)

    def list_versions(self, plan_id: str) -> list[StructuredPlan]:
        rows = self._conn.execute(
            "SELECT version FROM phase6_plan_versions WHERE plan_id=? ORDER BY version",
            (plan_id,),
        ).fetchall()
        return [self.get_plan(plan_id, int(row[0])) for row in rows]

    def tombstoned_steps(self, plan_id: str) -> set[str]:
        return {
            row[0]
            for row in self._conn.execute(
                "SELECT step_id FROM phase6_plan_tombstones WHERE plan_id=?", (plan_id,)
            ).fetchall()
        }

    def edit_remove_steps(
        self,
        plan_id: str,
        remove_step_ids: Iterable[str],
        *,
        edit_id: str,
        reason: str,
        edited_by_marc: bool = True,
    ) -> StructuredPlan:
        requested = tuple(dict.fromkeys(str(item) for item in remove_step_ids))
        existing_edit = self._conn.execute(
            "SELECT plan_id, to_version, body_json "
            "FROM phase6_plan_edits WHERE edit_id=?",
            (edit_id,),
        ).fetchone()
        if existing_edit:
            stored_plan_id, to_version, body_json = existing_edit
            if stored_plan_id != plan_id:
                raise ValueError("edit ID already belongs to another plan")
            stored = json.loads(body_json)
            if stored != {
                "remove_step_ids": list(requested),
                "reason": reason,
                "edited_by_marc": edited_by_marc,
            }:
                raise ValueError("edit ID already exists with different content")
            return self.get_plan(plan_id, int(to_version))
        current = self.get_plan(plan_id)
        if current.status is not PlanStatus.ACTIVE:
            raise ValueError("only an active plan can be edited")
        current_ids = {step.step_id for step in current.steps}
        missing = set(requested) - current_ids
        if missing:
            raise ValueError(f"cannot remove missing plan steps: {sorted(missing)}")
        next_version = current.version + 1
        removed = tuple(dict.fromkeys((*current.removed_step_ids, *requested)))
        next_plan = StructuredPlan(
            plan_id=current.plan_id,
            version=next_version,
            goal_id=current.goal_id,
            situation_id=current.situation_id,
            situation_type=current.situation_type,
            created_at=_now(),
            valid_until=current.valid_until,
            rationale=current.rationale,
            context_snapshot=current.context_snapshot,
            evidence_ids=current.evidence_ids,
            steps=tuple(
                step for step in current.steps if step.step_id not in set(requested)
            ),
            resource_budget=current.resource_budget,
            status=PlanStatus.ACTIVE,
            removed_step_ids=removed,
            edited_by_marc=edited_by_marc,
            cancellation_reason="",
        )
        with self._conn:
            self.create_plan(next_plan)
            for step_id in requested:
                self._conn.execute(
                    "INSERT INTO phase6_plan_tombstones("
                    "plan_id, step_id, removed_in_version) VALUES (?, ?, ?)",
                    (plan_id, step_id, next_version),
                )
            self._conn.execute(
                "INSERT INTO phase6_plan_edits("
                "edit_id, plan_id, from_version, to_version, body_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    edit_id,
                    plan_id,
                    current.version,
                    next_version,
                    _json(
                        {
                            "remove_step_ids": list(requested),
                            "reason": reason,
                            "edited_by_marc": edited_by_marc,
                        }
                    ),
                    _now(),
                ),
            )
            self._audit(
                "plan-edited",
                plan_id,
                {
                    "from_version": current.version,
                    "to_version": next_version,
                    "removed": list(requested),
                },
            )
        return next_plan

    def edit_history(self, plan_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT edit_id, from_version, to_version, body_json, created_at "
            "FROM phase6_plan_edits WHERE plan_id=? ORDER BY to_version",
            (plan_id,),
        ).fetchall()
        return [
            {
                "edit_id": row[0],
                "from_version": row[1],
                "to_version": row[2],
                **json.loads(row[3]),
                "created_at": row[4],
            }
            for row in rows
        ]

    def set_plan_status(self, plan_id: str, status: PlanStatus, reason: str) -> None:
        current = self.get_plan(plan_id)
        with self._conn:
            self._conn.execute(
                "UPDATE phase6_plan_versions SET status=? "
                "WHERE plan_id=? AND version=?",
                (status.value, plan_id, current.version),
            )
            self._conn.execute(
                "UPDATE phase6_plan_state SET status=?, reason=?, updated_at=? "
                "WHERE plan_id=?",
                (status.value, reason, _now(), plan_id),
            )
            if status in {PlanStatus.CANCELED, PlanStatus.EXPIRED, PlanStatus.INVALID}:
                self._conn.execute(
                    "UPDATE phase6_scheduled_effects "
                    "SET status='canceled', canceled_at=? "
                    "WHERE plan_id=? AND status IN ('scheduled', 'pending')",
                    (_now(), plan_id),
                )
            self._audit(
                "plan-status-changed",
                plan_id,
                {"status": status.value, "reason": reason},
            )

    def schedule_effect(
        self, plan_id: str, plan_version: int, step_id: str, effect_id: str
    ) -> None:
        plan = self.get_plan(plan_id, plan_version)
        if plan.status is not PlanStatus.ACTIVE:
            raise ValueError("only an active plan can schedule an effect")
        plan.step(step_id)
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO phase6_scheduled_effects "
                "(effect_id, plan_id, plan_version, step_id, status, created_at) "
                "VALUES (?, ?, ?, ?, 'scheduled', ?)",
                (effect_id, plan_id, plan_version, step_id, _now()),
            )
            self._audit(
                "effect-scheduled",
                plan_id,
                {"effect_id": effect_id, "step_id": step_id},
            )

    def scheduled_effects(self, plan_id: str | None = None) -> list[dict[str, Any]]:
        query = (
            "SELECT effect_id, plan_id, plan_version, step_id, status, "
            "created_at, canceled_at "
            "FROM phase6_scheduled_effects"
        )
        args: tuple[Any, ...] = ()
        if plan_id:
            query += " WHERE plan_id=?"
            args = (plan_id,)
        query += " ORDER BY created_at, effect_id"
        rows = self._conn.execute(query, args).fetchall()
        return [
            {
                "effect_id": row[0],
                "plan_id": row[1],
                "plan_version": row[2],
                "step_id": row[3],
                "status": row[4],
                "created_at": row[5],
                "canceled_at": row[6],
            }
            for row in rows
        ]

    def put_grant(self, grant: ContextualGrant) -> bool:
        existing = self._conn.execute(
            "SELECT body_json FROM phase6_grants WHERE grant_id=?", (grant.grant_id,)
        ).fetchone()
        body = grant.to_dict()
        if existing:
            if json.loads(existing[0]) == body:
                return False
            raise ValueError("grant ID already exists with different content")
        with self._conn:
            self._conn.execute(
                "INSERT INTO phase6_grants("
                "grant_id, body_json, created_at, revoked_at) VALUES (?, ?, ?, ?)",
                (grant.grant_id, _json(body), grant.created_at, grant.revoked_at),
            )
            self._audit("grant-created", None, {"grant_id": grant.grant_id})
        return True

    def get_grant(self, grant_id: str) -> ContextualGrant:
        row = self._conn.execute(
            "SELECT body_json, revoked_at FROM phase6_grants WHERE grant_id=?",
            (grant_id,),
        ).fetchone()
        if row is None:
            raise KeyError(grant_id)
        body = json.loads(row[0])
        body["revoked_at"] = row[1] or body.get("revoked_at")
        return ContextualGrant.from_dict(body)

    def list_grants(self) -> list[ContextualGrant]:
        rows = self._conn.execute(
            "SELECT grant_id FROM phase6_grants ORDER BY created_at, grant_id"
        ).fetchall()
        return [self.get_grant(row[0]) for row in rows]

    def revoke_grant(self, grant_id: str, reason: str = "Marc revoked grant") -> None:
        self.get_grant(grant_id)
        with self._conn:
            self._conn.execute(
                "UPDATE phase6_grants SET revoked_at=? "
                "WHERE grant_id=? AND revoked_at IS NULL",
                (_now(), grant_id),
            )
            self._audit("grant-revoked", None, {"grant_id": grant_id, "reason": reason})

    def reserve_grant_usage(
        self, grant: ContextualGrant, action_id: str, now: datetime
    ) -> tuple[bool, str]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self._conn.execute(
                "SELECT 1 FROM phase6_grant_usage WHERE grant_id=? AND action_id=?",
                (grant.grant_id, action_id),
            ).fetchone()
            if existing:
                self._conn.execute("COMMIT")
                return True, "already-reserved"
            now_iso = _timestamp(now)
            window_start = _timestamp(
                now - timedelta(seconds=grant.frequency_window_seconds)
            )
            row = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(resource_cost), 0) "
                "FROM phase6_grant_usage "
                "WHERE grant_id=? AND used_at>=?",
                (grant.grant_id, window_start),
            ).fetchone()
            if int(row[0]) >= grant.frequency_limit:
                self._conn.execute("COMMIT")
                return False, "grant frequency budget exhausted"
            if float(row[1]) + grant.resource_cost > grant.resource_budget:
                self._conn.execute("COMMIT")
                return False, "grant resource budget exhausted"
            self._conn.execute(
                "INSERT INTO phase6_grant_usage("
                "grant_id, action_id, used_at, resource_cost) VALUES (?, ?, ?, ?)",
                (grant.grant_id, action_id, now_iso, grant.resource_cost),
            )
            self._audit(
                "grant-usage-reserved",
                None,
                {"grant_id": grant.grant_id, "action_id": action_id},
            )
            self._conn.execute("COMMIT")
            return True, "reserved"
        except Exception:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    def release_grant_usage(self, grant: ContextualGrant, action_id: str) -> bool:
        """Release a reservation when Guardian denied before any execution."""

        with self._conn:
            deleted = self._conn.execute(
                "DELETE FROM phase6_grant_usage WHERE grant_id=? AND action_id=?",
                (grant.grant_id, action_id),
            ).rowcount
            if deleted:
                self._audit(
                    "grant-usage-released",
                    None,
                    {"grant_id": grant.grant_id, "action_id": action_id},
                )
        return bool(deleted)

    def set_autonomy_level(self, action_type: str, level: AutonomyLevel) -> None:
        if not action_type:
            raise ValueError("action type is required")
        with self._conn:
            self._conn.execute(
                "INSERT INTO phase6_policies(action_type, level, created_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(action_type) DO UPDATE SET level=excluded.level",
                (action_type, level.value, _now()),
            )
            self._audit(
                "autonomy-level-set",
                None,
                {"action_type": action_type, "level": level.value},
            )

    def autonomy_level(self, action_type: str) -> AutonomyLevel:
        row = self._conn.execute(
            "SELECT level FROM phase6_policies WHERE action_type=?", (action_type,)
        ).fetchone()
        return AutonomyLevel(int(row[0])) if row else AutonomyLevel.SHADOW

    def save_decision(self, decision: AuthorizationDecision) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO phase6_authorizations "
                "(plan_id, plan_version, step_id, body_json, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(plan_id, plan_version, step_id) DO UPDATE SET "
                "body_json=excluded.body_json, created_at=excluded.created_at",
                (
                    decision.plan_id,
                    decision.plan_version,
                    decision.step_id,
                    _json(decision.to_dict()),
                    _now(),
                ),
            )
            self._audit(
                "authorization-decision",
                decision.plan_id,
                {
                    "version": decision.plan_version,
                    "step_id": decision.step_id,
                    "allowed": decision.allowed,
                },
            )

    def get_decision(
        self, plan_id: str, plan_version: int, step_id: str
    ) -> AuthorizationDecision | None:
        row = self._conn.execute(
            "SELECT body_json FROM phase6_authorizations "
            "WHERE plan_id=? AND plan_version=? AND step_id=?",
            (plan_id, plan_version, step_id),
        ).fetchone()
        if row is None:
            return None
        body = json.loads(row[0])
        authorization = body.get("authorization")
        return AuthorizationDecision(
            allowed=bool(body["allowed"]),
            plan_id=str(body["plan_id"]),
            plan_version=int(body["plan_version"]),
            step_id=str(body["step_id"]),
            reason=str(body["reason"]),
            authorization=(
                Authorization.from_dict(authorization) if authorization else None
            ),
            grant_id=body.get("grant_id"),
            autonomy_level=AutonomyLevel(int(body.get("autonomy_level", 0))),
            evidence=tuple(str(item) for item in body.get("evidence", [])),
        )

    def list_decisions(self, plan_id: str) -> list[AuthorizationDecision]:
        rows = self._conn.execute(
            "SELECT plan_version, step_id FROM phase6_authorizations "
            "WHERE plan_id=? ORDER BY plan_version, step_id",
            (plan_id,),
        ).fetchall()
        decisions = []
        for version, step_id in rows:
            decision = self.get_decision(plan_id, int(version), step_id)
            if decision is not None:
                decisions.append(decision)
        return decisions

    def audit(self, plan_id: str | None = None) -> list[dict[str, Any]]:
        if plan_id is None:
            rows = self._conn.execute(
                "SELECT event, plan_id, details_json, event_at "
                "FROM phase6_audit ORDER BY sequence"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT event, plan_id, details_json, event_at FROM phase6_audit "
                "WHERE plan_id=? ORDER BY sequence",
                (plan_id,),
            ).fetchall()
        return [
            {
                "event": row[0],
                "plan_id": row[1],
                "details": json.loads(row[2]),
                "at": row[3],
            }
            for row in rows
        ]


__all__ = ["PlanningStore"]
