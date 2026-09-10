# Codex Worker Permissions

## Current boundary

RPG Kingdom workers need to inspect and edit source files inside their active `GH-N` workspace. They do **not** need to own Git metadata, Windows interop, or final GitHub handoff state.

The Supervisor now splits those responsibilities deliberately:

- Codex model turn: repository/source inspection, implementation, tests that do not require host services, and requests to typed host interfaces.
- Unity host broker: Windows/Unity execution and structured Unity results.
- Git handoff broker: branch preparation plus final stage/commit/non-force-push/PR creation.
- Human: PR review and merge.

This split is the result of the GH-97/GH-98 investigation. Model-free permission probes could prove `.git` writes and WSL interop while real model turns still intermittently observed read-only Git metadata, DNS failures, or blocked WSL/Windows behavior. Those capabilities are therefore no longer requirements of the model-turn sandbox.

## Named permission profile

The shared profile is defined by `scripts/codex-permission-profile.sh` and selected by Symphony through the named-permissions compatibility seam.

The profile grants:

- `:root = read` for host read access;
- `:workspace_roots/. = write` for ordinary source edits in the active issue workspace;
- `/tmp` and the configured temp directory for scratch files;
- network access for model research/package needs.

The profile does **not** explicitly reopen `.git` for writes. Protected metadata such as `.git`, `.agents`, and `.codex` is intentionally outside the model's required mutation boundary.

The Supervisor does not use `danger-full-access` to make Git or Unity work.

## Symphony compatibility

The evaluated Symphony revision does not natively express Codex's named `permissions` field in `WORKFLOW.md`, so the Supervisor carries a narrow deterministic transform in `scripts/patch-symphony-named-permissions.py`.

Apply/verify it with:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/apply-symphony-permissions-patch.sh
bash scripts/verify-symphony-permissions-patch.sh
```

`scripts/run-symphony.sh` fails closed when this compatibility seam is missing.

## Startup proof

Normal startup proves the capability the worker still needs: ordinary workspace writes through the selected App Server profile.

```bash
bash scripts/codex-app-server-permission-probe.sh
```

Expected:

```text
RPG Kingdom Codex App Server permission probe: PASS (active=rpgk_supervisor_workspace, workspace_write=ok)
```

The probe is model-free. It initializes App Server, creates an ephemeral thread with the named profile, performs an ordinary source-file write/remove through `command/exec`, and never starts a model turn.

`run-symphony.sh` requires this proof before dispatch. It no longer requires the legacy Git-write probe because `.git` ownership is not part of the worker contract.

## Legacy Git-write diagnostic

`scripts/codex-git-write-probe.sh` remains available as an explicit diagnostic when evaluating a new Codex release, but its result does not determine whether Symphony can operate.

```bash
bash scripts/codex-git-write-probe.sh
```

A failure here is not an operational blocker once the host Git handoff path is healthy. Do not broaden worker permissions solely to make this probe pass.

## Model-backed environment probe

`scripts/codex-turn-environment-probe.sh --run` remains useful for diagnosing differences between model-free App Server commands and actual model-turn tool execution. It consumes a small amount of model allowance and is not run automatically.

A turn-probe failure is evidence about the Codex environment, not a reason to bypass the host-owned Git or Unity boundaries.

## Host-owned Git handoff

Before edits, workers prepare their task branch through:

```bash
bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/git-handoff.sh" prepare \
  --branch codex/<issue-or-feature-name>
```

After implementation and validation, workers use:

```bash
bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/git-handoff.sh" handoff \
  --branch codex/<issue-or-feature-name> \
  --commit-message "<message>" \
  --pr-title "<title>" \
  --pr-body "<body>" \
  --validation-run <run-id>
```

The host validates the workspace/repository/branch, refuses force or non-fast-forward handoff, verifies required Unity evidence, stages and commits the source changes, pushes, confirms the remote SHA, creates or updates the PR, and only then removes `symphony:ready`.

See `docs/GIT_HANDOFF.md` for the complete contract.

## Unity remains separate

Unity uses its own host-owned broker because it also crosses a boundary the model turn should not control directly: WSL-to-Windows editor execution and the exclusive Unity resource lock.

Git handoff does not grant Unity access, and Unity resource ownership does not grant Git handoff authority outside the current GH workspace.

## Security rules

The Supervisor must not grant workers write access to unrelated repositories, the Supervisor checkout, host credentials, or Windows staging state.

Host-owned interfaces must remain typed and bounded. Do not replace them with a generic command-execution broker.

The Git handoff broker never merges PRs, force-pushes, resets worktrees, deletes branches, or targets a repository other than the configured RPG Kingdom repository.

## Operator escape hatch

`RPGK_SKIP_CODEX_PERMISSION_PROBE=1` exists only for diagnosis. Normal unattended operation should keep the App Server source-write proof enabled. The Symphony compatibility check is not optional.

## Upgrade rule

After upgrading Codex or Symphony:

```bash
bash tests/run.sh
bash scripts/verify-symphony-permissions-patch.sh
bash scripts/codex-app-server-permission-probe.sh
```

Then smoke-test both host interfaces before dispatching important work:

```bash
bash scripts/git-handoff.sh health --project ~/code/rpg-kingdom-symphony-workspaces/GH-N
bash scripts/unity-runner.sh health --project ~/code/rpg-kingdom-symphony-workspaces/GH-N
```

Run the legacy Git-write or model-turn environment probes only when specifically investigating a Codex sandbox/runtime change.
