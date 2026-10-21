# Autonomous Claude — Rekindle visual previews (opt-in, staged-only)

This is a **narrow visual-experiment lane**, not general Unity authoring. It lets an explicitly authorized Claude issue propose a bounded **data-only** two-part scene specification; a host-owned fixed Unity Editor method renders screenshots on a separate Windows staging project, and Claude evaluates those images. No Unity scene, prefab, FBX, clip, licensed source, or other Unity project asset is committed or copied back from the staged project.

The normal code-only Claude dispatcher still prohibits Unity assets and scene changes. The Codex Unity migration gate remains disabled by default; **do not set `RPGK_REKINDLE_UNITY_READY=1` to activate this feature**. Host activation and independent visual review remain separate.

## Prerequisites

1. Supervisor PR #278 is independently reviewed and merged and the host checkout updated. This feature is **not production-validated by CI**: Linux CI mocks the behavior, and the Windows Unity runner needs a deliberate host smoke with expendable assets before deployment.
2. The Windows system account running Unity must be able to access Unity **6000.3.10f1** (or the version declared by `ProjectSettings/ProjectVersion.txt`) and `powershell.exe`/WSL interop. All Unity processes must be closed when the host runner takes the editor resource. The Supervisor runs only **one** request at a time using the shared Unity lock.
3. The host has an independently accessible local Unity project with the user's approved installed asset packages. The Windows paths must be absolute and owned by the operator. It is a separate source only for *local* licensed content; the main issue workspace is cloned from `rekindle-vertical-slice`.
4. The operator must confirm relevant Unity Asset Store publisher/EULA permission for AI-assisted visual review of the purchased assets. **A purchased license is not by itself proof of consent to use its source as an input to an AI system.** This lane sends rendered PNGs to the Claude model and is not suitable for packages without appropriate permission. The user must make this determination before setting the approval flag.
5. Before enabling, run a staged no-licensed-content proof with expendable, operator-owned assets. Verify screenshot quality, the exact Unity runner path, no SaveService/PlayMode calls, and byte-for-byte unchanged real save files and local purchased source packages. If Windows Unity compilation or renderer setup fails, leave this lane disabled and attach logs to PR #278.

### Operator environment

The following are **independent** of `RPGK_CLAUDE_AUTONOMOUS=1`:

| Variable | Purpose |
| --- | --- |
| `RPGK_CLAUDE_VISUAL_UNITY_ENABLED=1` | Allow the reviewed visual dispatcher lane only |
| `RPGK_CLAUDE_VISUAL_HOST_READY=1` | Attests host smoke and staged Windows Unity capability |
| `RPGK_CLAUDE_LICENSED_VISUAL_REVIEW_APPROVED=1` | Explicit operator authorization for rendered purchased-asset screenshots to be examined by the model |
| `RPGK_CLAUDE_VISUAL_ASSET_PROJECT_WINDOWS` | Absolute Windows path to **local** imported licensed assets project, not the issue workspace |
| `RPGK_CLAUDE_VISUAL_LICENSED_ROOTS` | Semicolon-separated set of exact, owner-approved top-level folders under that project's `Assets/`, e.g. `Kevin Iglesias;AssetStoreLocal` |

Never pass a GitHub token, package-manager credential, or arbitrary folder path to a model. Set these on the host service **only after** reviewing the permissions and completing the stage smoke. The example does not mean approval has been granted for those named packages.

With incomplete host capabilities, an authorized queued issue is reported `blocked` and remains queued; the dispatcher does **not** spend a model turn. Missing required labels or malformed issue authority fail closed. A crashed Unity/host run deliberately retains the editor lock for human inspection; do not delete or reclaim it without proving the Windows request stopped and its stage is safe.

## Exact issue authorization

Human-approved issue labels **all required**:

```
agent:claude
work:visual-unity
resource:unity-editor
validation:unity-required
authoring:scene-new-composition
symphony:claude-ready
```

Do **not** use `symphony:ready`, `agent:codex`, another `authoring:` label, `validation:unity-optional`, or `completion:report-only`. Add this exact human-authored block in the issue body (replace issue `17` with the actual GitHub issue number):

```text
<!-- rpgk-claude-visual-authority
{"protocolVersion":1,"issue":17,"phases":["character-comparison","environment-cohesion"],"allowLicensedScreenshots":true}
-->
```

The issue must also specify allowed visual design questions and human approval boundaries. The visual issue should use a fresh `GH-N` workspace and a dedicated `claude/gh-N-implementation` branch. **No automatic merge.**

