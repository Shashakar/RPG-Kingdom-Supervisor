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
  if [[ "${RPGK_TEST_READY:-1}" == "1" ]]; then
    printf '[{"name":"symphony:ready"}]\n'
  else
    printf '[]\n'
  fi
elif [[ "$*" == *'/pulls?state=open&head=Shashakar:codex%2Ffixture&base=main&per_page=10'* ]]; then
  printf '[{"number":77}]\n'
else
  printf '{}\n'
fi
MOCK
chmod +x "$TMP/bin/curl"

cat > "$TMP/bin/git" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == 'branch --show-current' ]]; then
  printf 'codex/fixture\n'
else
  exec /usr/bin/git "$@"
fi
MOCK
chmod +x "$TMP/bin/git"

export PATH="$TMP/bin:$PATH"
export RPGK_TEST_CURL_LOG="$TMP/curl.log"
export SYMPHONY_GITHUB_TOKEN="test-token"
export RPGK_TEST_READY=1

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

# A successful Git handoff removes symphony:ready. after_run must then queue independent review,
# not halt or immediately re-dispatch implementation.
export RPGK_TEST_READY=0
: > "$RPGK_TEST_CURL_LOG"
bash "$ROOT/scripts/after-run-guard.sh"
grep -Fq '/pulls?state=open&head=Shashakar:codex%2Ffixture&base=main&per_page=10' "$RPGK_TEST_CURL_LOG"
grep -Fq 'symphony:agent-review' "$RPGK_TEST_CURL_LOG"
if grep -Fq 'symphony:halted' "$RPGK_TEST_CURL_LOG"; then
  echo "Successful handoff was incorrectly halted" >&2
  exit 1
fi

echo "after-run-guard-test: PASS"
