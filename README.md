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
- unclassified work defaults to Terra rather than silently using the most expensive model;
- model and reasoning labels allow explicit human/ChatGPT overrides;
- worker turns are capped at four for the current phase;
- a persistent workspace marker prevents a second Codex worker lifetime from starting accidentally;
- the host also removes `symphony:ready` and adds `symphony:halted` when an attempt ends without a clean PR handoff;
- worker prompts scale context gathering to task risk while still obeying RPG Kingdom's repository-mandated reads.

See [`docs/PHASE2_BUDGETED_ROUTING.md`](docs/PHASE2_BUDGETED_ROUTING.md) for the routing table, the #91 baseline, and the Phase 2 benchmark plan.

## Repositories

- Game repository: `Shashakar/RPG-Kingdom`
- Supervisor repository: `Shashakar/RPG-Kingdom-Supervisor`
- Symphony upstream: `openai/symphony`

The currently evaluated upstream revision is recorded in [`SYMPHONY_UPSTREAM.md`](SYMPHONY_UPSTREAM.md).

## Files

- [`WORKFLOW.md`](WORKFLOW.md) — Symphony configuration and the RPG Kingdom worker prompt.
- [`scripts/routing-policy.sh`](scripts/routing-policy.sh) — deterministic label-to-model/effort policy.
- [`scripts/codex-app-server-router.sh`](scripts/codex-app-server-router.sh) — per-issue Codex App Server launcher.
- [`scripts/before-run-guard.sh`](scripts/before-run-guard.sh) — blocks accidental second worker lifetimes before Codex starts.
- [`scripts/after-run-guard.sh`](scripts/after-run-guard.sh) — records the local execution boundary and performs tracker cleanup/halt handoff.
- [`scripts/install-labels.sh`](scripts/install-labels.sh) — creates/updates Phase 2 GitHub labels.
- [`scripts/rearm-issue.sh`](scripts/rearm-issue.sh) — explicitly clears the local/remote halt gates for one approved retry.
- [`AGENTS.md`](AGENTS.md) — rules for modifying this supervisor repository.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — supervisor boundaries and phased design.
- [`docs/SETUP.md`](docs/SETUP.md) — local installation and operator prerequisites.
- [`docs/PHASE2_BUDGETED_ROUTING.md`](docs/PHASE2_BUDGETED_ROUTING.md) — Phase 2 policy and benchmark.

## Safety posture

Symphony is engineering-preview software and Codex App Server workers are intentionally autonomous. The configuration therefore keeps one concurrent worker, no automatic merge, a required PR review boundary, explicit model escalation, a four-turn worker budget, and a host-side execution gate that requires explicit rearm before a second worker lifetime.

Do not put GitHub tokens or other secrets in this repository. Runtime credentials belong in the operator environment.
