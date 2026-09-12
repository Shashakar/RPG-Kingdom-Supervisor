# Operations Retention and Worker Detail

This document defines the #46C operations-console slice. It builds on the 46A telemetry foundation and 46B lifecycle/activity aggregation without changing GitHub lifecycle authority.

## Scope

46C adds two bounded capabilities:

1. Supervisor-local telemetry retention and restart reconciliation.
2. A worker-lifetime drill-down that correlates the existing authoritative/durable sources for one Codex lifetime.

Comparative usage analysis remains a later #46 slice.

## Retention

Supervisor telemetry history is retained for 30 days by default. Set `RPGK_TELEMETRY_RETENTION_DAYS` to another positive integer to change the local retention window.

The maintenance pass prunes expired records from:

- `workers/history/*.json`;
- `workers/history.jsonl`;
- `telemetry/events.jsonl`;
- `usage/snapshots.jsonl`.

Current service status, current quota, active worker state, GitHub issue/PR state, workspace source, and Unity/Git broker source artifacts are not deleted by this maintenance pass. Their own retention/authority rules remain separate.

Maintenance writes its last result to:

```text
~/.local/state/rpg-kingdom-supervisor/maintenance/status.json
```

The dashboard displays this status but does not invoke maintenance. It remains a read-only renderer.

## Restart reconciliation

`scripts/run-symphony.sh` runs:

```bash
python3 scripts/supervisor_maintenance.py --apply
```

before starting host services.

An active worker record whose PID is no longer alive is reconciled into completed history with:

- outcome `stale-process`;
- the original issue/role/model/effort/start metadata;
- a reconciliation reason/time;
- token usage marked unavailable rather than guessed;
- the current authoritative quota sample as the after-sample when available;
- quota delta only if both authoritative before/after samples support it.

The stale active record is then removed. Existing completed history for the same run ID is not duplicated.

If startup maintenance itself fails, Supervisor refuses to start. A failed cleanup must not be silently interpreted as a free worker slot.

This reconciliation is local observability repair only. It does **not** add/remove GitHub labels, rearm work, create a new worker lifetime, or merge anything.

## Manual inspection

Preview maintenance without mutation:

```bash
python3 scripts/supervisor_maintenance.py
```

Apply it explicitly:

```bash
python3 scripts/supervisor_maintenance.py --apply
```

Show the last applied status:

```bash
python3 scripts/supervisor_maintenance.py --status
```

## Worker-lifetime detail

`scripts/supervisor_detail.py <run-id>` and dashboard `/api/worker/<run-id>` correlate one active or completed worker lifetime with:

- issue, role, route/model/effort;
- start/end/duration and final outcome;
- token usage;
- before/after Codex quota snapshots and authoritative percentage-point delta when available;
- current GitHub lifecycle projection;
- lifecycle/review events overlapping the worker window;
- automated review history for the issue;
- Unity runs occurring inside the worker window;
- Git prepare/handoff results occurring inside the worker window;
- Unity artifact/log paths;
- chronological same-issue worker lineage.

The same-issue lineage is deliberately labeled as chronological context. It does not claim that adjacent lifetimes are causal retries unless another authoritative source records that relationship.

## Authority and safety

46C does not create a second source of truth:

- GitHub labels/issues/PRs remain lifecycle authority.
- Worker JSON/JSONL remains Supervisor's durable lifetime telemetry.
- Codex App Server/rollout data remains usage authority.
- Unity broker/artifacts remain Unity-run authority.
- Git broker responses remain Git-handoff authority.
- Review structured comments remain review-cycle authority.

Missing data is reported as unavailable rather than reconstructed from token counts, guessed timestamps, or prose.

The dashboard remains bound to `127.0.0.1` and exposes no merge, lifecycle mutation, kill, rearm, force-unlock, or maintenance-apply button.

## Tests

The deterministic regression suite covers:

- dry-run retention planning;
- configurable retention pruning;
- stale PID reconciliation after restart;
- no duplicate completed record for a reconciled run;
- preservation of recent history;
- worker detail usage/lifecycle/Unity/Git/artifact correlation;
- chronological continuation context;
- dashboard endpoints and read-only policy;
- Python and shell syntax for the new seams.

Run the canonical gate with:

```bash
bash tests/run.sh
```
