# Codex Worker Permissions

## Problem

RPG Kingdom workers must perform ordinary local Git operations: fetch existing branches, switch/merge, stage, commit, and push. Codex's legacy `workspace-write` sandbox deliberately protects `.git` metadata as read-only, so source files can be edited while commands such as `git fetch`, `git add`, and `git commit` fail on `.git/FETCH_HEAD` or `.git/index.lock`.

Issue #97 exposed this mismatch before implementation began. The worker could see `origin/codex/loot-rewards-v1` but could not switch to it because Symphony supplied the legacy `workspaceWrite` turn policy.

## Policy

The Supervisor uses Codex's named permission-profile model instead of Symphony's legacy `thread_sandbox` / `turn_sandbox_policy` fields.

The shared profile is defined by `scripts/codex-permission-profile.sh`:

- extend Codex's built-in `:workspace` profile;
- explicitly grant `write` to `.git` under the current workspace root;
- enable network access for GitHub/package operations;
- do not grant unrestricted host filesystem access;
- do not use `danger-full-access`.

The model router passes this profile to `codex app-server` through trusted host-side `--config` overrides. `WORKFLOW.md` intentionally omits legacy sandbox fields because permission profiles and the legacy sandbox model must not be composed.

## Startup proof

`scripts/codex-git-write-probe.sh` performs a model-free sandbox test against a disposable Git repository. It runs `git add` and `git commit` under the exact named permission profile used by the App Server. No model request or ChatGPT allowance is consumed.

`scripts/run-symphony.sh` runs this probe before entering the alternate screen or starting Symphony. If the installed Codex version/configuration cannot write `.git` under the scoped profile, Symphony fails closed before any issue can consume a worker lifetime.

Run it directly with:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/codex-git-write-probe.sh
```

Expected:

```text
RPG Kingdom Codex permission probe: PASS
```

`RPGK_SKIP_CODEX_PERMISSION_PROBE=1` exists only as an operator escape hatch for diagnosis; normal unattended operation should not skip the proof.

## Security boundary

Writable Git metadata is necessary because Git state is part of the current issue workspace's implementation contract. It does not authorize writes to unrelated repositories, the Supervisor checkout, host credentials, or the Windows Unity staging project.

Unity remains a separate host-owned resource and may only be exercised through `scripts/unity-runner.sh` when the issue owns `resource:unity-editor`.

## Upgrade rule

Codex owns the permission-profile schema and sandbox behavior. After upgrading Codex, rerun both:

```bash
bash tests/run.sh
bash scripts/codex-git-write-probe.sh
```

Do not rearm a halted implementation issue after a Codex upgrade until the probe passes.
