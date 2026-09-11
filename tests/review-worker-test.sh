#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/workspace" "$TMP/state"

cat > "$TMP/bin/codex" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
echo 'usage_limit_exceeded: You have hit your usage limit. try again at tomorrow.' >&2
exit 1
MOCK
chmod +x "$TMP/bin/codex"

cat > "$TMP/prompt.txt" <<'EOF'
Review this fixture.
EOF

export PATH="$TMP/bin:$PATH"
export RPGK_SUPERVISOR_ROOT="$ROOT"
export RPGK_SUPERVISOR_STATE_ROOT="$TMP/state"

bash "$ROOT/scripts/review-worker.sh" \
  "$TMP/workspace" \
  "$TMP/prompt.txt" \
  "$TMP/verdict.json" \
  gpt-5.6-terra \
  medium

python3 - "$TMP/verdict.json" <<'PY'
import json, sys
from pathlib import Path
value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert value["verdict"] == "blocked_or_ambiguous"
assert value["requires_human"] is True
assert value["routing_recommendation"] == "unchanged"
assert "quota" in value["summary"].lower()
PY

grep -Fq 'usage_limit_exceeded' "$TMP/verdict.json.codex.log"

echo "review-worker-test: PASS"