## Claude visual request file

Claude may edit exactly `Docs/VisualExperiments/GH-17/request.json` in its own clean workspace. The host validates a strict subset of fields, two model candidates, 2–18 bounded objects, 1–6 named shots per phase, bounded transforms, and an explicit per-phase camera. Example (illustrative paths; actual files must be present in the owner's approved local imported project or issue workspace):

```json
{
  "protocolVersion": 1,
  "issue": 17,
  "experiments": [
    {
      "kind": "character-comparison",
      "objects": [
        {"id":"blink","role":"character","asset":"Assets/AssetStoreLocal/Blink/Model.fbx","position":[-2,0,0],"yaw":0,"scale":1},
        {"id":"swordman","role":"character","asset":"Assets/AssetStoreLocal/Swordman/Model.fbx","position":[2,0,0],"yaw":0,"scale":1}
      ],
      "shots":[{"id":"idle","clip":"Assets/Kevin Iglesias/Motions/Idle.anim","clipName":"","seconds":0.3}],
      "camera":{"position":[0,2,-12],"lookAt":[0,1,0],"fov":45}
    },
    {
      "kind":"environment-cohesion",
      "objects":[
        {"id":"selected","role":"character","asset":"Assets/AssetStoreLocal/Blink/Model.fbx","position":[0,0,0],"yaw":0,"scale":1},
        {"id":"townhouse","role":"building","asset":"Assets/AssetStoreLocal/Townsmith/House.prefab","position":[5,0,3],"yaw":0,"scale":1},
        {"id":"tree","role":"nature","asset":"Assets/AssetStoreLocal/Fantasy/Tree.prefab","position":[-5,0,3],"yaw":0,"scale":1}
      ],
      "shots":[{"id":"wide","clip":"","clipName":"","seconds":0}],
      "camera":{"position":[0,4,-15],"lookAt":[0,1,0],"fov":52}
    }
  ]
}
```

For character comparison, a shot must specify a **shared** Kevin Iglesias clip applied to both character candidates. PR #3's Swordman is not automatically in `rekindle-vertical-slice`; it must exist as an operator-approved local preview source. Never import its FBX or texture to Git. The first trial may stop if required local-only files aren't installed; update the approved paths, do not bypass checking.

## Trusted execution & evidence boundary

1. The dispatcher validates exact labels, issue authority, separate host capabilities and clean workspace **before** its first model turn.
2. The confined Claude CLI creates a JSON plan only; no Bash, shell tools, web, arbitrary Unity Editor methods or executable asset authoring are granted.
3. The host validates that plan; takes `$RPGK_SUPERVISOR_STATE_ROOT/locks/unity-editor.lock`, stages `Assets`, `Packages`, `ProjectSettings` plus exact approved locally owned asset roots into the separate Windows stage; invokes the **fixed** `SupervisorRekindleVisualTrial.Execute` Editor method; creates stage-only preview scenes and screenshots.
4. Only `Docs/Evidence/VisualExperiments/GH-N/*.png` plus `summary.json` return to the issue workspace. Claude's second confined turn reviews the rendered evidence and writes only `Docs/VisualExperiments/GH-N/review.md`.
5. The host verifies exact screenshots, summary, request SHA256s and host-only Unity receipt before Git broker commit/push/draft PR. The independent reviewer and a human make the final decision. **A successful capture is not final art approval.**

Source scenes, generated preview scenes, FBX/prefab/animation bytes and any local paid source remain on the operator-owned Windows stage or licensed Unity project, never in the submitted pull request. No Hearthmere production scene edits, PlayMode, save manipulation, generic Editor RPC, production builds, package installs, or auto merge are in scope.

### Recovery

On crash/timeout, the Claude visual journal retains `visual_started` or `visual_evidence`, and dispatch does not replay automatically. Inspect the outstanding Windows Unity PID, request staging directory, `claude-visual/GH-N.json` receipt and resource lock before a deliberate one-shot rearm. The current v1 stage produces one evidence generation per issue; rework with existing screenshots may require a new explicitly approved generation. Do not delete or overwrite human review evidence to make a second run appear fresh.

### Validation

```bash
python3 tests/claude-visual-contract-test.py
python3 tests/claude-dispatcher-test.py
python3 tests/rekindle-migration-test.py
bash tests/run.sh
```

CI is intentionally unit/mocked only. The staged Windows PowerShell/Unity C# render path has to be validated on the user's real Windows Unity installation before any real licensed assets are imported or a Claude visual issue is armed.
