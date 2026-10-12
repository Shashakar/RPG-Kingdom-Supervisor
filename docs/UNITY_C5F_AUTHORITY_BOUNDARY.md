# C5f: independent promotion authority and stage freeze design

Tracks Supervisor #228 after #226 / PR #227. **State: security design and observational host audit only.** This PR does NOT implement the service, freeze, copyback, human approval, nonce consumption, Unity validation or any writes to game scene assets. RPG Kingdom #260 stays halted.

## Selected trust boundary

The persistent Unity Editor currently executes worker-authored C# as the operator's ordinary Windows identity. That script may access anything the user identity can. Local TTY prompts, issue labels, HMAC keys stored under that same identity, GitHub comments and worker-provided JSON are NOT independent promotion authorization. Moving an issuer script outside the Git workspace does not fix this.

The chosen direction is a **protected Windows promotion service under a distinct OS identity**, combined with **transaction-specific human presence and user verification** (proposed: WebAuthn/FIDO2 with required UV, verified challenge, origin and RP ID). A separate service is necessary but not enough: a service endpoint callable by untrusted worker C# cannot grant approval without independently verified human intervention. The authenticator must sign a service-generated challenge bound to an on-screen exact asset/destination summary. No model-supplied signature, nonce or approval can be substituted.

- The authoring worker and persistent Editor may write the disposable stage, but never the private snapshot, signing material, nonce ledger or service-owned destination.
- The protected service alone controls stage handoff/freeze, private Windows NTFS snapshot and subsequent copyback transaction. Its authenticated IPC must not provide a worker-callable bypass.
- The human reviews issue, source branch/HEAD, frozen generation, exact asset hashes and GUIDs, exclusions, target workspace, expiry and one-time nonce. A valid approval must not be reusable for any other snapshot, actor or destination.
- The Windows worker account must not be allowed to impersonate the protected service or read its secrets. A privileged local administrator or fully compromised host is out of scope for this local service; a separately protected remote signer is required if that stronger threat model matters.
- If protection of this service cannot be demonstrated, the alternative is moving the Unity worker process into a restricted account/VM and proving isolation. Existing same-account authoring may continue only while promotion remains disabled.
- No automatic merge. Human PR review and source-scene protection remain mandatory.

### Role matrix

| Actor | Edit disposable Unity stage | Read private snapshot/keys | Authorize promotion | Perform write transaction |
| --- | --- | --- | --- | --- |
| Codex / Unity C# in ordinary Windows account | Yes, under current C3 grant | No | No | No |
| Operator shell under same identity | Read-only probes and explicit handoff request | No private key/snapshot writes | Not through TTY alone | No |
| Protected Windows service identity | Freeze/snapshot after verified ownership | Yes | Verify independent human assertion | Future, separately reviewed implementation only |
| Human reviewer | No implied direct source write | Review manifest/receipt | Independent user-presence verification | Final PR review/merge only |

## Required freeze lifecycle, not yet implemented

1. **Drain gate:** after a deliberate operator handoff request, broker atomically rejects new stage-write/validation requests and new Unity resource leases. No read-only inspection action may forcibly close the operator Editor.
2. **Process quiescence:** verify all owned Unity Editor/import worker and batchmode processes have exited, no foreign Editor is active, and there are no outstanding broker operations, ambiguous PIDs or abandoned Unity leases. Verify process creation identities; a PID alone is not enough.
3. **Service-owned exclusion:** service obtains an OS-enforced Windows snapshot/write boundary and denies access to the worker identity. WSL flock is NOT assumed to exclude Windows processes; a mutable stage cannot be described as immutable merely because broker reports idle.
4. **Volume and path inspection:** confirm the actual Windows NTFS volume serial and stage root, and reject junctions/reparse points/path swaps using safe Windows handle-based APIs. A WSL 9p/drvfs mount check is not sufficient NTFS evidence.
5. **Protected snapshot:** copy allowed plus quarantined changes into a private service-owned snapshot, flush using tested Windows durability semantics, rehash and produce a generation receipt binding source revision, protected PlaytestScene hash, every changed file/Unity GUID and quarantined exclusion. Only that immutable snapshot may later be the subject of approval. The mutable stage can resume afterward without changing snapshot bytes.
6. **Independent human authorization:** service creates single-use challenge bound to snapshot generation, destination feature branch and exact HEAD, issue/grant, exact file hashes, expiration and nonce. Verify separate human ceremony before any eligible transaction. Approval and snapshot mismatch always deny.
7. **Later promotion work:** after #228 validation and separate human review, implement a separate write-capable transaction with durable nonce reservation, crash journal, selective copy to dedicated feature branch, Windows Unity validation, rollback and PR-only handoff. Do not build the promotion endpoint in this audit slice.

