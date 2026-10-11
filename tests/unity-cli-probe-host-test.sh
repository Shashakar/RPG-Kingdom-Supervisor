#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/unity-cli-pilot.sh"
temp="$(mktemp -d)"
trap 'rm -rf "$temp"' EXIT
mkdir -p "$temp/bin"

cat > "$temp/bin/wslpath" <<'EOF'
#!/usr/bin/env bash
[[ "$1" == "-w" ]] || exit 8
printf 'C:\\Pilot\\probe-unity-cli.ps1\n'
EOF

cat > "$temp/bin/powershell.exe" <<'EOF'
#!/usr/bin/env bash
printf '<%s>\n' "$@" > "$PROBE_ARGUMENT_CAPTURE"
printf '{"schemaVersion":1,"status":"pipeline-listed-project-unchecked"}\n'
EOF
chmod +x "$temp/bin/wslpath" "$temp/bin/powershell.exe"

export PROBE_ARGUMENT_CAPTURE="$temp/arguments"
export PATH="$temp/bin:$PATH"
bash -n "$SCRIPT"

output="$(bash "$SCRIPT")"
[[ "$output" == *'"status":"pipeline-listed-project-unchecked"'* ]]
grep -Fxq '<-File>' "$temp/arguments"
grep -Fxq '<C:\Pilot\probe-unity-cli.ps1>' "$temp/arguments"
! grep -Fq '<-ProjectPath>' "$temp/arguments"

output="$(bash "$SCRIPT" --cli-windows 'C:\Program Files\Unity CLI\unity.exe' --project-windows 'D:\Unity Stages\GH-205')"
[[ "$output" == *'"schemaVersion":1'* ]]
grep -Fxq '<-CliPath>' "$temp/arguments"
grep -Fxq '<C:\Program Files\Unity CLI\unity.exe>' "$temp/arguments"
grep -Fxq '<-ProjectPath>' "$temp/arguments"
grep -Fxq '<D:\Unity Stages\GH-205>' "$temp/arguments"

if bash "$SCRIPT" --project-windows '\\wsl.localhost\Ubuntu\home\project' > /dev/null 2>&1; then
  echo "FAIL: WSL UNC project was accepted" >&2
  exit 1
fi
if bash "$SCRIPT" --arbitrary-unity-command > /dev/null 2>&1; then
  echo "FAIL: arbitrary command was accepted" >&2
  exit 1
fi
if bash "$SCRIPT" --project-windows > /dev/null 2>&1; then
  echo "FAIL: missing project value was accepted" >&2
  exit 1
fi
echo "PASS: CLI pilot wrapper rejects arbitrary commands and preserves Windows arguments"
