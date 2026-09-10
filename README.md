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
- Codex opens a pull request; merge remains a human decision.

Phase 2 makes that path usage-aware:

- risk/model labels route work among GPT-5.6 Luna, Terra, Sol, and GPT-6 Astra;
- Luna / medium is the default for normal and unclassified bounded work;
- `risk:investigative` promotes ambiguous debugging and multi-layer investigation to Terra / medium;
- Sol remains the architecture-sensitive tier and Astra remains the hardest end-to-end tier;
- model and reasoning labels allow explicit human/ChatGPT overrides;
- worker turns are capped at four for the current phase;
- a persistent workspace marker prevents a second Codex worker lifetime from starting accidentally;
- the host also removes `symphony:ready` and adds `symphony:halted` when an attempt ends without a clean PR handoff;
- worker prompts scale context gathering to task risk while still obeying RPG Kingdom's repository-mandated reads.

The routing policy was adjusted after real #93/#95 benchmarks showed that Luna mechanical work had negligible visible allowance impact while one successful Terra investigative turn consumed 8 percentage points of the five-hour allowance. See [`docs/PHASE2_BUDGETED_ROUTING.md`](docs/PHASE2_BUDGETED_ROUTING.md) for the measurements and routing table.

Phase 3 makes Unity dependence explicit:

- `resource:unity-editor` declares exclusive host-owned Unity Editor access;
- `validation:unity-required` blocks before Codex if the Unity resource/runner is unavailable;
- `validation:unity-optional` permits implementation while requiring missing Unity validation to be reported in the PR;
- conflicting Unity validation labels fail closed;
- a host-side lock prevents multiple future workers from sharing the editor.

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
- zero matched tests are reported as `NoTestsMatched`, not as a successful green run;
- test execution is refused unless the current issue owns the Phase 3 Unity lock.

See [`docs/PHASE4_UNITY_RUNNER.md`](docs/PHASE4_UNITY_RUNNER.md) for the runner contract.

Current diagnostics work addresses a boundary exposed by GH-97: model-free App Server probes can succeed while the actual model-backed turn still sees `.git` as read-only or cannot use WSL-to-Windows interop. The Supervisor provides a read-only issue dashboard plus an explicit, low-cost model-turn environment probe. The Unity broker also emits host-owned structured status so the dashboard can report the current/last Unity operation without scraping terminal output. See [`docs/DIAGNOSTICS.md`](docs/DIAGNOSTICS.md).

## Repositories

- Game repository: `Shashakar/RPG-Kingdom`
- Supervisor repository: `Shashakar/RPG-Kingdom-Supervisor`
- Symphony upstream: `openai/symphony`

The currently evaluated upstream revision is recorded in [`SYMPHONY_UPSTREAM.md`](SYMPHONY_UPSTREAM.md).

## Files

