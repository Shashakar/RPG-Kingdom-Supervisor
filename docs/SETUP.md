# Supervisor Setup

This document covers the operator setup for running official OpenAI Symphony against `Shashakar/RPG-Kingdom` using this repository's `WORKFLOW.md`.

## 1. Host environment

Official Symphony currently publishes macOS and Linux release targets. On Windows, use WSL2 for the initial setup unless you intentionally choose to build and support the Elixir runtime another way.

The Symphony host needs:

- Git
- GitHub CLI (`gh`) or another Git authentication path that can push to `Shashakar/RPG-Kingdom`
- Codex CLI with App Server support
- `mise`
- `curl` and `jq`
- network access to GitHub and OpenAI

Verify from the same shell that will run Symphony:

```bash
git --version
gh --version
codex --version
mise --version
curl --version
jq --version
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

Symphony's GitHub tracker adapter reads the repository configured in `WORKFLOW.md`. This supervisor intentionally uses a dedicated environment variable:

```text
SYMPHONY_GITHUB_TOKEN
```

Do **not** use `GITHUB_TOKEN` or `GH_TOKEN` for the Symphony tracker credential. GitHub CLI treats those names as authentication overrides, which can replace the operator's stored `gh auth` credential during clone/push/PR operations.

Set a host-side fine-grained PAT with the minimum GitHub Issues permissions required by the workflow. Do not commit it to either repository.

For an interactive shell session without writing the token to shell history:

```bash
read -s -p "Symphony GitHub token: " SYMPHONY_GITHUB_TOKEN
echo
export SYMPHONY_GITHUB_TOKEN
unset GITHUB_TOKEN
unset GH_TOKEN
```

The evaluated Symphony revision scrubs the provider secret environment reference before it execs the Codex App Server command. The Phase 2 model router therefore does not receive the narrow tracker PAT.

### Separate Git push/PR authentication

The tracker token is not the worker's Git credential.

Configure Git/GitHub CLI authentication independently with `gh auth login` and `gh auth setup-git`, then verify it in the same WSL/Linux environment used by Symphony.

```bash
gh auth status
git clone https://github.com/Shashakar/RPG-Kingdom.git /tmp/rpg-kingdom-supervisor-auth-check
rm -rf /tmp/rpg-kingdom-supervisor-auth-check
```

The Phase 2 model router uses this normal `gh` authentication only to read routing labels for the issue being launched.

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

before starting Symphony and update the workflow command/hook path if necessary.

## 5. Create Phase 2 labels

The RPG Kingdom repository needs the following labels.

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

The only label required to make an issue dispatchable is `symphony:ready`. Risk/model/effort labels affect the Codex route but do not authorize execution by themselves.

Install or update the full label set with:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/install-labels.sh
```

Avoid applying more than one risk label, more than one model label, or more than one effort label. Conflicting routing labels intentionally cause the App Server router to fail closed.

## 6. Validate Phase 2 locally

Before starting Symphony after a supervisor update:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash tests/run.sh
```

Expected:

```text
routing-policy-test: PASS
after-run-guard-test: PASS
supervisor-tests: PASS
```

The aggregate runner also executes the Codex-router and local worker-lifetime guard tests; their individual PASS lines are intentionally suppressed to keep routine operator output concise.

You can also inspect how an existing issue would route without starting Codex by entering its workspace and using the router dry-run mode:

```bash
cd ~/code/rpg-kingdom-symphony-workspaces/GH-<number>
RPGK_ROUTER_DRY_RUN=1 bash ~/src/RPG-Kingdom-Supervisor/scripts/codex-app-server-router.sh
```

## 7. Phase 2 routing policy

Default routes:

| Issue policy | Model | Effort |
|---|---|---|
| `risk:mechanical` | GPT-5.6 Luna | low |
| `risk:normal` | GPT-5.6 Luna | medium |
| `risk:investigative` | GPT-5.6 Terra | medium |
| `risk:architecture` | GPT-5.6 Sol | high |
| `risk:end-to-end` | GPT-6 Astra | medium |
| no risk/model label | GPT-5.6 Luna | medium |

`model:*` labels override the model. `effort:*` labels override reasoning effort.

Use `risk:normal` for bounded implementation, straightforward bug fixes, focused refactors, and similar work where a strong first attempt does not require broad root-cause exploration. Use `risk:investigative` when there are multiple plausible causes, several affected runtime/test layers, or enough ambiguity that a weaker first attempt is likely to waste more allowance than Terra saves.

Astra is deliberately reserved for difficult end-to-end work. Do not use `risk:end-to-end` as a synonym for "important"; importance alone does not justify the allowance cost.

## 8. Start Symphony

From the installed Symphony `elixir` directory:

```bash
cd ~/src/openai-symphony/elixir

