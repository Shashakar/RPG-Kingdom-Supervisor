#!/usr/bin/env bash
set -uo pipefail

if (( $# < 3 )); then
  echo "usage: $0 <review-pid> <launcher-pid> <review-log>" >&2
  exit 64
fi

review_pid="$1"
launcher_pid="$2"
review_log="$3"
interval="${RPGK_REVIEW_WATCHDOG_SECONDS:-5}"
supervisor_root="${RPGK_SUPERVISOR_ROOT:-$HOME/src/RPG-Kingdom-Supervisor}"
quota_refresh_seconds="${RPGK_QUOTA_REFRESH_SECONDS:-300}"
quota_snapshot="$supervisor_root/scripts/codex-usage-snapshot.py"
last_quota_refresh=0

refresh_quota_if_due() {
  local now
  now="$(date +%s)"
  if (( last_quota_refresh == 0 || now - last_quota_refresh >= quota_refresh_seconds )); then
    # Quota refresh is observability-only here. Preserve Supervisor availability if the short-lived
    # App Server probe fails; codex-usage-snapshot.py persists the failed attempt and last-known-good
    # state without starting a model turn.
    env -u SYMPHONY_GITHUB_TOKEN -u GITHUB_TOKEN -u GH_TOKEN -u OPENAI_API_KEY \
      python3 "$quota_snapshot" --write --quiet >/dev/null 2>&1 || true
    last_quota_refresh="$now"
  fi
}

# Populate quota immediately on Supervisor startup rather than waiting for the first worker.
refresh_quota_if_due

while kill -0 "$launcher_pid" 2>/dev/null; do
  sleep "$interval"
  if ! kill -0 "$review_pid" 2>/dev/null; then
    echo "ERROR: review orchestrator exited unexpectedly; terminating Supervisor so the failure is visible." >&2
    echo "Recent review log:" >&2
    tail -n 40 "$review_log" >&2 2>/dev/null || true
    kill -TERM "$launcher_pid" 2>/dev/null || true
    exit 1
  fi
  refresh_quota_if_due
done

exit 0
