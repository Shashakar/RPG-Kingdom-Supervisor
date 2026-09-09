# Codex Worker Permissions

## Problem

RPG Kingdom workers must perform ordinary local Git operations: fetch existing branches, switch/merge, stage, commit, and push. Codex's legacy `workspace-write` sandbox deliberately protects `.git` metadata as read-only, so source files can be edited while commands such as `git fetch`, `git add`, and `git commit` fail on `.git/FETCH_HEAD` or `.git/index.lock`.

Issue #97 exposed three layers of this mismatch:

1. Symphony originally sent the legacy `workspace-write` sandbox on every App Server thread/turn;
2. after Symphony was taught to forward a named permission profile, the worker still resolved to an effective policy with `.git = read`;
3. the original direct `codex sandbox` probe did not prove the actual `codex app-server -> thread/start` profile-selection path used by Symphony.

A passing standalone sandbox probe is therefore necessary but not sufficient. Startup must also prove that App Server selects the intended named profile before any model-backed worker is allowed to run.

## Policy

The Supervisor uses Codex's named permission-profile model rather than `danger-full-access`.

The shared profile is defined by `scripts/codex-permission-profile.sh`. It is explicit instead of inheriting `:workspace`:

- `:root = read` keeps the host readable without granting host writes;
- `:workspace_roots/. = write` grants ordinary writes inside the active issue workspace;
- `:workspace_roots/.git = write` narrowly reopens Git metadata for fetch/switch/add/commit/push;
- `/tmp` and the configured temp directory remain writable for tool scratch space;
- network access is enabled for GitHub/package operations;
- `.agents` and `.codex` receive no narrower write grant and therefore remain protected by Codex's metadata-write rules;
- unrestricted host filesystem access and `danger-full-access` are not granted.

The profile is intentionally not implemented as `extends = ":workspace"`. Codex's built-in workspace profile contributes protected metadata rules for `.git`, `.agents`, and `.codex`; GH-97 demonstrated that depending on inheritance plus a child `.git` override did not produce the required effective App Server worker policy on the evaluated Codex build.

The model router defines this profile for `codex app-server` through trusted host-side `--config` overrides. `WORKFLOW.md` names `rpgk_supervisor_workspace` through the local Symphony compatibility seam so `thread/start` and `turn/start` select `permissions` instead of sending mutually exclusive legacy sandbox overrides.

## Why Symphony needs a compatibility transform

The evaluated Symphony revision defaults `codex.thread_sandbox` to `workspace-write` and synthesizes a `workspaceWrite` turn policy when no explicit turn policy is configured. Its upstream App Server client always sends those values. Codex App Server 0.153.4 supports a named `permissions` field on both `thread/start` and `turn/start`, and that field cannot be combined with the legacy sandbox field on the same request.

The pinned upstream Symphony revision does not expose that named-profile field in `WORKFLOW.md`, so configuration alone cannot express the required Git-write boundary. The Supervisor therefore carries a narrow, deterministic source transform in `scripts/patch-symphony-named-permissions.py`. It is intentionally anchored to the evaluated upstream source and fails if those anchors no longer match. The transform adds a generic `codex.permissions` setting and preserves Symphony's legacy sandbox behavior as the fallback when no named profile is configured.

Apply it once to the evaluated Symphony checkout with:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/apply-symphony-permissions-patch.sh
```

The installer:

- refuses a dirty Symphony checkout;
- requires the evaluated upstream pin;
- creates or safely reuses local branch `rpgk/named-permissions` when it still points at that pin;
- applies the deterministic pinned-source transform;
- formats and runs the focused Symphony configuration/App Server tests;
- commits the local compatibility change;
- verifies the expected named-permission seam is active.

`scripts/run-symphony.sh` fails closed if that compatibility seam is missing, so a future upstream checkout/pull cannot silently return workers to read-only Git metadata.

## Startup proofs

Three independent checks protect unattended dispatch:

1. `scripts/verify-symphony-permissions-patch.sh` proves the pinned Symphony source can propagate `codex.permissions` and choose it instead of legacy sandbox request fields.
2. `scripts/codex-git-write-probe.sh` performs a model-free sandbox test against a disposable Git repository and proves the configured profile can execute `git add` and `git commit`.
3. `scripts/codex-app-server-permission-probe.sh` performs only the App Server initialize + ephemeral `thread/start` handshake and verifies `ThreadStartResponse.activePermissionProfile.id` is exactly `rpgk_supervisor_workspace`. It never calls `turn/start`, so it consumes no model allowance.

`scripts/run-symphony.sh` requires all three checks before entering the alternate screen or starting Symphony.

Run them directly with:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/verify-symphony-permissions-patch.sh
bash scripts/codex-git-write-probe.sh
bash scripts/codex-app-server-permission-probe.sh
```

Expected:

```text
RPG Kingdom Symphony named-permissions compatibility: PASS
RPG Kingdom Codex permission probe: PASS
RPG Kingdom Codex App Server permission probe: PASS (active=rpgk_supervisor_workspace)
```

`RPGK_SKIP_CODEX_PERMISSION_PROBE=1` exists only as an operator escape hatch for diagnosis; normal unattended operation should not skip either Codex proof. The Symphony compatibility check is not optional.

## Security boundary

Writable Git metadata is necessary because Git state is part of the current issue workspace's implementation contract. It does not authorize writes to unrelated repositories, the Supervisor checkout, host credentials, or the Windows Unity staging project.

Unity remains a separate host-owned resource and may only be exercised through `scripts/unity-runner.sh` when the issue owns `resource:unity-editor`.

## Upgrade rule

Codex owns the permission-profile schema and App Server request contract. Symphony owns the orchestration request construction. After upgrading either one:

```bash
bash tests/run.sh
bash scripts/verify-symphony-permissions-patch.sh
bash scripts/codex-git-write-probe.sh
bash scripts/codex-app-server-permission-probe.sh
```

If upstream Symphony gains first-class named-permission support, remove the local transform rather than maintaining duplicate behavior. Do not rearm a halted implementation issue after a Codex or Symphony upgrade until all permission checks pass.
