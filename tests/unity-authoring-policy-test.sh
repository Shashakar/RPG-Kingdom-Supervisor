#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for file in \
  "$ROOT/scripts/unity-author.sh" \
  "$ROOT/scripts/unity-author-host.sh" \
  "$ROOT/scripts/unity-resource-guard.sh" \
  "$ROOT/scripts/windows/run-unity-authoring.ps1"; do
  [[ -f "$file" ]] || { echo "missing authoring file: $file" >&2; exit 1; }
done

grep -q 'authoring:scene-mechanical' "$ROOT/scripts/install-labels.sh"
grep -q 'authoring:scene-structural' "$ROOT/scripts/install-labels.sh"
grep -q 'recorded.*scene-authoring authority' "$ROOT/scripts/unity-resource-guard.sh"
grep -q 'tier == "mechanical"' "$ROOT/scripts/unity-author-host.sh"
grep -q '\.author-broker' "$ROOT/scripts/unity-author.sh"
grep -q 'only Tier-1.*mechanical' "$ROOT/scripts/unity-author-broker.py"
grep -q 'executor changed-assets evidence does not exactly match' "$ROOT/scripts/windows/run-unity-authoring.ps1"
grep -q 'SourceTemp' "$ROOT/scripts/windows/run-unity-authoring.ps1"
grep -q 'Move-Item -LiteralPath \$SourceTemp -Destination \$SourceScene -Force' "$ROOT/scripts/windows/run-unity-authoring.ps1"
grep -q 'SymphonyMechanicalSceneAuthoring.ApplyFromCommandLine' "$ROOT/scripts/windows/run-unity-authoring.ps1"

grep -q '/\.symphony-worker-status.json' <(cat "$ROOT/scripts/before-run-guard.sh"; true) || \
  grep -q 'WORKER_STATUS="\.symphony-worker-status.json"' "$ROOT/scripts/before-run-guard.sh"

bash -n "$ROOT/scripts/unity-author.sh"
bash -n "$ROOT/scripts/unity-author-host.sh"
bash -n "$ROOT/scripts/unity-resource-guard.sh"
bash -n "$ROOT/scripts/run-symphony.sh"
python3 -m py_compile "$ROOT/scripts/unity-author-broker.py"

echo "unity-authoring-policy-test: PASS"
