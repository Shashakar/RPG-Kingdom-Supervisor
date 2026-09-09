# Supervisor Setup

This document covers the evaluated Windows/WSL setup for running official OpenAI Symphony against `Shashakar/RPG-Kingdom` using this repository's `WORKFLOW.md`.

## 1. Host environment

Symphony runs in WSL2 Ubuntu. Unity remains a Windows application.

The WSL host needs:

- Git
- GitHub CLI (`gh`) with push/PR access to `Shashakar/RPG-Kingdom`
- Codex CLI with App Server support
- `mise`
- `curl`, `jq`, and `tput`
- `wslpath`
- Windows PowerShell reachable as `powershell.exe`
- network access to GitHub and OpenAI

Verify:

```bash
git --version
gh --version
codex --version
mise --version
curl --version
jq --version
command -v wslpath
command -v powershell.exe
tput colors >/dev/null 2>&1 || true
```

Normal Git operations must work before involving Symphony:

```bash
git clone https://github.com/Shashakar/RPG-Kingdom.git /tmp/rpg-kingdom-supervisor-auth-check
cd /tmp/rpg-kingdom-supervisor-auth-check
git remote -v
gh auth status
cd ..
rm -rf rpg-kingdom-supervisor-auth-check
```

## 2. Install the evaluated Symphony revision

The evaluated upstream revision is recorded in `SYMPHONY_UPSTREAM.md`.

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

On Ubuntu 26.04, if `mise` tries to compile Erlang and native dependencies are unavailable:

```bash
export MISE_ERLANG_COMPILE=false
export MISE_ERLANG_PRECOMPILED_OS=ubuntu-26.04
mise install
```

Do not silently replace the pinned Symphony revision with upstream `main`.

## 3. Configure GitHub tracker authentication

Symphony uses a dedicated tracker credential:

```text
SYMPHONY_GITHUB_TOKEN
```

Do **not** use `GITHUB_TOKEN` or `GH_TOKEN` for this PAT; those names override normal GitHub CLI credentials and can break clone/push access.

Recommended persistent setup:

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

`scripts/run-symphony.sh` loads that file automatically. Keep normal Git/PR authentication separate through `gh auth login` / `gh auth setup-git`.

## 4. Clone/update the supervisor

```bash
git clone https://github.com/Shashakar/RPG-Kingdom-Supervisor.git ~/src/RPG-Kingdom-Supervisor
cd ~/src/RPG-Kingdom-Supervisor
git switch main
git pull --ff-only
```

Override a nonstandard supervisor location with `RPGK_SUPERVISOR_ROOT`.

## 5. Install routing and Unity labels

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/install-labels.sh
```

Dispatch/runtime:

```text
symphony:ready
symphony:halted
```

Risk:

```text
risk:mechanical
risk:normal
risk:investigative
risk:architecture
risk:end-to-end
```

Model/effort overrides:

```text
model:luna
model:terra
model:sol
model:astra
effort:low
effort:medium
effort:high
```

Unity scheduling:

```text
resource:unity-editor
validation:unity-required
validation:unity-optional
```

Only `symphony:ready` authorizes execution. Other labels select policy.

## 6. Validate the supervisor locally

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
unity-runner-policy-test: PASS
unity-runner-host-syntax-test: PASS
rearm-issue-test: PASS
supervisor-tests: PASS
```

On a non-WSL host the PowerShell syntax check may report `SKIP`; the evaluated Windows/WSL host should report `PASS`.

## 7. Model/risk policy

| Issue policy | Model | Effort |
|---|---|---|
| `risk:mechanical` | GPT-5.6 Luna | low |
| `risk:normal` | GPT-5.6 Luna | medium |
| `risk:investigative` | GPT-5.6 Terra | medium |
| `risk:architecture` | GPT-5.6 Sol | high |
| `risk:end-to-end` | GPT-6 Astra | medium |
| no risk/model label | GPT-5.6 Luna | medium |

Use Terra when ambiguity/multiple runtime layers justify its measured higher allowance cost. Sol remains architecture-oriented. Astra remains reserved for genuinely difficult end-to-end/tool-heavy work.

## 8. Unity scheduling policy

Ordinary code-only work needs no Unity labels.

If Unity validation is useful but not mandatory:

```text
validation:unity-optional
```

If the worker should actually get editor access but the result is still optional, add:

```text
resource:unity-editor
validation:unity-optional
```

If a clean PR requires Unity evidence:

```text
resource:unity-editor
validation:unity-required
```

`validation:unity-required` requires the resource label. Required and optional validation labels are mutually exclusive.

The editor lock is stored under:

```text
~/.local/state/rpg-kingdom-supervisor/locks/unity-editor.lock/
```

## 9. Phase 4 Unity host requirements

RPG Kingdom declares its editor version in:

```text
ProjectSettings/ProjectVersion.txt
```

The runner reads that file dynamically. On the evaluated host, RPG Kingdom currently declares `6000.3.10f1`, installed at:

```text
C:\Program Files\Unity\Hub\Editor\6000.3.10f1\Editor\Unity.exe
```

Verify Windows directly if needed:

```powershell
Get-ChildItem "C:\Program Files\Unity\Hub\Editor" -Directory |
  Select-Object -ExpandProperty Name

Test-Path "C:\Program Files\Unity\Hub\Editor\6000.3.10f1\Editor\Unity.exe"
```

