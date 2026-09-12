# Codex Quota Refresh

Supervisor treats Codex account quota as an operator control signal, not merely as historical worker telemetry.

## Source of truth

Quota is sampled only through a short-lived Codex App Server process using `account/rateLimits/read`. The sampler does **not** start a model thread or turn, does not scrape the ChatGPT UI, and does not infer account quota from token counts.

The normalized sample records only fields returned by App Server, including:

- `observedAt`;
- `source`;
- primary five-hour used/remaining percentage and reset time;
- secondary weekly used/remaining percentage and reset time;
- account/plan metadata when returned;
- explicit `status` and failure `reason`.

Tracker/API credentials are removed from the sampler child environment and all persisted telemetry continues through Supervisor redaction.

## Refresh lifecycle

`scripts/run-symphony.sh` performs one synchronous quota refresh during Supervisor startup before Symphony can dispatch a worker. It then starts `scripts/quota-refresh-service.py`, which refreshes quota periodically while Supervisor is alive, including while no Codex worker is active.

Existing worker-boundary sampling remains in place:

- the Codex router refreshes immediately before a worker lifetime starts;
- the after-run host boundary refreshes when a worker lifetime ends.

The idle service therefore fills the operator-visibility gap rather than replacing worker historical samples.

The default idle cadence is **300 seconds (5 minutes)**. Configure it with:

```text
RPGK_QUOTA_REFRESH_SECONDS
```

The service enforces a 60-second minimum cadence. Minute-scale sampling is intentional; quota refresh is observability and should not become a high-frequency control loop.

## Freshness

Each persisted attempt includes `staleAfterSeconds`. The default stale threshold is **600 seconds (10 minutes)** and can be configured with:

```text
RPGK_QUOTA_STALE_AFTER_SECONDS
```

Operator presentation derives one of four states:

- `fresh` — latest attempt succeeded and is younger than the stale threshold;
- `stale` — latest successful attempt is older than the stale threshold;
- `unavailable` — the latest refresh attempt failed;
- `not-sampled` — no authoritative attempt has been recorded yet.

The dashboard computes age continuously and the operations API also exposes derived `ageSeconds` and `freshness` fields. A sample never remains visually current merely because its percentages are present.

## Latest attempt versus last successful sample

Two meanings are intentionally kept separate:

- `usage/current.json` is the **latest refresh attempt**. If that attempt failed, its status remains `unavailable`. Continuation policy and other safety checks therefore cannot silently fall back to older quota.
- `usage/last_success.json` is the **last successful authoritative sample**. A failed latest attempt includes that sample under `lastSuccessful` for operator context.

This distinction matters to #60: a failed fresh quota probe must stop automatic continuation rather than allowing an older healthy percentage to authorize another expensive turn.

## Worker historical quota

Worker telemetry retains the existing lifetime fields:

```text
quotaBefore
quotaAfter
quotaDelta
```

Those values describe the historical worker lifetime and are never rewritten when the global quota window later resets. Worker detail additionally exposes `currentQuota` with an explicit semantic note so a later 100% five-hour reset cannot be mistaken for the quota state during an earlier worker.

`scripts/diagnose-issue.sh` likewise prints current global quota in a separate section. With `--json`, it adds `currentQuota` and a `quotaSemantics` explanation without replacing any issue/worker evidence.

## Failure behavior

Idle quota refresh is observability-only. A transient probe or refresher failure does not stop Symphony, mutate issue lifecycle, rearm work, or create another worker lifetime.

When a probe fails:

1. the failed attempt becomes `usage/current.json` with an explicit reason;
2. the prior `usage/last_success.json` is preserved;
3. the current record references the last successful sample for presentation;
4. the dashboard shows `unavailable` plus last-known-good age/percentages instead of an ambiguous dash.

If the background refresher itself exits, Supervisor continues and warns on stderr. The last successful sample will naturally age into `stale` until another worker-boundary sample or Supervisor restart refreshes it.

## Files

Runtime state lives below:

```text
~/.local/state/rpg-kingdom-supervisor/usage/
```

Relevant files are:

- `current.json` — latest attempt;
- `last_success.json` — last successful authoritative sample;
- `snapshots.jsonl` — append-only retained sampling history;
- `refresher-status.json` — background refresher lifecycle/last-attempt state;
- `refresher.log` — host service log.

All paths move with `RPGK_SUPERVISOR_STATE_ROOT`.

## Validation

Deterministic coverage verifies:

- a successful sample persists both latest and last-success state;
- a later failure remains latest/unavailable while preserving last success;
- repeated model-free refreshes update authoritative percentages without `thread/start` or `turn/start`;
- fresh, stale, unavailable, and not-sampled presentation states;
- current global quota remains distinct from historical worker quota;
- quota UI remains read-only and localhost dashboard binding is unchanged;
- secret redaction remains intact;
- the full `bash tests/run.sh` gate remains green.
