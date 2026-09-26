#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW="$ROOT/WORKFLOW.md"
LABELS="$ROOT/scripts/install-labels.sh"

grep -Fq '{% elsif issue.labels contains "authoring:scene-existing-composition" %}' "$WORKFLOW"
grep -Fq 'tier: `"existing-scene-composition"`' "$WORKFLOW"
grep -Fq -- '- `risk:end-to-end` -> GPT-6 Sol / high reasoning.' "$WORKFLOW"
if grep -Fq -- '- `risk:end-to-end` -> GPT-6 Astra / medium reasoning.' "$WORKFLOW"; then
  echo "workflow-policy-alignment: stale Astra end-to-end route remains" >&2
  exit 1
fi
grep -Fq 'risk:end-to-end" "5319E7" "Hardest end-to-end execution; defaults to GPT-6 Sol / high; Astra requires explicit escalation"' "$LABELS"

echo "workflow-policy-alignment: PASS"
