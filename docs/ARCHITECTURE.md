# RPG Kingdom Supervisor Architecture

## Purpose

RPG Kingdom Supervisor removes manual Codex session management from RPG Kingdom development while preserving human architectural control, usage discipline, and independent review.

The supervisor is an orchestration system, not a gameplay system. It consumes approved GitHub issues, prepares isolated workspaces, runs Codex through Symphony, and hands completed work back as pull requests.

## System boundaries

### ChatGPT / human planning

Architecture, prioritization, research, issue definition, risk classification, model overrides, Unity validation requirements, and independent PR review remain outside the autonomous execution loop.

The supervisor does not decide the RPG Kingdom roadmap.

### GitHub

`Shashakar/RPG-Kingdom` is the system of record for work and code.

GitHub Issues carry implementation scope. A `symphony:ready` label explicitly opts an issue into autonomous execution. Risk/model/effort labels communicate execution policy. Phase 3 resource/validation labels communicate whether a dispatch requires exclusive Unity access and whether missing Unity validation blocks execution.

GitHub pull requests are the handoff boundary back to human review. The supervisor never auto-merges.

### Symphony

Official `openai/symphony` provides:

- tracker polling;
- required-label filtering;
- per-issue workspace lifecycle;
- Codex App Server lifecycle;
- turn continuation and retry behavior;
- tracker-native tools exposed to Codex while keeping tracker credentials host-side;
- runtime observability.

This repository provides RPG Kingdom-specific workflow configuration plus narrow host-side adapters for model routing, fail-closed execution budgeting, and Unity resource scheduling.

### Codex

Codex operates only inside the workspace created for the current issue. Once the RPG Kingdom repository is cloned, its own `AGENTS.md`, `README.md`, architecture docs, and system docs are authoritative.

Codex is responsible for implementation, relevant tests, required documentation updates, branch publication, and PR creation when the RPG Kingdom repository instructions require them.

Phase 2 starts Codex App Server through `scripts/codex-app-server-router.sh`, which selects an explicit model/reasoning profile from GitHub labels before the thread starts.

### Unity

Unity is now an explicitly scheduled host resource, but it is not yet an integrated worker.

Phase 3 defines:

- `resource:unity-editor` for exclusive editor ownership;
- `validation:unity-required` for work that must not start unless the supported Unity runner is healthy;
- `validation:unity-optional` for work that may proceed while explicitly reporting missing editor validation;
- a host-side exclusive lock held for the worker lifetime.

Phase 3 does **not** invoke the Windows Unity Editor. `RPGK_UNITY_RUNNER_READY=1` remains a host-side health assertion that should stay unset until Phase 4 provides and validates the real Windows runner bridge.

## Current flow

```text
Human + ChatGPT
      |
      | define issue + risk/model + Unity policy when useful
      v
GitHub issue + symphony:ready
      |
      v
Official Symphony
      |
      | create isolated workspace
      v
clone Shashakar/RPG-Kingdom
      |
      v
worker-lifetime budget guard
      |
      v
Unity resource preflight
      |
      | no Unity resource -> continue
      | Unity required but unavailable -> halt before Codex
      | Unity resource available -> acquire exclusive lock
      v
Codex router
      |
      | labels -> Luna / Terra / Sol / Astra + effort
      v
Codex App Server
      |
      | read RPG Kingdom instructions
      | create codex/* branch
      | implement + validate available paths
      | push + open PR
      | remove symphony:ready
      v
release Unity lock if owned
      |
      v
host after-run budget guard
      |
      v
GitHub PR
      |
      v
Human + ChatGPT review

If worker attempt ends while symphony:ready remains:
      |
      v
host after-run budget guard
      |
      | remove symphony:ready
      | add symphony:halted
      v
human/ChatGPT inspection before any retry
```

## Dispatch contract

The current contract is deliberately small:

- issue must be open;
- issue must have `symphony:ready`;
- maximum concurrent agents is `1`;
- maximum turns per worker attempt is `4`;
- Unity validation/resource labels must be internally consistent;
- required Unity work cannot enter Codex unless the host Unity runner is healthy and the exclusive resource is acquired;
- the worker keeps `symphony:ready` while implementation is genuinely active;
- when a PR is ready for human review, the worker removes `symphony:ready` as its final orchestration-label mutation;
- if the worker attempt ends with that lease still present, the host guard removes it and adds `symphony:halted` so another fresh Codex thread cannot start automatically;
- the issue remains open until the normal RPG Kingdom review/merge process decides its final outcome.

## Model/risk routing

The supervisor uses deterministic labels rather than asking a separate LLM to classify every issue. This avoids spending Codex allowance merely to decide which Codex model should run.

Default risk routes:

- `risk:mechanical` -> GPT-5.6 Luna / low reasoning;
- `risk:normal` -> GPT-5.6 Luna / medium reasoning;
- `risk:investigative` -> GPT-5.6 Terra / medium reasoning;
- `risk:architecture` -> GPT-5.6 Sol / high reasoning;
- `risk:end-to-end` -> GPT-6 Astra / medium reasoning;
- no risk/model label -> GPT-5.6 Luna / medium reasoning.

Explicit `model:*` labels override risk routing, and explicit `effort:*` labels override reasoning effort. Conflicting labels fail closed.

Luna is the default workhorse for bounded implementation. Terra is an explicit upgrade for ambiguous debugging, multi-layer investigation, or substantial implementation where a cheaper first attempt is likely to waste iterations. Sol remains the architecture-sensitive default when the job is primarily reasoning over code and contracts. Astra is intentionally a fourth tier rather than a replacement for Sol and is reserved for the hardest end-to-end tasks where stronger implementation/tool-use/verification behavior can plausibly save iterations.

