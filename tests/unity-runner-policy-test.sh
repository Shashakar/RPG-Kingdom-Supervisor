#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../scripts/unity-runner-policy.sh
source "$ROOT/scripts/unity-runner-policy.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/ProjectSettings"
cat > "$TMP/ProjectSettings/ProjectVersion.txt" <<'EOF'
m_EditorVersion: 6000.3.10f1
m_EditorVersionWithRevision: 6000.3.10f1 (e35f0c77bd8e)
EOF

[[ "$(rpgk_project_unity_version "$TMP")" == "6000.3.10f1" ]] || { echo "failed to parse Unity version" >&2; exit 1; }
[[ "$(rpgk_default_unity_editor_windows 6000.3.10f1)" == 'C:\Program Files\Unity\Hub\Editor\6000.3.10f1\Editor\Unity.exe' ]] || { echo "unexpected default Unity path" >&2; exit 1; }
[[ "$(rpgk_normalize_test_platform editmode)" == "EditMode" ]] || { echo "editmode normalization failed" >&2; exit 1; }
[[ "$(rpgk_normalize_test_platform PlayMode)" == "PlayMode" ]] || { echo "playmode normalization failed" >&2; exit 1; }

set +e
rpgk_normalize_test_platform nonsense >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 32 ]] || { echo "invalid platform should exit 32, got $status" >&2; exit 1; }

bash -n "$ROOT/scripts/unity-runner.sh"

# Production runner and broker must reject experimental staged-editor operations.
# Rejection happens before creating a broker request or starting a Unity process.
for operation in editor-scene-summary editor-stage-benchmark editor-staged-code editor-staged-preflight; do
  set +e
  bash "$ROOT/scripts/unity-runner.sh" "$operation" \
    --project "$TMP" --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity \
    > "$TMP/runner-stdout" 2> "$TMP/runner-stderr"
  result=$?
  set -e
  [[ "$result" -eq 64 ]] || {
    echo "unsupported $operation must be rejected by production runner (got $result)" >&2
    exit 1
  }
  grep -q "Unknown Unity runner command" "$TMP/runner-stderr" || {
    echo "unsupported $operation did not fail at the runner allowlist" >&2
    exit 1
  }
done

python3 - "$ROOT/scripts/unity-host-broker.py" <<'PY'
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("unity_broker_contract_check", path)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.ALLOWED_OPERATIONS == {"health", "editmode", "playmode", "capture", "inspect"}
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    workspace_root = root / "workspaces"
    workspace = workspace_root / "GH-321"
    request_dir = workspace / "Logs/SymphonyUnity/.broker/requests"
    request_dir.mkdir(parents=True)
    state_root = root / "state"
    lock = state_root / "locks/unity-editor.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text("GH-321\\n")
    (lock / "workspace").write_text(str(workspace) + "\\n")
    for operation in ("editor-scene-summary", "editor-stage-benchmark",
                      "editor-staged-code", "editor-staged-preflight"):
        request = request_dir / (operation + ".json")
        request.write_text(json.dumps({
            "protocolVersion": 1,
            "requestId": operation,
            "operation": operation,
            "scenePath": "Assets/RPGKingdom/Scenes/PlaytestScene.unity",
        }))
        denied = module.prepare_request(request, workspace_root.resolve(), state_root)
        assert isinstance(denied, dict), operation
        assert denied["exitCode"] == 64, (operation, denied)
PY

echo "unity-runner-policy-test: PASS"
