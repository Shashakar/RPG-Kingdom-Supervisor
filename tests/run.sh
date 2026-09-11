#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

bash "$ROOT/tests/routing-policy-test.sh"
bash "$ROOT/tests/codex-router-test.sh" >/dev/null
bash "$ROOT/tests/codex-permissions-policy-test.sh"
bash "$ROOT/tests/symphony-permissions-compat-test.sh"
bash "$ROOT/tests/diagnostics-policy-test.sh"
bash "$ROOT/tests/worker-lifetime-guard-test.sh" >/dev/null
bash "$ROOT/tests/after-run-guard-test.sh"
bash "$ROOT/tests/unity-resource-policy-test.sh"
bash "$ROOT/tests/unity-resource-guard-test.sh"
bash "$ROOT/tests/unity-runner-policy-test.sh"
bash "$ROOT/tests/unity-runner-host-syntax-test.sh"
bash "$ROOT/tests/unity-broker-test.sh"
bash "$ROOT/tests/git-handoff-broker-test.sh"
python3 "$ROOT/tests/git-handoff-host-test.py"
bash "$ROOT/tests/rearm-issue-test.sh"
bash "$ROOT/tests/continuation-context-test.sh"
bash -n "$ROOT/scripts/run-symphony.sh"

echo "supervisor-tests: PASS"