This split is evidence-driven: the #93 Luna mechanical benchmark completed in one turn with no visible allowance movement, while the #95 Terra investigative benchmark also completed in one turn but consumed 8 percentage points of the five-hour allowance and 2 points of the weekly allowance. The Terra session still cached about 93% of input, so the higher cost was not a cache-failure signal.

## Unity resource/validation policy

Unity scheduling uses separate resource and validation concepts:

- `resource:unity-editor` reserves a scarce host capability;
- `validation:unity-required` says absence of that capability blocks execution;
- `validation:unity-optional` says implementation may proceed but missing Unity evidence must be explicit.

`validation:unity-required` requires `resource:unity-editor`. Required and optional validation labels are mutually exclusive. Invalid combinations halt before Codex.

The exclusive lock is stored outside the game workspace under the supervisor operator state directory. It records its owning issue/workspace and is released only by the owning issue's after-run hook. With one concurrent agent this mainly proves the ownership contract. Later code-only concurrency can increase without allowing multiple workers to share Unity.

Phase 3 deliberately separates scheduling from execution. Workers may not launch Unity through arbitrary WSL/Windows commands or mark the runner healthy themselves. Phase 4 will provide the one supported Unity runner interface and health check.

## Context budget

RPG Kingdom's own `AGENTS.md` always wins. The supervisor does not skip repository-required architecture/system documentation to save tokens.

Within those requirements, workers are told to scale exploration to the task:

- mechanical work should not inventory unrelated systems;
- normal work should stay within affected system implementation/tests and required docs and prefer a focused implementation path;
- investigative work may expand across the affected runtime/test/system layers only far enough to distinguish plausible root causes;
- architecture work may expand to affected cross-system contracts;
- end-to-end work may build broader context when necessary for actual verification.

This is meant to reduce repeated context processing, not weaken architecture discipline.

## Execution budget boundary

Phase 1 demonstrated that upstream Symphony's `agent.max_turns` is a per-worker-lifetime ceiling, not a total issue budget. If an issue remains open and routable after that ceiling, the orchestrator can dispatch a new worker lifetime.

Phase 2 therefore treats `symphony:ready` as both dispatch authorization and the hard redispatch lease. The `after_run` guard revokes that lease when an attempt ends without a clean handoff.

Phase 3 adds a second fail-closed gate before Codex for known host-resource problems. Required Unity work that cannot be validated does not get to consume a worker turn merely to discover that the host runner is unavailable.

The guards are intentionally host-side and use the narrow Symphony tracker credential where tracker mutation is required. Codex does not receive that secret.

## Authentication boundary

Symphony owns tracker authentication on the host side and exposes its provider-native `github_api` tool to Codex. The tracker token is stored in `SYMPHONY_GITHUB_TOKEN` and is scrubbed from the Codex child environment by the evaluated upstream revision.

Repository Git operations are separate. The operator host must already be able to clone and push `Shashakar/RPG-Kingdom` using normal Git/GitHub CLI authentication. Do not reuse or expose the narrow Symphony tracker token merely to make `git push` work.

The model router runs as the Codex launch command after the tracker secret is scrubbed. It reads issue routing labels using the operator's normal `gh` authentication, not the tracker PAT.

## Upstream strategy

The default remains configuration over forking.

Phases 2 and 3 do **not** patch official Symphony. Required RPG Kingdom-specific behavior is implemented through narrow adapters at existing seams:

1. `codex.command` -> model router;
2. `hooks.before_run` -> worker-lifetime and Unity resource guards;
3. `hooks.after_run` -> Unity release and redispatch guard.

A future local Symphony patch is justified only when a required behavior cannot be expressed safely through workflow configuration, prompts, hooks, or command adapters.

## Planned phases

### Phase 1 — smoke path — COMPLETE

Proved one low-risk RPG Kingdom issue can travel from `symphony:ready` to an inspectable PR through Codex App Server.

### Phase 2 — budgeted model/risk routing — COMPLETE

Route Luna/Terra/Sol/Astra with explicit overrides, reduce turn budget, scale context, and prevent automatic fresh-session redispatch. The Luna mechanical and Terra investigative lanes are measured; Sol/Astra should be validated on genuine work when those classes naturally occur rather than through synthetic allowance spend.

### Phase 3 — Unity-aware scheduling — CURRENT

Define explicit Unity resource/validation labels, fail closed before Codex when required Unity access is unavailable, and reserve an exclusive host-owned `unity-editor` resource for the worker lifetime.

### Phase 4 — Unity worker integration

Connect the exclusive worker path to a supported Windows Unity runner and make EditMode/PlayMode validation observable to Codex and the PR handoff.

### Phase 5 — controlled concurrency

Increase code-only concurrency after measuring workspace, Git, CI, and usage behavior. The Phase 3 Unity lock remains exclusive.

### Phase 6 — review/rework automation

Allow actionable PR feedback to return work to Symphony without bypassing human merge authority.

## Non-goals

The supervisor does not:

- own RPG Kingdom architecture;
- replace GitHub Issues or PRs;
- merge autonomous changes;
- author the Unity production scene merely because an agent can access it;
- maximize agent count or token consumption;
- silently escalate work to Astra/Sol;
- hide failed validation, exhausted budgets, missing Unity infrastructure, or unresolved blockers.
