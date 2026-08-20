"""Additive SQLite storage for the Phase 5 Living World Model.

This store is intentionally independent from the existing context, fact,
knowledge, session, trace, Phase 4, and Guardian stores.  It provides a small
typed projection over those systems without rewriting them or creating an
authority path.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from openjarvis.core.paths import get_config_dir
from openjarvis.world_model.models import (
    Belief,
    BeliefExplanation,
    BeliefStatus,
    ClaimClass,
    Commitment,
    Entity,
    EntityKind,
    Evidence,
    EvidenceRole,
    Goal,
    InformationKind,
    MemoryItem,
    MemoryLayer,
    Observation,
    Prediction,
    Provenance,
    ReviewStatus,
    SourceStatus,
    WorldSource,
    canonical_json,
    enum_value,
    parse_timestamp,
    stable_id,
    utc_iso,
)

# SQL statements are kept readable as complete statements; the formatter and
# tests still validate syntax and behavior for this storage boundary.
# ruff: noqa: E501

_SCHEMA = """
CREATE TABLE IF NOT EXISTS phase5_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS phase5_sources (
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deleted_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS phase5_entities (
    entity_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    retired_at TEXT,
    UNIQUE(kind, canonical_name)
);
CREATE TABLE IF NOT EXISTS phase5_observations (
    observation_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_json TEXT NOT NULL,
    information_kind TEXT NOT NULL,
    claim_class TEXT NOT NULL,
    confidence REAL NOT NULL,
    observed_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    evidence_text TEXT NOT NULL DEFAULT '',
    sensitivity_json TEXT NOT NULL DEFAULT '[]',
    taint_json TEXT NOT NULL DEFAULT '[]',
    correction INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    FOREIGN KEY(source_id) REFERENCES phase5_sources(source_id),
    FOREIGN KEY(entity_id) REFERENCES phase5_entities(entity_id)
);
CREATE INDEX IF NOT EXISTS phase5_observation_topic_idx
    ON phase5_observations(entity_id, predicate, observed_at, observation_id);
CREATE TABLE IF NOT EXISTS phase5_beliefs (
    belief_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_json TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    claim_class TEXT NOT NULL,
    information_kind TEXT NOT NULL,
    base_confidence REAL NOT NULL,
    half_life_days REAL NOT NULL DEFAULT 180.0,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_until TEXT,
    FOREIGN KEY(entity_id) REFERENCES phase5_entities(entity_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS phase5_belief_topic_value_idx
    ON phase5_beliefs(entity_id, predicate, normalized_value, claim_class);
CREATE TABLE IF NOT EXISTS phase5_belief_evidence (
    belief_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY(belief_id, observation_id, role),
    FOREIGN KEY(belief_id) REFERENCES phase5_beliefs(belief_id),
    FOREIGN KEY(observation_id) REFERENCES phase5_observations(observation_id)
);
CREATE INDEX IF NOT EXISTS phase5_belief_evidence_idx
    ON phase5_belief_evidence(belief_id, role);
CREATE TABLE IF NOT EXISTS phase5_belief_revisions (
    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    belief_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    confidence REAL NOT NULL,
    recorded_at TEXT NOT NULL,
    valid_until TEXT,
    reason TEXT NOT NULL DEFAULT '',
    UNIQUE(belief_id, revision),
    FOREIGN KEY(belief_id) REFERENCES phase5_beliefs(belief_id)
);
CREATE TABLE IF NOT EXISTS phase5_current_state (
    entity_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    belief_id TEXT NOT NULL,
    confidence REAL NOT NULL,
    materialized_at TEXT NOT NULL,
    PRIMARY KEY(entity_id, predicate),
    FOREIGN KEY(belief_id) REFERENCES phase5_beliefs(belief_id)
);
CREATE TABLE IF NOT EXISTS phase5_goals (
    goal_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    due_at TEXT,
    FOREIGN KEY(source_id) REFERENCES phase5_sources(source_id)
);
CREATE TABLE IF NOT EXISTS phase5_commitments (
    commitment_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    source_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    due_at TEXT,
    FOREIGN KEY(source_id) REFERENCES phase5_sources(source_id)
);
CREATE TABLE IF NOT EXISTS phase5_predictions (
    prediction_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    predicted_value_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    predicted_at TEXT NOT NULL,
    expected_by TEXT,
    source_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    verification_status TEXT NOT NULL DEFAULT 'pending',
    verified_at TEXT,
    actual_value_json TEXT,
    FOREIGN KEY(source_id) REFERENCES phase5_sources(source_id)
);
CREATE TABLE IF NOT EXISTS phase5_memory_items (
    memory_id TEXT PRIMARY KEY,
    layer TEXT NOT NULL,
    content TEXT NOT NULL,
    source_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(source_id) REFERENCES phase5_sources(source_id)
);
CREATE INDEX IF NOT EXISTS phase5_memory_layer_idx
    ON phase5_memory_items(layer, status, created_at);
CREATE TABLE IF NOT EXISTS phase5_history_sources (
    source_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    excluded INTEGER NOT NULL DEFAULT 0,
    exclusion_reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    FOREIGN KEY(source_id) REFERENCES phase5_sources(source_id)
);
CREATE TABLE IF NOT EXISTS phase5_import_candidates (
    candidate_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    candidate_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value TEXT NOT NULL,
    claim_class TEXT NOT NULL,
    information_kind TEXT NOT NULL,
    confidence REAL NOT NULL,
    quote TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    exclusion_reason TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(source_id) REFERENCES phase5_sources(source_id)
);
CREATE INDEX IF NOT EXISTS phase5_candidate_status_idx
    ON phase5_import_candidates(status, source_id);
INSERT OR IGNORE INTO phase5_schema_migrations(version, applied_at)
VALUES (1, CURRENT_TIMESTAMP);
"""


def _default_path() -> Path:
    return get_config_dir() / "phase5-world-model.db"


def _bounded_confidence(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _json_value(text: str) -> Any:
    return json.loads(text)


def _normal_name(value: str) -> str:
    return " ".join(str(value).strip().split()).casefold()


class WorldModelStore:
    """Thread-safe additive SQLite repository for typed world state."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        raw_path = db_path if db_path is not None else _default_path()
        if raw_path == ":memory:":
            self.path: Path | None = None
            connect_path = ":memory:"
        else:
            self.path = Path(raw_path).expanduser()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connect_path = str(self.path)
        self._conn = sqlite3.connect(connect_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_SCHEMA)
        belief_columns = {
            row[1] for row in self._conn.execute("PRAGMA table_info(phase5_beliefs)")
        }
        if "half_life_days" not in belief_columns:
            self._conn.execute(
                "ALTER TABLE phase5_beliefs ADD COLUMN half_life_days REAL NOT NULL DEFAULT 180.0"
            )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "WorldModelStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    # -- sources and entities ---------------------------------------------

    def register_source(
        self,
        source_id: str,
        *,
        source_type: str,
        label: str = "",
        created_at: datetime | str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> WorldSource:
        source_id = str(source_id).strip()
        source_type = str(source_type).strip()
        if not source_id or not source_type:
            raise ValueError("source_id and source_type are required")
        now = utc_iso(created_at)
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT * FROM phase5_sources WHERE source_id = ?", (source_id,)
            ).fetchone()
            if existing is not None:
                # A source ID is an identity boundary. Preserve its original
                # type, label, and metadata when later projections reuse it,
                # and never resurrect a deleted source.
                return self._source_from_row(existing)
            self._conn.execute(
                """
                INSERT INTO phase5_sources
                    (source_id, source_type, label, status, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO NOTHING
                """,
                (
                    source_id,
                    source_type,
                    label.strip() or source_id,
                    SourceStatus.ACTIVE.value,
                    now,
                    canonical_json(dict(metadata or {})),
                ),
            )
        return self.get_source(source_id)

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> WorldSource:
        return WorldSource(
            source_id=row["source_id"],
            source_type=row["source_type"],
            label=row["label"],
            status=SourceStatus(row["status"]),
            created_at=row["created_at"],
            deleted_at=row["deleted_at"],
            metadata=_json_value(row["metadata_json"]),
        )

    def _active_source(
        self,
        source_id: str,
        *,
        source_type: str,
        label: str = "",
        created_at: datetime | str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> WorldSource:
        source = self.register_source(
            source_id,
            source_type=source_type,
            label=label,
            created_at=created_at,
            metadata=metadata,
        )
        if source.status is SourceStatus.DELETED:
            raise ValueError(f"source {source_id!r} was deleted and cannot be written")
        return source

    def get_source(self, source_id: str) -> WorldSource:
        row = self._conn.execute(
            "SELECT * FROM phase5_sources WHERE source_id = ?", (source_id,)
        ).fetchone()
        if row is None:
            raise KeyError(source_id)
        return self._source_from_row(row)

    def sources(self, *, include_deleted: bool = False) -> list[WorldSource]:
        sql = "SELECT source_id FROM phase5_sources"
        if not include_deleted:
            sql += " WHERE status = 'active'"
        sql += " ORDER BY created_at, source_id"
        return [self.get_source(row[0]) for row in self._conn.execute(sql)]

    def ensure_entity(
        self,
        kind: EntityKind | str,
        canonical_name: str,
        *,
        aliases: Iterable[str] = (),
        created_at: datetime | str | None = None,
    ) -> Entity:
        entity_kind = EntityKind(enum_value(kind))
        name = " ".join(str(canonical_name).strip().split())
        if not name:
            raise ValueError("canonical_name cannot be empty")
        now = utc_iso(created_at)
        entity_id = stable_id("ent", entity_kind.value, _normal_name(name))
        alias_values = tuple(
            sorted({str(item).strip() for item in aliases if str(item).strip()})
        )
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT aliases_json FROM phase5_entities WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()
            merged = set(alias_values)
            if existing is not None:
                merged.update(_json_value(existing[0]))
            self._conn.execute(
                """
                INSERT INTO phase5_entities
                    (entity_id, kind, canonical_name, aliases_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET aliases_json=excluded.aliases_json
                """,
                (
                    entity_id,
                    entity_kind.value,
                    name,
                    canonical_json(sorted(merged)),
                    now,
                ),
            )
        return self.get_entity(entity_id)

    def get_entity(self, entity_id: str) -> Entity:
        row = self._conn.execute(
            "SELECT * FROM phase5_entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        if row is None:
            raise KeyError(entity_id)
        return Entity(
            entity_id=row["entity_id"],
            kind=EntityKind(row["kind"]),
            canonical_name=row["canonical_name"],
            aliases=tuple(_json_value(row["aliases_json"])),
            created_at=row["created_at"],
            retired_at=row["retired_at"],
        )

    def entities(self, *, limit: int = 100) -> list[Entity]:
        rows = self._conn.execute(
            "SELECT entity_id FROM phase5_entities "
            "ORDER BY canonical_name, entity_id LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [self.get_entity(row[0]) for row in rows]

    # -- observations and beliefs -----------------------------------------

    def add_observation(
        self,
        *,
        source_id: str,
        source_type: str,
        source_label: str = "",
        entity_kind: EntityKind | str,
        entity_name: str,
        predicate: str,
        value: Any,
        information_kind: InformationKind | str = InformationKind.REPORTED,
        claim_class: ClaimClass | str = ClaimClass.FACT,
        confidence: float = 0.5,
        observed_at: datetime | str | None = None,
        recorded_at: datetime | str | None = None,
        evidence_text: str = "",
        provenance: Mapping[str, Any] | Provenance | None = None,
        sensitivity_labels: Iterable[str] = (),
        taint_labels: Iterable[str] = (),
        correction: bool = False,
        half_life_days: float = 180.0,
    ) -> Observation:
        """Append one observation and deterministically refresh its topic.

        This method never removes an observation. A correction adds a new
        evidence edge and marks the older competing belief contradicted while
        retaining it for explanation and historical reconstruction.
        """

        source_id = str(source_id).strip()
        pred = " ".join(str(predicate).strip().split())
        if not pred:
            raise ValueError("predicate cannot be empty")
        sensitivity = tuple(
            sorted(
                {str(item).strip() for item in sensitivity_labels if str(item).strip()}
            )
        )
        if sensitivity:
            raise ValueError(
                "sensitive observations cannot enter accepted world memory"
            )
        info_kind = InformationKind(enum_value(information_kind))
        claim = ClaimClass(enum_value(claim_class))
        confidence_value = _bounded_confidence(confidence)
        observed = utc_iso(observed_at)
        recorded = utc_iso(recorded_at or observed_at)
        entity_kind_value = EntityKind(enum_value(entity_kind))
        entity_name = " ".join(str(entity_name).strip().split())
        if not entity_name:
            raise ValueError("entity_name cannot be empty")
        source = self._active_source(
            source_id,
            source_type=source_type,
            label=source_label or source_id,
            created_at=recorded_at or observed_at,
        )
        taint = tuple(
            sorted({str(item).strip() for item in taint_labels if str(item).strip()})
        )
        entity = self.ensure_entity(entity_kind_value, entity_name, created_at=observed)
        if isinstance(provenance, Provenance):
            provenance_record = provenance
        else:
            raw_provenance = dict(provenance or {})
            provenance_record = Provenance(
                source_id=source.source_id,
                source_type=source.source_type,
                source_ref=str(raw_provenance.get("source_ref", "")),
                observed_at=observed,
                transformation=str(raw_provenance.get("transformation", "direct")),
                extractor=str(raw_provenance.get("extractor", "")),
            )
        observation_id = stable_id(
            "obs",
            source_id,
            entity.entity_id,
            pred,
            canonical_json(value),
            observed,
            claim.value,
        )
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO phase5_observations
                    (observation_id, source_id, entity_id, predicate, value_json,
                     information_kind, claim_class, confidence, observed_at,
                     recorded_at, provenance_json, evidence_text, sensitivity_json,
                     taint_json, correction, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    observation_id,
                    source_id,
                    entity.entity_id,
                    pred,
                    canonical_json(value),
                    info_kind.value,
                    claim.value,
                    confidence_value,
                    observed,
                    recorded,
                    canonical_json(provenance_record.to_dict()),
                    str(evidence_text or ""),
                    canonical_json(list(sensitivity)),
                    canonical_json(list(taint)),
                    int(bool(correction)),
                ),
            )
            belief_id = stable_id(
                "belief", entity.entity_id, pred, canonical_json(value), claim.value
            )
            self._conn.execute(
                """
                INSERT OR IGNORE INTO phase5_beliefs
                    (belief_id, entity_id, predicate, value_json, normalized_value,
                     claim_class, information_kind, base_confidence, half_life_days,
                     status, created_at, updated_at, valid_from)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    belief_id,
                    entity.entity_id,
                    pred,
                    canonical_json(value),
                    canonical_json(value),
                    claim.value,
                    info_kind.value,
                    confidence_value,
                    max(1.0, float(half_life_days)),
                    BeliefStatus.HISTORICAL.value,
                    observed,
                    observed,
                    observed,
                ),
            )
            self._conn.execute(
                """
                UPDATE phase5_beliefs
                SET base_confidence = MAX(base_confidence, ?),
                    half_life_days = ?,
                    updated_at = CASE WHEN updated_at < ? THEN ? ELSE updated_at END,
                    information_kind = CASE WHEN updated_at < ? THEN ? ELSE information_kind END
                WHERE belief_id = ?
                """,
                (
                    confidence_value,
                    max(1.0, float(half_life_days)),
                    observed,
                    observed,
                    observed,
                    info_kind.value,
                    belief_id,
                ),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO phase5_belief_evidence(belief_id, observation_id, role) VALUES (?, ?, ?)",
                (belief_id, observation_id, EvidenceRole.SUPPORTS.value),
            )
            competing = self._conn.execute(
                """
                SELECT belief_id FROM phase5_beliefs
                WHERE entity_id = ? AND predicate = ? AND belief_id <> ?
                """,
                (entity.entity_id, pred, belief_id),
            ).fetchall()
            for row in competing:
                other_id = row[0]
                self._conn.execute(
                    "INSERT OR IGNORE INTO phase5_belief_evidence(belief_id, observation_id, role) VALUES (?, ?, ?)",
                    (other_id, observation_id, EvidenceRole.CONTRADICTS.value),
                )
                other_supports = self._conn.execute(
                    """
                    SELECT observation_id FROM phase5_belief_evidence
                    WHERE belief_id = ? AND role = ?
                    """,
                    (other_id, EvidenceRole.SUPPORTS.value),
                ).fetchall()
                for support in other_supports:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO phase5_belief_evidence(belief_id, observation_id, role) VALUES (?, ?, ?)",
                        (belief_id, support[0], EvidenceRole.CONTRADICTS.value),
                    )
                if correction and claim is ClaimClass.FACT:
                    self._conn.execute(
                        "UPDATE phase5_beliefs SET status = ?, valid_until = ? WHERE belief_id = ? AND status <> ? AND updated_at <= ?",
                        (
                            BeliefStatus.CONTRADICTED.value,
                            observed,
                            other_id,
                            BeliefStatus.TOMBSTONED.value,
                            observed,
                        ),
                    )
            self._recompute_topic_locked(entity.entity_id, pred, as_of=recorded)
        return self.get_observation(observation_id)

    def _recompute_topic_locked(
        self, entity_id: str, predicate: str, *, as_of: str
    ) -> None:
        rows = self._conn.execute(
            "SELECT * FROM phase5_beliefs WHERE entity_id = ? AND predicate = ?",
            (entity_id, predicate),
        ).fetchall()
        candidates: list[tuple[float, str, sqlite3.Row]] = []
        unavailable_ids: set[str] = set()
        for row in rows:
            if (
                row["claim_class"] != ClaimClass.FACT.value
                or row["status"] == BeliefStatus.TOMBSTONED.value
            ):
                continue
            supports = self._active_support_rows_locked(row["belief_id"])
            if not supports:
                unavailable_ids.add(row["belief_id"])
                self._conn.execute(
                    "UPDATE phase5_beliefs SET status = ? WHERE belief_id = ?",
                    (BeliefStatus.TOMBSTONED.value, row["belief_id"]),
                )
                continue
            confidence = self._effective_confidence(
                float(row["base_confidence"]),
                row["updated_at"],
                as_of,
                float(row["half_life_days"]),
            )
            if not self._has_active_correction_locked(row["belief_id"]):
                candidates.append((confidence, row["updated_at"], row))
        winner = max(
            candidates,
            key=lambda item: (item[0], item[1], item[2]["belief_id"]),
            default=None,
        )
        winner_id = winner[2]["belief_id"] if winner else None
        for row in rows:
            if (
                row["status"] == BeliefStatus.TOMBSTONED.value
                or row["belief_id"] in unavailable_ids
            ):
                continue
            if row["belief_id"] == winner_id:
                next_status = BeliefStatus.CURRENT.value
            elif self._has_active_correction_locked(row["belief_id"]):
                next_status = BeliefStatus.CONTRADICTED.value
            else:
                next_status = BeliefStatus.HISTORICAL.value
            self._conn.execute(
                "UPDATE phase5_beliefs SET status = ? WHERE belief_id = ?",
                (next_status, row["belief_id"]),
            )
            confidence = self._effective_confidence(
                float(row["base_confidence"]),
                row["updated_at"],
                as_of,
                float(row["half_life_days"]),
            )
            self._append_revision_locked(
                row["belief_id"], next_status, confidence, as_of, "materialized state"
            )

        self._conn.execute(
            "DELETE FROM phase5_current_state WHERE entity_id = ? AND predicate = ?",
            (entity_id, predicate),
        )
        if winner_id:
            self._conn.execute(
                "INSERT INTO phase5_current_state(entity_id, predicate, belief_id, confidence, materialized_at) VALUES (?, ?, ?, ?, ?)",
                (entity_id, predicate, winner_id, winner[0], as_of),
            )

    def _has_active_correction_locked(self, belief_id: str) -> bool:
        row = self._conn.execute(
            """
            SELECT 1
            FROM phase5_belief_evidence be
            JOIN phase5_observations o ON o.observation_id = be.observation_id
            JOIN phase5_sources s ON s.source_id = o.source_id
            WHERE be.belief_id = ?
              AND be.role = ?
              AND o.correction = 1
              AND o.status = 'active'
              AND s.status = 'active'
            LIMIT 1
            """,
            (belief_id, EvidenceRole.CONTRADICTS.value),
        ).fetchone()
        return row is not None

    def _append_revision_locked(
        self,
        belief_id: str,
        status: str,
        confidence: float,
        recorded_at: str,
        reason: str,
    ) -> None:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM phase5_belief_revisions WHERE belief_id = ?",
            (belief_id,),
        ).fetchone()
        revision = int(row[0]) + 1
        self._conn.execute(
            "INSERT INTO phase5_belief_revisions(belief_id, revision, status, confidence, recorded_at, reason) VALUES (?, ?, ?, ?, ?, ?)",
            (belief_id, revision, status, confidence, recorded_at, reason),
        )

    @staticmethod
    def _effective_confidence(
        base: float,
        updated_at: str,
        as_of: str,
        half_life_days: float = 180.0,
    ) -> float:
        age_days = max(
            0.0,
            (parse_timestamp(as_of) - parse_timestamp(updated_at)).total_seconds()
            / 86400.0,
        )
        # A conservative default half-life keeps stale information visible but
        # prevents it from winning against a newer explicit correction.
        return _bounded_confidence(
            base * math.exp(-math.log(2.0) * age_days / max(1.0, half_life_days))
        )

    def _active_support_rows_locked(self, belief_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT o.* FROM phase5_belief_evidence be
            JOIN phase5_observations o ON o.observation_id = be.observation_id
            JOIN phase5_sources s ON s.source_id = o.source_id
            WHERE be.belief_id = ? AND be.role = ?
              AND o.status = 'active' AND s.status = 'active'
            ORDER BY o.observed_at, o.observation_id
            """,
            (belief_id, EvidenceRole.SUPPORTS.value),
        ).fetchall()

    def get_observation(self, observation_id: str) -> Observation:
        row = self._conn.execute(
            "SELECT * FROM phase5_observations WHERE observation_id = ?",
            (observation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(observation_id)
        return self._observation_from_row(row)

    def observations(
        self,
        *,
        source_id: str | None = None,
        limit: int = 100,
        include_tombstoned: bool = False,
    ) -> list[Observation]:
        clauses: list[str] = []
        params: list[Any] = []
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        if not include_tombstoned:
            clauses.append("status = 'active'")
        where = " AND ".join(clauses) or "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM phase5_observations WHERE {where} ORDER BY observed_at, observation_id LIMIT ?",
            [*params, max(1, int(limit))],
        ).fetchall()
        return [self._observation_from_row(row) for row in rows]

    def _observation_from_row(self, row: sqlite3.Row) -> Observation:
        provenance = _json_value(row["provenance_json"])
        return Observation(
            observation_id=row["observation_id"],
            source_id=row["source_id"],
            entity_id=row["entity_id"],
            predicate=row["predicate"],
            value=_json_value(row["value_json"]),
            information_kind=InformationKind(row["information_kind"]),
            claim_class=ClaimClass(row["claim_class"]),
            confidence=float(row["confidence"]),
            observed_at=row["observed_at"],
            recorded_at=row["recorded_at"],
            provenance=Provenance(**provenance),
            evidence_text=row["evidence_text"],
            sensitivity_labels=tuple(_json_value(row["sensitivity_json"])),
            taint_labels=tuple(_json_value(row["taint_json"])),
            correction=bool(row["correction"]),
            status=row["status"],
        )

    def _belief_from_row(self, row: sqlite3.Row, *, as_of: str) -> Belief:
        supports = self._conn.execute(
            """
            SELECT o.* FROM phase5_belief_evidence be
            JOIN phase5_observations o ON o.observation_id = be.observation_id
            JOIN phase5_sources s ON s.source_id = o.source_id
            WHERE be.belief_id = ? AND be.role = ?
              AND o.status = 'active' AND s.status = 'active'
            ORDER BY o.observed_at, o.observation_id
            """,
            (row["belief_id"], EvidenceRole.SUPPORTS.value),
        ).fetchall()
        contradicts = self._conn.execute(
            """
            SELECT o.* FROM phase5_belief_evidence be
            JOIN phase5_observations o ON o.observation_id = be.observation_id
            JOIN phase5_sources s ON s.source_id = o.source_id
            WHERE be.belief_id = ? AND be.role = ?
              AND o.status = 'active' AND s.status = 'active'
            ORDER BY o.observed_at, o.observation_id
            """,
            (row["belief_id"], EvidenceRole.CONTRADICTS.value),
        ).fetchall()
        confidence = self._effective_confidence(
            float(row["base_confidence"]),
            row["updated_at"],
            as_of,
            float(row["half_life_days"]),
        )
        uncertainty: list[str] = []
        if contradicts:
            uncertainty.append("conflicting evidence is preserved")
        if confidence < 0.5:
            uncertainty.append(
                "confidence has decayed because the supporting information is stale"
            )
        return Belief(
            belief_id=row["belief_id"],
            entity_id=row["entity_id"],
            predicate=row["predicate"],
            value=_json_value(row["value_json"]),
            claim_class=ClaimClass(row["claim_class"]),
            information_kind=InformationKind(row["information_kind"]),
            base_confidence=float(row["base_confidence"]),
            confidence=confidence,
            status=BeliefStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            supporting_evidence_ids=tuple(item["observation_id"] for item in supports),
            contradicting_evidence_ids=tuple(
                item["observation_id"] for item in contradicts
            ),
            provenance=tuple(
                Provenance(**_json_value(item["provenance_json"])) for item in supports
            ),
            uncertainty=tuple(uncertainty),
        )

    def beliefs(
        self,
        *,
        entity_id: str | None = None,
        predicate: str | None = None,
        as_of: datetime | str | None = None,
        include_hypotheses: bool = True,
        limit: int = 100,
    ) -> list[Belief]:
        as_of_text = utc_iso(as_of)
        clauses: list[str] = []
        params: list[Any] = []
        if entity_id:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        if predicate:
            clauses.append("predicate = ?")
            params.append(predicate)
        if not include_hypotheses:
            clauses.append("claim_class = 'fact'")
        where = " AND ".join(clauses) or "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM phase5_beliefs WHERE {where} ORDER BY updated_at DESC, belief_id LIMIT ?",
            [*params, max(1, int(limit))],
        ).fetchall()
        return [self._belief_from_row(row, as_of=as_of_text) for row in rows]

    def current_belief(
        self, entity_id: str, predicate: str, *, as_of: datetime | str | None = None
    ) -> Belief | None:
        explanation = self.explain(entity_id, predicate, as_of=as_of)
        return explanation.current

    def explain(
        self,
        entity_id: str,
        predicate: str,
        *,
        as_of: datetime | str | None = None,
    ) -> BeliefExplanation:
        as_of_text = utc_iso(as_of)
        rows = self._conn.execute(
            "SELECT * FROM phase5_beliefs WHERE entity_id = ? AND predicate = ? ORDER BY updated_at, belief_id",
            (entity_id, predicate),
        ).fetchall()
        beliefs = [self._belief_from_row(row, as_of=as_of_text) for row in rows]
        fact_beliefs = [
            item
            for item in beliefs
            if item.claim_class is ClaimClass.FACT
            and item.status is not BeliefStatus.TOMBSTONED
        ]
        eligible = [
            item
            for item in fact_beliefs
            if item.status is not BeliefStatus.CONTRADICTED
        ]
        current = max(
            eligible,
            key=lambda item: (item.confidence, item.updated_at, item.belief_id),
            default=None,
        )
        supports: list[Evidence] = []
        contradicts: list[Evidence] = []
        if current:
            for evidence_id in current.supporting_evidence_ids:
                supports.append(
                    Evidence(
                        evidence_id,
                        EvidenceRole.SUPPORTS,
                        self.get_observation(evidence_id),
                    )
                )
            for evidence_id in current.contradicting_evidence_ids:
                contradicts.append(
                    Evidence(
                        evidence_id,
                        EvidenceRole.CONTRADICTS,
                        self.get_observation(evidence_id),
                    )
                )
        historical = tuple(
            item
            for item in fact_beliefs
            if item.belief_id != (current.belief_id if current else "")
        )
        hypotheses = tuple(
            item for item in beliefs if item.claim_class is ClaimClass.HYPOTHESIS
        )
        predictions = tuple(
            item for item in beliefs if item.claim_class is ClaimClass.PREDICTION
        )
        uncertainty: list[str] = []
        if contradicts:
            uncertainty.append("a newer or competing claim contradicts older evidence")
        if hypotheses:
            uncertainty.append("hypotheses are not treated as facts")
        if predictions:
            uncertainty.append(
                "predictions remain unverified until an observation verifies them"
            )
        if current and current.confidence < 0.5:
            uncertainty.append(
                "the current belief is stale and its confidence has decayed"
            )
        return BeliefExplanation(
            entity_id=entity_id,
            predicate=predicate,
            as_of=as_of_text,
            current=current,
            supports=tuple(supports),
            contradicts=tuple(contradicts),
            historical=historical,
            hypotheses=hypotheses,
            predictions=predictions,
            uncertainty=tuple(uncertainty),
        )

    def beliefs_at(self, as_of: datetime | str) -> list[Belief]:
        """Reconstruct materialized belief revisions at an earlier instant."""

        as_of_text = utc_iso(as_of)
        rows = self._conn.execute(
            """
            SELECT b.*, r.status AS revision_status, r.confidence AS revision_confidence
            FROM phase5_belief_revisions r
            JOIN phase5_beliefs b ON b.belief_id = r.belief_id
            WHERE r.recorded_at <= ?
              AND r.revision = (
                  SELECT MAX(r2.revision) FROM phase5_belief_revisions r2
                  WHERE r2.belief_id = r.belief_id AND r2.recorded_at <= ?
              )
            ORDER BY b.entity_id, b.predicate, b.belief_id
            """,
            (as_of_text, as_of_text),
        ).fetchall()
        result: list[Belief] = []
        for row in rows:
            base = self._belief_from_row(row, as_of=as_of_text)
            result.append(
                Belief(
                    belief_id=base.belief_id,
                    entity_id=base.entity_id,
                    predicate=base.predicate,
                    value=base.value,
                    claim_class=base.claim_class,
                    information_kind=base.information_kind,
                    base_confidence=base.base_confidence,
                    confidence=float(row["revision_confidence"]),
                    status=BeliefStatus(row["revision_status"]),
                    created_at=base.created_at,
                    updated_at=base.updated_at,
                    valid_from=base.valid_from,
                    valid_until=base.valid_until,
                    supporting_evidence_ids=base.supporting_evidence_ids,
                    contradicting_evidence_ids=base.contradicting_evidence_ids,
                    provenance=base.provenance,
                    uncertainty=base.uncertainty,
                )
            )
        return result

    # -- goals, commitments, predictions ----------------------------------

    def add_goal(
        self,
        *,
        entity_id: str,
        title: str,
        source_id: str,
        evidence_ids: Iterable[str] = (),
        priority: int = 0,
        status: str = "active",
        created_at: datetime | str | None = None,
        due_at: datetime | str | None = None,
    ) -> Goal:
        source_id = str(source_id).strip()
        self._active_source(
            source_id, source_type="goal", label=source_id, created_at=created_at
        )
        now = utc_iso(created_at)
        goal_id = stable_id("goal", entity_id, title, source_id)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO phase5_goals(goal_id, entity_id, title, status, priority, source_id, evidence_json, created_at, due_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    goal_id,
                    entity_id,
                    title.strip(),
                    status,
                    int(priority),
                    source_id,
                    canonical_json(list(evidence_ids)),
                    now,
                    utc_iso(due_at) if due_at else None,
                ),
            )
        return self.get_goal(goal_id)

    def get_goal(self, goal_id: str) -> Goal:
        row = self._conn.execute(
            "SELECT * FROM phase5_goals WHERE goal_id = ?", (goal_id,)
        ).fetchone()
        if row is None:
            raise KeyError(goal_id)
        return Goal(
            row["goal_id"],
            row["entity_id"],
            row["title"],
            row["status"],
            int(row["priority"]),
            row["source_id"],
            tuple(_json_value(row["evidence_json"])),
            row["created_at"],
            row["due_at"],
        )

    def goals(self, *, entity_id: str | None = None) -> list[Goal]:
        sql = "SELECT goal_id FROM phase5_goals"
        params: list[Any] = []
        if entity_id:
            sql += " WHERE entity_id = ?"
            params.append(entity_id)
        sql += " ORDER BY priority DESC, created_at, goal_id"
        return [self.get_goal(row[0]) for row in self._conn.execute(sql, params)]

    def add_commitment(
        self,
        *,
        entity_id: str,
        title: str,
        source_id: str,
        evidence_ids: Iterable[str] = (),
        status: str = "open",
        created_at: datetime | str | None = None,
        due_at: datetime | str | None = None,
    ) -> Commitment:
        source_id = str(source_id).strip()
        self._active_source(
            source_id, source_type="commitment", label=source_id, created_at=created_at
        )
        now = utc_iso(created_at)
        commitment_id = stable_id("commit", entity_id, title, source_id)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO phase5_commitments(commitment_id, entity_id, title, status, source_id, evidence_json, created_at, due_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    commitment_id,
                    entity_id,
                    title.strip(),
                    status,
                    source_id,
                    canonical_json(list(evidence_ids)),
                    now,
                    utc_iso(due_at) if due_at else None,
                ),
            )
        return self.get_commitment(commitment_id)

    def get_commitment(self, commitment_id: str) -> Commitment:
        row = self._conn.execute(
            "SELECT * FROM phase5_commitments WHERE commitment_id = ?", (commitment_id,)
        ).fetchone()
        if row is None:
            raise KeyError(commitment_id)
        return Commitment(
            row["commitment_id"],
            row["entity_id"],
            row["title"],
            row["status"],
            row["source_id"],
            tuple(_json_value(row["evidence_json"])),
            row["created_at"],
            row["due_at"],
        )

    def commitments(self, *, entity_id: str | None = None) -> list[Commitment]:
        sql = "SELECT commitment_id FROM phase5_commitments"
        params: list[Any] = []
        if entity_id:
            sql += " WHERE entity_id = ?"
            params.append(entity_id)
        sql += " ORDER BY created_at, commitment_id"
        return [self.get_commitment(row[0]) for row in self._conn.execute(sql, params)]

    def add_prediction(
        self,
        *,
        entity_id: str,
        predicate: str,
        predicted_value: Any,
        source_id: str,
        evidence_ids: Iterable[str] = (),
        confidence: float = 0.5,
        predicted_at: datetime | str | None = None,
        expected_by: datetime | str | None = None,
    ) -> Prediction:
        source_id = str(source_id).strip()
        self._active_source(
            source_id,
            source_type="prediction",
            label=source_id,
            created_at=predicted_at,
        )
        at = utc_iso(predicted_at)
        prediction_id = stable_id(
            "prediction", entity_id, predicate, canonical_json(predicted_value), at
        )
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO phase5_predictions(prediction_id, entity_id, predicate, predicted_value_json, confidence, predicted_at, expected_by, source_id, evidence_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    prediction_id,
                    entity_id,
                    predicate,
                    canonical_json(predicted_value),
                    _bounded_confidence(confidence),
                    at,
                    utc_iso(expected_by) if expected_by else None,
                    source_id,
                    canonical_json(list(evidence_ids)),
                ),
            )
        return self.get_prediction(prediction_id)

    def get_prediction(self, prediction_id: str) -> Prediction:
        row = self._conn.execute(
            "SELECT * FROM phase5_predictions WHERE prediction_id = ?", (prediction_id,)
        ).fetchone()
        if row is None:
            raise KeyError(prediction_id)
        return Prediction(
            row["prediction_id"],
            row["entity_id"],
            row["predicate"],
            _json_value(row["predicted_value_json"]),
            float(row["confidence"]),
            row["predicted_at"],
            row["expected_by"],
            row["source_id"],
            tuple(_json_value(row["evidence_json"])),
            row["verification_status"],
            row["verified_at"],
            _json_value(row["actual_value_json"]) if row["actual_value_json"] else None,
        )

    def verify_prediction(
        self,
        prediction_id: str,
        *,
        actual_value: Any,
        verified_at: datetime | str | None = None,
    ) -> Prediction:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE phase5_predictions SET verification_status = ?, verified_at = ?, actual_value_json = ? WHERE prediction_id = ?",
                (
                    "correct" if actual_value is not None else "incorrect",
                    utc_iso(verified_at),
                    canonical_json(actual_value),
                    prediction_id,
                ),
            )
        return self.get_prediction(prediction_id)

    # -- layered memory ----------------------------------------------------

    def add_memory_item(
        self,
        *,
        layer: MemoryLayer | str,
        content: str,
        source_id: str,
        evidence_ids: Iterable[str] = (),
        confidence: float = 0.5,
        created_at: datetime | str | None = None,
        expires_at: datetime | str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MemoryItem:
        source_id = str(source_id).strip()
        text = str(content).strip()
        if not text:
            raise ValueError("memory content cannot be empty")
        layer_value = MemoryLayer(enum_value(layer))
        self._active_source(
            source_id, source_type="memory", label=source_id, created_at=created_at
        )
        created = utc_iso(created_at)
        memory_id = stable_id("memory", layer_value.value, source_id, text, created)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO phase5_memory_items(memory_id, layer, content, source_id, evidence_json, confidence, created_at, expires_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    memory_id,
                    layer_value.value,
                    text,
                    source_id,
                    canonical_json(list(evidence_ids)),
                    _bounded_confidence(confidence),
                    created,
                    utc_iso(expires_at) if expires_at else None,
                    canonical_json(dict(metadata or {})),
                ),
            )
        return self.get_memory_item(memory_id)

    def get_memory_item(self, memory_id: str) -> MemoryItem:
        row = self._conn.execute(
            "SELECT * FROM phase5_memory_items WHERE memory_id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            raise KeyError(memory_id)
        return MemoryItem(
            row["memory_id"],
            MemoryLayer(row["layer"]),
            row["content"],
            row["source_id"],
            tuple(_json_value(row["evidence_json"])),
            float(row["confidence"]),
            row["created_at"],
            row["expires_at"],
            row["status"],
            _json_value(row["metadata_json"]),
        )

    def memory_items(
        self,
        *,
        layer: MemoryLayer | str | None = None,
        query: str = "",
        limit: int = 50,
        as_of: datetime | str | None = None,
    ) -> list[MemoryItem]:
        clauses = ["status = 'active'", "(expires_at IS NULL OR expires_at > ?)"]
        params: list[Any] = [utc_iso(as_of)]
        if layer:
            clauses.append("layer = ?")
            params.append(MemoryLayer(enum_value(layer)).value)
        if query.strip():
            clauses.append("content LIKE ?")
            params.append(f"%{query.strip()}%")
        rows = self._conn.execute(
            f"SELECT memory_id FROM phase5_memory_items WHERE {' AND '.join(clauses)} ORDER BY created_at DESC, memory_id LIMIT ?",
            [*params, max(1, int(limit))],
        ).fetchall()
        return [self.get_memory_item(row[0]) for row in rows]

    def tombstone_source(
        self, source_id: str, *, deleted_at: datetime | str | None = None
    ) -> dict[str, int | str]:
        deleted = utc_iso(deleted_at)
        with self._lock, self._conn:
            source = self._conn.execute(
                "SELECT source_id FROM phase5_sources WHERE source_id = ?", (source_id,)
            ).fetchone()
            if source is None:
                raise KeyError(source_id)
            obs_count = self._conn.execute(
                "UPDATE phase5_observations SET status = 'tombstoned' WHERE source_id = ? AND status <> 'tombstoned'",
                (source_id,),
            ).rowcount
            memory_count = self._conn.execute(
                "UPDATE phase5_memory_items SET status = 'tombstoned' WHERE source_id = ? AND status <> 'tombstoned'",
                (source_id,),
            ).rowcount
            candidate_count = self._conn.execute(
                "UPDATE phase5_import_candidates SET status = 'deleted' WHERE source_id = ? AND status <> 'deleted'",
                (source_id,),
            ).rowcount
            self._conn.execute(
                "UPDATE phase5_history_sources SET status = 'deleted', content = '' WHERE source_id = ?",
                (source_id,),
            )
            self._conn.execute(
                "UPDATE phase5_sources SET status = 'deleted', deleted_at = ? WHERE source_id = ?",
                (deleted, source_id),
            )
            topics = self._conn.execute(
                "SELECT DISTINCT entity_id, predicate FROM phase5_beliefs"
            ).fetchall()
            for row in topics:
                self._recompute_topic_locked(
                    row["entity_id"], row["predicate"], as_of=deleted
                )
        return {
            "source_id": source_id,
            "observations_tombstoned": obs_count,
            "memory_items_tombstoned": memory_count,
            "candidates_deleted": candidate_count,
        }

    def export_source(self, source_id: str) -> dict[str, Any]:
        source = self.get_source(source_id)
        history = self._conn.execute(
            "SELECT * FROM phase5_history_sources WHERE source_id = ?", (source_id,)
        ).fetchone()
        return {
            "source": source.to_dict(),
            "history": dict(history)
            if history is not None and source.status is SourceStatus.ACTIVE
            else None,
            "observations": [
                item.to_dict()
                for item in self.observations(
                    source_id=source_id, include_tombstoned=False
                )
            ],
            "candidates": [
                self.get_candidate(row[0]).to_dict()
                for row in self._conn.execute(
                    "SELECT candidate_id FROM phase5_import_candidates WHERE source_id = ? ORDER BY candidate_id",
                    (source_id,),
                )
            ],
        }

    # -- history importer persistence -------------------------------------

    def save_history_source(
        self,
        *,
        source_id: str,
        conversation_id: str,
        title: str,
        category: str,
        created_at: str,
        content: str,
        excluded: bool = False,
        exclusion_reason: str = "",
    ) -> None:
        self._active_source(
            source_id,
            source_type="personal_history",
            label=title or conversation_id,
            created_at=created_at,
            metadata={"conversation_id": conversation_id, "category": category},
        )
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO phase5_history_sources(source_id, conversation_id, title, category, created_at, content, excluded, exclusion_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_id,
                    conversation_id,
                    title,
                    category,
                    utc_iso(created_at),
                    content if not excluded else "",
                    int(excluded),
                    exclusion_reason,
                ),
            )

    def add_candidate(self, candidate: Any) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO phase5_import_candidates(
                    candidate_id, source_id, conversation_id, candidate_type,
                    subject, predicate, value, claim_class, information_kind,
                    confidence, quote, observed_at, status, exclusion_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                    source_id=excluded.source_id,
                    conversation_id=excluded.conversation_id,
                    candidate_type=excluded.candidate_type,
                    subject=excluded.subject,
                    predicate=excluded.predicate,
                    value=excluded.value,
                    claim_class=excluded.claim_class,
                    information_kind=excluded.information_kind,
                    confidence=excluded.confidence,
                    quote=excluded.quote,
                    observed_at=excluded.observed_at,
                    exclusion_reason=excluded.exclusion_reason
                """,
                (
                    candidate.candidate_id,
                    candidate.source_id,
                    candidate.conversation_id,
                    candidate.candidate_type,
                    candidate.subject,
                    candidate.predicate,
                    candidate.value,
                    enum_value(candidate.claim_class),
                    enum_value(candidate.information_kind),
                    candidate.confidence,
                    candidate.quote,
                    candidate.observed_at,
                    enum_value(candidate.status),
                    candidate.exclusion_reason,
                ),
            )

    def get_candidate(self, candidate_id: str) -> Any:
        from openjarvis.world_model.models import ImportCandidate

        row = self._conn.execute(
            "SELECT * FROM phase5_import_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        return ImportCandidate(
            row["candidate_id"],
            row["source_id"],
            row["conversation_id"],
            row["candidate_type"],
            row["subject"],
            row["predicate"],
            row["value"],
            ClaimClass(row["claim_class"]),
            InformationKind(row["information_kind"]),
            float(row["confidence"]),
            row["quote"],
            row["observed_at"],
            ReviewStatus(row["status"]),
            row["exclusion_reason"],
        )

    def candidates(
        self, *, status: ReviewStatus | str | None = None, source_id: str | None = None
    ) -> list[Any]:
        sql = "SELECT candidate_id FROM phase5_import_candidates WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(enum_value(status))
        if source_id:
            sql += " AND source_id = ?"
            params.append(source_id)
        sql += " ORDER BY observed_at, candidate_id"
        return [self.get_candidate(row[0]) for row in self._conn.execute(sql, params)]

    def update_candidate_status(
        self, candidate_id: str, status: ReviewStatus | str
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE phase5_import_candidates SET status = ? WHERE candidate_id = ?",
                (enum_value(status), candidate_id),
            )

    def history_inventory(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT source_id, conversation_id, title, category, created_at, excluded, exclusion_reason, status FROM phase5_history_sources ORDER BY created_at, source_id"
        ).fetchall()
        return [dict(row) for row in rows]


__all__ = ["WorldModelStore"]
