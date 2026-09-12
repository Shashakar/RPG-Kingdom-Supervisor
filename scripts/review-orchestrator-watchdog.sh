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

while kill -0 "$launcher_pid" 2>/dev/null; do
  sleep "$interval"
  if ! kill -0 "$review_pid" 2>/dev/null; then
    echo "ERROR: review orchestrator exited unexpectedly; terminating Supervisor so the failure is visible." >&2
    echo "Recent review log:" >&2
    tail -n 40 "$review_log" >&2 2>/dev/null || true
    kill -TERM "$launcher_pid" 2>/dev/null || true
    exit 1
  fi
done

exit 0
