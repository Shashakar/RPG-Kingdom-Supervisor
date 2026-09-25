# Phase 2 — Budgeted Model, Risk Routing, and Dynamic Continuation

## Why Phase 2 exists

The Phase 1 smoke path proved GitHub -> Symphony -> Codex App Server -> PR, but it also showed that a large fixed turn budget can waste allowance even on trivial work.

Smoke issue `Shashakar/RPG-Kingdom#91` was a documentation-only change that ultimately produced one commit, one changed file, and six added README lines. The successful run nevertheless reached the then-configured 20-turn ceiling and consumed approximately:

- 1,547,919 reported input tokens;
- 4,424 reported output tokens;
- 27 percentage points of the operator's five-hour Codex allowance;
- 4 percentage points of the weekly allowance.

Phase 2 therefore has two goals:

1. route work to an appropriate model/risk tier;
2. prevent routine work from buying unnecessary turns, contexts, or worker lifetimes.

The second goal is now implemented primarily through a **dynamic continuation budget**, not through small model-specific turn counts.

## Runtime design

Phase 2 keeps official Symphony as the baseline runtime and uses narrow host-side adapters:

1. `scripts/codex-app-server-router.sh`
   - derives the GitHub issue from the Symphony workspace;
   - reads only routing/capability labels;
   - selects model and reasoning effort through the host routing policy;
   - starts Codex App Server with explicit model configuration.

2. `scripts/before-run-guard.sh`
   - runs before each worker lifetime;
   - refuses accidental second lifetimes unless GitHub carries the one-shot `symphony:rearm` approval;
   - consumes that approval before Codex starts;
   - recovers only same-issue stale host resources where the relevant resource policy permits it.

3. `scripts/continuation-policy.py`
   - runs after a normal completed Codex turn;
   - refreshes authoritative Codex quota;
   - measures worker-lifetime and per-turn account quota movement when possible;
   - records cumulative/per-turn token telemetry without converting tokens into synthetic quota percentages;
   - evaluates source/Unity progress;
   - decides whether another turn is justified under the remaining hard turn ceiling.

4. `scripts/after-run-guard.sh`
   - records the completed worker-lifetime boundary;
   - reconciles successful host-owned completion paths;
   - otherwise removes the dispatch lease and surfaces `symphony:halted` for human/ChatGPT review.

The persistent attempt marker and one-shot rearm path remain important. Dynamic continuation applies **inside one worker lifetime**. It does not create an unlimited redispatch loop.

## Hard turn ceiling

`agent.max_turns` remains **4**.

Four is the current global fail-safe ceiling. A single Codex turn can perform many tool calls, edits, searches, and validation runs, so the Supervisor does not need an unbounded turn count to support substantial work.

The hard ceiling exists to protect against bugs or pathological agent behavior even if every softer budget signal misbehaves.

The hard ceiling is deliberately different from the continuation policy:

- **hard ceiling:** an absolute safety limit;
- **dynamic continuation budget:** the normal decision about whether another turn should be spent.

Model route no longer creates a smaller automatic turn ceiling such as Terra=2 or Astra=1. Luna, Sol, and Astra can all use turns up to the workflow hard maximum when the dynamic policy continues to approve them.

## Dynamic continuation budget

A completed turn earns another turn only when all applicable gates remain healthy.

### 1. Current quota reserve

The Supervisor refreshes the authoritative App Server rate-limit sample before deciding.

By default:

- Luna work requires at least 20% remaining in the primary/five-hour window;
- Sol and Astra require at least 35% remaining in the primary/five-hour window;
- all routes require at least 10% remaining weekly.

Unavailable, stale, or incomplete quota data fails safe.

These reserve thresholds are configurable but remain model-free host policy.

### 2. Observed spend budget

When the active worker's start quota and prior-turn quota are available in the same reset window, Supervisor derives actual **percentage-point movement** from authoritative remaining percentages.

Initial defaults:

| Budget | Default stop threshold |
|---|---:|
| one turn, five-hour window | > 15 percentage points |
| one turn, weekly window | > 4 percentage points |
| one worker lifetime, five-hour window | > 30 percentage points |
| one worker lifetime, weekly window | > 8 percentage points |

Environment overrides:

- `RPGK_CONTINUATION_MAX_TURN_PRIMARY_SPEND_PERCENT`
- `RPGK_CONTINUATION_MAX_TURN_WEEKLY_SPEND_PERCENT`
- `RPGK_CONTINUATION_MAX_LIFETIME_PRIMARY_SPEND_PERCENT`
- `RPGK_CONTINUATION_MAX_LIFETIME_WEEKLY_SPEND_PERCENT`

