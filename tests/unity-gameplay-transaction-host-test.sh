#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
GH="$TMP/workspaces/GH-255"
STATE="$TMP/state"
SCENE="Assets/RPGKingdom/Scenes/PlaytestScene.unity"
mkdir -p "$TMP/bin" "$STATE/locks/unity-editor.lock" "$STATE/authoring" \
  "$GH/Assets/RPGKingdom/Scenes" "$GH/ProjectSettings" \
  "$GH/Logs/SymphonyUnity/.author-broker/requests"
echo 'm_EditorVersion: 6000.3.10f1' > "$GH/ProjectSettings/ProjectVersion.txt"
echo scene > "$GH/$SCENE"
git -C "$GH" init -q -b codex/gh-255-opening
git -C "$GH" -c user.name=Test -c user.email=test@example.com add .
git -C "$GH" -c user.name=Test -c user.email=test@example.com commit -qm baseline
REV="$(git -C "$GH" rev-parse HEAD)"
printf 'GH-255\n' > "$STATE/locks/unity-editor.lock/owner"
printf '%s\n' "$GH" > "$STATE/locks/unity-editor.lock/workspace"

python3 - "$STATE/authoring/GH-255.json" "$GH" "$REV" <<'PY'
import json,sys
path,work,rev=sys.argv[1:]
json.dump({
    "protocolVersion":1,"issue":"GH-255","workspace":work,
    "branch":"codex/gh-255-opening","executorRevision":rev,
    "tier":"opening-encounter-composition",
    "scene":"Assets/RPGKingdom/Scenes/PlaytestScene.unity",
    "operations":["compose-authored-opening-encounter"],
    "allowedRoots":[
        "World/TownArea/PlayerSpawnPoint",
        "World/TownArea/Enemy_FirstApproach_Scavenger",
        "Systems/Encounter_FirstApproach",
        "PlayerCharacter","Systems/SaveLoadSystem","Systems"
    ]
},open(path,"w"))
PY
cat > "$TMP/bin/wslpath" <<'SH'
#!/usr/bin/env bash
[[ "${1:-}" == "-w" ]] && shift
printf '%s\n' "$1"
SH
cat > "$TMP/bin/fake-powershell" <<'SH'
#!/usr/bin/env bash
echo called >> "${FAKE_POWERSHELL_CALLS:?}"
SH
chmod +x "$TMP/bin/"*
export PATH="$TMP/bin:$PATH"
export RPGK_POWERSHELL_EXE="$TMP/bin/fake-powershell"
export RPGK_SUPERVISOR_STATE_ROOT="$STATE"
export RPGK_UNITY_EDITOR_WINDOWS='C:\Fake\Unity.exe'
export FAKE_POWERSHELL_CALLS="$TMP/calls"
REQUEST="$GH/Logs/SymphonyUnity/.author-broker/requests/tx.json"
cat > "$REQUEST" <<'JSON'
{"protocolVersion":1,"requestId":"tx","operation":"author","authoring":{"protocolVersion":1,"tier":"opening-encounter-composition","scene":"Assets/RPGKingdom/Scenes/PlaytestScene.unity","operations":[{"kind":"compose-authored-opening-encounter","openingSpawnPosition":{"x":0,"y":0,"z":-23},"openingEnemyPosition":{"x":0,"y":0,"z":-13},"openingSpawnYaw":0,"openingEnemyYaw":0}]}}
JSON
invoke() { bash "$ROOT/scripts/unity-author-host.sh" --project "$GH" --request "$REQUEST" >/dev/null 2>&1; }
invoke
[[ "$(wc -l < "$FAKE_POWERSHELL_CALLS")" -eq 1 ]] || { echo 'approved request did not reach host runner' >&2; exit 1; }
deny() {
  local reason="$1"
  set +e
  invoke
  code=$?
  set -e
  [[ "$code" -eq 83 ]] || { echo "expected $reason denial 83; got $code" >&2; exit 1; }
  [[ "$(wc -l < "$FAKE_POWERSHELL_CALLS")" -eq 1 ]] || { echo 'denied request launched Windows runner' >&2; exit 1; }
}
# Source revision must match host receipt.
python3 - "$STATE/authoring/GH-255.json" <<'PY'
import json,sys
p=sys.argv[1]; x=json.load(open(p)); x["executorRevision"]="0"*40; json.dump(x,open(p,"w"))
PY
deny "wrong revision"
python3 - "$STATE/authoring/GH-255.json" "$REV" <<'PY'
import json,sys
p,rev=sys.argv[1:]; x=json.load(open(p)); x["executorRevision"]=rev; json.dump(x,open(p,"w"))
PY
# Worker-supplied scripts are not eligible to run during an approved transaction.
mkdir -p "$GH/Assets/RPGKingdom/Editor"
echo 'class Injected {}' > "$GH/Assets/RPGKingdom/Editor/Injected.cs"
deny "untracked script"
rm "$GH/Assets/RPGKingdom/Editor/Injected.cs"
python3 - "$REQUEST" <<'PY'
import json,sys
p=sys.argv[1]; x=json.load(open(p)); x["authoring"]["scene"]="Assets/RPGKingdom/Scenes/Other.unity"; json.dump(x,open(p,"w"))
PY
deny "wrong scene"
echo "unity-gameplay-transaction-host-test: PASS"
