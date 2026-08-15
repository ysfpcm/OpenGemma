CREATE TABLE cognition_records (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE actions (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    proposal_json TEXT NOT NULL,
    authorization_json TEXT,
    state TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE action_attempt_records (
    record_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    action_id TEXT NOT NULL REFERENCES actions(id),
    phase TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE verifications (
    id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL REFERENCES actions(id),
    payload_json TEXT NOT NULL,
    observed_effect INTEGER NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE action_audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    action_id TEXT NOT NULL REFERENCES actions(id),
    from_state TEXT,
    to_state TEXT NOT NULL,
    reason TEXT NOT NULL,
    error_code TEXT,
    evidence_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TRIGGER action_attempt_records_no_update
BEFORE UPDATE ON action_attempt_records BEGIN
    SELECT RAISE(ABORT, 'action attempt records are immutable');
END;
CREATE TRIGGER action_attempt_records_no_delete
BEFORE DELETE ON action_attempt_records BEGIN
    SELECT RAISE(ABORT, 'action attempt records are immutable');
END;
CREATE TRIGGER action_audit_events_no_update
BEFORE UPDATE ON action_audit_events BEGIN
    SELECT RAISE(ABORT, 'action audit events are immutable');
END;
CREATE TRIGGER action_audit_events_no_delete
BEFORE DELETE ON action_audit_events BEGIN
    SELECT RAISE(ABORT, 'action audit events are immutable');
END;

