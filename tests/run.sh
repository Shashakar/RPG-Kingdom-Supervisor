#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

bash "$ROOT/tests/routing-policy-test.sh"
bash "$ROOT/tests/codex-router-test.sh" >/dev/null
bash "$ROOT/tests/codex-permissions-policy-test.sh"
bash "$ROOT/tests/symphony-permissions-compat-test.sh"
python3 "$ROOT/tests/symphony-usage-limit-transform-test.py"
bash "$ROOT/tests/diagnostics-policy-test.sh"
bash "$ROOT/tests/worker-lifetime-guard-test.sh" >/dev/null
bash "$ROOT/tests/after-run-guard-test.sh"
python3 "$ROOT/tests/review-orchestrator-test.py"
python3 "$ROOT/tests/review-orchestrator-service-test.py"
bash "$ROOT/tests/review-workflow-policy-test.sh"
bash "$ROOT/tests/review-worker-test.sh"
bash "$ROOT/tests/unity-resource-policy-test.sh"
bash "$ROOT/tests/unity-resource-guard-test.sh"
bash "$ROOT/tests/unity-runner-policy-test.sh"
bash "$ROOT/tests/unity-runner-host-syntax-test.sh"
bash "$ROOT/tests/unity-broker-test.sh"
python3 "$ROOT/tests/unity-run-history-test.py"
bash "$ROOT/tests/git-handoff-broker-test.sh"
python3 "$ROOT/tests/git-handoff-host-test.py"
python3 "$ROOT/tests/git-handoff-host-wrapper-test.py"
bash "$ROOT/tests/rearm-issue-test.sh"
bash "$ROOT/tests/continuation-context-test.sh"
bash "$ROOT/tests/continuation-context-auth-test.sh"
bash "$ROOT/tests/continuation-context-policy-test.sh"
python3 -m py_compile \
  "$ROOT/scripts/git-handoff-host-wrapper.py" \
  "$ROOT/scripts/patch-symphony-usage-limit.py" \
  "$ROOT/scripts/queue-agent-review.py" \
  "$ROOT/scripts/review-orchestrator.py" \
  "$ROOT/scripts/review-orchestrator-service.py" \
  "$ROOT/scripts/review_state.py" \
  "$ROOT/scripts/unity_run_history.py" \
  "$ROOT/scripts/supervisor_dashboard.py"
bash -n "$ROOT/scripts/after-run-guard.sh"
bash -n "$ROOT/scripts/review-worker.sh"
bash -n "$ROOT/scripts/refresh-rearmed-workspace.sh"
bash -n "$ROOT/scripts/run-symphony.sh"
python3 -m json.tool "$ROOT/schemas/review-verdict.schema.json" >/dev/null

echo "supervisor-tests: PASS"
