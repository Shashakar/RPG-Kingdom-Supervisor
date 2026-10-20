# Phase 2 — Claude execution pilot and Rekindle target migration

## Architecture decision

Official Symphony currently speaks **Codex App Server**, not the Claude Code CLI protocol. Pointing `codex.command` at `claude -p` would fail protocol negotiation. Phase 2 therefore adds an **operator-started Claude pilot** alongside Symphony. It does **not** claim unattended Claude issue dispatch or app-server compatibility. See [Claude headless CLI reference](https://code.claude.com/docs/en/headless).

The active Rekindle development baseline is **`rekindle-vertical-slice`**, not its minimal `main` branch. Open PRs #3 and #4 are also based on `rekindle-vertical-slice`. Supervisor's active migration profile chooses this as the Git clone branch, PR base and integration ancestry check.

## Two host profiles

| Profile | Tracker | Integration base | Workspace root | State root |
|---|---|---|---|---|
| `rekindle` (new default) | `Shashakar/Rekindle-Claude` | `rekindle-vertical-slice` | `~/code/rekindle-symphony-workspaces` | `~/.local/state/rekindle-supervisor` |
| `rpg-kingdom` (rollback) | `Shashakar/RPG-Kingdom` | `main` | `~/code/rpg-kingdom-symphony-workspaces` | `~/.local/state/rpg-kingdom-supervisor` |

`scripts/run-symphony.sh` selects the workflow file with `RPGK_TARGET_PROFILE`, exports matching `RPGK_REPO`, `RPGK_REPO_OWNER`, `RPGK_REPO_NAME`, `RPGK_BASE_BRANCH`, and workspace/state paths before launching host services. This prevents same-number GH issues from sharing working directories or worker state. Running both profiles against the same Unity Editor is **not supported**; shut down one Supervisor before starting another.

## Prerequisite: merge the Rekindle contract PR

1. Review and merge the separate `AGENTS.md` PR into **`rekindle-vertical-slice`**, not `main`. Verify that the target's `README.md` and `Docs/ARCHITECTURE.md` are present at the same ref.
2. Review and merge this Supervisor PR after CI is green; do not move any existing RPG Kingdom workspaces or copy host state into the new target.
3. Stop the original Supervisor and confirm that there are no active Codex/Unity/Git broker requests, queued rearm transitions or unmerged working diffs needing attention. Preserve the old workspace and state directories for rollback.
4. On the WSL host update `~/src/RPG-Kingdom-Supervisor`, verify the configured GitHub token and `gh` access to Rekindle, and register labels: `RPGK_REPO=Shashakar/Rekindle-Claude bash scripts/install-labels.sh`.
5. Confirm `git ls-remote --heads https://github.com/Shashakar/Rekindle-Claude.git rekindle-vertical-slice`, then start with `RPGK_TARGET_PROFILE=rekindle bash scripts/run-symphony.sh`. Do not add `symphony:ready` to any issue until you have inspected the new target dashboard and an isolated dry-run workspace.
6. For rollback, **stop** the Rekindle process, then use `RPGK_TARGET_PROFILE=rpg-kingdom bash scripts/run-symphony.sh`. Never run both instances concurrently against the same host resources.

## Important migration blocks

- **Production Unity editing and Unity-required work remain disabled** for the new target. `scripts/rekindle-migration-preflight.sh` fails before Codex if labels include `resource:unity-editor`, `validation:unity-required`, or `authoring:*` unless the operator has separately validated the Rekindle-specific runner and set `RPGK_REKINDLE_UNITY_READY=1`. Do not use that override merely to bypass a failing task.
- **PlayMode save isolation is not yet proven**. Do not run any destructive real-save tests or regeneration commands until separately fixed and verified.
- Target-repository `AGENTS.md` must be tracked in HEAD, and the origin must match `Shashakar/Rekindle-Claude`.
- Existing Codex model routing, host Git handoff, review/rework and human merge gate stay in place; new PRs use the target's configured base branch.
- Phase 1 provider guard still rejects a Claude-assigned `symphony:ready` issue. Claude pilot execution is **not** driven by the same label yet. Do not add a dispatch lease to Claude issues.

## Claude Code pilot

Requires an authenticated Claude Code CLI on the WSL host. Check the CLI and available flags from `claude --help`. The pilot never exposes the Supervisor's GitHub token to the Claude subprocess, never passes a shell string as a command, disallows Bash/network/agent tools, limits turns/elapsed time/reported API spend, writes a terminal result outside the game repo and uses the Claude CLI structured output. It **does not** infer remaining subscription quota from reported cost, and it does not automatically create PRs, merge, update issue lifecycle or acquire Unity Editor locks.

Example for an operator-approved issue (use a fresh issue workspace and ensure `AGENTS.md` is merged):

```bash
SUP=~/src/RPG-Kingdom-Supervisor
ROOT=~/code/rekindle-symphony-workspaces
mkdir -p "$ROOT"
git clone --branch rekindle-vertical-slice --single-branch \
  https://github.com/Shashakar/Rekindle-Claude.git "$ROOT/GH-123"
git -C "$ROOT/GH-123" switch -c claude/gh-123-audit
printf 'Audit the scope and report findings without changes.\n' > /tmp/rekindle-pilot-prompt.txt
python3 "$SUP/scripts/claude-worker-pilot.py" \
  --workspace "$ROOT/GH-123" --workspace-root "$ROOT" \
  --prompt-file /tmp/rekindle-pilot-prompt.txt --mode audit
```

To allow source editing, use `--mode edit --enable-edits` only after an operator checks the issue, workspace, branch and tool permissions. Edit mode allows file writes but **not Bash or automatic Git/PR handling**. Validate changes manually and submit a draft PR against `rekindle-vertical-slice` via a host-authorized path. Neither the CLI allowlist nor the prompt is an OS-level security sandbox; use a truly isolated host/container if untrusted source code or local secrets are a concern.

The pilot rejects mismatched origins, dirty initial workspaces, missing `AGENTS.md`, unexpected workspace paths, excessive time/turn/budget settings, CLI errors, missing terminal `result` events and requests to edit without explicit acknowledgment. Reports are written under `<state-root>/claude-pilot/GH-N/latest.json`. API spend telemetry may be unavailable or not representative of the user subscription plan.

## Verification

```bash
python3 tests/claude-worker-pilot-test.py
python3 tests/rekindle-migration-test.py
bash tests/run.sh
```

Before unattended Claude execution, require a separate reviewed phase with provider-native session recovery, permission mediation, resource locks, host-owned PR handoff, reliable token/quota observations, bounded review cycles, and a tested crash/timeout/cancellation story. Simply changing `codex.command` is not an acceptable shortcut.
