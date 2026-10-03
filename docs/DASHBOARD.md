# Supervisor Dashboard

The Supervisor dashboard is a localhost-only, read-mostly operator console with narrowly typed operator actions. It is served by `scripts/supervisor_dashboard.py` and renders `scripts/supervisor_dashboard.html` without a frontend framework or external runtime dependency.

## Operator hierarchy

The dashboard is organized around the questions an operator needs answered first:

1. Is Supervisor healthy?
2. Is work actively running?
3. Did recent work actually finish successfully?
4. Is anything degraded, blocked, or halted?
5. Does anything require human action?
6. What changed recently?

The sticky header keeps overall health, active-worker state, Codex quota, refresh time, and refresh control visible. The Overview view emphasizes those same signals, keeps healthy services compact, shows recent trusted finished tasks separately from live work, and raises an attention banner only when service state, halted work, or lifecycle data requires it.

## Active Work lifecycle semantics

**Active Work is issue-centric, not worker-centric.** Once an RPG Kingdom issue enters the automated Supervisor lifecycle, the Overview keeps that GitHub issue as the persistent top-level work item while implementation, automated review, repair, re-review, and host-owned visual review appear as ordered child stages.

The stage projection is read-only. It is derived from the same GitHub-authoritative lifecycle labels and structured automated-review state already used by the Work / Activity view, enriched by live worker telemetry only to identify the currently executing stage. The dashboard does not persist a second workflow state machine.

Stage states are:

- `pending` — the workflow has reached or scheduled the stage but no live execution/result is present;
- `running` — the corresponding worker or host visual-review operation is live;
- `passed` — progression proves the stage completed successfully;
- `failed` — a review verdict required another repair cycle;
- `blocked` — the current lifecycle cannot progress without operator/system intervention.

Repair rows are created only after rework actually occurs. One repair uses `Repair` / `Repair Review`; multiple cycles use numbered `Repair #N` / `Repair Review #N` rows. A failed intermediate review remains visible after repair begins so the operator can see how the issue reached its current stage. Reaching a later review cycle is sufficient lifecycle evidence that the preceding review required repair; this is presentation inference, not a new authority.

Live worker records still retain their normal model/effort/session/timing detail and remain available through worker drill-down. They no longer replace one another as the top-level Overview item. Host-owned visual review is similarly attached to its issue as a `Visual Review` stage while active.

An issue leaves Overview Active Work when the authoritative lifecycle moves it into a manual/terminal bucket such as human review/attention or report completion. Halted work remains visible as blocked Active Work because the issue has not completed and still needs intervention.


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

The dashboard is read-mostly. It must not add generic issue/PR lifecycle mutation, process-kill, force-unlock, arbitrary label editing, or generic host/GitHub controls. The only approved lifecycle mutations are the exact reviewed-head Merge PR action and exact halted-issue Rearm action, both delegated to narrow host-side adapters.

The HTTP server remains bound to `127.0.0.1`. Dashboard presentation changes must preserve that boundary and all API-backed observability introduced by the operations telemetry, lifecycle/activity, usage analysis, issue diagnostics, worker lifetime, and Unity run-history systems.


## Operator lifecycle actions

The primary work hierarchy is **Active Work → Manual Validation → Recently Completed → Finished / History**. Empty operational groups are suppressed so the current gate is visually dominant.

The localhost dashboard is read-mostly, not universally read-only. It exposes only two typed operator mutations: **Merge PR** for an exact automated-reviewed PR/head generation and **Rearm** for an exact currently halted issue. Rearm may carry a one-worker-lifetime below-reserve approval; the normal 10% weekly reserve remains the unattended/automatic threshold. Both actions are POST-only, localhost-origin/token protected, stale-state checked, and do not expose arbitrary GitHub commands or label editing.

Project-owned prefab derivative authoring may declare exact `derivativeOutputs` on an existing-scene-composition request. Each entry binds an exact source prefab, a destination prefab under `Assets/RPGKingdom/Generated/AgentDerivatives/`, and provenance JSON under the same root. The host hashes source prefabs before execution, requires them unchanged afterward, and permits copy-back only of the exact derivative prefab/meta/provenance/meta set attested in `changedAssets` alongside the already-authorized scene/NavMesh outputs.


### Trusted reverse-proxy dashboard origins

The dashboard listener remains localhost-bound by default. When the browser reaches it through a reverse proxy that preserves the browser-facing `Host`, exact same-origin requests are accepted automatically. For a proxy where the browser Origin intentionally differs from Host, set `RPGK_DASHBOARD_ALLOWED_ORIGINS` to a comma-separated list of exact origins, including scheme and optional port (for example `https://server.shashakar.com`). Localhost/127.0.0.1 browser origins remain accepted automatically. Non-local operator-action origins must exactly match this allowlist and still present the per-process action token embedded in the served dashboard. Forwarded host/origin headers are not authorization inputs.


## Supervisor deployment control

The Overview includes a narrow **Supervisor deployment** control for the dedicated host. It is intentionally not a terminal or arbitrary command runner.

- `GET /api/management/update` reports the deployed branch/SHA, local dirty state, the locally known `origin/main` relation, and the last dashboard-triggered update result.
- `POST /api/operator/update` uses the same per-process operator action token and same-origin validation as merge/rearm.
- Updates are accepted only from a clean `main` checkout.
- The dashboard can only start the fixed `rpg-kingdom-dashboard-update.service`; it cannot choose a command, service name, repository, ref, or filesystem path.
- The host update service fetches `origin/main`, fast-forwards only, restarts Supervisor and diagnostics, then triggers a pre-created elevated Windows Scheduled Task that restarts the `cloudflared` service.
- The browser expects the diagnostics service to disappear briefly and polls until it returns.

This requires one-time host installation after pulling the feature:

```bash
cd ~/src/RPG-Kingdom-Supervisor
bash scripts/install-dashboard-update.sh
```

Then, from an **elevated Windows PowerShell**, install the deliberately narrow Cloudflared privilege bridge:

```powershell
& "\\\\wsl.localhost\\Ubuntu\\home\\dex\\src\\RPG-Kingdom-Supervisor\\scripts\\windows\\install-cloudflared-refresh-task.ps1"
```

The Windows task has no schedule; it exists only so the WSL host update service can request one elevated `Restart-Service cloudflared` operation. Routine dashboard updates do not grant arbitrary Windows administrator or Linux root command execution.
