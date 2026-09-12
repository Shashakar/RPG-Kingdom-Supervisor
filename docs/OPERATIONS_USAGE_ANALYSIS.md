# Supervisor comparative usage analysis

This document defines the #46D comparative usage/efficiency slice built on the durable telemetry from #46A, lifecycle/activity correlation from #46B, and retention/detail work from #46C.

## Purpose

`scripts/supervisor_usage_analysis.py` turns retained worker-lifetime records into simple, auditable comparisons that can answer where Codex capacity is being spent without inventing a cost model that Codex does not expose.

The analysis is read-only. It does not route workers, mutate GitHub lifecycle, rearm issues, change concurrency, alter retention, or merge work.

## Authoritative inputs

The analysis consumes completed Supervisor worker records. Depending on what Codex/App Server exposed for a lifetime, those records can contain:

- issue and worker role (`implementation`, `review`, `repair`, `report-only`);
- selected model, effort, and route;
- persisted lifecycle labels, including the issue risk label when present;
- start/end/duration;
- completion outcome;
- uniquely attributable token counts;
- authoritative before/after quota percentage-point deltas.

Missing telemetry stays missing. A worker with unavailable token usage does not contribute a zero-token sample, and a worker without authoritative before/after quota percentages does not contribute a zero quota-cost sample.

## Dashboard and API

The localhost operations console exposes the analysis through `GET /api/usage-analysis`. The optional `limit` query parameter is bounded to 1–5000 retained completed workers; the default is 500.

The main dashboard renders the analysis alongside the existing operations/lifecycle views. It shows:

- telemetry coverage counts before any comparative numbers;
- grouped tables by role, model, effort, risk, and normalized outcome;
- recent highest-cost worker lifetimes with links into #46C worker detail;
- per-issue lifetime/continuation rollups;
- an explicit unavailable message for measurements the runtime cannot support authoritatively.

The dashboard remains localhost-only and read-only. Analysis refresh is a data read, not an operator mutation action.

## Views

The collector returns:

- coverage counts for token telemetry, primary quota deltas, and risk class;
- overall medians/totals;
- grouped comparisons by worker role;
- grouped comparisons by model;
- grouped comparisons by reasoning effort;
- grouped comparisons by persisted risk class;
- grouped comparisons by outcome class;
- recent workers ranked by authoritative primary-window quota consumption, falling back to token count when quota data is unavailable;
- per-issue lifetime rollups showing chronological worker count, continuation count, total tokens/quota cost where available, duration, roles, final outcome, and run IDs.

The grouped rows intentionally include both sample count and telemetry-coverage count. A median based on one authoritative quota sample should not be mistaken for a robust population estimate.

## Outcome classes

For comparison only, worker terminal outcomes are normalized into broad analysis groups:

- `successful_handoff`: agent review, human review, report complete, completed/idle, or reviewer approval;
- `halted`: explicit halt, stale-process reconciliation, or usage-limit exhaustion;
- `rework`: rework / changes-requested outcomes;
- `human_attention`: blocked/ambiguous or explicit human-attention outcomes;
- `other`: anything not covered above.

This normalization does not replace the original outcome stored on the worker record.

## Quota semantics

Quota cost is expressed as **percentage points consumed** from an authoritative App Server window. Worker records store remaining-percentage delta as `after - before`, so analysis presents consumption as the negative of that delta. For example:

- remaining 67% -> 58% = `-9` remaining percentage points;
- analysis cost = `9` percentage points consumed.

Token counts and quota percentage points remain separate metrics. No token-to-quota conversion is performed.

## Continuations / retries

Issue rollups count additional worker lifetimes for the same GH issue as chronological continuations (`lifetimes - 1`). This is useful for estimating incremental cost across repeated work, but it is **not proof of causal retry lineage**. #46C worker detail retains the same limitation.

## Risk class

Risk class is read from the completed worker's persisted `lifecycleLabels`. Older records that did not retain a single `risk:*` label are grouped as `unavailable`; multiple risk labels are grouped as `conflicting`. The analysis does not query today's GitHub label and retroactively apply it to an older lifetime.

## Timing limitation

The requested Unity-validation-wait vs model-reasoning-time comparison is currently unavailable. Supervisor knows worker wall-clock duration and Unity run durations, but Codex telemetry does not expose authoritative model-reasoning wall-clock time separately from tool execution/waits. Subtracting Unity time from worker duration would create a misleading derived metric, so #46D reports this comparison as unavailable instead.

## Use for #34 experiments

For plugin/context-efficiency experiments, compare like with like and preserve sample counts. At minimum record:

1. risk class;
2. worker role;
3. model + effort;
4. token telemetry coverage;
5. quota-delta coverage;
6. success/halt/rework outcome;
7. continuation count.

Prefer medians over single-run anecdotes once enough comparable lifetimes exist. Do not claim a routing/plugin improvement from quota percentages when only token telemetry is available, or vice versa.

## Validation

`tests/supervisor-usage-analysis-test.py` covers:

- grouping by role/risk/outcome;
- token medians/totals;
- authoritative quota-cost sign and median;
- missing token/quota/risk coverage;
- expensive-worker ranking;
- same-issue continuation rollups;
- explicit refusal to fabricate model-reasoning timing.

`tests/operations-dashboard-policy-test.py` additionally verifies that the usage-analysis endpoint and comparison views are wired into the existing dashboard while the dashboard remains free of privileged process/GitHub mutation behavior.

Both tests and the Python compile checks are part of `bash tests/run.sh`.
