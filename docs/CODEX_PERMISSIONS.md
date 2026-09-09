# Codex Worker Permissions

## Problem

RPG Kingdom workers must perform ordinary local Git operations: fetch existing branches, switch/merge, stage, commit, and push. Codex's legacy `workspace-write` sandbox deliberately protects `.git` metadata as read-only, so source files can be edited while commands such as `git fetch`, `git add`, and `git commit` fail on `.git/FETCH_HEAD` or `.git/index.lock`.

Issue #97 exposed four distinct layers of this mismatch:

1. Symphony originally sent the legacy `workspace-write` sandbox on every App Server thread/turn;
2. after Symphony was taught to forward a named permission profile, the worker still resolved to an effective policy with `.git = read`;
3. the original direct `codex sandbox` probe did not prove the App Server profile-selection path used by Symphony;
4. even after App Server `command/exec` proved real Git metadata writes under the named profile, the actual model-backed turn shell still observed `.git` as read-only and WSL-to-Windows interop failed before Unity could start.

The fourth finding is important: model-free App Server command execution and model-backed turn tool execution are not interchangeable evidence on the evaluated Codex build. Passing startup checks prove the configured host/App Server seams, but they do not by themselves prove that a model turn can safely own Git metadata or WSL interop.

## Policy

The Supervisor uses Codex's named permission-profile model rather than `danger-full-access`.

The shared profile is defined by `scripts/codex-permission-profile.sh`. It is explicit instead of inheriting `:workspace`:

- `:root = read` keeps the host readable without granting host writes;
- `:workspace_roots/. = write` grants ordinary writes inside the active issue workspace;
- `:workspace_roots/.git = write` requests a narrow Git metadata write boundary;
- `/tmp` and the configured temp directory remain writable for tool scratch space;
- network access is enabled for GitHub/package operations;
- `.agents` and `.codex` receive no narrower write grant and therefore remain protected by Codex's metadata-write rules;
- unrestricted host filesystem access and `danger-full-access` are not granted.

The profile is intentionally not implemented as `extends = ":workspace"`. Codex's built-in workspace profile contributes protected metadata rules for `.git`, `.agents`, and `.codex`; GH-97 demonstrated that depending on inheritance plus a child `.git` override did not produce the required worker behavior on the evaluated Codex build.

The model router defines this profile for `codex app-server` through trusted host-side `--config` overrides. `WORKFLOW.md` names `rpgk_supervisor_workspace` through the local Symphony compatibility seam so `thread/start` and `turn/start` select `permissions` instead of sending mutually exclusive legacy sandbox overrides.

## Why Symphony needs a compatibility transform

The evaluated Symphony revision defaults `codex.thread_sandbox` to `workspace-write` and synthesizes a `workspaceWrite` turn policy when no explicit turn policy is configured. Its upstream App Server client always sends those values. Codex App Server 0.153.4 supports a named `permissions` field on both `thread/start` and `turn/start`, and that field cannot be combined with the legacy sandbox field on the same request.

The pinned upstream Symphony revision does not expose that named-profile field in `WORKFLOW.md`, so configuration alone cannot express the required boundary. The Supervisor therefore carries a narrow, deterministic source transform in `scripts/patch-symphony-named-permissions.py`. It is intentionally anchored to the evaluated upstream source and fails if those anchors no longer match. The transform adds a generic `codex.permissions` setting and preserves Symphony's legacy sandbox behavior as the fallback when no named profile is configured.

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

`scripts/run-symphony.sh` fails closed if that compatibility seam is missing, so a future upstream checkout/pull cannot silently return to the legacy request fields.

## Model-free startup proofs

Three independent checks protect unattended dispatch from known configuration regressions:

1. `scripts/verify-symphony-permissions-patch.sh` proves the pinned Symphony source can propagate `codex.permissions` and choose it instead of legacy sandbox request fields.
2. `scripts/codex-git-write-probe.sh` performs a model-free sandbox test against a disposable Git repository and proves the configured profile can execute `git add` and `git commit` through the standalone sandbox path.
3. `scripts/codex-app-server-permission-probe.sh` performs App Server initialize + ephemeral `thread/start`, verifies `ThreadStartResponse.activePermissionProfile.id`, then uses App Server `command/exec` with the same named profile to write `.git/FETCH_HEAD`, update Git config/index/object/ref state, and make a real commit.

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
RPG Kingdom Codex App Server permission probe: PASS (active=rpgk_supervisor_workspace, git_write=ok)
```

These checks are deliberately model-free and do not consume Codex model allowance.

## Model-backed turn proof

GH-97 demonstrated that the checks above can all pass while the model-backed shell still fails. For that reason the Supervisor also provides an explicit diagnostic probe:

```bash
bash scripts/codex-turn-environment-probe.sh --run
```

This starts one tiny Luna/low turn in a disposable repository and asks it to run one deterministic script. The script records:

- whether the model-turn shell can write `.git/FETCH_HEAD` and complete `git add` + `git commit`;
- whether the same shell can invoke `cmd.exe`, which is the minimal WSL-to-Windows capability required before the Unity bridge can work.

The probe is never run automatically because it consumes a small amount of model allowance. See `docs/DIAGNOSTICS.md` for usage and interpretation.

A model-turn failure does **not** justify broadening to `danger-full-access`. If the turn cannot safely own Git metadata or Windows interop, prefer narrow host-owned Supervisor seams for those capabilities instead.

## Security boundary

Writable Git metadata is useful because Git state is part of the issue workspace's implementation contract, but the model does not inherently need unrestricted ownership of that metadata. A host-side Git seam is acceptable if the model-turn sandbox cannot provide the narrow write behavior reliably.

Similarly, Unity remains a separate host-owned resource. If WSL interop is blocked inside the model sandbox, Unity should move behind a bounded host-owned bridge rather than granting the worker unrestricted host execution.

The Supervisor must not authorize writes to unrelated repositories, the Supervisor checkout, host credentials, or the Windows Unity staging project merely to make a diagnostic probe pass.

## Operator escape hatch

`RPGK_SKIP_CODEX_PERMISSION_PROBE=1` exists only as an operator escape hatch for diagnosis; normal unattended operation should not skip the model-free Codex proofs. The Symphony compatibility check is not optional.

## Upgrade rule

Codex owns the permission-profile schema and App Server request contract. Symphony owns the orchestration request construction. After upgrading either one:

```bash
bash tests/run.sh
bash scripts/verify-symphony-permissions-patch.sh
bash scripts/codex-git-write-probe.sh
bash scripts/codex-app-server-permission-probe.sh
```

If the model-backed execution boundary is relevant to the upgrade, run the explicit turn probe once as well:

```bash
bash scripts/codex-turn-environment-probe.sh --run
```

If upstream Symphony gains first-class named-permission support, remove the local transform rather than maintaining duplicate behavior. Do not rearm a halted implementation issue after a Codex or Symphony upgrade until the applicable permission checks are understood.
