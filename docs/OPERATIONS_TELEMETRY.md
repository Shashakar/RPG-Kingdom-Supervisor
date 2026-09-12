# Supervisor Operations Telemetry

This document defines the durable telemetry and service-health foundation for Supervisor issue #46.

The goal is to make the dashboard consume structured Supervisor-owned state instead of reconstructing operations from terminals or screenshots. This first slice deliberately favors reliable raw records and simple tables/cards over charts.

## Authority boundaries

Telemetry does not become a second lifecycle authority.

- **GitHub labels/issues/PRs** remain authoritative for work lifecycle.
- **Codex App Server** remains authoritative for account rate-limit snapshots.
- **Codex rollout/session metadata** remains authoritative for per-thread token counts when those records are available and uniquely attributable.
- **Unity/Git broker status files** remain authoritative for broker state.
- **Review orchestrator `status.json`** remains authoritative for review-service poll health.
- **Supervisor telemetry** correlates those records into worker lifetimes and dashboard views.

If an authoritative source does not expose a field, Supervisor records that field as unavailable. It does not derive quota percentage from token count.

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

## Service health semantics

The operations collector normalizes each service to one of these health values:

- `healthy` — expected process is alive and its structured state is healthy.
- `busy` — a healthy broker is actively running a request.
- `degraded` — service is alive but has a recoverable/backoff condition.
- `blocked` — service is alive but a durable external/configuration condition prevents progress.
- `stopped` — the service explicitly stopped, has no status record, or its recorded PID is no longer alive.
- `unknown` — a live service reported a state the collector cannot safely classify.

The collector does not treat `PID exists` as sufficient for review health: durable review poll state (`ready`, `degraded`, `blocked`, last successful poll, last error/backoff) is preserved and surfaced. Broker `running` state is normalized to `busy` and retains its active request metadata.

`run-symphony.sh` now writes a Supervisor-owned Symphony status record for launcher startup/running/stopped state. This is intentionally a process/lifecycle signal, not a claim that the upstream Symphony internals have a heartbeat they do not expose.

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

The quota snapshot process explicitly removes GitHub/OpenAI environment secrets before spawning its read-only App Server probe.

## Dashboard

The existing localhost-only dashboard adds `/api/operations` and displays:

- Symphony, review, Unity, and Git service health;
- active broker request metadata where available;
- current authoritative Codex quota snapshot;
- active Codex worker records;
- recent completed worker lifetimes with token totals and quota deltas;
- the existing per-issue diagnostics and Unity history.

The dashboard remains read-only and bound to `127.0.0.1`.

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
- Python/shell syntax for all new runtime seams.

Run:

```bash
bash tests/run.sh
```

## Remaining #46 work

This foundation intentionally does not close issue #46. Follow-up slices still need to add the broader GitHub lifecycle queues/activity correlation and higher-level usage analysis/retention controls described by the umbrella issue. Those views should build on these durable records rather than introducing parallel telemetry formats.
