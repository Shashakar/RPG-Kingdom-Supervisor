# RPG Kingdom Supervisor

RPG Kingdom Supervisor is the orchestration layer for autonomous Codex implementation work in [`Shashakar/RPG-Kingdom`](https://github.com/Shashakar/RPG-Kingdom).

The supervisor deliberately does **not** live inside the Unity project. RPG Kingdom remains the source of truth for game architecture and repository instructions; this repository owns only orchestration policy and operator setup.

## Current target

The supervisor is built around the official [`openai/symphony`](https://github.com/openai/symphony) reference implementation and Codex App Server.

Phase 1 establishes a thin, reviewable wrapper around Symphony:

- GitHub Issues are the work queue.
- `symphony:ready` is the dispatch label.
- each issue receives an isolated Symphony workspace containing a clone of RPG Kingdom.
- Codex runs through App Server inside that workspace.
- RPG Kingdom's own `AGENTS.md` and documentation remain authoritative for implementation behavior.
- Codex opens a pull request; merge remains a human decision.
- model/risk routing and Unity-aware scheduling are intentionally deferred to later phases.

## Repositories

- Game repository: `Shashakar/RPG-Kingdom`
- Supervisor repository: `Shashakar/RPG-Kingdom-Supervisor`
- Symphony upstream: `openai/symphony`

The currently evaluated upstream revision is recorded in [`SYMPHONY_UPSTREAM.md`](SYMPHONY_UPSTREAM.md).

## Files

- [`WORKFLOW.md`](WORKFLOW.md) — Symphony configuration and the RPG Kingdom worker prompt.
- [`AGENTS.md`](AGENTS.md) — rules for modifying this supervisor repository.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — supervisor boundaries and phased design.
- [`docs/SETUP.md`](docs/SETUP.md) — local installation and smoke-test prerequisites.

## Safety posture

Symphony is engineering-preview software and Codex App Server workers are intentionally autonomous. The initial configuration therefore starts with one concurrent worker, no automatic merge, and a required PR review boundary.

Do not put GitHub tokens or other secrets in this repository. Runtime credentials belong in the operator environment.
