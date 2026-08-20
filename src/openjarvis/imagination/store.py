"""Durable Phase 9 candidate, prediction, simulation, and calibration ledger."""

# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .models import (
    CalibrationRecord,
    CandidatePlan,
    CriticResult,
    FailureMode,
    ImaginationDecision,
    ObservationRecord,
    Prediction,
    PredictionStatus,
    Score,
    SimulationRun,
    SimulationStatus,
)

MIGRATION_VERSION = 1


class ImaginationStore:
    """SQLite ledger with idempotent writes and explicit simulation scope."""

    def __init__(self, db_path: str | Path, *, auto_migrate: bool = True) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.path), isolation_level=None, check_same_thread=False
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        if auto_migrate:
            self.migrate()

    @property
    def migration_version(self) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM phase9_schema_migrations"
        ).fetchone()
        return int(row[0]) if row else 0

    def migrate(self) -> None:
        self._conn.executescript(
            """
            BEGIN;
            CREATE TABLE IF NOT EXISTS phase9_schema_migrations (
                version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase9_candidates (
                candidate_id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL,
                status TEXT NOT NULL, body_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase9_predictions (
                prediction_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL,
                status TEXT NOT NULL, body_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(candidate_id) REFERENCES phase9_candidates(candidate_id)
            );
            CREATE TABLE IF NOT EXISTS phase9_simulations (
                simulation_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL,
                status TEXT NOT NULL, body_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(candidate_id) REFERENCES phase9_candidates(candidate_id)
            );
            CREATE TABLE IF NOT EXISTS phase9_failure_modes (
                failure_mode_id TEXT PRIMARY KEY, body_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase9_critics (
                critic_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL,
                status TEXT NOT NULL, body_json TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(candidate_id) REFERENCES phase9_candidates(candidate_id)
            );
            CREATE TABLE IF NOT EXISTS phase9_scores (
                score_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL,
                status TEXT NOT NULL, body_json TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(candidate_id) REFERENCES phase9_candidates(candidate_id)
            );
            CREATE TABLE IF NOT EXISTS phase9_decisions (
                decision_id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL,
                status TEXT NOT NULL, body_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS phase9_observations (
                observation_id TEXT PRIMARY KEY, prediction_id TEXT NOT NULL,
                status TEXT NOT NULL, body_json TEXT NOT NULL, observed_at TEXT NOT NULL,
                FOREIGN KEY(prediction_id) REFERENCES phase9_predictions(prediction_id)
            );
            CREATE TABLE IF NOT EXISTS phase9_calibration (
                calibration_id TEXT PRIMARY KEY, prediction_id TEXT NOT NULL,
                status TEXT NOT NULL, body_json TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(prediction_id) REFERENCES phase9_predictions(prediction_id)
            );
            CREATE TABLE IF NOT EXISTS phase9_audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL,
                object_id TEXT NOT NULL, details_json TEXT NOT NULL,
                event_at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS phase9_audit_immutable_update
            BEFORE UPDATE ON phase9_audit BEGIN
                SELECT RAISE(ABORT, 'Phase 9 audit is immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS phase9_audit_immutable_delete
            BEFORE DELETE ON phase9_audit BEGIN
                SELECT RAISE(ABORT, 'Phase 9 audit is immutable');
            END;
            INSERT OR IGNORE INTO phase9_schema_migrations(version, applied_at)
                VALUES (1, CURRENT_TIMESTAMP);
            COMMIT;
            """
        )

    def backup(self, destination: str | Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(destination))
        try:
            self._conn.backup(target)
        finally:
            target.close()
        return destination

    def rollback(self) -> None:
        """Remove only Phase 9 tables, leaving unrelated application tables intact."""
        with self._conn:
            for table in (
                "phase9_calibration",
                "phase9_observations",
                "phase9_decisions",
                "phase9_scores",
                "phase9_critics",
                "phase9_simulations",
                "phase9_predictions",
                "phase9_failure_modes",
                "phase9_candidates",
                "phase9_audit",
                "phase9_schema_migrations",
            ):
                self._conn.execute(f"DROP TABLE IF EXISTS {table}")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ImaginationStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def save_candidate(self, candidate: CandidatePlan) -> bool:
        return self._save_versioned(
            "phase9_candidates",
            "candidate_id",
            candidate.candidate_id,
            candidate.to_dict(),
            candidate.status.value,
            candidate.created_at,
            candidate.updated_at,
            candidate.scenario_id,
        )

    def get_candidate(self, candidate_id: str) -> CandidatePlan:
        row = self._conn.execute(
            "SELECT body_json FROM phase9_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        return CandidatePlan.from_dict(json.loads(row[0]))

    def list_candidates(self, scenario_id: str | None = None) -> list[CandidatePlan]:
        if scenario_id is None:
            rows = self._conn.execute(
                "SELECT body_json FROM phase9_candidates ORDER BY created_at, candidate_id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT body_json FROM phase9_candidates WHERE scenario_id = ? "
                "ORDER BY created_at, candidate_id",
                (scenario_id,),
            ).fetchall()
        return [CandidatePlan.from_dict(json.loads(row[0])) for row in rows]

    def save_prediction(self, prediction: Prediction) -> bool:
        self._require_candidate(prediction.candidate_id)
        return self._save_simple(
            "phase9_predictions",
            "prediction_id",
            prediction.prediction_id,
            prediction.to_dict(),
            prediction.candidate_id,
            prediction.status.value,
            prediction.created_at,
        )

    def get_prediction(self, prediction_id: str) -> Prediction:
        row = self._conn.execute(
            "SELECT body_json FROM phase9_predictions WHERE prediction_id = ?",
            (prediction_id,),
        ).fetchone()
        if row is None:
            raise KeyError(prediction_id)
        return Prediction.from_dict(json.loads(row[0]))

    def list_predictions(self, candidate_id: str | None = None) -> list[Prediction]:
        query = "SELECT body_json FROM phase9_predictions"
        args: tuple[Any, ...] = ()
        if candidate_id is not None:
            query += " WHERE candidate_id = ?"
            args = (candidate_id,)
        query += " ORDER BY created_at, prediction_id"
        return [
            Prediction.from_dict(json.loads(row[0]))
            for row in self._conn.execute(query, args)
        ]

    def update_prediction_status(
        self,
        prediction_id: str,
        status: PredictionStatus,
        *,
        evidence_ids: Iterable[str] = (),
    ) -> Prediction:
        current = self.get_prediction(prediction_id)
        updated = Prediction(
            prediction_id=current.prediction_id,
            candidate_id=current.candidate_id,
            observation_id=current.observation_id,
            claim=current.claim,
            expected_value=current.expected_value,
            confidence=current.confidence,
            provenance=current.provenance,
            assumptions=current.assumptions,
            causal_parents=current.causal_parents,
            status=status,
            evidence_ids=tuple(evidence_ids) or current.evidence_ids,
            simulation_run_id=current.simulation_run_id,
            created_at=current.created_at,
        )
        self.save_prediction(updated)
        return updated

    def save_simulation(self, simulation: SimulationRun) -> bool:
        self._require_candidate(simulation.candidate_id)
        return self._save_simple(
            "phase9_simulations",
            "simulation_id",
            simulation.simulation_id,
            simulation.to_dict(),
            simulation.candidate_id,
            simulation.status.value,
            simulation.created_at,
        )

    def list_simulations(self, candidate_id: str | None = None) -> list[SimulationRun]:
        query = "SELECT body_json FROM phase9_simulations"
        args: tuple[Any, ...] = ()
        if candidate_id is not None:
            query += " WHERE candidate_id = ?"
            args = (candidate_id,)
        query += " ORDER BY created_at, simulation_id"
        return [
            SimulationRun.from_dict(json.loads(row[0]))
            for row in self._conn.execute(query, args)
        ]

    def save_failure_mode(self, failure_mode: FailureMode) -> bool:
        return self._save_body(
            "phase9_failure_modes",
            "failure_mode_id",
            failure_mode.failure_mode_id,
            failure_mode.to_dict(),
        )

    def list_failure_modes(self) -> list[FailureMode]:
        rows = self._conn.execute(
            "SELECT body_json FROM phase9_failure_modes ORDER BY failure_mode_id"
        ).fetchall()
        return [FailureMode.from_dict(json.loads(row[0])) for row in rows]

    def save_critic(self, critic: CriticResult) -> bool:
        self._require_candidate(critic.candidate_id)
        return self._save_simple(
            "phase9_critics",
            "critic_id",
            critic.critic_id,
            critic.to_dict(),
            critic.candidate_id,
            critic.kind.value,
            critic.created_at,
        )

    def list_critics(self, candidate_id: str | None = None) -> list[CriticResult]:
        query = "SELECT body_json FROM phase9_critics"
        args: tuple[Any, ...] = ()
        if candidate_id is not None:
            query += " WHERE candidate_id = ?"
            args = (candidate_id,)
        query += " ORDER BY created_at, critic_id"
        return [
            CriticResult.from_dict(json.loads(row[0]))
            for row in self._conn.execute(query, args)
        ]

    def save_score(self, score: Score) -> bool:
        self._require_candidate(score.candidate_id)
        return self._save_simple(
            "phase9_scores",
            "score_id",
            score.score_id,
            score.to_dict(),
            score.candidate_id,
            str(score.weighted_total),
            score.created_at,
        )

    def list_scores(self, candidate_id: str | None = None) -> list[Score]:
        query = "SELECT body_json FROM phase9_scores"
        args: tuple[Any, ...] = ()
        if candidate_id is not None:
            query += " WHERE candidate_id = ?"
            args = (candidate_id,)
        query += " ORDER BY created_at, score_id"
        return [
            Score.from_dict(json.loads(row[0]))
            for row in self._conn.execute(query, args)
        ]

    def save_decision(self, decision: ImaginationDecision) -> bool:
        return self._save_simple(
            "phase9_decisions",
            "decision_id",
            decision.decision_id,
            decision.to_dict(),
            decision.scenario_id,
            "decision",
            decision.created_at,
            foreign_column="scenario_id",
        )

    def get_decision(self, decision_id: str) -> ImaginationDecision:
        row = self._conn.execute(
            "SELECT body_json FROM phase9_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        if row is None:
            raise KeyError(decision_id)
        return ImaginationDecision.from_dict(json.loads(row[0]))

    def save_observation(self, observation: ObservationRecord) -> bool:
        self._require_prediction(observation.prediction_id)
        return self._save_simple(
            "phase9_observations",
            "observation_id",
            observation.observation_id,
            observation.to_dict(),
            observation.prediction_id,
            observation.status.value,
            observation.observed_at,
            foreign_column="prediction_id",
            time_column="observed_at",
        )

    def save_calibration(self, calibration: CalibrationRecord) -> bool:
        self._require_prediction(calibration.prediction_id)
        return self._save_simple(
            "phase9_calibration",
            "calibration_id",
            calibration.calibration_id,
            calibration.to_dict(),
            calibration.prediction_id,
            calibration.result.value,
            calibration.created_at,
            foreign_column="prediction_id",
        )

    def get_calibration(self, calibration_id: str) -> CalibrationRecord:
        row = self._conn.execute(
            "SELECT body_json FROM phase9_calibration WHERE calibration_id = ?",
            (calibration_id,),
        ).fetchone()
        if row is None:
            raise KeyError(calibration_id)
        return CalibrationRecord.from_dict(json.loads(row[0]))

    def list_calibrations(
        self, prediction_id: str | None = None
    ) -> list[CalibrationRecord]:
        query = "SELECT body_json FROM phase9_calibration"
        args: tuple[Any, ...] = ()
        if prediction_id is not None:
            query += " WHERE prediction_id = ?"
            args = (prediction_id,)
        query += " ORDER BY created_at, calibration_id"
        return [
            CalibrationRecord.from_dict(json.loads(row[0]))
            for row in self._conn.execute(query, args)
        ]

    def audit(self, object_id: str | None = None) -> list[dict[str, Any]]:
        if object_id is None:
            rows = self._conn.execute(
                "SELECT sequence, event, object_id, details_json, event_at "
                "FROM phase9_audit ORDER BY sequence"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT sequence, event, object_id, details_json, event_at "
                "FROM phase9_audit WHERE object_id = ? ORDER BY sequence",
                (object_id,),
            ).fetchall()
        return [
            {
                "sequence": row[0],
                "event": row[1],
                "object_id": row[2],
                "details": json.loads(row[3]),
                "event_at": row[4],
            }
            for row in rows
        ]

    def recover_interrupted(self) -> int:
        """Mark running simulations unknown, never as real-world failure."""
        rows = self._conn.execute(
            "SELECT simulation_id, body_json FROM phase9_simulations WHERE status = 'running'"
        ).fetchall()
        for simulation_id, body in rows:
            record = SimulationRun.from_dict(json.loads(body))
            recovered = SimulationRun(
                simulation_id=record.simulation_id,
                candidate_id=record.candidate_id,
                kind=record.kind,
                status=SimulationStatus.FAILED,
                simulation_outcome="unknown",
                evidence_scope=record.evidence_scope,
                findings=record.findings,
                prediction_ids=record.prediction_ids,
                assumptions=record.assumptions,
                failure_summary="restart interrupted simulation; real-world outcome unknown",
                workspace_path=record.workspace_path,
                created_at=record.created_at,
            )
            self._replace_simple(
                "phase9_simulations",
                "simulation_id",
                simulation_id,
                recovered.to_dict(),
                recovered.candidate_id,
                recovered.status.value,
                recovered.created_at,
            )
        return len(rows)

    def _require_candidate(self, candidate_id: str) -> None:
        if not self._conn.execute(
            "SELECT 1 FROM phase9_candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone():
            raise KeyError(candidate_id)

    def _require_prediction(self, prediction_id: str) -> None:
        if not self._conn.execute(
            "SELECT 1 FROM phase9_predictions WHERE prediction_id = ?", (prediction_id,)
        ).fetchone():
            raise KeyError(prediction_id)

    def _audit(self, event: str, object_id: str, details: dict[str, Any]) -> None:
        from .models import utc_now

        self._conn.execute(
            "INSERT INTO phase9_audit(event, object_id, details_json, event_at) VALUES (?, ?, ?, ?)",
            (event, object_id, json.dumps(details, sort_keys=True), utc_now()),
        )

    def _save_body(
        self, table: str, id_column: str, object_id: str, body: dict[str, Any]
    ) -> bool:
        existing = self._conn.execute(
            f"SELECT body_json FROM {table} WHERE {id_column} = ?", (object_id,)
        ).fetchone()
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
        if existing:
            if existing[0] != encoded:
                raise ValueError(f"duplicate {object_id} has different payload")
            return False
        self._conn.execute(
            f"INSERT INTO {table}({id_column}, body_json) VALUES (?, ?)",
            (object_id, encoded),
        )
        self._audit("created", object_id, {"table": table})
        return True

    def _save_simple(
        self,
        table: str,
        id_column: str,
        object_id: str,
        body: dict[str, Any],
        foreign_id: str,
        status: str,
        created_at: str,
        *,
        foreign_column: str = "candidate_id",
        time_column: str = "created_at",
    ) -> bool:
        existing = self._conn.execute(
            f"SELECT body_json FROM {table} WHERE {id_column} = ?", (object_id,)
        ).fetchone()
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
        if existing:
            if existing[0] != encoded:
                self._replace_simple(
                    table,
                    id_column,
                    object_id,
                    body,
                    foreign_id,
                    status,
                    created_at,
                    foreign_column=foreign_column,
                    time_column=time_column,
                )
                self._audit("updated", object_id, {"table": table, "status": status})
                return True
            return False
        self._conn.execute(
            f"INSERT INTO {table}({id_column}, {foreign_column}, status, body_json, {time_column}) "
            "VALUES (?, ?, ?, ?, ?)",
            (object_id, foreign_id, status, encoded, created_at),
        )
        self._audit("created", object_id, {"table": table, "status": status})
        return True

    def _replace_simple(
        self,
        table: str,
        id_column: str,
        object_id: str,
        body: dict[str, Any],
        foreign_id: str,
        status: str,
        created_at: str,
        *,
        foreign_column: str = "candidate_id",
        time_column: str = "created_at",
    ) -> None:
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
        self._conn.execute(
            f"UPDATE {table} SET {foreign_column} = ?, status = ?, body_json = ?, {time_column} = ? "
            f"WHERE {id_column} = ?",
            (foreign_id, status, encoded, created_at, object_id),
        )

    def _save_versioned(
        self,
        table: str,
        id_column: str,
        object_id: str,
        body: dict[str, Any],
        status: str,
        created_at: str,
        updated_at: str,
        scenario_id: str,
    ) -> bool:
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
        existing = self._conn.execute(
            f"SELECT body_json FROM {table} WHERE {id_column} = ?", (object_id,)
        ).fetchone()
        if existing:
            if existing[0] == encoded:
                return False
            self._conn.execute(
                f"UPDATE {table} SET scenario_id = ?, status = ?, body_json = ?, updated_at = ? "
                f"WHERE {id_column} = ?",
                (scenario_id, status, encoded, updated_at, object_id),
            )
            self._audit("updated", object_id, {"table": table, "status": status})
            return True
        self._conn.execute(
            f"INSERT INTO {table}({id_column}, scenario_id, status, body_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (object_id, scenario_id, status, encoded, created_at, updated_at),
        )
        self._audit("created", object_id, {"table": table, "status": status})
        return True


__all__ = ["ImaginationStore", "MIGRATION_VERSION"]