A crash while draining, freezing or reviewing leaves the stage/approval gate closed until owner-scoped recovery validates the process census, pending journal and snapshot generation. Never take another issue's lease or silently replay an approval.

## Implemented C5f audit

Command from the trusted WSL operator host with the existing C3 grant and C5e environment:

    python3 scripts/unity-stage-authority-audit.py --workspace "$HOME/code/rpg-kingdom-symphony-workspaces/GH-261"

The tool derives the owned PID and Windows stage solely through C5e and C3 grant checks, then queries the Windows Unity process SID, the calling Windows interoperability token SID and the C: logical disk's filesystem using PowerShell/CIM. It rejects missing/mismatched process identity, malformed Windows facts, unexpected project location or a non-NTFS C: volume. It does not accept a worker-supplied process ID, Windows SID, stage path or signature.

Matching Windows identities produce the observation status **same-identity-no-isolation**. Differing SIDs produce **different-identities-not-yet-attested**, never an approval. In both cases the output explicitly reports independentHumanAuthorization=false, hostServiceIdentityVerified=false, operatorPresenceVerified=false, exclusiveStageLeaseVerified=false, immutableSnapshotVerified=false, stageFrozen=false, promotionAuthorized=false and copyback=false.

The observed Editor can exit or stage files can change immediately after an inspection. A successful probe does NOT freeze the Editor, secure a snapshot, establish an immutable manifest or authorize any asset promotion. This is a diagnostic supporting the future service deployment only.

Local deterministic test (does not touch Windows, Unity, or source files):

    python3 tests/unity-stage-authority-audit-test.py

## Remaining acceptance for #228

- [ ] Implement and verify protected Windows service account, ACLs and human-presence enrollment on real host; worker Unity C# cannot self-authorize or read protected materials.
- [ ] Add broker drain and safe operator-controlled handoff; reject late requests, competing processes, stale PIDs, ambiguous workers and abandoned leases.
- [ ] Prove Windows NTFS volume identity, non-following handle checks, exclusive stage access and WSL/Windows lock interactions.
- [ ] Capture immutable snapshot under the service and prove post-scan edits cannot affect it; verify signed challenge binds exact snapshot, feature HEAD, files, metadata GUIDs and quarantined changes.
- [ ] Test interruption/recovery, stale snapshot/approval, worker privilege abuse, revocation and bounded cleanup on the actual Windows host.
- [ ] Obtain human review of evidence/rollback steps. Live copyback and Unity validation remain separately authorized work.

No existing worker-exposed broker command was expanded. No production scene edits, approval grants, nonce spending, feature asset copy or GH-260 rearm were performed.


## C5f broker dispatch drain (phase 2, code complete; Windows host unverified)

New cooperating-host WSL gate scripts:

    python3 scripts/unity-stage-operator-drain.py activate \
      --workspace "$HOME/code/rpg-kingdom-symphony-workspaces/GH-261"

This command calls the C5e read-only ownership/consistency inspection, then
takes the same POSIX flock used by the WSL Unity broker before it launches a
Unity request. With that lock held, it rechecks broker idle, absent Unity
resource lease, grant revision/PID/stage and full stage-manifest digest before
writing a private, persistent "draining" marker to the WSL host state root.
New Unity resource-lease admissions also check the same WSL gate lock.
All subsequent broker requests (including health and preflight) receive
StageDraining, and broker status eventually reports "draining".
The C5e inspection also rejects the marker immediately, before status refresh.
The broker fails closed on corrupt/linked/over-permissive marker state and
preserves the drain across broker restarts. Before launching a child, the broker
also writes a dispatch reservation under that lock; any restart with an
ambiguous unfinished reservation remains blocked, including across further
restarts, until an operator separately verifies actual process ownership.
Do not clear an ambiguous reservation merely because its PID was not recorded.

