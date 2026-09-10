#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
BROKER_PID=""
HANG_CLIENT_PID=""
cleanup() {
  if [[ -n "$HANG_CLIENT_PID" ]]; then
    kill "$HANG_CLIENT_PID" 2>/dev/null || true
    wait "$HANG_CLIENT_PID" 2>/dev/null || true
  fi
  if [[ -n "$BROKER_PID" ]]; then
    kill "$BROKER_PID" 2>/dev/null || true
    wait "$BROKER_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

STATE="$TMP/state"
WORKSPACES="$TMP/workspaces"
WORKSPACE="$WORKSPACES/GH-321"
FAKE_HOST="$TMP/fake-git-host.py"
BROKER_DIR="$WORKSPACE/Logs/SymphonyGit/.broker"
mkdir -p "$BROKER_DIR/requests" "$BROKER_DIR/acks" "$BROKER_DIR/responses"

cat > "$FAKE_HOST" <<'PY'
#!/usr/bin/env python3
import argparse
import json
import time

parser = argparse.ArgumentParser()
parser.add_argument("--request", required=True)
parser.add_argument("--workspace", required=True)
parser.add_argument("--workspace-root", required=True)
parser.add_argument("--state-root", required=True)
args = parser.parse_args()
request = json.load(open(args.request, encoding="utf-8"))
operation = request["operation"]
if request.get("prTitle") == "hang":
    time.sleep(30)
if operation == "health":
    payload = {"status":"completed","exitCode":0,"operation":"health","issue":321,"branch":"main","head":"abc"}
elif operation == "prepare":
    payload = {"status":"completed","exitCode":0,"operation":"prepare","issue":321,"branch":request["branch"],"head":"abc","created":True}
else:
    payload = {"status":"completed","exitCode":0,"operation":"handoff","issue":321,"branch":request["branch"],"commitSha":"def","pushedSha":"def","prNumber":42,"prUrl":"https://example.invalid/pr/42","leaseRemoved":True}
print(json.dumps(payload, separators=(",", ":")))
PY

cat > "$BROKER_DIR/requests/stale-request.json" <<'EOF'
{"protocolVersion":1,"requestId":"stale-request","operation":"health","issueNumber":321,"branch":"","commitMessage":"","prTitle":"","prBody":"","validationRunIds":[]}
EOF

python3 -u "$ROOT/scripts/git-handoff-broker.py" \
  --workspace-root "$WORKSPACES" \
  --state-root "$STATE" \
  --host-runner "$FAKE_HOST" \
  --poll-ms 25 \
  --command-timeout-seconds 2 \
  --kill-grace-seconds 0.2 \
  >"$TMP/broker.log" 2>&1 &
BROKER_PID=$!

for _ in $(seq 1 80); do
  if [[ -f "$STATE/git-broker/status.json" ]] && jq -e '.state == "ready" and .protocolVersion == 1' "$STATE/git-broker/status.json" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done
jq -e '.state == "ready" and .protocolVersion == 1' "$STATE/git-broker/status.json" >/dev/null

for _ in $(seq 1 40); do
  [[ -f "$BROKER_DIR/responses/stale-request.json" ]] && break
  sleep 0.05
done
jq -e '.status == "StaleRequest" and .exitCode == 89' "$BROKER_DIR/responses/stale-request.json" >/dev/null || {
  echo "git-handoff-broker-test: stale request must fail closed" >&2
  exit 1
}

COMMON_ENV=(
  RPGK_SYMPHONY_WORKSPACE_ROOT="$WORKSPACES"
  RPGK_SUPERVISOR_STATE_ROOT="$STATE"
  RPGK_GIT_BROKER_ACK_TIMEOUT_SECONDS=2
  RPGK_GIT_BROKER_TIMEOUT_SECONDS=5
)

health_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/git-handoff.sh" health --project "$WORKSPACE")"
grep -Fq '"operation":"health"' <<<"$health_output" || { echo "git-handoff-broker-test: health result missing" >&2; exit 1; }

prepare_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/git-handoff.sh" prepare --project "$WORKSPACE" --branch codex/gh-321-test)"
grep -Fq '"branch":"codex/gh-321-test"' <<<"$prepare_output" || { echo "git-handoff-broker-test: prepare result missing" >&2; exit 1; }

handoff_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/git-handoff.sh" handoff --project "$WORKSPACE" --branch codex/gh-321-test --commit-message 'test commit' --pr-title 'test pr' --pr-body 'Closes #321')"
grep -Fq '"prNumber":42' <<<"$handoff_output" || { echo "git-handoff-broker-test: handoff PR result missing" >&2; exit 1; }

jq -e '.lastResult.prNumber == 42 and .lastResult.branch == "codex/gh-321-test"' "$STATE/git-broker/status.json" >/dev/null || {
  echo "git-handoff-broker-test: status must expose last PR handoff" >&2
  exit 1
}

env "${COMMON_ENV[@]}" bash "$ROOT/scripts/git-handoff.sh" handoff \
  --project "$WORKSPACE" \
  --branch codex/gh-321-test \
  --commit-message 'test commit' \
  --pr-title hang \
  --pr-body 'Closes #321' \
  >"$TMP/hang-client.log" 2>&1 &
HANG_CLIENT_PID=$!

for _ in $(seq 1 80); do
  if jq -e '.state == "running" and .activeRequest.operation == "handoff" and .activeRequest.branch == "codex/gh-321-test"' "$STATE/git-broker/status.json" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done
jq -e '.state == "running" and .activeRequest.issue == "GH-321"' "$STATE/git-broker/status.json" >/dev/null || {
  echo "git-handoff-broker-test: active request state missing" >&2
  exit 1
}

set +e
busy_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/git-handoff.sh" health --project "$WORKSPACE" 2>&1)"
busy_status=$?
set -e
[[ "$busy_status" -eq 87 ]] || { echo "git-handoff-broker-test: concurrent request should exit 87, got $busy_status" >&2; exit 1; }
grep -Fq 'HostBusy' <<<"$busy_output" || { echo "git-handoff-broker-test: concurrent request must report HostBusy" >&2; exit 1; }

set +e
wait "$HANG_CLIENT_PID"
hang_status=$?
set -e
HANG_CLIENT_PID=""
[[ "$hang_status" -eq 124 ]] || { echo "git-handoff-broker-test: hung handoff should exit 124, got $hang_status" >&2; exit 1; }
grep -Fq 'TimedOut' "$TMP/hang-client.log" || { echo "git-handoff-broker-test: hung handoff must report TimedOut" >&2; exit 1; }

for _ in $(seq 1 40); do
  if jq -e '.state == "ready" and .lastResult.status == "TimedOut"' "$STATE/git-broker/status.json" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done
jq -e '.state == "ready" and .lastResult.status == "TimedOut"' "$STATE/git-broker/status.json" >/dev/null || {
  echo "git-handoff-broker-test: broker did not recover after timeout" >&2
  exit 1
}

recovery_output="$(env "${COMMON_ENV[@]}" bash "$ROOT/scripts/git-handoff.sh" health --project "$WORKSPACE")"
grep -Fq '"operation":"health"' <<<"$recovery_output" || { echo "git-handoff-broker-test: broker unusable after timeout" >&2; exit 1; }

bash -n "$ROOT/scripts/git-handoff.sh"
python3 -c 'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))' "$ROOT/scripts/git-handoff-broker.py"

echo "git-handoff-broker-test: PASS"
