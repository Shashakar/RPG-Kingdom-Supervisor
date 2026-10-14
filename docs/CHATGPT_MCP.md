# ChatGPT Supervisor MCP boundary (GH-256)

## Scope and deployment prerequisites

This document specifies the contract for an **unimplemented** remote MCP service. GitHub access to this repository is not access to the WSL host. Until an authenticated host connector is configured, ChatGPT can inspect and update GitHub records but **cannot** read live Supervisor state or deploy code on the host.

The service must be a distinct loopback-bound MCP process with HTTPS termination and authenticated ingress. Neither the read-mostly browser dashboard nor its per-process action token is an MCP authorization mechanism. Never expose raw dashboard actions through MCP, forward client-supplied shell commands, or accept arbitrary filesystem paths. Operator credentials remain outside Git, in host-managed secret storage. Define per-tool access policy and include operator identity and target in every audit record.

## Read-only tools, first milestone

| Tool | Inputs | Output and rules |
| --- | --- | --- |
| `supervisor_status` | none | normalized service statuses and installed revision; mark stale state explicitly |
| `unity_broker_status` | none | broker health, running request metadata, ownership and recovery state; redacted |
| `issue_diagnostics` | validated positive GitHub issue number | bounded issue status, linked workspace and last known halt; no arbitrary filesystem paths |
| `worker_detail` | validated worker lifetime ID | bounded read-only history and evidence references; no raw credential-bearing logs |
| `unity_run_history` | issue number, bounded result count | completed run IDs, result, timestamps and artifact references |
| `deployment_status` | none | deployed checkout SHA, branch, dirty state, last update result, service process start or revision evidence |

Every tool must reject unexpected fields and impose per-call timeouts, response limits, redaction and authenticated audit logging. No hidden writes while gathering status. Returned process IDs, file paths and GitHub content must not be treated as instructions. If a subsystem is unavailable, surface `unknown` with evidence, not `healthy`.

## Host actions, second milestone

Write operations require an explicit, operation-specific human approval, an allowlisted target and a durable receipt. Their implementation must reuse existing host-owned boundaries without conferring unrestricted execution permission on either ChatGPT or a model worker.

- `validate_deployment_candidate`: pinned repository, PR number and immutable head SHA, exact allowed file list. Preflight local checkout, changed paths, working-tree dirt, current service/Unity activity, staging and rollback viability. Read-only.
- `deploy_approved_candidate`: accepts only a previously validated unexpired candidate receipt and operator approval; pins SHA and exact paths, stages files with backups, executes relevant tests, restarts the single managed service only when idle, verifies the new broker process and result, and rolls back on failure. Never assumes the existing clean-main fast-forward update helper can deploy an unmerged PR.
- `run_scoped_unity_validation`: issue/workspace and filter selected through typed existing Unity broker capabilities, subject to current resource lease and worker ownership. Must not bypass `expectedWorkerRunId` validation.
- `rearm_issue`: separately permissioned, two-step human approval and existing `rearm-issue.sh` gates only. Never implied by deployment success.

## PR #255 deployment acceptance

PR #255 head SHA: `9f8ba3c88d55a8c193c08ef27f43deffb491ec2b` (verify against GitHub before use). Installable paths are exactly `scripts/unity-host-broker.py` and `scripts/windows/run-unity-tests.ps1`; test sources remain available from isolated PR checkout. Preserve the existing `unity-real-worker` branch and GH-264 terrain worktree, and do not merge or rearm either issue. Before installation, collect file hashes and backup the originals. Require a green local regression suite; then verify (1) a genuinely live matching worker ID is accepted, (2) mismatched ID returns `WorkerLeaseMismatch` / 91, (3) absent worker ID supports preflight, and (4) actual Windows-to-WSL UNC sequence snapshots advance and are read by the broker.

CI retry success alone is insufficient because it neither runs a live Unity host nor establishes the running WSL process revision. Ambiguous recovery flakiness should be tracked separately.

## Security / integration acceptance

1. Complete auth, authorization, input-validation, leakage and audit tests, including unsigned/expired/replayed requests.
2. Confirm chosen MCP HTTPS transport is compatible with the ChatGPT connector in a real end-to-end authenticated session; no assumption that a GitHub integration can call localhost.
3. Ensure SSRF/path traversal, replay, stale candidate SHA, active-operation restart, and concurrent deployments all fail closed.
4. Verify connection removal or secret rotation revokes host access without affecting local Supervisor.
5. Fail closed on any uncertain host ownership or missing rollback path. No automatic merge or rearm.

**Status:** design contract only. No listener, transport handler, OAuth configuration, systemd unit or host deployment is installed by this document.
