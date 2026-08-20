# ruff: noqa: E501
"""Durable Phase 12 state with a reversible, additive SQLite migration."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .contracts import (
    CommunicationState,
    ControllerDecision,
    EmbodimentIntent,
    InstallationGrant,
    PackInstallation,
    PackInstallState,
    PolicyEvaluation,
    PolicyPackManifest,
    SafetyStop,
    SimulatedActuatorCommand,
    SimulatedVerification,
    utc_now,
)

MIGRATION_VERSION = 1


class Phase12Store:
    """One local ledger for pack lifecycle and simulator evidence.

    Pack metadata is retained after uninstall so provenance and rollback remain
    reviewable.  Evidence tables have immutable triggers; rollback only removes
    this phase's tables and leaves unrelated application tables untouched.
    """

    def __init__(self, db_path: str | Path, *, auto_migrate: bool = True) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.path), isolation_level=None, check_same_thread=False
        )
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS phase12_schema_migrations "
            "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        if auto_migrate:
            self.migrate()

    @property
    def migration_version(self) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM phase12_schema_migrations"
        ).fetchone()
        return int(row[0])

    def migrate(self) -> None:
        if self.migration_version >= MIGRATION_VERSION:
            return
        migration = Path(__file__).with_name("migrations") / "0001_phase12.sql"
        with self._conn:
            self._conn.executescript(migration.read_text(encoding="utf-8"))
            self._conn.execute(
                "INSERT INTO phase12_schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (MIGRATION_VERSION, "phase12", utc_now()),
            )

    def rollback(self) -> None:
        if self.migration_version != MIGRATION_VERSION:
            return
        migration = Path(__file__).with_name("migrations") / "0001_phase12.down.sql"
        with self._conn:
            self._conn.executescript(migration.read_text(encoding="utf-8"))
            self._conn.execute(
                "DELETE FROM phase12_schema_migrations WHERE version = ?",
                (MIGRATION_VERSION,),
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

    @staticmethod
    def restore_backup(backup: str | Path, destination: str | Path) -> None:
        shutil.copy2(Path(backup), Path(destination))

    # ---- pack lifecycle -------------------------------------------------

    def save_installation(self, installation: PackInstallation) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO phase12_installed_packs("
                "installation_id, pack_id, version, state, manifest_json, grant_json, "
                "installed_at, updated_at, previous_version, migration_state, rollback_state, production_active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(installation_id) DO UPDATE SET pack_id=excluded.pack_id, "
                "version=excluded.version, state=excluded.state, manifest_json=excluded.manifest_json, "
                "grant_json=excluded.grant_json, updated_at=excluded.updated_at, "
                "previous_version=excluded.previous_version, migration_state=excluded.migration_state, "
                "rollback_state=excluded.rollback_state, production_active=excluded.production_active",
                (
                    installation.installation_id,
                    installation.manifest.pack_id,
                    installation.manifest.version,
                    installation.state.value,
                    installation.manifest.to_json(),
                    installation.authority_scope.to_json(),
                    installation.installed_at,
                    installation.updated_at,
                    installation.previous_version,
                    installation.migration_state,
                    installation.rollback_state,
                    int(installation.production_active),
                ),
            )

    def save_pack_version(self, installation: PackInstallation) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO phase12_pack_versions("
                "installation_id, pack_id, version, manifest_json, grant_json, captured_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    installation.installation_id,
                    installation.manifest.pack_id,
                    installation.manifest.version,
                    installation.manifest.to_json(),
                    installation.authority_scope.to_json(),
                    utc_now(),
                ),
            )

    def get_installation(self, pack_id: str) -> Optional[PackInstallation]:
        row = self._conn.execute(
            "SELECT installation_id, manifest_json, state, grant_json, installed_at, updated_at, "
            "previous_version, migration_state, rollback_state, production_active "
            "FROM phase12_installed_packs WHERE pack_id=? "
            "ORDER BY updated_at DESC LIMIT 1",
            (pack_id,),
        ).fetchone()
        return self._installation_from_row(row) if row else None

    def get_installation_by_id(
        self, installation_id: str
    ) -> Optional[PackInstallation]:
        row = self._conn.execute(
            "SELECT installation_id, manifest_json, state, grant_json, installed_at, updated_at, "
            "previous_version, migration_state, rollback_state, production_active "
            "FROM phase12_installed_packs WHERE installation_id=?",
            (installation_id,),
        ).fetchone()
        return self._installation_from_row(row) if row else None

    def list_installations(self) -> list[PackInstallation]:
        rows = self._conn.execute(
            "SELECT installation_id, manifest_json, state, grant_json, installed_at, updated_at, "
            "previous_version, migration_state, rollback_state, production_active "
            "FROM phase12_installed_packs ORDER BY updated_at DESC"
        ).fetchall()
        return [self._installation_from_row(row) for row in rows]

    def record_pack_event(
        self,
        installation_id: str,
        operation: str,
        from_state: Optional[str],
        to_state: str,
        details: dict[str, Any],
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO phase12_pack_events(installation_id, operation, from_state, to_state, details_json, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    installation_id,
                    operation,
                    from_state,
                    to_state,
                    json.dumps(details, sort_keys=True),
                    utc_now(),
                ),
            )

    def pack_events(self, pack_id: Optional[str] = None) -> list[dict[str, Any]]:
        query = (
            "SELECT e.sequence, e.installation_id, p.pack_id, e.operation, e.from_state, e.to_state, "
            "e.details_json, e.recorded_at FROM phase12_pack_events e "
            "LEFT JOIN phase12_installed_packs p ON p.installation_id=e.installation_id"
        )
        params: tuple[Any, ...] = ()
        if pack_id:
            query += " WHERE p.pack_id=?"
            params = (pack_id,)
        query += " ORDER BY e.sequence"
        rows = self._conn.execute(query, params).fetchall()
        return [
            {
                "sequence": row[0],
                "installation_id": row[1],
                "pack_id": row[2],
                "operation": row[3],
                "from_state": row[4],
                "to_state": row[5],
                "details": json.loads(row[6]),
                "recorded_at": row[7],
            }
            for row in rows
        ]

    def save_replay(
        self, pack_id: str, event: Any, evaluation: PolicyEvaluation
    ) -> bool:
        dedupe_key = event.dedupe_key
        with self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO phase12_replays(replay_id, pack_id, dedupe_key, event_json, evaluation_json, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    evaluation.evaluation_id,
                    pack_id,
                    dedupe_key,
                    event.to_json(),
                    evaluation.to_json(),
                    utc_now(),
                ),
            )
        return cursor.rowcount == 1

    def get_replay(self, pack_id: str, dedupe_key: str) -> Optional[PolicyEvaluation]:
        row = self._conn.execute(
            "SELECT evaluation_json FROM phase12_replays WHERE pack_id=? AND dedupe_key=?",
            (pack_id, dedupe_key),
        ).fetchone()
        if not row:
            return None
        data = json.loads(row[0])
        from .manager import evaluation_from_dict

        return evaluation_from_dict(data)

    def replay_records(self, pack_id: Optional[str] = None) -> list[dict[str, Any]]:
        query = "SELECT pack_id, dedupe_key, evaluation_json, recorded_at FROM phase12_replays"
        params: tuple[Any, ...] = ()
        if pack_id:
            query += " WHERE pack_id=?"
            params = (pack_id,)
        query += " ORDER BY recorded_at"
        return [
            {
                "pack_id": row[0],
                "dedupe_key": row[1],
                "evaluation": json.loads(row[2]),
                "recorded_at": row[3],
            }
            for row in self._conn.execute(query, params).fetchall()
        ]

    # ---- embodiment evidence ------------------------------------------

    def save_intent(self, intent: EmbodimentIntent) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO phase12_embodiment_intents(intent_id, idempotency_key, payload_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    intent.intent_id,
                    intent.idempotency_key,
                    intent.to_json(),
                    intent.created_at,
                ),
            )
        return cursor.rowcount == 1

    def get_intent_by_key(self, key: str) -> Optional[EmbodimentIntent]:
        row = self._conn.execute(
            "SELECT payload_json FROM phase12_embodiment_intents WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        return EmbodimentIntent.from_json(row[0]) if row else None

    def save_command(
        self, command: SimulatedActuatorCommand, status: str = "accepted"
    ) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO phase12_actuator_commands(command_id, idempotency_key, payload_json, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    command.command_id,
                    command.idempotency_key,
                    command.to_json(),
                    status,
                    utc_now(),
                ),
            )
        return cursor.rowcount == 1

    def command_by_key(self, key: str) -> Optional[SimulatedActuatorCommand]:
        row = self._conn.execute(
            "SELECT payload_json FROM phase12_actuator_commands WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        return SimulatedActuatorCommand(**json.loads(row[0])) if row else None

    def command_by_id(self, command_id: str) -> Optional[SimulatedActuatorCommand]:
        row = self._conn.execute(
            "SELECT payload_json FROM phase12_actuator_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
        return SimulatedActuatorCommand(**json.loads(row[0])) if row else None

    def save_decision(self, decision: ControllerDecision) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO phase12_controller_decisions(decision_id, intent_id, command_id, status, payload_json, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    decision.decision_id,
                    decision.intent_id,
                    decision.command_id,
                    decision.status.value,
                    decision.to_json(),
                    utc_now(),
                ),
            )

    def save_actuator_attempt(
        self, action_id: str, attempt_id: str, payload: dict[str, Any]
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO phase12_actuator_attempts "
                "(attempt_id, action_id, payload_json, recorded_at) VALUES (?, ?, ?, ?)",
                (attempt_id, action_id, json.dumps(payload, sort_keys=True), utc_now()),
            )

    def actuator_attempts(self, action_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT attempt_id, payload_json, recorded_at FROM phase12_actuator_attempts "
            "WHERE action_id=? ORDER BY recorded_at, attempt_id",
            (action_id,),
        ).fetchall()
        return [
            {
                "attempt_id": row[0],
                "payload": json.loads(row[1]),
                "recorded_at": row[2],
            }
            for row in rows
        ]

    def decisions(self, intent_id: Optional[str] = None) -> list[ControllerDecision]:
        if intent_id:
            rows = self._conn.execute(
                "SELECT payload_json FROM phase12_controller_decisions WHERE intent_id=? ORDER BY recorded_at",
                (intent_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT payload_json FROM phase12_controller_decisions ORDER BY recorded_at"
            ).fetchall()
        return [self._decision_from_json(row[0]) for row in rows]

    def save_safety_stop(self, stop: SafetyStop) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO phase12_safety_stops(stop_id, trigger, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (stop.stop_id, stop.trigger, stop.to_json(), stop.created_at),
            )

    def safety_stops(self) -> list[SafetyStop]:
        rows = self._conn.execute(
            "SELECT payload_json FROM phase12_safety_stops ORDER BY created_at"
        ).fetchall()
        return [SafetyStop(**json.loads(row[0])) for row in rows]

    def save_verification(self, verification: SimulatedVerification) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO phase12_simulated_verifications(verification_id, action_id, payload_json, recorded_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    verification.verification_id,
                    verification.action_id,
                    verification.to_json(),
                    utc_now(),
                ),
            )

    def get_verification(self, action_id: str) -> Optional[SimulatedVerification]:
        row = self._conn.execute(
            "SELECT payload_json FROM phase12_simulated_verifications WHERE action_id=? ORDER BY recorded_at DESC LIMIT 1",
            (action_id,),
        ).fetchone()
        return SimulatedVerification(**json.loads(row[0])) if row else None

    def save_state(self, key: str, state: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO phase12_simulation_state(state_key, state_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(state_key) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at",
                (key, json.dumps(state, sort_keys=True), utc_now()),
            )

    def load_state(self, key: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT state_json FROM phase12_simulation_state WHERE state_key=?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else {}

    def save_communication(self, state: CommunicationState) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO phase12_communication_states(connection_id, sequence, payload_json, recorded_at) VALUES (?, ?, ?, ?)",
                (state.connection_id, state.sequence, state.to_json(), utc_now()),
            )

    def communication(self, connection_id: str) -> Optional[CommunicationState]:
        row = self._conn.execute(
            "SELECT payload_json FROM phase12_communication_states WHERE connection_id=?",
            (connection_id,),
        ).fetchone()
        if not row:
            return None
        data = json.loads(row[0])
        from .contracts import CommunicationStatus

        data["status"] = CommunicationStatus(data["status"])
        return CommunicationState(**data)

    @staticmethod
    def _installation_from_row(row: tuple[Any, ...]) -> PackInstallation:
        manifest = PolicyPackManifest.from_json(row[1])
        grant = InstallationGrant(**json.loads(row[3]))
        return PackInstallation(
            installation_id=row[0],
            manifest=manifest,
            state=PackInstallState(row[2]),
            authority_scope=grant,
            installed_at=row[4],
            updated_at=row[5],
            previous_version=row[6],
            migration_state=row[7],
            rollback_state=row[8],
            production_active=bool(row[9]),
        )

    @staticmethod
    def _decision_from_json(raw: str) -> ControllerDecision:
        from .contracts import ControllerStatus, SafetyCheck

        data = json.loads(raw)
        data["status"] = ControllerStatus(data["status"])
        data["safety_checks"] = tuple(
            SafetyCheck(**item) for item in data.get("safety_checks", ())
        )
        data["causal_parents"] = tuple(data.get("causal_parents", ()))
        return ControllerDecision(**data)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Phase12Store":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


PolicyPackStore = Phase12Store
EmbodimentStore = Phase12Store
