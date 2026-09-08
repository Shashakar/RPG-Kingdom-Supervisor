#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/GH-123"

cat > "$TMP/bin/curl" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%q ' "$@" >> "$RPGK_TEST_CURL_LOG"
printf '\n' >> "$RPGK_TEST_CURL_LOG"
if [[ "$*" == *'/issues/123/labels?per_page=100'* ]]; then
  printf '[{"name":"symphony:ready"}]\n'
else
  printf '{}\n'
fi
MOCK
chmod +x "$TMP/bin/curl"

export PATH="$TMP/bin:$PATH"
export RPGK_TEST_CURL_LOG="$TMP/curl.log"
export SYMPHONY_GITHUB_TOKEN="test-token"

cd "$TMP/GH-123"

RPGK_GUARD_DRY_RUN=1 bash "$ROOT/scripts/after-run-guard.sh"
if grep -q '/labels/symphony%3Aready' "$RPGK_TEST_CURL_LOG"; then
  echo "Dry run unexpectedly mutated labels" >&2
  exit 1
fi

: > "$RPGK_TEST_CURL_LOG"
bash "$ROOT/scripts/after-run-guard.sh"

grep -q '/issues/123/labels?per_page=100' "$RPGK_TEST_CURL_LOG"
grep -q '/issues/123/labels/symphony%3Aready' "$RPGK_TEST_CURL_LOG"
grep -q '/issues/123/labels' "$RPGK_TEST_CURL_LOG"
grep -q '/issues/123/comments' "$RPGK_TEST_CURL_LOG"

echo "after-run-guard-test: PASS"
