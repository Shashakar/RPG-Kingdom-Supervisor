# Stage C2a: fixed, stage-only Unity scene benchmark (Supervisor #209)

**Status: draft / not host validated.** This stacks on read-only Stage C1 PR #210,
which stacks on Stage B PR #208. This is NOT arbitrary Editor authoring.

## Purpose and scope

Prove that a GH-N typed worker request can travel through the existing
Supervisor host broker, invoke the *fixed* trusted Unity Editor scene-creation
program, validate its result, and return a compact receipt. It does not require
model-generated C#, a new broker service, direct access to Pipeline's loopback
port, or any copyback into the game's Git worktree.

The worker-facing operation is:

```bash
bash "$HOME/src/RPG-Kingdom-Supervisor/scripts/unity-runner.sh" \
  editor-stage-benchmark \
  --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity
```

All requests continue through `unity-host-broker.py` and the single existing
`unity-editor.lock`, including its issue/workspace ownership and deadline
constraints. Host-side `unity-cli-stage-benchmark.py` checks a separate
operator grant containing the GH-N workspace, Git revision, pinned Windows
stage path, running Unity PID, exact existing source scene and SHA-256 hashes
of protected staged scene/project files. The grant lives in
`$RPGK_SUPERVISOR_STATE_ROOT/unity-cli-benchmark-grants/GH-N.json` (mode 0600).

A worker cannot supply an Editor command, eval source, Windows stage location,
an object description, a different scene, or a destination. The benchmark
runs one immutable C# snippet compiled into the trusted host adapter:
it creates an **additive empty scene**, a root and a cube, saves only to
`Assets/RPGKingdom/Generated/AgentBenchmarks/SupervisorCliBenchmark.unity`,
then restores the existing active `PlaytestScene` even on failure.
The benchmark refuses an existing output scene, dirty/wrong active scene,
changed protected hashes, mismatched Editor PID, incorrect Git HEAD,
unreachable Pipeline or unexpected versions.

Pinned host versions for this smoke: Unity 6000.3.10f1, Unity CLI 1.0.0-beta.8,
Pipeline 0.8.0-exp.1. A fresh disposable NTFS stage is required after the
first successful benchmark, as the generated scene must not be overwritten.
The command does **not** copy back any assets, modify the original game branch,
or run PlayMode/EditMode tests.

## On-host acceptance: operator-only WSL command

Use the existing Stage B session only if it remains running, has the original
clean `PlaytestScene` active, and does not yet contain the generated benchmark.

From the Supervisor pilot checkout on branch
`codex/209-unity-cli-stage-authoring-benchmark`:

```bash
cd ~/src/RPG-Kingdom-Supervisor-UnityPilot
python3 tests/unity-cli-stage-benchmark-test.py

RPGK_UNITY_CLI_EXE=/mnt/c/Users/Dex/AppData/Local/Unity/bin/unity.exe \
  bash scripts/unity-cli-stage-benchmark-smoke.sh \
  --session-windows 'C:\Users\Dex\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-51ed865d003844629683ec72ba0125db'
```

The smoke script checks that no real Supervisor Unity lock exists, creates a
**temporary** GH-999999 Git workspace and host broker state, grants access only
to that synthetic issue, invokes the normal `unity-runner.sh` typed command
through an actual Python Supervisor broker to Windows Unity CLI/Pipeline, and
checks the generated scene exists and the original PlaytestScene file hash is
unchanged. It cleans only its own temporary files. It does not stop Unity or
dispatch Codex. The saved test scene remains on the **disposable Windows stage**
for operator review; no attempt is made to delete it while the Editor is open.

To grant a *real* issue in a separate reviewed trial (not GH-260), the operator
must explicitly set `RPGK_UNITY_CLI_BENCHMARK_ENABLED=1` and provision a
host-only grant with `unity-cli-stage-benchmark.py grant --workspace ...`
and `--session-windows ...`; default is **off**, and the grant is never
automatically added. To revoke use `unity-cli-stage-benchmark.py revoke`.
Do not enable this lane against a halted/dirty GH-260 workspace.

## Security and remaining #209 gates

The fixed C# script is **host-authored**, not model-authored; this bounds this
single operation even without a separate Windows user account. It is not a
security sandbox. Unity is still running in the operator's Windows identity,
and in-process code can technically access that account's files.

**Arbitrary C# eval, general scene mutation, asset promotion, Terrain writes,
external file access and unattended worker authoring remain disabled.**
Before enabling generic commands, provision and verify an OS-isolated Windows
Editor identity with filesystem denied outside its stage; establish a clean
issue-revision-to-stage baseline, durable single-issue Editor lease with crash
recovery, command/job cancellation, constrained staging diff/copyback and
Unity test/capture receipts. A Git branch or read-only grant alone does not
meet that boundary. The Stage C2 benchmark is **not** authorization to resume
RPG Kingdom #260, merge unvalidated scenes, or enable Pipeline's generic eval.

## Tests

```bash
python3 tests/unity-cli-stage-benchmark-test.py
bash tests/run.sh
```

The fake CLI/Editor tests prove grant/pin denial, stage-path and worktree
binding, protected hashes and fixed output restrictions, broker issue locks,
and refusal of pose/filter arguments. Live Windows acceptance must be run
separately and cannot be inferred from Linux CI.

## Rollback / operator stop

The entire Stage C2a path remains disabled unless the host starts with
`RPGK_UNITY_CLI_BENCHMARK_ENABLED=1` **and** has a separate issue-bound
benchmark grant. Operators may revoke through `unity-cli-stage-benchmark.py
revoke --workspace <GH-N>` and unset the environment flag; a new request
will then be rejected. Do not reset, delete or recreate Stage B's NTFS
project while its Editor is running. Any previously created benchmark scene
is deliberately retained within the disposable stage for inspection and is
never promoted automatically.
