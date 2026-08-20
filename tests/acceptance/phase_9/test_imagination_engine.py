"""Phase 9 exit-gate tests.

All fixtures in this module are deterministic and side-effect-free.  In
particular, no Home Assistant, channel, traffic, calendar, deployment, or
Codex app-server connection is used.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from openjarvis.imagination import (
    BoundedCodexExperiment,
    CandidatePlan,
    CandidateStatus,
    DepartureFixture,
    DepartureSimulationSuite,
    ExpectedObservation,
    ImaginationService,
    ImaginationStore,
    PredictionStatus,
    SimulationKind,
    SimulationRun,
    SimulationStatus,
    TypedAction,
    candidate_diversity,
    departure_twin,
)
from openjarvis.imagination.models import stable_id


def _service(tmp_path: Path) -> tuple[ImaginationStore, ImaginationService]:
    store = ImaginationStore(tmp_path / "imagination.db")
    return store, ImaginationService(store)


def test_phase9_contracts_round_trip_and_simulation_cannot_claim_authority() -> None:
    fixture = DepartureFixture()
    twin = departure_twin(fixture)
    twin.apply("home_assistant.cover.position", fixture.cover_entity, {"position": 0})
    verified, state = twin.verify(
        "home_assistant.cover.position", fixture.cover_entity, {"position": 0}
    )

    assert verified is False
    assert state["state"] == "open"
    assert twin.live_effects == 0
    run = SimulationRun(
        simulation_id="simulation-contract",
        candidate_id="candidate-contract",
        kind=SimulationKind.HOME_ASSISTANT_TWIN,
        status=SimulationStatus.COMPLETED,
        simulation_outcome="failure",
        evidence_scope="simulation",
        findings=("cover feedback contradicted the simulated expectation",),
        prediction_ids=(),
    )
    restored = SimulationRun.from_dict(run.to_dict())
    assert restored.simulation_outcome == "failure"
    assert restored.real_effect is False
    assert restored.authority_created is False
    with pytest.raises(ValueError, match="real effect"):
        SimulationRun(
            simulation_id="unsafe",
            candidate_id="candidate-contract",
            kind=SimulationKind.HOME_ASSISTANT_TWIN,
            status=SimulationStatus.COMPLETED,
            simulation_outcome="success",
            evidence_scope="simulation",
            findings=(),
            prediction_ids=(),
            real_effect=True,
        )


def test_seeded_departure_deliberation_has_real_diversity_and_pre_guardian_rejection(
    tmp_path: Path,
) -> None:
    store, service = _service(tmp_path)
    decision = service.deliberate_departure(DepartureFixture())
    candidates = store.list_candidates("phase9-departure-seeded-faults")

    diversity = candidate_diversity(candidates)
    assert diversity["candidate_count"] == 3
    assert diversity["materially_different"] is True
    assert len(diversity["strategy_families"]) == 3
    assert decision.selected_candidate_id == "candidate:degraded-service-consent"
    assert decision.rejected_candidate_ids == ("candidate:full-away-automation",)
    assert "cover feedback contradiction" in decision.reason
    assert "household presence" in decision.reason
    assert "address did not prove physical attendance" in decision.reason
    assert "traffic outage" in decision.reason

    full_away = store.get_candidate("candidate:full-away-automation")
    assert full_away.status is CandidateStatus.REJECTED
    assert any(
        "cover-feedback contradiction" in item for item in full_away.rejection_reasons
    )
    assert any("alarm arming" in item for item in full_away.rejection_reasons)

    modified = store.get_candidate("candidate:presence-aware-preparation")
    assert modified.status is CandidateStatus.VALID
    assert modified.modification_notes
    assert all(
        item.action_type == "home_assistant.read_state" for item in modified.actions
    )

    degraded = store.get_candidate("candidate:degraded-service-consent")
    attendance = next(
        item
        for item in degraded.expected_observations
        if item.key == "physical_attendance"
    )
    assert attendance.expected_value == "unknown"
    assert any("traffic-fallback" in item for item in degraded.diversity_signature)
    assert len(store.list_predictions(degraded.candidate_id)) == len(
        degraded.expected_observations
    )

    simulations = [
        run
        for candidate in candidates
        for run in store.list_simulations(candidate.candidate_id)
    ]
    assert simulations
    assert all(run.evidence_scope == "simulation" for run in simulations)
    assert all(
        run.real_effect is False and run.authority_created is False
        for run in simulations
    )
    for run in simulations:
        candidate = store.get_candidate(run.candidate_id)
        assert run.prediction_ids == tuple(
            stable_id("prediction", run.candidate_id, item.observation_id)
            for item in candidate.expected_observations
        )
    assert any(
        run.kind is SimulationKind.HOME_ASSISTANT_TWIN
        and any("reported success" in finding for finding in run.findings)
        for run in simulations
    )
    assert any(
        run.kind is SimulationKind.SERVICE_DRY_RUN
        and any("fallback buffer" in finding for finding in run.findings)
        for run in simulations
    )
    store.close()


def test_only_valid_candidates_reach_guardian_and_no_authority_is_created(
    tmp_path: Path,
) -> None:
    store, service = _service(tmp_path)
    decision = service.deliberate_departure(DepartureFixture())
    handoff = service.guardian_handoff(decision.decision_id)

    assert {item.candidate_id for item in handoff} == set(decision.valid_candidate_ids)
    assert all(item.allowed for item in handoff)
    assert all(item.proposal_ids for item in handoff)
    assert all(
        item.authority_created is False and item.executed is False for item in handoff
    )
    assert not any(
        item["event"] == "guardian-authorization-granted" for item in store.audit()
    )
    assert decision.authority_created is False
    assert decision.live_effects is False
    store.close()


def test_prediction_ledger_replay_distinguishes_verified_contradicted_and_unknown(
    tmp_path: Path,
) -> None:
    store, service = _service(tmp_path)
    decision = service.deliberate_departure(DepartureFixture())
    predictions = store.list_predictions(decision.selected_candidate_id)
    by_key = {
        store.get_candidate(decision.selected_candidate_id)
        .expected_observations[index]
        .key: prediction
        for index, prediction in enumerate(predictions)
    }

    verified = service.record_replayed_observation(
        by_key["cover_state"].prediction_id,
        "open",
        source_id="replayed-home-assistant",
    )
    audit_before_duplicate = len(store.audit())
    duplicate = service.record_replayed_observation(
        by_key["cover_state"].prediction_id,
        "open",
        source_id="replayed-home-assistant",
    )
    audit_after_duplicate = len(store.audit())
    unknown = service.record_replayed_observation(
        by_key["physical_attendance"].prediction_id,
        "unknown",
        source_id="replayed-calendar",
    )
    contradicted = service.record_replayed_observation(
        by_key["departure_buffer_minutes"].prediction_id,
        42,
        source_id="replayed-traffic",
    )

    assert verified.result is PredictionStatus.VERIFIED
    assert duplicate.calibration_id == verified.calibration_id
    assert len(store.list_calibrations(by_key["cover_state"].prediction_id)) == 1
    assert audit_after_duplicate == audit_before_duplicate
    assert unknown.result is PredictionStatus.UNKNOWN
    assert contradicted.result is PredictionStatus.CONTRADICTED
    assert (
        store.get_prediction(by_key["cover_state"].prediction_id).status
        is PredictionStatus.VERIFIED
    )
    assert (
        store.list_calibrations(by_key["departure_buffer_minutes"].prediction_id)[
            0
        ].calibration_error
        == 1.0
    )
    store.close()


def test_restart_duplicate_and_interrupted_simulation_recovery(tmp_path: Path) -> None:
    db = tmp_path / "imagination.db"
    store = ImaginationStore(db)
    service = ImaginationService(store)
    decision = service.deliberate_departure(DepartureFixture())
    candidate = store.get_candidate(decision.selected_candidate_id)
    assert store.save_candidate(candidate) is False
    count_before = len(store.list_candidates())
    store.close()

    reopened = ImaginationStore(db)
    assert len(reopened.list_candidates()) == count_before
    running = SimulationRun(
        simulation_id=stable_id("sim", candidate.candidate_id, "restart-running"),
        candidate_id=candidate.candidate_id,
        kind=SimulationKind.SYMBOLIC,
        status=SimulationStatus.RUNNING,
        simulation_outcome="unknown",
        evidence_scope="simulation",
        findings=(),
        prediction_ids=(),
    )
    assert reopened.save_simulation(running) is True
    assert reopened.recover_interrupted() == 1
    recovered = [
        item
        for item in reopened.list_simulations(candidate.candidate_id)
        if item.simulation_id == running.simulation_id
    ][0]
    assert recovered.status is SimulationStatus.FAILED
    assert recovered.simulation_outcome == "unknown"
    assert "real-world outcome unknown" in (recovered.failure_summary or "")
    reopened.close()


def test_migration_backup_and_phase9_only_rollback(tmp_path: Path) -> None:
    db = tmp_path / "imagination.db"
    store = ImaginationStore(db)
    store._conn.execute("CREATE TABLE unrelated_application_state (value TEXT)")
    store._conn.execute(
        "INSERT INTO unrelated_application_state(value) VALUES ('preserve me')"
    )
    backup = store.backup(tmp_path / "imagination.backup.db")
    assert backup.exists()
    assert store.migration_version == 1
    store.rollback()
    assert (
        store._conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'phase9_candidates'"
        ).fetchone()
        is None
    )
    assert (
        store._conn.execute("SELECT value FROM unrelated_application_state").fetchone()[
            0
        ]
        == "preserve me"
    )
    store.migrate()
    assert store.migration_version == 1
    store.close()


def test_isolated_codex_failure_is_bounded_and_primary_unchanged(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    primary.mkdir()
    (primary / "README.md").write_text("primary baseline\n", encoding="utf-8")
    (primary / "test_fixture.py").write_text(
        "def test_seeded(): pass\n", encoding="utf-8"
    )
    experiment_root = tmp_path / "experiments"

    def digest(path: Path) -> str:
        return hashlib.sha256(
            b"".join(
                item.read_bytes() for item in sorted(path.rglob("*")) if item.is_file()
            )
        ).hexdigest()

    before = digest(primary)
    run = BoundedCodexExperiment(primary, experiment_root).run(
        candidate_id="candidate:codex-implementation", seeded_failure=True
    )
    after = digest(primary)
    experiment = Path(run.workspace_path or "")

    assert before == after
    assert experiment.is_relative_to(experiment_root.resolve())
    assert experiment != primary.resolve()
    assert (
        (experiment / "phase9-seeded-candidate.txt")
        .read_text(encoding="utf-8")
        .startswith("seeded test failure")
    )
    assert run.simulation_outcome == "failure"
    assert run.evidence_scope == "simulation"
    assert run.real_effect is False
    assert run.authority_created is False
    keep = experiment / "keep-me.txt"
    keep.write_text("do not delete\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        BoundedCodexExperiment(primary, experiment_root).run(
            candidate_id="candidate:codex-implementation", seeded_failure=True
        )
    assert keep.read_text(encoding="utf-8") == "do not delete\n"
    with pytest.raises(ValueError, match="outside"):
        BoundedCodexExperiment(primary, primary / "nested")


def test_symbolic_simulation_rejects_dependency_cycles(tmp_path: Path) -> None:
    candidate_id = "candidate:dependency-cycle"
    observation = ExpectedObservation(
        observation_id="observation:dependency-cycle",
        candidate_id=candidate_id,
        key="cycle",
        description="The cycle must not be schedulable.",
        expected_value=True,
        observable_by="test",
        confidence=1.0,
        provenance={"source": "test"},
    )
    first = TypedAction(
        action_id="action:first",
        candidate_id=candidate_id,
        action_type="test.first",
        target="test",
        parameters={},
        capability="test",
        consequence_class="none",
        reversible=True,
        expected_observation_ids=(observation.observation_id,),
        dependencies=("action:second",),
    )
    second = TypedAction(
        action_id="action:second",
        candidate_id=candidate_id,
        action_type="test.second",
        target="test",
        parameters={},
        capability="test",
        consequence_class="none",
        reversible=True,
        expected_observation_ids=(observation.observation_id,),
        dependencies=("action:first",),
    )
    candidate = CandidatePlan(
        candidate_id=candidate_id,
        scenario_id="scenario:dependency-cycle",
        strategy_family="cycle",
        title="Cyclic candidate",
        rationale="test",
        actions=(first, second),
        expected_observations=(observation,),
        assumptions=(),
        diversity_signature=("cycle", "dependency"),
    )

    run = DepartureSimulationSuite(DepartureFixture()).run_symbolic(candidate)

    assert run.simulation_outcome == "failure"
    assert "dependency graph contains a cycle" in run.findings


def test_replay_cannot_be_promoted_to_real_observation_scope(tmp_path: Path) -> None:
    store, service = _service(tmp_path)
    decision = service.deliberate_departure(DepartureFixture())
    prediction_id = store.list_predictions(decision.selected_candidate_id)[
        0
    ].prediction_id

    with pytest.raises(ValueError, match="only accepts replay"):
        service.record_replayed_observation(
            prediction_id,
            "open",
            source_id="untrusted-source",
            evidence_scope="real_observation",
        )
    with pytest.raises(ValueError, match="non-empty source ID"):
        service.record_replayed_observation(prediction_id, "open", source_id="")
    assert store.list_calibrations(prediction_id) == []
    store.close()
