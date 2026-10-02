# Unity Visual Review

Supervisor can now capture deterministic rendered evidence from an RPG Kingdom scene and attach fresh captures to the independent Codex review worker.

## Goals

- let automated review judge pixels instead of inferring scene quality from YAML, transforms, or hierarchy alone;
- keep Unity execution behind the existing host-owned broker and `resource:unity-editor` lock;
- keep captures out of source control;
- make stale visual evidence fail closed by attaching only captures generated after the current PR head commit;
- preserve human merge authority.

## Capture interface

From an active `GH-N` Symphony workspace that owns the Unity resource:

```bash
bash ~/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh capture \
  --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity
```

An exact camera hierarchy path may be supplied:

```bash
bash ~/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh capture \
  --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity \
  --camera 'World/Cameras/GameplayCamera' \
  --width 1920 \
  --height 1080
```

Supported resolution bounds are 320–4096 pixels wide and 180–4096 pixels high. The default is 1920x1080.

The worker-facing client never invokes PowerShell or Unity directly. It submits a typed `capture` request to the existing Unity broker. The host adapter stages `Assets`, `Packages`, and `ProjectSettings` on Windows and invokes `scripts/windows/run-unity-capture.ps1`.

## Staged capture implementation

The Windows runner injects a temporary Editor-only helper into the staged Unity project. It does not add capture code to RPG Kingdom.

The Windows capture path rebuilds the staged Unity `Library/` before the first visual capture in a review, then reuses that freshly imported Library for subsequent views in the same review. This prevents stale shader/import cache state from becoming visual evidence while avoiding a full reimport for every viewpoint.

The helper:

1. forces a synchronous asset refresh, opens the exact requested scene, and warms the scene's loaded shaders;
2. resolves the exact requested camera when supplied, otherwise prefers `Camera.main` and then the first active enabled scene camera;
3. optionally applies a temporary capture-only camera position plus either Euler rotation or a hierarchy-path look-at target; the production scene is never saved;
4. renders through a temporary `RenderTexture`; when an SRP is active it prefers Unity's native `RenderPipeline.StandardRequest` path and falls back to `Camera.Render()` only when that request is unsupported;
4. records capture-environment diagnostics for scene renderers/materials/shaders, including camera-frustum membership, material/shader asset paths, `Shader.isSupported`, render pipeline, graphics device, and the render method used;
5. extracts matching shader/compiler errors from `Editor.log`;
6. writes the PNG, manifest, diagnostics, and shader log;
7. exits Unity.

The helper exists only in the staged project. The next source mirror removes it.

Artifacts return to:

```text
Logs/SymphonyUnity/<run-id>/
├── scene.png
├── manifest.json
├── visual-diagnostics.json
├── shader-log.txt
├── summary.json
└── Editor.log
```

`Logs/` remains ignored by RPG Kingdom.

## Capture fidelity and render anomalies

Rendered pixels are evidence about the capture environment, not automatically proof about the shipped scene. In particular, magenta/pink output must be corroborated before the reviewer can classify it as a PR material defect.

For an attached capture the reviewer must inspect the sibling diagnostics. A material/shader failure can become `changes_required` when an in-camera-frustum renderer has a missing shader, reports `shaderSupported=false`, or the shader log contains a matching compile/unsupported-subshader failure. If the image is visibly corrupted but the relevant shaders report supported and the shader log is clean, the reviewer treats the capture as unreliable evidence and returns `blocked_or_ambiguous` with `reason=insufficient_evidence` instead of dispatching a material repair.

This distinction exists because batch/staged rendering can differ from the normal Editor/player render context. The visual-review system must not turn a tooling discrepancy into a production-art repair without corroborating evidence.

## Multi-view review profiles

Issues that require subjective spatial/readability review may declare up to four capture viewpoints in an issue-body metadata block:

