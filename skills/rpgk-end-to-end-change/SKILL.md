---
name: rpgk-end-to-end-change
description: Execute RPG Kingdom end-to-end changes across multiple bounded systems while preserving each system's ownership and validating the complete player-facing path. Use for risk:end-to-end work.
---

# RPG Kingdom End-to-End Change

Use this skill when the issue intentionally spans multiple systems or requires proving a complete gameplay path. The goal is coordinated integration without collapsing bounded systems into one implementation.

## 1. Map the path before editing

Read `AGENTS.md`, `docs/ARCHITECTURE.md`, and the relevant system documents. Identify the ordered path from input/data definition through runtime owners to presentation, persistence, and validation.

Use `rpgk_graphify` when available for dependency and impact mapping. Use `rpgk_context7` only when an external package/framework API is genuinely part of the change; repository code and docs remain the source of truth for RPG Kingdom behavior.

## 2. Keep each system responsible for its own state

For every boundary crossed, use the owning system's public query/command/event contract. Do not create a shortcut that lets one system mutate another system's private state merely because the feature is end-to-end.

If a missing contract is discovered, make that contract explicit and testable rather than coupling the systems directly.

## 3. Implement in dependency order

Prefer this order where applicable:

1. data/contracts;
2. deterministic runtime rules;
3. cross-system integration;
4. persistence/events;
5. presentation adapters;
6. host/test harnesses.

Do not author production scenes when repository instructions reserve scene authoring for a human. Provide scripts, assets, harnesses, and precise scene wiring instructions instead.

## 4. Validate progressively

Prove each owning system independently before relying on an end-to-end test. Then validate the integration seam and finally the complete player-facing path.

A broad E2E failure should be narrowed to the owning boundary before making another speculative change. Do not use repeated full-suite runs as the debugging loop.

## 5. Keep scope explicit

An end-to-end issue is not permission to clean up every system it touches. Defer unrelated debt. Preserve existing public contracts unless the issue requires changing them, and document any intentional compatibility implications.

## 6. Handoff with the whole path accounted for

Before completion, summarize which systems changed, which contracts/events/save data changed, which focused and end-to-end validations passed, and any remaining human-authored scene or asset step. The feature is not complete if a required boundary is only assumed to work.
