---
name: rpgk-architecture-change
description: Plan and implement RPG Kingdom architecture changes without violating system ownership, persistence contracts, or repository authority. Use for risk:architecture work.
---

# RPG Kingdom Architecture Change

Use this skill when the issue changes a system boundary, contract, lifecycle, persistence shape, or cross-system relationship. The objective is deliberate architecture work, not broad redesign.

## 1. Establish the existing contract first

Read `AGENTS.md`, `docs/ARCHITECTURE.md`, and the relevant `docs/systems/` material before editing. Identify the current owner of state, mutation, events, persistence, and presentation for the affected behavior.

Use `rpgk_graphify` when available to identify callers, dependents, and impact radius before opening unrelated source files. Confirm graph conclusions against current workspace source.

## 2. State the boundary being changed

Before implementation, be able to answer:

- which system owns the behavior after the change;
- what public query/command/event contract changes;
- what remains private;
- whether save/load compatibility changes;
- which dependent systems need migration;
- what deterministic behavior can be tested without scene presentation.

If those answers are unclear, investigate that boundary rather than coding several competing approaches.

## 3. Keep the design bounded

Prefer extending an existing owner over creating a new manager, singleton, service locator, or cross-system mutation path. Data-driven definitions should remain data-driven. Runtime rules should remain independent of scene presentation where practical.

Do not solve future hypothetical requirements unless the issue explicitly requires them. Document a follow-up instead of adding speculative extension points.

## 4. Migrate dependents through contracts

Update consumers to use the new public boundary. Do not leave compatibility shims that preserve an architecture violation unless the issue explicitly requires a staged migration.

Update event/save/system documentation when the contract changes. Keep production scene authoring outside Codex when repository instructions require a human-owned scene step.

## 5. Prove the architecture

Add deterministic tests for the new rule or contract. Add a bounded engine-facing harness only where Unity behavior cannot be proven in EditMode/unit-style tests.

Validate the changed owner first, then direct dependents, then the broader affected slice. Avoid full-suite retries as a substitute for understanding a focused failure.

## 6. Finish with an explicit impact summary

Before handoff, identify the changed boundary, dependent migrations, persistence/event implications, tests run, and any intentionally deferred migration. Do not claim architecture completion if a known caller still bypasses the new contract.