The WSL/Windows path bridge should also work:

```bash
cd ~/code
echo "Linux:   $PWD"
echo "Windows: $(wslpath -w "$PWD")"
```

### Runner health check

From any RPG Kingdom checkout/workspace:

```bash
cd /path/to/RPG-Kingdom
bash ~/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh health
```

A healthy result is compact JSON similar to:

```json
{"status":"ready","unityVersion":"6000.3.10f1","unityPath":"C:\\Program Files\\Unity\\Hub\\Editor\\6000.3.10f1\\Editor\\Unity.exe","sourceProject":"...","stageProject":"C:\\Users\\...\\AppData\\Local\\RPGKingdomSupervisor\\UnityStages\\6000.3.10f1\\RPG-Kingdom"}
```

The health check verifies the real editor and staging path. **Do not set `RPGK_UNITY_RUNNER_READY=1`; Phase 4 no longer uses that placeholder.**

### Windows-local staging project

The default stage is:

```text
%LOCALAPPDATA%\RPGKingdomSupervisor\UnityStages\<UnityVersion>\RPG-Kingdom
```

PowerShell/robocopy mirrors only:

```text
Assets/
Packages/
ProjectSettings/
```

The staging `Library/` survives between issue runs. The first test for a Unity version can therefore be slow while Unity imports the project; later runs should reuse the import cache.

If staging becomes corrupt, stop Symphony/Unity and delete the affected version directory. The next run recreates it from the authoritative WSL workspace.

## 10. Unity runner commands

`health` does not require the editor lock because the preflight calls it before lock acquisition.

Actual tests require the current workspace to own `resource:unity-editor`:

```bash
bash ~/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh editmode
bash ~/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh playmode
```

Prefer targeted filters:

```bash
bash ~/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh editmode \
  --filter 'RPGKingdom.Tests.EditMode.Inventory'

bash ~/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh playmode \
  --filter 'RPGKingdom.Tests.PlayMode.Inventory'
```

Unity receives its native `-testFilter` argument. Test artifacts are copied back to:

```text
Logs/SymphonyUnity/<run-id>/results.xml
Logs/SymphonyUnity/<run-id>/Editor.log
Logs/SymphonyUnity/<run-id>/summary.json
```

`Logs/` is ignored by RPG Kingdom and these files must not be committed.

The runner fails when Unity exits nonzero, no result XML is produced, zero tests match, the XML cannot be parsed, or tests fail.

## 11. Start Symphony

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/run-symphony.sh
```

The launcher loads the scoped secret, validates local prerequisites, launches the pinned Symphony runtime, and uses the alternate terminal screen when available so dashboard refreshes do not flood normal scrollback.

Set `RPGK_ALTERNATE_SCREEN=0` only when ordinary scrollback is intentionally desired.

The workflow currently allows one concurrent worker and four Codex turns per worker lifetime.

## 12. Dispatch an issue

Before dispatch:

1. make the issue sufficiently complete for unattended implementation;
2. classify risk/model/effort;
3. classify Unity resource/validation needs;
4. add `symphony:ready` last.

Code-only example:

```bash
gh issue edit <number> \
  --repo Shashakar/RPG-Kingdom \
  --add-label "risk:normal" \
  --add-label "symphony:ready"
```

Unity-required example:

```bash
gh issue edit <number> \
  --repo Shashakar/RPG-Kingdom \
  --add-label "risk:normal" \
  --add-label "resource:unity-editor" \
  --add-label "validation:unity-required" \
  --add-label "symphony:ready"
```

For Unity-required work the host health check and exclusive lock happen before Codex starts. Once running, Codex must use `unity-runner.sh` for relevant editor validation before a clean PR handoff.

## 13. Failed handoff / explicit retry

The persistent `.symphony-attempt-complete` marker prevents accidental second worker lifetimes. If a worker ends while `symphony:ready` remains, the after-run guard removes the lease, adds `symphony:halted`, and comments on the issue.

Unity policy/health/lock failures halt even earlier, before Codex spends a turn.

After inspection, an intentional retry is:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/rearm-issue.sh <number>
```

Change incorrect risk/model/Unity labels before rearming. Do not automate this retry loop.

## 14. Environment overrides

Normal evaluated-host operation should not require these:

```text
RPGK_SUPERVISOR_ROOT
RPGK_SECRETS_FILE
RPGK_ALTERNATE_SCREEN
RPGK_POWERSHELL_EXE
RPGK_UNITY_EDITOR_WINDOWS
RPGK_UNITY_STAGE_ROOT_WINDOWS
RPGK_SUPERVISOR_STATE_ROOT
```

Do not commit secrets in any override.

## 15. Benchmarks and next phase

#91 is the Phase 1 usage baseline. #93 and #95 establish the Luna/Terra Phase 2 measurements in `PHASE2_BUDGETED_ROUTING.md`.

Phase 4 should be validated with real Unity-required work rather than a synthetic Codex benchmark. Unity test execution itself does not consume Codex model allowance while the tool process is running, but the worker still spends model turns interpreting failures and making changes.

After the runner is proven stable, Phase 5 can consider increasing **code-only** concurrency while the `unity-editor` resource remains exclusive.
