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
