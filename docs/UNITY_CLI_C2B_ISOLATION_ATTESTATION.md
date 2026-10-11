# Stage C2b — source/stage provenance and Windows isolation preflight

**Status:** operator-only preparation for Supervisor #209; **no model authoring,
no new write capability, no stage-to-workspace copyback**. Depends on Stage C2a
PR #211, Stage C1 PR #210 and Stage B PR #208. Keep this PR draft.

## Real Windows-host diagnostic — October 11, 2026

The operator checked out `codex/209-unity-cli-c2b-isolation-attestation` in
`~/src/RPG-Kingdom-Supervisor-UnityPilot` and reported:

- `python3 tests/unity-cli-c2b-audit-test.py`: **10 tests passed** (`Ran 10 tests in 0.320s; OK`).
- `check-unity-cli-isolation.ps1` against the existing disposable pilot
  `pilot-51ed865d003844629683ec72ba0125db` returned:
  `status=not-isolated`, `verifiedEditorOwnership=true`,
  `editorSidSeparated=false`, `filesystemDenyProbePassed=false`,
  `readyForArbitraryEval=false` and `readyForCopyback=false`.

This confirms that the guard correctly refuses to treat the operator-owned
Windows Unity Editor as a secure sandbox. **It does not demonstrate a sandboxed
Editor or successful C2b source/stage equivalence.** The latter still requires
a separate *fresh* stage from the exact clean issue worktree, before any
benchmark output is created. No permission, Windows identity, ACL or game
source has been changed by this diagnostic.

## Motivation

Stage C2a proved that a host-brokered fixed C# operation can create one Unity
scene in a disposable project. It did **not** prove that an arbitrary C#
script can be run safely on the Windows host, or that a staged asset is
equivalent to an approved game worktree. This phase introduces two distinct
fail-closed checks while preserving that boundary.

### 1. Windows identity diagnostic (read-only)

The operator can inspect the Windows Unity process owner, using its Stage B
session receipt, Unity process executable and project-path command line:

```bash
cd ~/src/RPG-Kingdom-Supervisor-UnityPilot
STAGE='C:\Users\Dex\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-51ed865d003844629683ec72ba0125db'
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass \
  -File "$(wslpath -w "$PWD/scripts/windows/check-unity-cli-isolation.ps1")" \
  -SessionPath "$STAGE"
```

The result is **expected to fail closed** for the current pilot: it was
launched using the same Windows account as the operator. The script does not
change Windows users, rights or ACLs. It reports:
`readyForArbitraryEval=false`,
`filesystemDenyProbePassed=false`,
`readyForCopyback=false`.

A separate Editor process owner SID, even if explicitly matched with
`-ExpectedSandboxSid`, is only necessary evidence. It is *not sufficient*.
An actual sandbox identity must prove, using access attempts running **as that
identity**, that it cannot read the operator's profile/credential stores/game
source or write elsewhere outside its dedicated stage. The host must record
that proof and reject inherited `Users`/`Everyone` ACLs or token privileges
that undermine it. A later Windows broker must launch and own the Editor
inside the isolated identity without giving Codex Windows credentials or
PowerShell access. **No such provisioning is performed by this PR.**

### 2. Stage source equivalence and dry-run asset audit

`scripts/unity-cli-c2b-audit.py` has three operator actions:

```bash
export RPGK_UNITY_C2B_AUDIT_ENABLED=1
python3 scripts/unity-cli-c2b-audit.py baseline \
  --workspace /absolute/path/to/clean/GH-N \
  --session-windows 'C:\Users\Dex\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-REPLACE_WITH_FRESH_STAGE'
python3 scripts/unity-cli-c2b-audit.py audit \
  --workspace /absolute/path/to/clean/GH-N
python3 scripts/unity-cli-c2b-audit.py revoke \
  --workspace /absolute/path/to/clean/GH-N
```

Use **a new stage copied from that exact clean issue workspace** and establish
the baseline **before** the fixed benchmark runs. Do not use the old pilot
stage `pilot-51ed865d003844629683ec72ba0125db` for baseline: its generated
benchmark scene already exists. Do not use the dirty/halted game GH-260
workspace. Do not start a second Unity Editor while one is running; use the
existing Stage B ownership-checked stop procedure when ready to transition.

At `baseline`, the host-only tool checks:

- Real GH-N workspace path and Git HEAD, and a clean tracked/untracked Git
  worktree. It also checks the correct receipt/PID for the selected stage.
- Every regular file under `Assets`, `Packages` and `ProjectSettings`
  in both source and stage; rejects symbolic links, nonregular files,
  missing/mismatched paths, and case-insensitive filename collisions.
- SHA-256 of each staged file. Stage must match source exactly except a known,
  pinned `com.unity.pipeline=0.8.0-exp.1` addition to package manifest/lock.
  Additional dependency changes fail closed. The inventory is bounded at
  250,000 files and 150 GiB of source content.
- A new, not-yet-existing
  `Assets/RPGKingdom/Generated/AgentBenchmarks/SupervisorCliBenchmark.unity`
  target. The baseline receipt is stored in host state at
  `unity-cli-c2b-audit/GH-N.json` (permission mode 0600), not in the
  Codex workspace.

After the **separately authorized** fixed C2a benchmark, `audit` compares
the new staged inventory against that baseline. It permits only this new
benchmark scene, its Unity `.meta`, and the narrowly named generated-folder
metadata files. It denies **all deletions, all modifications of any prior
asset/project file, and all other additions**. The output includes SHA-256
hashes of additions, unchanged-baseline evidence, the unchanged worktree
revision, and explicit blockers. The tool never copies any bytes back into
the source. It returns `copyback=false` and
`readyForCopyback=false` even when every delta matches.

The original C2a benchmark is one-time per fresh stage, cannot overwrite its
existing target and remains disabled without its **separate** operator grant.
C2b does not alter broker permissions or enable arbitrary `unity eval`.

### Remaining acceptance gates before allowing unrestricted Editor C#

1. Windows Editor launched by host under dedicated OS identity with explicit
   *negative* filesystem access probes for operator secrets and source.
2. Durable stage/issue revision lease and demonstrated failure/crash recovery,
   no concurrent Editor or batchmode runner.
3. Version-/capability-pinned command catalog and bounded Editor task budget.
4. Independent Unity compilation and EditMode/PlayMode result IDs,
   geometry/path assertions, and screenshot review.
5. A new allowlisted *copyback implementation* with source HEAD recheck,
   atomic writes/rollback, sandbox output quarantine, human approval, and
   Git PR review. C2b's audit receipt is diagnostic only.

None of these can be skipped by generating a favorable JSON report.

## Tests

```bash
python3 tests/unity-cli-c2b-audit-test.py
bash tests/run.sh
```

Fake projects test exact source-stage matching, the pinned Pipeline-only
difference, successful new-scene review evidence, clean worktree/revision
binding, modified source and unexpected additions, symlink rejection,
grant/revoke and default-off behavior. Windows SID preflight is non-mutating
and refuses to declare success for the existing operator-owned pilot.

**Do not rearm RPG Kingdom GH-260 or merge #209 on the basis of this PR.**
