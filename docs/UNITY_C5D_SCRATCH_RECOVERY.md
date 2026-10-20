# C5d scratch-only filesystem recovery lab

Tracks Supervisor #224, following #222 and merged PR #223.

**This is an executable local filesystem durability test, not a live game-asset promotion tool.** The CLI has **no workspace, stage, destination, approval, Unity, grant, or source path arguments**. Every invocation creates and deletes its own private temporary directory underneath the host's OS temp path. It creates fake Unity fixture bytes and writes only inside that scratch directory. It cannot promote Unity assets, consume a real operator nonce, or commit a game branch.

## Running the harness

\`\`\`bash
python3 scripts/unity-promotion-recovery-lab.py
python3 scripts/unity-promotion-recovery-lab.py --inject-crash after-first-copy
python3 tests/unity-promotion-recovery-lab-test.py
\`\`\`

The harness forks a child process and calls \`os._exit(83)\` at one of these points:

1. \`after-prepare\` — durable journal created, no nonce reservation.
2. \`after-reserve\` — private scratch nonce reserved; no destination assets.
3. \`after-first-copy\` — first fixture file atomically installed and fsync'd.
4. \`after-all-copy\` — all three fixed fixtures installed.
5. \`after-validation\` — simulated per-file hash validation finished.
6. \`after-commit\` — journal's committed state durably written.

After process death, the parent reacquires a separate advisory OS file lock, reads the durable journal and monotonic nonce ledger, validates that each destination file is exactly a known authorized fixture by pinned SHA-256, and either removes only those matching files (rollback) or preserves the fully verified committed fixture. A foreign file, symlink, missing reserved nonce or modified destination prevents destructive recovery. The harness demonstrates idempotent recovery and denies nonce reuse even after rollback.

## Design evidence vs. production gaps

The test now exercises **real disk writes, real process death, OS advisory locking, fsync before atomic replace, nonce persistence and recovery** — but all in private scratch space with fake data and a single short-lived test actor. This is stronger evidence than PR #223's virtual dictionary rollback, **not proof** that a live Unity source transfer is safe.

Explicit missing production requirements:

- Independent, operator-authenticated permission unavailable to Codex executing under the same Windows user. C5b HMAC and C5c TTY are not sufficient authorization to mutate source.
- Issue-scoped stage grant and persistent Unity PID ownership, stage quiescence, immutable stage snapshot, cross-OS lock discipline and Windows filesystem durability semantics.
- Race-safe file descriptor-relative path enforcement; current scratch helpers are *not* safe for adversarial actors who can write into the same directories and must not be transplanted into a promotion endpoint.
- Durable backups of **existing** approved files, journal recovery after OS/power failure, and tamper-evident append-only host audit record. This harness only models new files absent from destination.
- Real Unity import/compile/EditMode/PlayMode and scene assertions after promotion; human PR review/merge, rollback respecting third-party modifications and game architecture rules.

No \`symphony:ready\` label, automatic merge, production copyback or update to game #260 is authorized by this test. The next step is to review the host security boundary and Windows stage lifecycle before defining any live promotion endpoint.
