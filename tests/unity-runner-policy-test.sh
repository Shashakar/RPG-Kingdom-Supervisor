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

echo "unity-runner-policy-test: PASS"
