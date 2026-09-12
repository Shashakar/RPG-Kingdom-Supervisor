#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

review_log="$TMP/review.log"
: > "$review_log"

# Use a harmless child shell as the launcher target. The watchdog must terminate it after the
# simulated review process exits, proving the parent Supervisor cannot remain silently healthy.
sleep 30 &
launcher_pid=$!
(sleep 0.1) &
review_pid=$!

set +e
RPGK_REVIEW_WATCHDOG_SECONDS=0.05 bash "$ROOT/scripts/review-orchestrator-watchdog.sh" \
  "$review_pid" "$launcher_pid" "$review_log" >"$TMP/out" 2>"$TMP/err"
watchdog_status=$?
set -e

wait "$review_pid" 2>/dev/null || true
sleep 0.05

if kill -0 "$launcher_pid" 2>/dev/null; then
  kill "$launcher_pid" 2>/dev/null || true
  wait "$launcher_pid" 2>/dev/null || true
  echo "watchdog failed to terminate launcher after review sidecar exit" >&2
  exit 1
fi
wait "$launcher_pid" 2>/dev/null || true

[[ "$watchdog_status" -ne 0 ]]
grep -q 'review orchestrator exited unexpectedly' "$TMP/err"

echo "review-orchestrator-watchdog-test: PASS"
