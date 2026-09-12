---
name: rpgk-investigate-bug
description: Investigate ambiguous RPG Kingdom regressions with bounded context. Use for risk:investigative work to reproduce narrowly, use repository graph evidence when available, identify the owning boundary, and stop broad exploration once evidence selects a root cause.
---

# RPG Kingdom Investigative Debugging

Use this skill for ambiguous regressions and failing-test clusters. The goal is to reduce repeated repository discovery without weakening RPG Kingdom's checked-in architecture or test contracts.

## 1. Start from the reported symptom

Read `AGENTS.md` and only the repository documents it requires for the affected systems. Reproduce the smallest relevant failing test or runtime path before opening a broad set of implementation files.

Treat historical failure inventories as leads, not current truth. If a prior failure no longer reproduces, record that rather than manufacturing a change.

## 2. Prefer graph queries before broad source traversal

If the `rpgk_graphify` MCP tools are available, use them for the first structural pass instead of repeated grep/full-file exploration. Good questions include:

- which symbols own the failing behavior;
- callers/callees of the failing entry point;
- shortest path between the test-facing API and the suspected runtime owner;
- impact radius of changing a candidate symbol;
- which files matter most to the reported symptom.

Graphify is read-only evidence, not authority. Its graph is a snapshot of the canonical repository and may not include this worker's uncommitted changes. Confirm any decision against current workspace source before editing.

If Graphify is unavailable, do not spend time trying to install or repair it from the worker. Fall back to targeted repository search.

## 3. Select the boundary, then stop exploring

Once evidence distinguishes the plausible root causes, choose the smallest owning boundary and stop opening unrelated systems. Explicitly decide whether the failure is:

- a production regression;
- stale or incomplete fixture/setup;
- state/lifecycle leakage;
- authored configuration drift;
- infrastructure failure;
- or a genuinely separate defect that deserves a follow-up issue.

Do not keep auditing adjacent systems after the owning boundary is established merely because more context is available.

## 4. Implement the smallest coherent fix

Preserve system ownership. Query another system's state or publish through its contract; do not reach into another system's private state to make a test green.

Change tests only when current contracts/evidence prove the prior fixture or expectation is obsolete. Do not weaken assertions because they are inconvenient.

## 5. Validate from narrow to broad

Run the exact failing test first. If green, run the owning fixture/system. Broaden only across boundaries actually changed by the fix.

A repeated failure with no source change is evidence to reassess, not a reason to rerun the same test indefinitely.

## 6. Finish or preserve a precise continuation point

If the bounded fix is complete, perform the normal host Git handoff. If host policy stops the lifetime before completion, leave the workspace in a state where the next reviewed continuation can begin from the current diff and latest validation result rather than repeating this investigation.
