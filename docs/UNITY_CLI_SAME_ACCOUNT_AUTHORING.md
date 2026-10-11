# Stage C3: Same-account Unity Editor authoring through Supervisor

**Operator decision (2026-10-11):** No second Windows account for RPG Kingdom's
current local development workflow. Use the existing Windows account with a
disposable NTFS Unity Editor project. The restricted-account prototype in
draft PR #213 is **optional / deferred**, not a prerequisite. This supersedes
the earlier #209 design gate requiring a separate Windows account.

## Capability

Supervisor's existing `unity-host-broker.py` accepts a new
`editor-staged-code` operation. An issue worker writes a Unity C# editor
expression to `AgentScripts/NAME.cs` inside its **own GH-N game worktree** and
runs:

```bash
bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh" \
  editor-staged-code \
  --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity \
  --script AgentScripts/NAME.cs
```

The request and exact script path pass through the existing GH issue
Unity-resource lease and request/ack/response broker. Only the broker's
host process calls the Windows Unity CLI `command eval --code ...`.
The worker cannot select the Windows stage or call CLI through the broker
directly. Script is read from a canonical non-linked `AgentScripts/<name>.cs`
path (max 64 KiB). The operator grant pins the GH-N worktree path, current Git
HEAD, exact Windows staged project and running Editor PID. The environment
flag defaults **off**; no grant is created automatically.

The script may modify **any Unity scene/asset within the disposable stage**,
including `PlaytestScene` and terrain. It must explicitly save scene/asset
changes using Unity Editor APIs. It can be invoked repeatedly on the same stage
without manual regrant after every scene edit. The host compares the staged
`Assets` inventory (SHA-256) before and after each invocation, returns the
new/removed/modified asset paths and hashes, and verifies that pinned Unity
project configuration files remain unchanged. There is **no automatic
copyback** into a source worktree, no commits, no auto-merge and no production
scene replacement. `requiresReview=true` always.

**Security tradeoff accepted by the operator:** This is locally **trusted C#**
running with the current Windows user's privileges. A C# script can technically
read/change files beyond the stage or use network/process APIs. The Git
workspace, stage provenance, script size, grants, and asset-diff reporting
provide workflow safeguards, **not OS security isolation**. Do not execute
untrusted third-party C# or use a model to handle credentials. A separate
restricted Windows user or VM can be revisited later.

## Existing-stage operator setup

Keep the Stage B Unity Editor open, with the original
`Assets/RPGKingdom/Scenes/PlaytestScene.unity` active and clean.
Do not run batchmode Unity concurrently. Stage must already have
`com.unity.pipeline 0.8.0-exp.1`, a reachable Pipeline endpoint, and the
correct Stage B receipt (Editor 6000.3.10f1, CLI 1.0.0-beta.8 verified in
prior experiments).

From the **trusted operator shell**, set these values in the environment
that starts Supervisor:

```bash
export RPGK_UNITY_STAGED_CODE_ENABLED=1
export RPGK_UNITY_CLI_EXE=/mnt/c/Users/Dex/AppData/Local/Unity/bin/unity.exe
```

After reviewing the intended **GH-N** worktree and Editor stage (no dirty,
halted GH-260 workspace), issue its grant:

```bash
python3 scripts/unity-cli-staged-code.py grant \
  --workspace "$RPGK_SYMPHONY_WORKSPACE_ROOT/GH-N" \
  --session-windows 'C:\Users\Dex\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-51ed865d003844629683ec72ba0125db' \
  --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity
```

The grant resides under `$RPGK_SUPERVISOR_STATE_ROOT/unity-staged-code-grants/GH-N.json`.
Any Git HEAD commit change requires regranting the revised workspace; ordinary
scene edits in the *disposable stage* do not require it.

A worker should author focused C# files, then call the typed request. For
simple generated scenes, use `EditorSceneManager.NewScene(...Additive)`,
`SaveScene`, restore the original active scene and `CloseScene`.
For modifying the existing playtest scene, call Unity Editor APIs against
that staged scene and explicitly `SaveScene`; changed scenes will appear in
the asset-diff receipt. Use multiple smaller scripts rather than a giant eval.

