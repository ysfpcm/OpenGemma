BEGIN;

CREATE TABLE IF NOT EXISTS phase12_installed_packs (
    installation_id TEXT PRIMARY KEY,
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    state TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    grant_json TEXT NOT NULL,
    installed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    previous_version TEXT,
    migration_state TEXT NOT NULL,
    rollback_state TEXT NOT NULL,
    production_active INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS phase12_installed_packs_lookup
    ON phase12_installed_packs(pack_id, updated_at);

CREATE TABLE IF NOT EXISTS phase12_pack_versions (
    installation_id TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    grant_json TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (installation_id, version)
);

CREATE TABLE IF NOT EXISTS phase12_pack_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    installation_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    details_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS phase12_pack_events_immutable_update
BEFORE UPDATE ON phase12_pack_events BEGIN
    SELECT RAISE(ABORT, 'phase12 pack events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS phase12_pack_events_immutable_delete
BEFORE DELETE ON phase12_pack_events BEGIN
    SELECT RAISE(ABORT, 'phase12 pack events are immutable');
END;

CREATE TABLE IF NOT EXISTS phase12_replays (
    replay_id TEXT PRIMARY KEY,
    pack_id TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    event_json TEXT NOT NULL,
    evaluation_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE(pack_id, dedupe_key)
);

CREATE TABLE IF NOT EXISTS phase12_embodiment_intents (
    intent_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS phase12_actuator_commands (
    command_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS phase12_controller_decisions (
    decision_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS phase12_actuator_attempts (
    attempt_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS phase12_safety_stops (
    stop_id TEXT PRIMARY KEY,
    trigger TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS phase12_simulated_verifications (
    verification_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS phase12_simulated_verifications_action
    ON phase12_simulated_verifications(action_id, recorded_at);
CREATE TABLE IF NOT EXISTS phase12_simulation_state (
    state_key TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS phase12_communication_states (
    connection_id TEXT PRIMARY KEY,
    sequence INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS phase12_commands_immutable_update
BEFORE UPDATE ON phase12_actuator_commands BEGIN
    SELECT RAISE(ABORT, 'phase12 actuator commands are immutable');
END;
CREATE TRIGGER IF NOT EXISTS phase12_commands_immutable_delete
BEFORE DELETE ON phase12_actuator_commands BEGIN
    SELECT RAISE(ABORT, 'phase12 actuator commands are immutable');
END;
CREATE TRIGGER IF NOT EXISTS phase12_verifications_immutable_update
BEFORE UPDATE ON phase12_simulated_verifications BEGIN
    SELECT RAISE(ABORT, 'phase12 verifications are immutable');
END;
CREATE TRIGGER IF NOT EXISTS phase12_actuator_attempts_immutable_update
BEFORE UPDATE ON phase12_actuator_attempts BEGIN
    SELECT RAISE(ABORT, 'phase12 actuator attempts are immutable');
END;
CREATE TRIGGER IF NOT EXISTS phase12_actuator_attempts_immutable_delete
BEFORE DELETE ON phase12_actuator_attempts BEGIN
    SELECT RAISE(ABORT, 'phase12 actuator attempts are immutable');
END;
CREATE TRIGGER IF NOT EXISTS phase12_verifications_immutable_delete
BEFORE DELETE ON phase12_simulated_verifications BEGIN
    SELECT RAISE(ABORT, 'phase12 verifications are immutable');
END;
CREATE TRIGGER IF NOT EXISTS phase12_stops_immutable_update
BEFORE UPDATE ON phase12_safety_stops BEGIN
    SELECT RAISE(ABORT, 'phase12 safety stops are immutable');
END;
CREATE TRIGGER IF NOT EXISTS phase12_stops_immutable_delete
BEFORE DELETE ON phase12_safety_stops BEGIN
    SELECT RAISE(ABORT, 'phase12 safety stops are immutable');
END;

COMMIT;
