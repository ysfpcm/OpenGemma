BEGIN;
CREATE TABLE IF NOT EXISTS phase11_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS phase11_records (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS phase11_records_kind_status
    ON phase11_records(kind, status, created_at);
CREATE TABLE IF NOT EXISTS phase11_audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    object_id TEXT,
    event TEXT NOT NULL,
    details_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS phase11_audit_object
    ON phase11_audit_events(object_id, sequence);
CREATE TRIGGER IF NOT EXISTS phase11_audit_immutable_update
BEFORE UPDATE ON phase11_audit_events BEGIN
    SELECT RAISE(ABORT, 'phase11 audit is immutable');
END;
CREATE TRIGGER IF NOT EXISTS phase11_audit_immutable_delete
BEFORE DELETE ON phase11_audit_events BEGIN
    SELECT RAISE(ABORT, 'phase11 audit is immutable');
END;
INSERT OR IGNORE INTO phase11_schema_migrations(version, applied_at)
VALUES (1, CURRENT_TIMESTAMP);
COMMIT;
