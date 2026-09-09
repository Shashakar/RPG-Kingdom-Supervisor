# Supervisor Setup

This document covers the operator setup for running official OpenAI Symphony against `Shashakar/RPG-Kingdom` using this repository's `WORKFLOW.md`.

## 1. Host environment

Official Symphony currently publishes macOS and Linux release targets. On Windows, use WSL2 for the evaluated setup unless you intentionally choose to build and support the Elixir runtime another way.

The Symphony host needs:

- Git
- GitHub CLI (`gh`) or another Git authentication path that can push to `Shashakar/RPG-Kingdom`
- Codex CLI with App Server support
- `mise`
- `curl`, `jq`, and `tput`
- network access to GitHub and OpenAI

Verify from the same shell that will run Symphony:

```bash
git --version
gh --version
codex --version
mise --version
curl --version
jq --version
tput colors >/dev/null 2>&1 || true
```

Also verify that normal repository operations work from that shell before involving Symphony:

```bash
git clone https://github.com/Shashakar/RPG-Kingdom.git /tmp/rpg-kingdom-supervisor-auth-check
cd /tmp/rpg-kingdom-supervisor-auth-check
git remote -v
gh auth status
cd ..
rm -rf rpg-kingdom-supervisor-auth-check
```

Do not proceed to unattended workers until the operator environment can authenticate to GitHub reliably.

## 2. Install the evaluated Symphony revision

The currently evaluated upstream revision is recorded in `SYMPHONY_UPSTREAM.md`.

```bash
git clone https://github.com/openai/symphony.git ~/src/openai-symphony
cd ~/src/openai-symphony
git checkout 8001b52e3062495a16e520e4ceaf8f9de868c4d0
cd elixir
mise trust
mise install
mise exec -- mix setup
mise exec -- mix build
```

On Ubuntu 26.04, if `mise` attempts to compile Erlang and fails on native build dependencies, the precompiled OTP route used during the evaluated setup is:

```bash
export MISE_ERLANG_COMPILE=false
export MISE_ERLANG_PRECOMPILED_OS=ubuntu-26.04
mise install
```

Do not silently replace the pinned revision with current upstream `main`. Review and deliberately update the pin first.

## 3. Configure GitHub tracker authentication

Symphony's GitHub tracker adapter uses a dedicated variable:

```text
SYMPHONY_GITHUB_TOKEN
```

Do **not** use `GITHUB_TOKEN` or `GH_TOKEN` for the Symphony tracker credential. GitHub CLI treats those names as authentication overrides, which can replace the operator's stored `gh auth` credential during clone/push/PR operations.

Use a host-side fine-grained PAT with the minimum GitHub Issues permissions required by the workflow. Do not commit it to either repository.

### Recommended persistent secret file

Keep the token scoped to the supervisor launcher instead of exporting it from every shell startup file:

```bash
mkdir -p ~/.config/rpg-kingdom-supervisor
chmod 700 ~/.config/rpg-kingdom-supervisor

read -rsp "Symphony GitHub PAT: " TOKEN
echo
printf 'export SYMPHONY_GITHUB_TOKEN=%q\n' "$TOKEN" \
  > ~/.config/rpg-kingdom-supervisor/secrets.env
unset TOKEN

chmod 600 ~/.config/rpg-kingdom-supervisor/secrets.env
```

Verify permissions without printing the secret:

```bash
ls -l ~/.config/rpg-kingdom-supervisor/secrets.env
```

Expected mode is `-rw-------`.

`scripts/run-symphony.sh` sources this file automatically. Override the location with `RPGK_SECRETS_FILE` if required.

For one-off interactive use instead:

```bash
read -s -p "Symphony GitHub token: " SYMPHONY_GITHUB_TOKEN
echo
export SYMPHONY_GITHUB_TOKEN
unset GITHUB_TOKEN
unset GH_TOKEN
```

The evaluated Symphony revision scrubs the provider secret environment reference before it execs the Codex App Server command. The model router therefore does not receive the narrow tracker PAT.

### Separate Git push/PR authentication

The tracker token is not the worker's Git credential.

Configure Git/GitHub CLI authentication independently with `gh auth login` and `gh auth setup-git`, then verify it in the same WSL/Linux environment used by Symphony:

