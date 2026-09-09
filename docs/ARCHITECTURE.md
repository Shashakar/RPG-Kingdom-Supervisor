# RPG Kingdom Supervisor Architecture

## Purpose

RPG Kingdom Supervisor removes manual Codex session management from RPG Kingdom development while preserving human architectural control, usage discipline, independent review, and explicit ownership of scarce host resources.

The supervisor is an orchestration system, not a gameplay system. It consumes approved GitHub issues, prepares isolated workspaces, runs Codex through Symphony, optionally validates through a host-owned Unity runner, and hands completed work back as pull requests.

## System boundaries

### ChatGPT / human planning

Architecture, prioritization, research, issue definition, risk classification, model overrides, Unity validation requirements, and independent PR review remain outside the autonomous execution loop.

The supervisor does not decide the RPG Kingdom roadmap.

### GitHub

`Shashakar/RPG-Kingdom` is the system of record for work and code.

GitHub Issues carry implementation scope. A `symphony:ready` label explicitly opts an issue into autonomous execution. Risk/model/effort labels communicate model policy. Resource/validation labels communicate whether a dispatch requires exclusive Unity access and whether missing Unity validation blocks completion.

GitHub pull requests are the handoff boundary back to human review. The supervisor never auto-merges.

### Symphony

Official `openai/symphony` provides tracker polling, required-label filtering, per-issue workspace lifecycle, Codex App Server lifecycle, turn continuation/retry behavior, tracker-native tools, and runtime observability.

This repository adds RPG Kingdom-specific workflow configuration plus narrow host adapters for model routing, fail-closed execution budgeting, Unity resource ownership, and Unity test execution.

### Codex

Codex operates in the workspace created for the current issue. Once RPG Kingdom is cloned, its own `AGENTS.md`, architecture docs, and system contracts remain authoritative.

Codex is responsible for implementation, relevant tests, required docs, branch publication, and PR creation. A worker may invoke the supervisor's supported Unity runner when its issue owns `resource:unity-editor`; it may not modify the supervisor or invent another Windows/Unity bridge.

### Unity

Unity is a host-owned validation resource, not part of Codex's general filesystem/tool authority.

Phase 3 defines scheduling:

- `resource:unity-editor` reserves exclusive editor ownership;
- `validation:unity-required` requires the resource and blocks before Codex when the host runner is unhealthy;
- `validation:unity-optional` allows implementation without editor evidence;
- an ownership-recorded lock is held for the worker lifetime.

Phase 4 defines execution:

- `scripts/unity-runner.sh` is the only supported worker entrypoint;
- the project-declared Unity version is resolved from `ProjectSettings/ProjectVersion.txt`;
- Windows PowerShell/robocopy mirrors `Assets`, `Packages`, and `ProjectSettings` from the WSL workspace into a persistent NTFS staging project;
- the stage keeps its `Library/` cache across issues;
- Unity Test Framework runs EditMode/PlayMode tests, optionally with a narrow filter;
- result XML, Editor logs, and JSON summaries return to the ignored `Logs/SymphonyUnity/` workspace path.

The staging project is disposable validation state. It never becomes a source of truth and is never synchronized back into RPG Kingdom.

## Current flow

```text
Human + ChatGPT
      |
      | issue + risk/model + Unity policy
      v
GitHub issue + symphony:ready
      |
      v
Official Symphony
      |
      | isolated GH-N workspace
      v
clone Shashakar/RPG-Kingdom
      |
      v
worker-lifetime budget guard
      |
      v
Unity resource preflight
      |
      | no resource -> continue
      | requested + health failure -> halt before Codex
      | requested + healthy -> acquire unity-editor lock
      v
Codex model router
      |
      v
Codex App Server
      |
      | inspect + implement
      | if Unity owned: unity-runner.sh editmode/playmode
      | push + PR
      | remove symphony:ready
      v
release Unity lock if owned
      |
      v
host after-run budget guard
      |
      v
GitHub PR -> Human + ChatGPT review
```

If a worker attempt ends while `symphony:ready` remains, the after-run budget guard revokes the lease, adds `symphony:halted`, and requires explicit rearm before another worker lifetime.

## Dispatch contract

- issue must be open and carry `symphony:ready`;
- maximum concurrent agents is currently `1`;
- maximum turns per worker lifetime is `4`;
- risk/model/effort and Unity labels must be internally consistent;
- known Unity host failures halt before Codex when editor access was requested;
- `validation:unity-required` requires actual relevant Unity runner evidence before clean handoff;
- the worker keeps `symphony:ready` while implementation is genuinely active;
- a reviewable PR must exist before the worker removes `symphony:ready`;
- no automatic merge or automatic fresh-session retry is permitted.

## Model/risk routing

The supervisor uses deterministic labels instead of spending an LLM call merely to classify work.

Default routes:

