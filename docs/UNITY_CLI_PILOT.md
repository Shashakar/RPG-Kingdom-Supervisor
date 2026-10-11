## Stage B: disposable Editor host trial

The operator-only NTFS stage lifecycle is documented in [UNITY_CLI_STAGE.md](UNITY_CLI_STAGE.md). It adds a separately controlled start/status/discover/install-pipeline/stop sequence, with no Codex worker dispatch integration and no source project copyback. Real Windows verification is still required before enabling the interactive broker lane.

# Official Unity CLI/Pipeline pilot (Supervisor #205)

**Status: design plus read-only operator diagnostic, NOT live Unity integration.**
This PR does not enable any new worker tools or scene mutations. The Windows
host, Editor process, Unity license and Pipeline package have not been
accessed or validated from this environment.

## Verified sources (October 11, 2026)

* Official Unity CLI (experimental): https://docs.unity.com/en-us/unity-cli/use-unity-cli
* Unity Pipeline installation and local Editor HTTP API (Unity 6.0+): https://docs.unity.com/en-us/unity-cli/unity-pipeline/unity-pipeline-package
* Unity deprecation notice: https://docs.unity.com/en-us/unity-cli/replace-mcp-server-unity-cli
* October 7 Unity CLI beta.13 release: https://docs.unity.com/en-us/unity-cli/release-notes
* IvanMurzak Unity-MCP: https://github.com/IvanMurzak/Unity-MCP
* IvanMurzak Unity-AI-Terrain: https://github.com/IvanMurzak/Unity-AI-Terrain
* Coplay MCP for Unity: https://github.com/CoplayDev/unity-mcp

Unity deprecated the **MCP server embedded in its in-Editor AI Assistant**,
not the official CLI MCP command. The CLI provides 'unity mcp', 'unity command'
and 'unity eval'. Unity documents direct command/eval use as faster and
less token-intensive than MCP for shell-capable clients. The CLI remains
experimental and the Pipeline package must be installed in a compatible Editor.
Do not assume the October 7 flag set exists on the operator host until checked.

### Comparison

| Approach | Strength | Weakness | Role |
| --- | --- | --- | --- |
| Official CLI + Pipeline | First party, direct commands and MCP, supports project selection | Experimental, needs staged package install and a running Editor | **Primary pilot** |
| In-editor AI Assistant MCP server | Existing assistant package in game manifest | Officially deprecated server component | Do not extend |
| Coplay MCP for Unity v10.3.0 | Broad scene, GameObject, UI, assets, test APIs; MIT | Additional Python process and package; must pin/test | Fallback |
| IvanMurzak Unity-MCP | General Editor tools, extensible, Codex-aware | Separate runtime/server/auth | Alternate fallback |
| IvanMurzak AI Terrain | Terrain sculpting, painting, neighbor stitching | Depends on base Unity-MCP; real 6000.3.10f1 coverage unknown | Conditional supplement |
| Standard Editor scripting + batchmode | Full Unity API; existing proven validation and typed execution | Editor startup/import cost, custom code maintenance | Preserve as validation/fallback |

There is no verified comparative benchmark of latency or reliability on this
host. The package capabilities above are documented, not local test evidence.

## Repository inspection and current boundary

Supervisor's 'WORKFLOW.md' dispatches a GitHub issue into an isolated GH-N
working tree with a dedicated codex/ branch. Agent concurrency is currently 1,
max turns 4. Its host-side unity resource guard, Windows broker, local stage,
EditMode/PlayMode runner, scene inspector, camera capture, authoring receipts
and Git handoff already exist. The broker was introduced after Codex worker
processes proved unreliable at the WSL-to-Windows execution boundary.

The game project is Unity 6000.3.10f1. Its package manifest contains
'com.unity.ai.assistant' at 2.19.0-pre.2. **No Pipeline installation has
been verified**; do not edit that manifest merely to satisfy a diagnostic.

The typed executor supports many bounded operations, including Terrain
creation, editing, deletion, and NavMesh baking. The bottleneck is not lack
of a scripting API; it is the per-operation authoring policy and limited
spatial/visual autonomous inspection and retry loop.

RPG Kingdom #260 is halted with speculative dirty terrain/NavMesh work.
The newest owner clarification authorizes a **new southwest village entrance**.
Do not impose earlier one-existing-entrance restrictions or assume its old
dirty branch is a valid baseline.

## Target architecture

    GitHub issue + explicit authoring-lane approval
      -> bounded Codex worker in GH-N + codex/ branch
      -> issue/lease-bound request to existing operator-owned broker
      -> Windows restricted Editor identity (OS-level file isolation)
      -> issue-specific NTFS stage + one persistent Unity Editor
      -> Unity CLI/Pipeline command invocation (or selected MCP functions)
      -> structured inspection, edit, compile, validate, screenshot loop
      -> staged asset diff, receipts, run IDs, human-review PR

The worker-facing entry point should remain small. Prefer CLI command transport
for shell-capable Codex, with MCP used only for necessary features not available
through the direct CLI. Do **not** expose a general Windows PowerShell endpoint.
The CLI must run at the Windows host broker boundary, **not** in Codex's WSL
sandbox directly. Target the exact project via '--project-path' (and, when
appropriate, 'UNITY_PROJECT_PATH'); do not depend on whichever Editor the
CLI discovers by accident.

