#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW="$ROOT/WORKFLOW.md"
LABELS="$ROOT/scripts/install-labels.sh"

# Assert semantic policy markers without coupling the regression to Markdown punctuation.
grep -Fq '{% elsif issue.labels contains "authoring:scene-existing-composition" %}' "$WORKFLOW"
grep -Fq 'timeout_ms: 600000' "$WORKFLOW" || {
  echo "workflow-policy-alignment: hooks.timeout_ms must allow real Unity preflight to exceed Symphony's 60s default" >&2
  exit 1
}
grep -Fq 'existing-scene-composition' "$WORKFLOW"
grep -Eq 'risk:end-to-end.*GPT-6 Sol / high reasoning' "$WORKFLOW"
grep -Eq 'risk:end-to-end.*defaults to GPT-6 Sol / high' "$LABELS"

echo "workflow-policy-alignment: PASS"
