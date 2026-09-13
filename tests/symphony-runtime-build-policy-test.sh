#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPLY="$ROOT/scripts/apply-symphony-permissions-patch.sh"
VERIFY="$ROOT/scripts/verify-symphony-permissions-patch.sh"
RUNNER="$ROOT/scripts/run-symphony.sh"

for file in "$APPLY" "$VERIFY" "$RUNNER"; do
  [[ -f "$file" ]] || { echo "missing expected file: $file" >&2; exit 1; }
done

# The compatibility installer must rebuild the escript after transformed source
# passes upstream tests; otherwise bin/symphony can keep running stale AgentRunner
# code even while source-level verification/tests are green.
grep -Fq 'mise exec -- mix build' "$APPLY"

test_line="$(grep -n 'mise exec -- mix test' "$APPLY" | head -n1 | cut -d: -f1)"
build_line="$(grep -n 'mise exec -- mix build' "$APPLY" | head -n1 | cut -d: -f1)"
commit_line="$(grep -n 'commit -m "Support RPG Kingdom Codex compatibility policy"' "$APPLY" | head -n1 | cut -d: -f1)"
[[ -n "$test_line" && -n "$build_line" && -n "$commit_line" ]]
(( build_line > test_line ))
(( build_line < commit_line ))

# Verification must reject a missing/stale generated runtime, not merely prove
# that the checked-in transformed source contains the expected markers.
grep -Fq 'runtime="$SYMPHONY_ROOT/bin/symphony"' "$VERIFY"
grep -Fq '[[ ! -x "$runtime" ]]' "$VERIFY"
grep -Fq '[[ "$source" -nt "$runtime" ]]' "$VERIFY"

# Normal startup already gates on compatibility verification before launching
# the generated escript; retain that ordering so a stale runtime cannot execute.
verify_line="$(grep -n 'verify-symphony-permissions-patch.sh' "$RUNNER" | head -n1 | cut -d: -f1)"
launch_line="$(grep -n 'mise exec -- ./bin/symphony' "$RUNNER" | head -n1 | cut -d: -f1)"
[[ -n "$verify_line" && -n "$launch_line" ]]
(( verify_line < launch_line ))

echo "symphony-runtime-build-policy-test: PASS"