Revoke after the run:

```bash
python3 scripts/unity-cli-staged-code.py revoke \
  --workspace "$RPGK_SYMPHONY_WORKSPACE_ROOT/GH-N"
unset RPGK_UNITY_STAGED_CODE_ENABLED RPGK_UNITY_CLI_EXE
```

Revoke affects future commands. Stop/close a persistent Editor separately
using the Stage B owner-checked tools before batchmode compilation/testing.

## Real Windows smoke (temporary synthetic issue; no game source edits)

Before granting a real issue, the operator can run this against the
**existing live disposable stage**:

```bash
cd ~/src/RPG-Kingdom-Supervisor-UnityPilot
python3 tests/unity-cli-staged-code-test.py
RPGK_UNITY_CLI_EXE=/mnt/c/Users/Dex/AppData/Local/Unity/bin/unity.exe \
  bash scripts/unity-cli-staged-code-smoke.sh \
  --session-windows 'C:\Users\Dex\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-51ed865d003844629683ec72ba0125db'
```

The smoke script starts a temporary broker and synthetic `GH-999999` Git
workspace containing an agent-authored C# script, grants it to the existing
live Editor, and creates
`Assets/RPGKingdom/Generated/AgentBenchmarks/SupervisorCodeSmoke.unity`
in the **disposable project only**. It checks the staged `PlaytestScene`
SHA-256 is unchanged and returns the broker's asset-change receipt.
The script cleans its temporary WSL state; the generated Unity asset remains
inside the disposable stage for visual inspection. Do not run it twice
against the same stage.

## Windows acceptance — passed on October 11, 2026

The operator checked out `codex/209-unity-staged-editor-code` in the WSL
Supervisor pilot checkout and tested the already-running Stage B disposable
Windows Editor session
`pilot-51ed865d003844629683ec72ba0125db`.

- `python3 tests/unity-cli-staged-code-test.py`: **6 tests passed** (`Ran 6 tests in 0.104s; OK`).
- `RPGK_UNITY_CLI_EXE=... bash scripts/unity-cli-staged-code-smoke.sh
  --session-windows ...`: **PASS**, with a temporary synthetic issue
  (`GH-999999`), host broker and operator-account Unity CLI/Pipeline.
- Returned `success=true`, `operation=editor-staged-code`,
  `scriptPath=AgentScripts/Smoke.cs`, `stageOnly=true`,
  `copyback=false`, `assetCounts={added:2,removed:0,modified:0}`.
- Added only
  `Assets/RPGKingdom/Generated/AgentBenchmarks/SupervisorCodeSmoke.unity`
  and its accompanying `.unity.meta` file within the disposable stage.
- `originalStageSceneUnchanged=true`: the staged `PlaytestScene.unity`
  SHA-256 was identical before and after the operation. No game repository
  checkout files were copied, committed or merged.
- Terminal confirmation:
  `PASS: synthetic GH issue -> Supervisor broker -> agent C# -> staged scene; no copyback`.

**Interpretation:** Same-account broker-mediated C# script execution and
additive scene creation are verified on the actual Windows host. This smoke
does **not** demonstrate an autonomous dispatched Codex worker, iterative
modifications of the original staged terrain, Unity compilation/PlayMode
results, screenshot/geometry validation or selective source promotion.
It also does not provide Windows identity/filesystem sandboxing, which the
operator elected to defer. The smoke scene is intentionally retained
in the disposable stage; do not rerun the one-time smoke in that stage.

## Follow-up after read/write acceptance

1. Integrate the operation into a real, issue-owned Supervisor Codex task
   using an issue workspace spawned from clean game-main, not halted GH-260.
2. Add deterministic compile/test/scene geometry assertions and screenshot
   review against the stage with no competing Unity Editor session.
3. Implement a separate **human-approved** allowlisted file-promotion
   procedure with pre/post SHA-256 and Git HEAD checks, stage-to-source
   diff and rollback. Do not automatically copy edited assets.
4. Resume GH-260 only after we have credible validation of the staged terrain
   work and an explicit review/merge gate.

No Windows account provisioning is necessary for this lane.
