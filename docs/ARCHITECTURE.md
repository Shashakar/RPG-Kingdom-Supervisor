# RPG Kingdom Supervisor Architecture

## Purpose

RPG Kingdom Supervisor removes manual Codex session management from RPG Kingdom development while preserving human architectural control, usage discipline, and independent review.

The supervisor is an orchestration system, not a gameplay system. It consumes approved GitHub issues, prepares isolated workspaces, runs Codex through Symphony, and hands completed work back as pull requests.

## System boundaries

### ChatGPT / human planning

Architecture, prioritization, research, issue definition, risk classification, model overrides, and independent PR review remain outside the autonomous execution loop.

The supervisor does not decide the RPG Kingdom roadmap.

### GitHub

`Shashakar/RPG-Kingdom` is the system of record for work and code.

GitHub Issues carry implementation scope. A `symphony:ready` label explicitly opts an issue into autonomous execution. Phase 2 risk/model/effort labels communicate execution policy without changing game architecture.

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

This repository provides RPG Kingdom-specific workflow configuration plus narrow host-side adapters for model routing and fail-closed execution budgeting.

### Codex

Codex operates only inside the workspace created for the current issue. Once the RPG Kingdom repository is cloned, its own `AGENTS.md`, `README.md`, architecture docs, and system docs are authoritative.

Codex is responsible for implementation, relevant tests, required documentation updates, branch publication, and PR creation when the RPG Kingdom repository instructions require them.

Phase 2 starts Codex App Server through `scripts/codex-app-server-router.sh`, which selects an explicit model/reasoning profile from GitHub labels before the thread starts.

### Unity

Unity is not yet an orchestrated exclusive resource.

The supervisor may dispatch code-only work or work that can safely stop with explicit Unity validation remaining. Automated exclusive Unity scheduling and editor integration remain separate later phases.

## Current flow

```text
Human + ChatGPT
      |
      | define issue + risk/model override when useful
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
Codex router
      |
      | labels -> Luna / Terra / Sol / Astra + effort
      v
Codex App Server
      |
      | read RPG Kingdom instructions
      | create codex/* branch
      | implement + validate
      | push + open PR
      | remove symphony:ready
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
- the worker keeps `symphony:ready` while implementation is genuinely active;
- when a PR is ready for human review, the worker removes `symphony:ready` as its final orchestration-label mutation;
- if the worker attempt ends with that lease still present, the host guard removes it and adds `symphony:halted` so another fresh Codex thread cannot start automatically;
- the issue remains open until the normal RPG Kingdom review/merge process decides its final outcome.

## Model/risk routing

The supervisor uses deterministic labels rather than asking a separate LLM to classify every issue. This avoids spending Codex allowance merely to decide which Codex model should run.

Default risk routes:

- `risk:mechanical` -> GPT-5.6 Luna / low reasoning;
- `risk:normal` -> GPT-5.6 Terra / medium reasoning;
- `risk:architecture` -> GPT-5.6 Sol / high reasoning;
- `risk:end-to-end` -> GPT-6 Astra / medium reasoning;
- no risk/model label -> GPT-5.6 Terra / medium reasoning.

Explicit `model:*` labels override risk routing, and explicit `effort:*` labels override reasoning effort. Conflicting labels fail closed.

Astra is intentionally a fourth tier rather than a replacement for Sol. It is reserved for the hardest end-to-end tasks where stronger implementation/tool-use/verification behavior can plausibly save iterations. Sol remains the architecture-sensitive default when the job is primarily reasoning over code and contracts.

## Context budget

RPG Kingdom's own `AGENTS.md` always wins. The supervisor does not skip repository-required architecture/system documentation to save tokens.

Within those requirements, workers are told to scale exploration to the task:

- mechanical work should not inventory unrelated systems;
- normal work should stay within affected system implementation/tests and required docs;
- architecture work may expand to affected cross-system contracts;
- end-to-end work may build broader context when necessary for actual verification.

This is meant to reduce repeated context processing, not weaken architecture discipline.

## Execution budget boundary

Phase 1 demonstrated that upstream Symphony's `agent.max_turns` is a per-worker-lifetime ceiling, not a total issue budget. If an issue remains open and routable after that ceiling, the orchestrator can dispatch a new worker lifetime.

Phase 2 therefore treats `symphony:ready` as both dispatch authorization and the hard redispatch lease. The `after_run` guard revokes that lease when an attempt ends without a clean handoff.

The guard is intentionally host-side and uses the narrow Symphony tracker credential. Codex does not receive that secret.

## Authentication boundary

Symphony owns tracker authentication on the host side and exposes its provider-native `github_api` tool to Codex. The tracker token is stored in `SYMPHONY_GITHUB_TOKEN` and is scrubbed from the Codex child environment by the evaluated upstream revision.

Repository Git operations are separate. The operator host must already be able to clone and push `Shashakar/RPG-Kingdom` using normal Git/GitHub CLI authentication. Do not reuse or expose the narrow Symphony tracker token merely to make `git push` work.

The model router runs as the Codex launch command after the tracker secret is scrubbed. It reads issue routing labels using the operator's normal `gh` authentication, not the tracker PAT.

## Upstream strategy

The default remains configuration over forking.

Phase 2 does **not** patch official Symphony. The two required behaviors that upstream does not directly expose—per-issue model selection and total-issue fail-closed redispatch—are implemented as narrow shell adapters at existing command/hook seams:

1. `codex.command` -> model router;
2. `hooks.after_run` -> redispatch guard.

A future local Symphony patch is justified only when a required behavior cannot be expressed safely through workflow configuration, prompts, hooks, or command adapters.

## Planned phases

### Phase 1 — smoke path — COMPLETE

Proved one low-risk RPG Kingdom issue can travel from `symphony:ready` to an inspectable PR through Codex App Server.

### Phase 2 — budgeted model/risk routing — CURRENT

Route Luna/Terra/Sol/Astra with explicit overrides, reduce turn budget, scale context, and prevent automatic fresh-session redispatch. Benchmark a second trivial task against #91 before increasing workload.

### Phase 3 — Unity-aware scheduling

Introduce an exclusive `unity-editor` resource and prevent concurrent Unity-dependent work.

### Phase 4 — Unity worker integration

Connect the exclusive worker path to the Unity Editor/integration and make engine-facing validation observable.

### Phase 5 — controlled concurrency

Increase code-only concurrency after measuring workspace, Git, CI, and usage behavior.

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
- hide failed validation, exhausted budgets, or unresolved blockers.
