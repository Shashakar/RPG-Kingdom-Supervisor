# C5d: reviewed Unity asset promotion — transaction design and dry run

Tracker: Supervisor #222. Predecessors: C5 proposal #217, C5b verifier #219, C5c manual issuer #221, C4 game GH-261.

**Status: DESIGN + IN-MEMORY FAILURE SIMULATION ONLY.** This change introduces **no stage-to-source copyback, no spent-nonce update, no file backup/copy/rename, no Unity invocation, and no source commit/PR**. The C5c signed envelope is still review evidence, **not promotion authorization**. RPG Kingdom #260 must remain halted.

## How to exercise safely

The optional dry-run CLI re-inventories the actual stage and verifies the existing C5b operator review envelope, but only *simulates* writes in a private Python dictionary:

\`\`\`bash
python3 scripts/unity-stage-promotion-dryrun.py \
  --workspace "$HOME/code/rpg-kingdom-symphony-workspaces/GH-261" \
  --stage "/mnt/c/Users/Dex/AppData/Local/RPGKingdomSupervisor/UnityCliPilots/pilot-08a3d3388ac5499189cabe8b7c106f0a" \
  --approval "/host-only/GH-261-review.json" \
  --host-key "/host-only/signing.key" \
  --spent-nonces "/host-only/used-nonces.json" \
  --inject-failure virtual-copy
\`\`\`

This requires a *previously issued* host review envelope and host-owned, private key and nonce files (no fake "approve" flag). It prints an exact-path plan and simulated transition trace, no transaction receipt claiming real promotion. \`--inject-failure\` accepts any checkpoint listed below. Without this flag, the returned \`simulated-commit\` still means **nothing was copied**. CI tests instead use disposable Git and stage fixtures and never need real operator credentials.

## Scope and threat model

Current C5 source/approval contract is **hardcoded to the C4 proof fixture**:

- \`Assets/RPGKingdom/Generated/AgentBenchmarks/RealWorkerStageProof.unity\`
- \`Assets/RPGKingdom/Generated/AgentBenchmarks/RealWorkerStageProof.unity.meta\`
- \`Assets/RPGKingdom/Generated/AgentBenchmarks.meta\`

All three must be new relative to the source; complete metadata and GUIDs are required. Every other staged difference is excluded, notably \`Assets/Settings/Mobile_RPAsset.asset\` (URP v12→v13 serialization change). The complete changed inventory, including exclusions, is covered by the signed proposal SHA-256. The source \`PlaytestScene.unity\` must be present, unchanged, and its source workspace clean at a pinned \`origin/main\` revision. No wildcard allowlist; the candidate list is immutable and only ever **simulated** here.

**Adversaries/failures:** worker-authored C# in a persistent same-user Windows Unity process may access anything that user's account can access; the stage is NOT a security sandbox. Other actors can mutate stage files or credentials, replace symlinks, race after hash calculations, hold different Editor sessions, kill a host operation, or alter Git branches/Unity asset GUIDs. A worker can control an issue body or produce JSON. A GitHub label, CLI request, TTY appearance or HMAC secret readable by the same OS identity is **not independent operator authorization**. Never expose an approval issuer or future promotion handler to worker code.

**Unresolved authority requirement:** a live promotion system needs a truly host-controlled, human-authenticated approval channel (e.g. an operator confirmation capability unavailable to Codex's execution identity). Until that identity boundary and operating-system protections are established, **keep live promotion disabled by default**. The current C5c key + TTY review is a procedural test and insufficient on its own.

## Future live transaction contract — NOT IMPLEMENTED

Each stage below must be designed, audited, and tested before enabling any real writes:

1. **Pre-authority:** host authenticates the human operator independently of the worker context and checks a distinct, short-lived **promotion** decision, not just the C5b \`review-verification-only\` envelope. Include exact GH issue, source branch, clean feature-workspace absolute path + HEAD, stage ID/owned Editor grant and PID, stage snapshot generation, original scene SHA, exact file hashes/Unity GUIDs, quarantined full inventory digest, one-shot nonce and expiry. No worker-self-signing or permissions inferred from labels.
2. **Lease and quiescence:** acquire a documented fixed lock order (host Editor/stage lease → feature-workspace lock → nonce journal lock) with bounded timeouts. Verify no competing Unity process; close or quiesce the persistent Editor *without destroying an operator's project*. Refuse concurrent batchmode validation and in-progress stage import. Freeze a private, host-attested immutable stage snapshot. Check source/stage non-symlink roots and every path component with safe OS APIs. Never trust a mere \`Path.resolve()\` check as race-free.
3. **Rehash before source write:** under the leases, refresh full source/stage inventories; compare canonical signed proposal and exact approved path+hash+GUID set and confirm the source branch/revision and clean Git state. Reject modified, removed or duplicate-GUID assets, unexpected package/config changes, unapproved path, or any source revision drift. Original \`PlaytestScene.unity\` is always protected.
4. **Durable reservation and rollback journal:** atomically record **nonce reserved** and plan digest in a private host ledger with exclusive lock, \`fsync\` file + parent directory, before writing to the destination. One nonce cannot ever authorize a second transaction. Write a durable per-issue journal entry before each step with original/temporary hashes and verified backup state. A crash between backup and copy must leave a *recoverable* journal, not an invisible partial source mutation. On restart, detect pending journals and recover/ask for operator review **before any new grant**. Refuse stale and previously failed nonces; no silent replay.
5. **Atomic selective copy:** create a dedicated fresh \`codex/\` game feature branch/workspace from authorized \`origin/main\`. Materialize only the three approved paths, using temporary files created with exclusive safe flags and hash verification. Preserve scene \`.meta\` and folder \`.meta\` GUIDs and verify no collisions with target. Never update or delete an existing scene, track project settings, or copy the quarantined URP file. Atomic replace after syncing staged output; do not use generic directory mirroring or asset globs.
6. **Unity validation:** after staging/Editor lifecycle is safe, run fresh Unity import/compile and applicable EditMode/PlayMode tests. Reopen the promoted scene and assert hierarchy/transforms, persistent asset hashes, GUID references, and protected PlaytestScene unchanged. Capture independent host validation receipts; a C5d simulated check is not Unity evidence.
7. **Commit and handoff:** after all checks, commit only approved assets to the dedicated feature branch. Open a PR with original snapshot/approval/nonce/asset hashes and a staged-diff review. Human must explicitly sign off and merge. **No auto-merge**.
8. **Failure and recovery:** fail closed on any mismatch; quarantine the transaction, stop Unity activity, and restore exact backups only when the destination matches the journal's expected intermediate hashes. Where external modification is detected, escalate to human review rather than overwriting changes. The nonce remains burned after any reservation, even if rollback succeeds. Never copy back to game \`main\` or write the actual source production scene.

## In-memory failure-injection model

\`scripts/unity-stage-promotion-dryrun.py\` models these ordered checkpoints:

\`\`\`
exclusive-lock
stage-quiescent
rehash-before-write
reserve-nonce
durable-backup
virtual-copy
unity-validation
finalize
\`\`\`

It clones an in-memory map of the three *expected absent destination files*. At \`virtual-copy\`, only their SHA-256 values are assigned to this map. Injecting a failure at **any** checkpoint restores the map to its initial state and reports \`simulated-rollback\`. A success reports \`simulated-commit\`. Both explicitly report \`simulationOnly=true\`, \`realFilesWritten=0\`, \`realNonceConsumed=false\`, \`copyback=false\`. Reservation status is virtual only. This is an executable specification for discussing crash points, **not a test of durable disk rollback or real concurrency**.

## Preconditions for a live follow-on PR

- Independently authenticate operator authorization; fix same-account trust weakness.
- Safe filesystem API, stage snapshot/lease and source lock across all writes.
- Durable transaction journal + nonce reservation/consumption and crash recovery.
- Rollback tests using real temporary files and fault-injected hard process terminations; tests for **stale PID, active Unity, permissions, symlinks, GUIDs, TOCTOU, duplicate nonce, concurrent workers, source edits**, and foreign processes.
- Real Unity validation with recorded receipts and manually reviewed PR.

Do not describe GH-261's report-only successful authoring or this dry run as proof of copyback safety. This work does not lift the halt on RPG Kingdom #260.
