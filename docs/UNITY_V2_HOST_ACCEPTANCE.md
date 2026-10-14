# Unity V2 — WSL/Windows host acceptance gate

**Status:** not executed. GitHub CI runs simulated Linux broker/issuer hosts, not the Unity Editor installed on the operator workstation. Passing CI is necessary but insufficient for production deployment.

This is an operator-controlled acceptance plan. **Do not rearm GH-264, change the running `unity-real-worker` checkout, or restart services as part of a read-only audit.**

## 1. Capture an immutable host baseline

From the currently installed WSL Supervisor checkout, collect the following without modifying anything:

```bash
pwd
git status --short
git branch --show-current
git rev-parse HEAD
systemctl --user --no-pager status rpg-kingdom-supervisor.service rpg-kingdom-diagnostics.service || true
```

Record the broker's state, active worker count, Unity editor version, Windows host process status, and resource lock owner. Do **not** print, copy, or upload signing keys, GitHub tokens, Codex credentials, or full environment dumps. Record whether `workers/active/*.json` contains an immutable process start identity (`processStartTicks`) produced by the trusted host process at dispatch. If it does not, signed client admission is blocked.

## 2. Security topology requirements

- The issuer runs under a different Unix UID from Codex workers; the signing key is owned and readable only by the issuer or an appropriately isolated verifier. The key never appears in a workspace or prompt.
- The Unix socket is owned by the issuer, group-accessible to the authorized worker group, and located inside an issuer-owned directory that workers cannot write to or replace. The issuer must start without `RPGK_UNITY_ISSUER_TEST_MODE`.
- `--worker-uid` and `--worker-gid` explicitly name the worker account/group; the kernel, not request JSON, supplies the caller's actual identity.
- The unique live worker lease includes run ID, PID, canonical workspace path and kernel start ticks. The issuer and brokers must reject dead, ambiguous, mismatched or revoked leases.
- Both brokers must verify against the same properly isolated signing authority. The durable execution journal under Supervisor state must not be writable by workers.
- The host-origin locked preflight smoke run has a **separate authenticated route**; it must not disable strict mode globally or masquerade as a worker request. Until implemented, strict mode must stay off on the installed host.

## 3. Simulated failure matrix (isolated test workspace)

| Scenario | Required observable result |
|---|---|
| Host health check | Succeeds without a worker lease and without modifying editor state |
| Host-owned locked pre-Codex editmode smoke | Succeeds through authenticated host preflight |
| Valid active worker validation | Broker starts Windows Unity once, emits structured result and history |
| Valid scoped authoring | Correct branch, workspace and grant; Unity starts once; files are reviewable |
| Missing/forged receipt | Reject before invoking Windows host |
| Cross-workspace/cross-operation receipt | Reject before invoking Windows host |
| Expired or altered receipt | Reject before invoking Windows host |
| Forged peer process ID / different UID | Issuer rejects before signing |
| Dead/reused/ambiguous registered PID | Issuer rejects before signing |
| Other issue owns shared editor lock | Reject without disturbing that issue |
| Duplicate request ID after response deletion | Journal rejects; no second Unity execution |
| Broker crash after reservation, before receipt | Hold for explicit reconciliation; never blind re-execution |
| Editor stalls before Unity owns a process | Bounded owned-process cancellation only |
| Editor ownership ambiguous | Publish blocked result, preserve process and terminal status |
| Client exits after blocked result | Do not overwrite the blocked classification |
| Broker restart with live inherited child | Advertise blocked; never launch a competing Unity process |
| Recovery after the child exits | Return ready without losing the original terminal reason |
| Repeated clean operations | At least 19/20 unattended attempts, with no hidden approval bypass |

All failure cases must capture the exact request ID, broker state, issued response, Windows Unity process owner, exit code, and whether a host invocation happened. Never run broad process killing or delete editor data to force a green result.

## 4. Performance and rollout constraints

Compare token consumption and time-to-reviewed-PR with baseline for the same bounded scene task. Preserve human approval before merging game changes. Verify at least one mechanical operation, one prefab/scene composition operation, an EditMode test and a visual capture. Review Unity files, logs and produced diffs in an isolated Git branch. **Do not directly edit the production scene** until the game repository's authorized policy change is reviewed and merged.

Do not deploy merely because the issuer socket connects in Linux CI. The installed WSL branch and remote `main` differ, and the broker's legacy compatibility must be reconciled before any rollout.

## 5. Exit criteria

1. Every GitHub CI check green, including the repeated ambiguous-recovery tests and execution-journal concurrency/replay cases.
2. Protected issuer, trusted worker registration and authenticated preflight all demonstrated on WSL.
3. Client -> broker -> Windows Unity -> structured receipt verified for both validation and authoring.
4. Failure matrix passed, journal persistence and rollback strategy documented.
5. Host installation change reviewed; service restarts and game issue rearm remain separate operator-approved actions.

**Current decision:** blocked pending actual WSL/Windows environment verification. This document is not proof of host acceptance.
