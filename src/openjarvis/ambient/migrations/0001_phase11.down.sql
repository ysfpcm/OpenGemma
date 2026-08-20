BEGIN;
DROP TRIGGER IF EXISTS phase11_audit_immutable_update;
DROP TRIGGER IF EXISTS phase11_audit_immutable_delete;
DROP TABLE IF EXISTS phase11_audit_events;
DROP TABLE IF EXISTS phase11_records;
DROP TABLE IF EXISTS phase11_schema_migrations;
COMMIT;