Quota is account-global. These deltas are therefore a **safety budget**, not guaranteed per-worker attribution when another Codex lifetime overlaps.

The policy never converts token counts into percentage-point quota cost.

### 3. Token fallback

If quota movement cannot be derived—for example because the rate-limit window reset between samples—the current quota reserve still remains authoritative, and fresh-token telemetry acts only as a fallback safety budget.

Fresh tokens are defined for this purpose as:

`max(0, input - cached_input) + output`

Initial fallback defaults:

- per-turn fresh-token ceiling: 750,000;
- worker-lifetime fresh-token ceiling: 2,000,000.

Environment overrides:

- `RPGK_CONTINUATION_MAX_TURN_FRESH_TOKENS`
- `RPGK_CONTINUATION_MAX_LIFETIME_FRESH_TOKENS`

These token values do not pretend to estimate Codex allowance percentage. They simply prevent an obviously pathological turn from receiving more unattended work when quota deltas cannot be compared safely.

### 4. Progress budget

For implementation/repair workers, Supervisor treats source-workspace or Unity-run changes as host-observable progress.

The following rules apply:

- source diff/HEAD change => progress;
- new Unity run => progress, subject to the repeated-failure rule below;
- the same focused Unity validation failing again with no source change => stop;
- one host-invisible analysis turn is allowed as a bounded grace turn;
- a second consecutive host-invisible turn with no source or Unity progress => stop;
- any later observable progress resets the invisible-turn count.

The analysis grace exists because investigation is real work even when it consists of reading logs/source and deciding the next repair. It is intentionally limited to one consecutive turn so an agent cannot spend the full allowance merely thinking without producing evidence.

Report-only tasks keep their separate source-clean progress semantics: report synthesis may be host-invisible, but the task must remain source-clean and is still bounded by quota/spend and the hard turn ceiling.

## Why fixed route caps were removed

The first version of continuation policy used route-specific automatic limits:

- Luna: 4;
- Terra: 2;
- Sol: 2;
- Astra: 1.

That was a conservative proxy for cost before enough production telemetry existed.

RPG Kingdom #111 exposed the downside. A legitimate Terra investigation reached turn 2 twice with healthy quota, preserved useful changes, and incomplete validation. The fixed cap forced a reviewed rearm and a new worker lifetime even though the account still had budget and the task was not complete. That repeated context and cost rather than controlling it.

The policy now measures the thing we actually care about:

- remaining allowance;
- observed spend;
- progress;
- repeated ineffective behavior;
- hard maximum autonomy.

Route still matters for model selection and minimum reserve thresholds, but **turn number alone is no longer the primary cost proxy**.

## Routing labels

### Risk classification

Use exactly zero or one of:

| Label | Default route | Default effort | Intended work |
|---|---|---|---|
| `risk:mechanical` | GPT-6 Luna | low | docs, file moves, renames, narrowly specified repetitive changes |
| `risk:normal` | GPT-6 Luna | medium | normal bounded implementation, straightforward fixes, focused refactors |
| `risk:investigative` | GPT-6 Sol | medium | ambiguous debugging, multiple plausible root causes, multi-layer investigation |
| `risk:architecture` | GPT-6 Sol | high | architecture-sensitive or cross-system boundary work |
| `risk:end-to-end` | GPT-6 Sol | high | substantial end-to-end integration/tool work; escalate to Astra only from explicit evidence |

If no risk or model label exists, the router defaults to Luna / medium.

The current practical rule is: **GPT-6 Luna is the workhorse; GPT-6 Sol covers investigative/debugging work at medium effort and architecture/end-to-end work at high effort; GPT-6 Astra is an explicit escalation tier, not the automatic consequence of end-to-end breadth.** Use `model:astra` when prior Sol work or concrete task evidence shows the stronger route is justified, or accept a fresh `repair-route:astra` recommendation during reviewed rework.

### Explicit model override

Use exactly zero or one of:

- `model:luna`
- `model:sol`
- `model:astra`

An explicit model label wins over risk classification.

### Reasoning override

Use exactly zero or one of:

- `effort:low`
- `effort:medium`
- `effort:high`

The router fails closed if multiple model, risk, or effort labels conflict.

## Real routing measurements

### #93 — Luna mechanical benchmark

`Shashakar/RPG-Kingdom#93` ran as Luna / low and completed in one turn.

Codex session totals:

- 359,460 input tokens;
- 323,072 cached input tokens;
- 36,388 uncached input tokens;
- 3,411 output tokens;
- about 89.9% of input cached;
- no visible movement in either allowance meter.

This validated Luna as the cheap mechanical lane.

### Historical #95 — GPT-5.6 Terra investigative benchmark

`Shashakar/RPG-Kingdom#95` ran as Terra / medium and completed in one turn while correctly diagnosing a stale test assumption.

Codex session totals:

- 1,758,565 input tokens;
- 1,641,728 cached input tokens;
- 116,837 uncached input tokens;
- 10,021 output tokens;
- about 93.4% of input cached;
- five-hour allowance usage increased by 8 percentage points;
- weekly allowance usage increased by 2 percentage points.

The large difference from Luna justified deliberate model routing, but it does **not** justify stopping every Terra worker after exactly two turns. Dynamic continuation uses the live account budget instead.

## Context budget

Model choice and turn policy are only part of efficiency. Worker prompts still scale repository exploration to the risk class while obeying RPG Kingdom's authoritative repository instructions.

- Mechanical: mandatory reads plus directly affected files.
- Normal: mandatory architecture/system docs plus affected implementation/tests.
- Investigative: enough runtime/test/system context to distinguish plausible root causes, stopping when evidence selects the boundary.
- Architecture: affected contracts and only cross-system/save/event docs actually touched.
- End-to-end: enough cross-system/tool context to validate the whole task without unrelated repository sweeps.

The Supervisor never overrides a repository-required read merely to save tokens.

## Fail-closed redispatch

`symphony:ready` remains the GitHub dispatch lease, but it is not the only execution gate.

At the end of every worker lifetime, the workspace receives a persistent attempt marker. A later worker lifetime is rejected before Codex starts unless a human/ChatGPT review explicitly supplies one-shot `symphony:rearm` approval.

Use:

```bash
bash scripts/rearm-issue.sh <issue-number>
```

The helper requests `symphony:rearm` before `symphony:ready`. The host consumes the rearm approval during preflight. Re-adding `symphony:ready` alone is not a continuation approval.

Dynamic continuation does not weaken this rule. It is intended to let one healthy worker lifetime finish efficiently, thereby reducing the need for repeated rearm lifetimes.

## Regression-preservation validation

A bounded fix is not complete merely because the original failing assertion becomes green. When implementation changes behavior-bearing configuration or wiring, the worker must identify the existing behavior that configuration provided and gather focused preservation evidence for it.

Examples include Animator/controller replacement, prefab or scene references, serialized asset links, input bindings, and other configuration where satisfying one contract can silently displace another.

This requirement remains focused; it does not justify repository-wide validation unrelated to the changed boundary.

## Current acceptance status

The Supervisor now has real evidence for both sides of continuation control:

- #91 demonstrated that large fixed turn budgets can waste allowance;
- #93 showed cheap Luna work can complete efficiently;
- #95 measured the materially higher cost of Terra;
- #110 demonstrated why report-only progress needs source-clean semantics;
- #111 demonstrated why small static Terra turn caps can force repeated lifetimes and waste context.

The resulting policy is therefore:

1. route to the cheapest model appropriate for the task;
2. retain a hard four-turn safety ceiling;
3. decide each additional turn dynamically from current quota, observed spend, and progress;
4. stop repeated ineffective behavior early;
5. require explicit human/ChatGPT rearm only when a bounded worker lifetime still cannot complete.

### #139 — Astra end-to-end cost benchmark

`Shashakar/RPG-Kingdom#139` provided a production benchmark for the previous automatic Astra end-to-end route. After the initial discovery lifetime, a reviewed continuation began with a freshly reset five-hour allowance. That continuation consumed approximately 97 percentage points of the five-hour allowance and roughly 20–25 percentage points of the weekly allowance while producing a technically sound 16-file integration PR (596 additions / 27 deletions) with extensive Unity validation.

Independent automated review approved the implementation, but immediate human playtesting still found basic player-facing acquisition/readability gaps: the weapon grant was not visibly communicated in normal play and the equip gesture needed adjustment. The result was useful, but there was no observed capability unique to Astra that justified making that cost the default for every `risk:end-to-end` issue.

Accordingly, end-to-end breadth now defaults to **Sol / high**. Astra remains available through explicit `model:astra` override or a fresh reviewed `repair-route:astra` escalation. A task should escalate because there is evidence that Sol is insufficient—not merely because the task crosses multiple systems or uses Unity tooling.

Continue measuring real production work and tune the spend thresholds from observed outcomes rather than introducing larger unconditional turn counts or automatic high-cost routing.
