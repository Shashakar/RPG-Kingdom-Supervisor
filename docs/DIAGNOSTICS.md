# Supervisor Diagnostics

## Why this exists

GH-97 exposed a diagnostic gap between host/App Server preflights and the actual model-backed turn environment. GH-98 then exposed a second gap: Unity run evidence was durable on disk, but operators and continuation workers had to manually rediscover it from broker response JSON and `Editor.log` after a failed worker lifetime.

This document defines two deliberately separate tools:

1. a read-only issue diagnostics collector/dashboard for everyday visibility, including durable Unity run history;
2. an explicit, tiny model-backed turn probe for reproducing the execution-environment boundary.

Neither tool rearms issues, starts Symphony workers, edits GitHub labels, runs Unity, or merges code.

## Read-only issue diagnostics

### Terminal

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/diagnose-issue.sh 98
```

For machine-readable output:

```bash
bash scripts/diagnose-issue.sh 98 --json
```

The collector combines:

- GitHub issue state, labels, comment count, and latest comment;
- Symphony workspace existence, current branch, host-visible Git status, and worker-lifetime marker;
- the latest Symphony session/turn identifiers found in local logs;
- the matching Codex rollout, token snapshot, turn-context signals, and matched failure strings;
- the Unity resource lock;
- the latest returned Unity `summary.json`, when one exists;
- recent Unity broker runs reconstructed from durable acknowledgements, responses, and returned run artifacts.

It reads existing state only. It does not call the Unity runner because repeatedly probing the Windows bridge from an auto-refreshing UI would itself become an active diagnostic operation.

### Unity run history

For each issue workspace, diagnostics reads:

```text
Logs/SymphonyUnity/.broker/acks/*.json
Logs/SymphonyUnity/.broker/responses/*.json
Logs/SymphonyUnity/<run>/Editor.log
Logs/SymphonyUnity/<run>/results.xml
Logs/SymphonyUnity/<run>/summary.json
```

Those files survive the broker returning to `ready` and survive broker restarts, so historical runs remain inspectable without adding a second persistence database.

Each normalized run includes, when evidence is available:

- broker request ID and Unity run ID;
- operation (`health`, `editmode`, `playmode`);
- requested test filter;
- accepted/completed timestamps and duration;
- normalized status and raw broker status;
- exit code;
- artifact directory plus `Editor.log`, `results.xml`, and `summary.json` paths;
- a derived primary diagnosis for failures.

The failure classifier deliberately prefers the strongest evidence:

1. compiler errors parsed from `Editor.log`;
2. failed tests and assertion messages parsed from `results.xml` / `summary.json`;
3. no-tests-matched outcomes;
4. broker/host timeout or infrastructure failures;
5. Unity startup/runner failure as a fallback when no stronger evidence exists.

A compilation failure before tests start therefore appears as something similar to:

```text
failed playmode GH-98-playmode-...
  Script compilation: Assets\RPGKingdom\Tests\Example.cs:54:50 CS1061: ...
```

rather than only the generic runner message that Unity exited without producing test results.

The JSON form exposes the same normalized `unity_runs` data to other local tooling or future Symphony continuation-context enrichment. This is intentionally read-only; workers should consume prior evidence rather than rerun expensive diagnostics simply to rediscover an existing failure.

### Localhost dashboard

```bash
bash scripts/serve-diagnostics.sh
```

Then open:

```text
http://127.0.0.1:8765
```

The page accepts an issue number and refreshes every five seconds. Alongside the existing GitHub/workspace/Symphony/Codex cards, it now shows recent Unity run history. Runs can be filtered client-side by operation and status, and each run expands to show:

- concise failure diagnosis and retryability classification;
- compiler errors or failed test names/messages;
- requested test filter;
- start/end timestamps, duration, and exit code;
- artifact paths for deeper inspection;
- broker stderr when useful.

The dashboard also shows a currently active broker request as `running` with elapsed time when that request belongs to the selected issue.

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
gpt-5.6-luna / low reasoning
```

Override only when diagnosing model-specific behavior:

```bash
bash scripts/codex-turn-environment-probe.sh --run --model gpt-5.6-terra
```

The probe creates a disposable Git repository, starts an ephemeral App Server thread under `rpgk_supervisor_workspace`, then starts one tiny turn whose only instruction is to execute `bash ./probe-actions.sh`.

The deterministic script records two independent checks.

### `git_write`

The model-turn shell attempts to:

- write `.git/FETCH_HEAD`;
- configure Git identity;
- `git add` a file;
- make a real local commit.

### `wsl_interop`

The model-turn shell attempts:

```text
cmd.exe /d /c "echo rpgk-wsl-interop-ok"
```

This is intentionally much narrower than running Unity. If this fails with the same WSL vsock error, the Unity runner cannot work from the model sandbox regardless of Unity configuration.

The probe prints JSON evidence followed by one summary line. A healthy result is:

```text
RPG Kingdom Codex model-turn environment probe: PASS (model=gpt-5.6-luna, git_write=ok, wsl_interop=ok)
```

The probe is explicit because it consumes allowance. It must never be added to automatic startup or the ordinary deterministic test suite.

## What to do with the result

Do not keep broadening the Codex sandbox simply to make a probe green.

If the model turn cannot safely own `.git` or Windows interop, the preferred architecture is to move those capabilities behind narrow host-owned Supervisor seams instead of granting `danger-full-access`:

- host-side Git preparation/finalization for fetch/switch/merge/commit/push;
- a host-owned Unity validation bridge callable through a bounded orchestration/tool interface;
- model workspace writes remain limited to source/test/doc files.

When Unity validation itself fails, inspect the dashboard history before spending another worker turn. If the failure is already classified as compilation, test, timeout, or infrastructure, continuation context should carry that concrete evidence forward rather than asking the next worker to rediscover it.
