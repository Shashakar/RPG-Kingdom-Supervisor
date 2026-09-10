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
printf '%s\n' "$*" >> "${FAKE_GH_LOG:?}"
if [[ "$1 $2" == "issue view" ]]; then
  printf 'symphony:halted\n'
fi
exit 0
EOF
chmod +x "$TMP/bin/gh"

FAKE_GH_LOG="$TMP/gh.log" \
PATH="$TMP/bin:$PATH" \
RPGK_WORKSPACE_ROOT="$TMP/workspaces" \
RPGK_SUPERVISOR_STATE_ROOT="$TMP/state" \
bash "$ROOT/scripts/rearm-issue.sh" 123 >/dev/null

# The operator helper now requests the same remote one-shot contract used by review tooling. Host
# preflight owns local marker/lock recovery; the caller does not mutate those files directly.
[[ -e "$TMP/workspaces/GH-123/.symphony-attempt-complete" ]] || { echo "rearm helper unexpectedly cleared the attempt marker locally" >&2; exit 1; }
[[ -d "$TMP/state/locks/unity-editor.lock" ]] || { echo "rearm helper unexpectedly cleared the Unity lock locally" >&2; exit 1; }
grep -Fq 'issue edit 123 --repo Shashakar/RPG-Kingdom --remove-label symphony:halted' "$TMP/gh.log" || { echo "rearm helper did not remove halted label" >&2; exit 1; }
rearm_line="$(grep -nF -- '--add-label symphony:rearm' "$TMP/gh.log" | head -n1 | cut -d: -f1)"
ready_line="$(grep -nF -- '--add-label symphony:ready' "$TMP/gh.log" | head -n1 | cut -d: -f1)"
[[ -n "$rearm_line" && -n "$ready_line" ]] || { echo "rearm helper did not request both rearm and ready labels" >&2; exit 1; }
(( rearm_line < ready_line )) || { echo "rearm helper exposed ready before rearm approval" >&2; exit 1; }

echo "rearm-issue-test: PASS"
