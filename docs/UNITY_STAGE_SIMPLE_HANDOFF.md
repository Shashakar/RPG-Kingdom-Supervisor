# Minimal Unity stage -> feature branch handoff (working-pipeline priority)

**Scope:** get the actual existing Windows Unity stage's selected asset changes into a normal RPG Kingdom codex feature branch, run the established Unity tests, then use the **existing Git handoff** to commit, push and create a PR for the owner. No new Windows service, privileged account, WebAuthn, separate nonce authority, freeze protocol or promotion-specific broker operation.

This is a deliberate operator workflow, not untrusted-worker-proof isolation. The user approved using the same Windows account for the disposable stage during initial integration. Keep production/main protected by the feature-branch PR review; do not auto-merge.

## Preconditions

- Supervisor has the C3 stage and an issue checkout under $HOME/code/rpg-kingdom-symphony-workspaces/GH-N.
- Stage exists at its known mapped WSL path (typically /mnt/c/... from the existing C3 receipt). Pass the actual stage mapping, never a guessed path.
- Finish the Unity work and save the scene. Stop further Unity edits while selecting/copying staged files. Closing Unity is sensible but is not a security protocol or a command this tool invokes.
- The GH-N checkout must be on clean main (or detached exactly at origin/main), with up-to-date origin/main. If it is not: review state and sync normally; the script will not reset/erase local changes.
- Use the issue-specific scene/authoring scope approved in RPG Kingdom AGENTS.md and docs/SCENE_AUTHORING.md. For #260, only approved World terrain/scene and generated related data/metadata should be selected; scene edits are authorized by that issue, not by this handoff tool.

## 1. Inspect staged differences without writing

From a trusted WSL operator shell:

    WS="$HOME/code/rpg-kingdom-symphony-workspaces/GH-260"
    STAGE="/mnt/c/ACTUAL_STAGE_PATH_FROM_C3_SESSION"
    SUP="$HOME/src/RPG-Kingdom-Supervisor"

    python3 "$SUP/scripts/unity-stage-simple-handoff.py" plan \
      --workspace "$WS" --stage "$STAGE"

The JSON report lists all changed Assets, Packages and ProjectSettings files. It does **not** copy anything. Review the exact file names before selecting. Changes in URP/project settings can remain excluded.

Preview an explicit scene selection and any automatically included new/changed Unity metadata:

    python3 "$SUP/scripts/unity-stage-simple-handoff.py" plan \
      --workspace "$WS" --stage "$STAGE" \
      --path Assets/RPGKingdom/Scenes/PlaytestScene.unity

For scene-created TerrainData assets, also repeat --path with each changed Assets/... terrain asset file; the tool auto-includes changed Unity .meta sidecars and changed parent-folder .meta files. No entire-directory glob, implicit all-asset copy, config copy or deletion is performed. A new Unity asset lacking its .meta is refused rather than silently generating a new GUID.

## 2. Copy to a NEW feature branch only

After inspecting the selection, run:

    python3 "$SUP/scripts/unity-stage-simple-handoff.py" apply \
      --workspace "$WS" --stage "$STAGE" \
      --branch codex/260-terrain-approach \
      --path Assets/RPGKingdom/Scenes/PlaytestScene.unity \
      --path Assets/RPGKingdom/Generated/EXACT_REVIEWED_TERRAIN_ASSET.asset

Replace the second example path with actual plan output, or omit it if no such asset exists. Any additional dependencies must be explicitly selected. The command:

1. Confirms the source workspace is clean at origin/main and the selected Assets files actually changed in the stage.
2. Includes changed/new .meta companions and new folder .meta files automatically; leaves unrelated stage changes out.
3. Makes an ordinary temporary copy with hash checks, creates a new codex branch, then writes those files **only after switching off main**.
4. Leaves changes uncommitted, returns excluded paths and instructs the normal Unity validation/Git PR handoff. It does **not** install or invoke any Windows service.

This is a simple productivity workflow. If a copy errors after switching branches, inspect the feature branch and its diff; nothing is automatically reset or force-pushed.

    cd "$WS"
    git status --short
    git diff --stat
    git diff -- Assets/RPGKingdom/Scenes/PlaytestScene.unity

Inspect binary/meta paths as well. Do not commit scene changes that exceed the issue authorization.

## 3. Run normal Unity validation, then existing PR handoff

Use existing host commands in the issue workspace:

    bash "$SUP/scripts/unity-runner.sh" health
    bash "$SUP/scripts/unity-runner.sh" editmode

Use the returned *actual passing validation run ID* for this issue. Then the existing Git handoff publishes the PR:

    bash "$SUP/scripts/git-handoff.sh" handoff \
      --branch codex/260-terrain-approach \
      --commit-message "Compose southern terrain approach" \
      --pr-title "GH-260: Southern starting pocket and terrain approach" \
      --pr-body "Terrain-stage changes selected and inspected; Unity validation: ACTUAL_RUN_ID. Awaiting owner scene review. Closes #260" \
      --validation-run ACTUAL_PASSING_RUN_ID

The Git host handoff already enforces issue workspace/origin, Git branch, Unity run evidence when required, safe history, push and PR creation against main. It **never merges**. Do not invent a successful run ID. An initial smoke test can use the generated proof scene from the C4 stage rather than the production scene when that stage and issue are available.

## Intentional limits

- No new general-purpose stage mutation API exposed to Codex. This is an operator command and existing Git handoff workflow.
- Only explicitly selected, present changed Assets files can be copied. No stage deletions or ProjectSettings/Packages copies in the first end-to-end slice. Review excluded paths and follow up if needed.
- No protected snapshot or Windows OS-enforced lock. A cooperating operator keeps Unity idle during copy; the temporary byte copy and digests catch moving selected files during sampling but are not a security barrier.
- This command does NOT itself run Unity or create the GitHub PR; it uses the existing, already-tested tools for those jobs.
- If a Unity validation or end-to-end host test fails, fix the specific pipeline defect. Do not add a new identity service or another independent approval system to troubleshoot it.

## Test coverage

    python3 tests/unity-stage-simple-handoff-test.py

Synthetic tests create a real temporary Git origin and issue checkout, a disposable fake Unity stage and a modified real scene file; they assert the selection, metadata inclusion, exclusion of unrelated URP changes, new branch creation before writes, baseline main not advancing, rejection of unsafe paths, dirty main and linked assets. Tests do not claim the Windows Unity Editor was operated from GitHub CI.

This MVP does **not** change the existing C5a-e read-only utilities. The C5f/C5h service work in draft PRs #229-232 is **deferred and intentionally excluded from this pipeline**. Keep human PR review/merge, and get one working end-to-end Unity pipeline before considering additional hardening.
