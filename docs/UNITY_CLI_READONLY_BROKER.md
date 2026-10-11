# Stage C1: host-brokered read-only Editor inspection (#209)

**Status:** draft, opt-in, awaiting actual Windows broker smoke test.
This branch stacks on Supervisor PR #208; merge it only after #208.

## Worker capability

When explicitly enabled and granted, an authorized GH-N Codex worker uses
the existing request client:

    bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh" \
      editor-scene-summary \
      --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity

The client sends a typed request to the existing host-owned Unity broker,
which enforces the GH-N workspace and exclusive Unity resource lock. Only
the host invokes the fixed Unity CLI command `get_scene_hierarchy` and
summarizes the JSON. No raw 12,087-object dump enters the Codex context.

The grant, stored outside the worker workspace under Supervisor state at
`unity-cli-grants/GH-N.json`, binds exactly the issue, real worktree path,
current Git HEAD, stage path, Stage B Editor PID, and expected active scene.
The host verifies the Stage B `session.json` receipt and matches the PID,
path and reachable Pipeline entry before calling the Editor. It denies if
the active scene differs or has unsaved changes.

It does not expose arbitrary CLI commands, `unity eval`, C# code,
scene mutation, package installation, copyback, or arbitrary Windows paths.
The inspector has a 16 MiB response limit and 90-second CLI timeout.
Normal broker locking, deadlines, and cancellation still apply.

## Operator setup (WSL, not a worker shell)

Choose a **clean issue worktree** at the configured Symphony workspace root
(e.g. `$HOME/code/rpg-kingdom-symphony-workspaces/GH-209`). Do not use the
halted dirty GH-260 workspace. Stage B must already have created a dedicated
Windows NTFS project and confirmed Pipeline connectivity; close other Editors.

In the trusted operator environment that launches `run-symphony.sh`:

    export RPGK_UNITY_CLI_READONLY_ENABLED=1
    export RPGK_UNITY_CLI_EXE=/mnt/c/Users/Dex/AppData/Local/Unity/bin/unity.exe

Issue a grant from this Supervisor checkout, substituting the actual workspace,
stage and scene:

    python3 scripts/unity-cli-readonly.py grant \
      --workspace "$HOME/code/rpg-kingdom-symphony-workspaces/GH-209" \
      --session-windows 'C:\Users\Dex\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-REPLACE_WITH_ID' \
      --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity

The grant is `0600` outside worker source. The host flag does **not** create a
grant automatically. After changing the worktree HEAD, stage or Editor PID,
an operator must review and issue a new grant. Begin Symphony with the same
exported environment, from a Supervisor checkout containing this change.
The worker can then call `editor-scene-summary`, without knowing the Windows
project location.

Revoke by running, in the operator shell:

    python3 scripts/unity-cli-readonly.py revoke \
      --workspace "$HOME/code/rpg-kingdom-symphony-workspaces/GH-209"

Then disable for future Supervisor starts with
`unset RPGK_UNITY_CLI_READONLY_ENABLED RPGK_UNITY_CLI_EXE`.
Revoke affects future requests; cancel/stop the existing broker for in-flight
work as appropriate. Keep using the legacy batchmode runner only when no
persistent Editor is active and the Unity lock has been released.

## Acceptance and limits

    python3 tests/unity-cli-readonly-test.py
    bash tests/unity-cli-stage-host-test.sh

Tests exercise revision/scene binding, dirty scene refusal, grant/revoke, PID
and Pipeline matching, compact hierarchy, and broker issue lock.

### Real Windows end-to-end broker acceptance (isolated)

With the already-running Stage B pilot Editor open in `PlaytestScene`, execute
this from the new Supervisor checkout on WSL (only after reviewing the script):

```bash
RPGK_UNITY_CLI_EXE=/mnt/c/Users/Dex/AppData/Local/Unity/bin/unity.exe \
  bash scripts/unity-cli-broker-smoke.sh \
  --session-windows 'C:\\Users\\Dex\\AppData\\Local\\RPGKingdomSupervisor\\UnityCliPilots\\pilot-REPLACE_WITH_EXISTING_ID'
```

This spins up a *temporary broker and synthetic GH-999999 workspace* in a
separate temporary state root, obtains a temporary read-only grant, uses the
same `unity-runner.sh` request/ack/response path, verifies that the staged
scene file did not change, and deletes only its own temporary WSL files.
**It never stops the live Editor, accesses halted GH-260, or dispatches Codex.**
This verifies transport/policy end to end; it does not validate real issue
workspace baseline equivalence or allow Editor writing.
 Run a separate
real-Windows test through the **broker client**, not only a direct CLI call,
before marking this capability host-verified.

Important: operator grants are *policy* checks, not an OS sandbox. A model
worker capable of arbitrary host filesystem access might bypass host-owned
state; maintain the existing worker sandbox. Any general-purpose C# evaluation
or mutable Editor APIs remain disallowed until #209 supplies separate
OS-enforced account/filesystem isolation, immutable source baseline,
bounded mutation validation, attested copyback and human PR approval.
This read-only grant is not authorization to rearm game GH-260.
