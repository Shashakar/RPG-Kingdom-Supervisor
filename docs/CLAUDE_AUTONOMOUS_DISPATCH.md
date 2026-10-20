# Phase 3 — opt-in automatic Claude issue execution

Claude Code is **not** a Codex App Server. This implementation runs Claude in a separate host-owned service child and deliberately keeps Symphony's `symphony:ready` queue Codex-only. The bounded Claude CLI pilot provides the tool-permission restrictions, model routing, output parsing, timeouts and per-issue/mutation file locks. The host owns cloning, Git branches, commits, pushes, draft PRs and independent review. No automatic merges.

## Dispatch protocol

Claude issues must have **both** `agent:claude` and `symphony:claude-ready`; do **not** add `symphony:ready`. The dedicated queue is never observed by Symphony's Codex tracker. Provider-neutral `agent:auto` and `work:visual-unity` do not automatically become Claude jobs; explicit provider opt-in is required. With `RPGK_CLAUDE_AUTONOMOUS` unset, the entire new dispatcher is disabled.

The dispatcher handles **one candidate per pass**, polling every 30 seconds when enabled. It accepts only `Shashakar/Rekindle-Claude` on `rekindle-vertical-slice`, and rejects simultaneous Codex ready leases, conflicting providers, human/review/halted gates, report-only work, Unity Editor/Unity-required labels, and **all** `authoring:*` labels. The Rekindle preflight runs with its Unity override forcibly set to `0`. Claude cannot use Bash, network, git, nested agents, or MCP tools. Changes to production scene/prefab/asset/meta, ProjectSettings, packages, secrets/policy files or workflow configuration are blocked at the host side before any PR.

On first dispatch it creates/reuses `~/code/rekindle-symphony-workspaces/GH-N`, validates the exact GitHub origin and clean status, runs the existing host Git broker `prepare` on `claude/gh-N-implementation`, then executes `scripts/claude-worker-pilot.py` with selected Haiku/Sonnet/Opus and effort labels. On successful work it uses the same host Git handoff broker to commit/push and open a **draft** PR against `rekindle-vertical-slice`. The existing review orchestrator takes over for agent review; approval leads to `symphony:human-review`, *not merge*. Rework within the existing two-automatic-repair limit dispatches `symphony:claude-ready` instead of the Codex queue. Manual `scripts/rearm-issue.sh` also uses the Claude queue when `agent:claude` is present.

Each attempt writes `~/.local/state/rekindle-supervisor/claude-dispatch/GH-N.json`, and the pilot writes `claude-pilot/GH-N/latest.json`. A restart never automatically replays an interrupted edit: it stops for human inspection. If a host handoff had already pushed an **advanced, matching** PR head, the dispatcher can re-queue review and remove a stale lease without rerunning Claude. Failed or timed-out attempts get `symphony:halted`, lose their ready lease, and require an explicit human `symphony:rearm`; any dirty working tree is **preserved**, never force reset. External CLI permissions are not an OS sandbox; run untrusted code in an isolated container/VM.

### Rollout on Dex-GMKTec-Server

1. Merge the PR after CI review, then `cd ~/src/RPG-Kingdom-Supervisor && git pull --ff-only`.
2. `RPGK_REPO=Shashakar/Rekindle-Claude bash scripts/install-labels.sh`.
3. Confirm host-level `claude --version`, `claude auth status`, `gh auth status`, and that the authenticated **dex system-service user** has access. Verify the CLI accepts `--model`/`--effort` on the installed version. Start with a very small, low-risk, code-only issue. Do not enable Unity scene jobs.
4. Opt in persistently only after these checks. Create a separate systemd drop-in (leave the `90-rekindle-target.conf` unchanged):

```bash
sudo install -d -m 0755 /etc/systemd/system/rpg-kingdom-supervisor.service.d
printf '[Service]\nEnvironment=RPGK_CLAUDE_AUTONOMOUS=1\n' | \
  sudo tee /etc/systemd/system/rpg-kingdom-supervisor.service.d/95-claude-autonomous.conf
sudo systemctl daemon-reload
# Verify no worker is currently active before intentionally restarting:
sudo systemctl restart rpg-kingdom-supervisor.service
sudo systemctl show rpg-kingdom-supervisor.service -p Environment --no-pager
sudo tail -n 80 /home/dex/.local/state/rekindle-supervisor/claude-dispatcher.log
```

5. Add labels `agent:claude`, `risk:mechanical`, `symphony:claude-ready` to **one intentionally chosen open issue** that changes only a safe code/test/doc path. Do not add `symphony:ready`. Watch the issue labels, dispatcher journal, pilot report, draft PR and independent review state. Confirm the review can request bounded rework and that no merge happens without human approval.

### Disable / rollback

Remove the opt-in drop-in and restart **only after** inspecting active work (the system service uses `KillMode=control-group` and can terminate a running Claude process). Existing in-flight journals/workspaces will remain for human inspection; rearming them is always an explicit choice.

```bash
sudo rm /etc/systemd/system/rpg-kingdom-supervisor.service.d/95-claude-autonomous.conf
sudo systemctl daemon-reload
sudo systemctl restart rpg-kingdom-supervisor.service
```

Do not rename `symphony:claude-ready` to `symphony:ready` as a rollback. That would allow the Codex tracker to claim Claude work. Keep both services targeting Rekindle unless deliberately reverting the entire migration.

## Limitations

- Opt-in dispatch uses an authenticated `gh` client on the system host. Failure to authenticate is a configuration error and should not be worked around by injecting tokens into Claude.
- The Claude pilot's reported API spend and `--max-budget-usd` do not measure the user's subscription quota and do not guarantee a billing ceiling; the service has fixed turns and timeout caps.
- No Unity Editor authoring, visual sign-off, direct scene modification, PlayMode save tests, unrestricted commands, or automatic PR merge.
- Changes are not compiled in the restricted Claude CLI; review and human gate must require evidence of validation, with real host-side smoke before production workload.
- The existing operator autonomous work-plan scheduler uses Codex quota floors; it is **not** the Claude dispatch scheduler. Claude selection is explicit through the separate queue.
- CI uses fake subprocess/provider responses and cannot verify actual installed Claude Code credentials or GitHub service permissions.

## Local verification

```bash
python3 tests/claude-dispatcher-test.py
python3 tests/claude-worker-pilot-test.py
python3 tests/claude-model-policy-test.py
bash tests/run.sh
```
