# Supervisor Dashboard

The Supervisor dashboard is a localhost-only, read-only operator console. It is served by `scripts/supervisor_dashboard.py` and renders `scripts/supervisor_dashboard.html` without a frontend framework or external runtime dependency.

## Operator hierarchy

The dashboard is organized around the questions an operator needs answered first:

1. Is Supervisor healthy?
2. Is work actively running?
3. Did recent work actually finish successfully?
4. Is anything degraded, blocked, or halted?
5. Does anything require human action?
6. What changed recently?

The sticky header keeps overall health, active-worker state, Codex quota, refresh time, and refresh control visible. The Overview view emphasizes those same signals, keeps healthy services compact, shows recent trusted finished tasks separately from live work, and raises an attention banner only when service state, halted work, or lifecycle data requires it.

## Active worker semantics

**Active work means live worker lifetimes only.** `activeWorkers` is filtered by the recorded worker PID before it reaches the dashboard. A dead PID must not remain visible as a `stale` card under Active work.

PID death alone is not evidence that work completed successfully. Normal worker completion moves the durable lifetime record into worker history with its actual lifecycle outcome. If a worker dies outside that normal completion boundary, startup maintenance preserves the record as `stale-process` reconciliation evidence before removing the orphaned active file. The dashboard remains read-only and does not perform this reconciliation itself.

As a result:

- live worker -> shown under Active work;
- normal terminal worker -> shown in recent worker history/activity with its durable outcome;
- crashed/orphaned worker -> not shown as active and not mislabeled `Done`; its diagnostic record is preserved/reconciled by Supervisor maintenance.

## Finished task semantics

**Finished tasks means trusted successful terminal work, not merely a worker process that ended.** The Overview keeps this separate from Active work so a fast successful run does not simply disappear and look as though it was never picked up.

A normal task is shown as **Done** only when GitHub reports the RPG Kingdom issue closed with `state_reason=completed` and there is evidence that the issue participated in the Symphony lifecycle, either through a `symphony:*` lifecycle label or retained worker telemetry. Recent worker telemetry may enrich the row with its latest outcome/model/effort, but it does not create completion authority by itself.

Report-only work is slightly different: an open issue carrying the authoritative `symphony:report-complete` lifecycle label is shown as **Report complete** because the report artifact is the terminal deliverable even though the tracking issue may remain open for a later human/product action.

The dashboard deliberately does **not** call these states finished:

- a dead or stale PID by itself;
- `symphony:halted`;
- `symphony:human-attention`;
- `symphony:human-review` while a PR still awaits the human merge decision;
- GitHub issues closed as `not_planned`, duplicate, or another non-completed reason.

This preserves the distinction between “the worker stopped,” “the implementation reached review,” and “the task actually completed.”

## Views

- **Overview** — system health, active workers, recent trusted finished tasks, human-action count, quota, compact service state, and collapsed telemetry maintenance.
- **Work / Activity** — lifecycle queues, unified recent activity, and recent Codex worker lifetimes. Empty queues render as lightweight zero-state rows. Queue, activity, and worker issue references open the Issue detail view directly.
- **Usage** — a compact retained-sample and coverage summary followed by collapsed grouped analysis, expensive-worker, and continuation-cost tables.
- **Unity** — global Unity run history with optional issue, operation, and status filters. Selecting a run opens a read-only detail drawer.
- **Issue detail** — focused diagnostics for one issue, including GitHub state, review lifecycle, workspace/session state, Unity resource state, recent Symphony lines, and issue-filtered Unity history.

Worker lifetime details and Unity run details open in a dismissible drawer so operators can inspect raw diagnostics without losing their place in the dashboard.

For workers that ran under the #60 continuation instrumentation, the worker drawer also renders **Per-turn continuation evidence**. Each row shows the turn duration, continuation/terminal decision, total and cached token delta, authoritative primary/weekly quota remaining after the turn, whether the workspace changed, latest Unity run, and the reason the next turn was allowed or denied. The row also carries the hard worker cap and route-specific automatic continuation cap so `agent.max_turns` is visibly separate from continuation permission.

Per-turn quota values are historical App Server snapshots. They are not reconstructed from tokens and are not replaced with the dashboard's current global quota after a reset window rolls over.

## Safety boundary

The dashboard exposes observability only. It must not add issue/PR lifecycle mutation, merge, rearm, process-kill, force-unlock, or other operational mutation controls. Those behaviors remain owned by their existing host-side adapters and workflows.

The HTTP server remains bound to `127.0.0.1`. Dashboard presentation changes must preserve that boundary and all API-backed observability introduced by the operations telemetry, lifecycle/activity, usage analysis, issue diagnostics, worker lifetime, and Unity run-history systems.
