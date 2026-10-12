# C5e: Unity stage ownership and read-only host inspection

Tracks Supervisor [#226](https://github.com/Shashakar/RPG-Kingdom-Supervisor/issues/226), following the C5d scratch recovery acceptance (#224 / PR #225). **Inspection only; no source copyback, production-scene write, nonce consumption, or promotion grant.**

## Operator-host invocation

Run from the **trusted WSL host**, using the existing C3 granted, owned, persistent Unity Editor. Do not invoke through a worker, issue comment, Codex shell, or a public endpoint.

\`\`\`bash
cd ~/src/RPG-Kingdom-Supervisor
export RPGK_UNITY_STAGED_CODE_ENABLED=1
export RPGK_UNITY_CLI_EXE=/mnt/c/Users/Dex/AppData/Local/Unity/bin/unity.exe

python3 scripts/unity-stage-host-preflight.py \
  --workspace "$HOME/code/rpg-kingdom-symphony-workspaces/GH-261"
\`\`\`

The command derives its Windows stage **only from the existing host C3 issue grant**, not from worker-supplied \`--stage\` or \`--script\` arguments. It does not add an operation to the worker-callable Unity broker. It uses the C3 host inspection implementation to confirm the exact grant, Windows stage source identity, original clean active \`PlaytestScene\`, Pipeline availability, owned Unity Editor PID and its own asset import children. A competing Unity Editor blocks inspection.

It additionally fails closed when:
- the Unity broker is absent, blocked, busy or associated with a dead/changed PID or wrong workspace root;
- any Unity resource lease directory still exists, **even if apparently abandoned** (operator recovery is a separate action);
- the source checkout is dirty or differs from its \`origin/main\` tracking revision;
- project version is missing, different between source and stage, or not the pinned Unity \`6000.3.10f1\`;
- the Windows stage does not map under \`/mnt/c/\` using the supported WSL \`9p\` or \`drvfs\` transport (these checks **do not independently attest the host volume is NTFS**);
- the protected original \`PlaytestScene.unity\` changes or source/stage differs;
- a symlinked project/status/asset root is encountered, the stage-change manifest differs across scans, or the Editor/broker identity changes during the observation.

A successful response has \`status=inspection-consistent-not-quiescent\` and a SHA-256 fingerprint of the complete stage-vs-clean-source manifest (including quarantined URP modifications). It includes explicit \`stageFrozen=false\`, \`persistentEditorRunning=true\`, \`independentHumanAuthorization=false\`, \`promotionAuthorized=false\`, \`nonceConsumed=false\`, and \`copyback=false\`.

## Critical limitation: idle != quiescent, consistent != immutable

Checking the broker status twice and reading files twice does **not** atomically freeze an Editor. A worker or same-account Windows process could modify files between observations, the broker could race after the last check, and the persistent Unity Editor remains running. There is no durable lease protecting this observation from later writes. Therefore the response **must never** be treated as proof of exclusive stage quiescence or as authority to copy files. It is not safe to pass its manifest hash directly into a write-capable transaction.

The current operator issuer (#220) uses an HMAC secret and console prompt accessible to the same Windows identity used by the Codex-authored C# Editor scripts. This does **not** establish independent human authority: the worker could potentially read accessible files, spoof a console interaction or invoke local tools under that identity. Separate host/worker OS identities or a human approval mechanism unreachable from the worker's execution context must be selected and verified **before enabling asset promotion**.

## Required follow-on implementation, not in this PR

1. Choose and test an independent host operator identity/capability boundary and separate credential storage.
2. Implement a broker-host-only **exclusive** stage freeze/quiescence lease compatible with the persistent Editor, batchmode runner and Windows process census, with an explicit operator-controlled editor stop or safe handoff. Never force-close an operator Editor as part of read-only inspection.
3. Verify the actual Windows filesystem/volume identity and revision, immutable stage snapshot and complete manifest **under that lease**.
4. Bind a distinct promotion approval to the frozen stage snapshot, source feature branch and issue/revision, exact files/hashes/Unity GUIDs, quarantined exclusions, expiration and durable nonce.
5. Execute crash-safe selective file copying and recovery only in an operator-authorized dedicated feature workspace, then independently run Unity import/compilation/EditMode/PlayMode and scene tests. Human PR review and merge remain mandatory.

**No existing Unity worker-facing broker operation was expanded.** In particular \`editor-staged-preflight\` remains a C3 dispatch eligibility check and does not become a promotion preflight. Game GH-260 remains \`symphony:halted\`.
