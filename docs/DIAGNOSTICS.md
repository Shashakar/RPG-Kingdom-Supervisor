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
gpt-5.6-luna / low reasoning
```

Override only when diagnosing model-specific behavior:

```bash
bash scripts/codex-turn-environment-probe.sh --run --model gpt-5.6-terra
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
RPG Kingdom Codex model-turn environment probe: PASS (model=gpt-5.6-luna, git_write=ok, wsl_interop=ok)
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
