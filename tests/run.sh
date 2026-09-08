#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

bash "$ROOT/tests/routing-policy-test.sh"
bash "$ROOT/tests/after-run-guard-test.sh"

echo "supervisor-tests: PASS"
