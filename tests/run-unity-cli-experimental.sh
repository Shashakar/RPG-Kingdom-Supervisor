#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Deferred Unity CLI broker contract tests.
# Known baseline failures are documented separately.
python3 "$ROOT/tests/unity-cli-readonly-test.py"
python3 "$ROOT/tests/unity-cli-stage-benchmark-test.py"
python3 "$ROOT/tests/unity-cli-staged-code-test.py"
