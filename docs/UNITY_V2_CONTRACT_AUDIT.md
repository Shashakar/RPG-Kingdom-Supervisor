# Unity V2.0 — initial contract inventory (in progress)

Tracked by [#260](https://github.com/Shashakar/RPG-Kingdom-Supervisor/issues/260), under [Unity Integration V2](UNITY_INTEGRATION_V2.md).

## Verified from repository main

| Component | Location | Observed boundary |
|---|---|---|
| Worker-facing validation | `scripts/unity-runner.sh` | Submits host broker requests |
| Worker-facing authoring | `scripts/unity-author.sh` | Separately submits author requests |
| Validation broker | `scripts/unity-host-broker.py` | `validate_lock` (~178), `prepare_request` (~202), `resolve_worker_lease` (~517), admission (~1146) |
| Authoring broker | `scripts/unity-author-broker.py` | `validate_shared_lock` (~89), `validate_authorization` (~107), `validate_request` (~153) |
| Host preflight | `scripts/unity-resource-guard.sh` | Editor lock and readiness smoke; also persists authoring and preflight state |
| Progress/recovery | `scripts/unity-host-broker.py`, `scripts/windows/run-unity-tests.ps1` | Progress tracking, scoped cancellation, restart reconciliation |
| Scoped Windows execution | `scripts/unity-runner-host.sh`, `scripts/unity-author-host.sh` | Crosses WSL -> Windows Unity execution boundary |
| Branch handoff | `scripts/git-handoff.sh`, `scripts/git-handoff-broker.py` | Host-owned Git changes and PR submission |

Main's validation broker resolves a worker lease at dispatch, whereas the author broker separately checks editor lock and authoring authorization. This represents **at least two independently coded admission checks**; equivalence is not yet established.

## Known compatibility discrepancy (operator-observed)

Installed `unity-real-worker` test `tests/unity-host-ownership-test.py` calls `verify_worker_ownership` and expects `WorkerLeaseRejected`/75 plus a `requiresWorkerLease` field. PR #255 expects inline `expectedWorkerRunId` verification returning `WorkerLeaseMismatch`/91; its focused regression passes in isolation but the installed test fails with a missing function. This is **not proof** that one branch's status code should replace the other. Compare callers and migration contracts before writing the V2 protocol.

## Required next audit activities

1. Extract all JSON request/response fields, privileged flags, transition predicates, error code mappings and state side effects from both brokers, all clients and the current game branch.
2. Compare `main` / #255 / installed `unity-real-worker` at the exact Git SHAs, ensuring installed-only tests are carried into the compatibility suite.
3. Record operational snapshots (broker worker lease, lock, Unix/Windows PIDs and progression) read-only on host before selecting restart semantics.
4. Implement behavior-driven contract tests with broker event loop and fake host runner, not source substring assertions.
5. Propose final V2 protocol and migrations with old/new status compatibility and rollback before production host deployment.

## Non-goals in this initial commit
No running services touched, no broker modifications, no GH-264 rearm, no changes to game scene or staging assets. All architectural findings beyond the verified facts above are pending further tests.

## Phase 0 characterization evidence — first executable test

`tests/unity-v2-lease-baseline-test.py` is now wired into `tests/run.sh`. It imports the broker's **actual** `resolve_worker_lease` function and exercises missing, unique live, foreign workspace, dead, duplicated, malformed and invalid-PID lease records. This intentionally records how `main` behaves; it does not assert that resolution alone authorizes a request. The test uses a deterministic mocked liveness predicate and never launches Unity.

### Current broker contract detail

- Validation `prepare_request` accepts `protocolVersion`, operation, test/capture arguments and optional `clientPid`, checks the request is inside the issue's workspace, and requires the shared editor lock for non-health operations. It does **not** presently parse `expectedWorkerRunId` in `main`.
- Validation broker subsequently calls `resolve_worker_lease`, which only returns a worker tuple when precisely one matching live record exists; otherwise it returns `(None, None)`. An absent worker ID is currently not a rejection by itself, supporting host preflight.
- Authoring `validate_request` independently checks `protocolVersion`, `operation=author`, tier, scene/assets, operations, shared editor lock and stored authoring grant. No equivalence with validation broker's live worker resolution has been established.
- The existing stall-recovery regression expects `StallRecoveryBlocked`/92 for ambiguous ownership. The same exit code `91` is used by baseline `Stalled`; PR #255 also proposes `91` for a distinct `WorkerLeaseMismatch`. V2 must make machine-consumable statuses authoritative and explicitly audit numeric exit-code collisions rather than perpetuate assumptions.

### Still outstanding before a protocol decision

Installed-branch `unity-host-ownership-test.py` is **not** present in PR #255 or current main, and its reported behavior must be checked against the original installed host branch before adopting a schema. The new characterization test is not live-host verification. No deployment or worker rearm is authorized by this document.

## Request admission characterization — added V2.0 tests

`tests/unity-v2-request-baseline-test.py` runs `prepare_request` against an isolated fake workspace and lock. It records current v1 behavior: health is admitted without lock or durable worker lease; optional positive `clientPid` is parsed; legacy v1 ignores an `expectedWorkerRunId` claim; malformed protocol/operation/client ID fail with 64; EditMode needs the matching lock and rejects mismatched owner with 82. Unlike the #255 string assertion this imports and executes production request validation code. It does **not** exercise the full broker loop or live Windows Unity.

### Proposed compatibility matrix (requires review against installed callers)

| Case | main v1 observed | installed branch test expectation | V2 target |
|---|---|---|---|
| Health preflight, no worker claim | allowed | not yet verified | allow, no worker ownership |
| Locked EditMode, no run-ID claim | lease resolved if available, client fallback | dependent on `requiresWorkerLease` | allow explicitly only for marked host preflight; reject absent claim for worker-issued validation |
| Claimed correct live run ID | ignored in main | allow | allow only after unique live lease check |
| Claimed wrong live run ID | ignored in main | `WorkerLeaseRejected` / 75 | fail-closed, single standardized machine-readable code |
| Missing/dead/duplicate live lease | resolver returns none | `WorkerLeaseRejected` / 75 | reject worker-owned request; preserve host-only preflight |
| Claim present but lease-required flag false | flag ignored in main | schema reject / 64 | reject incoherent request; avoid silent fallback |
| Ambiguous stall PID ownership | `StallRecoveryBlocked` / 92 | same behavior not yet verified | keep request blocked; never kill unrelated process |

Do not implement target policy until caller/host comparison establishes the exact preflight distinction. Avoid reusing existing `91` `Stalled` code for lease rejection without explicit migration handling. Make `status` authoritative and retain compatible v1 response translation until consumers migrate.

### Phase gate still open
The request parser is now behavior-tested, but *actual broker event-loop authorization* and both authoring/validation broker integration remain to be proven. PR #261 must remain draft, not deployed or merged merely for passing CI.

## Shared lease implementation and test boundary

Both brokers now import `scripts/unity_worker_lease.py`. Under protocol v1, a supplied `expectedWorkerRunId` is verified against the unique live host worker; malformed or internally contradictory `requiresWorkerLease` claims fail schema validation. The shared response chosen for a mismatched claim is `WorkerLeaseRejected`/75; this remains a migration candidate until installed caller compatibility is confirmed. Legacy unclaimed requests still work, so the client-authentication/worker-preflight distinction is **not complete**.

`tests/unity-v2-broker-admission-test.py` exercises validation `prepare_request`, the shared admission predicate and authoring `validate_request` against isolated fixture leases/locks. It confirms mismatched authoring claims are rejected, correct claims reach subsequent authorization, and contradictory claims fail parsing. The validation broker's **outer event loop** and an actual Unity subprocess are not yet exercised by this new test; do not describe it as a full end-to-end integration test.

Before enabling mandatory claims or installing on `unity-real-worker`, migrate `unity-runner.sh` and `unity-author.sh` to attach host-issued unique worker identity only for worker-owned operations, preserve genuine health/smoke preflight, test replays/duplicate claims and compare host error handling. Running host state remains unchanged.

## Validation broker process-loop test (V2.0)

`tests/unity-v2-broker-loop-test.py` starts the actual validation broker on a temporary workspace/state tree with a fake host executable. It checks that missing, mismatched and duplicate live lease claims are rejected with `WorkerLeaseRejected`/75, without invoking the host runner; and that contradictory protocol fields fail with 64. Its fake host is never Windows Unity and it intentionally exercises rejection cases only. This closes one previously identified gap: the validation broker event loop is now tested directly. The authoring broker and valid claimed-worker execution require equivalent lifecycle tests, plus a client migration policy that safely distinguishes preflight from worker ownership.

**Security distinction:** A worker-readable durable run ID is a correlation value, not a cryptographic credential. A client copying a matching ID from a telemetry file must never be treated as independent proof that the requester is the owning worker. Real authority comes from a host-trusted worker submission channel and workspace/process boundary. Mandatory client claims cannot safely be enabled until that path is defined and tested.

## Authoring loop success/rejection fixture

`tests/unity-v2-author-broker-loop-test.py` now launches the real author broker event loop in a temporary workspace with a synthetic grant and fake host runner. It asserts wrong claimed leases reject with 75 and never invoke the runner; correct live claims with valid authoring grant invoke the runner exactly once and return a successful structured receipt; invalid claim combinations return schema error 64 without another launch. This is still a simulated host, not a Windows Unity Editor run.

## Client migration decision pending

Do **not** modify the worker clients to infer a claim from files readable by arbitrary code in their own workspace and call that authentication. The claim is useful for *binding* an operation to a known run, not alone an authorization boundary. Before making claims mandatory, determine a host-issued request provenance method (e.g. request creation by trusted host shim with a verified worker lease or a protected submission endpoint with matching lifecycle) that distinguishes worker-owned operations from host preflight. Add behavioral tests to ensure a forged valid run ID from another process/workspace does not bypass admission. Preserve existing health/smoke preflight while migrating clients. Until then, the compatibility path accepts legacy unclaimed requests and the main security boundary remains workspace+editor lock+grant.

## Trusted submission migration design (proposed; not yet deployed)

### The trust boundary

The current brokers ingest workspace-writable JSON. A worker can therefore submit any `expectedWorkerRunId` string, and another local process able to write the same workspace may submit the same string. Matching an ID against `workers/active` is a useful consistency check, **not proof of who created the request**. Adding an environment variable to the shell client or having it read the worker telemetry does not solve this.

A reliable migration needs a trusted admission step, independent of model-supplied fields:

1. Supervisor's host dispatch owns the active worker registration, workspace path and editor lock.
2. A host-side admission shim/service identifies the submitting worker through an operating-system-enforced channel or host-issued short-lived per-worker capability unavailable to unrelated local processes. The authority is bound to issue, workspace, worker lifetime, operation, and protocol version. Do not put reusable credentials in Git-tracked files, logs, Unity artifacts or model prompts.
3. Admission checks that exactly one live worker lifetime matches the registered workspace, that the shared editor lock is held, that the job's scoped authoring grant applies, and that the bound authority has not expired or been revoked. Authorization is rechecked at execution, not merely at enqueue.
4. Independent host-origin preflight is a separate operation class, not a worker request forged by omitting the lease claim. Its trusted host caller may run `health` without lock and the existing locked smoke test before Codex is launched.
5. Broker-owned acknowledgements and terminal receipts are keyed by a durable request ID; retries return the existing receipt or a deterministic in-progress result rather than launching the same authoring job twice.

### Acceptance cases before enabling mandatory identity

| Case | Expected |
|---|---|
| Genuine host health and pre-Codex smoke | Allowed with explicit preflight authority |
| Active worker, correct host-bound provenance | Allowed for scoped operations |
| Matching run ID forged in worker-writable JSON | Rejected without host-bound provenance |
| Old or revoked run ID | Rejected |
| Dead or ambiguous worker lease | Rejected without invoking Windows |
| Another workspace's authority | Rejected |
| Expired request or broker restart/retry | Never duplicate mutations |
| Unclaimed legacy request | Accepted only under explicitly selected compatibility mode, disabled after migration |

This design intentionally does not pretend that the current code already enforces these guarantees. Because deployment credentials and process ownership are host concerns, choose and prototype the actual OS-protected transport with live WSL fixture validation before altering either worker-facing client. Run both old and new protocol tests until the installed `unity-real-worker` consumer compatibility is proven.

### New validation success coverage

`tests/unity-v2-valid-broker-loop-test.py` exercises a correctly claimed live worker through the actual validation broker event loop and a disposable fake host runner, verifying one successful execution. This complements rejection tests but is **not** evidence that a forged matching ID is rejected or that Windows Unity ran.

## Host receipt primitive (implementation candidate)

`scripts/unity_admission_receipt.py` provides HMAC-SHA256 integrity and fixed-scope, short-lived receipts over `issue`, canonical `workspace`, `workerRunId`, `operation`, `requestId`, issued time and expiry (max five minutes). The secret is a **host-owned key**, at least 32 bytes, never a workspace/git/model value. `tests/unity-v2-admission-receipt-test.py` covers tampering, cross-issue/workspace/operation/replay-to-other-request, wrong secret, expiration and malformed receipts. The primitive deliberately cannot grant health-preflight authority.

**This is not active authentication.** Neither broker currently verifies these receipts, and no protected issuer/transport has been implemented. Never inject the host key into a worker environment or serialize it with jobs. Receipt signing must be called only after trusted host-side worker validation. A valid signed receipt can still be replayed for the *same* request until expiry unless terminal request IDs are durably deduplicated; issuer and broker idempotency are remaining phase gates.

### Deployment sequence to avoid protocol downgrade
1. Install trusted host issuance + restrictive file/socket permissions, without changing worker clients; prove forged matching IDs cannot mint receipts.
2. Deploy broker verification with explicit temporary v1 compatibility mode, plus acknowledgement/receipt replay cache and authoritative status classification.
3. Migrate validation and authoring clients to the host-issued submission path, preserving separate trusted preflight operations.
4. Audit and disable legacy unsigned worker writes only after end-to-end host verification; ensure restart/rollback is reversible.
5. Reconcile the installed `unity-real-worker` code before restarting Supervisor. A green CI run on remote `main` does not substitute for this.

## Opt-in verification integration (not yet enforcement)

Both brokers now call `validate_submitted_receipt` before host execution. When a request includes `admissionReceipt`, it must validate against a host-only key file selected by `RPGK_UNITY_ADMISSION_KEY_FILE`, the live worker lease, issue, workspace, operation and request identity. Invalid/missing key or invalid submitted receipt returns `WorkerLeaseRejected`/75. An unsigned legacy request **remains permitted** while clients and protected issuer are unimplemented. These changes are code on the draft branch only.

This is a compatibility-stage feature, **not an authorization barrier** against forgery because a requester can omit `admissionReceipt`; do not claim it blocks an attacker from launching a legacy authorized operation. Before deployment, require signed receipts for the worker path using a separate configured policy gate, preserve an explicit trusted preflight path, and implement host-issued credentials/replay receipts. The key file must be host-managed and never writable or readable by worker processes. No key was generated, written to the repository, or installed on the live host.

## Strict receipt enforcement — opt-in compatibility gate

`RPGK_UNITY_ADMISSION_STRICT=1` now causes non-health validation jobs and all authoring jobs to reject unsigned requests (`WorkerLeaseRejected`/75). Correctly signed requests remain eligible. Health requests remain available without a receipt for existing preflight. With the variable unset, legacy unsigned operation requests still work. The signing secret is supplied via host-only `RPGK_UNITY_ADMISSION_KEY_FILE`, and a missing secret cannot validate a signed receipt.

**Do not enable strict mode on the installed server yet.** A trusted host issuer and updated clients are still absent; enabling it now would reject normal authoring/test operations. Additionally, `health` is an unsigned compatibility exception, **not an independently authenticated host-preflight endpoint**; the worker-trust split and operator-only transport remain pending. The existing author-broker grant and lock requirements remain active.

This is staged feature enforcement, not the completed authorization design. No deployment or change to the running host is part of this PR.

## Trusted issuer transport groundwork

`scripts/unity_peer_identity.py` now implements Linux kernel `SO_PEERCRED` extraction for Unix-domain connections, UID comparison, and bounded parent-process ancestry checks against the registered Supervisor worker process. `tests/unity-v2-peer-identity-test.py` covers cycles, missing/dead PIDs, UID mismatch, fake procfs, and actual local socket kernel credentials. This avoids treating worker-supplied JSON `peerPid` or `runId` as proof of identity.

**Not yet implemented:** an actual protected Unix socket listener, authorization decision joining peer identity with fresh lease/lock/grant, host-only key management and receipt issuance, durable request deduplication, or client handoff. Process ancestry alone is also subject to PID-reuse races; the final issuer needs a stable worker process identity (for example recorded Linux process start time) validated at admission. A broker that shares the same Unix UID as worker clients must not rely on socket permissions alone to separate these roles.

Live host deployment remains blocked until these are tested. Current opt-in strict admission would reject ordinary clients.

## Ambiguous-recovery publication-order defect (V2.0)

The CI failure on commit `13b3fa17` (`unity-broker-test: broker must expose blocked ambiguous recovery`) exposed a concrete broker publication race. In `make_terminal(keep_active=True)`, the broker wrote the terminal response before writing `status.json` with `state=blocked`. The worker CLI finishes as soon as the response appears, so an immediate status read could observe stale `running` state. V2 now publishes `active.recovery_blocked`, a matching `lastResult`, and atomic blocked status **before** publishing the response. The existing broker test enforces this ordering without polling. This does not replace load/stress testing of recovery across repeated host operations, but resolves the specific observable ordering defect.

## Follow-up: blocked recovery must not be reclassified by client exit

CI run `38525502364` captured the apparent reconciliation failure with `state=ready` but `lastResult.status=ClientAbandonRecoveryBlocked` (94), although the earlier terminal response had been `StallRecoveryBlocked` (92). The broker checked `not request_owner_alive(active)` **before** `active.recovery_blocked`; the expected client exit after consuming a terminal response caused a second recovery result to overwrite the first. Reordered these branches so an already-terminal blocked request only waits for its host child to exit and preserves the original terminal status. The process never becomes eligible for broad termination. The existing ambiguous-recovery regression now checks the preserved status at the client terminal boundary and after host resolution. This change still requires passing CI and repeated stress verification before host deployment.

## V2.0 host issuer and client prototype (draft PR #261)

A Linux Unix-domain socket issuer is implemented at `scripts/unity-admission-issuer.py`. It obtains `SO_PEERCRED` credentials from the kernel, checks a unique live worker lease, that the calling process is the registered worker or a descendant, matching UID and workspace, editor lock ownership, the worker PID's recorded `processStartTicks`, and presence of an authoring grant for author operations. It issues a scoped, short-lived signed receipt without exposing the host key to clients.

The companion `scripts/unity-admission-client.py` is optionally invoked by `unity-runner.sh` and `unity-author.sh` before enqueuing a request, when `RPGK_UNITY_ADMISSION_SOCKET` is set. It writes the server-returned worker claim and receipt to the request envelope. The request is not enqueued if the issuer refuses it. Unconfigured clients retain legacy behavior; host `health` preflight does not request worker admission. Both brokers already verify submitted receipts, and `RPGK_UNITY_ADMISSION_STRICT=1` enforces signed worker requests.

**Host prerequisites before opting in:**
- A separately managed host-only key at least 32 bytes, mode 0600, owned by the service account. Never store it in a checkout, workspace, prompts, CI output or shared Windows mount.
- A protected issuer socket path that is not publicly writable, with the issuer launched as a persistent Supervisor host service.
- The host worker lease producer currently needs migration to record the kernel `processStartTicks` string on dispatch (as well as runId, PID and workspace) and reliably revoke leases on exit. This draft does **not** modify that external producer; old leases intentionally fail admission.
- Route worker-launched clients to the socket but **not** the host-origin preflight smoke test. The latter still uses the legacy editmode path and therefore cannot coexist with global strict enforcement until it has a distinct authenticated preflight channel.
- Broker and issuer must use the same host-only signing key. Operational rollback should remove strict mode and client socket routing together.

**Important:** This is a prototype, not production deployment approval. The issuer must be exercised through its actual socket on WSL with a registered Codex worker process, and strict-mode broker execution and retries must pass on Windows Unity before rollout. The issuer's present `author` check verifies the grant marker exists; the authoring broker remains the authority for its *contents* and operation restrictions. The broker receipt currently authenticates metadata, not an immutable hash of authoring payload bytes; replay/idempotency hardening is still pending.
