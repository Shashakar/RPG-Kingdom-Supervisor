# Supervisor Operations Telemetry

This document defines the durable telemetry, service-health, lifecycle-queue, and activity-correlation contracts for Supervisor issue #46.

The goal is to make the dashboard consume structured Supervisor-owned state and authoritative GitHub lifecycle data instead of reconstructing operations from terminals or screenshots. The console deliberately favors reliable raw records and simple tables/cards over speculative charts.

## Authority boundaries

Telemetry does not become a second lifecycle authority.

- **GitHub labels/issues/PRs** remain authoritative for work lifecycle.
- **Codex App Server** remains authoritative for account rate-limit snapshots.
- **Codex rollout/session metadata** remains authoritative for per-thread token counts when those records are available and uniquely attributable.
- **Unity/Git broker status and durable response files** remain authoritative for broker work/results.
- **Review orchestrator structured issue comments and `status.json`** remain authoritative for review state and review-service poll health.
- **Supervisor telemetry/activity aggregation** correlates those records into worker lifetimes, queues, and dashboard views.

If an authoritative source does not expose a field, Supervisor records that field as unavailable. It does not derive quota percentage from token count and does not persist a parallel lifecycle state machine.

## State layout

By default, state lives under:

```text
~/.local/state/rpg-kingdom-supervisor/
```

`RPGK_SUPERVISOR_STATE_ROOT` overrides that root.

Relevant telemetry files:

```text
symphony/status.json
review-orchestrator/status.json
unity-broker/status.json
git-broker/status.json
usage/current.json
usage/snapshots.jsonl
workers/active/<role>.json
workers/history/<run-id>.json
workers/history.jsonl
telemetry/events.jsonl
```

JSON snapshots are written atomically. JSONL history writes use a local file lock so review/implementation telemetry cannot interleave partial records.

Lifecycle queues are **not** persisted under this state root. They are a live read-only projection of current GitHub issue labels plus structured review metadata.

## Service health semantics

The operations collector normalizes each service to one of these health values:

- `healthy` — expected process is alive and its structured state is healthy.
- `busy` — a healthy broker is actively running a request.
- `degraded` — service is alive but has a recoverable/backoff condition.
- `blocked` — service is alive but a durable external/configuration condition prevents progress.
- `stopped` — the service explicitly stopped, has no status record, or its recorded PID is no longer alive.
- `unknown` — a live service reported a state the collector cannot safely classify.

The collector does not treat `PID exists` as sufficient for review health: durable review poll state (`ready`, `degraded`, `blocked`, last successful poll, last error/backoff) is preserved and surfaced. Broker `running` state is normalized to `busy` and retains its active request metadata.

`run-symphony.sh` writes a Supervisor-owned Symphony status record for launcher startup/running/stopped state. This is intentionally a process/lifecycle signal, not a claim that upstream Symphony internals expose a heartbeat they do not provide.

## Codex quota snapshots

`scripts/codex-usage-snapshot.py` starts a short-lived local Codex App Server connection **without starting a model turn** and calls:

```text
account/rateLimits/read
```

The normalized snapshot records only values actually returned by Codex, including when available:

- primary window used/remaining percentage, duration, and reset;
- secondary window used/remaining percentage, duration, and reset;
- plan/limit identifier;
- whether ordinary included usage is allowed;
- credit/reset-credit metadata exposed by the same response.

The current Codex protocol exposes these rate-limit windows through App Server. The five-hour and weekly labels in the dashboard are presentation names for the returned primary/secondary windows; the stored durations remain visible so an unexpected server-side bucket shape is not silently misrepresented.

Sampling is fail-soft for worker execution. If App Server/account data is unavailable, `usage/current.json` becomes an explicit `unavailable` snapshot rather than blocking implementation or fabricating a percentage.

## Worker lifetime telemetry

The Codex implementation router and independent review worker create active records only **after** acquiring the existing Codex session lock. This prevents queued work from appearing active before it owns the slot.

Roles are recorded separately:

- `implementation`
- `repair`
- `review`
- `report-only`

Each active/completed record can contain:

- GH issue / workspace;
- role;
- model, effort, route;
- start/end time and duration;
- worker process PID while active;
- per-thread token totals where uniquely attributable;
- authoritative quota snapshot before and after the lifetime;
- percentage-point quota delta only when both authoritative samples are available;
- final lifecycle/verdict outcome.

Implementation/repair/report-only completion is finalized from `after-run-guard.sh`, after lifecycle mutation/reconciliation has occurred. Review completion is finalized by the review worker after its structured verdict exists.

### Token attribution

Per-worker tokens are read from Codex rollout/session `token_count` records. Supervisor prefers a rollout that contains the worker workspace path. A single rollout in the worker time window may be accepted as a fallback while the current global Codex lock guarantees one Codex worker at a time. Ambiguous multiple-session windows are reported as unavailable rather than guessed.

The stored token fields are:

- input tokens;
- cached input tokens;
- output tokens;
- reasoning-output tokens when exposed;
- total tokens.

These token values are not converted into quota percentages.