### Persistent Editor ownership and concurrency

Allocate one active Editor per Unity resource lease, not one global editor
shared simultaneously by unrelated branches. Prefer issue-specific NTFS stages
and caches. Complete source mirroring before starting the Editor; never
overwrite Assets while the Editor has them loaded. The current Windows
batchmode runner rejects any existing Unity.exe, so final verification must
either use Pipeline's Editor test facilities **or** close the leased Editor
cleanly before invoking the existing runner. Do not kill an unrelated human
Editor to free the resource.

Recovery requires a durable lease, PID, project path, stage revision and
successful operation receipt; a crashed or mismatched Editor is not safe to
reuse. Compare cold/warm stage imports, cache disk cost, task wall time,
worker tokens, validation failures and number of human interventions before
declaring any performance win.

### Security: Git branch != OS sandbox

An arbitrary Editor C# snippet can use the Windows file system or execute
external commands regardless of the Git branch. A disposable branch alone
does **not** provide host isolation. Before allowing general 'unity eval'
or arbitrary agent-generated Editor scripting, run the Editor with a
separate low-privilege Windows identity or equivalent OS-enforced sandbox,
restrict filesystem ACLs to the disposable stage and required Unity caches,
remove GitHub/Supervisor credentials, and limit network access.

The host must still enforce the issue's specific scene approval, one-editor
lease, project identity, output limits, timeout, diff/copyback allowlist,
review gate and explicit rollback. Broad *Editor authoring* is the goal,
not unrestricted host shell access. If this isolation is unavailable, fail
closed on arbitrary Editor C# rather than pretending Git isolation suffices.

## Automated acceptance loop

1. Inspect the live Editor scene hierarchy, Terrain objects, TerrainData
   bounds and height samples, colliders, NavMesh surfaces and protected roots.
2. Create or update scene, prefab, UI or Terrain content through Editor APIs
   in the disposable stage. Track touched assets and idempotent work units.
3. Recompile and run real EditMode and PlayMode tests; retain Unity result
   XML/logs, run identifiers and editor revision.
4. Test physical routes using ground probes, collision, corridor widths,
   terrain seam elevations, complete NavMesh path samples, off-route
   barriers and occlusion raycasts. Existence of NavMeshSurface is insufficient.
5. Capture visual evidence from exact poses; inspect both canonical overhead
   and player-eye frames and compare against the stated acceptance criteria.
6. Iterate on a bounded attempt budget; report unresolved spatial/visual
   failures instead of asserting success.
7. Verify the *attested* changed assets and protected gameplay references;
   submit a PR with evidence and wait for human approval. No scene auto-merge.

### #260 benchmark

Use a clean branch from game main, not the halted GH-260 dirty workspace.
Preserve original village floor, buildings, dungeon, restoration and gameplay.
Build an adjoining southwest Terrain starting pocket with one walkable pocket
exit and a new southwest village opening; verify a modest winding playable
approach, gradual village reveal, Terrain seams, collision and continuous
NavMesh. Use the owner's overhead camera position (0,200,20), Euler
(90,0,0) for *both* captures, with additional southwest entry and
spawn-eye images. A visually plausible screenshot alone does not prove
ground-level path feasibility.

## Migration gates and rollback

| Stage | Enablement gate | Rollback |
| --- | --- | --- |
| A. This PR | Read-only CLI/Pipeline diagnostic; mock wrapper tests | Remove script; existing Supervisor unchanged |
| B. Disposable pilot | Verify CLI version, licensing and Unity 6000.3.10f1 Pipeline install on Windows NTFS staged project | Remove staged package; no game-main changes |
| C. Restricted Editor job | Host-owned transport/lease/timeout and OS isolation tests | Disable feature flag, use old broker |
| D. Test-scene authoring | Real compile/tests/screenshots, attested asset diff; no credential/path escapes | Existing typed scene authoring |
| E. #260 | Clean baseline plus terrain/collision/NavMesh/visual evidence and human review | Halt new lane; preserve GH-260 evidence |
| F. General rollout | Cost/reliability benchmark meets stated targets | Feature flag off; old runner retained |

Do not replace the current Git handoff or Unity lock. Do not add Unity
Pipeline or third-party MCP to production 'main' in Stage A.

## Stage A operator procedure

From the operator WSL shell with this PR checked out:

    bash scripts/unity-cli-pilot.sh
    bash scripts/unity-cli-pilot.sh --project-windows 'C:\UnityStages\GH-205'
    bash scripts/unity-cli-pilot.sh --cli-windows 'C:\Program Files\Unity CLI\unity.exe'

The first command checks Windows CLI version and whether 'unity pipeline list'
can run. The second also attempts a read-only 'unity command --project-path'
against an actual Windows NTFS Unity project with Assets and ProjectSettings.
This is intended for a **disposable test stage**, not a production scene.
Only exact-project command discovery sets readyForProjectPilot=true.

No parameter runs an arbitrary CLI command. Output is compact JSON without
raw CLI stdout/stderr; nonzero exit codes indicate discovery problems.
This diagnostic does not install Pipeline, configure Codex or change a scene.

Local mock test:
    bash tests/unity-cli-probe-host-test.sh

These tests exercise argument forwarding and non-mutating command boundaries;
they do **not** validate PowerShell runtime semantics or Unity itself.
The actual Windows-host run and Unity scene validation remain **unperformed**.
