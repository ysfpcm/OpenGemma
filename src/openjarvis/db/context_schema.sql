-- SQLite-first live context schema for Ophanim.
--
-- This schema is intentionally additive. It does not replace the existing
-- PostgreSQL-oriented tables in schema.sql.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS context_sources (
    id                  INTEGER PRIMARY KEY,
    source_key          TEXT NOT NULL UNIQUE,
    source_type         TEXT NOT NULL,
    display_name        TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (status IN ('unknown', 'online', 'degraded', 'offline')),
    stale_after_seconds INTEGER NOT NULL DEFAULT 300
                        CHECK (stale_after_seconds > 0),
    last_seen_at        TEXT,
    last_sync_at        TEXT,
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS context_entities (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES context_sources(id) ON DELETE CASCADE,
    external_id     TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    area            TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    first_seen_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (source_id, external_id)
);

CREATE TABLE IF NOT EXISTS context_events (
    id                  INTEGER PRIMARY KEY,
    source_id           INTEGER NOT NULL REFERENCES context_sources(id) ON DELETE CASCADE,
    entity_id           INTEGER REFERENCES context_entities(id) ON DELETE SET NULL,
    event_type          TEXT NOT NULL,
    state_key           TEXT,
    external_event_id   TEXT,
    occurred_at         TEXT NOT NULL,
    received_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    payload_json        TEXT NOT NULL DEFAULT '{}',
    UNIQUE (source_id, external_event_id)
);

CREATE TABLE IF NOT EXISTS context_current_state (
    entity_id       INTEGER NOT NULL REFERENCES context_entities(id) ON DELETE CASCADE,
    state_key       TEXT NOT NULL,
    value_json      TEXT NOT NULL,
    unit            TEXT NOT NULL DEFAULT '',
    quality         TEXT NOT NULL DEFAULT 'good'
                    CHECK (quality IN ('good', 'stale', 'unknown', 'error')),
    observed_at     TEXT NOT NULL,
    received_at     TEXT NOT NULL,
    event_id        INTEGER REFERENCES context_events(id) ON DELETE SET NULL,
    PRIMARY KEY (entity_id, state_key)
);

CREATE TABLE IF NOT EXISTS context_state_history (
    id                      INTEGER PRIMARY KEY,
    entity_id               INTEGER NOT NULL REFERENCES context_entities(id) ON DELETE CASCADE,
    state_key               TEXT NOT NULL,
    previous_value_json     TEXT,
    new_value_json          TEXT NOT NULL,
    unit                    TEXT NOT NULL DEFAULT '',
    event_id                INTEGER REFERENCES context_events(id) ON DELETE SET NULL,
    occurred_at             TEXT NOT NULL,
    recorded_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (event_id, state_key)
);

CREATE TABLE IF NOT EXISTS context_snapshots (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES context_sources(id) ON DELETE CASCADE,
    entity_id       INTEGER REFERENCES context_entities(id) ON DELETE SET NULL,
    event_id        INTEGER REFERENCES context_events(id) ON DELETE SET NULL,
    snapshot_id     TEXT NOT NULL UNIQUE,
    local_ref       TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('received', 'failed')),
    content_type    TEXT,
    byte_size       INTEGER,
    sha256          TEXT,
    error_code      TEXT,
    captured_at     TEXT NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_context_entities_source_external
    ON context_entities(source_id, external_id);

CREATE INDEX IF NOT EXISTS idx_context_entities_area_type
    ON context_entities(area, entity_type, enabled);

CREATE INDEX IF NOT EXISTS idx_context_events_source_time
    ON context_events(source_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_context_events_entity_time
    ON context_events(entity_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_context_events_time
    ON context_events(occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_context_current_state_key
    ON context_current_state(state_key, entity_id);

CREATE INDEX IF NOT EXISTS idx_context_history_entity_state_time
    ON context_state_history(entity_id, state_key, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_context_history_time
    ON context_state_history(occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_context_snapshots_time
    ON context_snapshots(captured_at DESC);

CREATE INDEX IF NOT EXISTS idx_context_snapshots_entity_time
    ON context_snapshots(entity_id, captured_at DESC);

-- One-time, evidence-first departure watchers.  These tables intentionally
-- live beside the connected-world context tables so a watcher can never
-- claim an observation that was not durably ingested first.
CREATE TABLE IF NOT EXISTS departure_watchers (
    watcher_id          TEXT PRIMARY KEY,
    conversation_id     TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    armed_at             TEXT NOT NULL,
    expires_at           TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN (
                            'ACTIVE', 'TRIGGERED', 'COMPLETED', 'EXPIRED',
                            'CANCELED', 'NEEDS_ATTENTION'
                        )),
    mode                 TEXT NOT NULL CHECK (mode IN ('simulation', 'live')),
    fire_count           INTEGER NOT NULL DEFAULT 0 CHECK (fire_count >= 0),
    trigger_json         TEXT NOT NULL,
    action_json          TEXT NOT NULL,
    permissions_json     TEXT NOT NULL,
    cancellation_reason  TEXT,
    cancelled_at         TEXT,
    triggered_at         TEXT,
    completed_at         TEXT,
    last_error           TEXT,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS departure_watcher_triggers (
    watcher_id       TEXT PRIMARY KEY REFERENCES departure_watchers(watcher_id)
                     ON DELETE CASCADE,
    trigger_type     TEXT NOT NULL,
    condition_json   TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS departure_watcher_lifecycle (
    id               INTEGER PRIMARY KEY,
    watcher_id       TEXT NOT NULL REFERENCES departure_watchers(watcher_id)
                     ON DELETE CASCADE,
    status           TEXT NOT NULL,
    reason           TEXT NOT NULL DEFAULT '',
    details_json     TEXT NOT NULL DEFAULT '{}',
    recorded_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS departure_watcher_events (
    id               INTEGER PRIMARY KEY,
    watcher_id       TEXT NOT NULL REFERENCES departure_watchers(watcher_id)
                     ON DELETE CASCADE,
    context_event_id INTEGER NOT NULL REFERENCES context_events(id)
                     ON DELETE CASCADE,
    home_assistant_event_id TEXT,
    decision         TEXT NOT NULL,
    reason           TEXT NOT NULL DEFAULT '',
    recorded_at      TEXT NOT NULL,
    UNIQUE (watcher_id, context_event_id)
);

CREATE TABLE IF NOT EXISTS departure_watcher_evidence (
    evidence_id      TEXT PRIMARY KEY,
    watcher_id       TEXT NOT NULL REFERENCES departure_watchers(watcher_id)
                     ON DELETE CASCADE,
    context_event_id INTEGER REFERENCES context_events(id) ON DELETE SET NULL,
    evidence_kind    TEXT NOT NULL,
    classification   TEXT NOT NULL CHECK (classification IN (
                            'observed', 'inferred', 'stale', 'uncertain',
                            'contradictory'
                        )),
    source_scope     TEXT NOT NULL,
    facts_json       TEXT NOT NULL,
    recorded_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS departure_watcher_device_states (
    id               INTEGER PRIMARY KEY,
    watcher_id       TEXT NOT NULL REFERENCES departure_watchers(watcher_id)
                     ON DELETE CASCADE,
    phase            TEXT NOT NULL CHECK (phase IN ('pre_action', 'post_action', 'verification')),
    entity_id        TEXT NOT NULL,
    state_json       TEXT NOT NULL,
    classification   TEXT NOT NULL CHECK (classification IN (
                            'observed', 'inferred', 'stale', 'uncertain',
                            'contradictory'
                        )),
    observed_at      TEXT NOT NULL,
    recorded_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS departure_watcher_authorizations (
    id               INTEGER PRIMARY KEY,
    authorization_id TEXT NOT NULL,
    watcher_id       TEXT NOT NULL REFERENCES departure_watchers(watcher_id)
                     ON DELETE CASCADE,
    action_id        TEXT,
    decision         TEXT NOT NULL,
    authority        TEXT NOT NULL,
    required_json    TEXT NOT NULL DEFAULT '{}',
    scope_json       TEXT NOT NULL DEFAULT '{}',
    details_json     TEXT NOT NULL DEFAULT '{}',
    recorded_at      TEXT NOT NULL,
    UNIQUE (watcher_id, authorization_id)
);

CREATE TABLE IF NOT EXISTS departure_watcher_rollbacks (
    id               INTEGER PRIMARY KEY,
    watcher_id       TEXT NOT NULL REFERENCES departure_watchers(watcher_id)
                     ON DELETE CASCADE,
    action_id        TEXT,
    method           TEXT NOT NULL,
    prior_state_json TEXT,
    status           TEXT NOT NULL,
    details_json     TEXT NOT NULL DEFAULT '{}',
    recorded_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_departure_watchers_status_expiry
    ON departure_watchers(status, expires_at);

CREATE INDEX IF NOT EXISTS idx_departure_watcher_events_event
    ON departure_watcher_events(context_event_id, recorded_at DESC);

CREATE INDEX IF NOT EXISTS idx_departure_watcher_evidence_watcher
    ON departure_watcher_evidence(watcher_id, recorded_at DESC);

CREATE INDEX IF NOT EXISTS idx_departure_watcher_device_states_watcher
    ON departure_watcher_device_states(watcher_id, phase, recorded_at DESC);