- `risk:mechanical` -> GPT-5.6 Luna / low;
- `risk:normal` -> GPT-5.6 Luna / medium;
- `risk:investigative` -> GPT-5.6 Terra / medium;
- `risk:architecture` -> GPT-5.6 Sol / high;
- `risk:end-to-end` -> GPT-6 Astra / medium;
- no risk/model label -> GPT-5.6 Luna / medium.

Explicit `model:*` and `effort:*` labels override defaults. Conflicts fail closed.

Luna is the default workhorse. Terra is an upgrade for ambiguous/multi-layer investigation. Sol is the architecture-sensitive reasoning tier. Astra is reserved for the hardest end-to-end/tool-heavy execution where its higher allowance cost is plausibly offset by fewer iterations.

The split is evidence-driven: #93 completed as a one-turn Luna mechanical job with no visible allowance movement, while #95 completed as a one-turn Terra investigation but consumed 8 percentage points of the five-hour allowance and 2 weekly points despite about 93% cache reuse.

## Unity resource and runner policy

Scheduling and execution remain separate concepts:

- `resource:unity-editor` reserves a scarce host capability;
- `validation:unity-required` makes Unity evidence a completion requirement;
- `validation:unity-optional` allows missing editor evidence to be reported instead of blocking work.

`validation:unity-required` requires `resource:unity-editor`; required/optional labels are mutually exclusive.

The lock lives under the supervisor operator state directory, outside the game workspace. Test execution verifies the lock owner matches the current `GH-<number>` workspace.

The supported runner performs a live health check before lock acquisition. It no longer relies on the Phase 3 placeholder `RPGK_UNITY_RUNNER_READY=1` assertion.

The runner deliberately keeps Unity on Windows NTFS. PowerShell/robocopy mirrors only Unity project inputs into `%LOCALAPPDATA%\RPGKingdomSupervisor\UnityStages\<version>\RPG-Kingdom`; Unity-generated `Library/` state remains local and reusable. Validation artifacts are copied back only under RPG Kingdom's ignored `Logs/` tree.

Workers may not bypass this boundary with ad-hoc `Unity.exe`, PowerShell, or cmd invocations.

## Context budget

RPG Kingdom's `AGENTS.md` always wins. Within its required reads:

- mechanical work should not inventory unrelated systems;
- normal work should stay near affected implementation/tests;
- investigative work may expand only far enough to distinguish plausible root causes;
- architecture work may expand to affected cross-system contracts;
- end-to-end work may build broader context when required for actual verification.

The goal is to reduce context processing, not weaken architecture discipline.

## Execution budget boundary

Upstream Symphony's `agent.max_turns` is a worker-lifetime ceiling, not a total issue budget. Phase 2 therefore uses `symphony:ready` plus a persistent workspace marker to prevent accidental fresh-session redispatch.

Phase 3 adds a pre-Codex resource gate. Phase 4 makes that gate evidence-based by calling the real Unity runner health check before acquiring the editor lock.

These controls are host-side. The narrow Symphony tracker credential is not intentionally injected into Codex.

## Authentication boundary

Symphony's tracker PAT is stored in `SYMPHONY_GITHUB_TOKEN` and remains separate from normal `gh`/Git credentials used to clone, push, and create PRs.

The Windows Unity bridge uses no GitHub credential. It receives only project paths, Unity version/path, test platform/filter, and staging/output paths.

## Upstream strategy

The default remains configuration over forking official Symphony.

Current RPG Kingdom-specific behavior uses existing seams:

1. `codex.command` -> model router;
2. `hooks.before_run` -> worker-lifetime and Unity resource/health guards;
3. worker tool execution -> supported Unity runner;
4. `hooks.after_run` -> Unity release and redispatch guard.

A local Symphony patch is justified only when a required behavior cannot be expressed safely through configuration, prompts, hooks, command adapters, or the supported host runner.

## Planned phases

### Phase 1 — smoke path — COMPLETE

Proved GitHub issue -> isolated workspace -> Codex App Server -> PR.

### Phase 2 — budgeted model/risk routing — COMPLETE

Added Luna/Terra/Sol/Astra routing, context budgeting, turn limits, and fail-closed redispatch.

### Phase 3 — Unity-aware scheduling — COMPLETE

Added explicit Unity resource/validation labels, live pre-Codex resource ownership rules, and the exclusive editor lock.

### Phase 4 — Unity worker integration — CURRENT

Provide a supported WSL-to-Windows runner, persistent NTFS staging, EditMode/PlayMode execution, filtered tests, and observable result artifacts.

### Phase 5 — controlled concurrency

Increase code-only concurrency after measuring workspace, Git, CI, model allowance, and Unity-lock behavior.

### Phase 6 — review/rework automation

Allow actionable PR feedback to return work to Symphony without bypassing human merge authority.

## Non-goals

The supervisor does not:

- own RPG Kingdom architecture;
- replace GitHub Issues or PRs;
- merge autonomous changes;
- grant production-scene edit authority merely because Unity is available;
- maximize token consumption or agent count;
- silently escalate work to expensive models;
- treat a staged Unity project as authoritative;
- hide failed validation, exhausted budgets, missing host infrastructure, or unresolved blockers.