## Lifecycle queues

`scripts/supervisor_activity.py` projects current open RPG Kingdom issues into these dashboard buckets:

- `implementing` — `symphony:ready` when no more specific lifecycle label wins;
- `agent_review` — `symphony:agent-review`;
- `rework` — `symphony:rework` (including the intentional overlap where `symphony:ready` is also present for the repair dispatch lease);
- `human_review` — `symphony:human-review`;
- `human_attention` — `symphony:human-attention`;
- `halted` — `symphony:halted`, including quota-blocked workers when the durable issue comment identifies `usage_limit_exceeded`;
- `report_complete` — `symphony:report-complete`.

Lifecycle precedence is explicit so transient handoff overlaps cannot make an issue appear to be in two queues. In particular, `rework` takes precedence over both `agent-review` and `ready`.

Each queue row correlates current labels with the latest structured review marker and active/recent worker telemetry to show, where available:

- issue and PR;
- lifecycle state and time in that state;
- actual active worker model/effort or label-derived route for queued work;
- review cycle;
- automatic repairs consumed / maximum;
- latest verdict and summary;
- halt/review reason;
- current reviewed head SHA;
- whether human action is required.

The label-derived route is a presentation of the same precedence used by `scripts/routing-policy.sh`; an already-running worker's recorded model/effort wins because it is runtime truth. Conflicting route labels are shown as invalid rather than silently choosing one.

State age uses the latest relevant GitHub `labeled` issue event when available. If GitHub event history cannot identify that transition, the collector falls back to structured review/update timestamps and makes no stronger claim.

## Unified activity timeline

The same collector merges recent events from authoritative/durable sources into one reverse-chronological feed:

- GitHub lifecycle label transitions;
- structured automated-review verdicts/cycles;
- Supervisor `worker_started` / `worker_completed` telemetry;
- Unity run history, including compile/test/infrastructure diagnosis;
- Git prepare/handoff broker response history.

The feed keeps source identifiers such as issue number, PR number/head SHA, worker run ID, Unity request ID, and Git request ID. Completed worker lifetimes are correlated to Unity requests for the same issue when the Unity run falls inside the worker lifetime (with a small boundary tolerance), allowing the dashboard to jump from implementation/review context into the existing Unity run detail.

This is correlation, not event-sourcing: the activity view does not mutate or replace any source system. GitHub remains authoritative for lifecycle even when local telemetry is missing.

The collector uses a short in-process cache (10 seconds by default, configurable with `RPGK_ACTIVITY_CACHE_SECONDS`) because the dashboard refreshes frequently and GitHub lifecycle/event reads are remote. Partial GitHub read failures are surfaced in the response rather than silently replaced with stale invented state.

## Secret handling

Telemetry is local operational state and must not become a credential sink.

Before persistence or dashboard output, the telemetry layer redacts:

- authorization fields/headers;
- access/refresh/id tokens;
- API keys;
- passwords/secrets/credentials;
- configured GitHub/OpenAI secret values if they accidentally appear inside a string;
- bearer-token-looking strings.

Token **counts** such as `inputTokens` and `totalTokens` are not credentials and are retained.

The quota snapshot process explicitly removes GitHub/OpenAI environment secrets before spawning its read-only App Server probe. Activity output passes through the same telemetry sanitizer before it is served.

## Dashboard

The localhost-only dashboard exposes:

- `/api/operations` for service health, quota, active workers, and recent worker lifetimes;
- `/api/lifecycle` for GitHub lifecycle queues and the unified cross-system activity timeline;
- existing per-issue diagnostics and Unity history/detail endpoints.

The top-level UI visually separates human-review, human-attention, halted/quota, and report-complete work from automated queues. It provides only read-only navigation to issues/PRs and existing Unity run detail. It does not add lifecycle mutation, merge, process-kill, force-unlock, or other privileged controls.

The dashboard remains bound to `127.0.0.1`.

## Validation

The deterministic suite covers:

- healthy/busy/degraded/stopped service normalization;
- durable active/completed worker records;
- role/model/effort capture;
- rollout token attribution;
- before/after quota percentage-point delta;
- explicit unavailable behavior;
- secret redaction;
- App Server rate-limit normalization through a fake JSON-RPC server;
- reviewer telemetry integration;
- lifecycle queue precedence, including `rework + ready` overlap;
- human-review, human-attention, halted/quota, and active implementation fixtures;
- review-cycle/repair/PR/head correlation;
- route projection and conflicting-label behavior;
- unified worker/review/lifecycle/Unity/Git activity;
- worker-to-Unity request correlation;
- dashboard read-only policy and Python/shell syntax.

Run:

```bash
bash tests/run.sh
```

## Remaining #46 work

46A delivered the telemetry/service-health foundation and 46B adds lifecycle queues plus the unified activity timeline. The remaining umbrella work is intentionally separate: retention/reconciliation controls, richer worker/run detail, and comparative usage analysis for later #34 plugin/context-efficiency experiments. Those slices should continue building on the same authority boundaries rather than adding parallel state formats.