mise exec -- ./bin/symphony \
  --i-understand-that-this-will-be-running-without-the-usual-guardrails \
  ~/src/RPG-Kingdom-Supervisor/WORKFLOW.md
```

The workflow currently allows one concurrent worker and a maximum of four Codex turns per worker lifetime.

The observability HTTP dashboard remains optional. If you intentionally enable it, keep it loopback-only and review the currently pinned dependency/security posture first.

## 9. Dispatch an issue

Before dispatch:

1. make the issue complete enough for unattended implementation;
2. assign one risk label when the classification is known;
3. add model/effort overrides only when there is a concrete reason;
4. then add `symphony:ready`.

Example mechanical dispatch:

```bash
gh issue edit <number> \
  --repo Shashakar/RPG-Kingdom \
  --add-label "risk:mechanical" \
  --add-label "symphony:ready"
```

Expected success path:

1. Symphony sees the open issue with `symphony:ready`.
2. It creates/reuses the isolated workspace.
3. `before-run-guard.sh` verifies that this dispatch has not already consumed a worker lifetime.
4. The `after_create` hook clones RPG Kingdom using the operator Git credential when the workspace is new.
5. `codex-app-server-router.sh` reads route labels and launches the selected model/effort.
6. Codex follows RPG Kingdom's checked-in instructions, creates a `codex/` branch, implements, validates, pushes, and opens/updates a PR.
7. After the reviewable PR exists, Codex removes `symphony:ready`.
8. `after-run-guard.sh` records the completed worker lifetime locally; successful PR handoff requires no further tracker mutation.
9. The issue remains open and the PR waits for human/ChatGPT review.

## 10. Budget exhaustion / failed handoff

Phase 2 does not allow a still-routable issue to roll directly into another fresh Codex worker lifetime.

At the end of every worker lifetime, `scripts/after-run-guard.sh` writes `.symphony-attempt-complete` inside that issue's persistent workspace before it makes any GitHub request. A subsequent worker reaches `scripts/before-run-guard.sh` and stops before Codex App Server launches.

If `symphony:ready` is still present, the after-run guard also:

1. removes `symphony:ready` first;
2. adds `symphony:halted`;
3. leaves an issue comment explaining that automatic redispatch was stopped.

Before retrying, inspect the PR/workspace/Symphony and Codex logs and decide whether another bounded run is justified. Then explicitly rearm it:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/rearm-issue.sh <number>
```

The helper removes `symphony:halted` when present, clears the local completed-attempt marker, and re-adds `symphony:ready`. Change risk/model/effort labels before rearming when the prior route was inappropriate.

Do not automate this retry loop.

## 11. Phase 2 benchmark

Issue #91 is the Phase 1 baseline. Issues #93 and #95 provide the Phase 2 measured routes documented in `PHASE2_BUDGETED_ROUTING.md`.

The important operator signals are:

- Codex/Symphony turn count and worker lifetime count;
- session input split into cached and uncached tokens rather than raw cumulative Symphony totals alone;
- five-hour allowance movement;
- weekly allowance movement;
- whether the selected model materially improved the result enough to justify its cost.

Use real work to validate Sol and Astra when those task classes naturally occur rather than spending allowance on synthetic benchmarks.

## 12. Still deferred

Phase 2 does not add:

- more than one concurrent worker;
- Unity Editor resource locks;
- automatic Unity execution;
- automatic merge;
- automatic PR feedback re-dispatch;
- automatic retries after `symphony:halted`.
