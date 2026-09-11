#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/GH-123"

cat > "$TMP/bin/curl" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$RPGK_TEST_CURL_LOG"
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
test -f .symphony-attempt-complete
if grep -Fq '/labels/symphony%3Aready' "$RPGK_TEST_CURL_LOG"; then
  echo "Dry run unexpectedly mutated labels" >&2
  exit 1
fi

rm -f .symphony-attempt-complete
: > "$RPGK_TEST_CURL_LOG"
bash "$ROOT/scripts/after-run-guard.sh"

test -f .symphony-attempt-complete
grep -Fq '/issues/123/labels?per_page=100' "$RPGK_TEST_CURL_LOG"
grep -Fq '/issues/123/labels/symphony%3Aready' "$RPGK_TEST_CURL_LOG"
grep -Fq '/issues/123/labels' "$RPGK_TEST_CURL_LOG"
grep -Fq '/issues/123/comments' "$RPGK_TEST_CURL_LOG"

cat > .symphony-usage-limit.json <<'JSON'
{"reason":"usage_limit_exceeded","message":"You've hit your usage limit. try again at Sep 11th, 2026 12:11 AM.","retry_at":"Sep 11th, 2026 12:11 AM"}
JSON
: > "$RPGK_TEST_CURL_LOG"
bash "$ROOT/scripts/after-run-guard.sh"

if [[ -e .symphony-usage-limit.json ]]; then
  echo "Quota marker was not consumed after tracker reporting" >&2
  exit 1
fi
grep -Fq 'usage_limit_exceeded' "$RPGK_TEST_CURL_LOG"
grep -Fq 'Sep 11th, 2026 12:11 AM' "$RPGK_TEST_CURL_LOG"
if grep -Fq 'Phase 2 budget guard stopped automatic redispatch' "$RPGK_TEST_CURL_LOG"; then
  echo "Quota halt incorrectly used the generic max-turn/budget comment" >&2
  exit 1
fi

echo "after-run-guard-test: PASS"
