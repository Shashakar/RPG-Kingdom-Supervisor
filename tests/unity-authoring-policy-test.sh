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
grep -q 'authoring:scene-new-composition' "$ROOT/scripts/install-labels.sh"
grep -q 'authoring_tier="mechanical"' "$ROOT/scripts/unity-resource-guard.sh"
grep -q 'authoring_tier="mechanical-structural"' "$ROOT/scripts/unity-resource-guard.sh"
grep -q 'authoring_tier="new-scene-composition"' "$ROOT/scripts/unity-resource-guard.sh"
grep -q 'requested_tier' "$ROOT/scripts/unity-author-host.sh"
grep -q 'mechanical-structural' "$ROOT/scripts/unity-author.sh"
grep -q 'SUPPORTED_TIERS' "$ROOT/scripts/unity-author-broker.py"
grep -q 'mechanical-structural' "$ROOT/scripts/windows/run-unity-authoring.ps1"
grep -q 'new-scene-composition' "$ROOT/scripts/windows/run-unity-authoring.ps1"
grep -q 'sourceHashBefore' "$ROOT/scripts/windows/run-unity-authoring.ps1"
grep -q 'copiedBackAssets' "$ROOT/scripts/windows/run-unity-authoring.ps1"
grep -q 'executor changed-assets evidence does not exactly match' "$ROOT/scripts/windows/run-unity-authoring.ps1"
grep -q 'Assets/RPGKingdom/Navigation/Generated/' "$ROOT/scripts/windows/run-unity-authoring.ps1"
grep -q 'generatedAssets' "$ROOT/scripts/windows/run-unity-authoring.ps1"
grep -q 'Publish-AssetsAtomically' "$ROOT/scripts/windows/run-unity-authoring.ps1"
grep -q 'new-scene executor changed-assets evidence must exactly match' "$ROOT/scripts/windows/run-unity-authoring.ps1"
grep -q 'generated assets are only supported for new-scene composition' "$ROOT/scripts/windows/run-unity-authoring.ps1"
grep -q 'new-scene-provenance' "$ROOT/scripts/unity-author-host.sh"
grep -q 'SymphonyMechanicalSceneAuthoring.ApplyFromCommandLine' "$ROOT/scripts/windows/run-unity-authoring.ps1"

grep -q '/\.symphony-worker-status.json' <(cat "$ROOT/scripts/before-run-guard.sh"; true) || \
  grep -q 'WORKER_STATUS="\.symphony-worker-status.json"' "$ROOT/scripts/before-run-guard.sh"

bash -n "$ROOT/scripts/unity-author.sh"
bash -n "$ROOT/scripts/unity-author-host.sh"
bash -n "$ROOT/scripts/unity-resource-guard.sh"
bash -n "$ROOT/scripts/run-symphony.sh"
python3 -m py_compile "$ROOT/scripts/unity-author-broker.py"

echo "unity-authoring-policy-test: PASS"
