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
