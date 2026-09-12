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

## Persistence and lifecycle

The gate writes Supervisor-owned, locally Git-excluded state in the issue workspace:

- `.symphony-continuation-state.json` — latest continuation decision/evidence;
- `.symphony-continuation-stop.json` — written only when another automatic turn is declined.

It also appends a sanitized `continuation_decision` event to Supervisor telemetry.

When the policy stops continuation while `symphony:ready` remains, the existing after-run host boundary removes the dispatch lease, adds `symphony:halted`, and posts a distinct `continuation-budget-stop` report containing the route, turn, reason, current quota evidence, cumulative token evidence, and latest Unity run. This is intentionally distinct from both `usage_limit_exceeded` and generic `agent.max_turns` exhaustion.

The normal one-shot `symphony:rearm` path remains the only way to authorize another worker lifetime. No automatic rearm is introduced.

## Current limitations / follow-up

This first #60 slice is designed to prevent the GH-108 failure mode before more expensive investigative work is dispatched. Rich dashboard rendering of per-turn history and deeper context-growth analysis remain follow-up work under #60. Supervisor #34 separately owns plugin/skill/context-routing experiments intended to reduce the context cost inside each turn.

The quota probe opens a short-lived model-free App Server connection while the primary worker App Server is idle between turns. It does not start a model turn. If that probe cannot provide a fresh authoritative sample, automatic continuation is denied and the workspace is preserved.
