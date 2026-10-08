# Supervisor Diagnostics

## Why this exists

GH-97 exposed a diagnostic gap between host/App Server preflights and the actual model-backed turn environment.

The Supervisor can prove all of the following without starting a model:

- Symphony forwards the named Codex permission profile;
- App Server selects `rpgk_supervisor_workspace`;
- App Server `command/exec` can perform real `.git` writes under that profile.

Even with those checks green, GH-97's model-backed shell still observed `.git` as read-only and WSL-to-Windows Unity invocation failed with `UtilBindVsockAnyPort: socket failed 1`. The remaining failure therefore has to be measured in the actual turn execution path rather than inferred from profile identity or `command/exec` behavior.

This document defines two deliberately separate tools:

1. a read-only issue diagnostics collector/dashboard for everyday visibility;
2. an explicit, tiny model-backed turn probe for reproducing the execution-environment boundary.

Neither tool rearms issues, starts Symphony workers, edits GitHub labels, or merges code.

## Halt diagnosis

A halted worker now produces two deliberately separate layers of evidence.

### Supervisor stop reason

The host determines why the worker lifetime ended from deterministic evidence. Examples include:

- `usage_limit_exceeded`;
- `continuation_policy`;
- `hard_turn_ceiling`;
- `model_unavailable` when the selected Codex model is rejected by the configured account/provider;
- `app_server_terminated` when model compatibility succeeds but the actual Codex App Server exits before a trusted completion/handoff and leaves stderr evidence;
- generic `worker_lifetime_ended` when no stronger deterministic cause is available.

The host never asks Codex for an extra turn solely to explain a stop.

Before a normal worker is recorded as started, `codex-model-compatibility.sh` verifies the selected model against the installed Codex client/account. Successful checks are cached by Codex version + model. A rejection is persisted under `$RPGK_SUPERVISOR_STATE_ROOT/model-errors/GH-N.json`, the worker is not started, and the normal halt path surfaces the actual model/provider error as `model_unavailable` instead of misclassifying it as an unexplained worker lifetime.

The compatibility probe uses a separate `codex exec` session and therefore is not evidence that Symphony successfully established the subsequent App Server session. Worker token attribution excludes pre-worker probe rollouts. The router also preserves the current App Server stderr stream at `$RPGK_SUPERVISOR_STATE_ROOT/app-server-stderr/GH-N.log`; when an unexpected lifetime ends without stronger evidence, halt diagnosis includes the bounded stderr tail and classifies the boundary as `app_server_terminated`.

### Task-level worker status

Before an unfinished turn returns, the workflow asks Codex to persist the best semantic status it already knows with `scripts/worker-status.py`. Typical classifications include:

- `validation_failed`;
- `manual_action_required`;
- `handoff_incomplete`;
- `worker_error`;
- `scope_mismatch`;
- `unknown`.

The record may also contain unmet acceptance criteria, a blocker, whether manual action is required, validation run IDs, and a recommended next action. It is workspace-local Supervisor state and is excluded from normal Git staging.

Worker status is tied to the current worker-lifetime attempt boundary. A stale status from a prior rearm is ignored rather than reused as current evidence. If a worker crashes before it can write status, the diagnosis explicitly reports that semantic task status is unavailable instead of guessing.

### Durable output

`after-run-guard.sh` combines the deterministic stop reason, fresh task-level status when present, latest turn/Unity evidence, and preserved workspace state. It posts that diagnosis to the RPG Kingdom issue and persists the structured record at:

```text
$RPGK_SUPERVISOR_STATE_ROOT/halt-diagnostics/GH-N.json
```

A `worker_halt_diagnosed` telemetry event is also emitted. Worker drill-down includes the latest `haltDiagnosis` record so dashboard/detail tooling can explain the same stop without scraping GitHub comments.

The operator contract is: when Supervisor stops, the available evidence should answer **what stopped, what remains, whether manual action is required, and what to do next**. Unknown fields remain explicit unknowns.

## Operator runbook: locating logs for a halted issue

Use this runbook before rearming a halted issue. The paths below are **observed on the Dex-GMKTec-Server installation on 2026-10-08**, not portable defaults. Prefer the `RPGK_SUPERVISOR_STATE_ROOT` environment setting where available. The local Symphony/Elixir checkout was `~/src/openai-symphony/elixir`; the Supervisor checkout was `~/src/RPG-Kingdom-Supervisor`; issue workspaces were under `~/code/rpg-kingdom-symphony-workspaces/GH-N`.

### Fast path (replace 245 with the issue number)

```bash
ISSUE=245
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/diagnose-issue.sh "$ISSUE"
bash scripts/diagnose-issue.sh "$ISSUE" --json

# System services (these were system-wide, not --user units on this host)
systemctl status rpg-kingdom-supervisor.service rpg-kingdom-diagnostics.service --no-pager
sudo journalctl -u rpg-kingdom-supervisor.service \
  --since '2026-10-08 14:25:00' --until '2026-10-08 14:29:00' --no-pager
sudo journalctl -u rpg-kingdom-diagnostics.service \
  --since '2026-10-08 14:25:00' --until '2026-10-08 14:29:00' --no-pager
```

