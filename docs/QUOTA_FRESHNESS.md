# Codex Quota Freshness

Issue #61 makes authoritative Codex quota an operator signal that stays useful even when no worker is running.

## Source of truth

Quota is sampled only through a short-lived Codex App Server process using `account/rateLimits/read`.

The sampler does **not** start a model turn, scrape the ChatGPT UI, or infer quota from token counts. The normalized source remains:

```text
codex-app-server:account/rateLimits/read
```

## Refresh lifecycle

A host-owned refresh now occurs:

- immediately when the Supervisor launcher watchdog starts;
- every `RPGK_QUOTA_REFRESH_SECONDS` while the Supervisor remains alive (default: `300` seconds);
- before implementation/repair/report-only worker dispatch through the existing App Server router;
- after worker completion through the existing telemetry finalization path.

The periodic refresh is observability-only. A failed idle refresh never terminates the Supervisor or starts another worker.

## Persistence

Quota state lives under the Supervisor state root in `usage/`:

- `latest-attempt.json` — the most recent probe result, successful or failed;
- `last-successful.json` — the latest authoritative successful sample;
- `current.json` — the operator-facing current view;
- `snapshots.jsonl` — append-only probe history.

On a successful probe, `current.json` is the new authoritative sample. On a failed probe, the last successful percentages/reset metadata remain available in `current.json`, but the view is marked `status: stale` / `freshness: stale` and includes the latest failure under `latestRefresh`.

This prevents a transient App Server timeout from replacing useful historical state with an unexplained dash while still preventing old values from appearing current.

## Freshness threshold

The default stale threshold is **600 seconds (10 minutes)** and can be changed with:

```text
RPGK_QUOTA_STALE_SECONDS
```

The normal refresh cadence is intentionally half that threshold. The dashboard also computes age from the successful sample's timestamp, so a once-fresh sample becomes visibly stale if the refresher itself stops.

The continuation guard in #60 retains its own stricter continuation-specific freshness threshold. Dashboard/operator freshness and continuation authorization are related signals, not the same policy decision.

## Dashboard states

The Overview quota card distinguishes:

- fresh authoritative percentages, including weekly remaining and the next 5h reset when present;
- stale last-known-good state and its age;
- an unavailable latest refresh reason;
- the narrow `not sampled yet` startup state.

A failed latest refresh may therefore render a stale last-known-good value and a failure reason at the same time. That is intentional.

## Historical worker quota

Worker lifetime records continue to capture `quotaBefore`, `quotaAfter`, and their delta at the lifetime boundary. Those historical samples are immutable evidence for that worker.

The worker detail API additionally exposes current global quota under the explicit `currentGlobalQuota` key. A later quota-window reset must never be interpreted as the quota state that existed during an earlier worker lifetime.

## Security and authority

The sampler removes tracker/GitHub/OpenAI token aliases from the child App Server environment and all persisted payloads still pass through Supervisor telemetry redaction.

Quota refresh is not a dashboard action and does not mutate GitHub lifecycle. The dashboard remains localhost-only and read-only.
