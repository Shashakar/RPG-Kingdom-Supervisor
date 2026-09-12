#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/GH-123" "$TMP/state"

cat > "$TMP/bin/curl" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$RPGK_TEST_CURL_LOG"
printf '[]\n'
MOCK
chmod +x "$TMP/bin/curl"

cat > "$TMP/reconcile-ok.py" <<'PY'
#!/usr/bin/env python3
import json, os, sys
with open(os.environ["RPGK_TEST_RECONCILE_LOG"], "w", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]))
print(json.dumps({"status":"completed","issue":123}))
PY
chmod +x "$TMP/reconcile-ok.py"

export PATH="$TMP/bin:$PATH"
export SYMPHONY_GITHUB_TOKEN="test-token"
export RPGK_SUPERVISOR_STATE_ROOT="$TMP/state"
export RPGK_REPORT_RECONCILER="$TMP/reconcile-ok.py"
export RPGK_TEST_CURL_LOG="$TMP/curl.log"
export RPGK_TEST_RECONCILE_LOG="$TMP/reconcile.log"

cd "$TMP/GH-123"
bash "$ROOT/scripts/after-run-guard.sh" >"$TMP/stdout" 2>"$TMP/stderr"

test -f .symphony-attempt-complete
grep -Fq -- '--issue 123' "$RPGK_TEST_RECONCILE_LOG"
grep -Fq -- '--attempt-boundary none' "$RPGK_TEST_RECONCILE_LOG"
grep -Fq 'symphony:report-complete' "$TMP/stderr"
if [[ -s "$RPGK_TEST_CURL_LOG" ]]; then
  echo "report-completion-after-run-test: successful trusted reconciliation should bypass generic tracker mutation" >&2
  cat "$RPGK_TEST_CURL_LOG" >&2
  exit 1
fi

# A receipt/reconciliation failure must not be converted into the generic halted lifecycle.
cat > "$TMP/reconcile-fail.py" <<'PY'
#!/usr/bin/env python3
print('{"status":"GitHubNetworkFailed"}')
raise SystemExit(73)
PY
chmod +x "$TMP/reconcile-fail.py"
export RPGK_REPORT_RECONCILER="$TMP/reconcile-fail.py"
: > "$RPGK_TEST_CURL_LOG"
set +e
bash "$ROOT/scripts/after-run-guard.sh" >"$TMP/stdout-fail" 2>"$TMP/stderr-fail"
status=$?
set -e
[[ "$status" -eq 73 ]] || { echo "expected reconciliation failure 73, got $status" >&2; exit 1; }
grep -Fq 'refusing to convert it into symphony:halted' "$TMP/stderr-fail"
if grep -Fq 'symphony:halted' "$RPGK_TEST_CURL_LOG"; then
  echo "trusted report reconciliation failure was incorrectly halted" >&2
  exit 1
fi

# No matching receipt (exit 2) falls through to the existing guard behavior.
cat > "$TMP/reconcile-none.py" <<'PY'
#!/usr/bin/env python3
raise SystemExit(2)
PY
chmod +x "$TMP/reconcile-none.py"
export RPGK_REPORT_RECONCILER="$TMP/reconcile-none.py"
export RPGK_GUARD_DRY_RUN=1
: > "$RPGK_TEST_CURL_LOG"
bash "$ROOT/scripts/after-run-guard.sh" >"$TMP/stdout-none" 2>"$TMP/stderr-none"
grep -Fq 'would halt GH-123 because symphony:ready remains' "$TMP/stderr-none"

echo "report-completion-after-run-test: PASS"
