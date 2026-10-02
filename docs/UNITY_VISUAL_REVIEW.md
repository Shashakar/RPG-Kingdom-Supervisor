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

The helper:

1. opens the exact requested scene;
2. resolves the exact requested camera when supplied, otherwise prefers `Camera.main` and then the first active enabled scene camera;
3. renders through a temporary `RenderTexture`;
4. writes a PNG and manifest;
5. exits Unity.

The helper exists only in the staged project. The next source mirror removes it.

Artifacts return to:

```text
Logs/SymphonyUnity/<run-id>/
├── scene.png
├── manifest.json
├── summary.json
└── Editor.log
```

`Logs/` remains ignored by RPG Kingdom.

## Automated review integration

The normal independent review worker now accepts image evidence. Before each review, the review orchestrator scans Unity artifacts for successful capture summaries and attaches up to the four newest images whose modification time is not older than the checked-out PR head commit.

This freshness requirement prevents a screenshot from an older implementation from silently influencing review of a newer head.

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

This path:

1. requires the issue to already be at the durable `symphony:human-review` gate;
2. reuses the existing GH workspace and verifies it still matches the reviewed PR head;
3. acquires the normal exclusive Unity resource without starting an implementation worker;
4. creates a fresh scene capture;
5. runs a new independent review cycle with that image attached;
6. releases the Unity resource even when capture or review fails.

A passing visual review leaves the issue at `symphony:human-review`. A concern or ambiguity moves it to `symphony:human-attention`. Retrospective review is intentionally observational: it never adds `symphony:ready`, `symphony:rearm`, dispatches a repair, consumes a repair attempt, or merges the PR.

## Current limits

This first implementation renders an Editor-time camera view. It does not yet provide:

- scripted Play Mode traversal;
- automatic camera-tour profiles;
- video capture;
- baseline pixel-diff visual regression;
- segmentation/depth/normal debug passes;
- automatic scene mutation based solely on visual critique.

Those should be added only when a concrete review need justifies them. The current path deliberately establishes the smallest trustworthy evidence loop first.
