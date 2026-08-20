# Phase 2 Jarvis experience

The mission-control surface gives Marc a bounded operational view of a
running Codex mission:

- the Operations panel shows the mission mode, authority boundary, budget
  state, persisted controls, and observable effects;
- steer, interrupt, resume, fork, and checkpoint are explicit mission actions;
- waiting requests show whether Codex asked, whether Guardian allows the
  request, and whether Marc has approved it;
- command, file, permission, network, and user-input decisions are rendered
  from the request's offered choices rather than from a hand-built global
  permission menu;
- completed effects, changed files, notifications, and fork relationships
  remain in the mission ledger for return-after-absence review.

The surface deliberately does not expose danger-full-access, network
enablement, arbitrary writable roots, session-wide grants, exec-policy
amendments, or hidden reasoning. A declined operation remains declined for
that request and cannot be retried through a broader interpretation.
