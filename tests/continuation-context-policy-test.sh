#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

grep -Fq 'build-continuation-context-with-auth.sh' "$ROOT/scripts/codex-app-server-router.sh"
grep -Fq 'unset SYMPHONY_GITHUB_TOKEN' "$ROOT/scripts/codex-app-server-router.sh"
python3 - "$ROOT/scripts/codex-app-server-router.sh" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text()
assert text.index('build-continuation-context-with-auth.sh') < text.index('unset SYMPHONY_GITHUB_TOKEN')
PY

grep -Fq 'gh auth token' "$ROOT/scripts/build-continuation-context-with-auth.sh"
grep -Fq 'SYMPHONY_GITHUB_TOKEN="$TOKEN"' "$ROOT/scripts/build-continuation-context-with-auth.sh"
grep -Fq 'AGENTS.override.md' "$ROOT/scripts/build-continuation-context.sh"
grep -Fq 'git ls-tree -r --name-only HEAD' "$ROOT/scripts/build-continuation-context.sh"
grep -Fq 'reviewThreads(first:100)' "$ROOT/scripts/build-continuation-context.sh"

echo "continuation-context-policy-test: PASS"
