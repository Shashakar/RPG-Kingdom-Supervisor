# C5 stage change proposal — audit only

Parent: Supervisor #209; implementation slice: #216. C4 acceptance: game GH-261.

This first C5 slice is **read-only**. It inventories an existing issue workspace and a disposable Unity stage and emits a review proposal. It **does not** approve, promote, import, copy back, create a PR, or merge files. Every manifest entry is deliberately marked \`promotable: false\` and the document's \`authorization\` is null.

## Operator use

Run on the **host** with the original GH-261 workspace and its disposable stage mapped into WSL:

\`\`\`bash
python3 scripts/unity-stage-proposal.py \
  --workspace "$HOME/code/rpg-kingdom-symphony-workspaces/GH-261" \
  --stage "/mnt/c/Users/Dex/AppData/Local/RPGKingdomSupervisor/UnityCliPilots/pilot-08a3d3388ac5499189cabe8b7c106f0a" \
  | jq .
\`\`\`

The command requires a clean GH-N workspace with Git HEAD equal to its \`origin/main\` tracking ref. It does not contact Unity, stop the Editor, write files, or need Supervisor broker access. Snapshot consistency under concurrent Unity writes is **not** provided; do not treat a proposal as an attested stable stage. Future promotion must require an exclusive stage lease and rehash immediately before copying.

## Manifest contract (version 1)

- Source identity: \`issue\`, \`sourceWorkspace\`, \`sourceRevision\`; staged project: \`stageProject\`.
- Original and staged \`PlaytestScene.unity\` SHA-256.
- Full changed-file inventory across \`Assets\`, \`Packages\`, \`ProjectSettings\`, including added, removed, modified files, SHA-256, byte size and \`.meta\` GUID (if applicable).
- Exact C4 generated fixture \`.unity\` + \`.meta\` and the parent directory \`.meta\` are **candidates for human review**, not approved promotion targets. Every other path, including render pipeline \`Assets/Settings/Mobile_RPAsset.asset\`, is **quarantined**. Project configuration changes are always quarantined.
- \`counts\`, \`authorization: null\`, \`copyback: false\`, and \`promotable: false\` on *all* changes.

The inventory rejects missing/linked roots, symlink files or directories, nonregular files, malformed/duplicate \`.meta\` GUIDs, unsafe paths, excessive file counts, dirty source or stale source tracking revision. No approval is derived from filename classification.

## Unimplemented future phases

This is **not** a copyback security gate or reliable approval token. Before any promotion API is introduced, implement an operator-only signed/one-shot review contract with explicit source/stage/grant receipt binding, immutable stage snapshot/lease, GUID collision and reference validation, allowed path/hash decisions, atomic backup/rollback, independent Unity import/compilation and EditMode/PlayMode checks, PR review, and human merge approval.

Same-Windows-account worker-authored C# remains trusted code, not an OS sandbox. Game GH-260 remains halted; this stage proposal does not authorize any terrain or production-scene write.

## C5b verification-only attestation (Supervisor #218)

`scripts/unity-stage-approval-verify.py` checks a **host-issued HMAC-SHA256 envelope** against a freshly regenerated C5 proposal. The signature covers reviewer identity, issue, source revision/hash, stage path, the **entire canonical proposal SHA-256** (including all quarantined changes), exact approved C4 fixture and both required `.meta` paths, issue nonce, and a maximum one-hour UTC validity window.

Example verification syntax (only after a trusted host operator has separately issued the signed envelope):

```bash
python3 scripts/unity-stage-approval-verify.py \
  --workspace "$HOME/code/rpg-kingdom-symphony-workspaces/GH-261" \
  --stage "/mnt/c/Users/Dex/AppData/Local/RPGKingdomSupervisor/UnityCliPilots/pilot-08a3d3388ac5499189cabe8b7c106f0a" \
  --approval "/path/to/operator-issued-review.json" \
  --host-key "/home/dex/.local/state/rpg-kingdom-supervisor/c5b-signing.key" \
  --spent-nonces "/home/dex/.local/state/rpg-kingdom-supervisor/c5b-used-nonces.json"
```

**C5b provides no issuer, key enrollment, promotion or nonce-consuming endpoint.** Do not generate operator signing keys or signatures in a Codex issue workspace. The signing key and consumed nonce ledger must be host-controlled, private and separately provisioned; a worker-provided JSON approval is never trusted. The output reports `verified-for-review-only`, `consumed=false`, `copyback=false`. This verifier alone **does not prevent reuse of a valid envelope**: future host-only promotion must atomically spend its nonce under an exclusive lock *after* fresh stage hash checks and *before* any write. Until that exists, no approval constitutes authority to copy a file.

The current fixed-scene acceptance contract is deliberately limited to the original C4 proof scene and its two sidecars. URP asset migrations, other source assets, and project settings remain non-approvable by this verification contract. Same-account Unity C# is trusted local code, **not** an OS sandbox.
