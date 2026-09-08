#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

bash "$ROOT/tests/routing-policy-test.sh"
bash "$ROOT/tests/codex-router-test.sh" >/dev/null
bash "$ROOT/tests/worker-lifetime-guard-test.sh" >/dev/null
bash "$ROOT/tests/after-run-guard-test.sh"

echo "supervisor-tests: PASS"