```bash
gh auth status
git clone https://github.com/Shashakar/RPG-Kingdom.git /tmp/rpg-kingdom-supervisor-auth-check
rm -rf /tmp/rpg-kingdom-supervisor-auth-check
```

The model router uses this normal `gh` authentication only to read routing labels for the issue being launched.

## 4. Clone/update this supervisor repository

```bash
git clone https://github.com/Shashakar/RPG-Kingdom-Supervisor.git ~/src/RPG-Kingdom-Supervisor
cd ~/src/RPG-Kingdom-Supervisor
git switch main
git pull --ff-only
```

The runtime expects this default path. If you intentionally keep it elsewhere, set:

```bash
export RPGK_SUPERVISOR_ROOT=/absolute/path/to/RPG-Kingdom-Supervisor
```

The tracked launcher can be run with `bash scripts/run-symphony.sh` even if the checkout does not preserve executable mode. `chmod +x scripts/run-symphony.sh` is optional convenience.

## 5. Install routing and Unity scheduling labels

Install/update the full label set with:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/install-labels.sh
```

Dispatch/runtime labels:

```text
symphony:ready
symphony:halted
```

Risk labels:

```text
risk:mechanical
risk:normal
risk:investigative
risk:architecture
risk:end-to-end
```

Explicit model overrides:

```text
model:luna
model:terra
model:sol
model:astra
```

Reasoning overrides:

```text
effort:low
effort:medium
effort:high
```

Phase 3 Unity scheduling labels:

```text
resource:unity-editor
validation:unity-required
validation:unity-optional
```

The only label that authorizes dispatch is `symphony:ready`. Other labels choose execution/resource policy but do not authorize work by themselves.

Avoid more than one risk, model, or effort label. `validation:unity-required` and `validation:unity-optional` are mutually exclusive. Required Unity validation also requires `resource:unity-editor`. Conflicts fail closed.

## 6. Validate the supervisor locally

Before restarting Symphony after a supervisor update:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash tests/run.sh
```

Expected visible output includes:

```text
routing-policy-test: PASS
after-run-guard-test: PASS
unity-resource-policy-test: PASS
unity-resource-guard-test: PASS
supervisor-tests: PASS
```

The aggregate runner also executes the Codex-router and local worker-lifetime guard tests with their individual PASS output suppressed.

You can inspect how an existing issue would route without starting Codex by entering its workspace and using router dry-run mode:

```bash
cd ~/code/rpg-kingdom-symphony-workspaces/GH-<number>
RPGK_ROUTER_DRY_RUN=1 bash ~/src/RPG-Kingdom-Supervisor/scripts/codex-app-server-router.sh
```

## 7. Model/risk policy

Default routes:

| Issue policy | Model | Effort |
|---|---|---|
| `risk:mechanical` | GPT-5.6 Luna | low |
| `risk:normal` | GPT-5.6 Luna | medium |
| `risk:investigative` | GPT-5.6 Terra | medium |
| `risk:architecture` | GPT-5.6 Sol | high |
| `risk:end-to-end` | GPT-6 Astra | medium |
| no risk/model label | GPT-5.6 Luna | medium |

`model:*` labels override model selection. `effort:*` labels override reasoning effort.

Use `risk:normal` for bounded implementation, straightforward bugs, and focused refactors. Use `risk:investigative` when multiple plausible causes or several runtime/test layers make Terra's higher allowance cost likely to save iterations. Astra remains reserved for difficult end-to-end work rather than merely important work.

## 8. Phase 3 Unity scheduling policy

Use no Unity labels for ordinary code-only work.

Use:

```text
validation:unity-optional
```

when a reviewable implementation can be created without Unity but missing editor validation must be explicit in the PR.

Use both:

```text
resource:unity-editor
validation:unity-required
```

when Codex must not start unless the host can provide exclusive, healthy Unity execution.

Phase 3 intentionally does not provide that runner yet. Keep this unset unless the Phase 4 runner has actually passed its health check:

```text
RPGK_UNITY_RUNNER_READY=1
```

Do not set it merely to bypass preflight. Until Phase 4 is complete, required Unity dispatches are expected to halt before Codex and be marked `symphony:halted`.

