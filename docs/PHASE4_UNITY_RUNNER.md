# Phase 4 — Host-Owned Windows Unity Runner

Phase 4 turns the Phase 3 `unity-editor` scheduling contract into real, observable Unity validation without moving RPG Kingdom's source of truth out of the Symphony WSL workspace. The host-owned broker refinement keeps the fragile WSL-to-Windows process boundary outside the Codex worker sandbox.

## Goals

- run the Unity version declared by RPG Kingdom's `ProjectSettings/ProjectVersion.txt`;
- execute EditMode and PlayMode tests from a Symphony worker;
- support narrow Unity Test Framework filters;
- keep Unity's active project on Windows NTFS rather than `\\wsl.localhost`;
- preserve the staged `Library/` cache across issues and branches;
- return test XML, Editor logs, and a compact JSON summary to the worker workspace;
- require Phase 3 exclusive-resource ownership before test execution;
- treat any already-running Windows `Unity.exe` as the host resource being busy;
- keep WSL/Windows interop host-owned instead of model-owned;
- keep the broker responsive and diagnosable while a host Unity operation is running;
- keep production-scene authoring authority unchanged.

## Why the host broker exists

GH-97 and a fresh GH-98 run demonstrated that a model-backed Codex turn can fail WSL-to-Windows interop with `UtilBindVsockAnyPort` even when the same host can run the Unity bridge successfully from the operator shell. Permission probes therefore cannot prove that a future model process will own a reliable Windows bridge.

The worker still uses the same narrow command surface, but `scripts/unity-runner.sh` is now a broker client. It writes a typed request inside the worker's own ignored `Logs/SymphonyUnity/.broker/` directory. A persistent broker launched by `scripts/run-symphony.sh` from the operator shell performs the direct host operation through `scripts/unity-runner-host.sh`.

The broker is intentionally not a generic command service. Its protocol accepts only `health`, `editmode`, and `playmode`, plus an optional Unity Test Framework filter. The broker derives the project path from the request location, requires it to be an immediate `GH-<number>` child of the configured Symphony workspace root, and revalidates the Unity lock before test operations.

## Data flow

```text
Operator WSL shell
        |
        | scripts/run-symphony.sh
        +------------------------------+
        |                              |
        v                              v
Unity host broker                Symphony / Codex worker
        ^                              |
        |                              | unity-runner.sh
        |                              | typed workspace-local request
        |                              v
        +---- GH-N/Logs/SymphonyUnity/.broker/
        |
        | host-only unity-runner-host.sh
        | wslpath + powershell.exe
        v
Windows PowerShell bridge
        |
        | robocopy /MIR Assets, Packages, ProjectSettings
        v
%LOCALAPPDATA%\RPGKingdomSupervisor\UnityStages\<version>\RPG-Kingdom
        |
        | preserved Library/
        v
Unity.exe -batchmode -runTests ...
        |
        +--> results.xml
        +--> Editor.log
        +--> summary.json
        |
        | copied back to GH-N/Logs/SymphonyUnity/<run-id>/
        v
Broker response + structured host status
```

`Logs/` is ignored by RPG Kingdom. Requests, responses, and Unity artifacts remain available for diagnostics without entering the implementation branch.

## Broker lifecycle and status

`run-symphony.sh` starts the broker before Symphony and waits for protocol version 1 to report `ready`. If a compatible broker is already running for the same workspace root, the launcher reuses it. A broker started by the launcher is stopped when that launcher exits; a reused broker is left to its original owner.

The broker event loop remains responsive while a Unity host operation is active. The host adapter runs as a managed child process in its own process group. Exactly one host operation may run at a time; another valid request is acknowledged immediately and receives `HostBusy` with the active request details instead of timing out waiting for an acknowledgement.

Host status is written to:

```text
~/.local/state/rpg-kingdom-supervisor/unity-broker/status.json
```

The status records the protocol version, broker PID/state, workspace root, active request, and the compact last result. While a request is active, `activeRequest` includes the issue/workspace, operation, test filter, child PID, start time, and elapsed seconds; the status heartbeat is refreshed while the host process runs. This file is intended as a diagnostics source; worker requests continue to use workspace-local IPC so Codex does not need write access to Supervisor state.

A broker host operation has a bounded timeout, 1800 seconds by default. On timeout or broker shutdown, the broker terminates the host runner's process group so PowerShell/Unity descendants are not intentionally left behind, records `TimedOut` or `BrokerStopped`, and returns to a known broker state. Existing request files found when a new broker starts are failed as `StaleRequest` rather than replayed across broker lifetimes.

If the broker does not acknowledge a request promptly, the worker-facing runner still fails clearly and tells the operator to launch Symphony through `scripts/run-symphony.sh`. Because an active broker now acknowledges concurrent requests with `HostBusy`, an acknowledgement timeout should indicate an absent, incompatible, or unhealthy broker rather than merely a long-running Unity operation.

