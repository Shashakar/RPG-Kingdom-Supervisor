# RPG Kingdom Supervisor

GitHub-backed orchestration for autonomous Codex work against `Shashakar/RPG-Kingdom`, built on the official `openai/symphony` reference implementation and Codex App Server.

## Purpose

The Supervisor turns labeled RPG Kingdom GitHub issues into bounded Codex worker attempts with explicit model/risk routing, budget guards, Unity-aware scheduling, and evidence-based handoff to pull requests.

It intentionally does **not** own RPG Kingdom gameplay architecture. Workers must read the game repository's checked-in `AGENTS.md` and system documentation directly.

## Runtime

The evaluated Symphony upstream revision is pinned in `SYMPHONY_UPSTREAM.md`. Do not silently follow upstream `main` in production use.

The current pin requires one local compatibility transform so Symphony can forward Codex named permission profiles on App Server `thread/start` and `turn/start`. This keeps the worker workspace and its `.git` metadata writable without granting `danger-full-access`.

Install or repair that compatibility layer with:

```bash
bash scripts/apply-symphony-permissions-patch.sh
```

The installer applies a deterministic source transform only to the evaluated upstream pin, uses local Symphony branch `rpgk/named-permissions`, runs focused Symphony tests, commits the local compatibility change, and verifies the seam before normal orchestration starts.

## Starting Symphony

Use:

```bash
bash scripts/run-symphony.sh
```

The launcher loads the local GitHub tracker token, runs the direct Codex Git-write permission probe, verifies the active Symphony named-permissions compatibility seam, and then enters the alternate-screen Symphony dashboard.

Normal startup fails closed if either permission proof is missing.

## Worker routing

Model selection is issue-label driven:

- `risk:mechanical` -> GPT-5.6 Luna / low reasoning
- `risk:normal` or no risk label -> GPT-5.6 Luna / medium reasoning
- `risk:investigative` -> GPT-5.6 Terra / medium reasoning
- `risk:architecture` -> GPT-5.6 Sol / high reasoning
- `risk:end-to-end` -> GPT-6 Astra / medium reasoning

Explicit `model:*` and `effort:*` labels override the risk-derived route. Conflicting routing labels fail closed.

## Dispatch lease and worker budget

`symphony:ready` is the dispatch lease. A worker should leave it in place while useful implementation is active and remove it only after the branch is pushed and a reviewable PR exists.

The host records a completed-attempt marker after every worker lifetime. If a worker exits while `symphony:ready` is still present, the host removes the lease and applies `symphony:halted`. This prevents automatic redispatch from consuming multiple Codex worker lifetimes without explicit operator intent.

To intentionally retry a halted issue after fixing the blocker:

```bash
bash scripts/rearm-issue.sh <issue-number>
```

Do not manually toggle labels instead of using the rearm script.

## Unity scheduling

Unity is treated as a host-owned exclusive resource.

Issues that require real editor validation use:

- `resource:unity-editor`
- `validation:unity-required`

Workers may only invoke Unity through:

```bash
bash scripts/unity-runner.sh health
bash scripts/unity-runner.sh editmode [--filter FILTER]
bash scripts/unity-runner.sh playmode [--filter FILTER]
```

The runner stages the project onto Windows-local NTFS, preserves a warm `Library` cache, runs the project-declared Unity version, and returns test artifacts to the WSL workspace.

If a normal Unity Editor process is already open, Unity-required dispatch fails before Codex starts. This is intentional host exclusivity.

## Permissions

The Supervisor uses a named Codex permission profile defined in `scripts/codex-permission-profile.sh`.

The profile extends Codex's workspace boundary, explicitly grants `.git` write access inside the current worker workspace, and enables required network access. It does not make unrelated repositories, credentials, the Supervisor checkout, or the Windows Unity stage generally writable.

Two separate checks protect this boundary:

```bash
bash scripts/codex-git-write-probe.sh
bash scripts/verify-symphony-permissions-patch.sh
```

The first proves the Codex profile itself can perform ordinary Git metadata writes. The second proves Symphony will actually select that named profile rather than overriding it with its legacy `workspace-write` request policy.

See `docs/CODEX_PERMISSIONS.md` for the full rationale.

## Tests

Run the Supervisor shell suite with:

```bash
bash tests/run.sh
```

For permission-sensitive changes also run:

```bash
bash scripts/codex-git-write-probe.sh
bash scripts/verify-symphony-permissions-patch.sh
```

For the Symphony compatibility layer, use the installer once against the evaluated upstream pin so its focused Elixir tests also run:

```bash
bash scripts/apply-symphony-permissions-patch.sh
```

## Current boundaries

The Supervisor does not automatically merge pull requests. Concurrency remains deliberately conservative. Production-scene editing remains governed by RPG Kingdom's own repository rules even when a worker owns the Unity resource.
