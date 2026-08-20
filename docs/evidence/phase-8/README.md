# Phase 8 evidence — Cognitive Executive and specialist council

Status: **accepted by Marc on 2026-08-16 (America/New_York)**.

Deferred follow-up: live Codex/production RAG validation and broader generic
executive idempotency remain outside this frozen deterministic acceptance
scenario.

Phase 5, Phase 6, and Phase 7 remain pending Marc's review and explicit
acceptance. Phase 9 has not started.

Source snapshot: `e171045f550ebdf8aaed6fcf91f9c3231e428e69` plus the existing
uncommitted Phase 5–7 work and the uncommitted Phase 8 additions in this tree.
Migration: additive Phase 8 SQLite schema version 1.

## Evidence contents

- `docs/design/2026-08-15-phase-8-cognitive-executive.md` — additive design,
  boundaries, contracts, recovery, and rollback;
- `docs/evidence/phase-8/jarvis-experience.md` — presence, continuity,
  initiative, competence, and trust assessment;
- `docs/evidence/phase-8/artifacts/rag-benchmark.json` — deterministic recorded
  benchmark result for the fixed fixture;
- `src/openjarvis/executive/` — typed contracts, additive SQLite persistence,
  bounded routing, Codex templates, executive lifecycle, deterministic RAG
  benchmark, and baseline comparison;
- `tests/acceptance/phase_8/test_cognitive_executive.py` — sixteen deterministic
  tests covering contract evidence, restart recovery, bounded context,
  missing-information escalation, cancellation and supersession, authority
  boundaries, budgets, Codex failure recovery, disagreement, RAG quality, and
  baseline comparison.

## Focused result

```text
Phase 8 acceptance lane: 16 passed
RAG fixture hit rate: 1.0
RAG fixture mean reciprocal rank: 1.0
Restart consistency: true
Model-independent path: true
Duplicate-ingest-free: true
Codex mission requests in tangible scenario: 4
Authority expansion from specialist consensus: blocked
Executive versus recorded single-agent baseline: beats baseline
Deployment or consequential external effects: 0
```

## Review audit result

The review found and repaired lifecycle and evidence-boundary defects that the
original happy-path lane did not exercise:

- canceled, failed, blocked, completed, and superseded work cannot be resumed
  or completed through the wrong lifecycle transition;
- failed Codex delegation is durably recorded and blocks its subgoal instead of
  leaving an orphaned active assignment;
- council mission, token, and intervention budgets fail closed;
- cross-goal claim and artifact links are rejected before persistence, while
  Codex prompts receive only the bounded specialist context;
- durable RAG retries reject conflicting content for the same natural identity,
  and the single-agent baseline now uses the direct legacy retriever path.

The final full acceptance regression after these repairs was 134 passed. Marc
accepted Phase 8 on 2026-08-16.

Run it with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-phase8-focused `
  tests\acceptance\phase_8
```

The deterministic scenario writes a benchmark artifact to its temporary
acceptance directory, stores its SHA-256 checksum in `VerifiedArtifact`, and
stores the final `ExecutiveDecisionReceipt` in the durable Phase 8 database.

## Exit-gate mapping

| Gate | Evidence |
|---|---|
| Goal survives restart without replay | `test_restart_recovers_without_replaying_completed_subgoal`; durable assignment and subgoal projections |
| Minimum sufficient specialist context | `test_bounded_context_excludes_transcript_and_limits_claims`; `BoundedContext.transcript_included == false` |
| Claims retain evidence and confidence | `test_phase8_contracts_round_trip_with_evidence`; constructor rejects missing evidence |
| Missing information escalates | `test_missing_information_is_escalated_and_cancellation_is_durable` |
| Guardian authority cannot grow by consensus | `test_authority_cannot_expand_from_specialist_consensus` |
| Baseline comparison passes | `test_phase8_tangible_rag_objective_and_baseline`; explicit cost/intervention limits |
| One coherent narrative | persisted verified receipt includes one `narrative` owned by Executive |

## Security and privacy impact

Phase 8 adds no new external data flow. Specialist packages exclude raw
transcripts and carry only bounded evidence summaries. The RAG scenario uses
synthetic fixture documents. Mission templates forbid deployment and
consequential external effects. Specialist agreement cannot grant a
capability, and the Guardian feedback path records the denial.

## Manual review items

- Inspect the persisted goal, subgoal, assignment, claim, artifact, event, and
  receipt rows after running the scenario.
- Review one restart where an implementation mission remains active and confirm
  only its persisted Codex mission ID is resumed.
- Review the generated benchmark JSON and compare its checksum with the
  `VerifiedArtifact` record.
- Marc accepted the measured single-agent comparison cost and one restart
  intervention on 2026-08-16; live validation remains deferred follow-up.
- Review any future live Codex and production RAG work separately; this phase
  does not authorize deployment or real-world effects.

## Rollback

Back up the database, stop the process, and remove only the additive `phase8_*`
tables from a reviewed copy if rollback is ever required. No existing Phase
0–7 tables are part of this rollback.
