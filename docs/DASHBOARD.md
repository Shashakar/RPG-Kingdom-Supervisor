# Supervisor Dashboard

The Supervisor dashboard is a localhost-only, read-only operator console. It is served by `scripts/supervisor_dashboard.py` and renders `scripts/supervisor_dashboard.html` without a frontend framework or external runtime dependency.

## Operator hierarchy

The dashboard is organized around the questions an operator needs answered first:

1. Is Supervisor healthy?
2. Is work actively running?
3. Is anything degraded, blocked, or halted?
4. Does anything require human action?
5. What changed recently?

The sticky header keeps overall health, active-worker state, Codex quota, refresh time, and refresh control visible. The Overview view emphasizes those same signals, keeps healthy services compact, and raises an attention banner only when service state, halted work, or lifecycle data requires it.

## Views

- **Overview** — system health, active workers, human-action count, quota, compact service state, and collapsed telemetry maintenance.
- **Work / Activity** — lifecycle queues, unified recent activity, and recent Codex worker lifetimes. Empty queues render as lightweight zero-state rows. Queue, activity, and worker issue references open the Issue detail view directly.
- **Usage** — a compact retained-sample and coverage summary followed by collapsed grouped analysis, expensive-worker, and continuation-cost tables.
- **Unity** — global Unity run history with optional issue, operation, and status filters. Selecting a run opens a read-only detail drawer.
- **Issue detail** — focused diagnostics for one issue, including GitHub state, review lifecycle, workspace/session state, Unity resource state, recent Symphony lines, and issue-filtered Unity history.

Worker lifetime details and Unity run details open in a dismissible drawer so operators can inspect raw diagnostics without losing their place in the dashboard.

## Safety boundary

The dashboard exposes observability only. It must not add issue/PR lifecycle mutation, merge, rearm, process-kill, force-unlock, or other operational mutation controls. Those behaviors remain owned by their existing host-side adapters and workflows.

The HTTP server remains bound to `127.0.0.1`. Dashboard presentation changes must preserve that boundary and all API-backed observability introduced by the operations telemetry, lifecycle/activity, usage analysis, issue diagnostics, worker lifetime, and Unity run-history systems.
