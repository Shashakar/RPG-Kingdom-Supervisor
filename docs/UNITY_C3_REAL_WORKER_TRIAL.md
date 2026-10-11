# C3 real-worker acceptance — RPG Kingdom #261

This is the **first real Symphony/Codex-dispatched** use of the same-account
Unity C# Editor path from Supervisor #209 and C3 PR #214. It is **report-only**:
the game repository will remain clean, and staged scene assets will not be
copied back or merged. The operator deliberately deferred a second Windows
user; this is not an OS security sandbox.

## State and constraints

- RPG Kingdom issue: https://github.com/Shashakar/RPG-Kingdom/issues/261
- Required labels already on that issue: \`completion:report-only\`,
  \`resource:unity-editor\`, \`validation:unity-optional\`.
- **Do not add \`symphony:ready\` yet.** Setup must succeed first.
- No \`authoring:scene-*\` label: this test creates and edits an **additive,
  stage-only fixture**, not a production scene or codebase.
- Game \`GH-260\` remains halted. The existing modified/synthetic Windows
  stage is **not** suitable for this real worker trial.
- The Supervisor stage is on the user's existing Windows identity and uses
  Unity Editor 6000.3.10f1 / Unity CLI 1.0.0-beta.8 /
  \`com.unity.pipeline 0.8.0-exp.1\`.
- Only the running operator-owned host's \`unity-host-broker.py\` calls CLI.
  Codex never calls \`powershell.exe\`, the native CLI or the editor itself.

## Operator setup: trusted WSL shell

### 1. Install this Supervisor branch at its canonical path

\`WORKFLOW.md\` and the Supervisor service refer to
\`$HOME/src/RPG-Kingdom-Supervisor\`; the prior test checkout named
\`RPG-Kingdom-Supervisor-UnityPilot\` is **not** sufficient for live workers
unless the canonical checkout also contains the new code.

In WSL, if there is not already a canonical checkout:

\`\`\`bash
git clone --branch codex/209-real-worker-editor-proof \
  https://github.com/Shashakar/RPG-Kingdom-Supervisor.git \
  "$HOME/src/RPG-Kingdom-Supervisor"
\`\`\`

If it **already exists**, do not overwrite it or run commands that discard
local edits. Check \`git -C "$HOME/src/RPG-Kingdom-Supervisor" status --short\`;
only on a clean checkout:

\`\`\`bash
cd ~/src/RPG-Kingdom-Supervisor
git fetch origin
git switch -c unity-real-worker \
  --track origin/codex/209-real-worker-editor-proof
\`\`\`

The canonical checkout's \`scripts/unity-cli-staged-code.py\`,
\`scripts/unity-host-broker.py\`, \`scripts/unity-runner.sh\`, and
\`WORKFLOW.md\` must all be from the same branch.

### 2. Prepare a clean, persistent game issue workspace

\`\`\`bash
cd ~/src/RPG-Kingdom-Supervisor
python3 scripts/unity-c3-real-worker-setup.py prepare --issue GH-261
\`\`\`

The helper creates \`$HOME/code/rpg-kingdom-symphony-workspaces/GH-261\`
only if absent, cloning game \`main\`, or checks an existing checkout.
It requires the proper game origin, no tracked/untracked changes and
\`HEAD == origin/main\`. It neither marks the issue ready nor launches an
Editor. It does not touch the existing halted GH-260 workspace.

### 3. Stop the old disposable pilot Editor (operator-only)

There must not be two Unity Editor processes running during this trial.
Finish any running Supervisor worker first and release its lease.

If the earlier Stage B Editor still runs, use **its exact owned session**:

\`\`\`bash
bash scripts/unity-cli-stage.sh stop \
  --session-windows 'C:\Users\Dex\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-51ed865d003844629683ec72ba0125db'
\`\`\`

This requests a safe close rather than forcibly killing the Editor. Confirm
it has fully exited using \`unity-cli-stage.sh status\`. Do not delete the old
stage; it contains the successful test scenes and prior evidence.

### 4. Create a NEW Windows stage from this exact GH-261 checkout

\`\`\`bash
WS="$HOME/code/rpg-kingdom-symphony-workspaces/GH-261"
START_JSON="$(bash scripts/unity-cli-stage.sh start --source "$WS")"
printf '%s\n' "$START_JSON"
STAGE="$(printf '%s\n' "$START_JSON" | jq -r 'select(.status == "started") | .sessionPath')"
test -n "$STAGE" && test "$STAGE" != null

bash scripts/unity-cli-stage.sh install-pipeline --session-windows "$STAGE"
bash scripts/unity-cli-stage.sh discover --session-windows "$STAGE"
\`\`\`

Wait for the Editor's initial project import to finish before discovery and
grant. If Pipeline installation or discovery fails, stop here and report its
actual output. The Stage B session receipt for this NEW stage records
\`sourceProjectPath\`; no prior pilot stage has that new field.

### 5. Issue the operator-side grant

\`\`\`bash
export RPGK_UNITY_CLI_EXE=/mnt/c/Users/Dex/AppData/Local/Unity/bin/unity.exe
export RPGK_UNITY_STAGED_CODE_ENABLED=1

python3 scripts/unity-c3-real-worker-setup.py grant \
  --issue GH-261 --session-windows "$STAGE"
\`\`\`

This checks the actual game workspace's current \`origin/main\` SHA, a clean
Git index/worktree, the Stage B receipt's exact source checkout, equality of
the original PlaytestScene and ProjectVersion bytes in stage/source, absence
of the new fixture output, live Pipeline ownership/PID and the clean active
scene. If the Editor initially opened an empty scene, it uses one fixed
operator-owned command to open the existing PlaytestScene **in the stage**.
It then issues the existing C3 GH-261 grant. Output status must be
\`ready-to-arm\` and include the Editor PID and original scene hash.

Leave both environment variables exported for the Supervisor host process.
No model worker should be able to issue or change operator grants.

### 6. Start the real Supervisor and then mark the issue ready

From the canonical checkout, with the same exported environment:

\`\`\`bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/run-symphony.sh
\`\`\`

Confirm the host-owned broker and Git handoff services are healthy.
**Only then** apply the label \`symphony:ready\` to RPG Kingdom issue #261
(using the GitHub issue UI or authorized operator tooling). Do not mark it
ready before the Windows Editor and grant exist.

The real dispatched Codex worker's \`before_run\` hook should acquire the
exclusive \`resource:unity-editor\` lease. The issue is
\`completion:report-only\` and \`validation:unity-optional\`; there is no
mandatory batchmode readiness smoke competing with the persistent Editor.

## What the worker must deliver

Inside the real \`GH-261\` workspace, the worker should put temporary C#
scripts at \`Logs/SymphonyUnity/AgentScripts/Create.cs\`,
\`Modify.cs\`, and \`Verify.cs\`. These paths are ignored by Git.

The worker invokes the existing broker for each script, e.g.:

\`\`\`bash
bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh" \
  editor-staged-code \
  --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity \
  --script Logs/SymphonyUnity/AgentScripts/Create.cs
\`\`\`

Issue #261 precisely defines the two-step scene creation/modification, final
transform assertions, allowed generated asset path and clean gameplay scene.
The worker must inspect the broker receipts, record all changed assets and
hashes, confirm no actual source worktree edits, and post a final report via
\`git-handoff.sh report-complete\`. The host checks clean Git source
\`HEAD == origin/main\`, posts the durable comment and sets
\`symphony:report-complete\` without an empty PR or commit.

If Unity returns an error, the asset audit shows an unexpected path, the
issue's project HEAD changes, or the broker returns no grant, the worker
should record an exact blocked/incomplete status and stop, without bypassing
the host interface.

## After acceptance

Keep issue #261's report for review; don't automatically close/merge anything.
Revoke its grant before releasing the staged Editor to another issue:

\`\`\`bash
python3 scripts/unity-cli-staged-code.py revoke --workspace "$WS"
unset RPGK_UNITY_STAGED_CODE_ENABLED RPGK_UNITY_CLI_EXE
\`\`\`

This acceptance confirms a real worker can drive iterative scene authoring in
an issue-bound stage. Only after examining that evidence should Supervisor
implement **human-approved** selective asset copyback plus Unity compilation,
EditMode/PlayMode, geometry, navigation and screenshot review gates for
production work such as GH-260.

**Do not confuse Stage C3's trusted same-account code path with an OS sandbox,
and do not treat a passing scene smoke as approval to merge production changes.**