Adjust the journal time window to the incident and check the host timezone. The diagnostics command is read-only; do not treat a summary halt classification as a root cause.

### Symphony orchestrator logs

```bash
cd ~/src/openai-symphony/elixir
grep -n -C 12 'GH-245' log/symphony.log* | tail -250
grep -h -E 'GH-245|worker_lifetime_ended|issue_id=245' log/symphony.log* | tail -120
```

Rotated logs may appear as `log/symphony.log.2`; the shell glob scans the available files. For unfiltered surrounding events, use the relevant file and line range, e.g. `sed -n '7390,7450p' log/symphony.log.2`. Search for dispatch, `after_create`, `before_run`, router output, `Codex session started`, `after_run`, and completed handoff. Missing `Codex session started` means initialization is **unconfirmed**, not proof of why it failed.

### Supervisor local state and preserved workspace

```bash
ISSUE=245
STATE="${RPGK_SUPERVISOR_STATE_ROOT:-$HOME/.local/state/rpg-kingdom-supervisor}"
WS="$HOME/code/rpg-kingdom-symphony-workspaces/GH-$ISSUE"

# Metadata, stderr, and failure classification
for file in \
  "$STATE/halt-diagnostics/GH-$ISSUE.json" \
  "$STATE/app-server-stderr/GH-$ISSUE.log" \
  "$STATE/model-errors/GH-$ISSUE.json" \
  "$STATE/capabilities/GH-$ISSUE.json"; do
  if [ -f "$file" ]; then
    echo "===== $file ====="
    tail -100 "$file"
  fi
done

# Worker lifetime history, model compatibility artifacts, and Unity ownership
find "$STATE/workers" "$STATE/model-compatibility" "$STATE/unity-broker" "$STATE/locks" \
  -maxdepth 4 -type f 2>/dev/null | grep -E "GH-$ISSUE|history|status|compatibility|lock" | head -80

git -C "$WS" status --short --branch
git -C "$WS" log -1 --oneline
```

`app-server-stderr/GH-N.log` is particularly important if routing succeeded but the App Server did not establish a session. Check `model-compatibility/` and `model-errors/` to distinguish a successful separate compatibility probe from a rejected model. A successful compatibility check **does not prove** the actual App Server worker started. Worker records may live in `workers/history/GH-N-...json` and `workers/history.jsonl`. Unity broker status and issue/CodeX mutation locks reveal resource/preflight boundaries, but a lock file timestamp alone does not prove a lock is currently held.

A time-scoped inventory is useful when the specific evidence filename is unknown:

```bash
find "$HOME/.local/state/rpg-kingdom-supervisor" -maxdepth 4 -type f \
  -newermt '2026-10-08 14:25:00' ! -newermt '2026-10-08 14:29:00' \
  -printf '%TY-%Tm-%Td %TH:%TM:%TS %p\n' 2>/dev/null | sort
```

Review logs for tokens, credentials, and private content before sharing them outside the host.

### Source ownership: where to investigate behavior

- `WORKFLOW.md` defines the Symphony hooks and worker contract.
- `scripts/codex-app-server-router.sh` and `scripts/routing-policy.sh` choose the model/effort. Compare the router's emitted selection with the dashboard's displayed model and the actual Codex session initialization; these are distinct pieces of evidence.
- `scripts/codex-model-compatibility.sh` performs a separate model preflight.
- `scripts/before-run-guard.sh`, `scripts/after-run-guard.sh`, and `scripts/halt-diagnosis.py` enforce lifecycle/rearm and build halt diagnoses.
- `scripts/supervisor_telemetry.py`, `scripts/supervisor_detail.py`, and `scripts/supervisor_dashboard.py` serve worker history and presentation of routed model/lifecycle.
- `docs/PHASE3_UNITY_SCHEDULING.md` explains Unity locks and required preflight behavior.

### GH-245 incident evidence (2026-10-08, America/Denver)

- 14:25:50: GH-245 dispatched, worker attempt started.
- 14:26:18: `before_run` started; 14:26:22: router emitted `GH-245 -> luna (gpt-6-luna, effort=medium, role=implementation)`; capabilities state was written.
- 14:26:27: `after_run` started; 14:26:37: issue was no longer routed to that worker.
- GitHub marked the issue `symphony:halted` with `worker_lifetime_ended`; its comment reported no fresh structured task status.
- Preserved workspace `GH-245` was clean on `main` at `3bff307`, with no observed implementation change. The available Symphony log excerpt had no `Codex session started` event for this attempt.
- Observed local evidence included `app-server-stderr/GH-245.log`, `halt-diagnostics/GH-245.json`, `workers/history/GH-245-implementation-20261008T212632Z-1334ee4b.json`, `workers/history.jsonl`, and a `model-compatibility/*.json` file. **Their contents had not yet been examined; the root cause remains unknown.**
- Dashboard displayed `gpt-5.6-luna / medium` while router output selected `gpt-6-luna / medium`. This is an *observed discrepancy*, not yet proof of a stale dashboard label or model execution.
- System services on this host were `rpg-kingdom-supervisor.service` and `rpg-kingdom-diagnostics.service` (system-level services).

