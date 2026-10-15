# Unity editor resource lock recovery

Supervisor holds `~/.local/state/rpg-kingdom-supervisor/locks/unity-editor.lock` for the lifetime of any Symphony worker that owns `resource:unity-editor`.

Normal ownership is conservative: an idle Unity broker does **not** imply that the lease is stale, because a live worker may legitimately hold the editor between validation runs.

When a new dispatch encounters an existing lock, `unity-resource-guard.sh` reclaims it only when both of these are true:

1. the Unity broker reports protocol version 1, `state: ready`, and `activeRequest: null`; and
2. the recorded `GH-N` owner issue is provably no longer dispatch-active: it is closed, or it is open without `symphony:ready` and is explicitly in one of the terminal/hand-off lifecycle states (`symphony:halted`, `symphony:agent-review`, `symphony:human-review`, or `symphony:human-attention`).

If either signal is missing, contradictory, unavailable, or ambiguous, Supervisor keeps the existing lock and fails closed exactly as before.

A proven stale lock is reclaimed with an atomic directory rename before the new lease is created. Inspection, stale recovery, and acquisition run within one `flock` transaction using the persistent sibling `unity-editor.transaction.lock` file. The file stays outside the replaceable lock directory so contenders share the same transaction lock. This prevents a contender from reclaiming a newly acquired lease using evidence about its previous owner. The host requires the `flock` command.

The guard logs whether a lock was retained because the broker was active/ambiguous, retained because the owner could not be proven inactive, or successfully recovered as stale.

Deterministic coverage lives in `tests/unity-resource-guard-test.sh` and exercises active ownership, closed/halted stale ownership, ambiguous broker state, and two-contender recovery.

Health preflight retries `HostBusy` (87) for at most 30 seconds only when broker status identifies this exact issue/workspace and the configured EditMode readiness-smoke filter with a live owner. An explicit durable worker takes precedence over the transient client. Other busy operations, missing/ambiguous status, and expired waits retain the existing halt behavior. `RPGK_UNITY_PREFLIGHT_BUSY_WAIT_SECONDS` can set a bound from 0 to 300 seconds (0 disables retries); it does not extend worker turn budgets.

The operator can exercise both real progress writers without starting Unity:

```bash
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass \
  -File "$(wslpath -w scripts/windows/test-unity-progress-publication.ps1)" \
  -OutputRoot "$(wslpath -w /home/dex/code/rpg-kingdom-symphony-workspaces/GH-264/Logs/SymphonyUnity)"
```

The probe creates a unique ignored evidence directory and verifies three consecutive publications, accepting canonical files or the installed immutable sequence snapshots. Run it against the actual WSL UNC destination; a native Windows temporary directory is not equivalent evidence.

## Preflight and orchestration watchdog

The pinned Symphony orchestrator measures `codex.stall_timeout_ms` from attempt
dispatch when there is no Codex activity yet, including time spent in `before_run`.
The five-minute upstream default can therefore kill a healthy Unity readiness
smoke before the ten-minute hook deadline. The workflow sets a finite eleven-minute
watchdog (`660000` ms), leaving one minute beyond the hook deadline for termination
and polling. This setting also lengthens the global no-Codex-activity watchdog;
the Unity broker retains its separate progress-based stall checks. Turn, quota,
and continuation budgets remain unchanged.

The budget guard and Unity preflight are joined with `&&`: a refused dispatch
stops before Unity admission and preserves the original refusal exit code.
Do not rearm solely because an earlier detached smoke eventually passed; verify
broker idleness and review the failed attempt first.
