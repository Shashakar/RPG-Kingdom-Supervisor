# C2c — restricted Windows Unity task and host-owned read-only mailbox

**Status:** implementation draft for Supervisor #209. Linux regression tests
may pass, but the Windows Task Scheduler, Unity licensing, process-session
behavior, CLI instance discovery, access-denial probes and real message route
**have not yet been verified on the operator's Windows host**.

Builds on C2b #212, C2a #211, C1 #210 and Stage B #208. This is a NEW
isolated stage — not the successful but operator-owned
`pilot-51ed865d003844629683ec72ba0125db`.

## What this introduces

- Trusted operator/admin installer:
  `scripts/windows/install-isolated-unity-host.ps1`.
- Task Scheduler host worker:
  `scripts/windows/run-isolated-unity-host.ps1`. It runs as a
  **separate preexisting standard Windows local account** and has
  only a fixed scene-load initialization and a fixed
  `get_scene_hierarchy` request action. No worker-supplied C#,
  PowerShell, executables, paths, URLs or shell commands.
- Host-only `scripts/unity-isolated-mailbox.py` bridge. A worker's
  `unity-runner.sh isolated-scene-summary` request travels through
  the existing `unity-host-broker.py`, with GH-N issue/workspace
  lease, into a nonce-named Windows mailbox. The Windows task
  issues the Unity CLI call under the restricted account and returns
  compact scene statistics, not the full 12,000-object JSON.
- Separate operator grant in Supervisor state
  `unity-isolated-grants/GH-N.json`. Must match exact worktree,
  Git HEAD, approved Windows host ID, sandbox SID and Editor PID.
  Requires `RPGK_UNITY_ISOLATED_READONLY_ENABLED=1`; otherwise
  fail closed.
- A **fixed host-owned** Editor `eval` expression opens only
  `Assets/RPGKingdom/Scenes/PlaytestScene.unity` on startup.
  This is *not* a worker-facing general eval operation.

## Windows setup model — operator action only

This PR does **not** create an account automatically or change any existing
personal profile ACLs. A Windows administrator must explicitly provision
a local standard account such as `RPGKUnitySandbox` with a strong distinct
password. Do not give it local Administrators membership, source Git access
or the operator's Windows credentials. Do not reuse the operator's identity.
Do not store its password in any source, environment variable or Codex chat.

The installer **only supports a new** per-host ProgramData staging root,
default Windows installation directory:
`C:\ProgramData\RPGKingdomSupervisor\IsolatedUnity\isolated-<id>`.

The installed stage gets the *sandbox SID* Modify access; the pinned copied
agent/config are read-only to that identity. Requests are operator-writable
and sandbox-readable, responses are sandbox-writable. Sandbox-writable
telemetry resides under a separate `queue/status/` directory; the mailbox
parent and operator request inbox are not sandbox-writable. Task registration
uses `Get-Credential` in elevated PowerShell and hands the password only to
Windows Task Scheduler, not a Supervisor worker. Task is **on demand**,
limited privilege and bounded to eight hours. No model can register,
start, alter or disable tasks.

**Important Windows caveat:** Unity and Unity CLI are GUI/license-bearing
applications; background Task Scheduler batch sessions can behave differently
from interactive sessions. Their compatibility under a dedicated Windows
identity needs a real on-host test. A Unity CLI installed under
`C:\Users\Dex\AppData\Local\Unity\bin\unity.exe` may be unreadable
or unusable to the new account. Use a verified, accessible CLI installation
for the sandbox account; do not relax operator-profile ACLs to work around it.

The intended staged project is a fresh clone of a *clean* game worktree.
It may not be the previously modified C2a pilot. Before installation, review
the exact workspace HEAD/clean status. The current installer does **not**
independently prove its full Git-to-NTFS hash provenance; the C2b stage audit
must be extended to ProgramData stages before authoring/copyback is considered.

### 1. Review operator-only plan (no filesystem changes)

Run in **elevated Windows PowerShell**, from the checkout directory
containing this PR, with the local sandbox account already present:

```powershell
powershell.exe -NoProfile -File .\scripts\windows\install-isolated-unity-host.ps1 -Action plan -SandboxUser RPGKUnitySandbox
```

The plan refuses a missing or administrator identity.

### 2. Admin installation (explicit opt-in)

Use the exact clean Windows-accessible RPG Kingdom checkout and
existing Unity.exe and Unity CLI paths. These must already exist. Supply at
least two real operator-only private files as **read-denial probes**, plus
an existing operator-only directory for a write-denial probe. Use harmless
test-canary files in different protected operator locations, not your actual
credentials; ensure the sandbox must receive `AccessDenied`, not
`FileNotFound`. Example argument *shape* (replace all placeholders):

