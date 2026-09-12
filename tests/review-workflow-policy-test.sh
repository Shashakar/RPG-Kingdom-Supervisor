#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for file in \
  "$ROOT/scripts/review-orchestrator.py" \
  "$ROOT/scripts/review-orchestrator-service.py" \
  "$ROOT/scripts/review-orchestrator-watchdog.sh" \
  "$ROOT/scripts/review-worker.sh" \
  "$ROOT/scripts/run-symphony.sh" \
  "$ROOT/scripts/codex-app-server-router.sh" \
  "$ROOT/scripts/after-run-guard.sh" \
  "$ROOT/scripts/install-labels.sh" \
  "$ROOT/schemas/review-verdict.schema.json"; do
  [[ -f "$file" ]] || { echo "review-workflow-policy-test: missing $file" >&2; exit 1; }
done

grep -Fq 'review-orchestrator-service.py' "$ROOT/scripts/run-symphony.sh"
grep -Fq 'review-orchestrator-watchdog.sh' "$ROOT/scripts/run-symphony.sh"
grep -Fq 'review-orchestrator.py' "$ROOT/scripts/review-orchestrator-service.py"
grep -Fq 'codex-session.lock' "$ROOT/scripts/codex-app-server-router.sh"
grep -Fq 'codex-session.lock' "$ROOT/scripts/review-worker.sh"
grep -Fq -- '--sandbox read-only' "$ROOT/scripts/review-worker.sh"
grep -Fq -- '--output-schema' "$ROOT/scripts/review-worker.sh"
grep -Fq 'symphony:agent-review' "$ROOT/scripts/after-run-guard.sh"
grep -Fq 'symphony:human-review' "$ROOT/scripts/review-orchestrator.py"
grep -Fq 'symphony:human-attention' "$ROOT/scripts/review-orchestrator.py"
grep -Fq 'symphony:rearm' "$ROOT/scripts/review-orchestrator.py"
grep -Fq 'RPGK_MAX_AUTOMATIC_REPAIRS' "$ROOT/scripts/review-orchestrator.py"

for label in symphony:agent-review symphony:rework symphony:human-review symphony:human-attention repair-route:luna repair-route:terra repair-route:sol repair-route:astra; do
  grep -Fq "create_label \"$label\"" "$ROOT/scripts/install-labels.sh" || {
    echo "review-workflow-policy-test: missing installed label $label" >&2
    exit 1
  }
done

if grep -Eq '/merge|merge_pull|gh pr merge' "$ROOT/scripts/review-orchestrator.py"; then
  echo "review-workflow-policy-test: automated review orchestrator must not contain a merge path" >&2
  exit 1
fi

echo "review-workflow-policy-test: PASS"
