#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin" "$TMP/workspaces/GH-123" "$TMP/state/locks/unity-editor.lock"
printf 'GH-123\n' > "$TMP/state/locks/unity-editor.lock/owner"
printf 'used\n' > "$TMP/workspaces/GH-123/.symphony-attempt-complete"

cat > "$TMP/bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2" == "issue view" ]]; then
  printf 'symphony:halted\n'
  exit 0
fi
exit 0
EOF
chmod +x "$TMP/bin/gh"

PATH="$TMP/bin:$PATH" \
RPGK_WORKSPACE_ROOT="$TMP/workspaces" \
RPGK_SUPERVISOR_STATE_ROOT="$TMP/state" \
bash "$ROOT/scripts/rearm-issue.sh" 123 >/dev/null

[[ ! -e "$TMP/workspaces/GH-123/.symphony-attempt-complete" ]] || { echo "rearm did not clear attempt marker" >&2; exit 1; }
[[ ! -d "$TMP/state/locks/unity-editor.lock" ]] || { echo "rearm did not clear lock owned by the issue" >&2; exit 1; }

mkdir -p "$TMP/state/locks/unity-editor.lock"
printf 'GH-999\n' > "$TMP/state/locks/unity-editor.lock/owner"

PATH="$TMP/bin:$PATH" \
RPGK_WORKSPACE_ROOT="$TMP/workspaces" \
RPGK_SUPERVISOR_STATE_ROOT="$TMP/state" \
bash "$ROOT/scripts/rearm-issue.sh" 123 >/dev/null

[[ -d "$TMP/state/locks/unity-editor.lock" ]] || { echo "rearm incorrectly cleared another issue's Unity lock" >&2; exit 1; }
[[ "$(cat "$TMP/state/locks/unity-editor.lock/owner")" == "GH-999" ]] || { echo "rearm changed another issue's Unity lock owner" >&2; exit 1; }

echo "rearm-issue-test: PASS"