```powershell
powershell.exe -NoProfile -File .\scripts\windows\install-isolated-unity-host.ps1 -Action install `
  -SandboxUser RPGKUnitySandbox `
  -SourceProjectPath 'C:\path\to\clean\RPG-Kingdom' `
  -EditorPath 'C:\Program Files\Unity\Hub\Editor\6000.3.10f1\Editor\Unity.exe' `
  -CliPath 'C:\path\accessible-to-sandbox\unity.exe' `
  -ForbiddenReadFiles @('C:\Users\Dex\path\private-test-A.txt','C:\Users\Dex\other\private-test-B.txt') `
  -ForbiddenWriteDirectories @('C:\Users\Dex\path\protected-test-directory')
```

**This command changes ProgramData ACLs, copies the Unity project, and
registers a Windows Scheduled Task.** Do not run it from a Codex worker or
a non-elevated shell. It does not start Unity. Installation refuses to overwrite
an existing `C:\ProgramData\RPGKingdomSupervisor\IsolatedUnity` root;
this is a conservative one-host pilot, not an unattended provisioning service.
Use Task Scheduler management explicitly to remove a failed test setup.

### 3. Launch via operator task control

Close other Unity Editors, including the Stage B pilot, *only when ready to
test the new host*. Do not kill someone else's Editor to acquire the slot.
From elevated Windows PowerShell:

```powershell
powershell.exe -NoProfile -File .\scripts\windows\install-isolated-unity-host.ps1 -Action start -HostId isolated-<id>
powershell.exe -NoProfile -File .\scripts\windows\install-isolated-unity-host.ps1 -Action status -HostId isolated-<id>
```

The Windows task starts under its registered restricted account, first verifies
it cannot modify its trusted task/configuration or the operator request inbox,
confirms it can write only to its own stage and status/response folders, and performs
negative **read** probes against protected operator canaries and negative
**write** probes against an operator directory. It fails closed if a probe
succeeds or produces an inconclusive IO error. Only then does it start Unity.
It attempts to open the pinned PlaytestScene and publishes
`queue/status/probes.json`, `queue/status/editor.json` and `queue/status/ready.json` in
ProgramData. The host grants no mailbox capability until all these exist
and identify the pinned sandbox SID/Unity PID/scene.

The C2b identity preflight for the original pilot should remain
`status=not-isolated`; do **not** treat its diagnostic result as a failure
to launch the separate new host.

### 4. Supervisor operator grant (only after Windows host acceptance)

From trusted WSL Supervisor checkout, with the real authorized clean GH-N
workspace and host-specific ID (no worker involvement):

```bash
export RPGK_UNITY_ISOLATED_READONLY_ENABLED=1
python3 scripts/unity-isolated-mailbox.py grant \
  --workspace "$RPGK_SYMPHONY_WORKSPACE_ROOT/GH-N" \
  --host-dir-windows 'C:\ProgramData\RPGKingdomSupervisor\IsolatedUnity\isolated-REPLACE_WITH_ID'
```

Only then can a worker holding the existing issue `unity-editor.lock` call:

```bash
bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh" \
  isolated-scene-summary \
  --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity
```

Revoke through `python3 scripts/unity-isolated-mailbox.py revoke
--workspace "$RPGK_SYMPHONY_WORKSPACE_ROOT/GH-N"` and unset the enable flag
before subsequent launches. The Windows operator must stop/remove the task
via Task Scheduler; this connector intentionally does not expose
worker-controlled task cancellation or Windows process termination.

## Critical security limitations

Passing a small number of denial probes does **not** prove general Windows
process sandboxing. The task might have different profile, licensing,
networking or ACL access from what the probes cover. In-process C# or
imported editor code still has the full privileges of the sandbox identity.
Task configuration, private stage and Windows ACLs need independent
on-host review.

No arbitrary Editor C#, terrain editing, scene save, source checkout
write, asset copyback, formal Unity tests or autonomous issue rearm are
enabled here. The mailbox has one hardcoded read-only command with
strict response bounds. Copyback remains false, and
`readyForArbitraryEval=false` even after successful probes.

Remaining #209 requirements: OS-based isolation hardening and broader
denied-file/denied-network access verification, clean HEAD/stage content hash
attestation adapted to ProgramData, durable multi-request task lifecycle with
crash/restart/lease recovery, pinned command discovery, Unity formal validation
and safe human-reviewed Git copyback.

**Do not rearm RPG Kingdom GH-260 or merge scene assets because this PR passes
Linux tests alone.**
