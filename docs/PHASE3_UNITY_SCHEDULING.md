# Phase 3: Unity-Aware Resource Scheduling

Phase 3 adds a host-owned scheduling contract for Unity-dependent RPG Kingdom work without yet adding a Windows Unity test runner. The goal is to make Unity dependence explicit and fail closed before Codex spends allowance when required editor access is unavailable.

## Scope

Phase 3 owns:

- issue labels that declare Unity resource and validation requirements;
- a pre-Codex Unity policy check;
- an exclusive host-side `unity-editor` lock;
- release of that lock at the end of the worker lifetime;
- fail-closed tracker handoff when required Unity access is unavailable or policy labels conflict;
- worker instructions that forbid invented Unity results and ad hoc editor launching.

Phase 3 does **not** own:

- invoking Unity on Windows;
- selecting a Unity project path for Windows execution;
- EditMode or PlayMode command construction;
- parsing Unity XML/log results;
- increasing worker concurrency;
- automatic merge or automatic retry.

Those execution concerns belong to Phase 4.

## Labels

### `resource:unity-editor`

The issue needs exclusive ownership of the host's Unity Editor resource during the worker lifetime.

This label is a scheduling/resource declaration, not permission to edit the RPG Kingdom production scene. Repository `AGENTS.md` and scene-ownership rules remain authoritative.

### `validation:unity-required`

The issue cannot begin an unattended Codex worker unless the supported Unity runner is healthy and `resource:unity-editor` is also present.

If the resource label is missing, the policy is invalid and the dispatch halts before Codex starts.

If the runner is not marked ready, the dispatch also halts before Codex starts.

### `validation:unity-optional`

The issue may proceed without Unity. The worker must explicitly report any missing Unity validation in the PR rather than treating it as passed.

`validation:unity-required` and `validation:unity-optional` are mutually exclusive.

## Preflight sequence

For each dispatch, `WORKFLOW.md` executes:

1. `before-run-guard.sh` — enforces the one-worker-lifetime allowance boundary;
2. `unity-resource-guard.sh` — reads Unity labels, validates policy, checks runner readiness, and acquires the Unity lock when requested;
3. Codex App Server — only if both guards succeed.

A Unity preflight failure removes `symphony:ready`, adds `symphony:halted`, and leaves a concise issue comment before exiting. This avoids repeatedly buying Codex attempts for a host capability that is known to be unavailable.

## Exclusive resource lock

The lock lives outside the RPG Kingdom workspace:

```text
~/.local/state/rpg-kingdom-supervisor/locks/unity-editor.lock/
```

It records:

- owning `GH-<number>` issue;
- owning workspace path;
- acquisition timestamp.

The lock uses atomic directory creation. If another owner already holds it, the new dispatch fails closed instead of sharing the editor.

`release-unity-resource.sh` runs before the normal Phase 2 after-run guard and removes the lock only when the current issue owns it.

With `max_concurrent_agents: 1`, this lock is primarily a correctness contract. It becomes a real concurrency boundary when Phase 5 later allows multiple code-only workers.

## Runner readiness

Phase 3 uses this host-side readiness assertion:

```text
RPGK_UNITY_RUNNER_READY=1
```

Do **not** set it merely to bypass the guard. Phase 4 will define the actual Windows Unity bridge and its health check. Until that exists and passes, the variable should remain unset/zero.

A worker is explicitly forbidden from setting or inferring this value itself.

## Dispatch examples

Code-only bounded issue:

```text
risk:normal
symphony:ready
```

Implementation may proceed; no Unity promise is made.

Code issue where Unity validation would be useful but is not required to create a reviewable PR:

```text
risk:normal
validation:unity-optional
symphony:ready
```

Implementation may proceed. Missing Unity validation must be called out in the PR.

Issue that must prove behavior in Unity before Codex should run:

```text
risk:investigative
resource:unity-editor
validation:unity-required
symphony:ready
```

Until Phase 4 marks the runner healthy, this intentionally halts before Codex starts.

## Why the resource and validation labels are separate

The concepts are related but not identical:

- `resource:*` says what scarce host capability the worker needs;
- `validation:*` says whether absence of that capability blocks the dispatch.

Keeping them separate avoids making every Unity-adjacent code change editor-blocking while still allowing strict validation for tasks where a PR without Unity evidence would be misleading.

## Safety properties

Phase 3 is designed so that:

- required Unity work cannot silently degrade to unvalidated work;
- conflicting validation policy cannot silently pick the cheaper path;
- workers cannot invent Unity results;
- Unity access remains exclusive and host-owned;
- resource ownership does not broaden production-scene authority;
- unavailable Unity infrastructure does not consume a Codex worker turn;
- retry remains an explicit operator decision through the existing rearm path.
