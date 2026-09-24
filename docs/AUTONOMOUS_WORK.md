# Autonomous operating windows and approved work plans

Issue #109 adds a host-owned, model-free scheduler for bounded unattended work. It automates continuation and sequencing; it does not expand model authority, authoring authority, repair limits, or merge authority.

## Configuration

Durable configuration lives at `$RPGK_SUPERVISOR_STATE_ROOT/autonomous-plan.json` (default `~/.local/state/rpg-kingdom-supervisor/autonomous-plan.json`). Configure it with:

```bash
python3 scripts/autonomous-scheduler.py configure --from-file config/autonomous-plan.example.json
```

The recurring window is evaluated in its configured IANA timezone and supports midnight crossing. Equal start/end means an all-day window, but autonomy is still explicitly disabled until enabled.

Each work-plan item names an existing GitHub issue. `after` contains predecessor issue numbers. Predecessors are considered complete only when GitHub reports them closed. This deliberately avoids treating worker completion, PR creation, or human-review as dependency completion.

## Safety gates

The policy refuses a new dispatch outside the window, while a worker is active, when authoritative quota is unavailable/below its configured floors/reserve, or when an issue has human-review, human-attention, manual-action, or an unknown/non-recoverable halt. Recoverable halt classes are limited to quota exhaustion, continuation-policy stops, cleared resource unavailability, and explicitly recoverable preflight states.

A human gate blocks that dependency lane. Another explicitly approved item with satisfied dependencies may continue. No queue item is invented by a model and no merge is performed by this scheduler.

When an eligible planned issue is halted, the scheduler invokes the existing one-shot `rearm-issue.sh` contract. A fresh eligible issue receives only `symphony:ready`. Existing resource/authoring/routing labels are not rewritten.

## Quota

Each scheduler evaluation refreshes the existing model-free App Server quota snapshot. `min_primary_to_start_turn`, `reserve_primary`, and `min_weekly_to_start_turn` are enforced before dispatch. Missing/stale-unavailable authoritative quota fails closed rather than being estimated from tokens.

## Host lifecycle and restart recovery

`run-symphony.sh` starts the scheduler beside the existing brokers/review service. Configuration and the latest projection are durable host state. On restart the scheduler re-reads GitHub, active-worker telemetry, quota, and the durable plan before taking action; it never assumes an interrupted worker succeeded.

The scheduler never terminates an active worker or host transaction when the window closes. It simply declines the next dispatch.

## Operator controls

The dashboard exposes current window/decision/quota state, the ordered approved plan, blocked lanes, and controls to enable/disable autonomy, pause after the current turn, stop after the current issue, resume, enable/disable plan items, and reorder plan items.

Equivalent CLI controls are available through `autonomous-scheduler.py control`.

Pause/stop controls become effective at the next scheduler boundary; they do not kill active Unity/Git/model operations.

## Status and summary

`autonomous-status.json` is a model-free projection containing the current decision, quota, issue state, window, ordered plan, and blocked lanes. The dashboard serves this projection through `/api/autonomous`. This data is sufficient for an operator/end-of-window summary without spending a model turn.

## Testing

`tests/autonomous-plan-test.py` covers window boundaries, quota floors, active-worker exclusion, dependencies, human gates, and independent-lane progression. `tests/autonomous-scheduler-test.py` covers durable configuration/control state and mutation only after an eligible policy decision. Both run in the normal Supervisor CI suite.
