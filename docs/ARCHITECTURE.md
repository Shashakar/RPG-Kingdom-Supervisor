# RPG Kingdom Supervisor Architecture

## Purpose

RPG Kingdom Supervisor removes manual Codex session management from RPG Kingdom development while preserving human architectural control and independent review.

The supervisor is an orchestration system, not a gameplay system. It consumes approved GitHub issues, prepares isolated workspaces, runs Codex through Symphony, and hands completed work back as pull requests.

## System boundaries

### ChatGPT / human planning

Architecture, prioritization, research, issue definition, and independent PR review remain outside the autonomous execution loop.

The supervisor does not decide the RPG Kingdom roadmap in Phase 1.

### GitHub

`Shashakar/RPG-Kingdom` is the system of record for work and code.

GitHub Issues carry implementation scope. A `symphony:ready` label explicitly opts an issue into autonomous execution.

GitHub pull requests are the handoff boundary back to human review. Phase 1 never auto-merges.

### Symphony

Official `openai/symphony` provides:

- tracker polling;
- required-label filtering;
- per-issue workspace lifecycle;
- Codex App Server lifecycle;
- turn continuation and retry behavior;
- tracker-native tools exposed to Codex while keeping tracker credentials host-side;
- runtime observability.

This repository provides the RPG Kingdom-specific workflow configuration and operator policy.

### Codex

Codex operates only inside the workspace created for the current issue. Once the RPG Kingdom repository is cloned, its own `AGENTS.md`, `README.md`, architecture docs, and system docs are authoritative.

Codex is responsible for implementation, relevant tests, required documentation updates, branch publication, and PR creation when the RPG Kingdom repository instructions require them.

### Unity

Unity is not a Phase 1 orchestration resource.

The initial supervisor may dispatch code-only work or work that can safely stop with explicit Unity validation remaining. Automated exclusive Unity scheduling and editor integration are separate later phases.

## Phase 1 flow

```text
Human + ChatGPT
      |
      | define/review issue
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
Codex App Server
      |
      | read RPG Kingdom instructions
      | create codex/* branch
      | implement + test + document
      | push + open PR
      v
GitHub PR
      |
      v
Human + ChatGPT review
```

## Dispatch contract

Phase 1 uses a deliberately small contract:

- issue must be open;
- issue must have `symphony:ready`;
- maximum concurrent agents is `1`;
- the worker keeps `symphony:ready` while implementation is active;
- when a PR is ready for human review, the worker removes `symphony:ready` as its final orchestration-label mutation so the issue is no longer dispatchable;
- the issue remains open until the normal RPG Kingdom review/merge process decides its final outcome.

Later phases may introduce explicit `running`, `review`, `blocked`, and `failed` labels if they provide real operator value. They are not required for the first smoke path.

## Authentication boundary

Symphony owns tracker authentication on the host side and exposes its provider-native `github_api` tool to Codex. Tracker token environment aliases are intentionally removed from the Codex child environment by the evaluated upstream revision.

Repository Git operations are a separate concern. The operator host must already be able to clone and push `Shashakar/RPG-Kingdom` using its normal Git authentication mechanism. Do not reuse or expose the Symphony tracker token merely to make `git push` work.

## Upstream strategy

The default is configuration over forking.

A local Symphony code change is justified only when a required behavior cannot be expressed safely through:

1. `WORKFLOW.md` front matter;
2. the workflow prompt;
3. existing Symphony hooks;
4. documented Symphony extension/tool boundaries.

Any fork or adapter must have a bounded reason and automated tests.

## Planned phases

### Phase 1 — smoke path

Prove one low-risk RPG Kingdom issue can travel from `symphony:ready` to an inspectable PR through Codex App Server.

### Phase 2 — model/risk routing

Classify work and select Sol, Terra, or Luna plus reasoning effort. Explicit issue overrides take precedence over automatic routing.

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
- merge autonomous changes in Phase 1;
- author the Unity production scene merely because an agent can access it;
- maximize agent count or token consumption;
- hide failed validation or unresolved blockers.
