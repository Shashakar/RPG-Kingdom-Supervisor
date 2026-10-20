#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Experimental staged-editor contract tests are deliberately separate from
# production acceptance. Run every file and report all failures.
failures=0
for name in \
  unity-cli-readonly-test.py \
  unity-cli-stage-benchmark-test.py \
  unity-cli-staged-code-test.py
do
  echo "========== EXPERIMENTAL: $name =========="
  if python3 "$ROOT/tests/$name"; then
    echo "$name: PASS"
  else
    result=$?
    echo "$name: FAILED (exit $result)"
    failures=$((failures + 1))
  fi
done

if (( failures > 0 )); then
  echo "experimental-unity-cli: FAILED ($failures test files); not a production acceptance gate" >&2
  exit 1
fi
echo "experimental-unity-cli: PASS"
