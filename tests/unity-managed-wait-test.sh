#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/workspaces/GH-321" "$TMP/bin"
cp "$ROOT/scripts/unity-managed-wait.py" "$TMP/bin/unity-managed-wait.py"
cat > "$TMP/bin/unity-runner.sh" <<'FAKE'
#!/usr/bin/env bash
sleep 33
printf 'inspected %s\n' "$*"
FAKE
chmod +x "$TMP/bin/unity-runner.sh"
export RPGK_SYMPHONY_WORKSPACE_ROOT="$TMP/workspaces"
cli=(python3 "$TMP/bin/unity-managed-wait.py" --workspace "$TMP/workspaces/GH-321")
"${cli[@]}" start --operation inspect --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity >"$TMP/start.json"
jq -e '.status == "running" and .operation == "inspect"' "$TMP/start.json" >/dev/null
"${cli[@]}" start --operation inspect --scene Assets/RPGKingdom/Scenes/PlaytestScene.unity >"$TMP/repeat.json"
[[ "$(jq -r .jobId "$TMP/start.json")" == "$(jq -r .jobId "$TMP/repeat.json")" ]]
for i in $(seq 1 400); do
  "${cli[@]}" status >"$TMP/status.json"
  if [[ "$(jq -r .status "$TMP/status.json")" == completed ]]; then break; fi
  sleep .1
done
jq -e '.status == "completed" and .exitCode == 0' "$TMP/status.json" >/dev/null
# Assert the same invocation remained alive beyond the 30-second execution boundary.
[[ "$(jq -r .jobId "$TMP/start.json")" == "$(jq -r .jobId "$TMP/status.json")" ]]
grep -q 'inspected inspect' "$(jq -r '.stdoutPath' "$TMP/status.json")"
echo 'unity-managed-wait-test: PASS'
