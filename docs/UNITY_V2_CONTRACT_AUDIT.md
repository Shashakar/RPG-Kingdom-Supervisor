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
