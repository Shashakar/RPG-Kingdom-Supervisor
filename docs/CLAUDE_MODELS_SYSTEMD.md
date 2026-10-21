# Claude model routing and systemd service migration

## What this actually enables

Claude Code model routing is **independent** of the existing Codex Luna/Sol routing. The Claude adapter runs through the **normal Rekindle service** and consumes the separate `symphony:claude-ready` queue to avoid racing Symphony's Codex tracker. Model labels select the Claude CLI model and effort. Code/Unity authorization, Git handoff and review rules are the same for Claude and Codex; see [dispatch parity](CLAUDE_AUTONOMOUS_DISPATCH.md). Keep Claude-assigned issues out of the Codex dispatch lease.

### Claude routing policy

| Task classification (without override) | Claude model alias | CLI effort |
|---|---|---|
| `risk:mechanical` | `haiku` | `low` |
| `risk:normal` or no classification | `sonnet` | `medium` |
| `work:visual-unity` (without risk override) | `sonnet` | `high` |
| `risk:investigative` | `opus` | `medium` |
| `risk:architecture` or `risk:end-to-end` | `opus` | `high` |

Explicit labels: `claude-model:haiku`, `claude-model:sonnet`, `claude-model:opus`; `claude-effort:low`, `claude-effort:medium`, `claude-effort:high`. They are independent of `model:luna`, `model:sol`, and `effort:*` for Codex. Conflicting or unknown Claude model/effort labels and conflicting risk labels **fail closed**, even if the operator supplies CLI overrides. CLI `--model` / `--effort` overrides otherwise take precedence over single labels, which take precedence over the default classification. Requested aliases are sent to Claude Code with `--model` and `--effort` and included in result metadata. No automatic fallback to a different provider/model is configured.

Aliases intentionally follow Claude Code's installed model resolver. Run `claude --version`, `claude auth status` and `claude --help` on the actual host. Model availability, subscription quota and support for effort levels depend on the installed CLI/account. The CLI `--max-budget-usd` checks estimated API spend and is **not a subscription allowance gauge or a guaranteed hard billing ceiling**.

Register the newly added issue labels in Rekindle (idempotent):

```bash
cd ~/src/RPG-Kingdom-Supervisor
RPGK_REPO=Shashakar/Rekindle-Claude bash scripts/install-labels.sh
```

Example **operator-approved** audit for an existing isolated GH-123 workspace:

```bash
labels=$(mktemp)
gh issue view 123 --repo Shashakar/Rekindle-Claude --json labels > "$labels"
python3 scripts/claude-worker-pilot.py \
  --workspace "$HOME/code/rekindle-symphony-workspaces/GH-123" \
  --workspace-root "$HOME/code/rekindle-symphony-workspaces" \
  --prompt-file "$HOME/rekindle-pilot-prompt.txt" \
  --labels-json-file "$labels" --mode audit
rm -f "$labels"
```

Use `--model sonnet --effort high` only for an explicit operator override. Editing still requires a clean dedicated `claude/*` branch and `--mode edit --enable-edits`; neither CLI mode auto-creates a PR or merges code.

## The actual systemd units

On Dex-GMKTec-Server, this is a **system** service (`sudo systemctl`), not `systemctl --user`. Existing units:

- `rpg-kingdom-supervisor.service` runs as `dex`, with ExecStart calling `/home/dex/src/RPG-Kingdom-Supervisor/scripts/run-symphony.sh`.
- `rpg-kingdom-diagnostics.service` runs the separate dashboard from the Supervisor checkout. It needs matching Rekindle repository, state and workspace environment variables.

Keep both unit names and their ExecStart paths. Supervisor's executable belongs in its own checkout; **do not point either service into the Unity game repository**. The checked-in drop-ins set the Supervisor profile and the dashboard's target context. The service installer only writes those drop-ins and invokes `systemctl daemon-reload`; it **never restarts or disables any service automatically**.

### Safe rollout

1. Merge the PR and update the **Supervisor checkout**, leaving Rekindle's game checkout separate.
2. Inspect current service state: `sudo systemctl status rpg-kingdom-supervisor.service rpg-kingdom-diagnostics.service --no-pager`, then `sudo systemctl cat rpg-kingdom-supervisor.service` and `sudo systemctl cat rpg-kingdom-diagnostics.service`. Check for extra unit drop-ins/EnvironmentFiles that might override the new profile.
3. Install (but don't restart):

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/install-rekindle-service-profile.sh --apply
```

4. **Exit the manual `run-symphony.sh` foreground process first.** Never run both a terminal Supervisor and the systemd Supervisor concurrently: they can otherwise reuse Unity/Git brokers while launching duplicate review/scheduler children. The launcher now acquires a cross-profile nonblocking host ownership lock; a second launch fails with status 75.
5. After any current work is complete and the manual launcher is stopped, restart the two services:

```bash
sudo systemctl restart rpg-kingdom-supervisor.service
sudo systemctl restart rpg-kingdom-diagnostics.service
sudo systemctl show rpg-kingdom-supervisor.service -p User -p Environment -p MainPID
sudo systemctl show rpg-kingdom-diagnostics.service -p Environment -p MainPID
sudo systemctl status rpg-kingdom-supervisor.service rpg-kingdom-diagnostics.service --no-pager
sudo journalctl -u rpg-kingdom-supervisor.service -n 80 --no-pager
```

6. Verify the dashboard shows Rekindle issues and state, that no second foreground instance exists, and that the new Supervisor process does not refuse startup due to a live global host lock. The old RPG Kingdom state/workspaces are retained. Unity-required/scene-authoring Rekindle tasks must remain blocked until target-specific runner and save isolation have been validated.

### Rollback

Stop the service and manual processes first. Change the Supervisor profile drop-in to `Environment=RPGK_TARGET_PROFILE=rpg-kingdom`, change the diagnostics repo/state/workspace variables to the old RPG Kingdom values (or restore the previous diagnostics override), then `sudo systemctl daemon-reload` and restart each unit. Do **not** merely remove the Supervisor override: the launcher itself defaults to `rekindle`.

## Validation

```bash
python3 tests/claude-model-policy-test.py
python3 tests/claude-worker-pilot-test.py
python3 tests/service-profile-test.py
bash tests/run.sh
```

Host-side smoke is still necessary for actual systemd and Claude authentication behavior; CI mocks Claude and does not have access to the user's installed system service or subscription quota.
