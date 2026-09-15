#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin" "$TMP/fake-supervisor/scripts" "$TMP/state" "$TMP/GH-121"

cat > "$TMP/bin/curl" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${RPGK_TEST_CURL_LOG:?}"
if [[ "$*" == *'/issues/121/labels?per_page=100'* ]]; then
  printf '[{"name":"symphony:rearm"},{"name":"symphony:ready"}]\n'
else
  printf '{}\n'
fi
MOCK
chmod +x "$TMP/bin/curl"

cat > "$TMP/fake-supervisor/scripts/git-handoff.sh" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${RPGK_TEST_HANDOFF_LOG:?}"
MOCK
chmod +x "$TMP/fake-supervisor/scripts/git-handoff.sh"

cd "$TMP/GH-121"
git init -q -b main
git config user.name 'Test User'
git config user.email 'test@example.invalid'
printf 'base\n' > base.txt
git add base.txt
git commit -qm base
git switch -qc codex/gh-121-repair-character-sandbox-timed-resource-test

if [[ -e .symphony-attempt-complete ]]; then
  echo "fixture unexpectedly contains attempt marker" >&2
  exit 1
fi

export PATH="$TMP/bin:$PATH"
export SYMPHONY_GITHUB_TOKEN="test-token"
export RPGK_SUPERVISOR_STATE_ROOT="$TMP/state"
export RPGK_SUPERVISOR_ROOT="$TMP/fake-supervisor"
export RPGK_TEST_CURL_LOG="$TMP/curl.log"
export RPGK_TEST_HANDOFF_LOG="$TMP/handoff.log"

bash "$ROOT/scripts/before-run-guard.sh"

[[ -f .symphony-attempt-complete ]] || { echo "markerless rearm did not synthesize continuation boundary" >&2; exit 1; }
grep -Fq 'completed worker lifetime for GH-121 at ' .symphony-attempt-complete || { echo "synthesized marker did not use the canonical attempt-boundary format" >&2; exit 1; }
grep -Fxq 'prepare --branch codex/gh-121-repair-character-sandbox-timed-resource-test --project '"$TMP/GH-121" "$TMP/handoff.log" || { echo "markerless rearm did not invoke host-owned branch refresh" >&2; cat "$TMP/handoff.log" >&2; exit 1; }
grep -Fq '/issues/121/labels/symphony%3Arearm' "$TMP/curl.log" || { echo "rearm label was not consumed" >&2; exit 1; }

echo "markerless-rearm-refresh-test: PASS"
