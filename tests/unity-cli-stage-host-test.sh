#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAPPER="$ROOT/scripts/unity-cli-stage.sh"
WINDOWS="$ROOT/scripts/windows/run-unity-cli-stage.ps1"
temp="$(mktemp -d)"
trap 'rm -rf "$temp"' EXIT
mkdir -p "$temp/bin" "$temp/src/Assets" "$temp/src/Packages" "$temp/src/ProjectSettings"

cat > "$temp/bin/wslpath" <<'EOF'
#!/usr/bin/env bash
[[ "$1" == "-w" ]] || exit 90
printf 'C:\\NTFS Pilot\\Project\n'
EOF
cat > "$temp/bin/powershell.exe" <<'EOF'
#!/usr/bin/env bash
printf '<%s>\n' "$@" > "$RPGK_STAGE_ARGS"
echo '{"schemaVersion":1,"status":"mock-ok"}'
EOF
chmod +x "$temp/bin/wslpath" "$temp/bin/powershell.exe"
export PATH="$temp/bin:$PATH"
export RPGK_STAGE_ARGS="$temp/arguments"
bash -n "$WRAPPER"

bash "$WRAPPER" start --source "$temp/src" > "$temp/output"
grep -Fq '"status":"mock-ok"' "$temp/output"
grep -Fxq '<-Action>' "$temp/arguments"
grep -Fxq '<start>' "$temp/arguments"
grep -Fxq '<-SourceProjectPath>' "$temp/arguments"
grep -Fxq '<C:\NTFS Pilot\Project>' "$temp/arguments"
! grep -Fxq '<-SessionPath>' "$temp/arguments"
bash "$WRAPPER" start --source "$temp/src" --editor-windows 'D:\\Unity\\Hub\\Editor\\6000.3.10f1\\Editor\\Unity.exe' >/dev/null
grep -Fxq '<-EditorPath>' "$temp/arguments"
grep -Fxq '<D:\\Unity\\Hub\\Editor\\6000.3.10f1\\Editor\\Unity.exe>' "$temp/arguments"

for action in status discover install-pipeline stop; do
  bash "$WRAPPER" "$action" --session-windows 'C:\Users\Tester\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-123' >/dev/null
  grep -Fxq "<$action>" "$temp/arguments"
  grep -Fxq '<-SessionPath>' "$temp/arguments"
  grep -Fxq '<C:\Users\Tester\AppData\Local\RPGKingdomSupervisor\UnityCliPilots\pilot-123>' "$temp/arguments"
  ! grep -Fxq '<-SourceProjectPath>' "$temp/arguments"
done

expect_failure() {
  if bash "$WRAPPER" "$@" > /dev/null 2>&1; then
    echo "FAIL: accepted invalid wrapper call: $*" >&2
    exit 1
  fi
}
expect_failure start
expect_failure discover
expect_failure edit-scene --session-windows 'C:\Temp\stage'
expect_failure start --source "$temp/src" --session-windows 'C:\Temp\stage'
expect_failure install-pipeline --session-windows '\\wsl.localhost\Ubuntu\project'
expect_failure discover --source "$temp/src" --session-windows 'C:\Temp\stage'
expect_failure stop --session-windows 'C:\Temp\stage' --unsafe
expect_failure discover --session-windows 'C:\Temp\stage' --editor-windows 'D:\Unity\Editor\Unity.exe'
expect_failure start --source "$temp/nope"
export RPGK_SUPERVISOR_STATE_ROOT="$temp/supervisor-state"
mkdir -p "$RPGK_SUPERVISOR_STATE_ROOT/locks/unity-editor.lock"
expect_failure start --source "$temp/src"
expect_failure discover --session-windows 'C:\\NTFS Pilot\\pilot-123'
rm -rf "$RPGK_SUPERVISOR_STATE_ROOT/locks/unity-editor.lock"

# Pin explicit host execution boundaries; Stage B never becomes a worker
# generic command, copyback path or arbitrary scene/C# authoring service.
grep -Fq '[ValidateSet("start","status","discover","install-pipeline","stop")]' "$WINDOWS"
grep -Fq 'Join-Path $env:LOCALAPPDATA "RPGKingdomSupervisor\UnityCliPilots"' "$WINDOWS"
grep -Fq 'Session must be within the dedicated pilot root' "$WINDOWS"
grep -Fq 'PID project-path ownership could not be verified' "$WINDOWS"
grep -Fq 'Another Unity.exe exists' "$WINDOWS"
grep -Fq 'robocopy.exe' "$WINDOWS"
grep -Fq '"pipeline","install","--project-path"' "$WINDOWS"
! grep -Fq 'git push' "$WINDOWS"
! grep -Fq 'Copy-Item -Destination $SourceProjectPath' "$WINDOWS"
echo "PASS: isolated Unity CLI stage wrapper and non-worker boundaries"
