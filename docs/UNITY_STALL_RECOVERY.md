# Unity Validation Stall Detection and Recovery

Issue #44 extends the Phase 4 Unity broker so a validation request that stops making observable progress becomes an explicit infrastructure outcome instead of consuming the remainder of a Codex worker lifetime as repeated `HostBusy` checks.

The existing Phase 4 boundaries remain unchanged: workers submit typed `health`, `editmode`, and `playmode` requests; the host broker owns WSL/Windows interop; `resource:unity-editor` remains exclusive; and workers never receive a generic host process-control surface.

## Progress contract

For every broker request the host broker creates request-scoped progress/control paths under the issue workspace:

```text
Logs/SymphonyUnity/.broker/progress/<request-id>.json
Logs/SymphonyUnity/.broker/control/<request-id>.cancel.json
```

The broker passes those paths to `unity-runner-host.sh`, which forwards them to the Windows PowerShell bridge. The PowerShell runner reports progress only when something materially changes. It does not emit a synthetic heartbeat merely to keep the request alive.

Progress records include, where available:

- request ID and monotonically increasing sequence;
- current phase;
- observation timestamp;
- the exact Unity PID launched by that request;
- current `Editor.log` byte length;
- current `results.xml` byte length;
- whether the final summary exists.

Phases include staging, Unity startup, importing/compilation, test execution, results production, artifact finalization, and completion/recovery states. Phase names are diagnostic hints, not an API for gameplay behavior.

While Unity is running, changes to the requested `Editor.log` or `results.xml` count as progress. A long PlayMode run is therefore allowed to exceed the no-progress threshold as long as those artifacts continue moving.

Broker `status.json` exposes the active request's `phase`, `lastProgressAt`, `noProgressSeconds`, `unityPid`, progress payload, and `recoveryBlocked` state. The read-only Unity run history/dashboard surfaces the same fields for active and completed stall outcomes.

## Stall threshold versus absolute timeout

There are two independent bounds:

- **no-progress threshold**: `RPGK_UNITY_BROKER_STALL_SECONDS`, default **300 seconds**;
- **absolute host-operation ceiling**: `RPGK_UNITY_BROKER_HOST_TIMEOUT_SECONDS`, default **1800 seconds**.

`editmode` and `playmode` requests may be classified as stalled when they have made no observable progress for the no-progress threshold. `health` continues to use the absolute ceiling because it does not represent a long-running test lifecycle.

The absolute ceiling remains a final safety bound even for test requests that continue to report progress.

## Ownership-safe recovery

A stall does not authorize broad process cleanup.

### Request-owned Unity process

When progress metadata records a concrete Unity PID launched by the current request, the broker writes a request-scoped cancel marker and waits a bounded recovery grace period. The PowerShell runner validates the request ID and calls `Stop-Process` only for that exact PID.

If the host runner exits after that cancellation, the response is:

```text
status: Stalled
exitCode: 91
```

The response includes durable recovery evidence: the stalled phase, last progress timestamp, no-progress duration, recovery action, and recovery result. The broker returns to `ready` and an explicit validation retry may be attempted.

This retry is deliberately a normal tool retry inside the existing worker lifetime. It does not automatically create another Codex worker lifetime, add model turns, or bypass the existing continuation budget.

### Pre-Unity staging

If a stall occurs in a known pre-Unity staging phase, no Windows Unity process has been launched yet. The broker may terminate its own request-owned host process group and return `Stalled`.

### Ambiguous ownership

If the request appears to be in/after Unity execution but no request-owned Unity PID can be proven, or if bounded cancellation does not cause the host runner to exit, the response is:

```text
status: StallRecoveryBlocked
exitCode: 92
```

No unrelated Unity process is terminated. The broker enters `blocked` and retains the Unity execution slot until the ambiguous host operation exits or an operator resolves it. A new request receives `HostBusy` rather than stealing the slot.

This is intentionally fail-closed. A manual editor, another issue's process, or an otherwise ambiguous Windows process must never be killed merely because elapsed time looks suspicious.

## Worker behavior

The worker-facing `unity-runner.sh` prints explicit lifecycle markers for both outcomes:

```text
RPG Kingdom Unity runner: Stalled
RPG Kingdom Unity runner: StallRecoveryBlocked
```

For `Stalled`, preserve the implementation workspace and retry the relevant validation once if the host has returned to `ready`. If the retry fails for an ordinary test/compile reason, handle that result normally.

For `StallRecoveryBlocked`, do not loop on `HostBusy` and do not spend remaining model turns repeatedly polling. Preserve the request ID/evidence and surface the infrastructure block for operator/human attention. The implementation workspace and completed investigation remain intact for a reviewed continuation after the host condition is resolved.

## Diagnostics

Completed stall responses are durable in the normal broker response/history directories. The dashboard distinguishes:

- `running` — request is active and reporting its current phase/progress age;
- `stalled` — the broker positively identified no progress and completed bounded recovery;
- `blocked` — recovery was refused/failed because ownership could not be proven safely;
- `timed out` — the absolute host-operation ceiling was reached;
- `host busy` — a different live/blocked operation currently owns the slot.

Run detail shows the stall/recovery payload alongside broker request/response data and existing Unity artifacts.

## Configuration

Normal operation should use the conservative defaults. Available host settings are:

```text
RPGK_UNITY_BROKER_STALL_SECONDS=300
RPGK_UNITY_BROKER_STALL_RECOVERY_GRACE_SECONDS=15
RPGK_UNITY_BROKER_HOST_TIMEOUT_SECONDS=1800
RPGK_UNITY_BROKER_KILL_GRACE_SECONDS=5
```

Do not lower the stall threshold merely to make validation appear faster. Unity import/compile/test work can legitimately pause for substantial periods, especially after asset or package changes. The progress signal exists specifically to avoid treating total elapsed time as proof of a hang.

## Regression coverage

`tests/unity-broker-test.sh` uses a fake host runner to cover:

- a long-running request that remains healthy because progress continues;
- another issue receiving `HostBusy` without stealing the live slot;
- a positively-owned stall being cancelled and the broker returning to `ready`;
- an explicit retry succeeding after safe recovery;
- the absolute timeout remaining available for non-test host operations;
- ambiguous process ownership returning `StallRecoveryBlocked` without destructive cancellation;
- reconciliation back to `ready` after the ambiguous host process is externally resolved.

`tests/unity-run-history-test.py` covers durable `Stalled`/`StallRecoveryBlocked` classification plus active phase/progress data used by the dashboard.
