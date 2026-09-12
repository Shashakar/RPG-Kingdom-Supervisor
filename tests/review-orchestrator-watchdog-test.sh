#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

review_log="$TMP/review.log"
: > "$review_log"
mkdir -p "$TMP/supervisor/scripts"
cat > "$TMP/supervisor/scripts/codex-usage-snapshot.py" <<'PY'
from pathlib import Path
import os
path = Path(os.environ["RPGK_TEST_QUOTA_CALLS"])
prior = int(path.read_text() or "0") if path.exists() else 0
path.write_text(str(prior + 1))
PY

# Use a harmless child shell as the launcher target. The watchdog must terminate it after the
# simulated review process exits, proving the parent Supervisor cannot remain silently healthy.
sleep 30 &
launcher_pid=$!
(sleep 0.16) &
review_pid=$!

set +e
RPGK_SUPERVISOR_ROOT="$TMP/supervisor" \
RPGK_TEST_QUOTA_CALLS="$TMP/quota-calls" \
RPGK_REVIEW_WATCHDOG_SECONDS=0.02 \
RPGK_QUOTA_REFRESH_SECONDS=0 \
bash "$ROOT/scripts/review-orchestrator-watchdog.sh" \
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

# The first quota probe happens immediately, then periodic probes continue without any model turn.
quota_calls="$(cat "$TMP/quota-calls")"
if (( quota_calls < 2 )); then
  echo "watchdog did not perform startup + periodic quota refreshes (calls=$quota_calls)" >&2
  exit 1
fi

echo "review-orchestrator-watchdog-test: PASS"