- [`WORKFLOW.md`](WORKFLOW.md) — Symphony configuration and the RPG Kingdom worker prompt.
- [`scripts/run-symphony.sh`](scripts/run-symphony.sh) — operator launcher that loads the scoped tracker secret, starts/reuses the Unity host broker, and uses an alternate terminal screen when available.
- [`scripts/routing-policy.sh`](scripts/routing-policy.sh) — deterministic label-to-model/effort policy.
- [`scripts/codex-app-server-router.sh`](scripts/codex-app-server-router.sh) — per-issue Codex App Server launcher.
- [`scripts/before-run-guard.sh`](scripts/before-run-guard.sh) — blocks accidental second worker lifetimes before Codex starts.
- [`scripts/unity-resource-policy.sh`](scripts/unity-resource-policy.sh) — side-effect-free Unity resource/validation policy.
- [`scripts/unity-resource-guard.sh`](scripts/unity-resource-guard.sh) — host preflight that validates Unity policy/readiness and acquires the exclusive editor lock.
- [`scripts/unity-runner-policy.sh`](scripts/unity-runner-policy.sh) — project-version and test-platform helpers for the Windows runner.
- [`scripts/unity-runner.sh`](scripts/unity-runner.sh) — worker-facing broker client for Unity health/EditMode/PlayMode validation.
- [`scripts/unity-host-broker.py`](scripts/unity-host-broker.py) — host-owned typed Unity request broker and structured status producer.
- [`scripts/unity-runner-host.sh`](scripts/unity-runner-host.sh) — host-only direct WSL/Windows adapter used by the broker.
- [`scripts/windows/run-unity-tests.ps1`](scripts/windows/run-unity-tests.ps1) — Windows staging, Unity Test Framework execution, result parsing, and artifact return bridge.
- [`scripts/release-unity-resource.sh`](scripts/release-unity-resource.sh) — ownership-checked Unity lock release hook.
- [`scripts/after-run-guard.sh`](scripts/after-run-guard.sh) — records the local execution boundary and performs tracker cleanup/halt handoff.
- [`scripts/install-labels.sh`](scripts/install-labels.sh) — creates/updates routing and Unity scheduling labels.
- [`scripts/rearm-issue.sh`](scripts/rearm-issue.sh) — explicitly clears the local/remote halt gates for one approved retry.
- [`scripts/diagnose-issue.sh`](scripts/diagnose-issue.sh) — read-only terminal summary for one dispatched issue.
- [`scripts/serve-diagnostics.sh`](scripts/serve-diagnostics.sh) — localhost-only, read-only issue diagnostics dashboard.
- [`scripts/codex-turn-environment-probe.sh`](scripts/codex-turn-environment-probe.sh) — explicit model-backed probe for `.git` writes and WSL-to-Windows interop; never run automatically because it consumes allowance.
- [`AGENTS.md`](AGENTS.md) — rules for modifying this supervisor repository.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — supervisor boundaries and phased design.
- [`docs/SETUP.md`](docs/SETUP.md) — local installation and operator prerequisites.
- [`docs/PHASE2_BUDGETED_ROUTING.md`](docs/PHASE2_BUDGETED_ROUTING.md) — Phase 2 policy and benchmark.
- [`docs/PHASE3_UNITY_SCHEDULING.md`](docs/PHASE3_UNITY_SCHEDULING.md) — Phase 3 Unity resource/validation scheduling contract.
- [`docs/PHASE4_UNITY_RUNNER.md`](docs/PHASE4_UNITY_RUNNER.md) — Phase 4 host broker, Windows staging, and Unity Test Framework execution contract.
- [`docs/DIAGNOSTICS.md`](docs/DIAGNOSTICS.md) — model-turn execution diagnostics and localhost dashboard usage.

## Diagnostics quick start

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/diagnose-issue.sh 97
bash scripts/serve-diagnostics.sh
```

The dashboard is available only on `http://127.0.0.1:8765` by default and is read-only.

When the model-free probes and a real Symphony worker disagree, run the explicit model-turn probe once before spending another full worker lifetime:

```bash
bash scripts/codex-turn-environment-probe.sh --run
```

That probe uses Luna / low by default and consumes a small amount of Codex allowance. It is intentionally excluded from normal startup and test execution.

## Safety posture

Symphony is engineering-preview software and Codex App Server workers are intentionally autonomous. The configuration therefore keeps one concurrent worker, no automatic merge, a required PR review boundary, explicit model escalation, a four-turn worker budget, a host-side execution gate that requires explicit rearm before a second worker lifetime, and exclusive Unity scheduling for editor validation.

Unity execution does not broaden production-scene authority. Workers that own the editor resource must use the supported runner rather than ad-hoc Windows/Unity commands. The worker-facing runner accepts only typed Unity operations; the host broker owns Windows interop and revalidates resource ownership before executing a test. Unity validation artifacts remain untracked evidence.

Do not put GitHub tokens or other secrets in this repository. Runtime credentials belong in the operator environment or the permission-restricted operator secrets file described in [`docs/SETUP.md`](docs/SETUP.md).
