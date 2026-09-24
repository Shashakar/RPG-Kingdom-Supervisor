#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin" "$TMP/state/locks/unity-editor.lock" "$TMP/state/authoring" "$TMP/workspaces/GH-105"
GH="$TMP/workspaces/GH-105"
PS_AUTHOR="$ROOT/scripts/windows/run-unity-authoring.ps1"

# Iterative composition has a single target scene, so PowerShell may unwrap
# $baseExpected to a scalar. Normalize both operands before concatenating
# generated NavMesh assets; otherwise valid bake manifests fail with exit 92.
grep -Fq '$expected = @(@($baseExpected) + @($generatedCopyBackAssets) | Sort-Object)' "$PS_AUTHOR"
grep -Fq '$copyBackAssets = @(@($baseExpected) + @($generatedCopyBackAssets))' "$PS_AUTHOR"

cat > "$TMP/bin/wslpath" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "-w" ]] && shift
printf '%s\n' "$1"
SH
chmod +x "$TMP/bin/wslpath"

cat > "$TMP/bin/fake-powershell" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${FAKE_POWERSHELL_CALLS:?}"
printf '{"protocolVersion":1,"success":true,"scene":"Assets/RPGKingdom/Scenes/PlaytestScene.unity","changedAssets":["Assets/RPGKingdom/Scenes/PlaytestScene.unity"],"appliedOperations":["fake"],"error":null}\n'
SH
chmod +x "$TMP/bin/fake-powershell"

mkdir -p "$GH/Assets/RPGKingdom/Scenes" "$GH/ProjectSettings" "$GH/Logs/SymphonyUnity/.author-broker/requests"
printf 'm_EditorVersion: 6000.3.10f1\n' > "$GH/ProjectSettings/ProjectVersion.txt"
printf 'source\n' > "$GH/Assets/RPGKingdom/Scenes/VerticalSlice.unity"
printf 'existing\n' > "$GH/Assets/RPGKingdom/Scenes/AlreadyMain.unity"

git -C "$GH" init -q
git -C "$GH" config user.email test@example.com
git -C "$GH" config user.name test
git -C "$GH" add .
git -C "$GH" commit -qm initial
git -C "$GH" branch -M main
git -C "$GH" update-ref refs/remotes/origin/main HEAD
git -C "$GH" checkout -qb codex/gh-105-new-scene

printf 'GH-105\n' > "$TMP/state/locks/unity-editor.lock/owner"
printf '%s\n' "$GH" > "$TMP/state/locks/unity-editor.lock/workspace"
printf '{"protocolVersion":1,"issue":"GH-105","workspace":"%s","tier":"new-scene-composition"}\n' "$GH" > "$TMP/state/authoring/GH-105.json"

export PATH="$TMP/bin:$PATH"
export RPGK_POWERSHELL_EXE="$TMP/bin/fake-powershell"
export RPGK_SUPERVISOR_STATE_ROOT="$TMP/state"
export RPGK_UNITY_EDITOR_WINDOWS='C:\Fake\Unity.exe'
export FAKE_POWERSHELL_CALLS="$TMP/powershell-calls"

request="$GH/Logs/SymphonyUnity/.author-broker/requests/initial.json"
cat > "$request" <<'JSON'
{"protocolVersion":1,"requestId":"initial","operation":"author","authoring":{"protocolVersion":1,"tier":"new-scene-composition","sourceScene":"Assets/RPGKingdom/Scenes/VerticalSlice.unity","scene":"Assets/RPGKingdom/Scenes/PlaytestScene.unity","operations":[{"kind":"set-transform","objectPath":"World","localPosition":{"x":0,"y":0,"z":0},"localEulerAngles":{"x":0,"y":0,"z":0},"localScale":{"x":1,"y":1,"z":1}}]}}
JSON

bash "$ROOT/scripts/unity-author-host.sh" --project "$GH" --request "$request" >/dev/null
jq -e '.issue == "GH-105" and .sourceScene == "Assets/RPGKingdom/Scenes/VerticalSlice.unity" and .targetScene == "Assets/RPGKingdom/Scenes/PlaytestScene.unity" and .branch == "codex/gh-105-new-scene"' "$TMP/state/new-scene-provenance/GH-105.json" >/dev/null
[[ "$(wc -l < "$FAKE_POWERSHELL_CALLS")" -eq 1 ]]

# Simulate successful Windows copy-back for the iterative host provenance checks.
printf 'playtest\n' > "$GH/Assets/RPGKingdom/Scenes/PlaytestScene.unity"
printf 'fileFormatVersion: 2\n' > "$GH/Assets/RPGKingdom/Scenes/PlaytestScene.unity.meta"

request="$GH/Logs/SymphonyUnity/.author-broker/requests/iterative.json"
cat > "$request" <<'JSON'
{"protocolVersion":1,"requestId":"iterative","operation":"author","authoring":{"protocolVersion":1,"tier":"new-scene-composition","scene":"Assets/RPGKingdom/Scenes/PlaytestScene.unity","operations":[{"kind":"delete-object","objectPath":"World/TestOnly"}]}}
JSON
bash "$ROOT/scripts/unity-author-host.sh" --project "$GH" --request "$request" >/dev/null
[[ "$(wc -l < "$FAKE_POWERSHELL_CALLS")" -eq 2 ]]
grep -q -- '-CompositionMode iterative' "$FAKE_POWERSHELL_CALLS"

# A later request cannot switch to another target.
request="$GH/Logs/SymphonyUnity/.author-broker/requests/wrong-target.json"
cat > "$request" <<'JSON'
{"protocolVersion":1,"requestId":"wrong-target","operation":"author","authoring":{"protocolVersion":1,"tier":"new-scene-composition","scene":"Assets/RPGKingdom/Scenes/OtherScene.unity","operations":[{"kind":"delete-object","objectPath":"World/TestOnly"}]}}
JSON
set +e
bash "$ROOT/scripts/unity-author-host.sh" --project "$GH" --request "$request" >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 83 ]] || { echo "expected provenance mismatch exit 83, got $status" >&2; exit 1; }
[[ "$(wc -l < "$FAKE_POWERSHELL_CALLS")" -eq 2 ]]

# New-scene composition can never target a scene already present on origin/main.
rm -f "$TMP/state/new-scene-provenance/GH-105.json"
request="$GH/Logs/SymphonyUnity/.author-broker/requests/main-target.json"
cat > "$request" <<'JSON'
{"protocolVersion":1,"requestId":"main-target","operation":"author","authoring":{"protocolVersion":1,"tier":"new-scene-composition","sourceScene":"Assets/RPGKingdom/Scenes/VerticalSlice.unity","scene":"Assets/RPGKingdom/Scenes/AlreadyMain.unity","operations":[{"kind":"delete-object","objectPath":"World/TestOnly"}]}}
JSON
set +e
bash "$ROOT/scripts/unity-author-host.sh" --project "$GH" --request "$request" >/dev/null 2>&1
status=$?
set -e
[[ "$status" -eq 83 ]] || { echo "expected origin/main protection exit 83, got $status" >&2; exit 1; }
[[ "$(wc -l < "$FAKE_POWERSHELL_CALLS")" -eq 2 ]]

echo "unity-new-scene-host-test: PASS"