## Supported runner interface

From a Symphony `GH-<number>` workspace:

```bash
bash ~/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh health
bash ~/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh editmode
bash ~/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh playmode
```

Prefer filtered runs:

```bash
bash ~/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh editmode \
  --filter 'RPGKingdom.Tests.EditMode.Inventory'

bash ~/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh playmode \
  --filter 'RPGKingdom.Tests.PlayMode.Inventory'
```

`--filter` is passed unchanged to Unity Test Framework. Workers must not bypass this interface by launching PowerShell, Windows commands, or `Unity.exe` directly.

Broker lifecycle outcomes are explicit at the runner boundary:

- `HostBusy` — another Unity host operation already owns the broker execution slot;
- `TimedOut` — the active host process exceeded the configured host timeout and its process group was terminated;
- `StaleRequest` — a request survived from a previous broker lifetime and was deliberately not replayed;
- `BrokerStopped` — the broker was shut down while the request was active;
- `NoTestsMatched` — Unity ran successfully but the requested test filter matched zero tests.

## Health and resource ownership

`health` deliberately does not require the Unity lock because the Phase 3 preflight calls it before acquiring the resource. The broker/host adapter verifies the project and host prerequisites and confirms that no Windows `Unity.exe` is already running.

`editmode` and `playmode` require:

1. a request from an immediate `GH-<number>` child of the configured Symphony workspace root;
2. an existing Phase 3 lock;
3. a lock owner matching that GH issue;
4. the recorded lock workspace matching the request workspace;
5. a Windows host free of pre-existing `Unity.exe` processes.

The worker-facing client and host adapter retain defense-in-depth checks, while the broker is authoritative for deciding whether a typed request may cross the host boundary.

## Windows staging

The source workspace remains on the WSL Linux filesystem for Git/Codex performance. The host adapter resolves the repository-declared Unity version, converts the workspace and PowerShell script paths with `wslpath`, and invokes Windows PowerShell.

The PowerShell bridge mirrors only:

- `Assets/`
- `Packages/`
- `ProjectSettings/`

It does not mirror `.git/`, `Library/`, `Temp/`, `Logs/`, or unrelated generated state. `robocopy /MIR` removes staged source files deleted on the branch, while `Library/` persists for cache reuse. The staging directory is disposable validation state and is never synchronized back into RPG Kingdom.

## Test execution and evidence

Unity runs with the native Test Framework command-line flow: `-batchmode`, `-accept-apiupdate`, `-projectPath`, `-runTests`, `-testPlatform`, `-testResults`, `-logFile`, and optional `-testFilter`.

The bridge copies `results.xml`, `Editor.log`, and `summary.json` back under `Logs/SymphonyUnity/<run-id>/`. If Unity crashes before the requested log exists, a bounded tail of the global `%LOCALAPPDATA%\Unity\Editor\Editor.log` is copied when it was touched by the current run.

A run is unsuccessful when Unity exits nonzero, result XML is missing/malformed, one or more tests fail, the host operation times out, or zero tests match. Zero-test runs are explicitly represented as `NoTestsMatched` so a green-looking Unity aggregate cannot be mistaken for validation evidence.

## Unity 6000.3.10f1 host caveat

The evaluated host exposed Unity issue `UUM-140399`: Unity can crash during startup when its global `CurlRequestCache.db` cannot be opened because the database is corrupt or externally locked. Unity fixed the issue later in the 6000.3 stream, but RPG Kingdom currently declares `6000.3.10f1` and the Supervisor does not silently substitute another version.

The host-wide idle check remains correct independently of that Unity bug: `resource:unity-editor` promises exclusive Editor ownership.

## Environment overrides

Normal operation should not require overrides. Supported host/client settings include:

```text
RPGK_SUPERVISOR_STATE_ROOT
RPGK_SYMPHONY_WORKSPACE_ROOT
RPGK_UNITY_BROKER_ACK_TIMEOUT_SECONDS
RPGK_UNITY_BROKER_TIMEOUT_SECONDS
RPGK_UNITY_BROKER_HOST_TIMEOUT_SECONDS # launcher -> broker, default 1800
RPGK_UNITY_BROKER_KILL_GRACE_SECONDS   # launcher -> broker, default 5
RPGK_POWERSHELL_EXE                    # host adapter only
RPGK_UNITY_EDITOR_WINDOWS              # host adapter only
RPGK_UNITY_STAGE_ROOT_WINDOWS           # host adapter only
```

Do not put credentials in these values.

## Still deferred

This refinement does not add Git commit/push brokering, arbitrary shell/PowerShell execution, automatic Unity builds, production-scene authoring permission, visual-regression capture, additional worker concurrency, automatic retry, or automatic PR merge. Those remain separate decisions based on measured failures.
