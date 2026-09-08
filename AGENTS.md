# RPG Kingdom Supervisor Instructions

These instructions apply to changes in this repository.

## Purpose

This repository owns orchestration for autonomous Codex work against `Shashakar/RPG-Kingdom`. It does not own game architecture, Unity gameplay code, or RPG Kingdom system design.

## Boundaries

- Treat `Shashakar/RPG-Kingdom` as the authoritative source for game architecture and implementation rules.
- Do not duplicate large portions of RPG Kingdom's `AGENTS.md` or system documentation here. The worker clone must read those files directly.
- Keep the supervisor thin. Prefer configuration and narrow adapters over a forked orchestration framework when upstream Symphony already provides the needed capability.
- Do not add automatic merge behavior unless explicitly approved.
- Do not grant workers broader credentials than required.
- Keep tracker credentials host-side. Do not commit tokens or intentionally inject tracker secrets into Codex child environments.
- New orchestration behavior must be testable without running the Unity production scene.
- Treat Codex allowance as a bounded execution budget. Do not optimize for unattended completion by silently adding turns, sessions, concurrency, or higher-cost models.

## Upstream policy

The baseline runtime is the official `openai/symphony` reference implementation using Codex App Server.

- Pin evaluated upstream revisions in `SYMPHONY_UPSTREAM.md`.
- Review upstream changes before changing the pin.
- Prefer configuration-only adoption first.
- Add local code only when a required RPG Kingdom behavior cannot be expressed through the upstream workflow/configuration contract.
- Prefer narrow command/hook adapters before patching or forking Symphony.
- If a local patch to Symphony becomes necessary, document why the upstream seam is insufficient before implementing it.

## Phase discipline

The planned sequence is:

1. GitHub issue -> isolated workspace -> Codex App Server -> PR smoke path.
2. Budgeted model/risk routing across Luna, Terra, Sol, and Astra with explicit overrides and fail-closed redispatch.
3. Unity-aware resource scheduling.
4. Unity worker integration.
5. Carefully increased concurrency.
6. PR feedback/rework automation.

Do not pull later-phase complexity into an earlier phase just because it is convenient.

## Git workflow

- Make changes on a dedicated branch.
- Prefer the `codex/` prefix for Codex-authored changes.
- Open a pull request for review.
- Do not merge automatically.

## Documentation

When behavior or operator requirements change, update the relevant document under `docs/` and keep `README.md` accurate.