The exclusive lock is stored under:

```text
~/.local/state/rpg-kingdom-supervisor/locks/unity-editor.lock/
```

See `docs/PHASE3_UNITY_SCHEDULING.md` for the full contract.

## 9. Start Symphony

Preferred startup:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/run-symphony.sh
```

The launcher:

- loads the permission-restricted supervisor secrets file;
- validates the required local paths/tools;
- starts the pinned Symphony runtime with this repository's `WORKFLOW.md`;
- uses the terminal alternate-screen buffer when available so repeated status refreshes do not flood normal scrollback;
- restores the normal terminal when Symphony exits.

Set `RPGK_ALTERNATE_SCREEN=0` if you intentionally want ordinary terminal output/scrollback.

The workflow currently allows one concurrent worker and a maximum of four Codex turns per worker lifetime.

The observability HTTP dashboard remains optional. If intentionally enabled, keep it loopback-only and review the pinned dependency/security posture first.

## 10. Dispatch an issue

Before dispatch:

1. make the issue complete enough for unattended implementation;
2. assign one risk label when known;
3. add model/effort overrides only for a concrete reason;
4. classify Unity validation/resource needs;
5. then add `symphony:ready`.

Example code-only bounded dispatch:

```bash
gh issue edit <number> \
  --repo Shashakar/RPG-Kingdom \
  --add-label "risk:normal" \
  --add-label "symphony:ready"
```

Expected success path:

1. Symphony sees the open issue with `symphony:ready`.
2. It creates/reuses the isolated workspace and clones RPG Kingdom when new.
3. `before-run-guard.sh` verifies the dispatch has not already consumed a worker lifetime.
4. `unity-resource-guard.sh` validates Unity policy and acquires the editor lock if requested/available.
5. `codex-app-server-router.sh` reads route labels and launches the selected model/effort.
6. Codex follows RPG Kingdom instructions, creates a `codex/` branch, implements, validates available paths, pushes, and opens/updates a PR.
7. After the reviewable PR exists, Codex removes `symphony:ready`.
8. `release-unity-resource.sh` releases the Unity lock if this issue owns it.
9. `after-run-guard.sh` records the completed worker lifetime; successful PR handoff needs no further tracker mutation.
10. The issue remains open and the PR waits for human/ChatGPT review.

## 11. Budget exhaustion / failed handoff

The supervisor does not allow a still-routable issue to roll directly into another fresh Codex worker lifetime.

At the end of every worker lifetime, `scripts/after-run-guard.sh` writes `.symphony-attempt-complete` inside the persistent issue workspace before making any GitHub request. A subsequent worker reaches `scripts/before-run-guard.sh` and stops before Codex App Server launches.

If `symphony:ready` remains, the after-run guard removes it, adds `symphony:halted`, and leaves an explanatory comment.

Unity preflight failures happen even earlier: invalid required/optional labels, required Unity without the resource label, unavailable required Unity infrastructure, or a busy editor lock halt before Codex starts.

Before retrying, inspect the PR/workspace/Symphony and Codex logs and decide whether another bounded run is justified. Then explicitly rearm:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/rearm-issue.sh <number>
```

Change risk/model/effort/Unity labels before rearming when prior policy was inappropriate. Do not automate this retry loop.

## 12. Benchmarks

Issue #91 is the Phase 1 baseline. Issues #93 and #95 provide the Phase 2 measured routes documented in `PHASE2_BUDGETED_ROUTING.md`.

Important operator signals are:

- Symphony turn count and worker lifetime count;
- cached vs uncached input rather than raw cumulative dashboard totals alone;
- five-hour and weekly allowance movement;
- whether the selected model materially improved the result enough to justify its cost;
- whether Unity-required work was blocked before Codex when the host resource was unavailable.

Use real work to validate Sol and Astra when those classes naturally occur rather than spending allowance on synthetic benchmarks.

## 13. Still deferred

Phase 3 does not add:

- a Windows Unity runner or automatic Unity execution;
- more than one concurrent worker;
- automatic merge;
- automatic PR feedback re-dispatch;
- automatic retries after `symphony:halted`.
