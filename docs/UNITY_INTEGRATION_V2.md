# Unity Integration V2 delivery plan

**Tracking:** [Supervisor #258](https://github.com/Shashakar/RPG-Kingdom-Supervisor/issues/258). This is a targeted refactor, not a rewrite. Prioritize reliable, fast, token-efficient Unity content authoring over per-edit restrictions. Preserve branch/workspace isolation, exclusive shared Unity process ownership, human PR merge and bounded model usage.

## Baseline and protected work
The installed WSL Supervisor runs `unity-real-worker` at `4c5ede1` (reported by operator), not clean `main`. The patched broker from PR #255 was rolled back and the managed service remains active. PR #255's lease mismatch rejection (status `WorkerLeaseMismatch`, code 91) is incompatible with the installed branch's `verify_worker_ownership` test expecting `WorkerLeaseRejected`, code 75, and `requiresWorkerLease` semantics. Unity health works; a complete host authoring and Windows-to-WSL progress acceptance run is still unproven. Preserve GH-264 terrain workspace, GH-260, `unity-real-worker`, and their artifacts. Do not deploy #255 unchanged or rearm anything as a side effect.

## Target model
```text
GitHub issue -> Symphony budgeted worker -> isolated game branch
              -> unified host-owned Unity job contract
              -> Windows Unity Editor APIs (author/test/capture)
              -> verified scoped asset+meta copyback
              -> existing Git handoff -> draft PR -> human merge
```
Use one versioned job/request identity and authoritative host worker lease. Preflight is deliberately unclaimed. Worker-owned jobs must match the unique live lease. Durable monotonic progress; idempotent admission and terminal receipts; bounded deterministic retry; no model turns wasted polling host infrastructure. Strictly separate branch-local game content from shared host processes, other workers, credentials and main.

## Phase 0 — contract audit and migration design
Inventory `unity-runner.sh`, `unity-author.sh`, both Unity brokers, resource guard, host scripts, Windows PowerShell staging/authoring/capture/test code, Git handoff, worker telemetry, diagnostics and game-side `AGENTS.md`. Map every field, error code, ownership source, lease/fallback, status transition, process and file-write scope. Compare installed `unity-real-worker` to remote `main` and PR #255; distinguish branch-only tests from deployed functionality. Decide a single schema and status/error taxonomy with compatibility adapters; specify rollback, staged migration and independent fixture tests. **Gate:** reviewed state diagram, file ownership matrix, contract tests for missing/wrong/dead/ambiguous lease and preflight, no deployment before green compatibility matrix.

## Phase 1 — unified execution lifecycle
Consolidate validation and authoring job admission to one lease validator and job state machine; retain effective Windows Unity engine and host staging rather than replacing proven pieces. Make request identity, cancellation, progress snapshots, restart reconciliation and completion idempotent. Never terminate unrelated Unity PIDs; ambiguous processes yield structured operator action rather than infinite HostBusy/model polling. **Gate:** deterministic fault-injection tests for lost/duplicate/out-of-order requests, crashed/restarted broker, expired worker PID, partial publication, double completion, cancellation and WSL UNC semantics; preserve existing client behavior until migration is complete.

## Phase 2 — general branch-local Editor authoring
Permit Codex to create Editor scripts and use Unity APIs for scenes, terrain, prefabs, layers, textures, NavMesh and captures within an isolated task-owned project. Retire overly narrow per-content-operation allowlists only after updating the **game repository's own AGENTS.md and architecture policy** through a separately reviewed game PR. No uncontrolled scene YAML editing; avoid arbitrary host command execution. Restrict shared staging and directory escape, credentials, unrelated deletion, Packages/ProjectSettings, and main. Copy back source assets/meta through transactional, reviewed provenance and rollback; never copy generated cache. **Gate:** fixture produces and validates scene + heightfield + painted terrain + NavMesh + prefab while unrelated workspaces stay byte-identical.

## Phase 3 — actual integrated host acceptance
Run an unattended WSL/Windows harness through real Editor import, author, copyback, scoped EditMode/PlayMode, image capture and draft PR handoff. Include injected Unity hang/crash, PowerShell/UNC failure, service restart, stale PID, conflicting workers and handoff interruption. Record real job IDs, Editor.log, summary, artifact hashes, stage timings and token consumption. Target **>=19 of 20** repeated clean fixture executions, zero cross-workspace writes, zero unnecessary model turns for deterministic infrastructure retries. Record P50/P95 staging/import/author/validation/copyback times; targets are acceptance criteria, not existing measured performance. **Gate:** signed-off evidence on the installed host, not CI alone.

## Phase 4 — resume preserved terrain task
Read-only inventory GH-264 branch and diff before any resumption. Reconcile against game issue's latest approved scope; preserve GH-260. Verify southwest appendage, PlaytestSettlementGrass terrain layer, local joining path, NavMesh samples at `(-45,4,-35.5)` and `(-50,3,-35.5)`, collision, visual before/after, focused real tests. Only then explicitly rearm GH-264, finish through existing handoff and deliver draft PR; no automatic merge.

## Delivery controls
Each phase gets an implementation issue, dedicated `codex/*` branch, PR, tests and a host rollback recipe. Proceed in order, testing the deployed `unity-real-worker` contract as well as `main`. Keep existing 4-turn budget and quota routing until measured data justifies adjustment. Resolve flaky ambiguous-recovery CI with reproducible stress/fault tests, not reruns as proof of correctness. ChatGPT MCP #256 remains a separate, deferred access feature—not a prerequisite for Unity V2. Do not start a new Supervisor instance.

## Definition of done
An approved feature issue autonomously produces isolated Unity edits, meaningful real tests, screenshots and a reviewable draft PR. Nominal runs require no manual broker repair; recovery never corrupts another workspace. Infrastructure retry cost, model usage, and slow stages are visible and actionable.
