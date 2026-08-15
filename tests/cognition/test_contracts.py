from __future__ import annotations

import sqlite3

import pytest

from openjarvis.cognition import (
    ActionAttempt,
    ActionProposal,
    Authorization,
    Belief,
    CognitionContract,
    CognitionStore,
    Commitment,
    DecisionReceipt,
    Episode,
    Goal,
    LearningCandidate,
    Observation,
    Plan,
    Prediction,
    Situation,
    UnsupportedSchemaVersion,
    Verification,
)

CONTRACTS = [
    Observation(subject="calendar", value={"event": "demo"}, confidence=0.9),
    Belief(statement="The demo is scheduled", confidence=0.8),
    Goal(objective="Finish Phase 0", success_conditions=["tests pass"]),
    Commitment(promise="Report only verified results"),
    Situation(situation_type="phase-verification", evidence_ids=["obs_1"]),
    Plan(goal_id="goal_1", action_ids=["action_1"]),
    ActionProposal(
        action_type="fake.set",
        parameters={"value": True},
        idempotency_key="contract-test",
    ),
    Authorization(action_id="action_1", authority="test"),
    ActionAttempt(action_id="action_1", tool_succeeded=True, tool_response={"ok": 1}),
    Verification(action_id="action_1", succeeded=True, observed_effect=True),
    Prediction(claim="state becomes true"),
    Episode(title="Phase 0 test", event_ids=["event_1"], outcome="passed"),
    LearningCandidate(candidate_kind="procedure", proposal={"step": "verify"}),
    DecisionReceipt(
        decision="continue", rationale="gate passed", evidence_ids=["test_1"]
    ),
]


@pytest.mark.parametrize("contract", CONTRACTS, ids=lambda item: item.contract_type)
def test_every_shared_contract_round_trips_json_and_sqlite(tmp_path, contract):
    decoded = CognitionContract.from_json(contract.to_json())
    assert decoded == contract

    store = CognitionStore(tmp_path / "contracts.db")
    store.save_contract(contract)
    store.close()

    reopened = CognitionStore(tmp_path / "contracts.db")
    assert reopened.load_contract(contract.id) == contract
    reopened.close()


def test_pre_versioned_contract_migrates():
    migrated = CognitionContract.from_dict(
        {
            "contract_type": "observation",
            "id": "obs_legacy",
            "timestamp": "2026-08-15T00:00:00+00:00",
            "subject": "legacy",
            "value": 1,
        }
    )
    assert migrated.schema_version == 1
    assert migrated.created_at == "2026-08-15T00:00:00+00:00"


def test_incompatible_contract_fails_explicitly():
    with pytest.raises(UnsupportedSchemaVersion, match="newer"):
        CognitionContract.from_dict(
            {
                "contract_type": "observation",
                "id": "future",
                "schema_version": 2,
            }
        )


def test_backup_and_rollback_restore_pre_migration_fixture(tmp_path):
    database = tmp_path / "runtime.db"
    fixture = tmp_path / "pre-migration.db"
    original = sqlite3.connect(database)
    original.execute("CREATE TABLE legacy_marker(value TEXT NOT NULL)")
    original.execute("INSERT INTO legacy_marker VALUES ('before-phase-0')")
    original.commit()
    original.close()

    fixture.write_bytes(database.read_bytes())
    store = CognitionStore(database)
    store.save_contract(Observation(subject="new", value=True))
    store.close()

    CognitionStore.restore_backup(fixture, database)
    restored = sqlite3.connect(database)
    try:
        assert (
            restored.execute("SELECT value FROM legacy_marker").fetchone()[0]
            == "before-phase-0"
        )
        assert (
            restored.execute(
                "SELECT name FROM sqlite_master WHERE name='cognition_contracts'"
            ).fetchone()
            is None
        )
    finally:
        restored.close()
