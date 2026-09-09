#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m py_compile \
  "$ROOT/scripts/diagnostics.py" \
  "$ROOT/scripts/codex-turn-environment-probe.py"

bash -n "$ROOT/scripts/diagnose-issue.sh"
bash -n "$ROOT/scripts/serve-diagnostics.sh"
bash -n "$ROOT/scripts/codex-turn-environment-probe.sh"

# The dashboard must remain localhost-only and read-only.
grep -q 'ThreadingHTTPServer(("127.0.0.1", port)' "$ROOT/scripts/diagnostics.py"
if grep -Eq 'gh[[:space:]]+issue[[:space:]]+(edit|close|reopen)|git[[:space:]]+(commit|push|checkout|switch|merge)' "$ROOT/scripts/diagnostics.py"; then
  echo "Diagnostics collector contains mutating Git/GitHub operations" >&2
  exit 1
fi

# The model-turn probe is explicit because it consumes allowance. It must test
# both failure modes observed on GH-97 without becoming an automatic startup
# check.
grep -q 'parser.add_argument(' "$ROOT/scripts/codex-turn-environment-probe.py"
grep -q '"--run"' "$ROOT/scripts/codex-turn-environment-probe.py"
grep -q '"method": "turn/start"' "$ROOT/scripts/codex-turn-environment-probe.py"
grep -q '.git/FETCH_HEAD' "$ROOT/scripts/codex-turn-environment-probe.py"
grep -q 'cmd.exe' "$ROOT/scripts/codex-turn-environment-probe.py"
grep -q '"permissions": profile' "$ROOT/scripts/codex-turn-environment-probe.py"
grep -q '"effort": "low"' "$ROOT/scripts/codex-turn-environment-probe.py"

if grep -q 'codex-turn-environment-probe' "$ROOT/scripts/run-symphony.sh"; then
  echo "Model-backed turn probe must not run automatically at Symphony startup" >&2
  exit 1
fi

if grep -R -q 'danger-full-access' \
  "$ROOT/scripts/codex-turn-environment-probe.py" \
  "$ROOT/scripts/diagnostics.py"; then
  echo "Diagnostics must not broaden the Codex sandbox" >&2
  exit 1
fi

echo "diagnostics-policy-test: PASS"