Activation returns an operationId to cancel that *broker scheduling* drain,
only after verifying exact issue/operation identity and idle live broker:

    python3 scripts/unity-stage-operator-drain.py cancel \
      --issue GH-261 --operation-id EXACT_OPERATION_ID_FROM_ACTIVATION

A crash or uncertain broker status blocks cancellation until operator recovery.
This cancel command is procedural scheduling control, **NOT** a human-authenticated
approval: same-user code may still be able to invoke it. Do not store the ID
as a secret and do not treat it as an authorization token.

Critically, the persistent Windows Unity Editor remains running immediately
after activation. Worker C# or other same-account Windows processes could
still edit the stage directly. The WSL flock does not lock Windows files.
The marker is therefore explicitly **broker-dispatch-draining-not-frozen**,
and cannot be fed to promotion. The protected-service account, safe Editor
handoff, verified Windows process census, exclusive private snapshot and
independent user-presence ceremony remain separate mandatory follow-on work.

Deterministic tests include a real synthetic broker subprocess to prove that
a staged request cannot launch across activation, that malformed/linked state
blocks, and that broker restart preserves the deny. No actual Unity tests
or Windows-volume validation are claimed. Run:

    python3 tests/unity-stage-operator-drain-test.py


## C5g post-handoff Windows census and scratch snapshot experiment (phase 3)

**This is non-promoting work.** No protected Windows service identity, forced
Editor shutdown, real-stage asset copy, independent human authentication,
exclusive NTFS file lock or OS-protected immutable snapshot is supplied.

After the operator manually closes the disposable Unity Editor, while the C5f
dispatch drain remains active, run from the trusted WSL host:

    python3 scripts/unity-stage-windows-quiescence.py

This read-only command derives issue, revision and stage from the existing
C5f drain and C3 grant (no worker-supplied stage path). It requires a live
draining/idle broker, absent Unity resource lease, matching source revision and
Unity version. Under the cooperating dispatch lock it samples all Windows
Unity.exe processes three times around two full stage/source manifests; any
Editor, asset import worker or batchmode Unity process denies the observation.
It confirms the current Windows C: Win32_LogicalDisk reports NTFS and a
volume serial. This still does not independently verify stage volume handle
identity, process creation times, root reparse points or OS-enforced file
exclusion.

Success is a windows-quiescence-observed-not-frozen *observation*, not a
freeze. It always reports stageFrozen=false, immutableSnapshotVerified=false,
independentHumanAuthorization=false, promotionAuthorized=false,
nonceConsumed=false and copyback=false. Other Windows writers may modify files
after the scan, and a scan can miss uncoordinated mutations.

The scratch-only experiment takes NO real stage, source, destination,
credentials, approval or service arguments:

    python3 scripts/unity-stage-windows-snapshot-lab.py
    python3 tests/unity-stage-windows-snapshot-lab-test.py

It creates synthetic Unity scenes, metadata and a quarantined URP asset in a
fresh temporary directory, copies a bounded full project inventory into an
unrelated synthetic directory, fsyncs file contents, rehashes both sides, and
fails on mutations during copying. It verifies that a later source change does
not rewrite copied bytes. It rejects symlinks and Windows reparse/junction
entries when the OS reports those attributes and cleans up partial scratch
copies. It does not prove Windows parent-directory flush durability, handle
safety against untrusted concurrent writers, or protection from a worker
sharing the copying user's identity.

Its output deliberately reports realStageUsed=false,
windowsServiceDeployed=false, immutableSnapshotVerified=false,
stageFrozen=false and copyback=false. The dedicated Windows GitHub Actions
job runs these synthetic tests on a Windows runner; this is not a real-local
Unity Editor, credential-isolation or NTFS host acceptance test.

### Remaining blockers (do not mark #228 complete)

1. Install and independently verify a protected Windows service identity
   separate from Unity authoring; test worker cannot read service secrets,
   private snapshot or access privileged promotion IPC. Require independent,
   transaction-bound human user verification for any future promotion.
2. Operator-controlled Editor handoff and process creation-time identity checks;
   Windows-enforced stage exclusion, not only cooperative WSL flock.
3. Real private service-owned immutable snapshot, Windows non-following file
   handles, NTFS reparse/volume serial identity, GUID inventory, durable
   journal/recovery and hard process termination tests.
4. Real host negative tests under the worker identity and a separate
   reviewed PR authorizing a future write-capable promotion implementation.
