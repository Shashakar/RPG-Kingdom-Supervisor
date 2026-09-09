# Phase 4 — Windows Unity Runner

Phase 4 turns the Phase 3 `unity-editor` scheduling contract into real, observable Unity validation without moving the RPG Kingdom source of truth out of the Symphony WSL workspace.

## Goals

- run the Unity version declared by RPG Kingdom's `ProjectSettings/ProjectVersion.txt`;
- execute EditMode and PlayMode tests from a Symphony worker;
- support narrow Unity Test Framework filters so workers do not default to expensive full-suite runs;
- keep Unity's active project on Windows NTFS rather than `\\wsl.localhost`;
- preserve the staged `Library/` cache across issues and branches;
- return test XML, Editor logs, and a small JSON summary to the worker workspace;
- require Phase 3 exclusive-resource ownership before a worker can execute Unity;
- keep production-scene authoring authority unchanged.

## Host shape

The evaluated Windows/WSL host provides:

- WSL2 Ubuntu;
- Windows PowerShell reachable as `powershell.exe`;
- `wslpath` for WSL/Windows path conversion;
- Unity Hub editors installed under `C:\Program Files\Unity\Hub\Editor`;
- RPG Kingdom currently declaring Unity `6000.3.10f1`.

The runner does not hard-code the current project version. `scripts/unity-runner.sh` reads `ProjectSettings/ProjectVersion.txt` on every health check/run and resolves the default editor path from that version. `RPGK_UNITY_EDITOR_WINDOWS` can override the editor executable when a host intentionally uses a nonstandard install location.

## Data flow

```text
Symphony WSL workspace (GH-N)
        |
        | supported runner only
        v
scripts/unity-runner.sh
        |
        | verifies Phase 3 unity-editor lock ownership
        | converts workspace path with wslpath
        v
Windows PowerShell bridge
        |
        | robocopy /MIR
        |   Assets/
        |   Packages/
        |   ProjectSettings/
        v
%LOCALAPPDATA%\RPGKingdomSupervisor\UnityStages\<version>\RPG-Kingdom
        |
        | Library/ is preserved between runs
        v
Unity.exe -batchmode -runTests ...
        |
        +--> .symphony-results\<run-id>\results.xml
        +--> .symphony-results\<run-id>\Editor.log
        +--> .symphony-results\<run-id>\summary.json
        |
        | copy evidence back
        v
WSL workspace/Logs/SymphonyUnity/<run-id>/
```

`Logs/` is already ignored by RPG Kingdom. Unity validation artifacts therefore remain available to Codex and the human reviewer without polluting the implementation branch.

## Why stage on Windows NTFS

Unity performs substantial AssetDatabase, import-cache, metadata, and temporary-file I/O. The source workspace is intentionally kept in the WSL Linux filesystem for Git/Codex performance, but the project Unity opens is a Windows-local staging copy.

The runner mirrors only the three Unity source/configuration directories that define the project:

- `Assets/`
- `Packages/`
- `ProjectSettings/`

It does **not** mirror the worker's `.git/`, `Library/`, `Temp/`, `Logs/`, or other generated state into the stage. `robocopy /MIR` removes staged source files that were deleted on the branch, while the staged `Library/` survives to make later runs substantially cheaper than the first import.

The staging directory is validation state, not a second source of truth. Changes made there are never synchronized back into RPG Kingdom.

## Supported runner interface

From an RPG Kingdom project root:

```bash
bash ~/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh health
```

For an issue workspace that currently owns the Phase 3 Unity resource:

```bash
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

`--filter` is passed to Unity Test Framework's `-testFilter` argument. It may therefore be a full/partial test name, semicolon-separated names, or a supported regular-expression filter according to the installed Unity Test Framework.

## Health check

`health` deliberately does not require the Unity lock. The Phase 3 preflight calls it **before** acquiring the resource.

It verifies:

- the source project contains `Assets`, `Packages`, and `ProjectSettings`;
- the project-declared Unity editor executable exists;
- Windows `robocopy.exe` is available;
- the persistent staging project root can be created/written.

A successful health check returns a compact JSON object containing the Unity version, editor path, source path, and stage path.

Phase 4 removes the Phase 3 manual `RPGK_UNITY_RUNNER_READY=1` assertion. Readiness is now determined from the real host/project on each Unity-resource dispatch.

## Test execution

The Windows bridge runs Unity with the native Test Framework command-line flow:

- `-batchmode`
- `-accept-apiupdate`
- `-projectPath`
- `-runTests`
- `-testPlatform EditMode|PlayMode`
- `-testResults`
- `-logFile`
- optional `-testFilter`

The runner intentionally does not force `-nographics`; RPG Kingdom PlayMode validation may need a graphics device.

The PowerShell bridge parses the NUnit-style `test-run` result XML. A run fails when:

- Unity exits nonzero;
- the result XML is missing or malformed;
- zero tests matched the requested filter;
- one or more tests failed;
- the aggregate result is not successful.

The JSON summary records model-independent evidence such as test platform/filter, total/passed/failed/skipped counts, Unity exit code, run ID, artifact path, and staging project path.

## Resource ownership

`health` may run without a lock. `editmode` and `playmode` may not.

For test execution, `unity-runner.sh` requires:

1. the project root to be a Symphony `GH-<number>` workspace;
2. the Phase 3 lock directory to exist;
3. its recorded owner to match that issue identifier.

This means increasing code-only concurrency later does not allow two workers to share the staged Unity Editor.

Workers are explicitly instructed not to invoke `Unity.exe`, `powershell.exe`, or ad-hoc Windows commands themselves. The supported runner is the orchestration boundary.

## Validation labels

The Phase 3 labels keep their existing meanings, but Phase 4 makes them executable:

- `resource:unity-editor` — acquire exclusive Unity ownership and expose the supported runner;
- `validation:unity-required` — the issue must have the resource and must produce relevant Unity runner evidence before clean PR handoff;
- `validation:unity-optional` — work may complete without editor evidence; if the resource was also granted, the worker may use the runner.

A required Unity issue that fails the host health check still halts before Codex starts, preserving the usage budget.

## First-run cost and cache behavior

The first run for a Unity version can be slow because the staging project has no `Library/` yet. That is expected. The stage is keyed by Unity version and retained across issue workspaces, so subsequent runs reuse Unity's import cache.

If the stage becomes corrupt, the operator may stop Symphony/Unity and remove the affected version directory under `%LOCALAPPDATA%\RPGKingdomSupervisor\UnityStages`. The next run rebuilds it from the authoritative WSL workspace.

## Environment overrides

The default host should not require these, but the runner supports:

```text
RPGK_POWERSHELL_EXE
RPGK_UNITY_EDITOR_WINDOWS
RPGK_UNITY_STAGE_ROOT_WINDOWS
RPGK_SUPERVISOR_STATE_ROOT
```

Do not put credentials in these values.

## Still deferred

Phase 4 does not add:

- automatic Unity builds;
- production-scene authoring permission;
- screenshot/visual-regression capture;
- Unity Workbench/editor GUI control;
- automatic full-suite validation for every issue;
- more than one concurrent Symphony worker;
- automatic PR merge or retry.

Those capabilities should be added only when they solve a measured bottleneck without weakening the current resource and review boundaries.
