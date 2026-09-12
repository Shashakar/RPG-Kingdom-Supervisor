# RPG Kingdom Supervisor

RPG Kingdom Supervisor is the orchestration layer for autonomous Codex implementation work in [`Shashakar/RPG-Kingdom`](https://github.com/Shashakar/RPG-Kingdom).

The supervisor deliberately does **not** live inside the Unity project. RPG Kingdom remains the source of truth for game architecture and repository instructions; this repository owns orchestration policy, narrow runtime adapters, and operator setup.

## Current target

The supervisor is built around the official [`openai/symphony`](https://github.com/openai/symphony) reference implementation and Codex App Server.

Phase 1 proved the end-to-end path:

- GitHub Issues are the work queue.
- `symphony:ready` is the explicit dispatch lease.
- each issue receives an isolated Symphony workspace containing a clone of RPG Kingdom.
- Codex runs through App Server inside that workspace.
- RPG Kingdom's own `AGENTS.md` and documentation remain authoritative for implementation behavior.
- implementation work stops at a pull request; merge remains a human decision.

Phase 2 makes that path usage-aware:

- risk/model labels route work among GPT-5.6 Luna, Terra, Sol, and GPT-6 Astra;
- Luna / medium is the default for normal and unclassified bounded work;
- `risk:investigative` promotes ambiguous debugging and multi-layer investigation to Terra / medium;
- Sol remains the architecture-sensitive tier and Astra remains the hardest end-to-end tier;
- model and reasoning labels allow explicit human/ChatGPT overrides;
- worker turns are capped at four for the current phase;
- a persistent workspace marker prevents a second Codex worker lifetime from starting accidentally;
- `symphony:rearm` is a one-shot human/ChatGPT continuation approval consumed by host preflight, while `symphony:ready` remains only the normal dispatch lease;
- the host also removes `symphony:ready` and adds `symphony:halted` when an attempt ends without a clean host-owned completion path;
- worker prompts scale context gathering to task risk while still obeying RPG Kingdom's repository-mandated reads;
- fixes that change behavior-bearing configuration/wiring must validate the relevant pre-existing behavior as well as the new acceptance path.

The routing policy was adjusted after real #93/#95 benchmarks showed that Luna mechanical work had negligible visible allowance impact while one successful Terra investigative turn consumed 8 percentage points of the five-hour allowance. See [`docs/PHASE2_BUDGETED_ROUTING.md`](docs/PHASE2_BUDGETED_ROUTING.md) for the measurements and routing table.

Phase 3 makes Unity dependence explicit:

- `resource:unity-editor` declares exclusive host-owned Unity Editor access;
- `validation:unity-required` blocks before Codex if the Unity resource/runner is unavailable;
- `validation:unity-optional` permits implementation while requiring missing Unity validation to be reported in the PR;
- conflicting Unity validation labels fail closed;
- a host-side lock prevents multiple future workers from sharing the editor;
- a reviewed `symphony:rearm` may recover a stale Unity lock only when that lock is owned by the same GH issue; another issue's lock is never reclaimed.

See [`docs/PHASE3_UNITY_SCHEDULING.md`](docs/PHASE3_UNITY_SCHEDULING.md) for the scheduling contract.

Phase 4 turns that scheduling contract into real Unity validation:

- `run-symphony.sh` starts a host-owned Unity execution broker before Symphony;
- the worker-facing runner submits typed `health`, `editmode`, or `playmode` requests through workspace-local IPC instead of invoking Windows interop from the Codex sandbox;
- the host broker revalidates workspace/resource ownership and performs the WSL-to-Windows hop through the host-only runner;
- the runner reads the Unity version declared by the issue workspace;
- PowerShell/robocopy mirrors `Assets`, `Packages`, and `ProjectSettings` into a persistent Windows-local staging project;
- the staged `Library/` cache survives across issues;
- Unity Test Framework runs EditMode or PlayMode tests with optional narrow filters;
- `results.xml`, `Editor.log`, and `summary.json` return to the ignored `Logs/SymphonyUnity/` directory;
- active validation records phase/artifact progress so a long-running test can be distinguished from a request that has stopped moving;
- positively-owned stalled requests are recovered through request-scoped cancellation, while ambiguous ownership blocks without killing unrelated Unity processes;
- zero matched tests are reported as `NoTestsMatched`, not as a successful green run;
- test execution is refused unless the current issue owns the Phase 3 Unity lock.

See [`docs/PHASE4_UNITY_RUNNER.md`](docs/PHASE4_UNITY_RUNNER.md) for the runner contract and [`docs/UNITY_STALL_RECOVERY.md`](docs/UNITY_STALL_RECOVERY.md) for progress-aware stall detection/recovery.

Phase 5 removes the remaining model-turn infrastructure dependency from PR handoff:

- `run-symphony.sh` also starts a host-owned Git handoff broker;
- workers prepare their required `codex/*` branch through a typed host request before source edits;
- source implementation remains model-owned, but `.git` metadata writes and final GitHub push/PR network operations are host-owned;
- successful Unity run IDs are supplied to final handoff when `validation:unity-required` is present;
- the host stages/commits the task, refuses force/non-fast-forward history, pushes and verifies the remote SHA, creates or updates the PR, then removes `symphony:ready`;
- PR merge remains explicitly human-owned.

See [`docs/GIT_HANDOFF.md`](docs/GIT_HANDOFF.md) for the Git handoff contract.

Report-only diagnostic work has a separate explicit completion contract layered onto the same host-owned handoff boundary:

- only issues labeled `completion:report-only` may complete without a PR;
- `git-handoff.sh report-complete` requires a non-empty report and rejects dirty/source-changing workspaces;
- Unity-required reports still require fresh passing non-zero runner evidence;
- the host posts the durable issue report itself, persists a host-only receipt, and removes `symphony:ready` only after verification;
- successful report work enters `symphony:report-complete` and remains open for human review/closure;
- partial GitHub lifecycle failures are reconciled from the trusted receipt before the generic halted-worker rule is applied.

See [`docs/REPORT_ONLY_COMPLETION.md`](docs/REPORT_ONLY_COMPLETION.md) for the complete contract.

Current diagnostics work addresses boundaries exposed by GH-97/GH-98: model-free App Server probes can succeed while the actual model-backed turn still sees protected `.git`, unavailable GitHub DNS, or unavailable WSL-to-Windows interop. The Supervisor provides a read-only issue dashboard plus an explicit, low-cost model-turn environment probe. Host brokers emit structured status so diagnostics can report current/last operations without scraping terminal output. See [`docs/DIAGNOSTICS.md`](docs/DIAGNOSTICS.md).

## Repositories

- Game repository: `Shashakar/RPG-Kingdom`
- Supervisor repository: `Shashakar/RPG-Kingdom-Supervisor`
- Symphony upstream: `openai/symphony`

The currently evaluated upstream revision is recorded in [`SYMPHONY_UPSTREAM.md`](SYMPHONY_UPSTREAM.md).

## Files

- [`WORKFLOW.md`](WORKFLOW.md) — Symphony configuration and the RPG Kingdom worker prompt.
- [`scripts/run-symphony.sh`](scripts/run-symphony.sh) — operator launcher that loads the scoped tracker secret, starts/reuses the host brokers, and uses an alternate terminal screen when available.
- [`scripts/routing-policy.sh`](scripts/routing-policy.sh) — deterministic label-to-model/effort policy.
- [`scripts/codex-app-server-router.sh`](scripts/codex-app-server-router.sh) — per-issue Codex App Server launcher.
- [`scripts/before-run-guard.sh`](scripts/before-run-guard.sh) — blocks accidental second worker lifetimes and consumes one-shot reviewed rearm requests before Codex starts.
- [`scripts/git-handoff.sh`](scripts/git-handoff.sh) — worker-facing client for typed branch preparation, PR handoff, and explicit report-only completion.
- [`scripts/git-handoff-broker.py`](scripts/git-handoff-broker.py) — host-owned Git request broker and structured status producer.
- [`scripts/git-handoff-host.py`](scripts/git-handoff-host.py) — bounded host Git/GitHub implementation used by normal implementation handoff.
- [`scripts/report-complete-host.py`](scripts/report-complete-host.py) — host-only verifier/comment/lifecycle adapter for explicitly eligible report-only work.
- [`scripts/reconcile-report-completion.py`](scripts/reconcile-report-completion.py) — restart/after-run reconciliation for a trusted report-completion receipt.
- [`scripts/unity-resource-policy.sh`](scripts/unity-resource-policy.sh) — side-effect-free Unity resource/validation policy.
- [`scripts/unity-resource-guard.sh`](scripts/unity-resource-guard.sh) — host preflight that validates Unity policy/readiness and acquires the exclusive editor lock.
- [`scripts/unity-runner-policy.sh`](scripts/unity-runner-policy.sh) — project-version and test-platform helpers for the Windows runner.
- [`scripts/unity-runner.sh`](scripts/unity-runner.sh) — worker-facing broker client for Unity health/EditMode/PlayMode validation.
- [`scripts/unity-host-broker.py`](scripts/unity-host-broker.py) — host-owned typed Unity request broker and structured status producer.
- [`scripts/unity-runner-host.sh`](scripts/unity-runner-host.sh) — host-only direct WSL/Windows adapter used by the broker.
- [`scripts/windows/run-unity-tests.ps1`](scripts/windows/run-unity-tests.ps1) — Windows staging, Unity Test Framework execution, result parsing, progress reporting, request-owned cancellation, and artifact return bridge.
- [`scripts/release-unity-resource.sh`](scripts/release-unity-resource.sh) — ownership-checked Unity lock release hook.
- [`scripts/after-run-guard.sh`](scripts/after-run-guard.sh) — records the local execution boundary and performs report reconciliation or tracker cleanup/halt handoff.
- [`scripts/install-labels.sh`](scripts/install-labels.sh) — creates/updates routing, continuation, completion, and Unity scheduling labels.
- [`scripts/rearm-issue.sh`](scripts/rearm-issue.sh) — requests the one-shot remote continuation approval plus normal dispatch lease; host preflight owns local stale-state recovery.
- [`scripts/diagnose-issue.sh`](scripts/diagnose-issue.sh) — read-only terminal summary for one dispatched issue.
- [`scripts/serve-diagnostics.sh`](scripts/serve-diagnostics.sh) — localhost-only, read-only issue diagnostics dashboard.
- [`scripts/codex-turn-environment-probe.sh`](scripts/codex-turn-environment-probe.sh) — explicit model-backed environment diagnostic; never run automatically because it consumes allowance.
- [`AGENTS.md`](AGENTS.md) — rules for modifying this supervisor repository.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — supervisor boundaries and phased design.
- [`docs/SETUP.md`](docs/SETUP.md) — local installation and operator prerequisites.
- [`docs/CI.md`](docs/CI.md) — GitHub Actions regression-gate contract and local/CI equivalence.
- [`docs/CODEX_PERMISSIONS.md`](docs/CODEX_PERMISSIONS.md) — current model-turn permission boundary and probes.
- [`docs/GIT_HANDOFF.md`](docs/GIT_HANDOFF.md) — host-owned branch/commit/push/PR handoff contract.
- [`docs/REPORT_ONLY_COMPLETION.md`](docs/REPORT_ONLY_COMPLETION.md) — explicit no-code/report-only completion, evidence, lifecycle, and reconciliation contract.
- [`docs/PHASE2_BUDGETED_ROUTING.md`](docs/PHASE2_BUDGETED_ROUTING.md) — Phase 2 policy and benchmark.
- [`docs/PHASE3_UNITY_SCHEDULING.md`](docs/PHASE3_UNITY_SCHEDULING.md) — Phase 3 Unity resource/validation scheduling contract.
- [`docs/PHASE4_UNITY_RUNNER.md`](docs/PHASE4_UNITY_RUNNER.md) — Phase 4 host broker, Windows staging, and Unity Test Framework execution contract.
- [`docs/UNITY_STALL_RECOVERY.md`](docs/UNITY_STALL_RECOVERY.md) — progress-aware Unity stall classification and ownership-safe recovery contract.
- [`docs/DIAGNOSTICS.md`](docs/DIAGNOSTICS.md) — model-turn execution diagnostics and localhost dashboard usage.

## Regression gate

`bash tests/run.sh` is the canonical deterministic Supervisor regression gate. Developers and operators run that command locally, and GitHub Actions runs the same command for pull requests targeting `main` and pushes to `main`. The CI job is named `supervisor-tests` so it can be made a required branch-protection check later.

CI does not run live Symphony/Codex workers or Unity and does not require Supervisor account secrets. See [`docs/CI.md`](docs/CI.md) for the exact CI boundary and clean-environment expectations.

## Diagnostics quick start

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/diagnose-issue.sh 98
bash scripts/serve-diagnostics.sh
```

The dashboard is available only on `http://127.0.0.1:8765` by default and is read-only. Unity history now shows the active validation phase, last observed progress, stall recovery result, and blocked ownership state so a wedged request is distinguishable from a legitimately long test run. Report-only completion is visible through the distinct `symphony:report-complete` GitHub lifecycle label and its durable issue comment.

When host services and a real Symphony worker disagree, use the explicit diagnostics rather than broadening worker permissions. The model-turn environment probe remains available for Codex runtime investigation:

```bash
bash scripts/codex-turn-environment-probe.sh --run
```

That probe uses Luna / low by default and consumes a small amount of Codex allowance. It is intentionally excluded from normal startup and test execution.

## Safety posture

Symphony is engineering-preview software and Codex App Server workers are intentionally autonomous. The configuration therefore keeps one concurrent worker, no automatic merge, explicit human review boundaries, explicit model escalation, a four-turn worker budget, a host-side execution gate that requires an explicit one-shot rearm before a second worker lifetime, and exclusive Unity scheduling for editor validation.

Workers can edit source in their GH workspace but use typed host interfaces for privileged/fragile seams. Unity execution does not broaden production-scene authority. Git handoff validates workspace/repository/branch/history and never exposes a generic host command runner. Normal implementation stops at a reviewable PR; explicitly eligible report-only work stops at a host-verified `symphony:report-complete` issue state. Merge and issue closure remain human decisions.

Do not put GitHub tokens or other secrets in this repository. Runtime credentials belong in the operator environment or the permission-restricted operator secrets file described in [`docs/SETUP.md`](docs/SETUP.md).
