CREATE TABLE IF NOT EXISTS phase10_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phase10_records (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT,
    schema_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS phase10_records_kind_status
    ON phase10_records(kind, status);

CREATE TABLE IF NOT EXISTS phase10_audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    record_id TEXT,
    event TEXT NOT NULL,
    details_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS phase10_audit_event_key
    ON phase10_audit_events(record_id, event)
    WHERE event LIKE 'idempotency:%';

CREATE TRIGGER IF NOT EXISTS phase10_audit_no_update
BEFORE UPDATE ON phase10_audit_events BEGIN
    SELECT RAISE(ABORT, 'Phase 10 audit events are immutable');
END;

CREATE TRIGGER IF NOT EXISTS phase10_audit_no_delete
BEFORE DELETE ON phase10_audit_events BEGIN
    SELECT RAISE(ABORT, 'Phase 10 audit events are immutable');
END;

INSERT OR IGNORE INTO phase10_schema_migrations
    VALUES (1, 'phase10-learning-laboratory', CURRENT_TIMESTAMP);
