# Stage B: isolated Unity CLI/Pipeline host pilot (Supervisor #207)

This is an **operator-only** Windows/WSL smoke pilot built on Stage A (#206).
It is **not available to Codex workers**, does not modify existing Supervisor
dispatch or the authoring broker, and must not be used to claim #260 scene
acceptance. It is deliberately isolated until actual Windows testing passes.

## Requirements and scope

- Windows host with Unity 6000.3.10f1 installed by Unity Hub (exact version
  is resolved from `ProjectSettings/ProjectVersion.txt`, not guessed).
- Windows Unity CLI installed and available as `unity` or supplied with
  `--cli-windows`. CLI remains **experimental**.
- WSL with `powershell.exe`, `wslpath`, and the game project available.
- Windows PowerShell launched from **the operator account**, not the Codex
  permission/sandbox profile; the script is not added to `WORKFLOW.md`.
- Enough NTFS space for a complete copy of Assets, Packages and
  ProjectSettings plus a fresh Unity Library and imported assets.
- No Unity.exe running for `start`; a preexisting user's Editor is never
  terminated to obtain the resource.
- The WSL wrapper refuses operation while Supervisor's `unity-editor` lock
  exists. This is only a conservative operator preflight; the future worker
  implementation must use a durable broker-managed lease.

The pilot creates a **new, random, disposable stage** under the executing
Windows account's `%LOCALAPPDATA%\RPGKingdomSupervisor\UnityCliPilots\`.
It copies only `Assets/`, `Packages/` and `ProjectSettings/` from the
chosen source. It launches a Unity Editor matching the project's version,
records PID/executable/project-path ownership in `session.json`, and keeps
the staged Editor running between operator calls. It never copies back
staged changes and does not touch Git branches or game scene assets.

This is a host capability test, **not** the future automated authoring path.
Do not use the same stage for multiple Git branches or run the old batchmode
runner concurrently. Existing Supervisor locks and copyback protection are
unchanged. Stage C requires a real host-owned broker capability with
dedicated Windows process isolation and verified change attestation.

## Operator commands

From the Supervisor checkout containing this PR:

```bash
# 1. Start from a CLEAN checked-out game project, not GH-260's dirty workspace.
bash scripts/unity-cli-stage.sh start --source ~/src/RPG-Kingdom

# The JSON result contains sessionPath, for example:
# C:\Users\<user>\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-...

# 2. Inspect the Editor process and version.
bash scripts/unity-cli-stage.sh status \
  --session-windows 'C:\Users\<user>\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-...'

# 3. Discover current CLI and the exact project-specific Editor command set.
bash scripts/unity-cli-stage.sh discover \
  --session-windows 'C:\Users\<user>\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-...'

# 4. ONLY if discovery reports Pipeline missing and you are ready to
#    install a package into this DISPOSABLE project:
bash scripts/unity-cli-stage.sh install-pipeline \
  --session-windows 'C:\Users\<user>\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-...'

# 5. Once Editor recompilation finishes, repeat discover.
bash scripts/unity-cli-stage.sh discover \
  --session-windows 'C:\Users\<user>\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-...'

# 6. Explicitly stop only the session-owned Editor when finished.
bash scripts/unity-cli-stage.sh stop \
  --session-windows 'C:\Users\<user>\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-...'
```

Use the exact `sessionPath` printed by the start command. Do not type the
example string literally. If the CLI is installed outside Windows PATH,
add `--cli-windows 'C:\...\unity.exe'` to relevant invocations.

## What each step proves

| Step | Evidence | Does not prove |
| --- | --- | --- |
| `start` | Fresh NTFS copy plus started Unity PID, declared Editor version | Editor compiled, package installed |
| `status` | Correct PID, Unity.exe executable and quoted exact `-projectPath` | Healthy Editor, usable API |
| `discover` | CLI version, Pipeline listing, exact project command discovery; raw command stdout/stderr stored under stage | Ability to modify scenes or pass tests |
| `install-pipeline` | CLI installation command exit success on the disposable stage | Editor recompiled or Pipeline is connected |
| `stop` | Requested termination of verified owned process | Automatic stage deletion |

`discover` exits nonzero until all three checks succeed. It emits a
machine-readable summary and keeps diagnostic command output under
`<sessionPath>\cli-version.*`, `pipeline-list.*`, and
`editor-commands.*`. Avoid posting unreviewed logs publicly; they may
contain local machine paths or private project metadata.

Unity documents installing Pipeline through `unity pipeline install` after
signing in using `unity auth login`. **Authentication is operator-owned**.
The script does not call `unity auth login`, accept tokens, inject secrets,
or install packages automatically. If install fails with an authentication
error, sign in using your normal Windows terminal and retry explicitly.
The relevant package is `com.unity.pipeline`; installation only changes
the temporary stage's manifest. It is not a game-main dependency decision.

CLI and Pipeline command names may change as the experimental releases evolve.
Capture their actual versions (including the Pipeline manifest change) and
discover the available API *before* building the broker authoring integration.
Unity announced command renames for Pipeline 0.9.0 on October 7, 2026:
https://discussions.unity.com/t/heads-up-upcoming-com-unity-pipeline-0-9-0-release-will-include-breaking-changes-to-command-names/1738559

## Failure and recovery

- A failed copy or Editor start does not change the source game repo. The
  disposable stage may need manual removal **only after Editor exits**.
- A stale or substituted PID, malformed session receipt, or wrong project path
  fails closed; the `stop` action must never target another Editor.
- The existing Supervisor test runner has a host-wide no-existing-Unity rule.
  Stop the pilot before running legacy EditMode/PlayMode validation.
- Only operate on intentionally disposable pilot stages; do not point
  `start` at GH-260 or manually paste staged changes into production.
- If a Windows Editor hangs and the ownership guard cannot prove it owns the
  process, close it using Windows UI or inspect it manually instead of
  bypassing the guard.

## Tests

`bash tests/unity-cli-stage-host-test.sh` uses mocked Windows commands to
verify wrapper argument forwarding, bounded actions, and fail-closed paths.
CI also runs this test through `tests/run.sh`. The PowerShell lifecycle
is not executed by Linux CI and requires real Windows-host verification.

### Still outside this PR

- A broker protocol exposing exact project-bound CLI operations to Codex.
- Restricted Windows account and OS-enforced Editor filesystem isolation.
- Durable Editor leases/recovery coordinated with the current Unity lock.
- Semantic Unity acceptance tests and scene modification/copyback attestation.
- RPG Kingdom #260 clean-baseline Terrain implementation and visual review.
