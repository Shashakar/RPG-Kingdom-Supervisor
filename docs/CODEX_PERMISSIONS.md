# Codex Worker Permissions

## Problem

RPG Kingdom workers must perform ordinary local Git operations: fetch existing branches, switch/merge, stage, commit, and push. Codex's legacy `workspace-write` sandbox deliberately protects `.git` metadata as read-only, so source files can be edited while commands such as `git fetch`, `git add`, and `git commit` fail on `.git/FETCH_HEAD` or `.git/index.lock`.

Issue #97 exposed two distinct layers of this mismatch:

1. the standalone Codex permission profile originally needed explicit `.git` write access;
2. after that profile was corrected and the model-free probe passed, Symphony still sent `sandbox: workspace-write` on `thread/start` and `sandboxPolicy: workspaceWrite` on `turn/start`, overriding the App Server's named profile and restoring `.git` read-only protection.

A passing direct Codex sandbox probe therefore is necessary but not sufficient. The orchestration client must select the same named permission profile when it creates the App Server thread and turn.

## Policy

The Supervisor uses Codex's named permission-profile model rather than `danger-full-access`.

The shared profile is defined by `scripts/codex-permission-profile.sh`:

- extend Codex's built-in `:workspace` profile;
- explicitly grant `write` to `.git` under the current workspace root;
- enable network access for GitHub/package operations;
- do not grant unrestricted host filesystem access;
- do not use `danger-full-access`.

The model router defines and selects this profile for `codex app-server` through trusted host-side `--config` overrides. `WORKFLOW.md` also names `rpgk_supervisor_workspace` through the local Symphony compatibility seam so `thread/start` and `turn/start` select `permissions` instead of sending mutually exclusive legacy sandbox overrides.

## Why Symphony needs a compatibility patch

The evaluated Symphony revision defaults `codex.thread_sandbox` to `workspace-write` and synthesizes a `workspaceWrite` turn policy when no explicit turn policy is configured. Its App Server client always sends those values. Current Codex App Server supports a named `permissions` field on both `thread/start` and `turn/start`, and that field cannot be combined with the legacy sandbox field on the same request.

The pinned upstream Symphony revision does not expose that named-profile field in `WORKFLOW.md`, so configuration alone cannot express the required Git-write boundary. The Supervisor therefore carries a narrow, auditable patch in `patches/symphony-named-permissions.patch`. It adds a generic `codex.permissions` setting and preserves Symphony's legacy sandbox behavior as the fallback when no named profile is configured.

Apply the patch once to the evaluated Symphony checkout with:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/apply-symphony-permissions-patch.sh
```

The installer:

- refuses a dirty Symphony checkout;
- requires the evaluated upstream pin;
- creates local branch `rpgk/named-permissions` rather than modifying upstream history in place;
- applies the tracked compatibility patch;
- formats and runs the focused Symphony configuration/App Server tests;
- commits the local compatibility change;
- verifies the expected named-permission seam is active.

`scripts/run-symphony.sh` fails closed if that compatibility seam is missing, so a future upstream checkout/pull cannot silently return workers to read-only Git metadata.

## Startup proofs

`scripts/codex-git-write-probe.sh` performs a model-free sandbox test against a disposable Git repository. It runs `git add` and `git commit` under the exact named permission profile used by the App Server. No model request or ChatGPT allowance is consumed.

`scripts/verify-symphony-permissions-patch.sh` separately proves the active pinned Symphony source can propagate `codex.permissions` and choose the named profile instead of the legacy sandbox request fields.

`scripts/run-symphony.sh` requires both checks before entering the alternate screen or starting Symphony.

Run them directly with:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/verify-symphony-permissions-patch.sh
bash scripts/codex-git-write-probe.sh
```

Expected:

```text
RPG Kingdom Symphony named-permissions compatibility: PASS
RPG Kingdom Codex permission probe: PASS
```

`RPGK_SKIP_CODEX_PERMISSION_PROBE=1` exists only as an operator escape hatch for diagnosis; normal unattended operation should not skip the direct Codex proof. The Symphony compatibility check is not optional.

## Security boundary

Writable Git metadata is necessary because Git state is part of the current issue workspace's implementation contract. It does not authorize writes to unrelated repositories, the Supervisor checkout, host credentials, or the Windows Unity staging project.

Unity remains a separate host-owned resource and may only be exercised through `scripts/unity-runner.sh` when the issue owns `resource:unity-editor`.

## Upgrade rule

Codex owns the permission-profile schema and App Server request contract. Symphony owns the orchestration request construction. After upgrading either one:

```bash
bash tests/run.sh
bash scripts/verify-symphony-permissions-patch.sh
bash scripts/codex-git-write-probe.sh
```

If upstream Symphony gains first-class named-permission support, remove the local patch rather than maintaining duplicate behavior. Do not rearm a halted implementation issue after a Codex or Symphony upgrade until both permission checks pass.
