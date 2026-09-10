# Phase 3: Unity-Aware Resource Scheduling

Phase 3 adds a host-owned scheduling contract for Unity-dependent RPG Kingdom work. Its purpose is to make Unity dependence explicit and fail closed before Codex spends allowance when required editor access is unavailable.

Phase 3 is now complete. Phase 4 supplies the Windows runner described in `PHASE4_UNITY_RUNNER.md`; the scheduling contract here remains the authority for when that runner may be used.

## Scope

Phase 3 owns:

- issue labels that declare Unity resource and validation requirements;
- a pre-Codex Unity policy check;
- an exclusive host-side `unity-editor` lock;
- release of that lock at the end of the worker lifetime;
- reviewed recovery of a stale same-issue lock through the one-shot rearm path;
- fail-closed tracker handoff when required Unity access is unavailable or policy labels conflict;
- worker instructions that forbid invented Unity results and ad hoc editor launching.

Phase 4 owns Windows staging, editor invocation, EditMode/PlayMode command construction, and result parsing.

## Labels

### `resource:unity-editor`

The issue needs exclusive ownership of the host's Unity Editor resource during the worker lifetime.

This is a scheduling/resource declaration, not permission to edit the RPG Kingdom production scene. Repository `AGENTS.md` and scene-ownership rules remain authoritative.

### `validation:unity-required`

The issue cannot begin an unattended Codex worker unless the supported Unity runner is healthy and `resource:unity-editor` is also present.

If the resource label is missing, the policy is invalid and the dispatch halts before Codex starts. Once Codex starts, actual relevant Unity runner evidence is required before a clean PR handoff.

### `validation:unity-optional`

The issue may proceed without Unity. The worker must explicitly report any missing Unity validation in the PR rather than treating it as passed.

`validation:unity-required` and `validation:unity-optional` are mutually exclusive.

### `symphony:rearm`

This is not a Unity resource label. It is the one-shot reviewed continuation approval owned by the Phase 2 worker-lifetime guard. It matters to Phase 3 because the same host preflight may use it to recover a stale `unity-editor` lock owned by the issue being explicitly rearmed.

The label is consumed before Codex starts. It never authorizes clearing a lock owned by another issue.

## Preflight sequence

For each dispatch, `WORKFLOW.md` executes:

1. `before-run-guard.sh` — enforces the one-worker-lifetime allowance boundary and consumes an explicit `symphony:rearm` continuation request when present; during that reviewed continuation it may clear a stale Unity lock owned by the same GH issue;
2. `unity-resource-guard.sh` — reads Unity labels, validates policy, runs the supported Unity runner health check when the editor resource is requested, and acquires the lock;
3. Codex App Server — only if both guards succeed.

A Unity preflight failure removes `symphony:ready`, adds `symphony:halted`, and leaves a concise issue comment before exiting. This avoids repeatedly buying Codex attempts for a host capability known to be unavailable.

Because `symphony:rearm` is consumed before the Unity guard runs, a failed Unity preflight requires another explicit rearm after the condition is reviewed. The approval is never reusable.

## Exclusive resource lock

The lock lives outside the RPG Kingdom workspace:

```text
~/.local/state/rpg-kingdom-supervisor/locks/unity-editor.lock/
```

It records the owning `GH-<number>` issue, workspace path, and acquisition timestamp. Atomic directory creation prevents a second owner from sharing the editor.

`release-unity-resource.sh` runs before the normal after-run guard and removes the lock only when the current issue owns it.

A host interruption can prevent `after_run` from releasing the directory. A reviewed continuation may recover that stale lock only when `before-run-guard.sh` sees the one-shot `symphony:rearm` label and the recorded owner matches the same `GH-<number>`. A lock owned by another issue remains authoritative and is never reclaimed by rearm.

With `max_concurrent_agents: 1`, the lock is primarily a correctness contract. It becomes an active concurrency boundary when code-only concurrency increases later.

## Runner readiness

The original Phase 3 implementation used a temporary host assertion:

```text
RPGK_UNITY_RUNNER_READY=1
```

Phase 4 removes that placeholder. Do not set or depend on it.

`unity-resource-guard.sh` now calls:

```bash
bash ~/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh health --project "$PWD"
```

for any dispatch requesting `resource:unity-editor`. Readiness therefore comes from the real project-declared Unity version, installed Windows editor, PowerShell/robocopy bridge, and writable staging root.

## Dispatch examples

Code-only bounded issue:

```text
risk:normal
symphony:ready
```

Unity validation useful but not required:

```text
risk:normal
validation:unity-optional
symphony:ready
```

Implementation may proceed without Unity and must disclose missing editor evidence.

Issue that requires Unity evidence:

```text
risk:investigative
resource:unity-editor
validation:unity-required
symphony:ready
```

The host must pass the live Phase 4 health check and acquire the editor lock before Codex starts.

Reviewed continuation of a previously completed/halted Unity-required issue:

```text
risk:investigative
resource:unity-editor
validation:unity-required
symphony:rearm
symphony:ready
```

Add `symphony:rearm` before the normal ready lease. The host consumes it during preflight.

## Why resource and validation labels are separate

- `resource:*` says what scarce host capability the worker needs;
- `validation:*` says whether absence of that capability blocks work/completion.

Keeping them separate avoids making every Unity-adjacent code change editor-blocking while preserving strict validation where a PR without Unity evidence would be misleading.

## Safety properties

- required Unity work cannot silently degrade to unvalidated work;
- conflicting validation policy cannot silently choose a path;
- workers cannot invent Unity results;
- Unity access remains exclusive and host-owned;
- resource ownership does not broaden production-scene authority;
- unavailable Unity infrastructure does not consume a Codex worker turn;
- `symphony:ready` alone cannot bypass a prior worker lifetime;
- a reviewed rearm may recover only same-issue stale Unity state and can never steal another issue's lock;
- retry remains an explicit human/ChatGPT decision through the one-shot rearm path.
