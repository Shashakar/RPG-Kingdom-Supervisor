# Phase 2 — Budgeted Model and Risk Routing

## Why Phase 2 exists

The Phase 1 smoke test proved the full GitHub -> Symphony -> Codex App Server -> PR path, but it also exposed an unacceptable default execution cost.

Smoke issue `Shashakar/RPG-Kingdom#91` was a documentation-only change that ultimately produced one commit, one changed file, and six added README lines. The successful Symphony run nevertheless reached the configured 20-turn ceiling, was eligible for a fresh worker session because the issue still had `symphony:ready`, and consumed approximately:

- 1,547,919 reported input tokens;
- 4,424 reported output tokens;
- 27 percentage points of the operator's five-hour Codex allowance;
- 4 percentage points of the weekly allowance.

Those numbers are the Phase 2 baseline. The purpose of this phase is not merely to choose cheaper models; it is to prevent routine work from buying unnecessary turns, contexts, and fresh worker lifetimes.

## Runtime design

Phase 2 keeps official Symphony unmodified and uses narrow host-side adapters at its documented command/hook seams:

1. `scripts/codex-app-server-router.sh`
   - derives the GitHub issue number from the Symphony workspace name;
   - reads only the issue labels needed for routing;
   - selects model and reasoning effort through `scripts/routing-policy.sh`;
   - execs `codex ... app-server` with explicit model configuration.

2. `scripts/before-run-guard.sh`
   - runs before each Symphony worker lifetime;
   - refuses to launch Codex when the workspace already contains `.symphony-attempt-complete`;
   - gives Phase 2 a local hard execution gate even if a GitHub label mutation is temporarily unavailable.

3. `scripts/after-run-guard.sh`
   - writes `.symphony-attempt-complete` before any network-dependent cleanup;
   - does no tracker mutation when the worker already removed `symphony:ready` successfully;
   - otherwise removes `symphony:ready`, adds `symphony:halted`, and comments on the issue;
   - prevents the Phase 1 failure mode where reaching `agent.max_turns` caused a brand-new Codex thread to be dispatched automatically.

The local marker is deliberately persistent because Symphony preserves per-issue workspaces. A later retry must therefore be an explicit operator action rather than merely re-adding a GitHub label.

## Default turn budget

`agent.max_turns` is reduced from 20 to **4**.

Upstream Symphony exposes this as a workflow-level value rather than a per-issue value. Four turns is therefore a deliberately conservative global ceiling for Phase 2. A single Codex turn can perform many tool calls and is expected to make substantial repository progress.

If four turns are insufficient, the completed-attempt marker prevents another Codex worker lifetime. Human/ChatGPT review decides whether another bounded run is justified.

This is intentionally biased toward preserving allowance rather than maximizing unattended completion at any cost.

## Routing labels

### Risk classification

Use exactly zero or one of:

| Label | Default route | Default effort | Intended work |
|---|---|---|---|
| `risk:mechanical` | GPT-5.6 Luna | low | docs, file moves, renames, narrowly specified repetitive changes |
| `risk:normal` | GPT-5.6 Luna | medium | normal bounded implementation, straightforward fixes, focused refactors |
| `risk:investigative` | GPT-5.6 Terra | medium | ambiguous debugging, multiple plausible root causes, multi-layer investigation, substantial implementation where Luna is likely to waste iterations |
| `risk:architecture` | GPT-5.6 Sol | high | architecture-sensitive or cross-system boundary work |
| `risk:end-to-end` | GPT-6 Astra | medium | hardest end-to-end work where stronger execution/tool use is expected to reduce iteration |

If no risk or model label exists, the router defaults to **Luna / medium**. Unclassified work never silently promotes itself to Terra, Sol, or Astra.

The practical rule is: **Luna is the workhorse; Terra is the investigative/debugging upgrade; Sol is the architecture tier; Astra is the hardest engine/tool-heavy end-to-end tier.**

### Explicit model override

Use exactly zero or one of:

- `model:luna`
- `model:terra`
- `model:sol`
- `model:astra`

An explicit model label wins over risk classification. This is useful when ChatGPT/human review determines that a particular issue is unusually easy or difficult for its nominal risk class.

### Reasoning override

Use exactly zero or one of:

- `effort:low`
- `effort:medium`
- `effort:high`

The router fails closed if multiple model, risk, or effort labels conflict.

## Post-benchmark routing adjustment

Phase 2 produced two useful real measurements after the #91 baseline.

### #93 — Luna mechanical benchmark

`Shashakar/RPG-Kingdom#93` ran as `risk:mechanical` -> Luna / low and completed in one Symphony turn and one worker lifetime.

Codex session totals:

- 359,460 input tokens;
- 323,072 cached input tokens;
- 36,388 uncached input tokens;
- 3,411 output tokens;
- about 89.9% of input cached;
- no visible movement in either the five-hour or weekly allowance meter.

This validated Luna as a very cheap mechanical lane.

### #95 — Terra investigative benchmark

`Shashakar/RPG-Kingdom#95` ran as Terra / medium and also completed in one Symphony turn and one worker lifetime. It correctly diagnosed a stale production-test assumption rather than changing the production scene/runtime to satisfy the test.

Codex session totals:

- 1,758,565 input tokens;
- 1,641,728 cached input tokens;
- 116,837 uncached input tokens;
- 10,021 output tokens;
- about 93.4% of input cached;
- five-hour allowance usage increased by 8 percentage points;
- weekly allowance usage increased by 2 percentage points.

The cache was already working well. The material allowance difference therefore justified making Terra an explicit investigative tier instead of the default for every normal C# task.

## Astra policy

Astra is part of the routing pool, but it is not the default senior model.

Use Astra when the task is genuinely difficult end-to-end work: broad implementation plus verification, complex tool use, difficult engine-facing debugging, or work where fewer iterations are likely to offset Astra's higher allowance consumption. It is especially relevant once the later Unity worker integration can let the model observe and validate engine behavior directly.

Use Sol for architecture-sensitive work that primarily needs strong reasoning over code/contracts but does not need the full end-to-end Astra profile.

Astra may also be selected explicitly with `model:astra` when the human/ChatGPT planner has a concrete reason.

## Context budget

Model choice is only one part of efficiency. Worker prompts are also instructed to scale repository exploration to the risk class while still obeying RPG Kingdom's own `AGENTS.md` requirements.

- Mechanical: repository-mandated reads plus directly affected files; no unrelated system inventory.
- Normal: repository-mandated architecture/system docs plus affected implementation/tests, with a focused implementation path rather than broad debugging.
- Investigative: enough affected runtime/test/system context to distinguish plausible root causes, stopping when evidence selects the correct boundary.
- Architecture: affected system contracts and only the cross-system/save/event docs that the boundary actually touches.
- End-to-end: enough cross-system/tool context to validate the whole task, without unrelated repository sweeps.

The supervisor never overrides a read required by RPG Kingdom's authoritative repository instructions merely to save tokens.

## Fail-closed redispatch

`symphony:ready` remains the GitHub dispatch lease, but it is no longer the only execution gate.

At the end of every worker lifetime, the workspace receives `.symphony-attempt-complete` before the guard makes any GitHub request. A subsequent Symphony worker lifetime reaches `before_run` and is rejected before Codex App Server starts. This remains true even if GitHub is temporarily unavailable when the first attempt ends.

When the GitHub API is available, the after-run guard also removes `symphony:ready` first and then adds `symphony:halted` for operator visibility.

To approve a retry, use the checked-in helper:

```bash
bash scripts/rearm-issue.sh <issue-number>
```

The helper removes `symphony:halted` when present, deletes the local completed-attempt marker, and re-adds `symphony:ready`. Adjust risk/model/effort labels before rearming when the prior route was inappropriate.

Do not build an automatic retry loop around `symphony:halted` or the rearm helper.

## Phase 2 acceptance status

The routing/execution-budget core is validated by #93 and #95:

- trivial mechanical work can complete in one Luna turn with negligible visible allowance movement;
- genuine investigation can complete in one Terra turn without redispatch;
- the worker-lifetime guard prevents the Phase 1 runaway-session failure mode;
- model cost differences are large enough that higher tiers must remain deliberate rather than default.

Continue to measure real tasks rather than spending allowance on synthetic Sol/Astra benchmarks. Use the next genuine architecture and end-to-end tasks to validate those lanes when they naturally occur.
