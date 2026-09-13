# Codex Automatic Continuation Policy

Symphony's upstream `agent.max_turns` remains a hard safety ceiling. RPG Kingdom Supervisor adds a host-owned continuation gate so a normal completed Codex turn does not automatically imply that another model turn should be spent.

This policy was introduced after RPG Kingdom GH-108 used all four configured Terra/medium turns, accumulated 16.7M reported rollout tokens, retained useful dirty workspace changes, and still stopped before PR handoff. The goal is to preserve useful work while making additional model spend deliberate.

## Upstream seam

The evaluated Symphony pin automatically continues a routable active issue after every normal `turn/completed` result until `agent.max_turns` is reached. Current upstream still has the same behavior and does not expose a supported between-turn continuation hook.

Supervisor therefore carries a narrow compatibility transform, `scripts/patch-symphony-continuation-policy.py`, alongside the existing named-permissions and usage-limit transforms. The transform calls the model-free host policy only after the tracker confirms that the issue is still active/routable and before Symphony starts the next turn.

The hard `agent.max_turns: 4` value in `WORKFLOW.md` is intentionally retained. It is a ceiling, not the automatic continuation budget.

## Default route policy

The defaults are deliberately conservative for expensive routes:

| Route | Default automatic total-turn limit | Primary quota floor | Weekly quota floor |
| --- | ---: | ---: | ---: |
| Luna | 4 | 20% | 10% |
| Terra | 2 | 35% | 10% |
| Sol | 2 | 35% | 10% |
| Astra | 1 | 35% | 10% |

A limit of `2` means turn 1 may automatically continue to turn 2 when the other policy checks pass, but turn 2 will not automatically spend turn 3. Reviewed rearm remains available for preserved work.

The route comes from explicit `model:*` labels first, then the existing risk mapping. This does not change model selection itself.

Environment overrides are available for measured tuning:

- `RPGK_AUTO_TURN_LIMIT_LUNA`
- `RPGK_AUTO_TURN_LIMIT_TERRA`
- `RPGK_AUTO_TURN_LIMIT_SOL`
- `RPGK_AUTO_TURN_LIMIT_ASTRA`
- `RPGK_CONTINUATION_MIN_PRIMARY_PERCENT`
- `RPGK_CONTINUATION_MIN_WEEKLY_PERCENT`
- `RPGK_CONTINUATION_QUOTA_MAX_AGE_SECONDS` (default 180)

Do not raise these simply to increase unattended completion. Changes should be justified by retained usage/completion evidence.

## Decision inputs

Before another turn, `scripts/continuation-policy.py` evaluates only host-observable evidence:

1. **Route budget** — the route-specific automatic-turn limit must not already be reached.
2. **Authoritative quota** — a model-free `account/rateLimits/read` snapshot is refreshed. Missing/stale quota fails safe instead of being treated as unlimited capacity.
3. **Workspace progress** — current Git HEAD and non-Supervisor source/test worktree status are fingerprinted.
4. **Unity progress** — the latest Supervisor Unity run ID/result/filter is compared with the prior decision state.
5. **Rollout usage** — current cumulative token telemetry is sampled from the attributable Codex rollout, and a per-turn delta is recorded when a prior cumulative sample exists.

Obvious no-progress patterns stop automatic continuation. In particular, an unchanged workspace with no new Unity run is not enough evidence to spend another turn, and rerunning the same focused failing Unity validation without a source change is treated as a stop condition.

These progress checks are deliberately bounded heuristics. They do not claim to understand whether a code change is semantically correct.

## Per-turn lifecycle evidence

`scripts/turn-telemetry.py` instruments the actual Symphony turn boundary. Before each Codex turn starts it records a model-free snapshot, and after the completed turn reaches its tracker/continuation boundary it records the corresponding ending snapshot and decision.

Each completed turn retains, when available:

- worker run ID, turn number, route, hard cap, and automatic route cap;
- start/end timestamps and duration;
- cumulative token snapshots before/after plus the turn delta for input, cached input, output, reasoning, and total tokens;
- authoritative quota snapshots before/after and percentage-point delta for the primary and weekly windows;
- workspace HEAD/status fingerprints before/after and whether host-observable source state changed;
- latest Unity run before/after and whether validation evidence changed;
- the terminal turn decision and reason (`continue`, `continuation-budget-stop`, `hard-turn-cap`, `tracker-complete`, or an explicit error boundary).

The first turn can legitimately have no attributable rollout before it starts. When the ending rollout is uniquely attributable to the current worker session, that first cumulative sample is retained as the first-turn delta with an explicit basis marker rather than silently reporting an unknown or estimating quota spend.

Cached input remains a separate token field. Quota consumption is never inferred from token counts.

Turn evidence is appended both to the workspace-local Git-excluded `.symphony-turn-history.jsonl` file and to durable Supervisor telemetry as `worker_turn_completed` events keyed by `workerRunId`. Worker-detail diagnostics consume the durable telemetry so completed/halted workers remain explainable after the active workspace lifecycle changes.

## Persistence and lifecycle

The gate writes Supervisor-owned, locally Git-excluded state in the issue workspace:

- `.symphony-continuation-state.json` — latest continuation decision/evidence;
- `.symphony-continuation-stop.json` — written only when another automatic turn is declined;
- `.symphony-turn-start.json` — current in-flight turn baseline;
- `.symphony-turn-history.jsonl` — append-only local per-turn evidence for the preserved workspace.

It also appends sanitized continuation and turn lifecycle events to Supervisor telemetry.

When the policy stops continuation while `symphony:ready` remains, the existing after-run host boundary removes the dispatch lease, adds `symphony:halted`, and posts a distinct `continuation-budget-stop` report containing the route, turn, reason, current quota evidence, cumulative token evidence, and latest Unity run. This is intentionally distinct from both `usage_limit_exceeded` and generic `agent.max_turns` exhaustion.

The normal one-shot `symphony:rearm` path remains the only way to authorize another worker lifetime. No automatic rearm is introduced.

## Dashboard / diagnostics

The existing worker-lifetime detail endpoint and drawer expose retained `turnHistory` rather than creating a parallel continuation dashboard. The operator can see the hard cap versus automatic route cap, turn duration, total/cached token delta, authoritative quota after the turn, whether the workspace changed, latest Unity run, and the exact continuation/terminal reason.

Lifetime-level usage and current global quota remain separate from per-turn historical evidence so a later quota-window reset cannot be mistaken for what was available during the worker turn.

## Current limitation / follow-up

This policy now answers whether another turn was justified and records what each turn cost/accomplished. It does not attempt to compact or redesign Codex's retained thread context. Supervisor #34 owns plugin/skill/context-routing and context-growth experiments intended to reduce the context cost inside each turn without weakening RPG Kingdom's repository instructions.

The quota probe opens a short-lived model-free App Server connection while the primary worker App Server is idle between turns. It does not start a model turn. If that probe cannot provide a fresh authoritative sample, automatic continuation is denied and the workspace is preserved.
