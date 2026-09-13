---
name: rpgk-mechanical-change
description: Execute low-risk RPG Kingdom mechanical changes with minimal exploration and no opportunistic scope growth. Use for risk:mechanical work.
---

# RPG Kingdom Mechanical Change

Use this skill for bounded, low-risk changes where the intended behavior and owning area are already clear. The goal is to capture the useful minimalism we wanted from Ponytail without introducing a second workflow owner.

## 1. Confirm the requested boundary

Read `AGENTS.md` and only the system documentation required for the files you will touch. Do not perform broad architecture discovery unless the requested change reveals evidence that its risk classification is wrong.

## 2. Prefer the smallest complete change

Touch the fewest files necessary to satisfy the issue. Avoid opportunistic refactors, renames, formatting passes, abstraction work, dependency upgrades, or adjacent cleanup unless they are required for correctness.

Do not optimize for fewer lines at the expense of clarity, tests, or repository contracts. Smallest **complete** change is the target, not smallest diff at any cost.

## 3. Preserve system ownership

Do not reach through another RPG Kingdom system's private state or serialized fields to make a mechanical change easier. Use its documented query/event/command boundary.

If the requested change actually requires a cross-system contract redesign, stop treating it as mechanical work and leave precise evidence for reclassification rather than smuggling architecture work through a cheap route.

## 4. Validate exactly what changed

Run the narrowest deterministic test or host validation that proves the requested behavior. Broaden only when the touched boundary warrants it.

Do not repeatedly rerun an unchanged failure. A failure outside the intended scope should be recorded clearly rather than expanded into unrelated repair work.

## 5. Stop when acceptance is satisfied

Once the requested behavior is implemented, the relevant test is green, and required handoff/docs are complete, stop exploring. Do not spend the remaining worker turn budget looking for additional improvements.
