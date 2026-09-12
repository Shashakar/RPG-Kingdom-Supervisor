# Issue #60 Phase 1 Scope

This PR is intentionally the safety-first slice of #60. It closes the GH-108 failure mode before the richer analytics work lands.

Included now:

- a between-turn host continuation seam for the evaluated Symphony pin;
- route-aware automatic turn limits while preserving `agent.max_turns` as the hard ceiling;
- fresh model-free authoritative quota checks before continuation;
- conservative progress checks using Git/worktree and Unity evidence;
- cumulative rollout token capture plus per-turn delta when a prior sample exists;
- durable continuation decision telemetry;
- a distinct continuation-budget halt/report that preserves the workspace for reviewed rearm;
- deterministic regression coverage for Terra/Luna routing, quota gating, progress gating, the Symphony transform, and after-run reporting.

Still under #60 after this slice:

- richer dashboard rendering of per-turn history and decision evidence;
- stronger baseline-vs-current progress fingerprints for rearmed dirty workspaces;
- deeper investigation/measurement of context replay/compaction options;
- policy tuning from multiple real workloads rather than GH-108 alone.

Those remaining items should not block deploying the safety rail before the next expensive investigative worker.