```text
<!-- symphony-visual-review-requirements
{
  "views": [
    {
      "name": "settlement-overview",
      "camera": "ThirdPersonCamera",
      "position": [10, 7, -4],
      "lookAt": "Environment/BlockedExit",
      "fov": 55
    },
    {
      "name": "exit-approach",
      "camera": "ThirdPersonCamera",
      "position": [4, 2, 3],
      "rotation": [8, 125, 0],
      "fov": 60
    }
  ]
}
-->
```

Each view uses an existing scene camera as the rendering template. `position`, `rotation`, `lookAt`, and `fov` are temporary capture-time overrides only. A view may use either `rotation` or `lookAt`, never both. The profile is host-owned review configuration; it does not modify or save the production scene.

The first view performs a clean staged import. Later views reuse that freshly rebuilt stage Library, so a three- or four-view review does not repeatedly reimport the entire project.

## Automated review integration

The normal independent review worker now accepts image evidence. Before each review, the review orchestrator scans Unity artifacts for successful capture summaries and attaches up to the four newest images whose modification time is not older than the checked-out PR head commit.

This freshness requirement prevents a screenshot from an older implementation from silently influencing review of a newer head. Once at least one diagnostics-capable capture exists for the current head, legacy captures from the pre-diagnostics harness are excluded from that review so known-unreliable pixels are not mixed with corrected evidence.

When visual evidence is attached, the reviewer is instructed to evaluate:

- environment/world coherence;
- spatial readability;
- visual hierarchy;
- asset integration and obvious repetition;
- actor/target readability;
- whether the viewed space reads as an authored game environment rather than a test arena.

Visual findings must be grounded in the attached frame. Anything outside the captured view remains unassessed.

The image is supplemental evidence. A visually acceptable frame does not replace code, architecture, persistence, test, or Unity-validation review, and automated approval never authorizes merge.

## Retrospective review at the human gate

An issue that reached `symphony:human-review` before visual capture existed, or one that needs another visual inspection without another implementation lifetime, can use the host-owned one-shot command:

```bash
bash ~/src/RPG-Kingdom-Supervisor/scripts/visual-review-pr.sh \
  --issue 222 \
  --pr 225 \
  --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity
```

An exact camera path and dimensions may be supplied with the same `--camera`, `--width`, and `--height` options as the normal capture command.

The same path can be requested remotely in either of two one-shot forms while the issue is already at `symphony:human-review` or `symphony:human-attention`:

- add `symphony:visual-review`; or
- add an issue comment containing `<!-- rpgk-visual-review-request -->`.

The host review-orchestrator consumes the request, derives the exact scene from the issue's `symphony-scene-authoring-requirements` block, and runs the capture/review command. Label requests remove the request label after successful completion. Comment requests receive a completion receipt tied to the request comment ID, so restart/polling cannot replay the same request. No implementation dispatch label is added.

This path:

1. requires the issue to already have durable `human_review` or `human_attention` review state for the same PR head;
2. reuses the existing GH workspace and verifies it still matches the reviewed PR head;
3. acquires the normal exclusive Unity resource without starting an implementation worker;
4. creates a fresh scene capture;
5. runs a new independent review cycle with that image attached;
6. releases the Unity resource even when capture or review fails.

A passing visual review leaves or returns the issue to `symphony:human-review`. A concern or ambiguity leaves or moves it to `symphony:human-attention`. This permits a trustworthy recapture to supersede a prior visual-review false positive without rearming implementation. Retrospective review is intentionally observational: it never adds `symphony:ready`, `symphony:rearm`, dispatches a repair, consumes a repair attempt, or merges the PR.

## Current limits

This first implementation renders an Editor-time camera view. It does not yet provide:

- scripted Play Mode traversal;
- automatic camera-tour profiles;
- video capture;
- baseline pixel-diff visual regression;
- segmentation/depth/normal debug passes;
- automatic scene mutation based solely on visual critique.

Those should be added only when a concrete review need justifies them. The current path deliberately establishes the smallest trustworthy evidence loop first.
