#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/state/locks/unity-editor.lock" "$TMP/state/authoring" "$TMP/workspaces/GH-119/Assets/RPGKingdom/Scenes" "$TMP/workspaces/GH-119/ProjectSettings" "$TMP/workspaces/GH-119/Logs/SymphonyUnity/.author-broker/requests"
GH="$TMP/workspaces/GH-119"
printf 'm_EditorVersion: 6000.3.10f1\n' > "$GH/ProjectSettings/ProjectVersion.txt"
printf 'scene\n' > "$GH/Assets/RPGKingdom/Scenes/PlaytestScene.unity"
printf 'GH-119\n' > "$TMP/state/locks/unity-editor.lock/owner"
printf '%s\n' "$GH" > "$TMP/state/locks/unity-editor.lock/workspace"
printf '{"protocolVersion":1,"issue":"GH-119","workspace":"%s","tier":"existing-scene-composition","scene":"Assets/RPGKingdom/Scenes/PlaytestScene.unity"}\n' "$GH" > "$TMP/state/authoring/GH-119.json"

cat > "$TMP/bin/wslpath" <<'SH'
#!/usr/bin/env bash
[[ "${1:-}" == "-w" ]] && shift
printf '%s\n' "$1"
SH
cat > "$TMP/bin/fake-powershell" <<'SH'
#!/usr/bin/env bash
echo x >> "${FAKE_POWERSHELL_CALLS:?}"
printf '{"protocolVersion":1,"success":true,"scene":"Assets/RPGKingdom/Scenes/PlaytestScene.unity","changedAssets":["Assets/RPGKingdom/Scenes/PlaytestScene.unity"],"generatedNavigationAssets":[],"appliedOperations":["fake"],"error":null}\n'
SH
chmod +x "$TMP/bin/wslpath" "$TMP/bin/fake-powershell"
export PATH="$TMP/bin:$PATH"
export RPGK_POWERSHELL_EXE="$TMP/bin/fake-powershell"
export RPGK_SUPERVISOR_STATE_ROOT="$TMP/state"
export RPGK_UNITY_EDITOR_WINDOWS='C:\Fake\Unity.exe'
export FAKE_POWERSHELL_CALLS="$TMP/calls"

request="$GH/Logs/SymphonyUnity/.author-broker/requests/ok.json"
cat > "$request" <<'JSON'
{"protocolVersion":1,"requestId":"ok","operation":"author","authoring":{"protocolVersion":1,"tier":"existing-scene-composition","scene":"Assets/RPGKingdom/Scenes/PlaytestScene.unity","operations":[{"kind":"set-transform","objectPath":"World","localPosition":{"x":0,"y":0,"z":0},"localEulerAngles":{"x":0,"y":0,"z":0},"localScale":{"x":1,"y":1,"z":1}}]}}
JSON
bash "$ROOT/scripts/unity-author-host.sh" --project "$GH" --request "$request" >/dev/null
[[ "$(wc -l < "$FAKE_POWERSHELL_CALLS")" -eq 1 ]]

wrong="$GH/Logs/SymphonyUnity/.author-broker/requests/wrong.json"
cat > "$wrong" <<'JSON'
{"protocolVersion":1,"requestId":"wrong","operation":"author","authoring":{"protocolVersion":1,"tier":"existing-scene-composition","scene":"Assets/RPGKingdom/Scenes/VerticalSlice.unity","operations":[{"kind":"set-transform","objectPath":"World","localPosition":{"x":0,"y":0,"z":0},"localEulerAngles":{"x":0,"y":0,"z":0},"localScale":{"x":1,"y":1,"z":1}}]}}
JSON
set +e
bash "$ROOT/scripts/unity-author-host.sh" --project "$GH" --request "$wrong" >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 83 ]] || { echo "expected exact-scene authorization rejection, got $status" >&2; exit 1; }
[[ "$(wc -l < "$FAKE_POWERSHELL_CALLS")" -eq 1 ]]
echo "unity-existing-scene-host-test: PASS"
