# Phase 0 evidence pack

## Result

Phase 0 is implemented and technically verified. Its program status remains
`verifying` until Marc accepts it. Phase 1 has not started.

## Version and configuration

- Date: 2026-08-15
- Python: 3.12.13
- Package schema: cognition contract schema `1`
- SQLite migration: action/cognition schema `1`
- Phase flags: Phase 0 on; Phases 1-12 off unless explicitly enabled by an
  `OPHANIM_PHASE_<n>_ENABLED=1` environment variable.
- Source snapshot: pending initial Git commit; updated after snapshot creation.

## Migrations and rollback

- `CognitionStore` applies numbered migration `0001_phase0.sql` and records it
  in `schema_migrations`.
- `0001_phase0.down.sql` removes only Phase 0 tables and triggers.
- `ContractStore` adds its versioned contract tables without touching legacy
  tables.
- The rollback test restores a pre-migration SQLite fixture byte-for-byte and
  proves its legacy marker remains while the Phase 0 table is absent.
- Operational rollback: stop Ophanim, retain the current database, apply the
  down migration for a schema-only rollback or restore the reviewed backup
  fixture/copy, then run the pre-Phase-0 binary/configuration.

## Verification results

Commands are run from the repository root with workspace-local temporary paths.

| Lane | Result |
|---|---|
| Phase 0 contracts, lifecycle, acceptance, and approval routes | 114 passed |
| Phase 0 lint | passed |
| Frontend tests | 6 passed |
| Initial full Python baseline | collection blocked by absent optional `polars` dependency |
| Broad Python regression lane with that optional test excluded | 1,571 passed, 7 skipped, then stopped at 20 unrelated failures |

The 20 broad-lane failures occur in existing agent defaults, loop guard,
checkpoint retention, commute reliability, trace timing, behavior resolution,
throughput calculation, credential-less channels, memory lookup, and CLI JSON
output polluted by the update notifier. None imports or exercises Phase 0 code.
They are baseline debt, not silently classified as passing.

## Tangible demo transcript

The executable scenario is
`tests/acceptance/phase_0/test_truthful_lifecycle.py`.

1. Action A and B are proposed and authorized with distinct idempotency keys.
2. A changes the fake world once; its tool response moves it only to
   `effect_pending`.
3. B reports a timeout/failure and ends `failed`.
4. The ledger is closed and reopened before A is verified.
5. Re-delivering A's key returns the original action and the executor refuses
   to run it again; side-effect count remains exactly one.
6. An independent world read observes A's effect and moves it to `verified`.
7. Audit timelines are exactly:
   - A: proposed, authorized, executing, effect_pending, verified
   - B: proposed, authorized, executing, failed
8. Neither timeline contains `executed`; B never contains `verified`.

## Metrics against the gate

- Shared schemas round-tripped: 14/14 through JSON and SQLite.
- Illegal transition pairs rejected: all generated invalid state pairs.
- Duplicate side effects in replay: 0.
- False success states for forced failure: 0.
- Lost state over injected restart: 0.
- Transition audit coverage in the acceptance scenario: 100%.
- Immutable attempt/audit mutation checks: passed.

## Security and privacy impact

- No network, cloud, Home Assistant, Codex, or personal-memory integration was
  added.
- Sensitivity and taint labels are present on every shared contract.
- Attempts, verifications, and transition audit records are append-only at the
  database layer.
- Unknown/illegal transitions fail closed.
- A legacy `executed` row without independent evidence migrates to
  `needs_attention`, never to success.

## Known limitations and deferred work

- Phase 0 provides fake adapters only; real action registry and Guardian policy
  belong to Phase 3.
- Compensation states are modeled and validated, but compensation execution is
  deferred to Phase 3.
- Marc acceptance is intentionally not self-granted by the implementation.
- The unrelated repository baseline debt listed above remains open.

