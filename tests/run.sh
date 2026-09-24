#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

bash "$ROOT/tests/routing-policy-test.sh"
bash "$ROOT/tests/codex-router-test.sh" >/dev/null
bash "$ROOT/tests/codex-concurrency-policy-test.sh"
python3 "$ROOT/tests/codex-capability-policy-test.py"
python3 "$ROOT/tests/first-party-skill-policy-test.py"
bash "$ROOT/tests/codex-permissions-policy-test.sh"
bash "$ROOT/tests/symphony-permissions-compat-test.sh"
bash "$ROOT/tests/symphony-runtime-build-policy-test.sh"
python3 "$ROOT/tests/symphony-usage-limit-transform-test.py"
python3 "$ROOT/tests/symphony-continuation-transform-test.py"
python3 "$ROOT/tests/symphony-skill-roots-transform-test.py"
python3 "$ROOT/tests/continuation-policy-test.py"
python3 "$ROOT/tests/autonomous-plan-test.py"
python3 "$ROOT/tests/autonomous-scheduler-test.py"
python3 "$ROOT/tests/turn-telemetry-test.py"
python3 "$ROOT/tests/halt-diagnosis-test.py"
bash "$ROOT/tests/diagnostics-policy-test.sh"
python3 "$ROOT/tests/codex-usage-snapshot-test.py"
python3 "$ROOT/tests/supervisor-telemetry-test.py"
python3 "$ROOT/tests/supervisor-activity-test.py"
python3 "$ROOT/tests/finished-tasks-test.py"
python3 "$ROOT/tests/supervisor-maintenance-test.py"
python3 "$ROOT/tests/supervisor-detail-test.py"
python3 "$ROOT/tests/supervisor-usage-analysis-test.py"
python3 "$ROOT/tests/codex-concurrent-usage-test.py"
python3 "$ROOT/tests/operations-dashboard-policy-test.py"
bash "$ROOT/tests/worker-lifetime-guard-test.sh" >/dev/null
bash "$ROOT/tests/after-run-guard-test.sh"
bash "$ROOT/tests/report-completion-after-run-test.sh"
python3 "$ROOT/tests/report-completion-reconcile-test.py"
python3 "$ROOT/tests/review-orchestrator-test.py"
python3 "$ROOT/tests/review-orchestrator-service-test.py"
bash "$ROOT/tests/review-orchestrator-watchdog-test.sh"
bash "$ROOT/tests/review-workflow-policy-test.sh"
bash "$ROOT/tests/review-worker-test.sh"
bash "$ROOT/tests/unity-resource-policy-test.sh"
python3 "$ROOT/tests/structural-authoring-preflight-test.py"
bash "$ROOT/tests/structural-authoring-guard-test.sh"
bash "$ROOT/tests/unity-resource-guard-test.sh"
bash "$ROOT/tests/unity-runner-policy-test.sh"
bash "$ROOT/tests/unity-runner-host-syntax-test.sh"
bash "$ROOT/tests/unity-broker-test.sh"
bash "$ROOT/tests/unity-author-broker-test.sh"
bash "$ROOT/tests/unity-author-client-test.sh"
bash "$ROOT/tests/unity-authoring-policy-test.sh"
bash "$ROOT/tests/unity-new-scene-host-test.sh"
python3 "$ROOT/tests/unity-run-history-test.py"
bash "$ROOT/tests/git-handoff-broker-test.sh"
python3 "$ROOT/tests/git-handoff-host-test.py"
python3 "$ROOT/tests/report-complete-host-test.py"
python3 "$ROOT/tests/git-handoff-host-wrapper-test.py"
bash "$ROOT/tests/rearmed-workspace-refresh-test.sh"
bash "$ROOT/tests/rearm-issue-test.sh"
bash "$ROOT/tests/markerless-rearm-refresh-test.sh"
bash "$ROOT/tests/continuation-context-test.sh"
bash "$ROOT/tests/continuation-context-auth-test.sh"
bash "$ROOT/tests/continuation-context-policy-test.sh"
python3 -m py_compile \
  "$ROOT/scripts/autonomous-plan.py" \
  "$ROOT/scripts/autonomous-scheduler.py" \
  "$ROOT/scripts/codex-capability-policy.py" \
  "$ROOT/scripts/finished_tasks.py" \
  "$ROOT/scripts/first-party-skill-policy.py" \
  "$ROOT/scripts/codex-usage-snapshot.py" \
  "$ROOT/scripts/continuation-policy.py" \
  "$ROOT/scripts/turn-telemetry.py" \
  "$ROOT/scripts/worker-status.py" \
  "$ROOT/scripts/halt-diagnosis.py" \
  "$ROOT/scripts/git-handoff-host-wrapper.py" \
  "$ROOT/scripts/patch-symphony-continuation-policy.py" \
  "$ROOT/scripts/patch-symphony-skill-roots.py" \
  "$ROOT/scripts/patch-symphony-usage-limit.py" \
  "$ROOT/scripts/queue-agent-review.py" \
  "$ROOT/scripts/reconcile-report-completion.py" \
  "$ROOT/scripts/report-complete-host.py" \
  "$ROOT/scripts/review-orchestrator.py" \
  "$ROOT/scripts/review-orchestrator-service.py" \
  "$ROOT/scripts/review_state.py" \
  "$ROOT/scripts/structural-authoring-preflight.py" \
  "$ROOT/scripts/supervisor_activity.py" \
  "$ROOT/scripts/supervisor_dashboard.py" \
  "$ROOT/scripts/supervisor_detail.py" \
  "$ROOT/scripts/supervisor_maintenance.py" \
  "$ROOT/scripts/supervisor_telemetry.py" \
  "$ROOT/scripts/supervisor_usage_analysis.py" \
  "$ROOT/scripts/unity-author-broker.py" \
  "$ROOT/scripts/unity_run_history.py"
bash -n "$ROOT/scripts/after-run-guard.sh"
bash -n "$ROOT/scripts/before-run-guard.sh"
bash -n "$ROOT/scripts/codex-app-server-router.sh"
bash -n "$ROOT/scripts/codex-concurrency-policy.sh"
bash -n "$ROOT/scripts/git-handoff.sh"
bash -n "$ROOT/scripts/review-worker.sh"
bash -n "$ROOT/scripts/review-orchestrator-watchdog.sh"
bash -n "$ROOT/scripts/refresh-rearmed-workspace.sh"
bash -n "$ROOT/scripts/run-symphony.sh"
bash -n "$ROOT/scripts/unity-author.sh"
bash -n "$ROOT/scripts/unity-author-host.sh"
bash -n "$ROOT/scripts/unity-resource-guard.sh"
python3 -m json.tool "$ROOT/schemas/review-verdict.schema.json" >/dev/null

echo "supervisor-tests: PASS"