**Recovery gate:** inspect the preserved workspace, App Server stderr, halt diagnosis, worker history, model compatibility records, and system journal before rearming. Per `WORKFLOW.md`, a reviewed continuation uses `symphony:rearm` before `symphony:ready` (or `scripts/rearm-issue.sh`); adding only `symphony:ready` is not a valid rearm. Do not change routing policy or discard the workspace solely on the basis of a generic halt.

## Automated review startup failures

Independent PR review uses Codex strict structured output. The review verdict schema must therefore stay within the structured-output JSON Schema subset accepted by Codex. Nullable objects use a nullable `type` array rather than composition keywords such as `oneOf`.

If Codex rejects the review schema before inference, `review-worker.sh` preserves the raw Codex log beside the verdict and reports that the configured structured-output schema was rejected. This is a Supervisor configuration failure, not evidence that the implementation under review is ambiguous or incorrect. Fix the Supervisor schema before requeueing the review; do not spend an implementation repair attempt for this condition.

## Read-only issue diagnostics

### Terminal

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/diagnose-issue.sh 97
```

For machine-readable output:

```bash
bash scripts/diagnose-issue.sh 97 --json
```

The collector combines:

- GitHub issue state, labels, comment count, and latest comment;
- Symphony workspace existence, current branch, host-visible Git status, and worker-lifetime marker;
- the latest Symphony session/turn identifiers found in local logs;
- the matching Codex rollout, token snapshot, turn-context signals, and matched failure strings;
- the Unity resource lock;
- the latest returned Unity `summary.json`, when one exists.

It reads existing state only. It does not call the Unity runner because repeatedly probing the Windows bridge from an auto-refreshing UI would itself become an active diagnostic operation.

### Localhost dashboard

```bash
bash scripts/serve-diagnostics.sh
```

Then open:

```text
http://127.0.0.1:8765
```

The page defaults to GH-97, accepts another issue number, and refreshes every five seconds. The server uses Python's standard library only, binds to `127.0.0.1`, and exposes only read-only local diagnostic data.

Use a different port with:

```bash
bash scripts/serve-diagnostics.sh --port 8877
```

The dashboard is an operator visibility surface, not a second orchestrator. It must not gain issue mutation, worker dispatch, merge, or arbitrary shell execution controls.

## Model-backed turn environment probe

The turn probe exists specifically because App Server `command/exec` and a model-backed Codex turn have demonstrated different behavior on this WSL host.

It is **not** part of `run-symphony.sh` and is **not** part of `tests/run.sh`, because it starts a real model turn and consumes a small amount of Codex allowance.

Run it explicitly:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/codex-turn-environment-probe.sh --run
```

Default model/routing for the probe:

```text
gpt-6-luna / low reasoning
```

Override only when diagnosing model-specific behavior:

```bash
bash scripts/codex-turn-environment-probe.sh --run --model gpt-6-sol
```

The probe creates a disposable Git repository, starts an ephemeral App Server thread under `rpgk_supervisor_workspace`, then starts one tiny turn whose only instruction is to execute `bash ./probe-actions.sh`.

The deterministic script records two independent checks:

### `git_write`

The model-turn shell attempts to:

- write `.git/FETCH_HEAD`;
- configure Git identity;
- `git add` a file;
- make a real local commit.

This is the same class of metadata access that blocked GH-97.

### `wsl_interop`

The model-turn shell attempts:

```text
cmd.exe /d /c "echo rpgk-wsl-interop-ok"
```

This is intentionally much narrower than running Unity. If this fails with the same WSL vsock error, the Unity runner cannot work from the model sandbox regardless of Unity configuration.

The probe prints JSON evidence followed by one summary line. A healthy result is:

```text
RPG Kingdom Codex model-turn environment probe: PASS (model=gpt-6-luna, git_write=ok, wsl_interop=ok)
```

A result such as:

```text
git_write=failed
wsl_interop=failed
```

proves the remaining boundary is the model-turn execution sandbox rather than Symphony routing, the named profile definition, App Server profile selection, or Unity itself.

## What to do with the result

Do not keep broadening the Codex sandbox simply to make the probe green.

If the model turn cannot safely own `.git` or Windows interop, the preferred architecture is to move those capabilities behind narrow host-owned Supervisor seams instead of granting `danger-full-access`:

- host-side Git preparation/finalization for fetch/switch/merge/commit/push;
- a host-owned Unity validation bridge callable through a bounded orchestration/tool interface;
- model workspace writes remain limited to source/test/doc files.

That design would make the security boundary explicit rather than depending on model-shell access to host metadata and WSL interop.

## GH-97 retry rule

Do not rearm GH-97 merely because the model-free probes are green.

Before another expensive implementation attempt, run the model-turn environment probe once and use its evidence to choose the next architecture change. If either `git_write` or `wsl_interop` fails, fix or bypass that boundary first.
