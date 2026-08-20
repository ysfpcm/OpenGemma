# Phase 8 — Cognitive Executive and specialist council

Status: implementation slice accepted by Marc on 2026-08-16
(America/New_York). Deferred follow-up: live Codex/production RAG validation
and broader generic executive idempotency remain outside this frozen
deterministic acceptance scenario. Phase 5, Phase 6, and Phase 7 remain
pending Marc's review. Phase 9 has not started.

## Boundary

Phase 8 adds a durable executive above the existing cognition, Codex observer,
planning, and Guardian boundaries:

```text
Marc objective
     |
     v
ExecutiveStore ---- compact GlobalWorkspace
     |                         |
 durable goals, claims,        +--> bounded specialist context
 commitments, artifacts,       |
 councils, events, receipts    +--> Executive synthesis
     |
     +--> CodexMissionBoundary --> existing Codex thread/turn ledger
     |
     +--> Guardian boundary: no specialist or consensus authority growth
```

The Executive owns one synthesis. Specialists return typed claims and
artifacts. Guardian remains the only authority boundary for consequential
actions; this phase's RAG objective has deployment and external-effect
boundaries set to false.

## Durable contracts and state

`src/openjarvis/executive/models.py` defines versioned contracts with the
shared cognition causal envelope:

- `ExecutiveGoal` represents root goals and subgoals with success conditions,
  priority, owner, deadline, dependencies, and explicit lifecycle state.
- `ExecutiveCommitment` records a promise Ophanim made to Marc and its due and
  success conditions.
- `GlobalWorkspace` is compact by construction: focus, bounded observations and
  beliefs, constraints, unresolved questions, candidate plans, claim IDs, and
  Guardian feedback.
- `SpecialistClaim` requires confidence and evidence. It can retain supporting
  or contradicting claim IDs, artifact IDs, and missing-information questions.
- `SpecialistCouncil` and `SpecialistAssignment` identify the synthesis owner,
  role, budget, bounded capability vocabulary, and optional Codex mission ID.
- `VerifiedArtifact` records path, checksum, verification method, and evidence.
- `ExecutiveDecisionReceipt` records the decision, rationale, claims,
  disagreement resolution, verified artifacts, baseline comparison, outcome,
  and one coherent Ophanim narrative.

`src/openjarvis/executive/store.py` uses additive `phase8_` SQLite tables and
an append-only event table. It shares a database safely with existing
subsystems but does not rewrite their tables. Recovery returns completed
subgoals separately from pending assignments, so completed, failed, blocked,
and canceled work is not replayed after a process restart. Council mission,
token, and intervention budgets are enforced before new work or recovery is
allowed.

Lifecycle semantics are explicit:

```text
goal: active -> paused -> active
goal: active/paused -> canceled | superseded | completed
assignment: pending -> running -> completed | failed | canceled
assignment: running/pending -> paused -> running
commitment: active -> paused -> active | canceled | superseded | completed
```

Cancellation is durable and prevents pending work from being resumed. A goal
cannot complete while a subgoal remains incomplete.

## Specialist routing and Codex missions

`SpecialistRouter` creates `BoundedContext` packages. Packages include only the
goal summary, success conditions, current focus, a small recent evidence slice,
constraints, unresolved questions, relevant claim summaries, Guardian
feedback, budget, and allow-listed capabilities. Raw transcripts are never
included; when a Codex mission is started, only this bounded package is
serialized into its prompt. The initial council has eight typed roles: personal context/memory,
planning/scheduling, software/Codex, home systems, security/privacy, evidence
critic, plan/failure-mode critic, and communication.

`missions.py` supplies seven templates: repository investigation, feature
implementation, code review, regression diagnosis/repair, R&D experiment,
Windows service diagnosis, and security review. Each template fixes mode,
success evidence, budgets, and forbidden deployment/external effects. When a
Codex supervisor is supplied, the Executive calls its existing
`start_mission` and `resume_observation` boundary and persists the returned
mission ID. The Executive never impersonates a Codex approval or bypasses the
Guardian.

## Tangible RAG objective

`DurableRagIndex` makes the existing persistent `KnowledgeStore` and
`TwoStageRetriever` boundary explicit. It uses no reranker, embedding model,
cloud provider, or model-specific prompt. A fixed corpus is ingested twice
using stable source identity and chunk index. `DeterministicRagBenchmark` then
measures fixed-query hit rate and mean reciprocal rank, reopens the SQLite
projection, and runs the queries again.

`run_rag_phase8_scenario` advances this bounded objective. Its comparison
baseline uses the existing `KnowledgeStore` plus `TwoStageRetriever` directly;
it does not reuse the executive durability wrapper:

1. Clarify four success conditions and create a commitment.
2. Form a typed council and delegate repository research, implementation,
   benchmark, and security review through four bounded Codex mission requests.
3. Complete research, pause the goal, close the executive, reopen it, and
   resume only the still-pending mission mappings.
4. Produce and checksum-verify a benchmark artifact.
5. Retain specialist claims, reconcile an implementation/evidence disagreement,
   and escalate/record a Guardian authority-expansion denial.
6. Compare against the existing single-agent two-stage retrieval path and issue
   one verified decision receipt.

The scenario uses deterministic fixtures only and does not deploy, publish,
send a notification, call Home Assistant, or create a consequential action.

## Verification and rollback

Focused lane:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp .tmp-phase8-focused `
  tests\acceptance\phase_8
```

Quality lane:

```powershell
.\.venv\Scripts\ruff.exe check src\openjarvis\executive tests\acceptance\phase_8
.\.venv\Scripts\ruff.exe format --check src\openjarvis\executive tests\acceptance\phase_8
.\.venv\Scripts\python.exe -m compileall -q src\openjarvis\executive
```

The Phase 8 migration is additive. To roll it back, stop the service, copy the
SQLite file, and remove only the `phase8_*` tables using a reviewed backup
operation. Existing cognition, planning, Guardian, Codex, Phase 7, and user
tables are outside the Phase 8 namespace and must be preserved.

## Deliberate limitations

- The Codex adapter is optional in the library; the acceptance lane uses a
  deterministic fixture boundary rather than a live Codex process.
- The executive records and resumes Codex missions but does not autonomously
  answer Codex approval requests.
- The RAG artifact is a reproducible quality proof for the named fixture, not
  a claim about all personal data or every retrieval backend.
- No frontend surface or automatic background scheduler is added in Phase 8.
- Marc accepted the deterministic Phase 8 slice on 2026-08-16; the live
  Codex and production RAG limitations above remain deferred follow-up.
